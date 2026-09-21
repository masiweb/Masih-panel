import secrets
from sqlalchemy import select

from .models import ClientInbound, InboundStatus, VPNClient, VPNInbound, VPNNode, VPNService


PORTS = {"xray": 8443, "wireguard": 51820, "openvpn": 1194, "openconnect": 4443}
SETTINGS = {
    "xray": {"variant":"vless", "transport":"tcp", "security":"reality", "flow":"xtls-rprx-vision", "server_name":"www.microsoft.com", "fingerprint":"chrome", "sniffing":True},
    "wireguard": {"interface":"wg0", "network":"10.70.0.0/24", "dns":["1.1.1.1","1.0.0.1"], "mtu":1420, "allowed_ips":["0.0.0.0/0","::/0"], "persistent_keepalive":25},
    "openvpn": {"transport":"udp", "device":"tun", "network":"10.71.0.0/24", "cipher":"AES-256-GCM", "auth":"SHA256", "tls_mode":"tls-auth", "redirect_gateway":True, "dns":["1.1.1.1","1.0.0.1"]},
    "openconnect": {"network":"10.72.0.0/24", "dtls":True, "cisco_compatible":True, "max_clients":256, "max_same_clients":4, "dns":["1.1.1.1","1.0.0.1"]},
}


def migrate_legacy_services(db):
    services = db.scalars(select(VPNService).order_by(VPNService.created_at)).all()
    if not services:
        return {"clients":0, "inbounds":0, "attachments":0}
    inbound_map = {}
    made_inbounds = 0
    for service in services:
        key = (service.node_id, service.protocol)
        inbound = db.scalar(select(VPNInbound).where(VPNInbound.node_id == service.node_id, VPNInbound.protocol == service.protocol))
        if not inbound:
            node = db.get(VPNNode, service.node_id)
            inbound = VPNInbound(node_id=service.node_id, name=f"{node.name}-{service.protocol.upper()}", protocol=service.protocol,
                                 listen="0.0.0.0", port=PORTS.get(service.protocol, 1), public_host=node.public_address,
                                 enabled=True, settings=SETTINGS.get(service.protocol, {}), status=InboundStatus.active,
                                 remark="مهاجرت خودکار از سرویس فعال نسخه 0.6")
            db.add(inbound); db.flush(); made_inbounds += 1
        inbound_map[key] = inbound
    clients = {}
    made_clients = 0
    attachments = 0
    for service in services:
        client = clients.get(service.subscriber_id) or db.scalar(select(VPNClient).where(VPNClient.subscriber_id == service.subscriber_id))
        if not client:
            subscriber = service.subscriber_id
            from .models import Subscriber
            user = db.get(Subscriber, subscriber)
            related = [x for x in services if x.subscriber_id == subscriber]
            client = VPNClient(subscriber_id=subscriber, plan_id=service.plan_id, name=user.username,
                               sub_id=secrets.token_urlsafe(24), quota_bytes=max(x.quota_bytes for x in related),
                               used_bytes=sum(x.used_bytes for x in related), expires_at=max(x.expires_at for x in related),
                               telegram_id=user.telegram_id, enabled=user.enabled)
            db.add(client); db.flush(); made_clients += 1
        clients[service.subscriber_id] = client
        exists = db.scalar(select(ClientInbound).where(ClientInbound.legacy_service_id == service.id))
        if not exists:
            inbound = inbound_map[(service.node_id, service.protocol)]
            db.add(ClientInbound(client_id=client.id, inbound_id=inbound.id, legacy_service_id=service.id,
                                 enabled=service.status.value != "revoked", status=service.status.value,
                                 client_config=service.client_config))
            attachments += 1
    db.commit()
    return {"clients":made_clients, "inbounds":made_inbounds, "attachments":attachments}
