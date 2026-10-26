#!/usr/bin/python3
import hashlib, subprocess, time
from pathlib import Path
from manager import BASE, db, expire
import unified
from runtime_config import DOMAIN, CONFIG
import fcntl

def ensure(args):
    check=args.copy()
    check[check.index("-A")]="-C"
    if subprocess.run(check,capture_output=True).returncode:
        subprocess.run(args,check=True)
def network():
    subprocess.run(["sysctl","-w","net.ipv4.ip_forward=1"],check=True)
    routes=__import__("json").loads(subprocess.check_output(["ip","-j","route","show","default"]))
    egress=CONFIG.get("egress_interface") or next(r["dev"] for r in routes if r.get("dev"))
    for iface,subnet in [("tun-masiha","10.81.0.0/24"),("vpns+","10.82.0.0/24"),("tun-masiha-ros6","10.85.0.0/24")]:
        ensure(["iptables","-w","-t","nat","-A","POSTROUTING","-s",subnet,"-o",egress,"-j","MASQUERADE"])
        ensure(["iptables","-w","-A","FORWARD","-i",iface,"-s",subnet,"-o",egress,"-j","ACCEPT"])
        ensure(["iptables","-w","-A","FORWARD","-i",egress,"-o",iface,"-d",subnet,"-m","conntrack","--ctstate","RELATED,ESTABLISHED","-j","ACCEPT"])
def main():
    BASE.mkdir(mode=0o700,parents=True,exist_ok=True)
    network()
    cert=Path("/etc/letsencrypt/live")/DOMAIN/"fullchain.pem"
    last=hashlib.sha256(cert.read_bytes()).digest()
    sales_process = None
    while True:
        try:
            if sales_process is None or sales_process.poll() is not None:
                sales_process = subprocess.Popen(["/usr/bin/python3", "/opt/masiha-vpn-extension/sales_worker.py"])
            with open(BASE/"manager.lock","a") as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                with db() as c: expire(c)
                unified.reconcile()
            current=hashlib.sha256(cert.read_bytes()).digest()
            if current!=last:
                subprocess.run(["ocserv","-t","-c","/etc/ocserv/ocserv.conf"],check=True)
                subprocess.run(["systemctl","reload","ocserv"],check=True)
                last=current
        except Exception as e:
            print("Reconciliation error:",str(e),flush=True)
        time.sleep(15)
if __name__=="__main__": main()
