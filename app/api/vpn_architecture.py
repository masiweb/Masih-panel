import base64
import html
import io
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..auth import current_admin, db_session
from ..config import get_settings
from ..models import (
    Admin, ClientInbound, InboundStatus, NodeJob, Plan, ServiceStatus,
    Subscriber, VPNClient, VPNInbound, VPNNode, VPNService,
)

router = APIRouter(prefix="/api/v1/admin", tags=["vpn-architecture"], dependencies=[Depends(current_admin)])
public_router = APIRouter(tags=["client-subscription"])

PROTOCOLS = {"xray", "wireguard", "openvpn", "openconnect"}
XRAY_VARIANTS = {"vless", "vmess", "trojan", "shadowsocks"}
XRAY_TRANSPORTS = {"tcp", "ws", "grpc", "httpupgrade", "xhttp", "kcp"}
XRAY_SECURITY = {"none", "tls", "reality"}


# Kept in sync with the capability predicates in 3x-ui v3.8.5.
PROTOCOL_CAPABILITIES = {
    "xray": {"ready": True, "engine": "xray", "multiple_inbounds": True,
        "variants": ["vless", "vmess", "trojan", "shadowsocks"],
        "transports": ["tcp", "kcp", "ws", "grpc", "httpupgrade", "xhttp"],
        "security": ["none", "tls", "reality"],
        "tls_protocols": ["vmess", "vless", "trojan", "shadowsocks"],
        "tls_transports": ["tcp", "ws", "http", "grpc", "httpupgrade", "xhttp"],
        "reality_protocols": ["vless", "trojan"], "reality_transports": ["tcp", "http", "grpc", "xhttp"],
        "features": ["sniffing", "sockopt", "fallbacks", "proxy-protocol", "subscription", "qr"]},
    "wireguard": {"ready": True, "engine": "wireguard-tools", "multiple_inbounds": False,
        "variants": ["native"], "transports": ["udp"], "security": ["wireguard"],
        "features": ["peer-keys", "allowed-ips", "dns", "mtu", "keepalive", "subscription", "qr"]},
    "openvpn": {"ready": True, "engine": "openvpn", "multiple_inbounds": False,
        "variants": ["server"], "transports": ["udp", "tcp"], "security": ["tls-auth", "tls-crypt"],
        "features": ["tun", "tap", "cipher", "auth", "dns", "redirect-gateway", "profile-download"]},
    "openconnect": {"ready": True, "engine": "ocserv", "multiple_inbounds": False,
        "variants": ["anyconnect"], "transports": ["tcp", "dtls"], "security": ["tls"],
        "features": ["dtls", "cisco-compatible", "dns", "max-clients", "profile-download"]},
}
REFERENCE_PROTOCOLS = {
    "hysteria": {"ready": False, "engine": "xray", "reason": "adapter pending"},
    "tuic": {"ready": False, "engine": "sing-box", "reason": "adapter pending"},
    "http": {"ready": False, "engine": "xray", "reason": "adapter pending"},
    "mixed": {"ready": False, "engine": "xray", "reason": "adapter pending"},
    "mtproto": {"ready": False, "engine": "external", "reason": "adapter pending"},
    "tunnel": {"ready": False, "engine": "xray", "reason": "adapter pending"},
    "tun": {"ready": False, "engine": "xray", "reason": "adapter pending"},
    "amneziawg": {"ready": False, "engine": "amneziawg", "reason": "adapter pending"},
}

DEFAULT_SETTINGS = {
    "xray": {"variant": "vless", "transport": "tcp", "security": "reality", "flow": "xtls-rprx-vision", "server_name": "www.microsoft.com", "fingerprint": "chrome", "sniffing": True},
    "wireguard": {"interface": "wg0", "network": "10.70.0.0/24", "dns": ["1.1.1.1", "1.0.0.1"], "mtu": 1420, "allowed_ips": ["0.0.0.0/0", "::/0"], "persistent_keepalive": 25},
    "openvpn": {"transport": "udp", "device": "tun", "network": "10.71.0.0/24", "cipher": "AES-256-GCM", "auth": "SHA256", "tls_mode": "tls-auth", "redirect_gateway": True, "dns": ["1.1.1.1", "1.0.0.1"]},
    "openconnect": {"network": "10.72.0.0/24", "dtls": True, "cisco_compatible": True, "max_clients": 256, "max_same_clients": 4, "dns": ["1.1.1.1", "1.0.0.1"]},
}


