#!/usr/bin/env python3
"""Idempotently install/refresh the reviewed Masiha Nginx route bundle."""
from pathlib import Path
import sys

def merge(target: Path, bundle_path: Path) -> None:
    text=target.read_text()
    bundle=bundle_path.read_text().rstrip()+"\n"
    start_marker="# MASIHA-NATIVE-AUTH-ROUTES BEGIN"
    end_marker="# MASIHA-SIDECAR END"
    if "MASIHA-SIDECAR BEGIN" in text:
        start_at=text.find(start_marker)
        end_at=text.find(end_marker,start_at if start_at>=0 else 0)
        if start_at<0 or end_at<0:
            raise SystemExit("Incomplete Masiha Nginx markers; refusing duplicate routes")
        comment_at=text.rfind("# Native x-ui path-scoped cookie bootstrap.",0,start_at)
        if comment_at>=0:
            start=text.rfind("\n",0,comment_at)+1
        else:
            start=text.rfind("\n",0,start_at)+1
        end=end_at+len(end_marker)
        text=text[:start]+bundle.rstrip()+text[end:]
    else:
        if "MASIHA-NATIVE-AUTH-ROUTES BEGIN" in text or "location = /masiha-ui" in text:
            raise SystemExit("Unmarked Masiha Nginx routes already exist; manual review required")
        anchor="  location /sub/ {"
        if text.count(anchor)!=1:
            raise SystemExit("Could not find unique Nginx insertion point for Masiha routes")
        text=text.replace(anchor,bundle+anchor,1)
    if 'sub_filter "</body>"' not in text:
        anchor='    proxy_set_header Upgrade $http_upgrade;'
        if text.count(anchor)!=1:
            raise SystemExit("Could not find unique Nginx HTML injection point")
        injection=(
            '    proxy_set_header Accept-Encoding "";\n'
            '    sub_filter_once on;\n'
            '    sub_filter "</body>" \'<script src="/masiha-ui/nav.js" data-cfasync="false"></script></body>\';\n'
            +anchor
        )
        text=text.replace(anchor,injection,1)
    target.write_text(text)

if __name__=="__main__":
    if len(sys.argv)!=3:
        raise SystemExit("Usage: merge-sidecar-nginx.py NGINX_SITE ROUTE_BUNDLE")
    merge(Path(sys.argv[1]),Path(sys.argv[2]))
