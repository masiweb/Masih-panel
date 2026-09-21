from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import create_access_token, current_admin, db_session, verify_password
from ..models import Admin
from ..schemas import LoginIn

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.post("/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(db_session)):
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if not admin or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="نام کاربری یا رمز عبور اشتباه است")
    response.set_cookie("access_token", create_access_token(admin), httponly=True, secure=True, samesite="lax", max_age=43200, path="/")
    return {"ok": True, "username": admin.username}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}

@router.get("/me")
def me(admin: Admin = Depends(current_admin)):
    return {"id": str(admin.id), "username": admin.username}
