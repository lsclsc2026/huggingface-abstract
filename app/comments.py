"""Anonymous comment storage with stable pseudonyms and persistent flood controls."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import os
from pathlib import Path
import tempfile
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ConfigError
from .db import Database

MAX_LENGTH = 1000
MIN_INTERVAL = 15
HOURLY_LIMIT = 20
COLORS = ('#476c9b','#8a5b93','#3b7f69','#a56636','#9b4d57','#586c34',
          '#496e80','#80613c','#5d609a','#92677c','#3d796f','#775c96')


def comments_enabled() -> bool:
    # Explicit opt-in: public deployments remain read-only until the operator
    # has established the applicable publication and identity requirements.
    return os.environ.get('HF_PAPER_COMMENTS_ENABLED','').strip().lower() in {'1','true','yes'}


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=MAX_LENGTH, strict=True)

    @field_validator('content')
    @classmethod
    def clean_content(cls, value: str) -> str:
        value=value.strip()
        if not value or any(ord(char)<32 and char not in '\n\t\r' for char in value):
            raise ValueError('评论需为1–1000字的纯文本，不得包含控制字符。')
        return value


def identity_key(path: Path) -> bytes:
    if not path.exists():
        # Publish a completely written file atomically; simultaneous initializers
        # share the first key instead of observing a partially written secret.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temp=Path(file.name)
            os.fchmod(file.fileno(),0o600)
            file.write(os.urandom(32));file.flush();os.fsync(file.fileno())
        try:
            try:os.link(temp,path)
            except FileExistsError:pass
        finally:temp.unlink(missing_ok=True)
    key=path.read_bytes()
    if len(key)!=32:raise ConfigError('评论标识密钥文件无效，请从备份恢复，勿直接删除旧密钥。')
    return key


def masked_ip(address) -> str:
    if address.version==4:return '.'.join(str(address).split('.')[:2])+'.*.*'
    parts=address.exploded.split(':')[:3]
    return ':'.join(format(int(part,16),'x') for part in parts)+':*:*:*:*:*'


class Comments:
    def __init__(self, db: Database, data_dir: Path):
        self.db=db
        self.key=identity_key(data_dir/'comment_identity.key')
        self.clock=time.time

    @staticmethod
    def public(row) -> dict:
        digest=row['visitor_hash']
        return {'id':row['public_id'],'paper_id':row['paper_id'],'content':row['content'],
                'created_at':row['created_at'],'ip':row['masked_ip'],
                'visitor':'访客 '+digest[:6].upper(),
                'avatar_color':COLORS[int(digest[:8],16)%len(COLORS)]}

    def create(self, paper_id: str, content: str, peer: str) -> dict:
        try:
            address=ipaddress.ip_address(peer)
            if address.version==6 and address.ipv4_mapped:address=address.ipv4_mapped
        except ValueError as exc:
            raise HTTPException(400,'无法确定评论来源地址。') from exc
        digest=hmac.new(self.key,address.compressed.encode(),hashlib.sha256).hexdigest()
        now=self.clock()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM papers WHERE id=?',(paper_id,)).fetchone():
                raise HTTPException(404,'论文不存在。')
            rate=conn.execute('SELECT * FROM comment_limits WHERE visitor_hash=?',(digest,)).fetchone()
            count=0;window=now
            if rate:
                elapsed=now-rate['last_posted_at']
                if elapsed<MIN_INTERVAL:
                    raise HTTPException(429,'评论发送较快，请稍后再试。',headers={'Retry-After':str(max(1,math.ceil(MIN_INTERVAL-elapsed)))})
                if now-rate['window_start']<3600:
                    window=rate['window_start'];count=rate['post_count']
                    if count>=HOURLY_LIMIT:
                        raise HTTPException(429,'本小时评论次数已达上限。',headers={'Retry-After':str(max(1,math.ceil(3600-(now-window))))})
            cid=uuid.uuid4().hex
            created=datetime.fromtimestamp(now,timezone.utc).isoformat(timespec='seconds')
            conn.execute('INSERT INTO site_comments(public_id,paper_id,content,visitor_hash,masked_ip,created_at) VALUES (?,?,?,?,?,?)',
                         (cid,paper_id,content,digest,masked_ip(address),created))
            conn.execute('INSERT INTO comment_limits(visitor_hash,last_posted_at,window_start,post_count) VALUES (?,?,?,?) '
                         'ON CONFLICT(visitor_hash) DO UPDATE SET last_posted_at=excluded.last_posted_at,window_start=excluded.window_start,post_count=excluded.post_count',
                         (digest,now,window,count+1))
            row=conn.execute('SELECT * FROM site_comments WHERE public_id=?',(cid,)).fetchone()
        return self.public(row)

    def list(self,paper_id: str,limit: int,before: int | None) -> dict:
        with self.db.connect() as conn:
            if not conn.execute('SELECT 1 FROM papers WHERE id=?',(paper_id,)).fetchone():
                raise HTTPException(404,'论文不存在。')
            if before is None:
                rows=conn.execute('SELECT * FROM site_comments WHERE paper_id=? ORDER BY seq DESC LIMIT ?',
                                  (paper_id,limit+1)).fetchall()
            else:
                rows=conn.execute('SELECT * FROM site_comments WHERE paper_id=? AND seq<? ORDER BY seq DESC LIMIT ?',
                                  (paper_id,before,limit+1)).fetchall()
        shown=rows[:limit];more=len(rows)>limit
        return {'comments':[self.public(row) for row in shown],'has_more':more,
                'next_before':shown[-1]['seq'] if more else None,'enabled':comments_enabled()}

    def delete(self,comment_id: str) -> None:
        with self.db.connect() as conn:
            result=conn.execute('DELETE FROM site_comments WHERE public_id=?',(comment_id,))
            if not result.rowcount:raise HTTPException(404,'评论不存在。')
