from __future__ import annotations

import hmac
import os
import re
import secrets
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, UploadFile, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import Field

from .agent import AnalysisAgent, Instructions, Reports
from .contracts import Contract, ModelBinding, AnalysisScope, CostQuote, GenerationRequest, provider_catalog
from .media import MediaService, MetricImporter, SearchService
from .providers import ProviderClient, OutputLimitError, ProviderAccountError
from .model_catalog import model_availability, require_model
from .secrets import SecretStore
from .store import Store, Conflict, NotFound, export_safe as _export_safe, uid


def export_safe(value):
    return _export_safe(value, reset_credentials=False)
from .workflows import Workflows
from .briefs import Briefs, BriefCreate, BriefRevision
from .creative_tasks import TaskSpec,freeze_selection
from .scripts import Scripts
from .model_receipts import latest_model_use
from .evidence_policy import transcript_policy
from .material_readiness import readiness_for_asset, readiness_context
from .task_planning import plan_request, request_fingerprint, is_material_inspection, execution_configuration_revision


class ProjectInput(Contract):
    name: str
    budget: str | None = None
    currency: Literal['CNY', 'USD'] = 'CNY'


class BindingInput(ModelBinding):
    api_key: str = Field(default='', max_length=8192)


class ScriptRevisionContext(Contract):
    script_id: str = Field(min_length=1)
    version: int = Field(ge=1)


class ParseContext(Contract):
    parse_id:str=Field(min_length=1)
    version:int=Field(ge=1)


class Action(Contract):
    kind: Literal['analysis', 'followup', 'parse', 'embedding', 'scene_embedding', 'search'] = 'analysis'
    binding_id: str = ''
    analysis_kind: Literal['video', 'spoken'] = 'video'
    discussion_only: bool = False
    question: str = Field(default='', max_length=20000)
    mode: Literal['directed', 'exploration', 'inspiration'] = 'directed'
    asset_ids: list[str] = Field(default_factory=list, max_length=200)
    groups: list[dict] = Field(default_factory=list, max_length=20)
    fields: list[str] = Field(default_factory=list, max_length=40)
    session_id: str = ''
    native_video: bool = False
    video_route: dict | None = None
    quote: str | None = None
    task_intent: Literal['script', 'video', 'references'] | None = None
    script_revision: ScriptRevisionContext | None = None
    parse_context: ParseContext | None = None
    idempotency_key: str = Field(min_length=1, max_length=180)
    scope: AnalysisScope | None = None
    source_session_id: str | None = None
    source_turn_index: int = Field(default=0, ge=0)
    history_script_ids: list[str] = Field(default_factory=list,max_length=200)
    own_asset_ids: list[str] = Field(default_factory=list,max_length=10000)
    task_spec: TaskSpec | None = None
    selection_snapshot: dict | None = None
    template_id: str | None = None
    template_snapshot: dict | None = None
    plan_id: str | None = None


class MediaProcessInput(Contract):
    idempotency_key: str = Field(min_length=1,max_length=140)


class TranscriptionInput(Contract):
    asset_ids: list[str] = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=180)


class GenerationInput(Contract):
    request: GenerationRequest
    binding_id: str
    strategy_binding_id: str | None = None
    quote: str
    idempotency_key: str = Field(min_length=1, max_length=180)


class DownloadInput(Contract):
    url: str
    rights_confirmed: bool = False
    title: str = ''
    source_kind: Literal['own', 'inspiration', 'raw', 'finished', 'account', 'pushed', 'industry'] = 'inspiration'


class NoteInput(Contract):
    session_id: str
    title: str = '策略报告'
    turn_index: int = -1
    finding_indexes: list[int] | None = None


class EditInput(Contract):
    markdown: str = Field(max_length=500000)


class InstructionInput(Contract):
    title: str
    template: str
    variables: list[str] = Field(default_factory=list)
    favorite: bool = False
    scene: str = '通用'
    job: str = '策略'
    models: list[str] = Field(default_factory=list, max_length=40)


def create_app(data_dir, *, token=None, secret_store=None, run_jobs=True, static_dir=None, port=8765, store_override=None, security_profile="local", external_guard=None, license_runtime=None):
    if security_profile not in {'local', 'team'}: raise ValueError('未知安全配置')
    if security_profile == 'team' and store_override is None: raise ValueError('团队模式需要独立共享存储')
    from .workspace_lease import WorkspaceLease
    lease = WorkspaceLease(Path(data_dir).resolve()) if security_profile == 'local' and run_jobs else None
    if lease: lease.acquire()
    try:
        app = _create_app(data_dir, token=token, secret_store=secret_store, run_jobs=run_jobs,
            static_dir=static_dir, port=port, store_override=store_override, security_profile=security_profile,
            external_guard=external_guard, license_runtime=license_runtime, lease=lease)
    except BaseException:
        if lease: lease.release()
        raise
    if lease:
        import weakref
        weakref.finalize(app, lease.release)
    return app


