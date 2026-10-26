"""Versioned creative assets. Shared by local and authenticated team services."""
import json
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Literal
from copy import deepcopy

from pydantic import Field, model_validator

from .contracts import Contract, EvidenceRef
from .store import Conflict, NotFound, dumps, now, reject_secrets, uid
from .usage_rights import require_own_reuse


_KEY_PRODUCT_CLAIM = re.compile(
    r'承重|承载|防水|防潮|防漏|杀菌|除菌|抑菌|抗菌|消毒|消炎|治愈|治疗|修复|美白|祛斑|祛痘|'
    r'减肥|瘦身|降糖|降脂|止痛|见效|疗效|有效率|到手价|售价|价格|买一送|销量|售出|成交|回购|转化率'
)

_NOTE_PROHIBITION = re.compile(
    r'(?:不要|禁止|避免|不得|不)(?:写入?|提及|宣称|宣传)'
    r'(?:价格|促销|销量|功效|未确认的?成分)'
    r'(?:[、或及和](?:价格|促销|销量|功效|未确认的?成分))*'
    r'|(?:不要|禁止|避免|不得|不)使用(?:未经授权|未授权)的?素材'
)


class OwnFootageRef(EvidenceRef):
    usage_mode: Literal['normal', 'freeze_frame', 'slow_motion', 'loop'] = 'normal'
    use_duration_ms: int | None = Field(default=None, gt=0)
    usage_note: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def explicit_usage(self):
        if self.usage_mode != 'normal' and (self.use_duration_ms is None or not self.usage_note.strip()):
            raise ValueError('定格、慢放或循环需要明确展示时长和使用说明；原始观察范围不变')
        return self


class ScriptRow(Contract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    dialogue: str = Field(default='', max_length=10000)
    tone: str = Field(default='', max_length=1000)
    visual: str = Field(default='', max_length=10000)
    notes: str = Field(default='', max_length=10000)
    references: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    own_references: list[OwnFootageRef] = Field(default_factory=list, max_length=20)
    subtitle: str = Field(default='',max_length=10000)
    camera: str = Field(default='',max_length=2000)
    props: list[str] = Field(default_factory=list,max_length=50)
    reshoot: str = Field(default='',max_length=5000)
    reshoot_duration_ms: int | None = Field(default=None, ge=0)
    claim_ids: list[str] = Field(default_factory=list,max_length=50)
    factual_claims: list[dict] = Field(default_factory=list,max_length=50)

    @model_validator(mode='after')
    def interval(self):
        if self.end_ms <= self.start_ms: raise ValueError('脚本结束时间必须晚于开始时间')
        if self.reshoot_duration_ms is not None:
            if self.reshoot_duration_ms > 0 and not self.reshoot.strip(): raise ValueError('补拍时长需要对应的补拍内容')
            if self.reshoot_duration_ms > self.end_ms - self.start_ms: raise ValueError('补拍时长不能超过镜头计划时长')
        return self


class ScriptContent(Contract):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20000)
    rows: list[ScriptRow] = Field(min_length=1, max_length=300)
    confirmed: bool = False
    timing_estimated: bool = False
    product_id: str = ''
    sku_id: str = ''
    product_facts_snapshot: dict = Field(default_factory=dict)
    selection_snapshot: dict = Field(default_factory=dict)
    context_snapshot_id: str = ''
    provenance_summary: dict = Field(default_factory=dict)
    unresolved_items: list[str] = Field(default_factory=list,max_length=300)
    validation_issues: list[str] = Field(default_factory=list,max_length=300)
    production_notes: list[str] = Field(default_factory=list,max_length=300)
    usage_status: Literal['planned','adopted','shot','rejected'] = 'planned'
    feedback_reason: str = Field(default='',max_length=10000)


class Claim(Contract):
    fact_id: str = ''
    valid_until: date | None = None
    statement: str = Field(min_length=1, max_length=5000)
    source: str = Field(default='', max_length=5000)
    confirmed: bool = False

    @model_validator(mode='after')
    def evidence(self):
        if self.confirmed and not self.source.strip(): raise ValueError('确认卖点需要依据')
        return self


class ProductFacts(Contract):
    product_id: str = ''
    sku_id: str = ''
    product: str = Field(min_length=1, max_length=500)
    audience: str = Field(default='', max_length=5000)
    claims: list[Claim] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    price_and_promotion: str = Field(default='', max_length=5000)
    promotion_valid_until: str | None = None


