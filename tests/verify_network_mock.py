"""Official A2A client over TCP; mocked model, NOT actual provider calls."""
import asyncio
import socket
import tempfile
import secrets
import httpx
import uvicorn
from google.protobuf.json_format import ParseDict
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types import AgentCard, TaskState, GetTaskRequest, CancelTaskRequest, Part
from dara_external.config import Settings
from dara_external.service import create_app
from tests.test_service import FakeModel, request, complete

async def main():
    with tempfile.TemporaryDirectory() as data:
        s = Settings('unit-model','https://dara-test.example.org','https://models.example.org/v1',
            'mock-key-not-a-credential',secrets.token_urlsafe(32),data,timeout_seconds=2)
        fake = FakeModel(.08)
        app = create_app(s,fake)
        sock = socket.socket()
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.01)
        base = f'http://127.0.0.1:{port}'
        try:
            async with httpx.AsyncClient(base_url=base) as public:
                card_response = await public.get('/.well-known/agent-card.json')
                assert card_response.status_code == 200
                card = ParseDict(card_response.json(),AgentCard())
                assert card.supported_interfaces[0].url == s.public_base_url + '/a2a'
                assert (await public.get('/health')).status_code == 200
                assert (await public.post('/a2a',json={})).status_code == 401
            async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.shared_secret,'A2A-Version':'1.0'}) as http:
                rpc = JsonRpcTransport(http,card,base+'/a2a')
                a,b = await asyncio.gather(rpc.send_message(request()),rpc.send_message(request()))
                assert a.task.id == b.task.id
                final = await complete(rpc,a.task.id)
                assert final.status.state == TaskState.TASK_STATE_COMPLETED
                assert fake.calls == 1
                second = await rpc.send_message(request('discussion','discussion',False))
                assert second.task.status.state == TaskState.TASK_STATE_COMPLETED
                try:
                    await rpc.send_message(request('image',parts=[Part(raw=b'image',media_type='image/png')]))
                    raise AssertionError('Image unexpectedly accepted')
                except Exception as error:
                    assert 'Text-only' in str(error)
                fake.delay = 1
                running = await rpc.send_message(request('cancel'))
                canceled = await rpc.cancel_task(CancelTaskRequest(id=running.task.id))
                assert canceled.status.state == TaskState.TASK_STATE_CANCELED
            print('TCP official-client verification passed: public HTTPS card, health, bearer auth, independent assessment, discussion, duplicates, retrieval, image rejection, cancellation. Model mocked; zero actual provider calls.')
        finally:
            server.should_exit = True
            await serving

if __name__ == '__main__':
    asyncio.run(main())