def now():
    return datetime.now(timezone.utc)


def serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    return value


def row(model):
    return {column.name: serialize(getattr(model, column.name)) for column in model.__table__.columns}


def validate_settings(protocol, settings):
    data = {**DEFAULT_SETTINGS[protocol], **(settings or {})}
    if protocol == "xray":
        if data.get("variant") not in XRAY_VARIANTS:
            raise HTTPException(422, "نوع Xray نامعتبر است")
        if data.get("transport") not in XRAY_TRANSPORTS:
            raise HTTPException(422, "Transport نامعتبر است")
        if data.get("security") not in XRAY_SECURITY:
            raise HTTPException(422, "Security نامعتبر است")
        if data.get("security") in {"tls", "reality"} and not data.get("server_name"):
            raise HTTPException(422, "برای TLS/REALITY مقدار Server Name لازم است")
        if data.get("security") == "reality" and data.get("variant") not in {"vless", "trojan"}:
            raise HTTPException(422, "REALITY فقط برای VLESS یا Trojan مجاز است")
    elif protocol == "wireguard":
        if not data.get("network") or not 576 <= int(data.get("mtu", 1420)) <= 9000:
            raise HTTPException(422, "Network یا MTU وایرگارد معتبر نیست")
    elif protocol == "openvpn":
        if data.get("transport") not in {"udp", "tcp"} or data.get("device") not in {"tun", "tap"}:
            raise HTTPException(422, "Transport یا Device اوپن‌وی‌پی‌ان معتبر نیست")
    elif protocol == "openconnect":
        if int(data.get("max_clients", 0)) < 1:
            raise HTTPException(422, "حداکثر Client باید بزرگ‌تر از صفر باشد")
    return data


class InboundIn(BaseModel):
    node_id: UUID
    name: str = Field(min_length=2, max_length=120)
    protocol: str
    listen: str = "0.0.0.0"
    port: int = Field(ge=1, le=65535)
    public_host: str | None = None
    enabled: bool = True
    settings: dict = {}
    remark: str | None = None
    sub_sort_index: int = 1

    @model_validator(mode="after")
    def protocol_check(self):
        self.protocol = self.protocol.lower()
        if self.protocol not in PROTOCOLS:
            raise ValueError("protocol is not supported")
        return self


class InboundUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    listen: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    public_host: str | None = None
    enabled: bool | None = None
    settings: dict | None = None
    remark: str | None = None
    sub_sort_index: int | None = None


class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    subscriber_id: UUID | None = None
    plan_id: UUID | None = None
    inbound_ids: list[UUID] = Field(min_length=1)
    quota_gb: int | None = Field(default=None, ge=0, le=100000)
    expires_at: datetime | None = None
    limit_ip: int = Field(default=0, ge=0, le=1000)
    telegram_id: str | None = None
    group_name: str | None = None
    comment: str | None = None
    enabled: bool = True


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    inbound_ids: list[UUID] | None = None
    quota_gb: int | None = Field(default=None, ge=0, le=100000)
    expires_at: datetime | None = None
    limit_ip: int | None = Field(default=None, ge=0, le=1000)
    telegram_id: str | None = None
    group_name: str | None = None
    comment: str | None = None
    enabled: bool | None = None


def inbound_out(item):
    data = row(item)
    data["settings"] = item.settings or {}
    return data


