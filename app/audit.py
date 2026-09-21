import json
from sqlalchemy.orm import Session
from .models import Admin, AuditLog

def audit(db: Session, admin: Admin, action: str, resource_type: str, resource_id=None, details=None):
    db.add(AuditLog(
        admin_id=admin.id,
        admin_username=admin.username,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
    ))
