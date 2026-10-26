"""Narrow Native Messaging API; desktop approval retains normal root auth."""
from __future__ import annotations

import hmac
import json
import secrets
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .browser_grants import BrowserGrants, BridgeError, MAX_MESSAGE_BYTES
from .contracts import Contract
from .native_messaging import bridge_secret_ref, HOST_NAME, atomic_json, focus_workbench


class ApproveConnection(Contract):
    project_ids: list[str] = Field(min_length=1,max_length=100)


def browser_bridge_request(request):
    return request.method == 'POST' and request.url.path == '/browser-bridge/rpc' and not request.headers.get('origin')


def load_release_config():
    # PyInstaller bundles this JSON beside the module; source uses extension tree.
    candidates = [Path(__file__).with_name('browser-release-config.json'), Path(__file__).resolve().parents[2]/'browser-extension/release-config.json']
    for path in candidates:
        if path.is_file(): return json.loads(path.read_text())
    return {'protocol_version':1}


def register_browser_bridge(app, store, vault, root, *, port=8765, license_runtime=None, allowed_origins=None, open_workbench=None):
    root = Path(root)
    config = load_release_config()
    if allowed_origins is None:
        ids = [config.get('chrome_store_id'),config.get('edge_store_id')]
        # Development must be explicitly opted in by this local installation.
        try:
            installed = json.loads((root/'native-host.json').read_text())
            known = {f'chrome-extension://{id}/' for id in [*ids,config.get('development_extension_id')] if id}
            allowed_origins = [origin for origin in installed['allowed_origins'] if origin in known]
        except (FileNotFoundError,ValueError,KeyError):
            allowed_origins = [f'chrome-extension://{id}/' for id in ids if id]
    if getattr(app.state,'security_profile','local') != 'local': allowed_origins = []
    grants = BrowserGrants(store,allowed_origins,license_runtime=license_runtime,open_workbench=open_workbench or (lambda project_id: focus_workbench(root))) if allowed_origins else None
    app.state.browser_grants = grants
    bridge_key = None
    bridge_error = None
    if getattr(app.state,'security_profile','local') == 'local':
        try:
            bridge_key = vault.get(bridge_secret_ref(root))
            if not bridge_key:
                bridge_key = secrets.token_urlsafe(48);vault.set(bridge_secret_ref(root),bridge_key)
            atomic_json(root/'native-endpoint.json',{'port':port,'protocol_version':1})
        except Exception:
            bridge_error = '系统密钥库不可用，收藏工具暂时无法连接'

    def service():
        if getattr(app.state,'security_profile','local') != 'local': raise HTTPException(403,'浏览器收藏仅支持本机独立运行')
        if grants is None: raise HTTPException(503,'商店版待上架；内部收藏工具尚未完成本机注册')
        return grants

    @app.get('/api/browser-bridge/status')
    def status():
        return {'host_name':HOST_NAME,'protocol_version':1,'state':'bridge_unavailable' if bridge_error else 'ready' if grants else 'not_installed',
                'message':bridge_error,'chrome_store_url':config.get('chrome_store_url'),'edge_store_url':config.get('edge_store_url'),
                'store_state':'available' if config.get('chrome_store_url') or config.get('edge_store_url') else 'awaiting_store_publication'}

    @app.get('/api/browser-bridge/connections')
    def connections():
        return {'connections':grants.connections() if grants else []}

    @app.post('/api/browser-bridge/connections/{connection_id}/approve')
    def approve(connection_id: str, body: ApproveConnection):
        try: return service().approve(connection_id,body.project_ids)
        except ValueError as error: raise HTTPException(400,str(error)) from None

    @app.post('/api/browser-bridge/connections/{connection_id}/revoke')
    def revoke(connection_id: str):
        try: return service().revoke(connection_id)
        except ValueError as error: raise HTTPException(400,str(error)) from None

    @app.get('/api/projects/{project_id}/browser-bridge/receipts')
    def receipts(project_id: str):
        try:
            return {'receipts':[{k:v for k,v in result.items() if k != 'references'} for result in service().receipts(project_id)]}
        except ValueError as error: raise HTTPException(400,str(error)) from None

    @app.post('/browser-bridge/rpc')
    async def rpc(request: Request):
        if not browser_bridge_request(request) or not bridge_key or not hmac.compare_digest(request.headers.get('X-Creative-Bridge-Key',''),bridge_key):
            return JSONResponse({'ok':False,'code':'bridge_unauthorized','message':'本机收藏连接未授权'},status_code=401)
        body_bytes = await request.body()
        if len(body_bytes) > MAX_MESSAGE_BYTES:
            return JSONResponse({'ok':False,'code':'message_too_large','message':'一次收藏内容过多，请分批保存'},status_code=413)
        try:
            body = json.loads(body_bytes)
            result = service().dispatch(body['origin'],body['request'])
            # Records can contain long histories; the bridge sends only compact receipts.
            if 'references' in result:
                result = {**result,'references':[{'id':r['id'],'title':r.get('title',''),'media_state':r['media_state']} for r in result['references']]}
            return {'ok':True,**result}
        except BridgeError as error:
            return {'ok':False,'code':error.code,'message':str(error)}
        except HTTPException as error:
            return {'ok':False,'code':'bridge_unavailable','message':error.detail}
        except (ValueError,KeyError,TypeError):
            return {'ok':False,'code':'invalid_request','message':'收藏内容无法保存，请检查项目和公开链接后重试'}
    return grants
