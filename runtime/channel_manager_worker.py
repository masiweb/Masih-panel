#!/usr/bin/env python3
"""Isolated scheduled AI drafting/approved publishing; no impact on bot polling."""
import fcntl
from pathlib import Path
import channel_manager
import sales

LOCK=Path("/run/masiha-channel/worker.lock")

def run():
 LOCK.parent.mkdir(parents=True,exist_ok=True)
 with LOCK.open("a+") as handle:
  fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with sales.conn() as c:
   channel_manager.tick(c)
   c.commit()

if __name__=="__main__":
 run()
