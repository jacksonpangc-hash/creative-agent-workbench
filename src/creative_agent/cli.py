from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import webbrowser
from pathlib import Path


def default_data():
    if os.environ.get('CREATIVE_DATA_DIR'): return Path(os.environ['CREATIVE_DATA_DIR'])
    if sys.platform == 'darwin': return Path.home() / 'Library/Application Support/CreativeAgent'
    if sys.platform == 'win32': return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'CreativeAgent'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'creative-agent'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='creative-agent', description='本地电商创意 Agent；所有模型密钥由使用者配置')
    parser.add_argument('--data-dir', type=Path, default=default_data())
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--no-open', action='store_true')
    serve.add_argument('--token-stdin', action='store_true')
    serve.add_argument('--ui-dir', type=Path)
    project = commands.add_parser('project').add_subparsers(dest='operation', required=True)
    project.add_parser('list')
    create = project.add_parser('create'); create.add_argument('--name', required=True); create.add_argument('--budget'); create.add_argument('--currency', choices=['CNY','USD'],default='CNY')
    ingest = commands.add_parser('ingest'); ingest.add_argument('--project',required=True); ingest.add_argument('--file',type=Path,required=True); ingest.add_argument('--source',default='own')
    export = commands.add_parser('export');export.add_argument('--project',required=True)
    api = commands.add_parser('api', help='通过已运行且鉴权的工作空间执行共享接口')
    api.add_argument('path');api.add_argument('--method',choices=['GET','POST','PUT'],default='GET');api.add_argument('--json-file',type=Path);api.add_argument('--port',type=int,default=8765)
    proxy = commands.add_parser('browser-native-proxy', help='内部本机收藏桥；不输出会话令牌')
    proxy.add_argument('--origin', required=True)
    registration = commands.add_parser('browser-host-register', help='安装或更新当前用户的浏览器收藏桥')
    registration.add_argument('--host', type=Path, required=True)
    registration.add_argument('--worker', type=Path, required=True)
    registration.add_argument('--development', action='store_true')
    registration.add_argument('--home', type=Path)
    registration.add_argument('--desktop', type=Path)
    registration.add_argument('--desktop-pid', type=int)
    registration.add_argument('--refresh-existing', action='store_true')
    commands.add_parser('browser-host-unregister', help='移除当前安装拥有的用户级收藏桥登记')
    args = parser.parse_args(argv)
    if args.command == 'browser-native-proxy':
        from .native_messaging import proxy_request, MAX_MESSAGE_BYTES
        try:
            raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
            if len(raw) > MAX_MESSAGE_BYTES: raise ValueError('message too large')
            result = proxy_request(args.data_dir, args.origin, json.loads(raw))
        except (ValueError, KeyError, TypeError):
            result = {'ok':False,'code':'invalid_request','message':'收藏请求无效'}
        sys.stdout.write(json.dumps(result,ensure_ascii=False));return 0
    if args.command == 'browser-host-register':
        from .native_messaging import register_host
        from .browser_bridge_api import load_release_config
        config = load_release_config()
        ids = [config.get('development_extension_id')] if args.development else [config.get('chrome_store_id'),config.get('edge_store_id')]
        origins = [f'chrome-extension://{value}/' for value in ids if value]
        if args.refresh_existing and not args.development:
            try:
                existing = json.loads((args.data_dir/'native-host.json').read_text())
                known = {f'chrome-extension://{value}/' for value in [config.get('development_extension_id'),config.get('chrome_store_id'),config.get('edge_store_id')] if value}
                origins = [origin for origin in existing['allowed_origins'] if origin in known]
            except (FileNotFoundError,ValueError,KeyError): pass
        if not origins: parser.error('正式商店 ID 尚未配置；内部测试需明确使用 --development')
        result = register_host(args.host,args.worker,args.data_dir,origins,home=args.home,desktop_path=args.desktop,desktop_pid=args.desktop_pid)
        print(json.dumps(result,ensure_ascii=False));return 0
    if args.command == 'browser-host-unregister':
        from .native_messaging import unregister_host
        print(json.dumps(unregister_host(args.data_dir),ensure_ascii=False));return 0
    if args.command == 'serve':
        if not 1024 <= args.port <= 65535: parser.error('端口必须在1024–65535之间')
        token = sys.stdin.readline().strip() if args.token_stdin else secrets.token_urlsafe(32)
        if len(token) < 24: parser.error('本次会话令牌长度不足')
        from .api import create_app
        import uvicorn
        app = create_app(args.data_dir, token=token, static_dir=args.ui_dir, port=args.port)
        url = f'http://127.0.0.1:{args.port}/#token={token}'
        if not args.token_stdin:
            print('本次会话启动链接（请勿分享）：\n' + url, flush=True)
        if not args.no_open: webbrowser.open(url)
        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, log_level='warning')
        return 0
    if args.command == 'api':
        import httpx
        token = os.environ.get('CREATIVE_SESSION_TOKEN', '')
        if not token: parser.error('请提供 CREATIVE_SESSION_TOKEN，仅发送到本机服务')
        if not args.path.startswith('/api/') or '?' in args.path or '#' in args.path: parser.error('仅支持 /api/ 下的接口路径')
        body = json.loads(args.json_file.read_text()) if args.json_file else None
        response = httpx.request(args.method, f'http://127.0.0.1:{args.port}' + args.path,
            headers={'Authorization': 'Bearer '+token}, json=body, timeout=60, trust_env=False)
        print(json.dumps(response.json(),ensure_ascii=False));return 0 if response.is_success else 1
    from .commercial_license import runtime_from_config
    from .secrets import SecretStore
    runtime=runtime_from_config(args.data_dir,SecretStore(),mode='internal')
    if args.command=='ingest':runtime.require_work('assets.import')
    elif args.command=='project' and args.operation=='create':runtime.require_work('workspace.use')
    from .store import Store
    store = Store(args.data_dir / 'creative.sqlite3')
    if args.command == 'project':
        result = store.projects() if args.operation == 'list' else store.create_project(args.name,args.budget,args.currency)
    elif args.command == 'ingest':
        from .media import MediaService
        from .store import export_safe
        result = export_safe(MediaService(store,args.data_dir/'media').ingest(args.project,args.file,source_kind=args.source))
    else:
        from .media import MediaService
        from .portability import validate_bundle_export
        validate_bundle_export(store, MediaService(store,args.data_dir/'media'),args.project)
        result = store.export_project(args.project)
    print(json.dumps(result, ensure_ascii=False));return 0


if __name__ == '__main__':
    raise SystemExit(main())
