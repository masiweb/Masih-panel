from datetime import datetime, timedelta, timezone
from uuid import UUID
import jwt
from fastapi import Cookie, Depends, HTTPException, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from .config import get_settings
from .database import SessionLocal
from .models import Admin

password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"

def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(value: str) -> str:
    return password_hash.hash(value)

def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)

def create_access_token(admin: Admin) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(admin.id), "username": admin.username, "iat": now, "exp": now + timedelta(hours=12)}, get_settings().app_secret, algorithm=ALGORITHM)

def current_admin(access_token: str | None = Cookie(default=None), db: Session = Depends(db_session)) -> Admin:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = jwt.decode(access_token, get_settings().app_secret, algorithms=[ALGORITHM])
        admin = db.get(Admin, UUID(payload["sub"]))
    except Exception:
        admin = None
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return admin
