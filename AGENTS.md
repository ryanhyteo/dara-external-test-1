# Architecture rules
- Keep this Python Docker service independent of the Bazaaro TanStack application; why: externally hosted agent calls must never be routed back to Bazaaro.
- Use official A2A protocol 1.0 protobuf types and JSON-RPC routes with 0.3 compatibility disabled; why: older wire formats are incompatible.
- Persist tasks and immutable message-id/digest mappings in single-process SQLite under /data; why: duplicates and interrupted requests must not cause silent paid replay.
- All task routes require the one shared bearer boundary; only card and health are public; why: task IDs alone are not authorization.
- Fail closed on any returned model mismatch, report unknown provider versions, and keep external evaluation identity separate; why: no silent substitution or score transfer.
- Never log raw case/provider bodies or credentials; why: educational health data may still be sensitive.
