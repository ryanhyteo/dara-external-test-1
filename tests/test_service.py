import asyncio
import secrets
import httpx
import pytest
from google.protobuf.json_format import ParseDict, MessageToDict
from a2a.types import AgentCard, SendMessageRequest, Message, Part, Role, GetTaskRequest, CancelTaskRequest, TaskState
from a2a.client.transports.jsonrpc import JsonRpcTransport
from dara_external.config import Settings
from dara_external.service import create_app

class FakeModel:
    def __init__(self, delay=0.03, identity_mismatch=False):
        self.calls = 0
        self.delay = delay
        self.identity_mismatch = identity_mismatch
        self.phases = []
    async def run(self, phase, text):
        self.calls += 1
        self.phases.append(phase)
        await asyncio.sleep(self.delay)
        info = {'requested_model': 'unit-model', 'returned_model': 'wrong-model' if self.identity_mismatch else 'unit-model',
                'reported_model_version': 'unknown', 'actual_model_call': False, 'token_usage': None, 'api_cost': 'unknown'}
        if self.identity_mismatch:
            return None, {**info, 'failure_reason': 'model_identity_mismatch'}
        return 'MOCK: dermatology response; confidence medium.', info

def settings(tmp_path, timeout=1):
    return Settings('unit-model', 'https://dara-test.example.org', 'https://models.example.org/v1',
                    'mock-key-not-a-credential', secrets.token_urlsafe(32), str(tmp_path), timeout)

def request(message_id='case-1', phase='assessment', immediate=True, parts=None):
    result = SendMessageRequest(message=Message(message_id=message_id, role=Role.ROLE_USER,
        parts=parts or [Part(text='Educational case: changing pigmented lesion.')]))
    result.metadata.update({'phase': phase})
    result.configuration.return_immediately = immediate
    return result

async def client(app, s, authenticated=True):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
        headers={'Authorization': 'Bearer ' + s.shared_secret, 'A2A-Version': '1.0'} if authenticated else {'A2A-Version': '1.0'})
    card = app.app.state.card
    return http, JsonRpcTransport(http, card, 'http://test/a2a'), card

async def complete(rpc, task_id):
    for _ in range(100):
        task = await rpc.get_task(GetTaskRequest(id=task_id))
        if task.status.state in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED):
            return task
        await asyncio.sleep(.01)
    raise AssertionError('Task did not finish')

@pytest.mark.asyncio
async def test_public_card_health_and_auth_for_every_operation(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, FakeModel())
    http, rpc, card = await client(app, s, False)
    async with http:
        assert card.supported_interfaces[0].protocol_version == '1.0'
        assert card.supported_interfaces[0].url == 'https://dara-test.example.org/a2a'
        assert card.security_requirements[0].schemes['sharedBearer'] is not None
        assert (await http.get('/health')).status_code == 200
        assert s.shared_secret not in (await http.get('/health')).text
        for method in ['SendMessage', 'GetTask', 'CancelTask', 'ListTasks', 'SendStreamingMessage', 'GetExtendedAgentCard']:
            response = await http.post('/a2a', json={'jsonrpc':'2.0','id':'auth','method':method,'params':{}})
            assert response.status_code == 401
        with pytest.raises(Exception):
            await rpc.send_message(request())

@pytest.mark.asyncio
async def test_assessment_duplicate_retrieval_and_restart(tmp_path):
    s = settings(tmp_path)
    fake = FakeModel(.08)
    app = create_app(s, fake)
    http, rpc, _ = await client(app, s)
    async with http:
        first, second = await asyncio.gather(rpc.send_message(request()), rpc.send_message(request()))
        assert first.task.id == second.task.id
        assert first.task.status.state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)
        final = await complete(rpc, first.task.id)
        assert final.status.state == TaskState.TASK_STATE_COMPLETED
        assert fake.calls == 1
        assert final.artifacts[0].parts[0].text.startswith('MOCK:')
        assert final.metadata['actual_model_call'] is False
        changed = request()
        changed.message.parts[0].text = 'Different educational case'
        with pytest.raises(Exception):
            await rpc.send_message(changed)
        assert fake.calls == 1
    restarted = create_app(s, FakeModel())
    http2, rpc2, _ = await client(restarted, s)
    async with http2:
        saved = await rpc2.get_task(GetTaskRequest(id=first.task.id))
        assert saved.status.state == TaskState.TASK_STATE_COMPLETED
        duplicate = await rpc2.send_message(request())
        assert duplicate.task.id == first.task.id
        assert restarted.app.state.handler.model.calls == 0

@pytest.mark.asyncio
async def test_discussion_independent_phase(tmp_path):
    s = settings(tmp_path)
    fake = FakeModel()
    http, rpc, _ = await client(create_app(s, fake), s)
    async with http:
        req = request('discussion-1', 'discussion', False)
        req.message.parts[0].text += '\nPanel: Orin recommends examination.'
        response = await rpc.send_message(req)
        assert response.task.status.state == TaskState.TASK_STATE_COMPLETED
        assert fake.phases == ['discussion']

