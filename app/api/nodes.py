from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..audit import audit
from ..auth import current_admin
from ..database import SessionLocal
from ..models import Admin, Country, NodeStatus, VPNNode
from ..node_security import issue_node_token
from ..schemas import NodeCreate, NodeOut, NodeUpdate, SUPPORTED_PROTOCOLS

router=APIRouter(prefix="/api/v1/nodes",tags=["nodes"])
def db_session():
    db=SessionLocal()
    try: yield db
    finally: db.close()
def validate_protocols(values:list[str])->list[str]:
    values=sorted({p.strip().lower() for p in values}); bad=set(values)-SUPPORTED_PROTOCOLS
    if bad: raise HTTPException(422,f"Unsupported protocols: {sorted(bad)}")
    return values
def serialize(n:VPNNode)->dict:
    return {"id":str(n.id),"name":n.name,"country_code":n.country_code,"public_address":n.public_address,
        "protocols":[p for p in n.protocols.split(",") if p],"status":n.status.value,"enabled":n.enabled,
        "agent_version":n.agent_version,"last_seen_at":n.last_seen_at.isoformat() if n.last_seen_at else None}

@router.get("",dependencies=[Depends(current_admin)])
def list_nodes(db:Session=Depends(db_session)): return [serialize(n) for n in db.scalars(select(VPNNode).order_by(VPNNode.created_at.desc())).all()]

@router.post("",status_code=201)
def create_node(payload:NodeCreate,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    if db.scalar(select(VPNNode).where(VPNNode.name==payload.name)): raise HTTPException(409,"Node name already exists")
    code=payload.country_code.upper()
    if not db.scalar(select(Country).where(Country.code==code)): raise HTTPException(422,"Country not found")
    token,token_hash=issue_node_token()
    n=VPNNode(name=payload.name,country_code=code,public_address=payload.public_address,protocols=",".join(validate_protocols(payload.protocols)),enabled=payload.enabled,token_hash=token_hash)
    db.add(n); db.flush(); audit(db,admin,"create","node",n.id,{"name":n.name}); db.commit(); db.refresh(n)
    return {"node":serialize(n),"node_token":token,"warning":"This token is shown once. Store it securely."}

@router.post("/{node_id}/rotate-token")
def rotate_token(node_id:str,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    n=db.get(VPNNode,node_id)
    if not n: raise HTTPException(404,"Node not found")
    token,n.token_hash=issue_node_token(); audit(db,admin,"rotate_token","node",n.id); db.commit()
    return {"node_token":token,"warning":"Previous token is now invalid."}

@router.patch("/{node_id}")
def update_node(node_id:str,payload:NodeUpdate,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    n=db.get(VPNNode,node_id)
    if not n: raise HTTPException(404,"Node not found")
    data=payload.model_dump(exclude_unset=True)
    if "country_code" in data:
        data["country_code"]=data["country_code"].upper()
        if not db.scalar(select(Country).where(Country.code==data["country_code"])): raise HTTPException(422,"Country not found")
    if "protocols" in data: data["protocols"]=",".join(validate_protocols(data["protocols"]))
    for k,v in data.items(): setattr(n,k,v)
    if not n.enabled:n.status=NodeStatus.disabled
    elif n.status==NodeStatus.disabled:n.status=NodeStatus.pending
    audit(db,admin,"update","node",n.id,data); db.commit(); db.refresh(n); return serialize(n)

@router.delete("/{node_id}")
def delete_node(node_id:str,admin:Admin=Depends(current_admin),db:Session=Depends(db_session)):
    n=db.get(VPNNode,node_id)
    if not n: raise HTTPException(404,"Node not found")
    audit(db,admin,"delete","node",n.id,{"name":n.name}); db.delete(n); db.commit(); return {"ok":True}
