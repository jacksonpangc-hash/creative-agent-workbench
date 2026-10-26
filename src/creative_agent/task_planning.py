"""Deterministic task previews. Planning never contacts a model or data service."""
import hashlib
import json

from .creative_tasks import TaskSpec


def is_material_inspection(question):
    import re
    return bool(re.search(r'(我的|当前项目|已上传|上传的|导入的).{0,8}素材', question)
                and re.search(r'检查|查询|查看|导入成功|处理状态|识别状态|上传成功', question)
                and not re.search(r'写.{0,6}脚本|比较|创作方向|分析.{0,6}开头|卖点表达', question))


def request_fingerprint(request):
    value = {k:v for k,v in request.items() if k != 'plan_id'}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def execution_configuration_revision(store, project_id):
    configuration={'models':sorted(store.list(project_id,'model'),key=lambda row:row['id']),
                   'preferences':store.list(project_id,'preferences')}
    return hashlib.sha256(json.dumps(configuration,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def plan_request(store, project_id, request, assets=None):
    store.project(project_id)
    scope = request.get('scope') or {}
    if scope.get('project_id',project_id) != project_id: raise ValueError('分析项目范围不一致')
    question = request.get('question','').strip()
    if not question: raise ValueError('请填写这次想做什么')
    local = is_material_inspection(question)
    spec = TaskSpec.resolve(question, explicit=request.get('task_spec'))
    if request.get('kind')=='followup':
        session=store.get(project_id,'session',request.get('session_id',''))
        inherited=(session.get('turns') or [{}])[-1].get('task_spec')
        spec=TaskSpec.resolve(question, explicit=request.get('task_spec'),previous=inherited)
    if request.get('script_revision'):
        spec=spec.model_copy(update={'output_type':'scripts','count':1,'max_generation_rounds':1})
    intent = ('inspect_assets' if local else 'revise_script' if request.get('script_revision') else
              'write_script' if spec and spec.output_type == 'scripts' else
              'explore' if spec and spec.output_type == 'directions' else 'analyze')
    rows = assets if assets is not None else [store.get(project_id,'asset',aid) for aid in scope.get('asset_ids',request.get('asset_ids',[]))]
    ranges=scope.get('selected_segments',[])
    observations=0 if request.get('parse_context') or request.get('script_revision') or request.get('kind')=='followup' or request.get('analysis_kind')=='spoken' or request.get('discussion_only') else sum(max(1,sum(r.get('asset_id')==a['id'] for r in ranges)) for a in rows)
    if request.get('parse_context'):observations=sum(row.get('media_type')=='image' for row in rows)
    return {'intent':intent,'output_type':'status' if local else spec.output_type if spec else 'answer',
            'local_only':local,'count':spec.count if spec else 1,
            'max_model_requests':0 if local else observations+(spec.max_generation_rounds if spec else 1),
            'configuration_revision':None if local else execution_configuration_revision(store,project_id),
            'project_id':project_id,'request_fingerprint':request_fingerprint(request),
            'material_versions':[{'id':a['id'],'sha256':a.get('sha256'),'updated_at':a.get('updated_at')} for a in rows],
            'pricing_status':'local' if local else 'unknown',
            'sources':['current_project'],'external_discovery':False}
