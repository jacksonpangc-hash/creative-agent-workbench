from __future__ import annotations

from copy import deepcopy
from pydantic import Field
from .contracts import Contract, EvidenceRef
from .store import now, Conflict, NotFound, dumps, reject_secrets


class BriefContent(Contract):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20000)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    hypotheses: list[str] = Field(default_factory=list, max_length=100)
    script_outline: list[str] = Field(default_factory=list, max_length=300)
    references: list[EvidenceRef] = Field(default_factory=list, max_length=200)
    confirmed: bool = False


class BriefCreate(Contract):
    session_id: str
    turn_index: int = Field(ge=0)
    finding_indexes: list[int] = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20000)
    hypotheses: list[str] = Field(default_factory=list, max_length=100)


class BriefRevision(BriefContent):
    expected_version: int = Field(ge=1)


class Briefs:
    def __init__(self, store): self.store = store

    def validate_content(self, project_id, source, values):
        content = BriefContent.model_validate(values).model_dump(mode='json')
        if not content['title'].strip() or not content['objective'].strip(): raise ValueError('简报标题和目标不能为空')
        allowed = set(source['scope']['asset_ids'])
        for ref in content['references']:
            if ref['asset_id'] not in allowed: raise ValueError('参考素材超出原分析范围')
            asset = self.store.get(project_id, 'asset', ref['asset_id'])
            start, end = ref.get('start_ms'), ref.get('end_ms')
            if (start is None) != (end is None): raise ValueError('片段起止时间需同时填写')
            if start is not None and (start >= end or end > asset['metadata']['duration_ms']): raise ValueError('简报片段时间越界')
            if ref.get('scene_id'):
                scene = self.store.get(project_id, 'scene', ref['scene_id'])
                if scene['asset_id'] != asset['id']: raise ValueError('镜头与素材不一致')
        return content

    def create(self, project_id, session_id, turn_index, finding_indexes, *, title, objective, hypotheses=None):
        args = BriefCreate(session_id=session_id, turn_index=turn_index, finding_indexes=finding_indexes,
                           title=title, objective=objective, hypotheses=hypotheses or [])
        session = self.store.get(project_id, 'session', args.session_id)
        if turn_index >= len(session['turns']): raise ValueError('分析轮次不存在')
        turn = session['turns'][turn_index]
        if any(i < 0 or i >= len(turn['answer']['findings']) for i in finding_indexes): raise ValueError('所选结论不存在')
        findings = [deepcopy(turn['answer']['findings'][i]) for i in dict.fromkeys(finding_indexes)]
        ids = {eid for finding in findings for eid in finding.get('evidence_ids', [])}
        evidence = [deepcopy(e) for e in turn['evidence'] if e['id'] in ids]
        source = dict(session_id=session_id, turn_index=turn_index, finding_indexes=list(dict.fromkeys(finding_indexes)),
                      conclusion_version=turn.get('created_at', session['updated_at']), answer_snapshot=deepcopy(turn['answer']),
                      findings=findings, evidence=evidence, scope=deepcopy(session['scope']))
        result = turn['answer'].get('result') or {}
        converged = session.get('mode') == 'inspiration' and result.get('phase') == 'converge'
        idea = (result.get('brief') or {}) if converged else {}
        inferred = list(dict.fromkeys([*idea.get('hypotheses', []), *[f['text'] for f in findings if f['type']=='hypothesis']]))
        content = self.validate_content(project_id, source, dict(title=title, objective=objective,
            hypotheses=hypotheses or inferred, constraints=idea.get('constraints', []),
            script_outline=result.get('script_outline', []) if converged else [],
            references=[dict(asset_id=e['asset_id'], observation=e.get('title','')) for e in evidence]))
        return self.store.put(project_id, 'brief', dict(**content, source=source, version=1, revisions=[]))

    def revise(self, project_id, brief_id, expected_version, **values):
        brief = self.store.get(project_id, 'brief', brief_id)
        values.setdefault('script_outline', brief.get('script_outline', []))
        content = self.validate_content(project_id, brief['source'], values)
        reject_secrets(content)
        # Compare and append under the same write lock; no lost revisions.
        import json
        with self.store.connection(True) as db:
            row = db.execute("SELECT data FROM records WHERE id=? AND project_id=? AND kind='brief'", (brief_id,project_id)).fetchone()
            if not row: raise NotFound('简报不存在')
            old = json.loads(row['data'])
            if old['version'] != expected_version: raise Conflict('简报已更新，请重新读取后编辑')
            snapshot = {k:v for k,v in old.items() if k != 'revisions'}
            updated = {**old, **content, 'version': old['version']+1, 'updated_at': now(),
                       'revisions': [*old['revisions'], snapshot]}
            db.execute('UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?', (dumps(updated),updated['updated_at'],brief_id,project_id))
        return updated

    def snapshot(self, project_id, brief_id):
        brief = self.store.get(project_id, 'brief', brief_id)
        if not brief['confirmed']: raise ValueError('生成前请先确认此版本创作简报')
        return {k:v for k,v in brief.items() if k != 'revisions'}
