"""Administrator access is separate from anonymous reading."""
from __future__ import annotations
import hmac
import os
from pathlib import Path
from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    expected=os.environ.get('HF_PAPER_ADMIN_TOKEN','').strip()
    token_file=os.environ.get('HF_PAPER_ADMIN_TOKEN_FILE','').strip()
    if not expected and token_file:
        try:expected=Path(token_file).read_text().strip()
        except OSError:expected=''
    scheme,_,provided=request.headers.get('authorization','').partition(' ')
    if not expected or scheme.lower()!='bearer' or not hmac.compare_digest(expected.encode(),provided.encode()):
        raise HTTPException(status_code=403,detail='此操作仅限管理员。')
