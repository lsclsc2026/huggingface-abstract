"""Private persistent result/source cache and per-request token accounting."""
from __future__ import annotations
import asyncio,json,time,weakref
from .db import Database

_LOCKS=weakref.WeakValueDictionary()


class ModelStore:
    def __init__(self,db: Database):
        self.db=db
        with db.connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS artifact_cache (
              kind TEXT NOT NULL, cache_key TEXT NOT NULL, value TEXT NOT NULL,
              expires_at REAL, PRIMARY KEY(kind,cache_key));
              CREATE TABLE IF NOT EXISTS model_calls (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
              model TEXT NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL,
              elapsed_ms INTEGER NOT NULL, prompt_tokens INTEGER, completion_tokens INTEGER,
              total_tokens INTEGER, cached_tokens INTEGER);''')

    def lock(self,key):
        identity=(str(self.db.path.resolve()),key)
        lock=_LOCKS.get(identity)
        if lock is None:lock=asyncio.Lock();_LOCKS[identity]=lock
        return lock

    def get(self,kind,key):
        with self.db.connect() as con:
            row=con.execute('SELECT value FROM artifact_cache WHERE kind=? AND cache_key=? AND (expires_at IS NULL OR expires_at>?)',(kind,key,time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self,kind,key,value,ttl=None):
        with self.db.connect() as con:
            con.execute('INSERT INTO artifact_cache VALUES (?,?,?,?) ON CONFLICT(kind,cache_key) DO UPDATE SET value=excluded.value,expires_at=excluded.expires_at',(kind,key,json.dumps(value,ensure_ascii=False),time.time()+ttl if ttl else None))

    def record(self,model,purpose,status,elapsed,usage=None):
        usage=usage if isinstance(usage,dict) else {}
        def count(key):
            value=usage.get(key)
            return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
        details=usage.get('prompt_tokens_details') or {}
        cached=details.get('cached_tokens') if isinstance(details,dict) else None
        with self.db.connect() as con:
            con.execute('INSERT INTO model_calls(created_at,model,purpose,status,elapsed_ms,prompt_tokens,completion_tokens,total_tokens,cached_tokens) VALUES (?,?,?,?,?,?,?,?,?)',(time.time(),model,purpose,str(status),int(elapsed*1000),count('prompt_tokens'),count('completion_tokens'),count('total_tokens'),cached if isinstance(cached,int) else None))

    def usage(self):
        with self.db.connect() as con:
            row=con.execute('SELECT COUNT(*),SUM(prompt_tokens),SUM(completion_tokens),SUM(total_tokens),SUM(CASE WHEN prompt_tokens IS NULL OR completion_tokens IS NULL THEN 1 ELSE 0 END) FROM model_calls').fetchone()
        return dict(zip(('requests','prompt_tokens','completion_tokens','total_tokens','unknown_usage_requests'),[row[0],row[1],row[2],row[3],row[4] or 0]))
