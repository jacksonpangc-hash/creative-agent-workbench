from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import CostQuote, decimal

UNSET = object()


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class BudgetError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def dumps(data):
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)


SECRET_KEYS = {"api_key", "authorization", "password", "access_token", "refresh_token", "client_secret", "secret", "cookie", "cookies"}


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_KEYS:
                raise ValueError("凭证只能存入系统密钥库")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def provenance_url(url):
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if ':' in host: host = '[' + host + ']'
    if parsed.port: host += ':' + str(parsed.port)
    return urlunsplit((parsed.scheme, host, parsed.path, '', ''))


def export_safe(value, reset_credentials=True):
    if isinstance(value, dict):
        return {k: False if k == "has_key" and reset_credentials else export_safe(v, reset_credentials)
                for k, v in value.items() if k.lower() not in SECRET_KEYS | {"secret_ref", "path", "local_path"} and not k.endswith("_path")}
    if isinstance(value, list):
        return [export_safe(v, reset_credentials) for v in value]
    if reset_credentials and isinstance(value, str) and value.startswith(('http://', 'https://')):
        try: return provenance_url(value)
        except ValueError: return '[无效来源地址]'
    return value


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, budget TEXT,
                currency TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_scope ON records(project_id,kind);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL,
                payload TEXT NOT NULL, quote TEXT NOT NULL, actual_cost TEXT, state TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, provider_request_id TEXT, result TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(project_id,idempotency_key));
            CREATE INDEX IF NOT EXISTS jobs_scope ON jobs(project_id,state);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                source_kind TEXT NOT NULL, source_record_id TEXT NOT NULL,
                operation TEXT NOT NULL, created_at TEXT NOT NULL, processed_at TEXT);
            CREATE TABLE IF NOT EXISTS index_job(
                id INTEGER PRIMARY KEY AUTOINCREMENT, outbox_id INTEGER NOT NULL UNIQUE,
                project_id TEXT NOT NULL, source_kind TEXT NOT NULL, source_record_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                chunks INTEGER NOT NULL DEFAULT 0, error TEXT, started_at TEXT, finished_at TEXT,
                duration_ms INTEGER, FOREIGN KEY(outbox_id) REFERENCES knowledge_outbox(id));
            CREATE INDEX IF NOT EXISTS index_job_state ON index_job(state,id);
            CREATE TABLE IF NOT EXISTS knowledge_chunk(
                chunk_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, pool TEXT NOT NULL,
                source_kind TEXT NOT NULL, source_record_id TEXT NOT NULL,
                source_version TEXT NOT NULL, content_hash TEXT NOT NULL,
                embedding_space_id TEXT, index_state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge_chunk(project_id,pool,source_record_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                chunk_id UNINDEXED, project_id UNINDEXED, pool UNINDEXED, tokens,
                tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS retrieval_snapshot(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
            """)

        # Capture every main-record mutation, including service code that writes
        # records directly. Outbox rows and business writes commit atomically.
        tracked = "'asset','scene','parse','observation','product_facts','cmm_reference','public_reference','script','preference','instruction','note','media_input_manifest','cmm_connection','cmm_job','industry_connection'"
        with self.connection() as db:
            for event, row, operation in [('INSERT', 'NEW', 'upsert'), ('UPDATE', 'NEW', 'upsert'), ('DELETE', 'OLD', 'delete')]:
                db.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS knowledge_records_{event.lower()}
                AFTER {event} ON records WHEN {row}.kind IN ({tracked})
                BEGIN
                    INSERT INTO knowledge_outbox(project_id,source_kind,source_record_id,operation,created_at)
                    VALUES({row}.project_id,{row}.kind,{row}.id,'{operation}',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                    INSERT INTO index_job(outbox_id,project_id,source_kind,source_record_id)
                    VALUES(last_insert_rowid(),{row}.project_id,{row}.kind,{row}.id);
                END;
                """)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_project(self, name, budget=None, currency="CNY"):
        if not name.strip() or len(name) > 100 or currency not in {"CNY", "USD"}:
            raise ValueError("项目名称或币种无效")
        if budget is not None and decimal(budget) < 0:
            raise ValueError("预算不能为负")
        data = dict(id=uid(), name=name.strip(), budget=str(decimal(budget)) if budget is not None else None,
                    currency=currency, created_at=now())
        with self.connection(True) as db:
            db.execute("INSERT INTO projects VALUES(:id,:name,:budget,:currency,:created_at)", data)
        return data

    def projects(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY created_at")]

    def _project(self, db, project_id):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise NotFound("项目不存在")
        return dict(row)

    def project(self, project_id):
        with self.connection() as db:
            return self._project(db, project_id)

    def update_project(self, project_id, *, name=None, budget=UNSET, currency=None):
        with self.connection(True) as db:
            p = self._project(db, project_id)
            if name is not None:
                if not name.strip() or len(name) > 100:
                    raise ValueError("项目名称无效")
                p["name"] = name.strip()
            if budget is not UNSET:
                if budget is not None and decimal(budget) < 0:
                    raise ValueError("预算不能为负")
                p["budget"] = str(decimal(budget)) if budget is not None else None
            if currency and currency != p["currency"]:
                if currency not in {"CNY", "USD"}:
                    raise ValueError("币种无效")
                if db.execute("SELECT 1 FROM jobs WHERE project_id=? LIMIT 1", (project_id,)).fetchone():
                    raise Conflict("已有任务的项目不能变更记账币种，请新建项目")
                p["currency"] = currency
            db.execute("UPDATE projects SET name=?,budget=?,currency=? WHERE id=?",
                       (p["name"], p["budget"], p["currency"], project_id))
        return p

    def put(self, project_id, kind, data, record_id=None):
        reject_secrets(data)
        if data.get("project_id", project_id) != project_id:
            raise ValueError("记录项目与请求范围不一致")
        with self.connection(True) as db:
            self._project(db, project_id)
            timestamp = now()
            if record_id:
                old = db.execute("SELECT * FROM records WHERE id=? AND project_id=? AND kind=?",
                                 (record_id, project_id, kind)).fetchone()
                if not old:
                    raise NotFound("记录不存在")
                record = {**data, "id": record_id, "project_id": project_id,
                          "created_at": old["created_at"], "updated_at": timestamp}
                db.execute("UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?",
                           (dumps(record), timestamp, record_id, project_id))
            else:
                record = {**data, "id": uid(), "project_id": project_id,
                          "created_at": timestamp, "updated_at": timestamp}
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)",
                           (record["id"], project_id, kind, dumps(record), timestamp, timestamp))
        return record

    def put_if_absent(self, project_id, kind, data, record_id):
        """Create one deterministic record; repeated saves preserve user edits."""
        reject_secrets(data)
        if data.get('project_id', project_id) != project_id:
            raise ValueError('记录项目与请求范围不一致')
        if not isinstance(record_id, str) or not record_id or len(record_id) > 180:
            raise ValueError('记录标识无效')
        with self.connection(True) as db:
            self._project(db, project_id)
            old = db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
            if old:
                if old['project_id'] != project_id or old['kind'] != kind:
                    raise Conflict('记录标识已用于其他范围')
                return json.loads(old['data'])
            timestamp = now()
            record = {**data, 'id': record_id, 'project_id': project_id,
                      'created_at': timestamp, 'updated_at': timestamp}
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (record_id, project_id, kind, dumps(record), timestamp, timestamp))
        return record

    def restore_record(self, project_id, kind, data, record_id):
        """Insert a validated historical record without replacing any existing identity."""
        reject_secrets(data)
        if not isinstance(record_id, str) or not record_id or len(record_id) > 180:
            raise ValueError('恢复记录标识无效')
        if data.get('project_id', project_id) != project_id:
            raise ValueError('恢复记录项目与请求范围不一致')
        with self.connection(True) as db:
            self._project(db, project_id)
            if db.execute('SELECT 1 FROM records WHERE id=?', (record_id,)).fetchone():
                raise Conflict('恢复记录已存在，禁止覆盖')
            timestamp = now()
            record = {**data, 'id': record_id, 'project_id': project_id,
                      'created_at': data.get('created_at', timestamp),
                      'updated_at': data.get('updated_at', timestamp)}
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (record_id, project_id, kind, dumps(record), record['created_at'], record['updated_at']))
        return record

    def get(self, project_id, kind, record_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM records WHERE project_id=? AND kind=? AND id=?",
                             (project_id, kind, record_id)).fetchone()
            if not row:
                raise NotFound("记录不存在")
            return json.loads(row["data"])

    def list(self, project_id, kind):
        with self.connection() as db:
            self._project(db, project_id)
            return [json.loads(r["data"]) for r in db.execute(
                "SELECT data FROM records WHERE project_id=? AND kind=? ORDER BY created_at DESC", (project_id, kind))]

    def delete(self, project_id, kind, record_id):
        self.get(project_id, kind, record_id)
        with self.connection(True) as db:
            db.execute("DELETE FROM records WHERE project_id=? AND kind=? AND id=?", (project_id, kind, record_id))

    def append_turn(self, project_id, session_id, turn):
        reject_secrets(turn)
        with self.connection(True) as db:
            row = db.execute("SELECT data FROM records WHERE id=? AND project_id=? AND kind='session'", (session_id,project_id)).fetchone()
            if not row: raise NotFound('分析会话不存在')
            data = json.loads(row['data'])
            data['turns'].append(turn)
            data['updated_at'] = now()
            db.execute('UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?', (dumps(data), data['updated_at'],session_id,project_id))
        return data

    def checkpoint_turn(self, project_id, session_id, turn_index, turn):
        """Replace one worker-owned turn while preserving concurrent appended turns."""
        reject_secrets(turn)
        with self.connection(True) as db:
            row=db.execute("SELECT data FROM records WHERE id=? AND project_id=? AND kind='session'",(session_id,project_id)).fetchone()
            if not row:raise NotFound('分析会话不存在')
            data=json.loads(row['data'])
            if not 0<=turn_index<len(data['turns']):raise ValueError('分析轮次不存在')
            data['turns'][turn_index]=turn;data['updated_at']=now()
            db.execute('UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?',(dumps(data),data['updated_at'],session_id,project_id))
        return data

    def checkpoint_result(self, project_id, job_id, result):
        reject_secrets(result)
        with self.connection(True) as db:
            row=db.execute('SELECT state FROM jobs WHERE id=? AND project_id=?',(job_id,project_id)).fetchone()
            if not row:raise NotFound('任务不存在')
            if row['state'] not in {'running','submitting','cancelling'}:raise Conflict('任务已停止，不再更新阶段结果')
            db.execute('UPDATE jobs SET result=?,updated_at=? WHERE id=? AND project_id=?',(dumps(result),now(),job_id,project_id))
        result_job=self.job(project_id,job_id)
        if result_job['state']=='cancelling':raise Conflict('取消后已保留在途成果，不再继续下一批')
        return result_job

    def export_project(self, project_id):
        with self.connection() as db:
            project = self._project(db, project_id)
            records = {}
            for row in db.execute("SELECT kind,data FROM records WHERE project_id=?", (project_id,)):
                records.setdefault(row["kind"], []).append(export_safe(json.loads(row["data"])))
            # Retrieval vectors are derived; the exact creative inputs are durable
            # audit data referenced by scripts and must travel with a backup.
            archived_ids = {r['id'] for r in records.get('creative_context_snapshot', [])}
            for row in db.execute('SELECT id,data FROM retrieval_snapshot WHERE project_id=?', (project_id,)):
                if row['id'] not in archived_ids:
                    snapshot = export_safe(json.loads(row['data']))
                    snapshot.update(audit_only=True, replay_allowed=False)
                    records.setdefault('creative_context_snapshot', []).append(snapshot)
        return dict(format="creative-agent-project", version=1, exported_at=now(), project=project, records=records)

    def _budget(self, db, project_id):
        project = self._project(db, project_id)
        reserved = decimal(0)
        spent = decimal(0)
        unknown_jobs = 0
        by_currency = {}
        for row in db.execute("SELECT quote,actual_cost,state FROM jobs WHERE project_id=?", (project_id,)):
            quote = json.loads(row["quote"])
            currency = quote.get('currency', project['currency'])
            totals = by_currency.setdefault(currency, {'spent': decimal(0), 'reserved': decimal(0), 'unknown_jobs': 0})
            if row["actual_cost"] is not None:
                totals['spent'] += decimal(row["actual_cost"])
                if currency == project['currency']: spent += decimal(row["actual_cost"])
            elif quote.get('amount') is None:
                unknown_jobs += 1
                totals['unknown_jobs'] += 1
            else:
                totals['reserved'] += decimal(quote["amount"])
                if currency == project['currency']: reserved += decimal(quote["amount"])
        limit = decimal(project["budget"]) if project["budget"] is not None else None
        return dict(limit=str(limit) if limit is not None else None, currency=project["currency"],
                    spent=str(spent), reserved=str(reserved),
                    available=str(limit - spent - reserved) if limit is not None else None,
                    unknown_jobs=unknown_jobs,
                    by_currency={c:{k:str(v) if k != 'unknown_jobs' else v for k,v in totals.items()} for c,totals in by_currency.items()})

    def budget(self, project_id):
        with self.connection() as db:
            return self._budget(db, project_id)

    @staticmethod
    def _decode_job(row):
        data = dict(row)
        for key in ("payload", "quote", "result"):
            if data[key] is not None:
                data[key] = json.loads(data[key])
        return data

    def create_job(self, project_id, kind, payload, quote: CostQuote, idempotency_key):
        with self.connection(True) as db:
            return self._create_job(db, project_id, kind, payload, quote, idempotency_key)

    def create_jobs(self, project_id, kind, items):
        with self.connection(True) as db:
            return [self._create_job(db, project_id, kind, payload, quote, key) for payload, quote, key in items]

    def _create_job(self, db, project_id, kind, payload, quote, idempotency_key):
        reject_secrets(payload)
        if not idempotency_key or len(idempotency_key) > 180:
            raise ValueError("需要有效的幂等键")
        old = db.execute("SELECT * FROM jobs WHERE project_id=? AND idempotency_key=?",
                         (project_id, idempotency_key)).fetchone()
        if old:
            if old["payload"] != dumps(payload) or old["kind"] != kind or old["quote"] != dumps(quote.model_dump(mode="json")):
                raise Conflict("幂等键已用于其他请求")
            return self._decode_job(old)
        budget = self._budget(db, project_id)
        if budget['limit'] is not None and quote.currency != budget["currency"]:
            raise BudgetError("报价和项目币种不一致")
        if budget['limit'] is not None and quote.amount != 0 and (decimal(budget['available']) <= 0 or
                (quote.amount is not None and quote.amount > decimal(budget['available']))):
            raise BudgetError("请先设置足够的项目预算，再确认计费任务")
        timestamp = now()
        data = dict(id=uid(), project_id=project_id, kind=kind, payload=dumps(payload),
                    quote=dumps(quote.model_dump(mode="json")), actual_cost="0" if quote.amount == 0 else None,
                    state="queued", idempotency_key=idempotency_key, provider_request_id=None,
                    result=None, error=None, created_at=timestamp, updated_at=timestamp)
        db.execute("""INSERT INTO jobs VALUES(:id,:project_id,:kind,:payload,:quote,:actual_cost,:state,
            :idempotency_key,:provider_request_id,:result,:error,:created_at,:updated_at)""", data)
        return self._decode_job(data)

    def job(self, project_id, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE project_id=? AND id=?", (project_id, job_id)).fetchone()
            if not row:
                archived = self.get(project_id, 'migration_job', job_id)
                if archived.get('state') != 'archived' or archived.get('replay_allowed') is not False:
                    raise NotFound('归档任务无效')
                return archived
            return self._decode_job(row)

    def jobs(self, project_id):
        self.project(project_id)
        with self.connection() as db:
            return [self._decode_job(r) for r in db.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def pending_jobs(self):
        with self.connection() as db:
            return [self._decode_job(r) for r in db.execute("SELECT * FROM jobs WHERE state IN ('queued','submitted','cancelling') ORDER BY created_at")]

    def transition(self, project_id, job_id, state, *, provider_request_id=None, error=None):
        transitions = {
            "queued": {"running", "cancelled", "failed"},
            "running": {"submitting", "submitted", "succeeded", "failed", "cancelled", "needs_attention"},
            "submitting": {"submitted", "succeeded", "failed", "needs_attention", "cancelling"},
            "submitted": {"succeeded", "failed", "cancelling", "needs_attention"},
            "cancelling": {"cancelled", "succeeded", "failed", "needs_attention"},
            "needs_attention": {"submitted", "failed", "cancelled"},
        }
        with self.connection(True) as db:
            old = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if not old:
                raise NotFound("任务不存在")
            if state not in transitions.get(old["state"], set()):
                raise Conflict(f"不能从 {old['state']} 变为 {state}")
            request_id = provider_request_id or old["provider_request_id"]
            if state == "submitted" and not request_id:
                raise ValueError("提交后的任务必须保存供应商编号")
            db.execute("UPDATE jobs SET state=?,provider_request_id=?,error=?,updated_at=? WHERE id=? AND project_id=?",
                       (state, request_id, error, now(), job_id, project_id))
        self.event(project_id, job_id, "state", {"state": state})
        return self.job(project_id, job_id)

    def accept_submission(self, project_id, job_id, request_id):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 180:
            raise ValueError('供应商任务编号无效')
        with self.connection(True) as db:
            row = db.execute('SELECT state,provider_request_id FROM jobs WHERE id=? AND project_id=?', (job_id, project_id)).fetchone()
            if not row: raise NotFound('任务不存在')
            if row['state'] not in {'submitting', 'cancelling'}: raise Conflict('当前任务不能接受提交结果')
            if row['provider_request_id'] and row['provider_request_id'] != request_id: raise Conflict('供应商任务编号不一致')
            state = 'cancelling' if row['state'] == 'cancelling' else 'submitted'
            db.execute('UPDATE jobs SET state=?,provider_request_id=?,updated_at=? WHERE id=? AND project_id=?',
                       (state, request_id, now(), job_id, project_id))
            db.execute('INSERT INTO events(job_id,type,data,created_at) VALUES(?,?,?,?)', (job_id,'state',dumps({'state':state}),now()))
        return self.job(project_id, job_id)

    def finish(self, project_id, job_id, *, result=None, error=None, actual_cost=None):
        reject_secrets(result)
        value = decimal(actual_cost) if actual_cost is not None else None
        if value is not None and value < 0:
            raise ValueError("费用不能为负")
        state = "failed" if error else "succeeded"
        with self.connection(True) as db:
            row = db.execute("SELECT state FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if not row:
                raise NotFound("任务不存在")
            if row["state"] not in {"running", "submitting", "submitted", "cancelling"}:
                raise Conflict("当前任务不能完成")
            db.execute("UPDATE jobs SET state=?,result=?,error=?,actual_cost=COALESCE(?,actual_cost),updated_at=? WHERE id=? AND project_id=?",
                       (state, dumps(result), error, str(value) if value is not None else None, now(), job_id, project_id))
            db.execute("INSERT INTO events(job_id,type,data,created_at) VALUES(?,?,?,?)",
                       (job_id, "state", dumps({"state": state}), now()))
        return self.job(project_id, job_id)

    def settle(self, project_id, job_id, actual_cost):
        value = decimal(actual_cost)
        if value < 0:
            raise ValueError("费用不能为负")
        with self.connection(True) as db:
            row = db.execute('SELECT state FROM jobs WHERE id=? AND project_id=?', (job_id, project_id)).fetchone()
            if not row: raise NotFound('任务不存在')
            if row['state'] not in {'succeeded', 'failed', 'cancelled', 'needs_attention'}:
                raise Conflict('任务仍有计费风险，不能释放预留；请先查询供应商终态')
            db.execute("UPDATE jobs SET actual_cost=?,updated_at=? WHERE id=? AND project_id=?",
                       (str(value), now(), job_id, project_id))
        return self.job(project_id, job_id)

    def cancel(self, project_id, job_id):
        job = self.job(project_id, job_id)
        if job["state"] == "queued":
            self.transition(project_id, job_id, "cancelled")
            return self.settle(project_id, job_id, "0")
        if job["state"] in {"submitting", "submitted"}:
            return self.transition(project_id, job_id, "cancelling")
        raise Conflict("任务无法直接取消；已计费或执行中的任务需查询供应商状态")

    def recover(self):
        with self.connection(True) as db:
            for row in db.execute("SELECT * FROM jobs WHERE state IN ('running','submitting')").fetchall():
                quote = json.loads(row["quote"])
                state = "queued" if quote.get('amount') is not None and decimal(quote["amount"]) == 0 and row["state"] == "running" else "needs_attention"
                db.execute("UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?",
                           (state, "运行中断；计费请求未自动重发" if state == "needs_attention" else None, now(), row["id"]))

    def event(self, project_id, job_id, event_type, data):
        reject_secrets(data)
        self.job(project_id, job_id)
        with self.connection(True) as db:
            db.execute("INSERT INTO events(job_id,type,data,created_at) VALUES(?,?,?,?)",
                       (job_id, event_type, dumps(data), now()))

    def events(self, project_id, job_id, after=0):
        job = self.job(project_id, job_id)
        if job.get("archival") and job.get("state") == "archived":
            return [event for event in job.get("events", []) if event.get("id", 0) > after][:500]
        with self.connection() as db:
            return [{**dict(row), "data": json.loads(row["data"])} for row in db.execute(
                "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 500", (job_id, after))]

