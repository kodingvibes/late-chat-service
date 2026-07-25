import os

SSO_SECRET = os.environ["SSO_BRIDGE_SECRET"]
SQLITE_PATH = os.environ.get("SQLITE_PATH", "/data/chat-bridge.db")
KV_WEBHOOK_URL = os.environ.get("KV_WEBHOOK_URL", "")
KV_WEBHOOK_SECRET = os.environ.get("KV_WEBHOOK_SECRET", "")
SHARED_INTERNAL_SECRET = os.environ.get("SHARED_INTERNAL_SECRET", SSO_SECRET)

# ponytail: late-auth-service owns users + sessions. chat-bridge
# validates incoming Bearer tokens by calling /api/auth/validate on
# the auth service. The two services share LATE_AUTH_SECRET over the
# loopback interface.
LATE_AUTH_URL = os.environ.get("LATE_AUTH_URL", "http://127.0.0.1:9300")
LATE_AUTH_SECRET = os.environ.get("LATE_AUTH_SECRET", SSO_SECRET)

ATTACHMENT_DIR = os.environ.get("ATTACHMENT_DIR", "/var/lib/late-attachments")
MAX_ATTACHMENT_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", str(50 * 1024 * 1024)))
ATTACHMENT_TTL_DAYS = int(os.environ.get("ATTACHMENT_TTL_DAYS", "7"))
ALLOWED_MIME_PREFIXES = ("image/", "audio/", "video/", "text/", "application/pdf", "application/zip", "application/x-rar", "application/x-tar", "application/gzip", "application/json", "application/octet-stream")

LINK_PREVIEW_TTL_SECONDS = int(os.environ.get("LINK_PREVIEW_TTL_SECONDS", "21600"))

# How long after sending an author may still edit their own message.
# 15 minutes, same as WhatsApp. The client reads this from the ws `hello`
# frame to decide whether to offer the Edit action, but the server is the
# authority and re-checks it on every PATCH.
EDIT_WINDOW_SECONDS = int(os.environ.get("EDIT_WINDOW_SECONDS", "900"))
