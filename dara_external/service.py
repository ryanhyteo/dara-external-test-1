import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from uuid import uuid4
from google.protobuf.json_format import MessageToDict
from a2a.types import (AgentCard, AgentInterface, AgentCapabilities, AgentSkill, SecurityScheme,
    HTTPAuthSecurityScheme, SecurityRequirement, Task, TaskState, Message, Part, Role,
    Artifact, ListTasksResponse)
from a2a.server.request_handlers import RequestHandler
from a2a.server.routes import create_jsonrpc_routes, create_agent_card_routes
from a2a.utils.errors import (InvalidParamsError, ContentTypeNotSupportedError,
    TaskNotFoundError, TaskNotCancelableError, UnsupportedOperationError)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from .config import RELEASE
from .store import Store
from .model import Model

TERMINAL = {TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED}

def agent_message(text, task):
    return Message(message_id=str(uuid4()), task_id=task.id, context_id=task.context_id,
                   role=Role.ROLE_AGENT, parts=[Part(text=text)])

class Handler(RequestHandler):
    def __init__(self, settings, model):
        self.settings, self.model = settings, model
        self.store = Store(settings.data_dir)
        self.store.recover()
        self.work = {}
        self.lock = asyncio.Lock()

    async def on_message_send(self, params, context):
        message = params.message
        if message.role != Role.ROLE_USER or not message.message_id or len(message.message_id) > 256:
            raise InvalidParamsError('A user message with a unique messageId (max 256 characters) is required')
        if message.task_id:
            raise InvalidParamsError('Each assessment/discussion uses a new task; provide full case and panel text')
        if message.extensions or message.reference_task_ids or params.tenant:
            raise UnsupportedOperationError('Extensions, referenced tasks and tenants are not supported')
        if not message.parts or any(p.WhichOneof('content') != 'text' or p.media_type not in ('', 'text/plain') for p in message.parts):
            raise ContentTypeNotSupportedError('Text-only educational cases: images, files, URLs and data inputs are not supported')
        text = '\n'.join(p.text for p in message.parts)
        if not text.strip() or len(text) > 100_000:
            raise InvalidParamsError('Provide between 1 and 100000 text characters')
        metadata = MessageToDict(params.metadata)
        if set(metadata) != {'phase'} or metadata['phase'] not in ('assessment', 'discussion'):
            raise InvalidParamsError('Request metadata must be {"phase":"assessment"} or {"phase":"discussion"}')
        if params.configuration.HasField('task_push_notification_config'):
            raise UnsupportedOperationError('Push notifications are not supported; poll GetTask')
        modes = params.configuration.accepted_output_modes
        if modes and 'text/plain' not in modes:
            raise ContentTypeNotSupportedError('Only text/plain output is supported')
        phase = metadata['phase']
        digest = hashlib.sha256(json.dumps({'message': MessageToDict(message), 'phase': phase}, sort_keys=True).encode()).hexdigest()
        async with self.lock:
            existing = self.store.duplicate(message.message_id)
            if existing:
                if not hmac.compare_digest(existing[0], digest):
                    raise InvalidParamsError('messageId was already used for a different payload')
                task = self.store.get(existing[1])
            else:
                if len(self.work) >= self.settings.max_concurrent:
                    raise InvalidParamsError('Execution capacity reached; retry later')
                task = Task(id=str(uuid4()), context_id=message.context_id or str(uuid4()), history=[message])
                task.status.state = TaskState.TASK_STATE_SUBMITTED
                task.status.timestamp.GetCurrentTime()
                task.metadata.update({'release': RELEASE, 'configuration_fingerprint': self.settings.fingerprint,
                                      'phase': phase, 'evaluation_status': 'unevaluated'})
                self.store.create(message.message_id, digest, task)
                self.work[task.id] = asyncio.create_task(self.execute(task.id, phase, text))
        if not params.configuration.return_immediately:
            job = self.work.get(task.id)
            if job:
                # A disconnected caller does not cancel durable work.
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    if not job.cancelled():
                        raise
        return self.store.get(task.id)

    async def execute(self, task_id, phase, text):
        task = self.store.get(task_id)
        task.status.state = TaskState.TASK_STATE_WORKING
        task.status.timestamp.GetCurrentTime()
        self.store.save(task)
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                output, info = await self.model.run(phase, text)
            task = self.store.get(task_id)
            if task.status.state == TaskState.TASK_STATE_CANCELED:
                return
            task.metadata.update(info)
            if output is None:
                task.status.state = TaskState.TASK_STATE_FAILED
                task.status.message.CopyFrom(agent_message('Model response could not be verified; no replacement model output was released.', task))
            else:
                task.status.state = TaskState.TASK_STATE_COMPLETED
                task.artifacts.append(Artifact(artifact_id=str(uuid4()), name=phase, parts=[Part(text=output)]))
                task.history.append(agent_message(output, task))
        except asyncio.CancelledError:
            task = self.store.get(task_id)
            task.status.state = TaskState.TASK_STATE_CANCELED
            task.metadata.update({'failure_reason': 'cancelled', 'provider_completion': 'unknown'})
            task.status.message.CopyFrom(agent_message('Task canceled. The provider may still charge for work already accepted.', task))
        except TimeoutError:
            task = self.store.get(task_id)
            task.status.state = TaskState.TASK_STATE_FAILED
            task.metadata.update({'failure_reason': 'time_limit_reached', 'provider_completion': 'unknown'})
            task.status.message.CopyFrom(agent_message('Model execution time limit reached.', task))
        except Exception:
            # Provider errors may contain case text, credentials or bodies. Do not expose/log them.
            task = self.store.get(task_id)
            task.status.state = TaskState.TASK_STATE_FAILED
            task.metadata.update({'failure_reason': 'provider_error', 'provider_completion': 'unknown'})
            task.status.message.CopyFrom(agent_message('Model request failed. Check provider configuration and credits.', task))
        finally:
            task.metadata.update({'execution_duration_ms': round((time.monotonic() - start) * 1000)})
            task.status.timestamp.GetCurrentTime()
            self.store.save(task)
            self.work.pop(task_id, None)

    async def on_get_task(self, params, context):
        task = self.store.get(params.id)
        if not task:
            raise TaskNotFoundError()
        if params.history_length < 0:
            raise InvalidParamsError('historyLength must be nonnegative')
        if params.HasField('history_length'):
            history = list(task.history)[-params.history_length:] if params.history_length else []
            del task.history[:]
            task.history.extend(history)
        return task

    async def on_cancel_task(self, params, context):
        task = self.store.get(params.id)
        if not task:
            raise TaskNotFoundError()
        if task.status.state == TaskState.TASK_STATE_CANCELED:
            return task
        if task.status.state in TERMINAL:
            raise TaskNotCancelableError()
        job = self.work.get(task.id)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        # Also handles cancellation before the coroutine started.
        task = self.store.get(task.id)
        if task.status.state in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED):
            raise TaskNotCancelableError()
        if task.status.state not in TERMINAL:
            task.status.state = TaskState.TASK_STATE_CANCELED
            task.status.timestamp.GetCurrentTime()
            self.store.save(task)
        self.work.pop(task.id, None)
        return task

    async def on_list_tasks(self, params, context):
        raise UnsupportedOperationError('Use GetTask with the returned task ID')

    async def on_message_send_stream(self, params, context):
        raise UnsupportedOperationError('Streaming is not supported; use SendMessage and GetTask')
        yield

    async def on_subscribe_to_task(self, params, context):
        raise UnsupportedOperationError('Subscriptions are not supported; poll GetTask')
        yield

    async def on_create_task_push_notification_config(self, params, context):
        raise UnsupportedOperationError()
    async def on_get_task_push_notification_config(self, params, context):
        raise UnsupportedOperationError()
    async def on_list_task_push_notification_configs(self, params, context):
        raise UnsupportedOperationError()
    async def on_delete_task_push_notification_config(self, params, context):
        raise UnsupportedOperationError()
    async def on_get_extended_agent_card(self, params, context):
        raise UnsupportedOperationError()

