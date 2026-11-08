# Dara External — Test

Standalone BYOA (bring your own agent) service. Python + official A2A SDK; A2A **1.0**, JSON-RPC 2.0. No changes to the existing Bazaaro Dara, her model configuration, or evaluation results. This service is **unevaluated** and claims neither fine-tuning nor Dara's score. It sends model work directly to the configured OpenAI-compatible endpoint, never to Bazaaro.

## Versions verified locally

- Python 3.13 (local 3.13.12; Docker base 3.13.9-slim).
- `a2a-sdk[http-server]==1.3.0`: genuine protocol 1.0 protobuf types, official JSON-RPC dispatcher, official `JsonRpcTransport` client.
- `openai==2.8.1`, `uvicorn==0.38.0`, `httpx==0.28.1`, `starlette==1.10.0`.
- Complete exact transitive/test dependency pins: `requirements.lock`. `pip check` passes.
- Protocol version and SDK release number are different: SDK 1.3.0 supports protocol 1.0.
- No 0.3 compatibility. Wire methods are `SendMessage`, `GetTask`, `CancelTask`, not old `message/send` or `tasks/get`. Clients send **`A2A-Version: 1.0`**.

## Exact Maritime setup (public web-app path)

1. Unzip this package. The **Dockerfile must remain at the upload/repository root**.
2. In Maritime, open **New agent**, choose **ZIP upload** (or a GitHub repository with this folder as its root), and upload the ZIP. Name it **Dara External — Test**. Use the custom Docker/public web-app route, not a chat template. Mark the app **Public** on its Overview (verified email required). CLI alternative for a repository:
   ```sh
   maritime create dara-external-test --repo https://github.com/YOUR_ACCOUNT/YOUR_REPO --public
   ```
   This service deliberately has **no `/chat` endpoint**; Maritime's separate chat contract is unnecessary.
3. Obtain the actual URL shown on **Overview → URL**, normally `https://<agent-id>.mtm.sh/`. Set **PUBLIC_BASE_URL** to that exact HTTPS origin, without a trailing path. The service intentionally fails startup until required configuration exists; if the initial launch fails, finish Env configuration and restart.
4. In the agent's **Env** tab, press **Use Maritime LLM**, then restart after completing the remaining settings. For custom/docker/auto agents this is **not enabled automatically**. Maritime injects **OPENAI_BASE_URL** (`https://api.maritime.sh/api/llm/v1`) and a per-agent **OPENAI_API_KEY**. Do not put either credential into source, the ZIP, a committed `.env`, screenshots, or chat. API alternative: create with `"useMaritimeLlm": true`, or call `POST /api/agents/{id}/reset-llm` then restart (requires secrets scope).
5. Set **MODEL_ID** to the exact model ID available on your Maritime endpoint. The current official docs show `gpt-5.4` as a chat-completions example; availability for your account is not verified. This is **not** a copy of Bazaaro's Gemini model release. If a provider returns a different model identifier, the task fails before exposing its text. Even an alias resolving to a dated identifier fails: configure the returned exact ID explicitly and redeploy as a new configuration. No automatic substitution/alias allowlist.
6. Set **BAZAARO_SHARED_SECRET** as a secret Env value (at least 32 characters) using a password manager's generator. The eventual Bazaaro BYOA caller must store the **same** bearer value securely. This package does not add a BYOA caller to the existing application and no value is bundled. Do not send this value in URLs.
7. Leave Maritime's default public forwarding port (**8080**) alone. Maritime injects **PORT**; the process binds **0.0.0.0:$PORT**, never a hardcoded port. Keep one process/replica. Maritime's documented persistent **/data** holds SQLite tasks and idempotency records; all other filesystem paths are disposable.
8. Restart/redeploy after updating Env. Visit `<your actual origin>/health` and `/.well-known/agent-card.json`. Confirm the card advertises your real HTTPS `/a2a` URL and protocol 1.0. A healthy response proves the service/store loaded, **not** model access or credit availability.
9. Run the live verification below using your environment values. Before using real health data, review platform privacy, retention and access policies; this first release is for text-only educational cases.

Useful documented CLI commands:
```sh
maritime agents url visibility <agent-id> public
maritime logs dara-external-test -f
maritime history dara-external-test
```
Set secrets through Env's secure fields rather than including their values in shell history. No Maritime account, agent ID, production deployment, model credits, or credentials were available for hosted verification here.

