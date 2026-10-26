"""Local-user browser grants and transactional, replayable collection receipts."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager

from .discovery import Discovery
from .store import Store

PROTOCOL_VERSION = 1
OPERATIONS = frozenset({'get_status', 'list_allowed_projects', 'save_references', 'get_import_receipt', 'open_workbench', 'revoke_connection'})
MAX_MESSAGE_BYTES = 256 * 1024


class BridgeError(ValueError):
    def __init__(self, message, code='invalid_request'):
        super().__init__(message)
        self.code = code


def checked_origins(origins):
    origins = list(dict.fromkeys(origins))
    if not origins or any(not re.fullmatch(r'chrome-extension://[a-p]{32}/', origin) for origin in origins):
        raise ValueError('本机桥必须配置精确的扩展 ID；禁止通配符')
    return origins


class _TransactionStore(Store):
    def __init__(self, source, db):
        self.path, self.db = source.path, db

    @contextmanager
    def connection(self, write=False):
        # Discovery and the receipt commit on the same SQLite transaction.
        yield self.db


class BrowserGrants:
    def __init__(self, store, allowed_origins, *, clock=time.time, user_id='local-user', license_runtime=None, open_workbench=None):
        self.store, self.allowed_origins, self.clock = store, checked_origins(allowed_origins), clock
        self.user_id, self.license_runtime, self.open_workbench = user_id, license_runtime, open_workbench
        with store.connection(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS browser_connections (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS browser_receipts (connection_id TEXT NOT NULL, project_id TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(connection_id,project_id,request_id))')

    def _put(self, db, record):
        db.execute('INSERT OR REPLACE INTO browser_connections VALUES (?,?,?)', (record['connection_id'], self.user_id, json.dumps(record)))

    def connections(self):
        with self.store.connection() as db:
            records = [json.loads(row['data']) for row in db.execute('SELECT data FROM browser_connections WHERE user_id=?', (self.user_id,))]
            for record in records:
                if record['state'] == 'connected' and record['expires_at'] <= self.clock(): record['state'] = 'expired'
                elif record['state'] == 'pending_confirmation' and record['requested_at'] + 600 <= self.clock(): record['state'] = 'confirmation_expired'
            return records

    def receipts(self, project_id):
        self.store.project(project_id)
        with self.store.connection() as db:
            # Connections and receipts are local-user scoped, never exported.
            rows = db.execute('SELECT r.data FROM browser_receipts r JOIN browser_connections c ON c.id=r.connection_id WHERE r.project_id=? AND c.user_id=? ORDER BY r.rowid DESC LIMIT 20', (project_id,self.user_id))
            return [json.loads(row['data']) for row in rows]

    def _record(self, db, connection_id):
        row = db.execute('SELECT data FROM browser_connections WHERE id=? AND user_id=?', (connection_id, self.user_id)).fetchone()
        if not row:
            raise BridgeError('浏览器连接不存在，请重新连接', 'connection_missing')
        return json.loads(row['data'])

    def approve(self, connection_id, project_ids, *, lifetime_seconds=180 * 86400):
        if not isinstance(project_ids, list) or not project_ids or len(project_ids) > 100:
            raise BridgeError('请明确选择允许保存到的项目')
        with self.store.connection(write=True) as db:
            record = self._record(db, connection_id)
            if record['state'] != 'pending_confirmation' or self.clock() > record['requested_at'] + 600:
                raise BridgeError('连接确认已失效，请在扩展中重新连接', 'confirmation_expired')
            for project_id in project_ids:
                _TransactionStore(self.store, db).project(project_id)
            record.update(state='connected', project_ids=list(dict.fromkeys(project_ids)), authorized_at=self.clock(), expires_at=self.clock()+min(max(lifetime_seconds, 60), 180*86400))
            self._put(db, record)
            return record

    def revoke(self, connection_id):
        with self.store.connection(write=True) as db:
            record = self._record(db, connection_id)
            record.update(state='revoked', revoked_at=self.clock())
            self._put(db, record)
        return {'state':'revoked', 'connection_id':connection_id}

    def dispatch(self, origin, request):
        if origin not in self.allowed_origins:
            raise BridgeError('此扩展尚未获准连接，请检查安装来源', 'origin_denied')
        if not isinstance(request, dict) or len(json.dumps(request).encode()) > MAX_MESSAGE_BYTES:
            raise BridgeError('收藏请求超出大小限制')
        operation = request.get('operation')
        if operation not in OPERATIONS:
            raise BridgeError('本机收藏桥不支持此操作', 'operation_denied')
        if request.get('protocol_version') != PROTOCOL_VERSION:
            raise BridgeError('收藏工具版本不兼容，请更新桌面和浏览器扩展', 'update_required')
        installation = request.get('installation_id', '')
        if not isinstance(installation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', installation):
            raise BridgeError('浏览器安装标识无效，请重新连接')
        with self.store.connection(write=True) as db:
            connection_id = request.get('connection_id')
            if not connection_id and operation == 'get_status':
                candidates = [json.loads(row['data']) for row in db.execute('SELECT data FROM browser_connections WHERE user_id=?', (self.user_id,))]
                record = next((row for row in reversed(candidates) if row['installation_id'] == installation and row['origin'] == origin), None)
                if record is None or request.get('request_connection') is True and (record['state'] != 'connected' or record.get('expires_at',0) <= self.clock()):
                    record = {'connection_id':str(uuid.uuid4()), 'user_id':self.user_id, 'installation_id':installation, 'origin':origin,
                              'protocol_version':PROTOCOL_VERSION, 'state':'pending_confirmation', 'project_ids':[], 'requested_at':self.clock()}
                    self._put(db, record)
            else:
                record = self._record(db, connection_id)
            if record['installation_id'] != installation or record['origin'] != origin or record['protocol_version'] != PROTOCOL_VERSION:
                raise BridgeError('连接与当前浏览器安装不一致', 'installation_mismatch')
            state = record['state']
            if state == 'connected' and record['expires_at'] <= self.clock():
                state = 'expired'
            if state == 'pending_confirmation' and record['requested_at'] + 600 <= self.clock():
                state = 'confirmation_expired'
            if operation == 'get_status':
                return {'state':state, 'connection_id':record['connection_id'], 'protocol_version':PROTOCOL_VERSION,
                        'expires_at':record.get('expires_at'), 'project_ids':record['project_ids']}
            if operation == 'open_workbench':
                project_id = request.get('project_id')
                if project_id and project_id not in record['project_ids']:
                    raise BridgeError('此项目不在浏览器授权范围', 'project_unavailable')
                if self.open_workbench and self.open_workbench(project_id) is not False:
                    return {'state':'opened','project_id':project_id}
                return {'state':'open_desktop','project_id':project_id,'message':'请切回桌面创作台查看此项目'}
            if state != 'connected':
                raise BridgeError({'revoked':'连接已撤销，请重新连接','expired':'连接已到期，请重新连接'}.get(state,'请先在桌面应用确认此浏览器'), state)
            if operation == 'revoke_connection':
                record.update(state='revoked', revoked_at=self.clock()); self._put(db, record)
                return {'state':'revoked', 'connection_id':record['connection_id']}
            scoped = _TransactionStore(self.store, db)
            projects = {p['id']:p for p in scoped.projects() if p['id'] in record['project_ids']}
            if operation == 'list_allowed_projects':
                return {'projects':[{'id':p['id'],'name':p['name']} for p in projects.values()]}
            project_id = request.get('project_id')
            if project_id not in projects:
                raise BridgeError('项目已删除或不在当前浏览器授权范围，请在桌面重新确认', 'project_unavailable')
            request_id = request.get('request_id', '')
            if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', request_id):
                raise BridgeError('收藏请求标识无效')
            row = db.execute('SELECT digest,data FROM browser_receipts WHERE connection_id=? AND project_id=? AND request_id=?', (record['connection_id'],project_id,request_id)).fetchone()
            if operation == 'get_import_receipt':
                if not row: raise BridgeError('还未找到本次保存回执，可用同一请求重试', 'receipt_missing')
                return json.loads(row['data'])
            captures = request.get('captures')
            if not isinstance(captures,list) or not 1 <= len(captures) <= 50:
                raise BridgeError('每次请选择 1–50 条参考')
            for capture in captures:
                if not isinstance(capture,dict) or not isinstance(capture.get('url'),str) or len(capture['url']) > 4096:
                    raise BridgeError('参考链接格式或长度无效')
                links = capture.get('links',[])
                if not isinstance(links,list) or any(not isinstance(link,str) or len(link)>4096 for link in links):
                    raise BridgeError('参考链接格式或长度无效')
            digest = hashlib.sha256(json.dumps(captures,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if row:
                if row['digest'] != digest: raise BridgeError('同一幂等请求不能保存不同内容', 'idempotency_conflict')
                return json.loads(row['data'])
            if self.license_runtime:
                try: self.license_runtime.require_work('assets.import')
                except ValueError as error: raise BridgeError(str(error),'license_required') from None
            discovery = Discovery(scoped)
            before = {r['id'] for r in discovery.references(project_id)}
            references = discovery.collect(project_id,captures)
            created = len({r['id'] for r in references} - before)
            result = {'state':'saved', 'project_id':project_id, 'project_name':projects[project_id]['name'], 'request_id':request_id,
                      'references':references, 'receipt':{'created':created,'updated':len(references)-created,'skipped':0,'failed':0,
                      'needs_video':sum(r['entity_type']=='video' and r['media_state']=='missing' for r in references)}}
            db.execute('INSERT INTO browser_receipts VALUES (?,?,?,?,?)',(record['connection_id'],project_id,request_id,digest,json.dumps(result,ensure_ascii=False)))
            return result
