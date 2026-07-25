import os
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import ATTACHMENT_DIR
from core.db import db
from services.broadcaster import ws_manager
from services.voice_rooms import voice_rooms
import notes_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chat-bridge")

async def cleanup_attachments():
    while True:
        await asyncio.sleep(3600)
        from repositories.attachments import delete_expired
        expired = delete_expired()
        for row in expired:
            path = Path(row["storage_path"])
            if path.exists():
                path.unlink(missing_ok=True)
            log.info("purged expired attachment %s", row["id"])

async def cleanup_at_startup():
    from repositories.attachments import delete_expired
    expired = delete_expired()
    for row in expired:
        path = Path(row["storage_path"])
        if path.exists():
            path.unlink(missing_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    with db() as conn:
        notes_store.init_table(conn)
    await cleanup_at_startup()
    task = asyncio.create_task(cleanup_attachments())
    log.info("chat-bridge started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://late.kodingvibes.com", "https://www.kodingvibes.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"ok": True}

from routers.channels import router as channels_router
from routers.messages import router as messages_router
from routers.reactions import router as reactions_router
from routers.members import router as members_router
from routers.categories import router as categories_router
from routers.attachments import router as attachments_router
from routers.buzz import router as buzz_router
from routers.webhook import router as webhook_router
from routers.ws import router as ws_router
from routers.link_preview import router as link_preview_router
import voice

# ponytail: auth lives in late-auth-service (/api/auth/*). chat-bridge
# only validates incoming Bearer tokens via the late-auth /validate
# endpoint and keeps a local users mirror for display_name lookups.
app.include_router(channels_router)
app.include_router(messages_router)
app.include_router(reactions_router)
app.include_router(members_router)
app.include_router(categories_router)
app.include_router(attachments_router)
app.include_router(buzz_router)
app.include_router(webhook_router)
app.include_router(ws_router)
app.include_router(link_preview_router)
app.include_router(voice.router)
