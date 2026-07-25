from pydantic import BaseModel
from typing import Optional

class ExchangeRequest(BaseModel):
    token: str

class CreateChannelRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_public: bool = True
    channel_type: str = "text"

class SendMessageRequest(BaseModel):
    content: str
    is_action: bool = False
    reply_to: Optional[int] = None

class EditMessageRequest(BaseModel):
    content: str

class WebhookPayload(BaseModel):
    event: str
    data: dict

class BuzzRequest(BaseModel):
    channel_id: int
    target_user_id: int

class RoleChangeRequest(BaseModel):
    role: str | None

class MuteRequest(BaseModel):
    muted: bool

class ForwardRequest(BaseModel):
    target_channel_id: int
    target_user_id: Optional[int] = None

class CreateCategoryRequest(BaseModel):
    name: str

class UpdateCategoryRequest(BaseModel):
    name: str | None = None
    is_collapsed: bool | None = None

class UpdateChannelRequest(BaseModel):
    category_id: int | None = None
    position: int | None = None

class InviteRequest(BaseModel):
    email: str

class ReactionRequest(BaseModel):
    emoji: str
