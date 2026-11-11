"""私有事务账本：审批绑定内容，API 请求先预留再结算。金额单位为微美元。"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from contextlib import contextmanager


class GateError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db = self.root / 'runtime.sqlite3'
        self.key = self.root / 'approval.key'
        try:
            fd = os.open(self.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if self.key.is_symlink():
                raise GateError('审批密钥不能是符号链接')
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(secrets.token_bytes(32))
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL);
            INSERT OR IGNORE INTO budget VALUES(1,1000000);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, run TEXT NOT NULL,
                reserved INTEGER NOT NULL, charged INTEGER, actual_cost REAL, usage TEXT,
                state TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(digest TEXT PRIMARY KEY, expiry REAL NOT NULL,
                signature TEXT NOT NULL, scope TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, issue INTEGER, task_digest TEXT,
                state TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, owner TEXT, expiry REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run TEXT, kind TEXT,
                data TEXT NOT NULL, created REAL NOT NULL);
            ''')
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:
                yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            yield c

    def balance(self):
        with self.connect() as c:
            cap = c.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0]
            used = c.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM requests').fetchone()[0]
            active = c.execute("SELECT count(*) FROM requests WHERE state='reserved'").fetchone()[0]
        return dict(cap_microusd=cap, accounted_microusd=used, available_microusd=cap-used,
                    active_requests=active, actual_cost=None)

    def set_cap(self, amount, policy_limit):
        if type(amount) is not int or not 0 < amount <= policy_limit:
            raise GateError('预算超出已冻结的项目授权；不得绕过本轮上限')
        with self.transaction() as c:
            if c.execute("SELECT count(*) FROM requests WHERE state='reserved'").fetchone()[0] or c.execute('SELECT count(*) FROM locks WHERE expiry>?',(time.time(),)).fetchone()[0]:
                raise GateError('运行中禁止更改预算')
            used=c.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM requests').fetchone()[0]
            if amount < used: raise GateError('新上限小于已有保守记账，不能抹掉费用')
            c.execute('UPDATE budget SET cap=? WHERE id=1',(amount,))
        self.event('operator','budget_cap',{'cap_microusd':amount,'previous_usage_preserved':True})

    def reserve(self, run, amount):
        if type(amount) is not int or amount <= 0:
            raise GateError('无效预算预留')
        request_id = secrets.token_hex(16)
        with self.transaction() as c:
            if c.execute("SELECT count(*) FROM requests WHERE state='reserved'").fetchone()[0]:
                raise GateError('PAID_CONCURRENCY_LIMIT')
            cap = c.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0]
            used = c.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM requests').fetchone()[0]
            if used + amount > cap:
                raise GateError('BUDGET_EXHAUSTED')
            c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',
                      (request_id,run,amount,None,None,None,'reserved',time.time()))
        return request_id

    def settle(self, request_id, charge=None, usage=None):
        # 没有可核查 usage 时不释放预留；保守记账不伪装 Provider 实际账单。
        with self.transaction() as c:
            row = c.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if not row or row['state'] != 'reserved':
                raise GateError('请求不存在或已经结算')
            if charge is not None and (type(charge) is not int or not 0 <= charge <= row['reserved']):
                raise GateError('Provider 用量超出预留合同；停止新请求')
            c.execute('UPDATE requests SET charged=?,usage=?,state=? WHERE id=?',
                      (row['reserved'] if charge is None else charge,
                       json.dumps(usage) if usage else None,
                       'unknown' if charge is None else 'accounted',request_id))

    def approve(self, subject, scope, ttl=3600):
        if not 0 < ttl <= 86400:
            raise GateError('批准有效期必须不超过一天')
        identity = digest(subject)
        expiry = time.time()+ttl
        payload = canonical([identity,expiry,scope])
        signature = hmac.new(self.key.read_bytes(),payload,'sha256').hexdigest()
        with self.transaction() as c:
            c.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?,?)',
                      (identity,expiry,signature,scope))
        return identity

    def authorized(self, subject, scope):
        identity = digest(subject)
        with self.connect() as c:
            row = c.execute('SELECT * FROM approvals WHERE digest=?',(identity,)).fetchone()
        if not row or row['expiry'] < time.time() or row['scope'] != scope:
            return False
        expected = hmac.new(self.key.read_bytes(),canonical([identity,row['expiry'],scope]),'sha256').hexdigest()
        return hmac.compare_digest(expected,row['signature'])

    @contextmanager
    def lock(self, name, owner, ttl=1800):
        with self.transaction() as c:
            row = c.execute('SELECT * FROM locks WHERE name=?',(name,)).fetchone()
            if row and row['expiry'] > time.time():
                raise GateError('CONCURRENCY_LOCKED')
            c.execute('INSERT OR REPLACE INTO locks VALUES(?,?,?)',(name,owner,time.time()+ttl))
        try:
            yield
        finally:
            with self.transaction() as c:
                c.execute('DELETE FROM locks WHERE name=? AND owner=?',(name,owner))

    def event(self, run, kind, data):
        with self.connect() as c:
            c.execute('INSERT INTO events(run,kind,data,created) VALUES(?,?,?,?)',
                      (run,kind,json.dumps(data,ensure_ascii=False),time.time()))

    def save_run(self, run, issue, subject, state, payload):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?)',
                      (run,issue,digest(subject),state,json.dumps(payload,ensure_ascii=False),time.time()))
        self.event(run,'state',{'state':state})

    def runs(self):
        with self.connect() as c:
            return [dict(x) for x in c.execute('SELECT * FROM runs ORDER BY updated DESC')]