def inbound_runtime(db, item):
    data = inbound_out(item)
    node = db.get(VPNNode, item.node_id)
    attachments = db.scalars(select(ClientInbound).where(ClientInbound.inbound_id == item.id)).all()
    active_attachments = [a for a in attachments if a.enabled]
    service_states, used_bytes = {}, 0
    for attachment in active_attachments:
        service = db.get(VPNService, attachment.legacy_service_id) if attachment.legacy_service_id else None
        state = service.status.value if service else attachment.status
        service_states[state] = service_states.get(state, 0) + 1
        used_bytes += service.used_bytes if service else 0
    jobs = db.scalars(select(NodeJob).where(NodeJob.node_id == item.node_id).order_by(NodeJob.created_at.desc())).all()
    latest_job = next((job for job in jobs if
        str((job.payload or {}).get("inbound_id", "")) == str(item.id) or
        str(((job.payload or {}).get("inbound") or {}).get("id", "")) == str(item.id)), None)
    settings = item.settings or {}
    data["runtime"] = {
        "node": {"name": node.name if node else None, "status": serialize(node.status) if node else "missing",
                 "last_seen_at": serialize(node.last_seen_at) if node else None,
                 "agent_version": node.agent_version if node else None},
        "variant": settings.get("variant") if item.protocol == "xray" else item.protocol,
        "transport": settings.get("transport"), "security": settings.get("security"),
        "clients_total": len(attachments), "clients_enabled": len(active_attachments),
        "service_states": service_states, "used_bytes": used_bytes,
        "capabilities": PROTOCOL_CAPABILITIES.get(item.protocol, {}),
        "last_job": ({"id": str(latest_job.id), "type": latest_job.job_type,
                      "status": serialize(latest_job.status), "result": latest_job.result,
                      "created_at": serialize(latest_job.created_at),
                      "finished_at": serialize(latest_job.finished_at)} if latest_job else None),
    }
    return data


def client_out(db, item, include_configs=False):
    attachments = db.scalars(select(ClientInbound).where(ClientInbound.client_id == item.id)).all()
    config_rows = []
    for attachment in attachments:
        inbound = db.get(VPNInbound, attachment.inbound_id)
        service = db.get(VPNService, attachment.legacy_service_id) if attachment.legacy_service_id else None
        entry = row(attachment)
        entry.update({
            "inbound_name": inbound.name if inbound else None,
            "protocol": inbound.protocol if inbound else None,
            "status": service.status.value if service else entry["status"],
            "has_config": bool(service and service.client_config),
        })
        if include_configs:
            entry["client_config"] = service.client_config if service else attachment.client_config
        config_rows.append(entry)
    data = row(item)
    base = get_settings().public_base_url.rstrip("/")
    data.update({
        "quota_gb": round(item.quota_bytes / 1024 ** 3),
        "used_gb": round(item.used_bytes / 1024 ** 3, 3),
        "subscription_url": f"{base}/sub/{item.sub_id}",
        "profile_url": f"{base}/sub/{item.sub_id}?html=1",
        "attachments": config_rows,
    })
    return data


def queue_inbound_job(db, inbound, job_type):
    payload = {"inbound_id": str(inbound.id), "name": inbound.name, "protocol": inbound.protocol,
               "listen": inbound.listen, "port": inbound.port, "public_host": inbound.public_host,
               "enabled": inbound.enabled, "settings": inbound.settings or {}}
    job = NodeJob(node_id=inbound.node_id, job_type=job_type, payload=payload)
    db.add(job)
    return job


def create_attachment(db, client, inbound):
    existing = db.scalar(select(ClientInbound).where(ClientInbound.client_id == client.id, ClientInbound.inbound_id == inbound.id))
    if existing:
        return existing
    service = VPNService(
        subscriber_id=client.subscriber_id,
        plan_id=client.plan_id,
        node_id=inbound.node_id,
        protocol=inbound.protocol,
        external_id="cli_" + secrets.token_urlsafe(12),
        access_token=secrets.token_urlsafe(32),
        quota_bytes=client.quota_bytes,
        expires_at=client.expires_at,
        status=ServiceStatus.pending,
    )
    db.add(service)
    db.flush()
    attachment = ClientInbound(client_id=client.id, inbound_id=inbound.id, legacy_service_id=service.id, status="pending")
    db.add(attachment)
    db.flush()
    payload = {"service_id": str(service.id), "attachment_id": str(attachment.id), "client_id": str(client.id),
               "external_id": service.external_id, "client_name": client.name, "protocol": inbound.protocol,
               "quota_bytes": client.quota_bytes, "expires_at": client.expires_at.isoformat(),
               "subscriber_id": str(client.subscriber_id), "inbound": inbound_out(inbound)}
    db.add(NodeJob(node_id=inbound.node_id, service_id=service.id, job_type="provision", payload=payload))
    return attachment


