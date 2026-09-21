from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field

SUPPORTED_PROTOCOLS = {"xray", "wireguard", "openvpn", "openconnect"}

class LoginIn(BaseModel):
    username: str
    password: str

class CountryIn(BaseModel):
    code: str = Field(min_length=2, max_length=2)
    name: str = Field(min_length=2, max_length=100)
    enabled: bool = True

class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    duration_days: int = Field(gt=0, le=3650)
    traffic_gb: int = Field(gt=0, le=100000)
    price: Decimal = Field(ge=0)
    enabled: bool = True

class SubscriberIn(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    mobile: str | None = None
    telegram_id: str | None = None
    plan_id: UUID | None = None
    enabled: bool = True
    expires_at: datetime | None = None

class NodeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    country_code: str = Field(min_length=2, max_length=2)
    public_address: str = Field(min_length=3, max_length=255)
    protocols: list[str]
    enabled: bool = True

class NodeHeartbeat(BaseModel):
    agent_version: str = Field(min_length=1, max_length=32)
    protocols: list[str]

class NodeOut(BaseModel):
    id: UUID
    name: str
    country_code: str
    public_address: str
    protocols: list[str]
    status: str
    enabled: bool
    agent_version: str | None
    last_seen_at: datetime | None
    model_config = {"from_attributes": True}
