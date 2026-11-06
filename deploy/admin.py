#!/usr/bin/env python3
"""Admin-only CLI. Tokens are read from a file, never command-line arguments."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import urllib.request
from urllib.parse import urlparse


def init_admin_token(path: Path) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:file.write(secrets.token_urlsafe(48)+'\n')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    parser.add_argument('--token-file',type=Path,default=Path('data/admin.token'))
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init-token');sub.add_parser('status');sub.add_parser('usage')
    run=sub.add_parser('run');run.add_argument('--date',required=True);run.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    if args.command=='init-token':
        init_admin_token(args.token_file);print('Administrator token created:',args.token_file);return
    url=args.url.rstrip('/');parsed=urlparse(url)
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
        parser.error('远程管理必须使用HTTPS；HTTP仅允许本机或SSH隧道入口。')
    token=args.token_file.read_text().strip()
    if not token:parser.error('管理令牌文件为空。')
    data=None
    if args.command=='run':
        path='/api/runs';method='POST';data=json.dumps({'date':args.date,'refresh':args.refresh}).encode()
    elif args.command=='usage':path='/api/admin/usage';method='GET'
    else:path='/api/runs/active';method='GET'
    request=urllib.request.Request(url+path,data=data,method=method,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:
        print(json.dumps(json.load(response),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