@pytest.mark.asyncio
async def test_images_files_urls_rejected_before_model_call(tmp_path):
    s = settings(tmp_path)
    fake = FakeModel()
    http, rpc, _ = await client(create_app(s, fake), s)
    async with http:
        for i, part in enumerate([Part(raw=b'image',media_type='image/png'), Part(url='https://example.org/img.png'),
                                  Part(text='fake image',media_type='image/png'), ParseDict({'data':{'x':1}}, Part())]):
            with pytest.raises(Exception) as error:
                await rpc.send_message(request(str(i), parts=[part]))
            assert 'Text-only' in str(error.value)
        assert fake.calls == 0

@pytest.mark.asyncio
async def test_cancellation_stops_local_work(tmp_path):
    s = settings(tmp_path)
    fake = FakeModel(.8)
    http, rpc, _ = await client(create_app(s, fake), s)
    async with http:
        response = await rpc.send_message(request())
        canceled = await rpc.cancel_task(CancelTaskRequest(id=response.task.id))
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED
        await asyncio.sleep(.04)
        assert (await rpc.get_task(GetTaskRequest(id=response.task.id))).status.state == TaskState.TASK_STATE_CANCELED
        assert not canceled.artifacts

@pytest.mark.asyncio
async def test_timeout_bounded(tmp_path):
    s = settings(tmp_path, timeout=1)
    http, rpc, _ = await client(create_app(s, FakeModel(2)), s)
    async with http:
        response = await rpc.send_message(request(immediate=False))
        assert response.task.status.state == TaskState.TASK_STATE_FAILED
        assert response.task.metadata['failure_reason'] == 'time_limit_reached'

@pytest.mark.asyncio
async def test_substitution_never_releases_output(tmp_path):
    s = settings(tmp_path)
    http, rpc, _ = await client(create_app(s, FakeModel(identity_mismatch=True)), s)
    async with http:
        response = await rpc.send_message(request(immediate=False))
        assert response.task.status.state == TaskState.TASK_STATE_FAILED
        assert not response.task.artifacts
        assert response.task.metadata['returned_model'] == 'wrong-model'

@pytest.mark.asyncio
async def test_restart_fails_interrupted_work_without_replaying(tmp_path):
    s = settings(tmp_path)
    fake = FakeModel(.8)
    app = create_app(s, fake)
    http, rpc, _ = await client(app, s)
    async with http:
        response = await rpc.send_message(request())
        restarted = create_app(s, FakeModel())
        task = restarted.app.state.handler.store.get(response.task.id)
        assert task.status.state == TaskState.TASK_STATE_FAILED
        assert task.metadata['failure_reason'] == 'service_restarted'
        assert restarted.app.state.handler.model.calls == 0
        jobs = list(app.app.state.handler.work.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)

@pytest.mark.asyncio
async def test_v03_wire_disabled_and_oversized_body(tmp_path):
    s = settings(tmp_path)
    http, rpc, _ = await client(create_app(s, FakeModel()), s)
    async with http:
        response = await http.post('/a2a',json={'jsonrpc':'2.0','id':'old','method':'message/send','params':{}})
        assert response.json()['error']['code'] == -32601
        assert (await http.post('/a2a',content=b'x'*1_000_001)).status_code == 413

def test_config_rejects_localhost_and_fingerprint_excludes_secrets(tmp_path):
    s = settings(tmp_path)
    alternate = Settings(s.model_id,s.public_base_url,s.openai_base_url,'other-key',secrets.token_urlsafe(32),str(tmp_path),s.timeout_seconds)
    assert alternate.fingerprint == s.fingerprint
    changed = Settings('changed-model',s.public_base_url,s.openai_base_url,s.api_key,s.shared_secret,str(tmp_path))
    assert changed.fingerprint != s.fingerprint
    with pytest.raises(ValueError):
        Settings('model','http://localhost:8080','https://models.example.org','key',s.shared_secret)

@pytest.mark.asyncio
async def test_direct_openai_adapter_identity_and_usage(tmp_path):
    from dara_external.model import Model
    s = settings(tmp_path)
    model = Model(s)
    calls = []
    async def endpoint(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200,json={'id':'mock-response','object':'chat.completion','created':1,
            'model':'unit-model','choices':[{'index':0,'message':{'role':'assistant','content':'MOCK direct assessment'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':12,'completion_tokens':7,'total_tokens':19}})
    import json
    await model.client.close()
    from openai import AsyncOpenAI
    model.client = AsyncOpenAI(api_key=s.api_key,base_url=s.openai_base_url,max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint)))
    text, metadata = await model.run('assessment','Educational example')
    assert text == 'MOCK direct assessment'
    assert metadata['token_usage']['total_tokens'] == 19
    assert metadata['reported_model_version'] == 'unknown'
    assert calls[0]['model'] == 'unit-model'
    assert 'independent initial assessment' in calls[0]['messages'][0]['content']
    await model.close()

@pytest.mark.asyncio
async def test_completed_task_cannot_be_cancelled(tmp_path):
    from a2a.utils.errors import TaskNotCancelableError
    s = settings(tmp_path)
    http, rpc, _ = await client(create_app(s, FakeModel()), s)
    async with http:
        response = await rpc.send_message(request(immediate=False))
        with pytest.raises(TaskNotCancelableError):
            await rpc.cancel_task(CancelTaskRequest(id=response.task.id))
        assert (await rpc.get_task(GetTaskRequest(id=response.task.id))).status.state == TaskState.TASK_STATE_COMPLETED