class Guard:
    """Protect every task operation, reject excessive bodies before JSON parsing."""
    def __init__(self, app, secret):
        self.app, self.secret = app, secret
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path, method = scope['path'], scope['method']
        public = method in ('GET', 'HEAD') and path in ('/health', '/.well-known/agent-card.json')
        headers = dict(scope['headers'])
        if not public and not hmac.compare_digest(headers.get(b'authorization', b''), ('Bearer ' + self.secret).encode()):
            return await JSONResponse({'error': 'Bearer authentication required'}, status_code=401,
                headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            body.extend(event.get('body', b''))
            if len(body) > 1_000_000:
                return await JSONResponse({'error': 'Request body too large'}, status_code=413)(scope, receive, send)
            if not event.get('more_body', False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)

def create_app(settings, model=None):
    handler = Handler(settings, model or Model(settings))
    card = AgentCard(name='Dara External — Test',
        description='Unevaluated educational dermatology specialist. Text only. Independently hosted; no fine-tuning claim and no inherited Bazaaro scores.',
        version=RELEASE, supported_interfaces=[AgentInterface(url=settings.public_base_url + '/a2a',
            protocol_binding='JSONRPC', protocol_version='1.0')],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=['text/plain'], default_output_modes=['text/plain'],
        security_schemes={'sharedBearer': SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme='Bearer'))},
        security_requirements=[SecurityRequirement(schemes={'sharedBearer': {'list': []}})],
        skills=[AgentSkill(id=phase, name=name, description=description, tags=['dermatology', 'educational'],
                          input_modes=['text/plain'], output_modes=['text/plain']) for phase, name, description in [
            ('assessment', 'Independent assessment', 'Send full text case with request metadata phase=assessment.'),
            ('discussion', 'Discussion response', 'Send full text case plus named panel assessments with request metadata phase=discussion.')]])
    async def health(request):
        return JSONResponse({'status': 'ok', 'agent': 'Dara External — Test', 'release': RELEASE,
            'protocol_version': '1.0', 'configuration_fingerprint': settings.fingerprint,
            'requested_model': settings.model_id, 'reported_model_version': 'unknown', 'evaluation_status': 'unevaluated'})
    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in list(handler.work.values()):
            job.cancel()
        await asyncio.gather(*list(handler.work.values()), return_exceptions=True)
        if hasattr(handler.model, 'close'):
            await handler.model.close()
        handler.store.db.close()
    app = Starlette(routes=[Route('/health', health)] + create_agent_card_routes(card) +
                    create_jsonrpc_routes(handler, '/a2a', enable_v0_3_compat=False), lifespan=lifespan)
    app.state.handler, app.state.card = handler, card
    return Guard(app, settings.shared_secret)
