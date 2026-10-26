"""Task-driven outputs and immutable material manifests, independent of chat mode."""
from copy import deepcopy
from typing import Literal
import re
import math

from pydantic import Field
from .contracts import Contract


class TaskSpec(Contract):
    output_type: Literal['answer','analysis','comparison','directions','scripts'] = 'comparison'
    count: int = Field(default=1,ge=1,le=20)
    target_duration_ms: int | None = Field(default=None,gt=0,le=3600000)
    product_id: str = ''
    sku_id: str = ''
    own_asset_ids: list[str] = Field(default_factory=list,max_length=10000)
    max_generation_rounds: int = Field(default=1,ge=1,le=8)
    history_script_ids: list[str] = Field(default_factory=list,max_length=200)
    constraints: list[str] = Field(default_factory=list,max_length=100)

    @classmethod
    def resolve(cls, question, explicit=None, previous=None):
        """Resolve this turn's delivery independently from retained scope identity.

        ``infer`` remains available to legacy callers that only need creative
        deliverables. New executions always receive a task, including plain Q&A.
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError('请填写本轮问题')
        prior = cls.model_validate(previous) if previous is not None else None
        override = (explicit.model_dump(exclude_unset=True) if isinstance(explicit, cls)
                    else deepcopy(explicit) if explicit is not None else {})
        if not isinstance(override, dict):
            raise ValueError('任务要求格式无效')
        # Validate explicit fields before merging; do not silently drop unknowns.
        cls.model_validate(override)
        text = cls._request_text(question)
        inferred = cls.infer(question)
        comparison = bool(re.search(r'比较|对比|对照|差异|异同|哪个.{0,8}更', text))
        analysis = bool(re.search(r'分析|解释|拆解|为什么|原因|判断|评估|总结|提炼|复盘|怎么改|如何改|建议', text))
        output = inferred.output_type if inferred else 'comparison' if comparison else 'analysis' if analysis else 'answer'
        # "继续" is authorization to repeat the prior deliverable. A new
        # comparison/explanation is a new task even when it mentions scripts.
        repeat = bool(re.search(r'继续|接着|再写|再做|再来|重做|重写|再给|再生成|再.*一遍|补齐', text))
        prior_noun = r'脚本|分镜稿' if prior and prior.output_type == 'scripts' else r'方向|创意方案'
        denied = ((bool(re.search(prior_noun, question)) and not re.search(prior_noun, text))
                  or bool(re.search(r'(?:不要|不用|不需要|不必|无需|别|暂不)\s*(?:再|继续)?(?:重写|改写)', question)))
        continuing = bool(prior and repeat and not denied and not comparison and not analysis
                          and (inferred is None or inferred.output_type == prior.output_type))
        if continuing:
            output = prior.output_type
        output = override.get('output_type', output)
        values = {key: deepcopy(getattr(prior, key)) for key in (
            'product_id', 'sku_id', 'own_asset_ids', 'history_script_ids', 'constraints')
                  } if prior else {}
        values['output_type'] = output
        if inferred and output == inferred.output_type:
            values.update(inferred.model_dump(include={'count', 'target_duration_ms', 'max_generation_rounds'}))
        if continuing and output == prior.output_type:
            if not cls._quantity(text, output):
                values['count'] = prior.count
            if inferred is None or inferred.target_duration_ms is None:
                values['target_duration_ms'] = prior.target_duration_ms
        values.update(override)
        if output == 'scripts' and 'max_generation_rounds' not in override:
            values['max_generation_rounds'] = (prior.max_generation_rounds if continuing and not cls._quantity(text, output)
                                                else max(1, math.ceil(values.get('count', 1) / 3)))
        return cls.model_validate(values)

    @staticmethod
    def _request_text(question):
        # Remove only negated requests, not production constraints such as
        # "不需要补拍的脚本". Keep other clauses available for a positive task.
        quantity = r'(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:条|个|份|种|套)'
        production = r'(?:不需要|不用|无需|不必)(?:额外)?(?:补拍|素材|拍摄|演员|模特|道具)(?:的)?'
        modifier = r'(?:完整|不同|拍摄|创意|方向|口播|视频|任何|的|秒|\d+|' + production + r')\s*'
        negation = r'(?:暂时|这次|先)?(?:不要|不用|不需要|不必|无需|别|不想|暂不|不(?=写|生成|给|提供))'
        text = re.sub(negation + r'\s*(?:(?:再|继续|给我|给|写|生成|输出|提供|创作|要)\s*)*(?:' + quantity + r')?\s*(?:' + modifier + r')*(?:脚本|分镜稿|方向|创意方案)', '', question)
        return re.sub(negation + r'\s*(?:再|继续)?(?:重写|改写|比较|对比|分析|解释)', '', text)

    @staticmethod
    def _quantity(question, output):
        noun = r'(?:脚本|分镜稿)' if output == 'scripts' else r'(?:方向|创意方案)'
        return re.search(r'(\d+|[一二两三四五六七八九十]{1,3})\s*(?:条|个|份|种|套)\s*[^，。；！？,;!?]{0,30}?' + noun, question)

    @classmethod
    def infer(cls,question):
        if not isinstance(question,str):return None
        question = cls._request_text(question)
        # Asking how/why to make a deliverable refers to that activity; it is
        # not itself a request to generate the deliverable.
        question = re.sub(r'(?:为什么|为何|怎么|如何|怎样)(?:要|需要)?(?:给我|给|写|生成|输出|提供|创作|产出)[^，。；！？,;!?\n]*?(?:脚本|分镜稿|方向|创意方案)', '', question)
        quantity=r'(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:条|个|份|种|套)'
        production=r'(?:不需要|不用|无需|不必)(?:额外)?(?:补拍|素材|拍摄|演员|模特|道具)(?:的)?'
        modifier=r'(?:完整|不同|拍摄|创意|方向|口播|视频|任何|这个|当前|已有|原来|上述|手工|的|秒|\d+|'+production+r')\s*'
        request=r'(?:给我|给|写|生成|输出|提供|创作|产出|要|改写|重写|修改|调整|优化|缩短|延长|润色)\s*(?:'+quantity+r')?\s*(?:'+modifier+r')*'
        scripts=bool(re.search(request+r'(?:脚本|分镜稿)',question))
        directions=bool(re.search(request+r'(?:方向|创意方案)',question))
        if not (scripts or directions):return None
        count=1
        words={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'零':0}
        def number(token):
            if token.isdigit():return int(token)
            if '十' in token:
                tens,ones=token.split('十',1)
                return words.get(tens,1)*10+words.get(ones,0)
            return words[token]
        deliverable=r'(?:脚本|分镜稿)' if scripts else r'(?:方向|创意方案)'
        match=re.search(r'(\d+|[一二两三四五六七八九十]{1,3})\s*(?:条|个|份|种|套)\s*(?:'+modifier+r')*'+deliverable,question)
        if match:count=number(match[1])
        duration=(re.search(r'(\d+)\s*秒(?:钟)?\s*(?:的|完整|拍摄|口播|视频|\s)*(?:脚本|分镜稿)',question)
                  or re.search(r'(?:每条|每个|单条|单个|总长|全长|目标时长|时长)[^\d]{0,6}(\d+)\s*秒',question)
                  or re.search(r'(?:脚本|分镜稿)[，,、\s:：]*(\d+)\s*秒',question))
        return cls(output_type='scripts' if scripts else 'directions',count=count,max_generation_rounds=max(1,math.ceil(count/3)) if scripts else 1,target_duration_ms=int(duration[1])*1000 if duration else None)


def freeze_selection(store,p,scope,spec):
    if scope.get('project_id',p)!=p:raise ValueError('选材项目范围不一致')
    def manifest(ids):
        items=[]
        for aid in dict.fromkeys(ids):
            a=store.get(p,'asset',aid)
            items.append({'id':aid,'sha256':a.get('sha256'),'updated_at':a.get('updated_at'),
                          'metadata':deepcopy(a.get('metadata',{})),'source_kind':a.get('source_kind'),'parsed_record_id':a.get('parsed_record_id')})
        return items
    history=[]
    for sid in dict.fromkeys(spec.history_script_ids if spec else []):
        script=store.get(p,'script',sid)
        if spec.sku_id and script.get('sku_id')!=spec.sku_id:raise ValueError('历史脚本SKU与本次商品不一致')
        if spec.product_id and script.get('product_id')!=spec.product_id:raise ValueError('历史脚本商品与本次任务不一致')
        history.append({k:deepcopy(script.get(k)) for k in ('id','version','title','objective','product_id','sku_id','usage_status','feedback_reason')} | {'opening':script.get('rows',[{}])[0].get('dialogue','')})
    return {'script_history':history,'project_id':p,'scope':deepcopy(scope),'assets':manifest(scope.get('asset_ids',[])),
            'own_assets':manifest(spec.own_asset_ids if spec else []),'version':1}


def validate_delivery(result,spec):
    """Retain actual partial artifacts; never pad a missing count with placeholders."""
    from .scripts import ScriptContent
    if result is None and spec.output_type in {'answer', 'analysis'}:return {}
    if not isinstance(result,dict):raise ValueError('创作结果必须是结构化对象')
    result=deepcopy(result)
    if spec.output_type=='scripts' or 'scripts' in result:
        scripts=result.get('scripts',[])
        if not isinstance(scripts,list) or (spec.output_type=='scripts' and len(scripts)>spec.count):raise ValueError('脚本数量与本次任务不一致')
        result['scripts']=[ScriptContent.model_validate(s).model_dump(mode='json') for s in scripts]
        for script in result['scripts']:
            if any(not (r['dialogue'].strip() or r.get('subtitle','').strip()) or not r['visual'].strip() for r in script['rows']):
                raise ValueError('完整脚本需要实际台词或字幕，以及对应画面；大纲不能代替')
    if spec.output_type=='scripts':
        count=len(result['scripts'])
    elif spec.output_type in {'answer', 'analysis'}:
        return result
    elif spec.output_type=='directions':
        items=result.get('directions',[])
        if not isinstance(items,list) or any(not isinstance(x,str) or not x.strip() for x in items):raise ValueError('创作方向格式无效')
        count=len(items)
    else:
        items=result.get('comparisons',[])
        if not isinstance(items,list):raise ValueError('比较结果格式无效')
        count=1 if items or result.get('recommendations') else 0
    duration_issues=[]
    if spec.output_type=='scripts' and spec.target_duration_ms:
        tolerance=max(1000,round(spec.target_duration_ms*0.1))
        for i,script in enumerate(result['scripts']):
            actual=script['rows'][-1]['end_ms']
            if abs(actual-spec.target_duration_ms)>tolerance:
                duration_issues.append(f'第{i+1}个脚本计划时长{actual/1000:g}秒，与目标{spec.target_duration_ms/1000:g}秒不符')
    if duration_issues:result['duration_issues']=duration_issues
    result['delivery_progress']={'requested':spec.count,'completed':count,'state':'complete' if count==spec.count and not duration_issues else 'partial'}
    return result


def model_selection(snapshot):
    return {k:deepcopy(snapshot[k]) for k in ('project_id','scope','version') if k in snapshot} | {kind:[{k:deepcopy(row[k]) for k in ('id','sha256','updated_at','source_kind') if k in row} for row in snapshot.get(kind,[])] for kind in ('assets','own_assets')}
