# late-chat-service

FastAPI chat service for late.kodingvibes.com. Owns messages, channels,
attachments, voice notes, and the WebSocket fanout. Validates every
Bearer token through [`late-auth-service`][auth] (no local users table).

## Stack

- Python 3.12
- FastAPI + Uvicorn (`/healthz` on `:9100`)
- SQLite (WAL) at `/data/late-chat-service/chat.db`
- WebSockets via FastAPI
- Voice notes persist to disk under `/var/lib/late-attachments`
- ffmpeg bundled in the Docker image for audio probing

## Auth

`LATE_AUTH_URL` and `LATE_AUTH_SECRET` are the only auth-related env
vars. The chat never holds a user or session row of its own; the
`/api/auth/*` service is the source of truth and every incoming
request hits `/api/auth/validate` (cached in `services/user_cache.py`).

## Run

```bash
docker build -t late-chat-service:dev .
docker run -d --name late-chat-service -p 9100:9100 \
  -e SSO_BRIDGE_SECRET=... \
  -e LATE_AUTH_SECRET=... \
  -e LATE_AUTH_URL=http://host.docker.internal:9300 \
  -e SQLITE_PATH=/data/late-chat-service/chat.db \
  -e ATTACHMENT_DIR=/var/lib/late-attachments \
  -v /data/late-chat-service:/data/late-chat-service \
  -v /var/lib/late-attachments:/var/lib/late-attachments \
  late-chat-service:dev
```

## Deploy

`scripts/deploy.sh` rebuilds the image, replaces the running container,
and waits for `/healthz`. The auto-deploy webhook on this repo calls it
on every push to `main`.

## Tests

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio respx httpx coverage
python -m pytest tests/
```

[auth]: https://github.com/kodingvibes/late-auth-service


## Operational notes

- 1-ago-2026: /data/chat-bridge is the pre-extraction SQLite path. If
  the late-chat-service container has been running cleanly for a week,
  drop the safety-net directory: \`rm -rf /data/chat-bridge\`.
