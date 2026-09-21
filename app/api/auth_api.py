from fastapi import APIRouter, Depends, HTTPException, Request, Response
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..audit import audit
from ..auth import create_access_token, current_admin, db_session, hash_password, verify_password
from ..config import get_settings
from ..models import Admin
from ..schemas import LoginIn, PasswordChangeIn

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

def limiter():
    return Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=2)

@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(db_session)):
    ip = request.client.host if request.client else "unknown"
    key = f"auth:login:{ip}"
    try:
        redis = limiter()
        attempts = redis.incr(key)
        if attempts == 1: redis.expire(key, 300)
        if attempts > 5:
            raise HTTPException(status_code=429, detail="تلاش‌های ورود بیش از حد است؛ پنج دقیقه بعد دوباره امتحان کنید")
    except HTTPException:
        raise
    except Exception:
        redis = None
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if not admin or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="نام کاربری یا رمز عبور اشتباه است")
    if redis: redis.delete(key)
    response.set_cookie("access_token", create_access_token(admin), httponly=True, secure=True, samesite="lax", max_age=43200, path="/")
    audit(db, admin, "login", "admin", admin.id, {"ip": ip}); db.commit()
    return {"ok": True, "username": admin.username}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}

@router.get("/me")
def me(admin: Admin = Depends(current_admin)):
    return {"id": str(admin.id), "username": admin.username}

@router.post("/change-password")
def change_password(payload: PasswordChangeIn, response: Response, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(400, "رمز فعلی صحیح نیست")
    if payload.current_password == payload.new_password:
        raise HTTPException(400, "رمز جدید باید متفاوت باشد")
    admin.password_hash = hash_password(payload.new_password)
    audit(db, admin, "password_changed", "admin", admin.id)
    db.commit()
    response.delete_cookie("access_token", path="/")
    return {"ok": True, "reauthenticate": True}
