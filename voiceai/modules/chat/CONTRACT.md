# chat CONTRACT

Owner: `squad-platform`.

## Routes

- `POST /api/v1/chat/{agent_id}` — SSE text turn (spec 0038).
- `GET /api/v1/chat/sessions?agent_id=` — bounded history list.

## Events

- in: chat turns (HTTP SSE).
- out: assistant frames + terminal `[DONE]`.
