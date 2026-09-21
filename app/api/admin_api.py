from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..auth import current_admin, db_session
from ..models import Admin, Country, Plan, Subscriber, VPNNode
from ..schemas import CountryIn, PlanIn, SubscriberIn

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(current_admin)])

def obj(model):
    data = {c.name: getattr(model, c.name) for c in model.__table__.columns}
    for k, v in list(data.items()):
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
        elif not isinstance(v, (str, int, float, bool, type(None))):
            data[k] = str(v)
    return data

@router.get("/summary")
def summary(db: Session = Depends(db_session)):
    return {
        "users": db.scalar(select(func.count()).select_from(Subscriber)),
        "plans": db.scalar(select(func.count()).select_from(Plan)),
        "countries": db.scalar(select(func.count()).select_from(Country)),
        "nodes": db.scalar(select(func.count()).select_from(VPNNode)),
        "online_nodes": db.scalar(select(func.count()).select_from(VPNNode).where(VPNNode.status == "online")),
    }

@router.get("/countries")
def countries(db: Session = Depends(db_session)):
    return [obj(x) for x in db.scalars(select(Country).order_by(Country.name)).all()]

@router.post("/countries", status_code=201)
def country_create(payload: CountryIn, db: Session = Depends(db_session)):
    code = payload.code.upper()
    if db.scalar(select(Country).where(Country.code == code)):
        raise HTTPException(409, "Country code already exists")
    item = Country(code=code, name=payload.name, enabled=payload.enabled)
    db.add(item); db.commit(); db.refresh(item)
    return obj(item)

@router.delete("/countries/{item_id}")
def country_delete(item_id: str, db: Session = Depends(db_session)):
    item = db.get(Country, item_id)
    if not item: raise HTTPException(404, "Country not found")
    db.delete(item); db.commit()
    return {"ok": True}

@router.get("/plans")
def plans(db: Session = Depends(db_session)):
    return [obj(x) for x in db.scalars(select(Plan).order_by(Plan.created_at.desc())).all()]

@router.post("/plans", status_code=201)
def plan_create(payload: PlanIn, db: Session = Depends(db_session)):
    if db.scalar(select(Plan).where(Plan.name == payload.name)):
        raise HTTPException(409, "Plan name already exists")
    item = Plan(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return obj(item)

@router.delete("/plans/{item_id}")
def plan_delete(item_id: str, db: Session = Depends(db_session)):
    item = db.get(Plan, item_id)
    if not item: raise HTTPException(404, "Plan not found")
    if db.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.plan_id == item.id)):
        raise HTTPException(409, "Plan is assigned to users")
    db.delete(item); db.commit()
    return {"ok": True}

@router.get("/users")
def users(db: Session = Depends(db_session)):
    return [obj(x) for x in db.scalars(select(Subscriber).order_by(Subscriber.created_at.desc())).all()]

@router.post("/users", status_code=201)
def user_create(payload: SubscriberIn, db: Session = Depends(db_session)):
    if db.scalar(select(Subscriber).where(Subscriber.username == payload.username)):
        raise HTTPException(409, "Username already exists")
    if payload.plan_id and not db.get(Plan, payload.plan_id):
        raise HTTPException(422, "Plan not found")
    item = Subscriber(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return obj(item)

@router.delete("/users/{item_id}")
def user_delete(item_id: str, db: Session = Depends(db_session)):
    item = db.get(Subscriber, item_id)
    if not item: raise HTTPException(404, "User not found")
    db.delete(item); db.commit()
    return {"ok": True}