### Environment reference

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_BASE_URL` | Yes | HTTPS OpenAI-compatible base including `/v1`; Maritime injects it when enabled. |
| `OPENAI_API_KEY` | Yes, secret | Maritime per-agent model credential; never a Bazaaro/gateway key. |
| `MODEL_ID` | Yes | Exact requested/returned model ID; mismatch fails closed. |
| `PUBLIC_BASE_URL` | Yes | Actual public HTTPS origin; localhost and credentials/query strings are rejected. |
| `BAZAARO_SHARED_SECRET` | Yes, secret | Shared bearer for **all** A2A task operations, minimum 32 characters. |
| `PORT` | Yes | Injected by Maritime; local Docker operators supply it explicitly. |
| `MODEL_TIMEOUT_SECONDS` | No | Total model execution deadline; default 90, allowed 1–300 seconds. |
| `DATA_DIR` | No | Default `/data`; use only a persistent private volume. |

## A2A contract

Public unauthenticated GET: `/health`, `/.well-known/agent-card.json`. The card declares HTTP bearer security, text input/output, assessment/discussion skills, no streaming/push. All other requests require `Authorization: Bearer <shared value>`. Task IDs are not authorization; there is one trusted shared-secret client boundary, **not multi-user isolation**.

A2A JSON-RPC endpoint: **POST `/a2a`**, headers `Content-Type: application/json`, `A2A-Version: 1.0`, and bearer authentication. Example payload (no credentials):

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "unique-message-id",
      "role": "ROLE_USER",
      "parts": [{"text": "Educational dermatology case: a changing pigmented lesion."}]
    },
    "metadata": {"phase": "assessment"},
    "configuration": {"returnImmediately": true, "acceptedOutputModes": ["text/plain"]}
  }
}
```

Response contains `result.task` with an ID and submitted/working state. Poll:
```json
{"jsonrpc":"2.0","id":"rpc-2","method":"GetTask","params":{"id":"RETURNED_TASK_ID"}}
```
Cancel:
```json
{"jsonrpc":"2.0","id":"rpc-3","method":"CancelTask","params":{"id":"RETURNED_TASK_ID"}}
```

Use a **new messageId** with `metadata.phase="discussion"`, and put the full case plus **named panel assessments** into the text. This service does not fetch history, images, other agents, or Bazaaro routes. Each operation creates its own task; task continuations, streaming, push, subscriptions, tenants and task listing are intentionally unsupported. `returnImmediately=false` waits within the model deadline; long work remains retrievable after client disconnect.

Same messageId and identical message/phase returns the original task, including after restart; different payload on the same ID is rejected. A terminal duplicate never performs another model call. An interrupted task on restart becomes **failed**, never automatically replayed (provider execution/charges may already have happened). Retry intentionally with a new messageId. Cancellation stops local waiting; it **cannot guarantee** the remote provider stopped billing/executing.

Text inputs only, maximum 100,000 characters per task; maximum request body 1,000,000 bytes; maximum four concurrently active model calls. Images/raw bytes/files/URL/data parts are rejected before model execution. No retries or fallbacks. Case text is persisted privately under `/data`; ordinary access/provider logs are disabled or suppressed. Durable data is not encrypted by this service: protect the volume, backups and platform access. There is no automatic retention expiry; define a deployment retention policy and delete the private volume deliberately when appropriate.

## Identity and measurement

Release: `dara-external-test@1.0.0`. Configuration fingerprint hashes release/protocol/SDK/model/endpoint, copied specialty instructions, generation settings, concurrency and timeout (not credentials). Change the release when editing prompts/adapter behavior and maintain a new deployment record; existing tasks keep their own pinned fingerprint. This is a configuration fingerprint, not proof of unchanged provider weights.

Authenticated task metadata includes requested model, returned model, explicitly reported model version or **unknown**, system fingerprint or **unknown**, execution duration, token usage when provided, and API cost **unknown** (not inferred). No evaluation scores are imported. `/health` includes only safe configuration identity; it reports model version unknown until a response supplies it.

## Local and actual-model verification

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m pytest tests -q
.venv/bin/python -m tests.verify_network_mock
```

The network check starts a local TCP server and uses the **official A2A JSON-RPC client**. It overrides the client destination to localhost for testing; the public card still advertises the configured HTTPS service. The model is a clearly labeled mock. See `TEST_RESULTS.md`.

For real deployment/model verification, load `PUBLIC_BASE_URL` and `BAZAARO_SHARED_SECRET` securely into your local environment and run:
```sh
.venv/bin/python -m tests.verify_live
```
It makes **two actual paid model calls** (assessment and discussion), verifies duplicates reuse each task and retrieval, and checks unauthenticated access is denied. It does not print case text or secrets. Cancellation, timeout, restart, unsupported input and model replacement are fault-tested locally instead of sending cancelable paid work unnecessarily.

Local Docker, with credentials injected securely from your environment:
```sh
docker build -t dara-external-test .
docker run --rm -p 8080:8080 -v dara-external-data:/data \
  -e PORT=8080 -e MODEL_ID -e PUBLIC_BASE_URL -e OPENAI_BASE_URL \
  -e OPENAI_API_KEY -e BAZAARO_SHARED_SECRET dara-external-test
```
No credential-bearing environment file is included. Container execution and hosted Maritime calls have not been verified in this environment.

## Sources (checked November 8, 2026)

- Official SDK exact release: https://pypi.org/project/a2a-sdk/1.3.0/
- Official SDK code: https://github.com/a2aproject/a2a-python
- A2A specification: https://a2a-protocol.org/latest/specification/
- Maritime public web apps: https://maritime.sh/docs/web-apps (Docker/ZIP, public URL, PORT, persistent `/data`, no chat contract)
- Maritime LLM: https://maritime.sh/docs/llm (Env → Use Maritime LLM; credentials not automatic for custom containers)
