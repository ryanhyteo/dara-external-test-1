# Verification — November 8, 2026

## Passed locally

- **12/12 automated tests**. Official SDK `JsonRpcTransport` over ASGI; mocked model calls.
- **Real TCP client/server check passed** using official A2A SDK JSON-RPC transport and Uvicorn. Model deliberately mocked; **zero actual provider calls**.
- `pip check`: **No broken requirements found** for exact pins in `requirements.lock`.

Covered: public protocol 1.0 HTTPS card and health; bearer enforcement on task operations; independent assessment and discussion; concurrent duplicate submissions; payload-conflict rejection; retrieval after completion and restart; no automatic replay after restart; clear image/file/URL/data rejection before model call; local cancellation and completed-task not-cancelable handling; one-second model deadline fault; returned-model mismatch withholding output; oversized body; legacy 0.3 method rejection; config fingerprint excludes secrets and changes with model identity; OpenAI-compatible adapter request/response and token usage via mock HTTP.

## Not verified

- Actual Maritime/OpenAI-compatible model calls: credentials and model ID not supplied.
- Maritime hosted deployment/public TLS/restart/sleep persistence: no Maritime deployment created.
- Docker image build/start: Docker unavailable in this sandbox. Python entrypoint/app construction and real TCP run were tested locally.
- Existing Bazaaro integration: intentionally not implemented; deliverable is the external service only.
- No benchmark was run and no evaluation score is claimed.

`python -m tests.verify_live` is included for hosted actual-model verification after configuration; it makes two paid calls. Do not interpret mock responses as evidence of clinical/model quality.