def _create_app(data_dir, *, token, secret_store, run_jobs, static_dir, port, store_override, security_profile, external_guard, license_runtime, lease):
    root = Path(data_dir).resolve(); root.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt': root.chmod(0o700)
    store = store_override if store_override is not None else Store(root / 'creative.sqlite3')
    media = MediaService(store, root / 'media')
    workflow = Workflows(store, media)
    agent = AnalysisAgent(store, media)
    vault = secret_store or SecretStore()
    from .commercial_license import runtime_from_config,LicenseError
    commercial = license_runtime or runtime_from_config(root,vault,mode='internal')
    original_external_guard=external_guard
    def external_guard():
        commercial.require_work()
        if original_external_guard:original_external_guard()
    bearer = token or secrets.token_urlsafe(32)
    from .media_streams import MediaTicketRegistry
    media_tickets=MediaTicketRegistry()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='creative-worker')
    media_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='creative-media')
    from .knowledge_index import KnowledgeIndex
    from .creative_context import CreativeContextService
    index_stop=threading.Event()
    knowledge=KnowledgeIndex(store,media)
    store.knowledge_index=knowledge
    def allowed_candidate(p,kind,record):
        licensing.check(p,record,kind,model=True)
        return True
    knowledge.candidate_filter=allowed_candidate
    agent.context_service=CreativeContextService(store,media,index=knowledge,
        authorize=lambda p,intent:commercial.require_work('workspace.use'),candidate_filter=allowed_candidate)
    def index_loop():
        while not index_stop.is_set():
            try:
                commercial.require_work('assets.analyze')
                knowledge.process_pending(limit=10)
            except Exception:
                # Persistent index jobs retain concrete errors; no cloud fallback.
                pass
            index_stop.wait(2)
    index_thread=threading.Thread(target=index_loop,name='creative-local-index',daemon=True)


    @asynccontextmanager
    async def lifespan(app):
        if lease: lease.acquire()
        try:
            commercial.start()
            if security_profile=='local' and run_jobs:
                from .local_model_assets import install_bundled_embeddings
                install_bundled_embeddings(root)
                knowledge.recover()
                index_thread.start()
            if security_profile == "local":
                store.recover()
                if run_jobs:
                    for project in store.projects():
                        for job in store.jobs(project['id']):
                            if job['kind'] in {'media_ingest','public_reference_media','transcription'} and job['state'] == 'queued': media_executor.submit(execute,project['id'],job['id'])
            yield
        finally:
            index_stop.set()
            if index_thread.is_alive():index_thread.join()
            commercial.stop()
            executor.shutdown(wait=True, cancel_futures=True)
            media_executor.shutdown(wait=True, cancel_futures=True)
            if lease: lease.release()

    app = FastAPI(title='Creative Agent Local API', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.token = store, bearer
    app.state.media = media
    app.state.knowledge = knowledge
    app.state.security_profile=security_profile
    app.state.license_runtime=commercial
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    allowed_origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}', 'tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost'}
    if os.environ.get('CREATIVE_DEV') == '1': allowed_origins.add('http://127.0.0.1:5173')

    @app.middleware('http')
    async def protect(request: Request, call_next):
        origin = request.headers.get('origin')
        from .discovery_api import discovery_extension_request
        extension_request=security_profile=='local' and discovery_extension_request(request)
        def refusal(message, status):
            headers = {'Cache-Control':'no-store'}
            if security_profile == 'local' and origin in allowed_origins:
                headers.update({'Access-Control-Allow-Origin':origin,'Vary':'Origin'})
            return JSONResponse({'detail':message},status_code=status,headers=headers)
        if security_profile == 'team':
            if getattr(request.state, 'team_authorized', False) is not True:
                return refusal('团队身份未验证', 401)
        else:
            if request.headers.get('host') not in allowed_hosts:
                return refusal('禁止非本机主机访问', 403)
            origin = request.headers.get('origin')
            if origin and origin not in allowed_origins and not extension_request:
                return refusal('禁止跨来源访问', 403)
            if request.method == 'OPTIONS' and origin in allowed_origins:
                return Response(status_code=204, headers={'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
                    'Access-Control-Allow-Headers': 'Authorization,Content-Type', 'Vary': 'Origin'})
            if request.url.path == '/api/team/config':
                return refusal('本地运行模式', 404)
            stream_request=request.method in {'GET','HEAD'} and bool(re.fullmatch(r'/api/media-streams/[A-Za-z0-9_-]+',request.url.path))
            if request.url.path.startswith('/api/') and not stream_request:
                if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + bearer):
                    return refusal('此会话未授权，请从应用启动链接进入', 401)
        path = request.url.path
        from .browser_bridge_api import browser_bridge_request
        if request.method not in {'GET','HEAD','OPTIONS'} and not path.startswith('/api/license/') and not path.endswith('/cancel') and not browser_bridge_request(request):
            try:
                feature=('discovery.public' if '/discovery/' in path or extension_request else
                    'discovery.paid' if '/cmm/' in path and not path.endswith('/connection') else
                    'providers.configure' if '/models' in path or path.endswith('/cmm/connection') or path.endswith('/preferences') else
                    'product.manage' if '/product-facts' in path else
                    'script.generate' if '/scripts' in path else
                    'assets.import' if '/assets' in path else 'workspace.use')
                commercial.require_work(feature)
            except LicenseError as exc:return refusal(str(exc),403)
        if security_profile == 'team' and '/cmm' in path.split('/projects/',1)[-1]:
            return refusal('此版本蝉妈妈仅支持个人本地工作空间；团队凭证与任务接入待验证',501)
        maximum = 10*1024**3+1048576 if path=='/api/backups/restore' else media.MAX_FILE_BYTES + 1048576 if path.endswith('/assets') else 21000000 if path.endswith('/reports/import') else 2000000
        try:
            if int(request.headers.get('content-length', '0')) > maximum:
                return refusal('请求内容超过大小限制', 413)
        except ValueError:
            return refusal('请求长度无效', 400)
        received = 0
        original_receive = request._receive
        async def limited_receive():
            nonlocal received
            message = await original_receive()
            received += len(message.get('body', b''))
            if received > maximum: raise HTTPException(413, '请求内容超过大小限制')
            return message
        request._receive = limited_receive
        try:
            response = await call_next(request)
        except Exception:
            if received > maximum:
                return refusal('请求内容超过大小限制', 413)
            raise
        if received > maximum:
            return refusal('请求内容超过大小限制', 413)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data: https:; media-src 'self' blob: https:; connect-src 'self' http://127.0.0.1:* ipc: http://ipc.localhost; frame-ancestors 'none'; object-src 'none'"
        if origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': [{'location': e['loc'], 'message': e['msg']} for e in exc.errors()]}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        from .user_errors import describe
        return JSONResponse(describe(exc), status_code=404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400)

    def model(p, bid, snapshot=None,required_features=(),job_id=None,max_requests=None):
        if not snapshot: raise ValueError('旧任务缺少不可变模型快照，不能自动调用；请在供应商控制台核对')
        saved = snapshot
        if saved.get('project_id') != p: raise ValueError('模型快照项目范围不一致')
        binding = ModelBinding.model_validate({k:v for k,v in saved.items() if k in ModelBinding.model_fields})
        calls=0
        def before_model_request():
            nonlocal calls
            if job_id and store.job(p,job_id)['state'] not in {'running','submitting'}:raise Conflict('任务已停止，不再发送新请求')
            if max_requests is not None and calls>=max_requests:raise ValueError('已达到本次模型请求次数上限')
            external_guard()
            for feature in required_features:commercial.require_work(feature)
            calls+=1
            if job_id:store.event(p,job_id,'model_request_attempt',{'sequence':calls,'state':'may_be_sent','binding_id':bid})
        return ProviderClient(binding, vault.get(saved.get('secret_ref') or f'{p}:{bid}'), before_request=before_model_request,on_receipt=(lambda receipt:store.event(p,job_id,'model_response_receipt',receipt)) if job_id else None)

    def action_features(p,body):
        features=[{'parse':'video.breakdown','analysis':'creative.generate','followup':'creative.generate','embedding':'assets.analyze','scene_embedding':'assets.analyze','search':'assets.analyze'}[body.kind]]
        previous=store.get(p,'session',body.session_id)['turns'][-1].get('task_spec') if body.kind=='followup' else None
        spec=TaskSpec.resolve(body.question,explicit=body.task_spec,previous=previous) if body.kind in {'analysis','followup'} else None
        if spec and spec.output_type=='scripts':features.append('script.generate')
        return features

    def preflight_action(p, body, config):
        for feature in action_features(p,body):commercial.require_work(feature)
        licensing.check(p, body.model_dump(mode='json'), model=True)
        if body.scope and body.scope.project_id != p: raise ValueError('分析项目范围不一致')
        ids = body.scope.asset_ids if body.scope else body.asset_ids
        if body.kind == 'followup':
            ids = store.get(p, 'session', body.session_id)['scope']['asset_ids']
        app.state.industry.validate_model_use(p, asset_ids=ids)
        reference_ids = (store.get(p, 'session', body.session_id)['scope'].get('reference_ids', []) if body.kind == 'followup' else body.scope.reference_ids if body.scope else [])
        if reference_ids: app.state.cmm.validated_evidence(p, reference_ids, actions=['retain_metadata', 'model_process'])
        source_context = None
        if body.source_session_id:
            source = store.get(p, 'session', body.source_session_id)
            if body.kind != 'parse' or body.source_turn_index >= len(source['turns']): raise ValueError('解析来源轮次无效')
            turn = source['turns'][body.source_turn_index]
            if set(body.asset_ids) - {e['asset_id'] for e in turn['evidence'] if e.get('asset_id')}: raise ValueError('解析素材超出来源分析范围')
            source_context = dict(session_id=source['id'], turn_index=body.source_turn_index, answer_snapshot=turn['answer'], scope=source['scope'])
        if body.parse_context:
            from .parse_continuity import prepare_parse_continuation
            reuse=prepare_parse_continuation(store,licensing,p,body.parse_context.parse_id,body.parse_context.version,ids,[r.model_dump(mode='json') for r in body.scope.selected_segments])
            images=[media.file(p,aid) for aid in reuse.get('reobserve_asset_ids',[])]
            ProviderClient.validate_chat(config,has_images=bool(images))
            if images:ProviderClient.validate_chat_files(config,images=images)
        elif body.script_revision:
            ProviderClient.validate_chat(config)
        elif body.kind == 'analysis' and (body.analysis_kind == 'spoken' or body.discussion_only):
            if not body.discussion_only:
                for aid in ids:
                    asset = store.get(p, 'asset', aid)
                    if not asset.get('metadata', {}).get('transcript', '').strip() and (asset.get('media_type') != 'video' or asset.get('inspection') != 'ready'):
                        raise ValueError('请先完成视频处理，或为图片提供文字稿')
            ProviderClient.validate_chat(config)
        elif body.kind in {'analysis', 'parse'}:
            ids = body.scope.asset_ids if body.kind == 'analysis' and body.scope else body.asset_ids
            for aid in ids:
                asset = store.get(p, 'asset', aid)
                path = media.file(p, aid)
                video = asset['media_type'] == 'video'
                if asset.get('inspection') != 'ready': raise ValueError('素材还在处理中，请等就绪后再分析')
                if video and body.video_route is not None:
                    from .video_understanding import VideoUnderstanding
                    intervals=[{'start_ms':r.start_ms,'end_ms':r.end_ms} for r in body.scope.selected_segments if r.asset_id==aid] if body.scope else []
                    for interval in intervals or [None]:
                        prepared = VideoUnderstanding(store, media).prepare(p, aid, config, plan=body.video_route,interval=interval)
                        ProviderClient.validate_chat(config, has_images=bool(prepared['images']), has_video=bool(prepared['video']))
                        ProviderClient.validate_chat_files(config, images=prepared['images'], video=prepared['video'])
                    continue
                ProviderClient.validate_chat(config, has_images=not video, has_video=video)
                if video and not body.native_video:
                    raise ValueError('旧视频任务使用抽帧设置，请重新选择视频分析模型并确认新任务')
                ProviderClient.validate_chat_files(config, images=[] if video else [path], video=path if video else None)
        elif body.kind == 'followup':
            ProviderClient.validate_chat(config)
        else:
            require_model(config.model_dump(), 'embedding')
        return source_context

    def execute(p, job_id):
        job = store.job(p, job_id)
        if job['state']=='queued':
            try:commercial.require_work({'media_ingest':'assets.analyze','public_reference_media':'discovery.public','transcription':'assets.analyze','analysis':'creative.generate','followup':'creative.generate','parse':'video.breakdown','embedding':'assets.analyze','scene_embedding':'assets.analyze','search':'assets.analyze','industry_search':'discovery.paid','industry_import':'discovery.paid'}.get(job['kind']))
            except LicenseError as exc:
                store.transition(p,job_id,'failed',error=str(exc));store.settle(p,job_id,'0');return
        if job['kind'] == 'industry_search':
            return app.state.industry.run(p, job_id)
        if job['kind'] == 'public_reference_media':
            return app.state.discovery_media.run(p, job_id)
        if job['kind'] == 'industry_import':
            if job['state'] != 'queued': return
            try:
                from .industry import SelectionInput
                store.transition(p, job_id, 'running')
                refs = app.state.industry.select(p,job['payload']['source_job_id'],SelectionInput(**job['payload']['selection']))
                store.finish(p,job_id,result={'reference_ids':[r['id'] for r in refs]},actual_cost='0')
            except Exception:
                if store.job(p,job_id)['state']=='running': store.transition(p,job_id,'failed',error='行业媒体导入未完成，请核对下载授权、链接和任务记录；不会自动重新查询供应商')
            return
        if job['kind'] == 'media_ingest':
            if job['state'] != 'queued': return
            try:
                store.transition(p, job_id, 'running')
                result = media.process_upload(p, job['payload']['asset_id'])
                store.finish(p, job_id, result={'asset_id': result['id']}, actual_cost='0')
                if result.get('media_type') == 'video' and not transcript_policy(result)['allowed']:
                    speech_job = store.create_job(p,'transcription',{'asset_ids':[result['id']]},
                        CostQuote(amount='0',currency=store.project(p)['currency'],basis='随导入执行的本地口播识别，无外部接口费用'),
                        'automatic-speech:'+result['id']+':'+(result.get('sha256') or 'legacy'))
                    dispatch(p,speech_job)
            except Exception:
                if store.job(p, job_id)['state'] == 'running':
                    store.transition(p, job_id, 'failed', error='文件已保存，但媒体处理未完成。可在素材详情重试。')
            return
        if job['kind'] == 'transcription':
            if job['state'] != 'queued': return
            try:
                licensing.check(p, job, 'job', model=True)
                store.transition(p, job_id, 'running')
                from .transcription import Transcriber
                result = Transcriber(store, media).run(p, job['payload']['asset_ids'],cancelled=lambda:store.job(p,job_id)['state']!='running')
                if result.get('state') == 'needs_attention':
                    store.finish(p, job_id, actual_cost='0', result={'record_id': result['id'], 'kind': 'transcription', 'errors': result.get('errors', [])}, error='部分素材未能识别口播，可查看记录并重试失败项')
                else:
                    store.finish(p, job_id, result={'record_id': result['id'], 'kind': 'transcription'}, actual_cost='0')
            except Exception:
                if store.job(p, job_id)['state'] in {'queued','running'}:
                    store.transition(p, job_id, 'failed', error='本地转写未完成，请核对本地模型、音轨及素材权限')
            return
        if job['kind'] == 'generation':
            if job['state'] == 'queued':
                store.transition(p, job_id, 'cancelled', error='视频生成功能已取消，未提交的旧任务不再执行')
                store.settle(p, job_id, '0')
            elif job['state'] in {'submitted', 'cancelling'} and job.get('provider_request_id'):
                licensing.check(p, job, 'job')
                workflow.resume_generation(p, job_id, model(p, job['payload']['binding_id'], job['payload'].get('binding_snapshot')))
            return
        if job['state'] != 'queued': return
        try:
            store.transition(p, job_id, 'running')
            data = Action(**{k:v for k,v in job['payload'].items() if k in Action.model_fields})
            client = model(p, data.binding_id, job['payload'].get('binding_snapshot'),required_features=action_features(p,data),job_id=job_id,max_requests=job['payload'].get('max_model_requests'))
            scope = data.scope or AnalysisScope(project_id=p, asset_ids=data.asset_ids)
            preflight_action(p, data, client.binding)
            external_evidence = []
            if data.kind == 'analysis' and not data.discussion_only and not data.parse_context and not data.script_revision:
                if scope.reference_ids:
                    external_evidence = app.state.cmm.validated_evidence(p, scope.reference_ids, actions=['retain_metadata','model_process'])
                missing = [aid for aid in scope.asset_ids if store.get(p, 'asset', aid).get('media_type') == 'video' and not store.get(p, 'asset', aid).get('metadata', {}).get('transcript', '').strip()]
                if missing:
                    from .transcription import Transcriber
                    result = Transcriber(store, media).run(p, missing,cancelled=lambda:store.job(p,job_id)['state']!='running')
                    if result.get('errors'):
                        # An audio-free source can still be analyzed visually, with the gap recorded.
                        for issue in result['errors']:
                            asset = store.get(p, 'asset', issue['asset_id'])
                            asset['metadata']['audio_processing_gap'] = issue['error']
                            store.put(p, 'asset', asset, asset['id'])
                        if data.analysis_kind == 'spoken':
                            raise ValueError('部分素材无法提取口播，请在素材详情核对音轨或补充字幕')
                if data.analysis_kind == 'spoken': agent.spoken_evidence([store.get(p, 'asset', aid) for aid in scope.asset_ids])
            if data.parse_context:
                from .parse_continuity import prepare_parse_continuation
                fresh=prepare_parse_continuation(store,licensing,p,data.parse_context.parse_id,data.parse_context.version,scope.asset_ids,[r.model_dump(mode='json') for r in scope.selected_segments])
                if fresh!=job['payload'].get('parse_context_snapshot'):raise ValueError('拆解依据已变化，请重新选择后继续创作')
            # Local processing may take minutes; check cancellation and current rights again.
            if store.job(p, job_id)['state'] != 'running': return
            preflight_action(p, data, client.binding)
            store.transition(p, job_id, 'submitting')
            if data.kind == 'analysis':
                result = agent.analyze(scope, data.mode, data.question, client, groups=data.groups, native_video=data.native_video, analysis_kind=data.analysis_kind, discussion_only=data.discussion_only, video_route=data.video_route, external_evidence=external_evidence,cached_parse=job['payload'].get('parse_context_snapshot'),script_context=job['payload'].get('script_revision_snapshot'),task_spec=data.task_spec,selection_snapshot=data.selection_snapshot,on_checkpoint=lambda sid:store.checkpoint_result(p,job_id,{'record_id':sid,'kind':'session'}))
            elif data.kind == 'followup':
                result = agent.followup(p, data.session_id, data.question, client,script_context=job['payload'].get('script_revision_snapshot'),task_spec=data.task_spec,on_checkpoint=lambda sid:store.checkpoint_result(p,job_id,{'record_id':sid,'kind':'session'}))
            elif data.kind == 'parse':
                result = workflow.parse(p, data.asset_ids, data.fields, client, native_video=data.native_video, source_context=job['payload'].get('source_context'), video_route=data.video_route,template_snapshot=data.template_snapshot)
            elif data.kind == 'scene_embedding':
                from .semantic_clips import SemanticClips
                result = SemanticClips(store).index(p, data.asset_ids, client)
            elif data.kind == 'embedding':
                result = index_embeddings(p, data.asset_ids, client)
            else:
                vector = client.embed(data.question)
                found = SearchService(store).search(scope, data.question, 'semantic', vector)
                found = [a for a in found if a.get('embedding_model') == client.binding.model_id and a.get('embedding_provider') == client.binding.provider]
                result = store.put(p, 'search', {'query': data.question, 'asset_ids': [a['id'] for a in found], 'scores': [a['search_score'] for a in found]})
            from .model_receipts import record_model_success
            for call in getattr(client, 'successful_calls', []):
                record_model_success(store, p, job['payload']['binding_snapshot'], call)
            partial=data.kind in {'analysis','followup'} and result['turns'][result.get('_worker_turn_index',-1)]['answer'].get('result',{}).get('delivery_progress',{}).get('state')=='partial'
            result_pointer={'record_id':result['id'],'kind':'session' if data.kind in {'analysis','followup'} else data.kind}
            if partial:
                store.checkpoint_result(p,job_id,result_pointer)
                store.transition(p,job_id,'needs_attention',error='已保存部分成果，可在原对话继续完成剩余数量或调整时长')
            else:store.finish(p,job_id,result=result_pointer)
        except Conflict:
            stopped=store.job(p,job_id)
            if stopped['state']=='cancelling' and not stopped.get('provider_request_id'):
                store.transition(p,job_id,'cancelled',error='已停止后续请求；已保存成果可查看，已提交调用费用待核账')
            return
        except Exception as exc:
            from .user_errors import describe
            diagnostic=describe(exc)
            store.event(p,job_id,'diagnostic',{'diagnostic_id':diagnostic['diagnostic_id'],'exception_type':type(exc).__name__,'reason':str(exc)[:800] if isinstance(exc,ValueError) else '后台处理未完成，请检查本机运行环境','retryable':False})
            state = store.job(p, job_id)['state']
            if state == 'cancelling' and not store.job(p,job_id).get('provider_request_id'):
                store.transition(p,job_id,'cancelled',error='已停止后续请求；本次在途调用未返回有效结果，已保存成果可查看，费用待核账')
            elif state == 'running':
                store.transition(p, job_id, 'failed', error=('执行前检查未通过，尚未调用模型：' + str(exc)[:300]) if isinstance(exc, ValueError) else '执行前检查未通过，尚未调用模型；请检查素材、授权与模型设置')
                store.settle(p, job_id, '0')
            elif state == 'submitting':
                store.transition(p, job_id, 'needs_attention', error=str(exc) if isinstance(exc, (OutputLimitError, ProviderAccountError)) else '分析或解析未完成，可能有部分调用已计费；请核对模型配置、权限与任务记录')

    app.state.execute = execute
    app.state.vault = vault
    app.state.workflow = workflow

    def dispatch(p, job):
        if run_jobs and job['state'] == 'queued': (media_executor if job['kind'] in {'media_ingest','public_reference_media','transcription'} else executor).submit(execute, p, job['id'])
        return export_safe(job)

    def index_embeddings(p, aids, client):
        for aid in aids:
            asset = store.get(p, 'asset', aid)
            text = asset['title'] + '\n' + asset['metadata'].get('transcript', '') + '\n' + asset.get('description', '') + '\n' + asset.get('parsed_text', '')
            vector = client.embed(text)
            asset.update(embedding=vector, embedding_model=client.binding.model_id, embedding_provider=client.binding.provider)
            store.put(p, 'asset', asset, aid)
        return store.put(p, 'embedding', {'asset_ids': aids, 'model_id': client.binding.model_id})

    @app.get('/api/catalog')
    def catalog():
        return {'providers': provider_catalog(), 'data_channels': [{'id': 'cmm', 'name': '蝉妈妈正式 API', 'live_verified': False}, {'id': 'qianchuan', 'name': '千川报表 CSV 导入', 'live_verified': False}]}

    @app.get('/api/projects')
    def projects(): return store.projects()

    @app.post('/api/projects')
    def project_create(body: ProjectInput): return store.create_project(body.name, body.budget, body.currency)

    @app.put('/api/projects/{p}')
    def project_update(p: str, body: ProjectInput): return store.update_project(p, **body.model_dump(exclude_unset=True))

    @app.get('/api/projects/{p}/state')
    def state(p: str):
        readiness = readiness_context(store,p)
        return export_safe(dict(project=store.project(p), budget=store.budget(p), assets=[{**a,'transcript_policy':transcript_policy(a),'readiness':readiness_for_asset(store,p,a,readiness)} for a in licensing.visible(p, store.list(p, 'asset'), 'asset')],
            scripts=licensing.visible(p,store.list(p,'script'),'script'), product_facts=Scripts(store).facts(p), models=[{**m, 'availability': model_availability(m), 'recent_use':latest_model_use(store,p,m)} for m in store.list(p, 'model')], sessions=licensing.visible(p, store.list(p, 'session'), 'session'), notes=licensing.visible(p, store.list(p, 'note'), 'note'), instructions=store.list(p,'instruction'),
            parses=licensing.visible(p, store.list(p, 'parse'), 'parse'), templates=store.list(p, 'template'), briefs=licensing.visible(p, store.list(p, 'brief'), 'brief'), jobs=[licensing.job_view(p, j) for j in [*store.jobs(p), *store.list(p, 'migration_job')]], groups=licensing.visible(p, store.list(p, 'group'), 'group'), favorites=[a['job_id'] for a in licensing.visible(p, store.list(p,'job_annotation')) if a.get('favorite')]))

    from .material_views import MaterialViews
    def material_views(): return MaterialViews(store,licensing)

    @app.get('/api/projects/{p}/activity')
    def activity(p:str): return material_views().activity(p)

    @app.get('/api/projects/{p}/outcomes')
    def outcomes(p:str): return material_views().outcomes(p)

    @app.get('/api/projects/{p}/assets')
    def list_assets(p:str,offset:int=0,limit:int=50,query:str='',source:str=''):
        return material_views().assets(p,offset,limit,query,source)

    @app.get('/api/projects/{p}/assets/{aid}')
    def asset_detail(p:str,aid:str): return material_views().asset(p,aid)

    @app.get('/api/projects/{p}/assets/{aid}/analysis-results')
    def asset_results(p:str,aid:str): return material_views().analysis_results(p,aid)

    from .pattern_library import PatternLibrary
    pattern_library=None
    @app.get('/api/projects/{p}/patterns')
    def patterns(p:str,query:str='',market:str='',category:str='',offset:int=0,limit:int=20):
        nonlocal pattern_library
        store.project(p)
        if pattern_library is None: pattern_library=PatternLibrary()
        return pattern_library.search(query,market,category,offset,limit)

    @app.post('/api/projects/{p}/briefs')
    def create_brief(p: str, body: BriefCreate):
        licensing.check(p, {'session_id': body.session_id})
        return export_safe(Briefs(store).create(p, **body.model_dump()))

    @app.get('/api/projects/{p}/briefs')
    def list_briefs(p: str): return export_safe(licensing.visible(p, store.list(p, 'brief'), 'brief'))

    @app.get('/api/projects/{p}/briefs/{bid}')
    def get_brief(p: str, bid: str): return export_safe(licensing.check(p, store.get(p, 'brief', bid), 'brief'))

    @app.put('/api/projects/{p}/briefs/{bid}')
    def revise_brief(p: str, bid: str, body: BriefRevision):
        licensing.check(p, store.get(p, 'brief', bid), 'brief')
        licensing.check(p, body.model_dump(mode='json'))
        return export_safe(Briefs(store).revise(p, bid, **body.model_dump(exclude_unset=True)))

    @app.get('/api/projects/{p}/export')
    def project_export(p: str):
        app.state.industry.validate_export(p)
        for kind, records in store.export_project(p)['records'].items():
            for record in records: licensing.check(p, record, kind, export=True)
        return store.export_project(p)

    @app.post('/api/projects/{p}/models')
    def save_model(p: str, body: BindingInput):
        store.project(p)
        data = body.model_dump(mode='json', exclude={'api_key'})
        if body.role in {'video_generation','image_generation'}: raise HTTPException(410, '此版本专注创意分析，已取消视频与图片生成配置')
        require_model(data, body.role)
        record_id = body.id or None
        old = store.get(p, 'model', record_id) if record_id else None
        if old and old['provider'] != body.provider and not body.api_key:
            raise ValueError('更换厂商需要填写新厂商的 API 密钥')
        if not old and not body.api_key:
            donors = [m for m in store.list(p, 'model') if m.get('provider') == body.provider and m.get('has_key')
                      and m.get('base_url') == body.base_url]
            if not donors: raise ValueError('请先配置该厂商及接口地址的 API 密钥')
            donor = max(donors, key=lambda m: m['updated_at'])
            try: body.api_key = vault.get(donor.get('secret_ref') or f"{p}:{donor['id']}") or ''
            except Exception: raise ValueError('系统密钥库读取失败') from None
            if not body.api_key: raise ValueError('厂商凭证不可用，请重新配置')
        secret_ref = f'{p}:{uid()}' if body.api_key else (old.get('secret_ref') or f'{p}:{record_id}' if old else None)
        try:
            if body.api_key: vault.set(secret_ref, body.api_key)
        except Exception:
            raise ValueError('系统密钥库写入失败，模型设置未保存；不会使用明文回退') from None
        try:
            saved = store.put(p, 'model', {**data, 'secret_ref': secret_ref, 'has_key': bool(body.api_key) or bool(old and old.get('has_key'))}, record_id)
        except Exception:
            if body.api_key: vault.delete(secret_ref)
            raise
        from .model_receipts import latest_model_use
        try: prefs = store.get(p,'preferences','preferences-'+p)
        except NotFound: prefs = {}
        if body.role in {'strategy','video_input','image_input'} and not prefs.get('default_binding_id'):
            candidates=[m for m in store.list(p,'model') if m.get('enabled',True) and m.get('role') in {'strategy','image_input','video_input'}]
            if len(candidates)==1:
                Scripts(store)._write(p,'preferences',{**prefs,'default_binding_id':saved['id']},'preferences-'+p,prefs.get('version',0))
        return export_safe({**saved, 'availability': model_availability(saved),'recent_use':latest_model_use(store,p,saved)})

    @app.delete('/api/projects/{p}/models/{bid}')
    def delete_model(p: str, bid: str):
        saved = store.get(p, 'model', bid)
        jobs = store.jobs(p)
        if any(j['state'] not in {'succeeded','failed','cancelled'} and (j['payload'].get('binding_id') == bid or j['payload'].get('strategy_binding_id') == bid) for j in jobs):
            raise Conflict('仍有任务依赖此模型，请先处理任务；避免旧任务失去原始凭证')
        refs = {saved.get('secret_ref') or f'{p}:{bid}'}
        for j in jobs:
            for name in ('binding_snapshot', 'strategy_snapshot'):
                snap = j['payload'].get(name) or {}
                if snap.get('id') == bid and snap.get('secret_ref'): refs.add(snap['secret_ref'])
        for ref in refs: vault.delete(ref)
        store.delete(p, 'model', bid)
        return {'deleted': True}

    @app.post('/api/projects/{p}/assets')
    def upload(p: str, file: UploadFile, background: bool = False, source_kind: Literal['own', 'inspiration', 'raw', 'finished', 'account', 'pushed', 'industry'] = 'own'):
        store.project(p)
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in media.IMAGE_EXT | media.VIDEO_EXT: raise ValueError('不支持的媒体文件')
        tmp = None
        try:
            if background:
                asset = media.stage_upload_stream(p, file.file, file.filename or '', source_kind=source_kind)
                if asset.get('inspection') == 'ready': return export_safe(asset)
                job = store.create_job(p, 'media_ingest', {'asset_id': asset['id']},
                    CostQuote(amount='0', currency=store.project(p)['currency'], basis='本地媒体处理'), 'media-ingest:' + asset['id'])
                return {**export_safe(asset), 'processing_job': dispatch(p, job)}
            with tempfile.NamedTemporaryFile(dir=root, suffix=suffix, delete=False) as output:
                tmp = Path(output.name); total = 0
                while chunk := file.file.read(65536):
                    total += len(chunk)
                    if total > media.MAX_FILE_BYTES: raise ValueError('单文件超过1GB')
                    output.write(chunk)
            return export_safe(media.ingest(p, tmp, title=Path(file.filename).name, source_kind=source_kind))
        finally:
            file.file.close()
            if tmp: tmp.unlink(missing_ok=True)

    @app.post('/api/projects/{p}/assets/{aid}/process')
    def retry_media(p: str, aid: str, body: MediaProcessInput):
        asset = store.get(p,'asset',aid)
        licensing.check(p,asset,'asset')
        key = 'media-retry:' + body.idempotency_key
        for old in store.jobs(p):
            if old['idempotency_key'] == key:
                if old['payload'].get('asset_id') != aid: raise Conflict('重试编号已用于其他素材')
                return export_safe(old)
        if asset.get('inspection') != 'failed': raise Conflict('只有处理失败的素材需要重试')
        return dispatch(p,store.create_job(p,'media_ingest',{'asset_id':aid},
            CostQuote(amount='0',currency=store.project(p)['currency'],basis='本地媒体处理'),key))

    @app.post('/api/projects/{p}/download')
    def download(p: str, body: DownloadInput):
        return export_safe(media.download(p, body.url, rights_confirmed=body.rights_confirmed, title=body.title, source_kind=body.source_kind))

    @app.post('/api/projects/{p}/media-ticket/{aid}')
    def media_ticket(p:str,aid:str):
        if security_profile!='local':return {'streaming':False}
        asset=licensing.check(p,store.get(p,'asset',aid),'asset')
        ticket=media_tickets.issue(p,aid,asset['sha256'])
        return {'streaming':True,'path':'/api/media-streams/'+ticket,'expires_in_seconds':900}

    @app.api_route('/api/media-streams/{ticket}',methods=['GET','HEAD'])
    def media_stream(ticket:str):
        if security_profile!='local':raise ValueError('此媒体入口只支持本机播放')
        identity=media_tickets.resolve(ticket)
        asset=licensing.check(identity.project_id,store.get(identity.project_id,'asset',identity.asset_id),'asset')
        if asset.get('sha256')!=identity.sha256:raise Conflict('素材版本已改变，请重新打开素材')
        return FileResponse(media.file(identity.project_id,identity.asset_id))

    @app.get('/api/projects/{p}/assets/{aid}/file')
    def file(p: str, aid: str, poster: bool = False):
        licensing.check(p, store.get(p, 'asset', aid), 'asset')
        return FileResponse(media.file(p, aid, poster=poster))

    @app.get('/api/projects/{p}/assets/{aid}/scenes')
    def scenes(p: str, aid: str):
        licensing.check(p, store.get(p, 'asset', aid), 'asset')
        return export_safe(media.scenes(p, aid))

    @app.get('/api/projects/{p}/assets/{aid}/frames')
    def frame_index(p: str, aid: str):
        asset = store.get(p, 'asset', aid)
        licensing.check(p, asset, 'asset')
        import json
        return json.loads(Path(asset['metadata']['frame_index_path']).read_text()) if asset['media_type'] == 'video' else []

    @app.get('/api/projects/{p}/assets/{aid}/scene/{sid}')
    def scene_poster(p: str, aid: str, sid: str):
        licensing.check(p, store.get(p, 'asset', aid), 'asset')
        scene = store.get(p, 'scene', sid)
        if scene['asset_id'] != aid: raise NotFound('镜头不存在')
        path = Path(scene['poster_path']).resolve()
        if not path.is_relative_to(media.root / p): raise ValueError('镜头路径无效')
        return FileResponse(path)

    @app.post('/api/projects/{p}/assets/{aid}/subtitles')
    async def subtitles(p: str, aid: str, request: Request):
        licensing.check(p, store.get(p, 'asset', aid), 'asset')
        data = await request.json()
        from .evidence_policy import transcript_policy
        before=store.get(p,'asset',aid)
        expected=data.get('expected_transcript_revision')
        if expected and expected!=transcript_policy(before)['transcript_revision']:raise Conflict('台词已被修改，请重新打开后再保存')
        result=media.add_subtitles(p, aid, str(data.get('text', '')))
        if data.get('reviewed') is True:
            provenance={**result.get('metadata',{}).get('transcription',{}),'method':'user_corrected','reviewed':True,'review_state':'corrected','execution_state':'succeeded','quality_state':'usable','warnings':[]}
            result['metadata']['transcription']=provenance
            result=store.put(p,'asset',result,aid)
        return export_safe(result)

    @app.patch('/api/projects/{p}/assets/{aid}')
    async def asset_edit(p: str, aid: str, request: Request):
        body = await request.json(); asset = store.get(p, 'asset', aid)
        licensing.check(p, asset, 'asset')
        if set(body) - {'title', 'tags', 'description', 'metadata', 'lifecycle', 'favorite'}: raise ValueError('不可更改此素材字段')
        if 'metadata' in body:
            allowed = {'platform_id', 'account_id', 'published_at', 'rights_note','product_id','sku_id','generic_footage','reuse_allowed'}
            if not isinstance(body['metadata'], dict) or set(body['metadata']) - allowed: raise ValueError('元数据字段无效')
            body['metadata'] = {**asset['metadata'], **body['metadata']}
        return export_safe(store.put(p, 'asset', {**asset, **body}, aid))

    @app.post('/api/projects/{p}/reports/import')
    async def import_report(p: str, request: Request):
        text = (await request.body()).decode('utf-8-sig')
        if len(text) > 20000000: raise ValueError('报表超过20MB')
        return MetricImporter(store).import_csv(p, text)

    @app.post('/api/projects/{p}/search')
    async def search(p: str, request: Request):
        body = await request.json(); scope = AnalysisScope(**{**body.get('scope', {}), 'project_id': p})
        method = body.get('method', 'exact'); query = str(body.get('query', ''))
        vector = None
        if method == 'semantic':
            raise ValueError('语义检索需要先创建检索任务并确认向量调用费用；可用已完成索引的向量接口')
        return export_safe(licensing.visible(p, SearchService(store).search(scope, query, method), 'asset'))

    @app.post('/api/projects/{p}/transcriptions')
    def transcriptions(p: str, body: TranscriptionInput):
        from .transcription import Transcriber
        Transcriber.model_path()
        for aid in body.asset_ids:
            licensing.check(p, store.get(p, 'asset', aid), kind='asset', model=True)
            if store.get(p, 'asset', aid)['media_type'] != 'video': raise ValueError('请选择视频素材')
        quote = CostQuote(amount='0', currency=store.project(p)['currency'], basis='本地转写，无外部接口费用；使用本机计算资源')
        job = store.create_job(p, 'transcription', body.model_dump(), quote, body.idempotency_key)
        return dispatch(p, job)

    @app.post('/api/projects/{p}/actions/preview')
    def action_preview(p: str, body: Action):
        if body.kind not in {'analysis','followup'}:raise ValueError('此操作请使用对应的素材处理流程')
        if is_material_inspection(body.question):
            assets=licensing.visible(p,store.list(p,'asset'),'asset')
        elif body.kind=='followup':
            session=store.get(p,'session',body.session_id)
            licensing.check(p,session,'session',model=True)
            assets=[] if session.get('discussion_only') else agent.collect(AnalysisScope.model_validate(session['scope']))
        elif body.discussion_only or (body.task_intent=='script' and not body.asset_ids and not body.scope):
            assets=[]
        else:assets=agent.collect(body.scope or AnalysisScope(project_id=p,asset_ids=body.asset_ids))
        plan = plan_request(store,p,body.model_dump(mode='json'),assets)
        return export_safe(store.put(p,'task_plan',plan))

    @app.post('/api/projects/{p}/actions')
    def action(p: str, body: Action):
        import hashlib, json
        raw_request=body.model_dump(mode='json')
        legacy_hash=hashlib.sha256(json.dumps(raw_request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        request_hash=request_fingerprint(raw_request)
        for old in store.jobs(p):
            if old['idempotency_key'] == body.idempotency_key:
                if old['payload'].get('_action_request_hash') not in {request_hash,legacy_hash}:
                    raise Conflict('该次操作已经提交，请勿用相同标识提交不同内容')
                licensing.check(p,old,'job')
                return export_safe(old)
        if body.plan_id:
            plan = store.get(p,'task_plan',body.plan_id)
            if plan['request_fingerprint'] != request_fingerprint(body.model_dump(mode='json')):
                raise Conflict('这次任务与预览不同，请重新确认')
            if not plan['local_only'] and plan.get('configuration_revision') != execution_configuration_revision(store,p):
                raise Conflict('模型或付款账户配置已经更新，请重新确认')
            for version in plan['material_versions']:
                asset = store.get(p,'asset',version['id'])
                if any(asset.get(k) != version.get(k) for k in ('sha256','updated_at')):
                    raise Conflict('素材已经更新，请重新确认这次任务')
        if body.kind in {'analysis','followup'} and is_material_inspection(body.question):
            if body.scope and body.scope.project_id != p: raise ValueError('分析项目范围不一致')
            identifier = 'local-status-' + hashlib.sha256((p+body.idempotency_key).encode()).hexdigest()
            try:
                saved = store.get(p,'session',identifier)
                if saved.get('request_hash') != request_hash: raise Conflict('该次操作已经提交，请勿用相同标识提交不同内容')
            except NotFound:
                assets = licensing.visible(p,store.list(p,'asset'),'asset')
                readiness = readiness_context(store,p)
                lines = [f"当前项目有 {len(assets)} 条素材。"]
                lines.extend(f"- {a['title']}：{readiness_for_asset(store,p,a,readiness)['summary']}" for a in assets[:20])
                if len(assets)>20: lines.append('其余素材可在我的素材中查看。')
                if not assets: lines.append('可以先添加一条视频，再查看台词和画面。')
                saved = store.put_if_absent(p,'session',{'title':body.question[:60], 'mode':'directed',
                    'analysis_kind':'spoken','discussion_only':True,'scope':{'project_id':p,'asset_ids':[]},
                    'local_only':True,'request_hash':request_hash,'turns':[{'question':body.question,
                    'answer':{'summary':lines[0],'findings':[],'result':{'local_status':[{'asset_id':a['id'],'title':a['title'],'summary':readiness_for_asset(store,p,a,readiness)['summary']} for a in assets[:20]]}},'evidence':[]}]},identifier)
            return {'id':identifier,'session_id':saved['id'],'local_only':True,'state':'succeeded'}
        parse_snapshot=None
        if body.parse_context:
            if body.kind!='analysis' or body.script_revision:raise ValueError('请从拆解结果新建创作')
            from .parse_continuity import prepare_parse_continuation
            chosen=body.scope or AnalysisScope(project_id=p,asset_ids=body.asset_ids)
            if chosen.project_id!=p:raise ValueError('分析项目范围不一致')
            parse_snapshot=prepare_parse_continuation(store,licensing,p,body.parse_context.parse_id,body.parse_context.version,chosen.asset_ids,[r.model_dump(mode='json') for r in chosen.selected_segments])
            body.scope=AnalysisScope.model_validate(parse_snapshot['scope'])
            body.asset_ids=body.scope.asset_ids
            body.discussion_only=False
        revision_snapshot = None
        if body.script_revision:
            if body.kind not in {'analysis','followup'}: raise ValueError('请从已有脚本继续修改')
            record = Scripts(store).get(p,body.script_revision.script_id)
            licensing.check(p,record,'script',model=True)
            revision_snapshot = Scripts(store).version(p,record['id'],body.script_revision.version)
            current_facts=Scripts(store).facts(p)
            if any(revision_snapshot.get(key) and current_facts.get(key) and revision_snapshot[key]!=current_facts[key] for key in ('product_id','sku_id')):raise ValueError('这份脚本属于另一款商品，请先切回原商品资料再修改')
            if body.kind=='followup' and (revision_snapshot.get('source') or {}).get('session_id') != body.session_id:
                raise ValueError('脚本与这次创作记录不对应')
            if body.kind=='analysis':
                source=revision_snapshot.get('source') or {}
                refs=[ref for row in revision_snapshot.get('rows',[]) for ref in row.get('references',[]) if ref.get('asset_id')]
                own=[ref['asset_id'] for row in revision_snapshot.get('rows',[]) for ref in row.get('own_references',[]) if ref.get('asset_id')]
                snapshot_scope=source.get('scope') or {'project_id':p,'asset_ids':list(dict.fromkeys(ref['asset_id'] for ref in refs)), 'selected_segments':[{'asset_id':ref['asset_id'],'start_ms':ref['start_ms'],'end_ms':ref['end_ms']} for ref in refs if ref.get('start_ms') is not None and ref.get('end_ms') is not None]}
                body.scope=AnalysisScope.model_validate({**snapshot_scope,'project_id':p,'group_ids':[],'discovery':False})
                body.asset_ids=body.scope.asset_ids
                body.own_asset_ids=list(dict.fromkeys(own))
                body.history_script_ids=[]
                body.discussion_only=not bool(body.scope.asset_ids or body.scope.reference_ids)
                body.analysis_kind='spoken'
            body.task_spec=TaskSpec.resolve(body.question,explicit=body.task_spec).model_copy(update={'output_type':'scripts','count':1,'max_generation_rounds':1,'product_id':revision_snapshot.get('product_id',''),'sku_id':revision_snapshot.get('sku_id','')})
        if body.kind == 'analysis' and body.task_intent:
            body.mode = {'script':'inspiration','video':'directed','references':'exploration'}[body.task_intent]
            if body.task_intent == 'script':
                body.task_spec = body.task_spec or TaskSpec.infer(body.question)
                chosen = body.scope or AnalysisScope(project_id=p,asset_ids=body.asset_ids)
                if not (chosen.asset_ids or chosen.reference_ids or chosen.group_ids or chosen.discovery):
                    body.discussion_only = True
        if body.scope and body.scope.project_id != p: raise ValueError('分析项目范围不一致')
        # A local inventory reply has no model evidence/context to continue.
        # Start a separate, explicitly empty-scope discussion and keep the local
        # record intact; never treat the project's inventory as analyzed media.
        local_status_origin_id = None
        if body.kind == 'followup':
            prior = store.get(p, 'session', body.session_id)
            licensing.check(p, prior, 'session', model=True)
            if prior.get('local_only'):
                if body.script_revision: raise ValueError('本地素材检查没有可供修改的脚本')
                local_status_origin_id = prior['id']
                body = body.model_copy(update={'kind':'analysis', 'session_id':'',
                    'mode':'directed', 'analysis_kind':'spoken', 'discussion_only':True,
                    'scope':AnalysisScope(project_id=p,asset_ids=[]), 'asset_ids':[],
                    'groups':[], 'source_session_id':None, 'own_asset_ids':[],
                    'history_script_ids':[], 'selection_snapshot':None})
        if (body.own_asset_ids or body.history_script_ids) and body.kind!='analysis':raise ValueError('变更自有检索范围需要新建分析任务')
        if body.kind == 'analysis':
            chosen = body.scope or AnalysisScope(project_id=p, asset_ids=body.asset_ids)
            if body.discussion_only and (chosen.asset_ids or chosen.reference_ids or chosen.group_ids or chosen.discovery): raise ValueError('无素材讨论不能同时选择素材范围')
            assets = [] if body.discussion_only else agent.collect(chosen)
            if not body.question.strip(): raise ValueError('请填写分析问题')
            ids = {a['id'] for a in assets}
            if any(set(g.get('asset_ids', [])) - ids for g in body.groups): raise ValueError('分组包含范围外素材')
            body.scope = chosen.model_copy(update={'asset_ids': [a['id'] for a in assets], 'group_ids': [], 'discovery': False, 'query': ''})
            body.task_spec=TaskSpec.resolve(body.question,explicit=body.task_spec)
            if body.own_asset_ids:
                body.task_spec=(body.task_spec or TaskSpec()).model_copy(update={'own_asset_ids':body.own_asset_ids})
            if body.history_script_ids:
                body.task_spec=(body.task_spec or TaskSpec()).model_copy(update={'history_script_ids':body.history_script_ids})
            if body.task_spec:
                for sid in body.task_spec.history_script_ids:licensing.check(p,store.get(p,'script',sid),'script',model=True)
                licensing.check(p,{'asset_ids':body.task_spec.own_asset_ids},model=True)
                facts=Scripts(store).facts(p)
                body.task_spec=body.task_spec.model_copy(update={'product_id':body.task_spec.product_id or facts.get('product_id',''),'sku_id':body.task_spec.sku_id or facts.get('sku_id','')})
                if body.task_spec.sku_id and facts.get('sku_id') and body.task_spec.sku_id!=facts['sku_id']:raise ValueError('任务SKU与当前商品款式不一致')
            body.selection_snapshot={**freeze_selection(store,p,body.scope.model_dump(mode='json'),body.task_spec),'product_facts_snapshot':(revision_snapshot or {}).get('product_facts_snapshot') or Scripts(store).facts(p)}
        elif body.kind == 'followup':
            prior_session = store.get(p, 'session', body.session_id)
            body.analysis_kind = prior_session.get('analysis_kind', 'video')
            body.discussion_only = prior_session.get('discussion_only', False)
            body.task_spec=TaskSpec.resolve(body.question,explicit=body.task_spec,previous=prior_session['turns'][-1].get('task_spec'))
            if not body.question.strip(): raise ValueError('请填写追问')
        elif body.kind == 'search':
            if not body.question.strip(): raise ValueError('语义搜索需要向量模型与查询文本')
        else:
            if not body.asset_ids or (body.kind == 'parse' and len(body.asset_ids) > 100): raise ValueError('请选择有效的素材范围')
            for aid in body.asset_ids: store.get(p, 'asset', aid)
        if body.kind=='parse':
            body.template_snapshot=store.get(p,'template',body.template_id) if body.template_id else None
            if body.template_snapshot:body.fields=body.template_snapshot['fields']
        search_assets = []
        if body.kind == 'search':
            search_scope = body.scope or AnalysisScope(project_id=p, asset_ids=body.asset_ids)
            search_assets = licensing.visible(p, SearchService(store).search(search_scope), 'asset')
        def has_search_index(candidate):
            import math
            for asset in search_assets:
                vector = asset.get('embedding')
                if (asset.get('embedding_model') == candidate['model_id'] and asset.get('embedding_provider') == candidate['provider']
                    and isinstance(vector, list) and vector and any(vector)
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector)):
                    return True
            return False
        if not body.binding_id:
            try: preferred = store.get(p, 'preferences', 'preferences-'+p).get('default_binding_id')
            except NotFound: preferred = ''
            if preferred:
                try: store.get(p,'model',preferred)
                except NotFound: raise ValueError('此前默认模型已不可用，请在设置中选择服务')
                body.binding_id = preferred
        if not body.binding_id:
            ids = body.scope.asset_ids if body.kind == 'analysis' and body.scope else body.asset_ids
            kinds = {store.get(p, 'asset', aid)['media_type'] for aid in ids}
            purposes = (['image_input'] if (parse_snapshot or {}).get('reobserve_asset_ids') else ['strategy'] if body.kind == 'followup' or body.parse_context or body.script_revision or (body.kind == 'analysis' and (body.analysis_kind == 'spoken' or body.discussion_only)) else
                        ([k for k, kind in [('video_analysis', 'video'), ('image_input', 'image')] if kind in kinds] or ['strategy']))
            if body.kind in {'search', 'embedding', 'scene_embedding'}: purposes = ['embedding']
            compatible = [m for m in store.list(p, 'model') if m.get('has_key') and purposes and
                          all(model_availability(m).get(k, {}).get('available', False) for k in purposes)]
            if body.kind == 'search':
                compatible = [m for m in compatible if has_search_index(m)]
                if not compatible: raise ValueError('当前范围没有与可用向量模型匹配的有效索引，请先建立索引')
            if not compatible: raise ValueError('没有已配置且兼容当前分析方式的模型，请先设置服务')
            body.binding_id = sorted(compatible, key=lambda m: (m['created_at'], m['id']))[0]['id']
        binding = store.get(p, 'model', body.binding_id)
        if body.kind == 'search':
            if binding['role'] != 'embedding': raise ValueError('语义搜索需要向量模型与查询文本')
            if not has_search_index(binding):
                raise ValueError('请先用同一个向量模型建立素材索引')
        if not binding.get('has_key'): raise ValueError('所选模型尚未设置密钥')
        if body.kind in {'analysis','followup','parse'} and binding['role'] not in {'strategy','image_input','video_input'}: raise ValueError('此任务需要多模态分析用途的模型')
        if body.kind in {'embedding', 'scene_embedding'} and binding['role'] != 'embedding': raise ValueError('索引需要向量用途的模型')
        config = ModelBinding.model_validate({k:v for k,v in binding.items() if k in ModelBinding.model_fields})
        if body.kind == 'parse' and (not body.fields or len(set(body.fields)) != len(body.fields) or any(not f.strip() for f in body.fields)):
            raise ValueError('请提供不同且非空的解析字段')
        if body.kind in {'analysis', 'parse'}:
            ids = body.scope.asset_ids if body.kind == 'analysis' else body.asset_ids
            # Media type decides the input route. An image-only model must never
            # acquire video eligibility through a client-controlled frames flag.
            video_task = not body.parse_context and not body.script_revision and not (body.kind == 'analysis' and body.analysis_kind == 'spoken') and any(store.get(p, 'asset', aid)['media_type'] == 'video' for aid in ids)
            if video_task:
                from .video_understanding import plan_video_route
                body.video_route = plan_video_route(config)
                body.native_video = body.video_route['route'] == 'native_video'
            else:
                body.video_route = None
                body.native_video = False
        source_context = preflight_action(p, body, config)
        rounds=body.task_spec.max_generation_rounds if body.task_spec else 1
        if body.kind=='analysis':
            observation_count=sum(max(1,sum(r.asset_id==aid for r in body.scope.selected_segments)) for aid in body.scope.asset_ids) if body.analysis_kind=='video' and not body.discussion_only and not body.script_revision and not body.parse_context else 0
            max_requests=observation_count+len((parse_snapshot or {}).get('reobserve_asset_ids',[]))+rounds
        elif body.kind=='followup':max_requests=rounds
        elif body.kind=='scene_embedding':max_requests=sum(s['asset_id'] in body.asset_ids for s in store.list(p,'scene'))
        elif body.kind=='parse':max_requests=sum(max(1,sum(r['asset_id']==aid for r in (source_context or {}).get('scope',{}).get('selected_segments',[]))) for aid in body.asset_ids)
        elif body.kind=='embedding':max_requests=len(body.asset_ids)
        else:max_requests=1
        from .action_budget import plan_reservation
        if body.plan_id and max_requests > plan['max_model_requests']:
            raise Conflict('这次处理需要的调用次数已变化，请重新确认')
        quote = plan_reservation(store,p,binding,body.model_dump(mode='json'),max_requests)
        job = store.create_job(p, body.kind, {**body.model_dump(mode='json'), '_action_request_hash':request_hash, 'script_revision_snapshot':revision_snapshot, 'parse_context_snapshot':parse_snapshot, 'max_model_requests':max(1,max_requests), 'binding_snapshot': binding, 'source_context': source_context, **({'local_status_origin_id':local_status_origin_id} if local_status_origin_id else {})}, quote, body.idempotency_key)
        return dispatch(p, job)

    @app.post('/api/projects/{p}/generation')
    def generation(p: str, body: GenerationInput):
        store.project(p)
        raise HTTPException(410, '视频生成已取消；已提交的历史任务仍可查询与核账')

    @app.get('/api/projects/{p}/search-results/{rid}')
    def search_result(p: str, rid: str):
        result = store.get(p, 'search', rid)
        return export_safe(licensing.visible(p, [{**store.get(p, 'asset', aid), 'search_score': score} for aid, score in zip(result['asset_ids'], result['scores'])], 'asset'))

    @app.post('/api/projects/{p}/jobs/{jid}/resume')
    def resume(p: str, jid: str):
        job = store.job(p, jid)
        licensing.check(p, job, 'job')
        if job['kind'] == 'generation': raise HTTPException(410, '视频生成已取消，不能重新执行旧任务')
        if job['state'] != 'queued': raise Conflict('仅未执行的任务可以恢复；不确定任务不能重发')
        return dispatch(p, job)

    @app.post('/api/projects/{p}/jobs/{jid}/reconcile')
    async def reconcile(p: str, jid: str, request: Request):
        body = await request.json();job = store.job(p, jid)
        rid = body.get('provider_request_id', '')
        if job['state'] != 'needs_attention' or job['kind'] != 'generation' or not isinstance(rid, str) or not rid or len(rid) > 180:
            raise ValueError('仅支持把不确定的生成任务与供应商真实编号关联；不会重新提交')
        return export_safe(licensing.job_view(p, store.transition(p, jid, 'submitted', provider_request_id=rid)))

    @app.post('/api/projects/{p}/jobs/{jid}/favorite')
    async def favorite(p: str, jid: str, request: Request):
        store.job(p, jid)
        body = await request.json()
        if not isinstance(body.get('favorite'), bool): raise ValueError('收藏标记无效')
        old = next((a for a in store.list(p,'job_annotation') if a['job_id'] == jid), None)
        return store.put(p,'job_annotation',{'job_id':jid,'favorite':body['favorite']},old['id'] if old else None)

    @app.get('/api/projects/{p}/assets/{aid}/clip')
    def scene_clip(p: str, aid: str, start_ms: int = 0, duration: float = 5):
        licensing.check(p, store.get(p, 'asset', aid), 'asset')
        return FileResponse(media.clip(p, aid, start_ms, duration), filename='reference-clip.mp4',media_type='video/mp4')

    @app.post('/api/projects/{p}/jobs/{jid}/poll')
    def poll(p: str, jid: str):
        job = store.job(p, jid)
        licensing.check(p, job, 'job')
        return export_safe(licensing.job_view(p, workflow.resume_generation(p, jid, model(p, job['payload']['binding_id'], job['payload'].get('binding_snapshot')))))

    @app.post('/api/projects/{p}/jobs/{jid}/cancel')
    def cancel(p: str, jid: str):
        job = store.cancel(p, jid)
        if job['kind'] == 'media_ingest' and job['state'] == 'cancelled':
            asset = store.get(p,'asset',job['payload']['asset_id'])
            if asset.get('inspection') != 'ready':
                asset['inspection'] = 'failed'
                asset.setdefault('metadata',{})['processing'] = {'state':'cancelled','error':'处理已取消，原文件已保留，可以重新处理'}
                store.put(p,'asset',asset,asset['id'])
        if security_profile == 'team':
            return export_safe(licensing.job_view(p, job))
        if job['state'] == 'cancelling' and job['provider_request_id']:
            result = model(p, job['payload']['binding_id'], job['payload'].get('binding_snapshot')).cancel_video(job['provider_request_id'])
            if result['state'] == 'cancelled': job = store.transition(p, jid, 'cancelled')
        return export_safe(licensing.job_view(p, job))

    @app.post('/api/projects/{p}/jobs/{jid}/settle')
    async def settle(p: str, jid: str, request: Request):
        body = await request.json()
        if not body.get('confirmed'): raise ValueError('需要核对供应商账单后确认实际费用')
        if not body.get('evidence') or not body.get('bill_reference'):raise ValueError('核账需要账单编号和金额依据，请使用用量与费用中的核对入口')
        store.put(p,'billing_reconciliation',{'job_id':jid,'actual_cost':body['actual_cost'],'evidence':body['evidence'],'bill_reference':body['bill_reference']})
        return export_safe(licensing.job_view(p, store.settle(p, jid, body['actual_cost'])))

    @app.post('/api/projects/{p}/jobs/{jid}/archive')
    async def archive(p: str, jid: str, request: Request):
        job = store.job(p, jid)
        licensing.check(p, job)
        if job['state'] != 'succeeded' or not job.get('result', {}).get('url'): raise ValueError('此任务没有可归档成片')
        body = await request.json()
        asset = media.download(p, job['result']['url'], rights_confirmed=body.get('rights_confirmed', False), source_kind='finished', title='生成成片')
        brief = job['payload'].get('brief_snapshot')
        lineage = dict(brief_id=brief['id'], brief_version=brief['version'], source_session_id=brief['source']['session_id']) if brief else {}
        script = job['payload'].get('script_snapshot')
        if script:
            lineage.update(script_id=script['id'], script_version=script['version'], source_session_id=(script.get('source') or {}).get('session_id'))
        return export_safe(store.put(p, 'asset', {**asset, 'generation_job_id': jid, **lineage}, asset['id']))

    @app.get('/api/projects/{p}/jobs/{jid}/events')
    def events(p: str, jid: str, after: int = 0):
        licensing.check(p, store.job(p, jid), 'job')
        return store.events(p, jid, after)

    @app.post('/api/projects/{p}/notes')
    def note_create(p: str, body: NoteInput):
        licensing.check(p, {'session_id': body.session_id})
        return Reports(store).from_session(p, body.session_id, body.title, body.turn_index, body.finding_indexes)

    @app.put('/api/projects/{p}/notes/{nid}')
    def note_edit(p: str, nid: str, body: EditInput):
        licensing.check(p, store.get(p, 'note', nid), 'note')
        return Reports(store).edit(p, nid, body.markdown)

    @app.get('/api/projects/{p}/notes/{nid}/export')
    def note_export(p: str, nid: str, format: Literal['md', 'pdf', 'csv', 'xlsx'] = 'md'):
        licensing.check(p, store.get(p, 'note', nid), 'note', export=True)
        return Response(Reports(store).export(p, nid, format), headers={'Content-Disposition': f'attachment; filename="report.{format}"'})

    @app.get('/api/projects/{p}/parses/{rid}/export')
    def parse_export(p: str, rid: str, format: Literal['md', 'pdf', 'csv', 'xlsx'] = 'xlsx'):
        licensing.check(p, store.get(p, 'parse', rid), 'parse', export=True)
        return Response(workflow.export_parse(p, rid, format), headers={'Content-Disposition': f'attachment; filename="parsing.{format}"'})

    @app.post('/api/projects/{p}/instructions')
    def instruction(p: str, body: InstructionInput): return Instructions(store).save(p, **body.model_dump())

    @app.put('/api/projects/{p}/instructions/{iid}')
    def instruction_edit(p: str, iid: str, body: InstructionInput): return Instructions(store).save(p, **body.model_dump(), record_id=iid)

    @app.post('/api/projects/{p}/instructions/{iid}/render')
    async def render(p: str, iid: str, request: Request): return {'text': Instructions(store).render(p, iid, await request.json())}

    @app.post('/api/projects/{p}/templates')
    async def template(p: str, request: Request):
        from .analysis_templates import Templates
        return Templates(store).save(p,await request.json())

    @app.put('/api/projects/{p}/templates/{tid}')
    async def template_revision(p:str,tid:str,request:Request):
        from .analysis_templates import Templates
        body=await request.json()
        return Templates(store).save(p,body,tid,body.get('expected_version'))

    @app.get('/api/projects/{p}/preferences')
    def preferences(p:str):
        store.project(p)
        try:return store.get(p,'preferences','preferences-'+p)
        except NotFound:return {'default_quote':'','default_binding_id':''}

    @app.put('/api/projects/{p}/preferences')
    async def preferences_save(p:str,request:Request):
        from .contracts import decimal
        body=await request.json()
        if set(body)-{'default_quote','default_binding_id','expected_version','allow_external_references'}:raise ValueError('设置字段无效')
        if body.get('default_quote') and decimal(body['default_quote'])<=0:raise ValueError('默认任务额度需要大于零')
        if 'allow_external_references' in body and type(body['allow_external_references']) is not bool:raise ValueError('请选择是否允许补充外部参考')
        if body.get('default_binding_id'):store.get(p,'model',body['default_binding_id'])
        expected=body.pop('expected_version',None)
        try: prior=store.get(p,'preferences','preferences-'+p)
        except NotFound: prior={}
        return Scripts(store)._write(p,'preferences',{**prior,**body},'preferences-'+p,expected)

    @app.post('/api/projects/{p}/assets/search-segments')
    async def search_segments(p:str,request:Request):
        from .footage import FootageSearch
        body=await request.json()
        spec=TaskSpec(own_asset_ids=body.get('asset_ids',[]),sku_id=body.get('sku_id',''))
        snap=freeze_selection(store,p,{},spec)
        licensing.check(p,{'asset_ids':spec.own_asset_ids},model=True)
        return FootageSearch(store,media).search(p,body.get('query',''),snap,sku_id=spec.sku_id)

    @app.post('/api/projects/{p}/groups')
    async def group(p: str, request: Request):
        body = await request.json()
        if not body.get('title') or not 1 <= len(body.get('asset_ids', [])) <= 200: raise ValueError('分组需要名称与1–200条素材')
        for aid in body['asset_ids']: store.get(p, 'asset', aid)
        return store.put(p, 'group', {'title': body['title'], 'asset_ids': body['asset_ids']})

    @app.get('/api/projects/{p}/clusters')
    def clusters(p: str, first_three: bool = False, recent_days: int | None = None, source_kind: str = '', lifecycle: str | None = None):
        return export_safe(licensing.visible(p, workflow.clusters(p, source_kinds=source_kind.split(',') if source_kind else None,
            first_three=first_three, recent_days=recent_days, lifecycle=lifecycle)))

    @app.post('/api/projects/{p}/review')
    async def review(p: str, request: Request):
        body = await request.json()
        licensing.check(p, body)
        return workflow.review(p, body.get('asset_ids', []), body.get('start_date'), body.get('end_date'))

    from .license_api import register_license
    register_license(app,commercial)
    from .discovery_api import register_discovery
    if security_profile=='local':
        register_discovery(app,store,media,dispatch=dispatch,external_guard=external_guard)
        from .browser_bridge_api import register_browser_bridge
        register_browser_bridge(app,store,vault,root,port=port,license_runtime=commercial)
        from .creative_loop_api import register_creative_loop
        register_creative_loop(app,store,media,commercial)
    from .scripts_api import register_scripts
    from .industry_api import register_industry
    from .licensing_access import LicensedAccess
    industry = register_industry(app, store, media, vault, run_jobs=run_jobs, external_guard=external_guard)
    licensing = LicensedAccess(store, industry, cmm=app.state.cmm)
    from .data_request_plan import DataRequestPlanner
    agent.external_planner=DataRequestPlanner(store,app.state.cmm,authorize=lambda:commercial.require_work('discovery.paid'))
    register_scripts(app, store, access=licensing)
    ui = Path(static_dir) if static_dir else Path(__file__).resolve().parents[2] / 'dist'
    @app.get('/{path:path}')
    def frontend(path: str):
        candidate = (ui / path).resolve()
        if not candidate.is_relative_to(ui.resolve()): raise HTTPException(404)
        if path.startswith('api/'): raise HTTPException(404, '接口不存在')
        if candidate.is_file(): return FileResponse(candidate)
        if '.' not in path and (ui / 'index.html').is_file(): return FileResponse(ui / 'index.html')
        raise HTTPException(404, '请先构建界面')
    return app
