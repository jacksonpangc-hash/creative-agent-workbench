"""Derived, scoped local knowledge: durable outbox, FTS5 and Qdrant Local.

SQLite remains authoritative. Every returned candidate is checked against current
records, content versions, file hashes, rights and transcript quality again.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from threading import RLock
import time
from typing import Literal
import uuid

from .footage import tokens
from .local_embeddings import EmbeddingUnavailable, LocalEmbeddings
from .store import NotFound, dumps, now
from .usage_rights import require_own_reuse

POOLS = ('reference', 'own', 'history')
SOURCE_KINDS = ('asset', 'public_reference', 'cmm_reference', 'script', 'preference', 'instruction')
OWN_KINDS = {'own', 'raw', 'finished', 'account', 'pushed'}
_DEFAULT = object()


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def reciprocal_rank_fusion(lexical, semantic, *, k=60):
    scores = Counter()
    for branch in (lexical, semantic):
        for rank, identifier in enumerate(dict.fromkeys(branch), 1):
            scores[identifier] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _active(value):
    if value.get('withdrawn') or value.get('deleted') or value.get('excluded') or value.get('revoked'):
        return False
    for key in ('expires_at', 'valid_until'):
        if not value.get(key):
            continue
        try:
            expiry = datetime.fromisoformat(str(value[key]).replace('Z', '+00:00'))
            if expiry.tzinfo is None:
                if len(str(value[key])) == 10:
                    expiry = expiry.replace(hour=23, minute=59, second=59, tzinfo=UTC)
                else:
                    return False
            if expiry <= datetime.now(UTC):
                return False
        except (ValueError, TypeError):
            return False
    return True


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    workspace_id: str
    project_id: str
    kind: str
    pool: Literal['reference', 'own', 'history']
    source_kind: str
    source_record_id: str
    source_version: str
    content_hash: str
    text: str
    asset_id: str | None = None
    asset_sha256: str | None = None
    platform_work_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    evidence_ids: list[str] = field(default_factory=list)
    product_id: str | None = None
    sku_id: str | None = None
    generic_footage: bool = False
    category_path: list[str] = field(default_factory=list)
    audience_tags: list[str] = field(default_factory=list)
    scene_tags: list[str] = field(default_factory=list)
    quality_state: str = 'usable'
    allowed_uses: list[str] = field(default_factory=lambda: ['retrieve', 'inspiration'])
    rights_snapshot_id: str | None = None
    source_observed_at: str | None = None
    expires_at: str | None = None
    index_state: str = 'keyword_only'
    embedding_space_id: str | None = None
    provenance: str = ''
    review_state: str = 'unreviewed'
    support: list[dict] = field(default_factory=list)
    input_manifest_id: str | None = None
    metrics: dict | list | None = None
    title: str = ''

    def __post_init__(self):
        if self.pool not in POOLS or not self.text.strip() or self.quality_state != 'usable':
            raise ValueError('知识片段缺少可用正文或有效资料池')
        if 'retrieve' not in self.allowed_uses:
            raise ValueError('知识片段没有当前检索用途许可')
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError('片段时间必须同时存在')
        if self.start_ms is not None and (type(self.start_ms) is not int or type(self.end_ms) is not int or not 0 <= self.start_ms < self.end_ms):
            raise ValueError('知识片段时间范围无效')


class _VectorStore:
    """One QdrantLocal client per process/path, never front-end supplied paths."""
    _clients = {}
    _lock = RLock()

    def __init__(self, path, space):
        self.path, self.space = Path(path).resolve(), space
        with self._lock:
            if self.path not in self._clients:
                from qdrant_client import QdrantClient
                self.path.mkdir(parents=True, exist_ok=True)
                self._clients[self.path] = QdrantClient(path=str(self.path))
            self.client = self._clients[self.path]
            self.collection = space.id.replace('-', '_')
            from qdrant_client import models
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(self.collection,
                    vectors_config=models.VectorParams(size=space.dimensions, distance=models.Distance.COSINE))

    @staticmethod
    def point_id(chunk_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'creative-knowledge:' + chunk_id))

    def upsert(self, chunks, vectors):
        from qdrant_client import models
        with self._lock:
            self.client.upsert(self.collection, [models.PointStruct(
                id=self.point_id(c['chunk_id']), vector=vector,
                payload={'chunk_id': c['chunk_id'], 'project_id': c['project_id'], 'pool': c['pool']})
                for c, vector in zip(chunks, vectors)], wait=True)

    def delete(self, ids):
        if not ids:
            return
        from qdrant_client import models
        with self._lock:
            self.client.delete(self.collection, models.PointIdsList(points=[self.point_id(i) for i in ids]), wait=True)

    def search(self, project_id, pool, vector, allowed_ids, *, limit=40):
        if not allowed_ids:
            return []
        from qdrant_client import models
        # Hard eligibility is applied inside the vector query before top-k.
        conditions = [models.FieldCondition(key='project_id', match=models.MatchValue(value=project_id)),
                      models.FieldCondition(key='pool', match=models.MatchValue(value=pool)),
                      models.HasIdCondition(has_id=[self.point_id(i) for i in allowed_ids])]
        with self._lock:
            result = self.client.query_points(self.collection, query=vector,
                query_filter=models.Filter(must=conditions), limit=limit, with_payload=True)
        return [point.payload['chunk_id'] for point in result.points]


class KnowledgeIndex:
    def __init__(self, store, media=None, *, embeddings=_DEFAULT, candidate_filter=None):
        self.store, self.media = store, media
        self.candidate_filter = candidate_filter
        self.workspace_id = 'workspace-' + digest(str(store.path.resolve()))[:24]
        self.embeddings = LocalEmbeddings(store.path.parent / 'models' / 'bge-small-zh-v1.5') if embeddings is _DEFAULT else embeddings
        self._vectors = None
        self._lock = RLock()
        self.semantic_error = None
        self._bootstrap()

    def _bootstrap(self):
        with self.store.connection(True) as db:
            if db.execute("SELECT 1 FROM knowledge_meta WHERE key='bootstrap-v1'").fetchone():
                return
            rows = db.execute('SELECT id,project_id,kind FROM records WHERE kind IN (' + ','.join('?' for _ in SOURCE_KINDS) + ')', SOURCE_KINDS).fetchall()
            for row in rows:
                self._enqueue(db, row['project_id'], row['kind'], row['id'])
            db.execute("INSERT INTO knowledge_meta VALUES('bootstrap-v1',?)", (now(),))

    @staticmethod
    def _enqueue(db, p, kind, identifier):
        cursor = db.execute('INSERT INTO knowledge_outbox(project_id,source_kind,source_record_id,operation,created_at) VALUES(?,?,?,?,?)', (p, kind, identifier, 'upsert', now()))
        db.execute('INSERT INTO index_job(outbox_id,project_id,source_kind,source_record_id) VALUES(?,?,?,?)', (cursor.lastrowid, p, kind, identifier))

    def enqueue(self, p, kind, identifier):
        self.store.project(p)
        if kind not in SOURCE_KINDS:
            raise ValueError('该资料类型不能直接加入索引')
        with self.store.connection(True) as db:
            self._enqueue(db, p, kind, identifier)

    def recover(self):
        """Call once at single-worker startup; interrupted jobs resume durably."""
        with self.store.connection(True) as db:
            db.execute("UPDATE index_job SET state='queued',error='应用中断后恢复' WHERE state='running'")

    def jobs(self, p):
        self.store.project(p)
        with self.store.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM index_job WHERE project_id=? ORDER BY id', (p,))]

    def cancel(self, p, job_id):
        with self.store.connection(True) as db:
            cursor = db.execute("UPDATE index_job SET state='cancelled',finished_at=? WHERE id=? AND project_id=? AND state IN ('queued','running','failed','keyword_only')", (now(), job_id, p))
            if not cursor.rowcount:
                raise ValueError('索引任务不存在或已经完成')

    def retry(self, p, job_id):
        with self.store.connection(True) as db:
            cursor = db.execute("UPDATE index_job SET state='queued',error=NULL WHERE id=? AND project_id=? AND state IN ('failed','keyword_only','cancelled')", (job_id, p))
            if not cursor.rowcount:
                raise ValueError('索引任务不允许重试')

    def _file_valid(self, p, asset):
        try:
            path = self.media.file(p, asset['id']) if self.media else Path(asset.get('path', ''))
            if not path.is_file() or not asset.get('sha256'):
                return False
            # Recheck bytes when file stat changes, including replacements at same path.
            stat = path.stat()
            key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            cache = getattr(self, '_file_hashes', {})
            if key not in cache:
                with path.open('rb') as stream:
                    cache[key] = hashlib.file_digest(stream, 'sha256').hexdigest()
                self._file_hashes = {key: cache[key], **dict(list(cache.items())[-255:])}
            return cache[key] == asset['sha256']
        except (OSError, ValueError, KeyError):
            return False

    def _allowed(self, p, kind, record):
        if not self.candidate_filter:
            return True
        try:
            return self.candidate_filter(p, kind, record) is not False
        except ValueError:
            return False

    def _state(self, p, kind, identifier):
        record = self.store.get(p, kind, identifier)
        if not self._allowed(p, kind, record):
            raise ValueError('资料不属于当前账号可访问范围')
        if not _active(record):
            raise ValueError('资料已撤销、排除或过期')
        state = {'record': record, 'kind': kind}
        if kind in {'public_reference', 'cmm_reference'}:
            if record.get('merged_into'):
                raise ValueError('资料已合并，请使用当前作品记录')
            if kind == 'cmm_reference':
                from .cmm import CMMService
                CMMService(self.store, None).validated_evidence(p, [identifier], actions=['retain_metadata', 'model_process'])
            elif (record.get('model_process') is False or record.get('metadata', {}).get('model_process') is False):
                raise ValueError('参考没有模型处理许可')
            if record.get('rights_snapshot'):
                from .industry import Industry
                Industry._require(record['rights_snapshot'], ['retain_metadata', 'model_process'])
            if not record.get('asset_id'):
                # Supplier text is supported only with an explicit verified content basis.
                if kind != 'cmm_reference' or record.get('content_basis') not in {'transcript', 'supplier_transcript', 'storyboard_transcript'}:
                    raise ValueError('仅有链接或标题资料，尚无可用内容依据')
                return state
            state['asset'] = self.store.get(p, 'asset', record['asset_id'])
        elif kind == 'asset':
            state['asset'] = record
        if state.get('asset'):
            asset = state['asset']
            if not self._allowed(p, 'asset', asset):
                raise ValueError('素材不属于当前账号可访问范围')
            if not _active(asset) or asset.get('inspection') != 'ready' or not self._file_valid(p, asset):
                raise ValueError('素材文件不存在、版本变化或尚未就绪')
            meta = asset.get('metadata') or {}
            if asset.get('source_kind') == 'industry' or meta.get('industry_reference_ids') or meta.get('industry_reference_id'):
                from .industry import Industry
                legacy = Industry(self.store, self.media, None)
                legacy.validate_asset_access(p, asset['id'])
                legacy.validate_model_use(p, asset_ids=[asset['id']])
            if meta.get('model_process') is False or (meta.get('rights') or {}).get('model_process') is False:
                raise ValueError('素材没有模型处理许可')
            if asset.get('source_kind') in OWN_KINDS:
                require_own_reuse(meta)
            elif meta.get('rights'):
                from .industry import Industry
                Industry._require(meta['rights'], ['retain_media', 'model_process'])
            state['observations'] = [r for r in self.store.list(p, 'observation') if r.get('asset_id') == asset['id'] and self._allowed(p, 'observation', r)]
            state['manifests'] = {}
            for observation in state['observations']:
                manifest_id = observation.get('input_manifest_id')
                if manifest_id:
                    try:
                        state['manifests'][manifest_id] = self.store.get(p, 'media_input_manifest', manifest_id)
                    except NotFound:
                        pass
            if asset.get('parsed_record_id'):
                try:
                    state['parse'] = self.store.get(p, 'parse', asset['parsed_record_id'])
                except NotFound:
                    pass
        return state

    @staticmethod
    def _observed_parts(state):
        """Only granular persisted observations with original input support qualify."""
        asset = state.get('asset') or {}
        result = []
        observations = list(state.get('observations', []))
        parsed = state.get('parse') or {}
        observations.extend(r for r in parsed.get('rows', []) if r.get('asset_id') == asset.get('id'))
        for row in observations:
            observation = row.get('observation') or row
            items = [observation] if observation.get('claim_kind') else observation.get('observations', [])
            if not items:
                continue
            for item in items:
                manifest = state.get('manifests', {}).get(item.get('input_manifest_id'))
                if not manifest or manifest.get('execution_state') != 'succeeded':
                    continue
                try:
                    from .evidence_policy import validate_observations
                    validate_observations(manifest, [item])
                except ValueError:
                    continue
                if (item.get('asset_id') != asset.get('id') or item.get('asset_sha256') != asset.get('sha256')
                        or item.get('quality_state') != 'usable' or item.get('interpretation') != 'observed'
                        or item.get('claim_kind') not in {'visual_observation', 'screen_text'}
                        or not item.get('support') or not item.get('input_manifest_id')):
                    continue
                points = [s['time_ms'] for s in item['support'] if type(s.get('time_ms')) is int]
                ranges = [s for s in item['support'] if type(s.get('start_ms')) is int and type(s.get('end_ms')) is int]
                start = min(points + [s['start_ms'] for s in ranges], default=None)
                end = max([t + 1 for t in points] + [s['end_ms'] for s in ranges], default=None)
                result.append({'text': item['statement'], 'kind': 'visual', 'start_ms': start, 'end_ms': end,
                    'evidence_ids': [item.get('observation_id') or item.get('id') or digest(item)],
                    'support': item['support'], 'input_manifest_id': item['input_manifest_id'],
                    'provenance': item.get('route', observation.get('provenance', 'stored_visual_observation'))})
        return result

    def _chunks(self, p, kind, identifier):
        state = self._state(p, kind, identifier)
        record, asset = state['record'], state.get('asset')
        parts = []
        if asset:
            from .evidence_policy import transcript_policy
            policy = transcript_policy(asset, purpose='retrieve')
            if policy['allowed']:
                parts.extend({'text': s['text'], 'start_ms': s['start_ms'], 'end_ms': s['end_ms'],
                    'kind': 'transcript', 'evidence_ids': [s['segment_id']], 'review_state': policy['review_state'],
                    'provenance': policy['source'], 'allowed_uses': policy['allowed_uses']} for s in policy['segments'])
            parts.extend(self._observed_parts(state))
            pool = 'own' if kind == 'asset' and asset.get('source_kind') in OWN_KINDS else 'reference'
        elif kind in {'script', 'preference', 'instruction'}:
            if record.get('confirmed') is not True:
                raise ValueError('创作历史或偏好尚未人工确认')
            if kind == 'script' and record.get('usage_status') not in {'adopted', 'shot'}:
                raise ValueError('自动生成稿未被采用，不作为历史经验')
            text = record.get('text') or record.get('content') or record.get('feedback_reason')
            if kind == 'script':
                text = '\n'.join(r.get('dialogue', '') for r in record.get('rows', []))
            if text:
                parts = [{'text': text, 'kind': 'confirmed_history', 'provenance': 'human_confirmed_history'}]
            pool = 'history'
        else:
            # Content qualification and rights were verified in _state, never title.
            text = record.get('transcript') or record.get('text')
            if isinstance(text, str) and text.strip():
                parts = [{'text': text, 'kind': 'supplier_transcript', 'provenance': 'supplier_transcript'}]
            pool = 'reference'
        if not parts:
            raise ValueError('资料尚无可检索的可靠内容；标题不能作为已观察内容')
        metadata = (asset or record).get('metadata') or {}
        source_version = digest(state)
        chunks = []
        for part in parts:
            # A timed source's full interval stays attached to each text subdivision.
            # FTS fallback preserves its entire text; it is never embedded truncated.
            try:
                texts = self.embeddings.split_text(part['text']) if self.embeddings else [part['text']]
            except EmbeddingUnavailable:
                texts = [part['text']]
            for number, text in enumerate(texts):
                identity = digest([p, kind, identifier, source_version, part, number, text])
                chunks.append(asdict(KnowledgeChunk(
                    chunk_id='chunk-' + identity, workspace_id=self.workspace_id, project_id=p,
                    kind=part['kind'], pool=pool, source_kind=kind, source_record_id=identifier,
                    source_version=source_version, content_hash=digest(text), text=text,
                    asset_id=asset.get('id') if asset else None, asset_sha256=asset.get('sha256') if asset else None,
                    platform_work_id=record.get('platform_entity_id') or record.get('platform_work_id'),
                    start_ms=part.get('start_ms'), end_ms=part.get('end_ms'), evidence_ids=part.get('evidence_ids', []),
                    product_id=metadata.get('product_id') or record.get('product_id'),
                    sku_id=metadata.get('sku_id') or record.get('sku_id'), generic_footage=bool(metadata.get('generic_footage')),
                    category_path=metadata.get('category_path') or record.get('category_path') or [],
                    audience_tags=metadata.get('audience_tags') or record.get('audience_tags') or [],
                    scene_tags=metadata.get('scene_tags') or record.get('scene_tags') or [],
                    allowed_uses=part.get('allowed_uses', ['retrieve', 'inspiration']),
                    rights_snapshot_id=digest(metadata.get('rights') or record.get('rights_snapshot') or {'reuse_allowed': metadata.get('reuse_allowed')}),
                    source_observed_at=record.get('updated_at'), expires_at=(metadata.get('rights') or record.get('rights_snapshot') or {}).get('expires_at'),
                    embedding_space_id=self.embeddings.space.id if self.embeddings else None,
                    provenance=part['provenance'], review_state=part.get('review_state', 'unreviewed'),
                    support=part.get('support', []), input_manifest_id=part.get('input_manifest_id'),
                    metrics=record.get('metrics'), title=(asset or record).get('title', ''))))
        return chunks

    def valid(self, p, chunk, *, purpose='retrieve', product_id='', sku_id='', selected_asset_ids=None, selected_reference_ids=None, excluded_source_ids=(), selected_own_asset_ids=None):
        if chunk.get('project_id') != p or chunk.get('workspace_id') != self.workspace_id:
            return False, '资料项目或工作空间不一致'
        if chunk['source_record_id'] in excluded_source_ids or chunk.get('asset_id') in excluded_source_ids:
            return False, '本轮已排除此来源'
        if chunk['pool'] == 'own':
            # A separate own pool is opt-in. Otherwise explicit material
            # selection constrains own footage too; [] must stay empty.
            own_scope = selected_own_asset_ids if selected_own_asset_ids is not None else selected_asset_ids
            if own_scope is not None and chunk.get('asset_id') not in own_scope:
                return False, '不在本轮允许查找的自有素材范围内'
        if chunk['pool'] == 'reference':
            asset_chosen = selected_asset_ids is not None and chunk.get('asset_id') in selected_asset_ids
            ref_chosen = selected_reference_ids is not None and chunk['source_kind'] in {'public_reference', 'cmm_reference'} and chunk['source_record_id'] in selected_reference_ids
            explicit = selected_asset_ids is not None or selected_reference_ids is not None
            if explicit and not (asset_chosen or ref_chosen):
                return False, '不在本轮明确选材或参考范围内'
        if purpose not in chunk.get('allowed_uses', []):
            return False, '资料未获当前用途许可'
        if chunk['pool'] in {'own', 'history'}:
            generic = chunk.get('generic_footage') and chunk['pool'] == 'own'
            if product_id and chunk.get('product_id') != product_id and not generic:
                return False, '素材不适用于当前商品'
            if sku_id and chunk.get('sku_id') != sku_id and not generic:
                return False, '素材不适用于当前SKU'
        try:
            fresh = self._chunks(p, chunk['source_kind'], chunk['source_record_id'])
            if not any(c['chunk_id'] == chunk['chunk_id'] for c in fresh):
                return False, '内容或资料版本已变化，正在重新整理'
        except (ValueError, OSError) as exc:
            return False, str(exc)
        return True, ''

    def _vector_store(self):
        if not self.embeddings:
            raise EmbeddingUnavailable('未启用本地语义模型')
        if self._vectors is None:
            self._vectors = _VectorStore(self.store.path.parent / 'knowledge-vectors' / self.workspace_id, self.embeddings.space)
        return self._vectors

    def _replace(self, p, kind, identifier):
        try:
            chunks = self._chunks(p, kind, identifier)
        except (ValueError, OSError):
            chunks = []
        with self.store.connection() as db:
            old = [row['chunk_id'] for row in db.execute('SELECT chunk_id FROM knowledge_chunk WHERE project_id=? AND source_kind=? AND source_record_id=?', (p, kind, identifier))]
        semantic_ready, error = False, None
        if chunks and self.embeddings:
            try:
                vectors = self.embeddings.embed([c['text'] for c in chunks])
                self._vector_store().upsert(chunks, vectors)
                semantic_ready = True
            except Exception as exc:
                error = str(exc)
                self.semantic_error = error
        if self._vectors:
            self._vectors.delete(list(set(old) - {c['chunk_id'] for c in chunks}))
        with self.store.connection(True) as db:
            db.execute('DELETE FROM knowledge_fts WHERE project_id=? AND chunk_id IN (SELECT chunk_id FROM knowledge_chunk WHERE project_id=? AND source_kind=? AND source_record_id=?)', (p, p, kind, identifier))
            db.execute('DELETE FROM knowledge_chunk WHERE project_id=? AND source_kind=? AND source_record_id=?', (p, kind, identifier))
            for c in chunks:
                c['index_state'] = 'ready' if semantic_ready else 'keyword_only'
                db.execute('INSERT INTO knowledge_chunk VALUES(?,?,?,?,?,?,?,?,?,?)', (c['chunk_id'], p, c['pool'], kind, identifier, c['source_version'], c['content_hash'], c['embedding_space_id'], c['index_state'], dumps(c)))
                db.execute('INSERT INTO knowledge_fts VALUES(?,?,?,?)', (c['chunk_id'], p, c['pool'], ' '.join(tokens(c['text']))))
        return len(chunks), semantic_ready or not chunks, error

    def _affected(self, job):
        p, kind, identifier = job['project_id'], job['source_kind'], job['source_record_id']
        affected = [(kind, identifier)] if kind in SOURCE_KINDS else []
        if kind in {'observation', 'parse', 'scene', 'media_input_manifest'}:
            try:
                row = self.store.get(p, kind, identifier)
                ids = {row.get('asset_id')} | {r.get('asset_id') for r in row.get('rows', [])}
                affected.extend(('asset', a) for a in ids if a)
            except NotFound:
                # Removed observations must invalidate their previously derived asset chunks.
                affected.extend(('asset', a['id']) for a in self.store.list(p, 'asset'))
        if kind == 'asset':
            affected.extend((k, r['id']) for k in ('public_reference', 'cmm_reference') for r in self.store.list(p, k) if r.get('asset_id') == identifier)
        if kind in {'cmm_connection', 'cmm_job', 'industry_connection'}:
            affected.extend(('cmm_reference', r['id']) for r in self.store.list(p, 'cmm_reference'))
        return list(dict.fromkeys(affected))

    def process_pending(self, *, limit=20):
        """Single worker owns this method; it never invokes a commercial provider."""
        handled = []
        with self._lock:
            for _ in range(max(1, min(limit, 100))):
                with self.store.connection(True) as db:
                    row = db.execute("SELECT * FROM index_job WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
                    if not row:
                        break
                    job = dict(row)
                    db.execute("UPDATE index_job SET state='running',attempts=attempts+1,started_at=?,error=NULL WHERE id=?", (now(), job['id']))
                started = time.monotonic()
                try:
                    count, ready, errors = 0, True, []
                    for kind, identifier in self._affected(job):
                        with self.store.connection() as db:
                            if db.execute('SELECT state FROM index_job WHERE id=?', (job['id'],)).fetchone()['state'] == 'cancelled':
                                break
                        number, ok, error = self._replace(job['project_id'], kind, identifier)
                        count += number; ready = ready and ok
                        if error:
                            errors.append(error)
                    state = 'ready' if ready else 'keyword_only'
                    error = '; '.join(errors) or None
                except Exception as exc:
                    state, count, error = 'failed', 0, str(exc)
                with self.store.connection(True) as db:
                    db.execute("UPDATE index_job SET state=?,chunks=?,error=?,finished_at=?,duration_ms=? WHERE id=? AND state!='cancelled'", (state, count, error, now(), round((time.monotonic()-started)*1000), job['id']))
                    db.execute('UPDATE knowledge_outbox SET processed_at=? WHERE id=?', (now(), job['outbox_id']))
                handled.append(job['id'])
        return handled

    def candidates(self, p, pool, *, filters=None):
        if pool not in POOLS:
            raise ValueError('资料池无效')
        self.store.project(p)
        filters = filters or {}
        with self.store.connection() as db:
            indexed = [json.loads(row['data']) for row in db.execute('SELECT data FROM knowledge_chunk WHERE project_id=? AND pool=?', (p, pool))]
        current, exclusions, seen = [], [], set()
        for chunk in indexed:
            ok, reason = self.valid(p, chunk, **filters)
            if ok:
                current.append(chunk)
                seen.add((chunk['source_kind'], chunk['source_record_id']))
            elif '账号可访问范围' not in reason:
                exclusions.append({'source_record_id': chunk['source_record_id'], 'reason': reason})
        # Immediately selected/new records remain usable with a truthful keyword
        # fallback while the durable worker catches up; this does not run embedding.
        for kind in SOURCE_KINDS:
            for record in self.store.list(p, kind):
                if not self._allowed(p, kind, record):
                    continue
                if (kind, record['id']) in seen:
                    continue
                try:
                    for chunk in self._chunks(p, kind, record['id']):
                        if chunk['pool'] != pool:
                            continue
                        ok, reason = self.valid(p, chunk, **filters)
                        if ok:
                            current.append(chunk)
                        else:
                            exclusions.append({'source_record_id': record['id'], 'reason': reason})
                except (ValueError, OSError) as exc:
                    exclusions.append({'source_record_id': record['id'], 'reason': str(exc)})
        self.last_exclusions = exclusions
        return current

    def search(self, p, query, *, pool='reference', limit=40, **filters):
        limit = max(1, min(limit, 40))
        current = self.candidates(p, pool, filters=filters)
        by_id = {c['chunk_id']: c for c in current}
        wanted = tokens(query)[:128]
        lexical = []
        if wanted and by_id:
            match = ' OR '.join('"' + t.replace('"', '""') + '"' for t in dict.fromkeys(wanted))
            # Rank only current eligible rows; FTS5 BM25 is smaller-is-better.
            with self.store.connection() as db:
                placeholders = ','.join('?' for _ in by_id)
                rows = db.execute(f'SELECT chunk_id,bm25(knowledge_fts) AS score FROM knowledge_fts WHERE knowledge_fts MATCH ? AND project_id=? AND pool=? AND chunk_id IN ({placeholders}) ORDER BY score LIMIT ?', (match, p, pool, *by_id, limit)).fetchall()
            lexical = [r['chunk_id'] for r in rows]
            indexed_ids = set(lexical)
            # New records have no FTS row yet. Only their real body enters this fallback.
            fresh = sorted(((sum((Counter(wanted) & Counter(tokens(c['text']))).values()), c['chunk_id']) for c in current if c['chunk_id'] not in indexed_ids), key=lambda r: (-r[0], r[1]))
            lexical.extend(identifier for overlap, identifier in fresh if overlap > 0)
            lexical = lexical[:limit]
        semantic = []
        if self.embeddings and query.strip() and by_id:
            eligible = [i for i, c in by_id.items() if c['index_state'] == 'ready' and c['embedding_space_id'] == self.embeddings.space.id]
            if eligible:
                try:
                    semantic = self._vector_store().search(p, pool, self.embeddings.query(query), eligible, limit=limit)
                except Exception as exc:
                    self.semantic_error = str(exc)
        ranked = reciprocal_rank_fusion(lexical, semantic)
        if (filters.get('selected_asset_ids') is not None or filters.get('selected_reference_ids') is not None
                or (pool == 'own' and filters.get('selected_own_asset_ids') is not None)):
            ranked_ids = {identifier for identifier, _ in ranked}
            ranked.extend((identifier, 0.0) for identifier in by_id if identifier not in ranked_ids)
        if not query.strip():
            ranked = [(identifier, 0.0) for identifier in by_id]
        results = []
        for identifier, score in ranked[:limit]:
            c = by_id[identifier]
            ok, reason = self.valid(p, c, **filters)
            if ok:
                results.append({**c, 'rrf_score': score, 'matching_basis': 'hybrid_local' if semantic else 'local_text_content'})
            else:
                self.last_exclusions.append({'source_record_id': c['source_record_id'], 'reason': reason})
        return results

    def status(self, p):
        self.store.project(p)
        with self.store.connection() as db:
            rows = db.execute('SELECT index_state,count(*) AS n FROM knowledge_chunk WHERE project_id=? GROUP BY index_state', (p,)).fetchall()
            counts = {r['index_state']: r['n'] for r in rows}
            pending = db.execute("SELECT count(*) FROM index_job WHERE project_id=? AND state IN ('queued','running')", (p,)).fetchone()[0]
        root = self.store.path.parent / 'knowledge-vectors' / self.workspace_id
        size = sum(f.stat().st_size for f in root.rglob('*') if f.is_file()) if root.exists() else 0
        return {'chunks': sum(counts.values()), 'semantic_chunks': counts.get('ready', 0),
            'semantic_ready': bool(counts.get('ready')) and not pending, 'pending_jobs': pending,
            'state': 'organizing' if pending else 'ready' if counts.get('ready') else 'keyword_only',
            'message': '正在整理资料' if pending else '本地资料已整理' if counts.get('ready') else '暂使用关键词查找',
            'vector_disk_bytes': size, 'semantic_error': self.semantic_error,
            'embedding_space': asdict(self.embeddings.space) if self.embeddings else None,
            'embedding_space_id': self.embeddings.space.id if self.embeddings else None}
