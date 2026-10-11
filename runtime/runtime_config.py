"""Host configuration; never store credentials in source."""
import json,os,re
from pathlib import Path
CONFIG_PATH=Path(os.environ.get("MASIHA_CONFIG","/etc/masiha-vpn-extension/config.json"))
CONFIG=json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
DOMAIN=os.environ.get("MASIHA_DOMAIN",CONFIG.get("domain","localhost"))
if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",DOMAIN):raise ValueError("Invalid public domain")
PANEL_PATH=CONFIG.get("panel_path","/xui/")
PUBLIC_PANEL_URL="https://"+DOMAIN+PANEL_PATH.rstrip("/")
