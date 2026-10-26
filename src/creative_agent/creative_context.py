"""One server-side context entry for reference search, scripts and revisions."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json

from .creative_tasks import TaskSpec, freeze_selection
from .knowledge_index import KnowledgeIndex, digest
from .product_evidence import build_product_evidence, available_product_evidence, model_product_facts
from .scripts import Scripts
from .store import NotFound, dumps, now, uid

INTENTS = {'new_script', 'find_references', 'analyze_selected', 'revise_current', 'new_direction', 'general_discussion'}


class CreativeContextService:
    def __init__(self, store, media=None, *, index=None, authorize=None, candidate_filter=None):
        self.store, self.media = store, media
        self.index = index if index is not None else getattr(store, 'knowledge_index', None) or KnowledgeIndex(store, media, candidate_filter=candidate_filter)
        if candidate_filter is not None:
            self.index.candidate_filter = candidate_filter
        # API retains authentication/licensing; embedding/search cannot enlarge it.
        self.authorize = authorize

    @staticmethod
    def _within_segments(chunk,segments):
        if chunk.get('source_kind')!='asset':return True
        ranges=[r for r in segments if r['asset_id']==chunk.get('asset_id',chunk.get('source_record_id'))]
        if not ranges:return True
        start,end=chunk.get('start_ms'),chunk.get('end_ms')
        return type(start) is int and type(end) is int and any(r['start_ms']<=start<end<=r['end_ms'] for r in ranges)

    def snapshot(self, project_id, snapshot_id):
        self.store.project(project_id)
        with self.store.connection() as db:
            row = db.execute('SELECT data FROM retrieval_snapshot WHERE id=? AND project_id=?', (snapshot_id, project_id)).fetchone()
        if not row:
            try:
                return self.store.get(project_id, 'creative_context_snapshot', snapshot_id)
            except NotFound:
                raise NotFound('创作依据快照不存在或不属于当前项目') from None
        return json.loads(row['data'])

    def revalidate_context(self, project_id, snapshot_or_id):
        """Reload the server-owned snapshot and fail closed immediately before use.

        Returns the persisted context on success; raises ValueError on any revoked,
        expired, changed, missing, differently owned or unqualified source. Does
        not call a model, re-embed or mutate the historical snapshot.
        """
        identifier = snapshot_or_id if isinstance(snapshot_or_id, str) else snapshot_or_id.get('context_snapshot_id')
        saved = self.snapshot(project_id, identifier)
        if saved.get('archived_restored') or saved.get('model_use_requires_revalidation') or saved.get('audit_only'):
            raise ValueError('恢复的历史创作依据仅供审计；请从当前已授权资料重新建立创作依据')
        if self.authorize:
            self.authorize(project_id, saved['intent'])
        if not isinstance(snapshot_or_id, str) and snapshot_or_id.get('snapshot_hash') != saved['snapshot_hash']:
            raise ValueError('创作依据与服务端快照不一致')
        if digest({k: v for k, v in saved.items() if k != 'snapshot_hash'}) != saved['snapshot_hash']:
            raise ValueError('创作依据快照校验失败')
        frozen = saved.get('selection_snapshot', {})
        filters = {'product_id': saved.get('product_id', ''), 'sku_id': saved.get('sku_id', ''),
                   'selected_asset_ids': frozen.get('selected_asset_ids'),
                   'selected_reference_ids': frozen.get('selected_reference_ids'),'selected_own_asset_ids':frozen.get('selected_own_asset_ids')}
        for rows in saved.get('chunks', {}).values():
            if any(not self._within_segments(c,frozen.get('selected_segments',[])) for c in rows):
                raise ValueError('创作依据超出所选片段')
            for chunk in rows:
                ok, reason = self.index.valid(project_id, chunk, **filters)
                if not ok:
                    raise ValueError('创作依据已变化，需要刷新：' + reason)
        current = Scripts(self.store).facts(project_id)
        valid, invalid = available_product_evidence(saved.get('product_evidence', []), current,
            project_id, product_id=saved.get('product_id', ''), sku_id=saved.get('sku_id', ''))
        if invalid or len(valid) != len(saved.get('product_evidence', [])):
            raise ValueError('商品依据已撤销、过期或变更，需要刷新创作依据')
        _, _, excluded = self._facts(project_id, saved.get('product_id'), saved.get('sku_id'))
        if saved.get('product_facts', {}).get('price_and_promotion') and any('促销' in e['reason'] for e in excluded):
            raise ValueError('价格或促销已过期，需要刷新创作依据')
        return saved

    @staticmethod
    def _diverse(candidates, *, limit, source_limit=2, text_budget=14000):
        picked, sources, bodies, size = [], Counter(), set(), 0
        for item in candidates:
            source = item.get('asset_sha256') or item['source_record_id']
            normalized = ''.join(item['text'].lower().split())
            if sources[source] >= source_limit or normalized in bodies:
                continue
            if size + len(item['text']) > text_budget:
                continue
            picked.append(item)
            sources[source] += 1
            bodies.add(normalized)
            size += len(item['text'])
            if len(picked) >= limit:
                break
        return picked

    @staticmethod
    def _evidence(chunk):
        item = {**deepcopy(chunk), 'id': chunk['chunk_id'], 'title': chunk.get('title', ''),
                'evidence_kind': chunk['kind'], 'source_version': chunk['source_version'],
                'not_product_fact': True, 'statement': chunk['text'], 'claim_kind': 'visual_observation' if chunk['kind'] == 'visual' else 'transcript_content',
                'interpretation': 'observed', 'observed_timestamps_ms': [s['time_ms'] for s in chunk.get('support', []) if 'time_ms' in s]}
        if chunk['kind'] in {'transcript', 'supplier_transcript'}:
            item['transcript'] = chunk['text']
            item['subtitles'] = ([{'start_ms': chunk['start_ms'], 'end_ms': chunk['end_ms'], 'text': chunk['text']}]
                                 if chunk.get('start_ms') is not None else [])
            item['word_exact'] = chunk.get('review_state') == 'corrected'
        else:
            item['description'] = chunk['text']
        return item

    @staticmethod
    def _applicability(chunk, facts):
        category = facts.get('category_path') or []
        matching = [v for v in chunk.get('category_path', []) if v in category]
        if matching:
            kind, reason = '同类参考', '资料标注与当前商品共有品类：' + '、'.join(matching)
        else:
            kind, reason = '表达借鉴', '内容按本次需求召回；尚无充分品类匹配依据'
        audience = [tag for tag in chunk.get('audience_tags', []) if tag in facts.get('audience', '')]
        if audience:
            reason += '；已标注受众匹配：' + '、'.join(audience)
        return {'type': kind, 'suitable_reason': reason,
            'transferable': '仅借鉴已提供内容的信息顺序、演示安排或开头表达；不沿用竞品参数',
            'needs_adjustment': ['本品参数须使用当前商品事实', '出镜、场地与补拍条件尚未核实'],
            'performance_basis': deepcopy(chunk.get('metrics')) if chunk.get('metrics') else '未提供',
            'content_basis': '已分析画面' if chunk['kind'] == 'visual' else '文字稿',
            'causal_claim': None}

    def _facts(self, p, product_id, sku_id):
        facts = Scripts(self.store).facts(p)
        exclusions = []
        if ((product_id and facts.get('product_id') != product_id)
                or (sku_id and facts.get('sku_id') != sku_id)):
            exclusions.append({'source_record_id': facts.get('id'), 'reason': '当前商品资料与本轮商品或SKU不同，不能替代本品事实'})
            return {'version': 0, 'product_id': product_id or '', 'sku_id': sku_id or '', 'claims': [], 'constraints': []}, [], exclusions
        evidence = build_product_evidence(facts, p)
        allowed_ids = {e['fact_id'] for e in evidence}
        exclusions.extend({'source_record_id': facts.get('id'), 'fact_id': c.get('fact_id'),
                           'reason': '商品说法未确认、已撤销、过期或缺少依据'} for c in facts.get('claims', []) if c.get('fact_id') not in allowed_ids)
        safe = model_product_facts(facts)
        safe['claims'] = [c for c in safe.get('claims', []) if c.get('fact_id') in allowed_ids]
        if safe.get('price_and_promotion'):
            from datetime import date
            try:
                valid = date.fromisoformat(str(safe.get('promotion_valid_until'))) >= date.today()
            except ValueError:
                valid = False
            if not valid:
                safe['price_and_promotion'] = ''
                exclusions.append({'source_record_id': facts.get('id'), 'reason': '价格或促销有效期不明或已过期'})
        return safe, evidence, exclusions

    def _freeze(self, p, refs, own, history, facts, selected_asset_ids, selected_reference_ids):
        asset_ids = list(dict.fromkeys(c['asset_id'] for c in refs if c.get('asset_id')))
        own_ids = list(dict.fromkeys(c['asset_id'] for c in own if c.get('asset_id')))
        spec = TaskSpec(own_asset_ids=own_ids,
                        history_script_ids=[c['source_record_id'] for c in history if c['source_kind'] == 'script'])
        frozen = freeze_selection(self.store, p, {'project_id': p, 'asset_ids': asset_ids,
            'reference_ids': list(dict.fromkeys(c['source_record_id'] for c in refs if c['source_kind'] in {'public_reference', 'cmm_reference'})), 'discovery': False}, spec)
        frozen.update(product_facts_snapshot=deepcopy(facts), selected_asset_ids=selected_asset_ids,
                      selected_reference_ids=selected_reference_ids)
        return frozen

    def build_context(self, project_id, intent, product_id=None, sku_id=None,
                      selected_asset_ids=None, reference_scope='project', previous_context_id=None,
                      retrieval_policy_version='v1', *, selected_reference_ids=None, query='',
                      excluded_source_ids=None, selected_own_asset_ids=None, selected_segments=None):
        """Build a bounded, persisted, current-rights-checked local-only snapshot.

        None selection = current project's authorized pool. An explicit [] selects
        no assets. selected_own_asset_ids, when supplied, independently constrains
        the own pool; None inherits selected_asset_ids and [] stays empty.
        Explicit reference IDs add only those references, never a new
        project or provider request. Revisions reuse the prior sources and recheck
        their current versions, permissions, files and product claims.
        """
        self.store.project(project_id)
        if intent not in INTENTS or reference_scope != 'project' or retrieval_policy_version != 'v1':
            raise ValueError('创作意图、资料范围或检索策略不受支持')
        if self.authorize:
            self.authorize(project_id, intent)
        if not isinstance(query, str) or len(query) > 20000:
            raise ValueError('检索内容无效或过长')
        for ids in (selected_asset_ids, selected_reference_ids, excluded_source_ids, selected_own_asset_ids):
            if ids is not None and (not isinstance(ids, (list, tuple, set)) or len(ids) > 10000 or any(not isinstance(i, str) for i in ids)):
                raise ValueError('选材列表无效')
        selected_asset_ids = list(dict.fromkeys(selected_asset_ids)) if selected_asset_ids is not None else None
        selected_own_asset_ids = list(dict.fromkeys(selected_own_asset_ids)) if selected_own_asset_ids is not None else None
        selected_reference_ids = list(dict.fromkeys(selected_reference_ids)) if selected_reference_ids else None
        # Validate identities in this exact project before retrieval.
        for aid in dict.fromkeys((selected_asset_ids or []) + (selected_own_asset_ids or [])):
            self.store.get(project_id, 'asset', aid)
        for rid in selected_reference_ids or []:
            try:
                self.store.get(project_id, 'public_reference', rid)
            except NotFound:
                self.store.get(project_id, 'cmm_reference', rid)
        previous = self.snapshot(project_id, previous_context_id) if previous_context_id else None
        if previous and intent == 'revise_current' and (previous.get('archived_restored') or previous.get('model_use_requires_revalidation')):
            raise ValueError('恢复的历史创作依据仅供审计；请从当前已授权资料重新建立创作依据')
        if previous and intent == 'revise_current':
            product_id = product_id or previous.get('product_id')
            sku_id = sku_id or previous.get('sku_id')
        if selected_segments is None:selected_segments=(previous or {}).get('selection_snapshot',{}).get('selected_segments',[])
        from .contracts import EvidenceRef
        segments=[EvidenceRef.model_validate(r).model_dump(mode='json') for r in selected_segments]
        for segment in segments:
            self.store.get(project_id,'asset',segment['asset_id'])
            if type(segment['start_ms']) is not int or type(segment['end_ms']) is not int or segment['start_ms']>=segment['end_ms']:
                raise ValueError('片段需要明确起止时间')
        facts, product_evidence, exclusions = self._facts(project_id, product_id, sku_id)
        product_id = product_id or facts.get('product_id') or ''
        sku_id = sku_id or facts.get('sku_id') or ''
        filters = {'product_id': product_id, 'sku_id': sku_id,
                   'selected_asset_ids': selected_asset_ids, 'selected_reference_ids': selected_reference_ids,
                   'excluded_source_ids': list(excluded_source_ids or []),'selected_own_asset_ids':selected_own_asset_ids}
        if previous and intent == 'revise_current':
            # A new product/SKU is a new local retrieval, even when wording says revise.
            same_product = product_id == previous.get('product_id') and sku_id == previous.get('sku_id')
        else:
            same_product = False
        pools = {pool: [] for pool in ('reference', 'own', 'history')}
        if same_product:
            for pool in pools:
                for chunk in previous.get('chunks', {}).get(pool, []):
                    ok, reason = self.index.valid(project_id, chunk, **filters)
                    if ok:
                        pools[pool].append(chunk)
                    else:
                        exclusions.append({'source_record_id': chunk['source_record_id'], 'reason': reason})
            current_facts = Scripts(self.store).facts(project_id)
            prior_evidence, invalid = available_product_evidence(previous.get('product_evidence', []), current_facts,
                project_id, product_id=product_id, sku_id=sku_id)
            product_evidence = prior_evidence
            exclusions.extend(invalid)
        elif intent != 'general_discussion':
            for pool in pools:
                pools[pool] = self.index.search(project_id, query, pool=pool, limit=40, **filters)
                exclusions.extend(self.index.last_exclusions)
        for pool,rows in pools.items():
            exclusions.extend({'source_record_id':c['source_record_id'],'chunk_id':c['chunk_id'],'reason':'内容不在本次所选片段内'} for c in rows if not self._within_segments(c,segments))
            pools[pool]=[c for c in rows if self._within_segments(c,segments)]
        refs = self._diverse(pools['reference'], limit=6, text_budget=10000)
        own = self._diverse(pools['own'], limit=12, text_budget=14000)
        history = self._diverse(pools['history'], limit=3, text_budget=5000)
        for pool, picked in [('reference', refs), ('own', own), ('history', history)]:
            chosen = {c['chunk_id'] for c in picked}
            exclusions.extend({'source_record_id': c['source_record_id'], 'chunk_id': c['chunk_id'],
                               'reason': '按内容去重、来源多样性或输入长度预算未选入'} for c in pools[pool] if c['chunk_id'] not in chosen)
        # Final read after ranking: stale vector payload never grants permission.
        final = {'reference': [], 'own': [], 'history': []}
        for pool, rows in [('reference', refs), ('own', own), ('history', history)]:
            for chunk in rows:
                ok, reason = self.index.valid(project_id, chunk, **filters)
                if ok:
                    final[pool].append(chunk)
                else:
                    exclusions.append({'source_record_id': chunk['source_record_id'], 'reason': reason})
        refs, own, history = (final[pool] for pool in ('reference', 'own', 'history'))
        snapshot_id = 'context-' + uid()
        frozen = self._freeze(project_id, refs, own, history, facts, selected_asset_ids, selected_reference_ids)
        frozen['selected_own_asset_ids']=selected_own_asset_ids
        frozen['selected_segments']=deepcopy(segments)
        context = {'id': snapshot_id, 'context_snapshot_id': snapshot_id, 'project_id': project_id,
            'workspace_id': self.index.workspace_id, 'intent': intent, 'query': query,
            'product_id': product_id, 'sku_id': sku_id, 'product_facts': facts,
            'product_evidence': product_evidence, 'previous_context_id': previous_context_id,
            'retrieval_policy_version': retrieval_policy_version, 'rrf_k': 60,
            'selection_snapshot': frozen, 'chunks': final,
            'evidence': [self._evidence(c) for c in refs + own],
            'own_footage': [{**deepcopy(c), 'id': c['chunk_id'], 'score': c.get('rrf_score', 0),
                             'matching_basis': c.get('matching_basis', 'snapshot_reuse')} for c in own],
            'reference_patterns': [{**self._evidence(c), **self._applicability(c, facts)} for c in refs],
            'history_preferences': [self._evidence(c) for c in history],
            'source_versions': {c['source_record_id']: c['source_version'] for c in refs + own + history},
            'exclusions': list({dumps(e): e for e in exclusions}.values()),
            'browse_only': [{'source_record_id': e.get('source_record_id'), 'type': '仅供浏览',
                             'needs_observation': True, 'reason': e['reason']} for e in exclusions
                            if any(word in e.get('reason', '') for word in ('内容', '尚未就绪', '质量'))],
            'local_retrieval': self.index.status(project_id),
            'cost': {'local_embedding': '0', 'model_calls_added': 0, 'external_calls_added': 0},
            'created_at': now(), 'semantic_ranking_is_performance_probability': False}
        from .knowledge_cases import methodology_context
        context['methodology']=methodology_context(query=query)
        context['snapshot_hash'] = digest(context)
        with self.store.connection(True) as db:
            db.execute('INSERT INTO retrieval_snapshot VALUES(?,?,?,?)', (snapshot_id, project_id, dumps(context), context['created_at']))
        return context
