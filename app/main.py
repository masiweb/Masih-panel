from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis import Redis
from sqlalchemy import select, text
from . import __version__
from .api.admin_api import router as admin_router
from .api.auth_api import router as auth_router
from .api.nodes import router as nodes_router
from .api.services import router as services_router, public_router as subscription_router
from .api.node_agent_api import router as agent_router
from .api.vpn_architecture import router as vpn_router, public_router as client_subscription_router
from .auth import hash_password
from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin
from .migrate_architecture import migrate_legacy_services

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as migration_db:
        migrate_legacy_services(migration_db)
    settings = get_settings()
    if settings.bootstrap_admin_password:
        with SessionLocal() as db:
            if not db.scalar(select(Admin).where(Admin.username == settings.bootstrap_admin_username)):
                db.add(Admin(username=settings.bootstrap_admin_username, password_hash=hash_password(settings.bootstrap_admin_password)))
                db.commit()
    yield

app = FastAPI(title="Masiha VPN Platform", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(nodes_router)
app.include_router(services_router)
app.include_router(client_subscription_router)
app.include_router(subscription_router)
app.include_router(agent_router)
app.include_router(vpn_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.get("/")
def root(): return {"name": "Masiha VPN Platform", "version": __version__, "status": "running"}

@app.get("/admin/login")
def admin_login(request: Request): return templates.TemplateResponse("login.html", {"request": request})

@app.get("/admin")
def admin_dashboard(request: Request):
    if not request.cookies.get("access_token"): return RedirectResponse("/admin/login", 302)
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/health")
def health():
    services = {}
    try:
        with engine.connect() as conn: conn.execute(text("SELECT 1"))
        services["database"] = "ok"
    except Exception: services["database"] = "error"
    try:
        Redis.from_url(get_settings().redis_url, socket_timeout=2).ping()
        services["redis"] = "ok"
    except Exception: services["redis"] = "error"
    return {"ok": all(v == "ok" for v in services.values()), "version": __version__, "services": services}
