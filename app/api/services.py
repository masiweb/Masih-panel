import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..audit import audit
from ..auth import current_admin, db_session
from ..models import Admin, JobStatus, NodeJob, Order, OrderStatus, Plan, ServiceStatus, Subscriber, VPNNode, VPNService
from ..schemas import OrderCreate, ServiceCreate, ServiceRenew

router = APIRouter(prefix="/api/v1/admin", tags=["services"], dependencies=[Depends(current_admin)])

def obj(x):
    d={c.name:getattr(x,c.name) for c in x.__table__.columns}
    for k,v in list(d.items()):
        if hasattr(v,"isoformat"): d[k]=v.isoformat()
        elif hasattr(v,"value"): d[k]=v.value
        elif not isinstance(v,(str,int,float,bool,dict,list,type(None))): d[k]=str(v)
    return d

def create_job(db, service, job_type):
    payload={"service_id":str(service.id),"external_id":service.external_id,"protocol":service.protocol,
        "quota_bytes":service.quota_bytes,"expires_at":service.expires_at.isoformat(),"subscriber_id":str(service.subscriber_id)}
    job=NodeJob(node_id=service.node_id,service_id=service.id,job_type=job_type,payload=payload)
    db.add(job)
    return job

@router.get("/services")
def list_services(db:Session=Depends(db_session)):
    return [obj(x) for x in db.scalars(select(VPNService).order_by(VPNService.created_at.desc())).all()]

@router.post("/services",status_code=201)
def create_service(payload:ServiceCreate,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    user=db.get(Subscriber,payload.subscriber_id); plan=db.get(Plan,payload.plan_id); node=db.get(VPNNode,payload.node_id)
    if not user or not plan or not node: raise HTTPException(422,"User, plan or node not found")
    protocol=payload.protocol.lower()
    if protocol not in [p for p in node.protocols.split(",") if p]: raise HTTPException(422,"Protocol is not enabled on node")
    if not node.enabled: raise HTTPException(422,"Node is disabled")
    service=VPNService(subscriber_id=user.id,plan_id=plan.id,node_id=node.id,protocol=protocol,
        external_id="svc_"+secrets.token_urlsafe(12),quota_bytes=plan.traffic_gb*1024**3,
        expires_at=datetime.now(timezone.utc)+timedelta(days=plan.duration_days))
    db.add(service); db.flush(); job=create_job(db,service,"provision")
    audit(db,admin,"create","service",service.id,{"job_id":str(job.id),"protocol":protocol}); db.commit(); db.refresh(service)
    return obj(service)

@router.post("/services/{service_id}/renew")
def renew(service_id:str,payload:ServiceRenew,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    service=db.get(VPNService,service_id)
    if not service: raise HTTPException(404,"Service not found")
    plan=db.get(Plan,payload.plan_id or service.plan_id)
    if not plan: raise HTTPException(422,"Plan not found")
    base=max(service.expires_at,datetime.now(timezone.utc)); service.plan_id=plan.id
    service.expires_at=base+timedelta(days=plan.duration_days); service.quota_bytes+=plan.traffic_gb*1024**3
    service.status=ServiceStatus.pending; job=create_job(db,service,"update")
    audit(db,admin,"renew","service",service.id,{"job_id":str(job.id)}); db.commit(); db.refresh(service); return obj(service)

@router.post("/services/{service_id}/revoke")
def revoke(service_id:str,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    service=db.get(VPNService,service_id)
    if not service: raise HTTPException(404,"Service not found")
    service.status=ServiceStatus.revoked; job=create_job(db,service,"revoke")
    audit(db,admin,"revoke","service",service.id,{"job_id":str(job.id)}); db.commit(); return {"ok":True,"job_id":str(job.id)}

@router.get("/orders")
def orders(db:Session=Depends(db_session)):
    return [obj(x) for x in db.scalars(select(Order).order_by(Order.created_at.desc())).all()]

@router.post("/orders",status_code=201)
def create_order(payload:OrderCreate,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    user=db.get(Subscriber,payload.subscriber_id); plan=db.get(Plan,payload.plan_id)
    if not user or not plan: raise HTTPException(422,"User or plan not found")
    order=Order(subscriber_id=user.id,plan_id=plan.id,amount=plan.price,source=payload.source)
    db.add(order); db.flush(); audit(db,admin,"create","order",order.id); db.commit(); db.refresh(order); return obj(order)

@router.post("/orders/{order_id}/paid")
def mark_paid(order_id:str,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    order=db.get(Order,order_id)
    if not order: raise HTTPException(404,"Order not found")
    order.status=OrderStatus.paid; audit(db,admin,"paid","order",order.id); db.commit(); return obj(order)