@router.get("/protocol-capabilities")
def protocol_capabilities():
    return {"reference": {"name": "3x-ui", "version": "3.8.5"},
            "available": PROTOCOL_CAPABILITIES, "planned": REFERENCE_PROTOCOLS}


@router.get("/inbounds")
def list_inbounds(db: Session = Depends(db_session)):
    return [inbound_runtime(db, x) for x in db.scalars(select(VPNInbound).order_by(VPNInbound.created_at.desc())).all()]


@router.get("/inbounds/{inbound_id}/runtime")
def get_inbound_runtime(inbound_id: UUID, db: Session = Depends(db_session)):
    inbound = db.get(VPNInbound, inbound_id)
    if not inbound:
        raise HTTPException(404, "Inbound پیدا نشد")
    return inbound_runtime(db, inbound)


@router.post("/inbounds", status_code=201)
def add_inbound(payload: InboundIn, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    node = db.get(VPNNode, payload.node_id)
    if not node or not node.enabled:
        raise HTTPException(422, "Node معتبر و فعال نیست")
    if db.scalar(select(VPNInbound).where(VPNInbound.node_id == node.id, VPNInbound.port == payload.port, VPNInbound.listen == payload.listen)):
        raise HTTPException(409, "این Listen/Port قبلاً روی Node ثبت شده است")
    if payload.protocol != "xray" and db.scalar(select(VPNInbound).where(VPNInbound.node_id == node.id, VPNInbound.protocol == payload.protocol, VPNInbound.enabled == True)):
        raise HTTPException(409, "در نسخه فعلی هر Node فقط یک Inbound فعال از WireGuard/OpenVPN/OpenConnect می‌پذیرد؛ Xray می‌تواند چند Inbound داشته باشد")
    settings = validate_settings(payload.protocol, payload.settings)
    inbound = VPNInbound(**payload.model_dump(exclude={"settings"}), settings=settings, status=InboundStatus.pending)
    if not inbound.public_host:
        inbound.public_host = node.public_address
    db.add(inbound); db.flush()
    job = queue_inbound_job(db, inbound, "create_inbound")
    audit(db, admin, "create", "inbound", inbound.id, {"job_id": str(job.id), "protocol": inbound.protocol})
    db.commit(); db.refresh(inbound)
    return inbound_out(inbound)


@router.patch("/inbounds/{inbound_id}")
def update_inbound(inbound_id: UUID, payload: InboundUpdate, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    inbound = db.get(VPNInbound, inbound_id)
    if not inbound: raise HTTPException(404, "Inbound پیدا نشد")
    changes = payload.model_dump(exclude_unset=True)
    if "settings" in changes:
        changes["settings"] = validate_settings(inbound.protocol, changes["settings"])
    listen = changes.get("listen", inbound.listen)
    port = changes.get("port", inbound.port)
    collision = db.scalar(select(VPNInbound).where(
        VPNInbound.node_id == inbound.node_id, VPNInbound.listen == listen,
        VPNInbound.port == port, VPNInbound.id != inbound.id))
    if collision:
        raise HTTPException(409, "این Listen/Port قبلاً روی Node ثبت شده است")
    for key, value in changes.items(): setattr(inbound, key, value)
    inbound.status = InboundStatus.pending
    job = queue_inbound_job(db, inbound, "update_inbound")
    for attachment in db.scalars(select(ClientInbound).where(ClientInbound.inbound_id == inbound.id, ClientInbound.enabled == True)).all():
        if not attachment.legacy_service_id: continue
        service = db.get(VPNService, attachment.legacy_service_id)
        client = db.get(VPNClient, attachment.client_id)
        if not service or not client: continue
        service.status = ServiceStatus.pending
        job_payload = {"service_id":str(service.id), "attachment_id":str(attachment.id), "client_id":str(client.id),
                       "external_id":service.external_id, "client_name":client.name, "protocol":inbound.protocol,
                       "quota_bytes":client.quota_bytes, "expires_at":client.expires_at.isoformat(),
                       "subscriber_id":str(client.subscriber_id), "inbound":inbound_out(inbound)}
        db.add(NodeJob(node_id=inbound.node_id, service_id=service.id, job_type="update", payload=job_payload))
    audit(db, admin, "update", "inbound", inbound.id, {"job_id": str(job.id)})
    db.commit(); db.refresh(inbound)
    return inbound_out(inbound)


@router.delete("/inbounds/{inbound_id}")
def delete_inbound(inbound_id: UUID, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    inbound = db.get(VPNInbound, inbound_id)
    if not inbound: raise HTTPException(404, "Inbound پیدا نشد")
    count = len(db.scalars(select(ClientInbound).where(ClientInbound.inbound_id == inbound.id)).all())
    if count: raise HTTPException(409, "ابتدا Clientهای متصل به این Inbound را جدا کنید")
    queue_inbound_job(db, inbound, "delete_inbound")
    inbound.enabled = False; inbound.status = InboundStatus.deleting
    audit(db, admin, "delete", "inbound", inbound.id)
    db.commit(); return {"ok": True}


@router.get("/clients")
def list_clients(db: Session = Depends(db_session)):
    return [client_out(db, x) for x in db.scalars(select(VPNClient).order_by(VPNClient.created_at.desc())).all()]


@router.get("/clients/{client_id}")
def get_client(client_id: UUID, db: Session = Depends(db_session)):
    client = db.get(VPNClient, client_id)
    if not client: raise HTTPException(404, "Client پیدا نشد")
    return client_out(db, client, True)


@router.post("/clients", status_code=201)
def add_client(payload: ClientIn, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    subscriber = db.get(Subscriber, payload.subscriber_id) if payload.subscriber_id else None
    plan = db.get(Plan, payload.plan_id) if payload.plan_id else None
    if not subscriber:
        subscriber = Subscriber(username=payload.name, telegram_id=payload.telegram_id, plan_id=payload.plan_id)
        db.add(subscriber); db.flush()
    if not plan and subscriber.plan_id:
        plan = db.get(Plan, subscriber.plan_id)
    if not plan:
        raise HTTPException(422, "برای Client باید یک پلن معتبر انتخاب شود")
    quota = (payload.quota_gb if payload.quota_gb is not None else (plan.traffic_gb if plan else 0)) * 1024 ** 3
    expires = payload.expires_at or (now() + timedelta(days=plan.duration_days if plan else 30))
    client = VPNClient(subscriber_id=subscriber.id, plan_id=plan.id if plan else None, name=payload.name,
                       sub_id=secrets.token_urlsafe(24), quota_bytes=quota, expires_at=expires,
                       limit_ip=payload.limit_ip, telegram_id=payload.telegram_id, group_name=payload.group_name,
                       comment=payload.comment, enabled=payload.enabled)
    db.add(client); db.flush()
    for inbound_id in set(payload.inbound_ids):
        inbound = db.get(VPNInbound, inbound_id)
        if not inbound or not inbound.enabled: raise HTTPException(422, f"Inbound نامعتبر: {inbound_id}")
        create_attachment(db, client, inbound)
    audit(db, admin, "create", "client", client.id, {"inbounds": len(payload.inbound_ids)})
    db.commit(); db.refresh(client)
    return client_out(db, client)


@router.patch("/clients/{client_id}")
def update_client(client_id: UUID, payload: ClientUpdate, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    client = db.get(VPNClient, client_id)
    if not client: raise HTTPException(404, "Client پیدا نشد")
    changes = payload.model_dump(exclude_unset=True, exclude={"inbound_ids", "quota_gb"})
    for key, value in changes.items(): setattr(client, key, value)
    if payload.quota_gb is not None: client.quota_bytes = payload.quota_gb * 1024 ** 3
    if payload.inbound_ids is not None:
        desired = set(payload.inbound_ids)
        current = {x.inbound_id: x for x in db.scalars(select(ClientInbound).where(ClientInbound.client_id == client.id)).all()}
        for inbound_id in desired - set(current):
            inbound = db.get(VPNInbound, inbound_id)
            if not inbound or not inbound.enabled: raise HTTPException(422, f"Inbound نامعتبر: {inbound_id}")
            create_attachment(db, client, inbound)
        for inbound_id in set(current) - desired:
            attachment = current[inbound_id]
            if attachment.legacy_service_id:
                service = db.get(VPNService, attachment.legacy_service_id)
                if service:
                    db.add(NodeJob(node_id=service.node_id, service_id=service.id, job_type="revoke", payload={"service_id":str(service.id),"external_id":service.external_id,"protocol":service.protocol,"attachment_id":str(attachment.id)}))
                    service.status = ServiceStatus.revoked
            db.delete(attachment)
    audit(db, admin, "update", "client", client.id)
    db.commit(); db.refresh(client)
    return client_out(db, client)


@router.post("/clients/{client_id}/rotate-subscription")
def rotate_subscription(client_id: UUID, admin: Admin = Depends(current_admin), db: Session = Depends(db_session)):
    client = db.get(VPNClient, client_id)
    if not client: raise HTTPException(404, "Client پیدا نشد")
    client.sub_id = secrets.token_urlsafe(24)
    audit(db, admin, "rotate_subscription", "client", client.id)
    db.commit(); return client_out(db, client)


def find_attachment(db, attachment_id):
    attachment = db.get(ClientInbound, attachment_id)
    if not attachment: raise HTTPException(404, "کانفیگ پیدا نشد")
    service = db.get(VPNService, attachment.legacy_service_id) if attachment.legacy_service_id else None
    config = service.client_config if service else attachment.client_config
    if not config: raise HTTPException(409, "کانفیگ هنوز آماده نیست")
    inbound = db.get(VPNInbound, attachment.inbound_id)
    return attachment, inbound, config


@router.get("/client-configs/{attachment_id}/download")
def download_config(attachment_id: UUID, db: Session = Depends(db_session)):
    _, inbound, config = find_attachment(db, attachment_id)
    ext = {"xray":"txt", "wireguard":"conf", "openvpn":"ovpn", "openconnect":"txt"}.get(inbound.protocol, "txt")
    return Response(config, media_type="text/plain", headers={"Content-Disposition":f'attachment; filename="{inbound.name}.{ext}"'})


@router.get("/client-configs/{attachment_id}/qr")
def config_qr(attachment_id: UUID, db: Session = Depends(db_session)):
    _, _, config = find_attachment(db, attachment_id)
    if len(config.encode()) > 2200: raise HTTPException(413, "این کانفیگ برای QR بزرگ است؛ فایل را دانلود کنید")
    image = qrcode.make(config); buf = io.BytesIO(); image.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control":"no-store"})


def subscription_client(db, sub_id):
    client = db.scalar(select(VPNClient).where(VPNClient.sub_id == sub_id))
    if not client or not client.enabled or client.expires_at <= now(): raise HTTPException(404, "Subscription available نیست")
    return client


def configs_for(db, client):
    result = []
    attachments = db.scalars(select(ClientInbound).where(ClientInbound.client_id == client.id, ClientInbound.enabled == True)).all()
    for attachment in attachments:
        inbound = db.get(VPNInbound, attachment.inbound_id)
        service = db.get(VPNService, attachment.legacy_service_id) if attachment.legacy_service_id else None
        config = service.client_config if service else attachment.client_config
        if inbound and inbound.enabled and config and (not service or service.status == ServiceStatus.active):
            result.append((attachment, inbound, config))
    return sorted(result, key=lambda x: x[1].sub_sort_index)


@public_router.get("/sub/{sub_id}")
def client_subscription(sub_id: str, request: Request, html: int = 0, db: Session = Depends(db_session)):
    client = db.scalar(select(VPNClient).where(VPNClient.sub_id == sub_id))
    if not client:
        legacy = db.scalar(select(VPNService).where(VPNService.access_token == sub_id))
        if not legacy or legacy.status != ServiceStatus.active or not legacy.client_config:
            raise HTTPException(404, "Subscription available نیست")
        return Response(base64.b64encode(legacy.client_config.encode()).decode(), media_type="text/plain",
                        headers={"Cache-Control":"no-store", "Profile-Update-Interval":"12"})
    if not client.enabled or client.expires_at <= now():
        raise HTTPException(404, "Subscription available نیست")
    items = configs_for(db, client)
    client.last_sub_fetch_at = now(); db.commit()
    if html or "text/html" in request.headers.get("accept", ""):
        cards = []
        for attachment, inbound, config in items:
            qr = f"/sub/{sub_id}/qr/{attachment.id}"
            download = f"/sub/{sub_id}/download/{attachment.id}"
            copy_value = html_module(config)
            cards.append(f'<section><h2>{html_module(inbound.name)}</h2><p>{html_module(inbound.protocol.upper())}</p><img src="{qr}" alt="QR"><textarea readonly>{copy_value}</textarea><p><a href="{download}">دانلود کانفیگ</a></p></section>')
        remain = max(0, client.quota_bytes-client.used_bytes)
        page = f'''<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Masiha VPN</title><style>body{{font-family:Tahoma;background:#06101c;color:#fff;max-width:900px;margin:auto;padding:24px}}header,section{{background:#0d1d2f;border:1px solid #29445e;border-radius:16px;padding:20px;margin:14px}}img{{width:220px;max-width:100%;background:#fff;padding:8px;border-radius:10px}}textarea{{width:100%;min-height:100px;background:#030914;color:#5eead4;direction:ltr}}a{{color:#2dd4bf}}</style></head><body><header><h1>{html_module(client.name)}</h1><p>حجم باقی‌مانده: {remain/1024**3:.2f} GB</p><p>انقضا: {html_module(client.expires_at.isoformat())}</p></header>{''.join(cards)}</body></html>'''
        return HTMLResponse(page, headers={"Cache-Control":"no-store"})
    body = "\n".join(config for _, _, config in items)
    headers = {"Cache-Control":"no-store", "Profile-Update-Interval":"12", "Profile-Title":"Masiha VPN",
               "Subscription-Userinfo":f"upload=0; download={client.used_bytes}; total={client.quota_bytes}; expire={int(client.expires_at.timestamp())}"}
    return Response(base64.b64encode(body.encode()).decode(), media_type="text/plain", headers=headers)


def html_module(value):
    return html.escape(str(value), quote=True)


@public_router.get("/sub/{sub_id}/qr/{attachment_id}")
def public_qr(sub_id: str, attachment_id: UUID, db: Session = Depends(db_session)):
    client = subscription_client(db, sub_id)
    attachment, _, config = find_attachment(db, attachment_id)
    if attachment.client_id != client.id: raise HTTPException(404, "Config پیدا نشد")
    value = config if len(config.encode()) <= 2200 else f"{get_settings().public_base_url}/sub/{sub_id}/download/{attachment_id}"
    image = qrcode.make(value); buf = io.BytesIO(); image.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control":"no-store"})


@public_router.get("/sub/{sub_id}/download/{attachment_id}")
def public_download(sub_id: str, attachment_id: UUID, db: Session = Depends(db_session)):
    client = subscription_client(db, sub_id)
    attachment, inbound, config = find_attachment(db, attachment_id)
    if attachment.client_id != client.id: raise HTTPException(404, "Config پیدا نشد")
    ext = {"xray":"txt", "wireguard":"conf", "openvpn":"ovpn", "openconnect":"txt"}.get(inbound.protocol, "txt")
    return Response(config, media_type="text/plain", headers={"Content-Disposition":f'attachment; filename="{inbound.name}.{ext}"', "Cache-Control":"no-store"})
