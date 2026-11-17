import sqlite3,json,os,hashlib
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 封存台账：同一排产决定（run_id + 决策指纹）只入账一次，重跑不新增用纸和订单成本。
    db.execute('CREATE TABLE IF NOT EXISTS seals (run_id TEXT NOT NULL, decision_fingerprint TEXT NOT NULL, body TEXT NOT NULL, sealed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(run_id, decision_fingerprint))')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)

def decision_fingerprint(body):
    sub=body.get('substitution',{})
    picks=[]
    for d in sub.get('decisions',[]):
        sel=d.get('selected')
        if not sel:picks.append((d['order_id'],d.get('status'),d.get('reason')));continue
        if sel.get('segments'):
            detail=tuple((s['lot'],s['gross']) for s in sel['segments'])
        else:
            detail=(sel['lot'],sel['gross'])
        picks.append((d['order_id'],d.get('status'),d.get('reason'),detail))
    raw=json.dumps({'policy':sub.get('policy'),'picks':picks,
                    'projection':[(p['lot'],p['opening'],p['consumed'],p['closing']) for p in sub.get('projection',[])],
                    'business':sub.get('business',{})},ensure_ascii=False,sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]

def seal(run_id, version, body):
    # 条件化封存：复核版本须为当前版本；整批对账通过才入账，失败保留旧版（不写入）。
    sub=body.get('substitution',{})
    result=sub.get('reconcile')
    if not result or not result.get('balanced'):
        raise DomainError('UNBALANCED','整批对账未通过，已保留旧版，未封存')
    fingerprint=decision_fingerprint(body)
    with connection() as db:
        row=db.execute('SELECT version FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','批次不存在',404)
        if row['version']!=version:raise DomainError('STALE','复核版本已更新，请刷新',409)
        # INSERT OR IGNORE：同一排产决定重跑幂等，不新增用纸和订单成本。
        db.execute('INSERT OR IGNORE INTO seals(run_id,decision_fingerprint,body) VALUES(?,?,?)',
                   (run_id,fingerprint,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('sealed:'||?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (run_id,fingerprint))
    return get_seal(run_id)

def get_seal(run_id):
    with connection() as db:
        rows=db.execute('SELECT run_id,decision_fingerprint,sealed_at FROM seals WHERE run_id=? ORDER BY sealed_at',(run_id,)).fetchall()
    if not rows:return {'run_id':run_id,'sealed':False,'seals':[]}
    return {'run_id':run_id,'sealed':True,'seals':[dict(r) for r in rows]}

def sealed_lots():
    """汇总所有已封存决定对各纸批的累计占用，供后续批次核对可用量。"""
    totals={}
    with connection() as db:
        for row in db.execute('SELECT body FROM seals'):
            body=json.loads(row['body'])
            for p in body.get('substitution',{}).get('projection',[]):
                slot=totals.setdefault(p['lot'],{'lot':p['lot'],'opening':p['opening'],'consumed':0,'closing':p['opening']})
                slot['consumed']+=p['consumed']
                slot['closing']=slot['opening']-slot['consumed']
    return [totals[k] for k in sorted(totals)]
