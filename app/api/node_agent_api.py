from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import ClientInbound, InboundStatus, JobStatus, NodeJob, ServiceStatus, VPNInbound, VPNNode, VPNService
from ..node_security import verify_node_token
from ..schemas import JobResultIn, NodeHeartbeat

router=APIRouter(prefix="/api/v1/agent",tags=["node-agent"])
def db_session():
    db=SessionLocal()
    try: yield db
    finally: db.close()
def node_auth(node_id:str,x_node_token:str=Header(default=""),db:Session=Depends(db_session))->VPNNode:
    node=db.get(VPNNode,node_id)
    if not node or not verify_node_token(x_node_token,getattr(node,"token_hash",None)): raise HTTPException(401,"Invalid node credentials")
    if not node.enabled: raise HTTPException(403,"Node disabled")
    return node

@router.post("/{node_id}/heartbeat")
def heartbeat(node_id:str,payload:NodeHeartbeat,node:VPNNode=Depends(node_auth),db:Session=Depends(db_session)):
    current=db.get(VPNNode,node.id); current.agent_version=payload.agent_version; current.protocols=",".join(sorted(set(payload.protocols)))
    current.status="online"; current.last_seen_at=datetime.now(timezone.utc); db.commit()
    return {"ok":True,"server_time":datetime.now(timezone.utc).isoformat()}

@router.get("/{node_id}/jobs/next")
def next_job(node_id:str,node:VPNNode=Depends(node_auth),db:Session=Depends(db_session)):
    job=db.scalar(select(NodeJob).where(NodeJob.node_id==node.id,NodeJob.status==JobStatus.queued).order_by(NodeJob.created_at).with_for_update(skip_locked=True))
    if not job: return {"job":None}
    job.status=JobStatus.processing; job.started_at=datetime.now(timezone.utc); db.commit()
    return {"job":{"id":str(job.id),"type":job.job_type,"payload":job.payload}}

@router.post("/{node_id}/jobs/{job_id}/result")
def job_result(node_id:str,job_id:str,payload:JobResultIn,node:VPNNode=Depends(node_auth),db:Session=Depends(db_session)):
    job=db.get(NodeJob,job_id)
    if not job or job.node_id!=node.id: raise HTTPException(404,"Job not found")
    job.status=JobStatus.completed if payload.ok else JobStatus.failed; job.result=payload.model_dump(); job.finished_at=datetime.now(timezone.utc)
    inbound_id=(job.payload or {}).get("inbound_id")
    if inbound_id:
        inbound=db.get(VPNInbound,inbound_id)
        if inbound:
            if job.job_type=="delete_inbound" and payload.ok:
                inbound.status=InboundStatus.disabled; inbound.enabled=False
            else:
                inbound.status=InboundStatus.active if payload.ok else InboundStatus.failed
    if job.service_id:
        service=db.get(VPNService,job.service_id)
        if service:
            if payload.ok:
                service.status=ServiceStatus.revoked if job.job_type=="revoke" else ServiceStatus.active
                if payload.client_config: service.client_config=payload.client_config
                if payload.used_bytes is not None: service.used_bytes=payload.used_bytes
            else: service.status=ServiceStatus.failed
            attachment=db.scalar(select(ClientInbound).where(ClientInbound.legacy_service_id==service.id))
            if attachment:
                attachment.status=service.status.value
                if payload.client_config: attachment.client_config=payload.client_config
    db.commit(); return {"ok":True}
