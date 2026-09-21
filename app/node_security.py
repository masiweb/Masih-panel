import hashlib, hmac, secrets
from .config import get_settings

def issue_node_token() -> tuple[str, str]:
    raw = "mnp_" + secrets.token_urlsafe(36)
    return raw, hash_node_token(raw)

def hash_node_token(raw: str) -> str:
    return hmac.new(get_settings().app_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

def verify_node_token(raw: str, stored: str | None) -> bool:
    return bool(raw and stored and hmac.compare_digest(hash_node_token(raw), stored))