class Scripts:
    def __init__(self, store): self.store = store

    def _write(self, p, kind, data, record_id=None, expected=None):
        reject_secrets(data)
        with self.store.connection(True) as db:
            self.store._project(db, p)
            row = db.execute('SELECT data FROM records WHERE project_id=? AND kind=? AND id=?', (p,kind,record_id)).fetchone()
            old = json.loads(row['data']) if row else None
            if expected is not None and (old or {}).get('version', 0) != expected:
                raise Conflict('内容已更新，请重新读取后编辑')
            if old:
                snapshot = {k:v for k,v in old.items() if k != 'revisions'}
                record = {**old, **data, 'version':old['version']+1, 'updated_at':now(), 'revisions':[*old.get('revisions',[]),snapshot]}
                db.execute('UPDATE records SET data=?,updated_at=? WHERE project_id=? AND kind=? AND id=?', (dumps(record),record['updated_at'],p,kind,record_id))
            else:
                if record_id and expected not in (None,0): raise NotFound('记录不存在')
                timestamp = now()
                record = {**data,'id':record_id or uid(),'project_id':p,'created_at':timestamp,'updated_at':timestamp,'version':1,'revisions':[]}
                db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)', (record['id'],p,kind,dumps(record),timestamp,timestamp))
        return record

    @staticmethod
    def _normal_coverage(ranges):
        total = 0
        for intervals in ranges.values():
            end = -1
            for start, stop in sorted(intervals):
                total += max(0, stop - max(start, end))
                end = max(end, stop)
        return total

    @staticmethod
    def _normal_claim(text):
        return re.sub(r'\s+|[，。！？,.!?；;：:、]', '', text).lower()

    @staticmethod
    def _pending_critical_note(text):
        # Only whole, bounded avoidance instructions in production notes are exempt.
        # Keep all other clauses together: "商品功效，待核实" must still block.
        text = '；'.join(clause for clause in re.split(r'[，,。！？!?；;\n]', text)
                        if not _NOTE_PROHIBITION.fullmatch(clause.strip()))
        return (re.search(r'商品|事实|依据|功效|承重|价格|促销|权限|授权|复用|SKU|款式', text, re.I)
                and re.search(r'待核实|未核实|待确认|未确认|待验证|未验证|未经授权|未授权|未知|过期', text))

    def _current_fact_issues(self, content, current):
        used = {cid for row in content['rows'] for cid in row['claim_ids']}
        used.update(cid for row in content['rows'] for claim in row['factual_claims'] for cid in claim['fact_ids'])
        if not used:
            return []
        frozen = {claim['fact_id']: claim for claim in content['product_facts_snapshot'].get('claims', [])}
        applicable = {claim['fact_id']: claim for claim in current.get('claims', [])}
        issues = []
        if content['product_id'] != current.get('product_id', ''):
            issues.append('当前商品身份已变更，请核实所引用商品事实')
        if content['sku_id'] != current.get('sku_id', ''):
            issues.append('当前SKU款式已变更，请核实所引用商品事实')
        for cid in sorted(used):
            claim = applicable.get(cid)
            if not claim or not claim.get('confirmed') or not claim.get('source', '').strip():
                issues.append('引用的商品事实当前已撤销或缺少已确认依据：'+cid)
            elif claim.get('valid_until') and str(claim['valid_until'])[:10] < date.today().isoformat():
                issues.append('引用的商品事实当前已过期：'+cid)
            elif any(frozen.get(cid, {}).get(key) != claim.get(key) for key in ('statement', 'source', 'valid_until')):
                issues.append('引用的商品事实内容或依据当前已变更，请重新核实：'+cid)
        return issues

    def _content(self, p, data, source=None, previous=None):
        previous=previous or {}
        data={**{k:v for k,v in previous.items() if k in ScriptContent.model_fields},**data}
        content = ScriptContent.model_validate(data).model_dump(mode='json')
        if content.get('context_snapshot_id'):
            from .creative_context import CreativeContextService
            from .context_payload import provenance_summary
            service=CreativeContextService(self.store,index=getattr(self.store,'knowledge_index',None))
            ctx=service.snapshot(p,content['context_snapshot_id'])
            historical=ctx.get('archived_restored') or ctx.get('model_use_requires_revalidation')
            if not (historical and previous.get('context_snapshot_id')==content['context_snapshot_id'] and not content['confirmed']):
                ctx=service.revalidate_context(p,content['context_snapshot_id'])
            content['provenance_summary']=provenance_summary(ctx,content['rows'])
        else:
            content['provenance_summary']={}
        if not content['title'].strip() or not content['objective'].strip(): raise ValueError('标题和目标不能为空')
        facts=content['product_facts_snapshot']
        current=self.facts(p)
        if not facts and not previous and current.get('version',0)>0:
            facts=content['product_facts_snapshot']=deepcopy(current)
            content['product_id']=content['product_id'] or facts.get('product_id','')
            content['sku_id']=content['sku_id'] or facts.get('sku_id','')
        if facts:
            content['product_id']=content['product_id'] or facts.get('product_id','')
            content['sku_id']=content['sku_id'] or facts.get('sku_id','')
        if facts and content['sku_id'] and facts.get('sku_id') and content['sku_id']!=facts['sku_id']:raise ValueError('脚本SKU与商品事实款式不一致')
        if facts:
            versions=[current,*current.get('revisions',[])]
            if not any({k:v for k,v in f.items() if k!='revisions'}=={k:v for k,v in facts.items() if k!='revisions'} for f in versions):
                raise ValueError('商品事实快照与已保存版本不一致')
        claims={c.get('fact_id'):c for c in facts.get('claims',[]) if c.get('fact_id')}
        # Only saved server checks can identify stale generated issues; caller labels are untrusted.
        prior_generated=set(previous.get('validation_issues',[]))
        manual=[x for x in content['unresolved_items'] if x not in prior_generated]
        issues=[]
        for note in content['production_notes']:
            if self._pending_critical_note(note):issues.append('制作备注中仍有需要核实的事实或权限：'+note)
        previous_end = 0
        for row_index,row in enumerate(content['rows']):
            planned_duration = row['end_ms'] - row['start_ms']
            normal_ranges = {}
            special_duration = 0
            if self._pending_critical_note(row['notes']):issues.append('制作说明中仍有需要核实的事实或权限：'+row['notes'])
            if not row['own_references'] and not row['reshoot'].strip():issues.append(f'第{row_index+1}镜需要指定自有素材或补拍内容')
            if row['start_ms'] < previous_end: raise ValueError('脚本时间轴不能重叠')
            previous_end = row['end_ms']
            for own,key in [(False,'references'),(True,'own_references')]:
                for ref in row[key]:
                    asset = self.store.get(p,'asset',ref['asset_id'])
                    allowed=(source or {}).get('selection_snapshot',{}).get('own_assets',[]) if own else []
                    ids={a['id'] for a in allowed} if own else set((source or {}).get('scope',{}).get('asset_ids',[]))
                    if source and ref['asset_id'] not in ids: raise ValueError('素材引用超出来源范围')
                    start,end=ref.get('start_ms'),ref.get('end_ms')
                    ranges=[r for r in (source or {}).get('scope',{}).get('selected_segments',[]) if r['asset_id']==ref['asset_id']]
                    if not own and ranges and not any(start is not None and end is not None and r['start_ms']<=start<end<=r['end_ms'] for r in ranges):raise ValueError('引用超出本次所选片段范围')
                    if (start is None)!=(end is None): raise ValueError('参考时间范围不完整')
                    if asset.get('media_type')=='image' and (start is not None or end is not None):raise ValueError('图片使用整图引用，不能虚构原始时间段')
                    if end is not None and end>asset.get('metadata',{}).get('duration_ms',0): raise ValueError('参考片段时间越界')
                    if ref.get('scene_id') and self.store.get(p,'scene',ref['scene_id'])['asset_id']!=asset['id']: raise ValueError('镜头与素材不一致')
                    if own:
                        m=asset.get('metadata',{})
                        if asset.get('source_kind') not in {'own','raw','finished','account','pushed'}:raise ValueError('研究参考不能作为自有可用镜头')
                        if content['sku_id'] and m.get('sku_id')!=content['sku_id'] and not m.get('generic_footage'):raise ValueError('自有镜头的SKU款式与脚本不一致或尚未标明')
                        require_own_reuse(m)
                        path=asset.get('path') or asset.get('file_path')
                        if not path or not Path(path).is_file():raise ValueError('自有素材文件已丢失，请重新定位')
                        if asset.get('media_type') == 'image':
                            if ref['usage_mode'] != 'normal':raise ValueError('图片按整图展示，不使用视频片段的特殊用法')
                            special_duration += ref['use_duration_ms'] or planned_duration
                        else:
                            first = 0 if start is None else start
                            last = m.get('duration_ms', 0) if end is None else end
                            if type(last) is not int or last <= first:
                                raise ValueError('自有视频片段时长未知，请先核实有效源片段')
                            if ref['usage_mode'] == 'normal':
                                duration = ref['use_duration_ms'] or last - first
                                if duration > last - first:raise ValueError('正常速度的使用时长不能超过有效源片段时长')
                                normal_ranges.setdefault(asset['id'], []).append((first, first + duration))
                            else:
                                # Display planning never widens the source's evidence interval or rights.
                                special_duration += ref['use_duration_ms']
            if row['own_references']:
                available = self._normal_coverage(normal_ranges) + special_duration
                reshoot_duration = row['reshoot_duration_ms'] or 0
                if available + reshoot_duration < planned_duration:
                    issues.append(f'第{row_index+1}镜自有片段仅覆盖{available}毫秒，计划{planned_duration}毫秒；请明确剩余补拍时长或特殊使用方式')
            customer_text=row['dialogue']+' '+row.get('subtitle','')
            used=set(row['claim_ids'])
            for claim in row['factual_claims']:
                if not isinstance(claim,dict) or not isinstance(claim.get('statement'),str) or not isinstance(claim.get('fact_ids'),list):raise ValueError('商品说法依据格式无效')
                used.update(claim['fact_ids'])
                supported=[claims.get(cid,{}) for cid in claim['fact_ids']]
                exact_support = any(self._normal_claim(c.get('statement',''))==self._normal_claim(claim['statement']) for c in supported)
                if not exact_support and (not claim.get('reviewed',False) or _KEY_PRODUCT_CLAIM.search(claim['statement'])):
                    issues.append('商品说法仍需核实支持关系：'+claim['statement'])
            for cid in used:
                claim=claims.get(cid)
                if not claim or not claim.get('confirmed') or not claim.get('source','').strip():issues.append('商品说法缺少已确认依据：'+cid)
                elif claim.get('valid_until') and str(claim['valid_until'])[:10]<date.today().isoformat():issues.append('商品事实已过期：'+cid)
            numeric_pattern=r'(?:[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:元|公斤|kg|克|%|天见效|小时|毫升|ml))'
            source_text=' '.join(claims.get(cid,{}).get('statement','') for cid in used)
            missing_numbers=[x for x in re.findall(numeric_pattern,customer_text,re.I) if re.sub(r'\s+','',x).lower() not in re.sub(r'\s+','',source_text).lower()]
            if used and missing_numbers:issues.append('台词数值没有对应商品依据：'+'、'.join(missing_numbers))
            if re.search(r'(?:[¥￥]\s*\d|\d+(?:\.\d+)?\s*(?:元|公斤|kg|克|%|天见效|小时|毫升|ml))',customer_text,re.I) and not used:
                issues.append('台词中的数值或效果需要商品依据：'+customer_text)
            # A bounded guard for high-risk claims, not a semantic review of every possible claim.
            supported_statements = {self._normal_claim(claims[cid]['statement']) for cid in used if cid in claims}
            for text in (row['dialogue'], row['subtitle']):
                for statement in re.split(r'[。！？!?；;\n]', text):
                    if _KEY_PRODUCT_CLAIM.search(statement) and self._normal_claim(statement) not in supported_statements:
                        issues.append('关键商品说法缺少对应依据，仍需核实：'+statement.strip())
        if facts.get('promotion_valid_until') and str(facts['promotion_valid_until'])[:10]<date.today().isoformat() and any(re.search(r'买一送|限时|促销|到手价',r['dialogue']+' '+r.get('subtitle','')) for r in content['rows']):issues.append('促销有效期已过，请核实当前活动')
        if content['confirmed']:issues.extend(self._current_fact_issues(content,current))
        content['validation_issues']=list(dict.fromkeys(issues))
        content['unresolved_items']=list(dict.fromkeys(manual+issues))
        if content['confirmed'] and content['unresolved_items']:
            reason = ('未分类待处理项请先处理或明确归入制作备注：'+'；'.join(manual)+'；') if manual else ''
            raise ValueError('确认前请核实商品事实与依据：'+reason+'；'.join(issues))
        return content

    def create(self,p,data): return self._write(p,'script',self._content(p,data))
    def get(self,p,sid): return self.store.get(p,'script',sid)

    def revise(self,p,sid,expected_version,data):
        old=self.get(p,sid)
        return self._write(p,'script',self._content(p,{'confirmed':False,**data},old.get('source'),old),sid,expected_version)

    def from_session(self,p,sid,turn_index,script_index=0):
        session=self.store.get(p,'session',sid)
        if not 0<=turn_index<len(session['turns']): raise ValueError('分析轮次不存在')
        turn=session['turns'][turn_index]
        scripts=(turn['answer'].get('result') or {}).get('scripts') or []
        if not 0<=script_index<len(scripts):raise ValueError('此轮尚无完整脚本，请继续创作；脚本大纲不会被自动转换为成稿')
        identity='script-'+hashlib.sha256(json.dumps([p,sid,turn_index,script_index]).encode()).hexdigest()
        try:return self.get(p,identity)
        except NotFound:pass
        source={'session_id':sid,'turn_index':turn_index,'script_index':script_index,'answer_snapshot':deepcopy(turn['answer']),
                'evidence':deepcopy(turn['evidence']),'scope':deepcopy(turn.get('scope_snapshot',session['scope'])),
                'selection_snapshot':deepcopy(turn.get('selection_snapshot',session.get('selection_snapshot',{}))),
                'conclusion_version':turn.get('created_at')}
        body={**scripts[script_index],'confirmed':False,'product_facts_snapshot':turn.get('product_facts_snapshot',{}),
              'selection_snapshot':source['selection_snapshot']}
        content=self._content(p,body,source)
        return self.store.put_if_absent(p,'script',{**content,'source':source,'version':1,'revisions':[]},identity)

    def export(self,p,sid,format,version=None):
        from .script_exports import export_script
        return export_script(self.version(p,sid,version) if version else self.get(p,sid),format)

    def version(self,p,sid,version):
        script=self.get(p,sid)
        result=next((v for v in [script,*script.get('revisions',[])] if v['version']==version),None)
        if result is None: raise ValueError('脚本版本不存在')
        return {k:v for k,v in result.items() if k!='revisions'}

    def comment(self,p,sid,version,actor,text,*,row_index=None,verdict='comment'):
        snapshot=self.version(p,sid,version)
        if not text.strip() or len(text)>10000: raise ValueError('评论不能为空或过长')
        if row_index is not None and not 0<=row_index<len(snapshot['rows']): raise ValueError('脚本行不存在')
        if verdict not in {'comment','approved','changes_requested'}: raise ValueError('评审状态无效')
        return self.store.put(p,'script_comment',{'script_id': sid,'script_version': version,'row_index': row_index,'actor_id': actor,'text': text,'verdict': verdict})

    def produce(self,p,sid,version,idempotency_key,assignee):
        snapshot=self.version(p,sid,version)
        if not snapshot['confirmed']: raise ValueError('请先确认脚本版本')
        self._content(p,{k:v for k,v in snapshot.items() if k in ScriptContent.model_fields},snapshot.get('source'),snapshot)
        if not idempotency_key or len(idempotency_key)>180: raise ValueError('需要制作任务幂等标识')
        import hashlib
        task_id=hashlib.sha256(f'{p}:production:{idempotency_key}'.encode()).hexdigest()
        data={'script_id': sid,'script_version': version,'script_snapshot': snapshot,'assignee': assignee,'status': 'planned','finished_asset_id': None}
        try: return self._write(p,'production',data,task_id,0)
        except Conflict:
            existing=self.store.get(p,'production',task_id)
            if existing['script_id']!=sid or existing['script_version']!=version or existing['assignee']!=assignee: raise Conflict('制作幂等标识已用于不同请求')
            return existing

    def update_task(self,p,tid,expected_version,status,finished_asset_id=None):
        old=self.store.get(p,'production',tid)
        if old['version']!=expected_version: raise Conflict('制作任务已更新')
        transitions={'planned':{'in_progress','cancelled'},'in_progress':{'in_review','cancelled'},'in_review':{'approved','in_progress','cancelled'},'approved':set(),'cancelled':set()}
        if status not in transitions[old['status']]: raise ValueError('制作任务状态转换无效')
        aid=finished_asset_id or old.get('finished_asset_id')
        if aid:
            asset=self.store.get(p,'asset',aid)
            if asset.get('source_kind')!='finished': raise ValueError('需要本项目已归档的成片')
        if status in {'in_review','approved'} and not aid: raise ValueError('评审前请关联实际成片')
        return self._write(p,'production',{'status': status,'finished_asset_id': aid},tid,expected_version)

    def facts(self,p):
        self.store.project(p)
        try: return self.store.get(p,'product_facts','facts-'+p)
        except NotFound: return {'version': 0,'product': '','claims': [],'constraints': []}

    def save_facts(self,p,expected_version,data):
        facts=ProductFacts.model_validate(data).model_dump(mode='json')
        facts['product_id']=facts['product_id'] or 'product-'+p
        for claim in facts['claims']:
            claim['fact_id']=claim['fact_id'] or 'fact-'+hashlib.sha256(claim['statement'].encode()).hexdigest()[:16]
        if len({c['fact_id'] for c in facts['claims']})!=len(facts['claims']):raise ValueError('商品事实ID不能重复')
        return self._write(p,'product_facts',facts,'facts-'+p,expected_version)
