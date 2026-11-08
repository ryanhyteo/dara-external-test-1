"""Real official A2A client against a running service; makes TWO actual paid model calls."""
import asyncio
import os
from uuid import uuid4
import httpx
from google.protobuf.json_format import ParseDict
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types import AgentCard, SendMessageRequest, Message, Part, Role, GetTaskRequest, TaskState

async def main():
    base = os.environ['PUBLIC_BASE_URL'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + os.environ['BAZAARO_SHARED_SECRET'], 'A2A-Version': '1.0'}
    async with httpx.AsyncClient(headers=headers, timeout=330) as http:
        card_response = await http.get(base + '/.well-known/agent-card.json')
        card_response.raise_for_status()
        card = ParseDict(card_response.json(), AgentCard())
        assert card.supported_interfaces[0].url == base + '/a2a'
        assert card.supported_interfaces[0].protocol_version == '1.0'
        rpc = JsonRpcTransport(http, card, base + '/a2a')
        for phase in ('assessment', 'discussion'):
            req = SendMessageRequest(message=Message(message_id=str(uuid4()), role=Role.ROLE_USER,
                parts=[Part(text='Educational case: adult with a recently changing pigmented lesion. No examination available.' +
                    ('\nPanel: Orin advises prompt in-person lesion examination, avoiding diagnosis from text.' if phase == 'discussion' else ''))]))
            req.metadata.update({'phase': phase})
            req.configuration.return_immediately = True
            first = await rpc.send_message(req)
            second = await rpc.send_message(req)
            assert first.task.id == second.task.id
            for _ in range(180):
                task = await rpc.get_task(GetTaskRequest(id=first.task.id))
                if task.status.state in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED):
                    break
                await asyncio.sleep(2)
            assert task.status.state == TaskState.TASK_STATE_COMPLETED, 'Model task failed; inspect authenticated task metadata'
            assert task.metadata['actual_model_call'] is True
            print(phase, 'completed; duplicate reused task; model identity and usage available in authenticated metadata')
    async with httpx.AsyncClient() as public:
        response = await public.post(base + '/a2a',json={'jsonrpc':'2.0','id':'unauthorized','method':'GetTask','params':{'id':first.task.id}})
        assert response.status_code == 401
    print('Live checks passed. No case output or secrets printed.')

if __name__ == '__main__':
    asyncio.run(main())
