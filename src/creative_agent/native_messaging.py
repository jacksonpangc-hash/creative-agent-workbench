"""User-level Native Messaging registration and authenticated local worker proxy.

No plaintext secret fallback. Registration is explicit and can target an isolated
home; desktop startup may refresh only the same installed executable's records.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

from .browser_grants import MAX_MESSAGE_BYTES, checked_origins

HOST_NAME = 'org.creativeagent.collector'
BRIDGE_SECRET_REF = 'browser-native-bridge-v1'

def bridge_secret_ref(data_dir):
    return BRIDGE_SECRET_REF + '-' + hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:24]


def encode_message(value):
    data = json.dumps(value,ensure_ascii=False).encode('utf-8')
    if len(data) > MAX_MESSAGE_BYTES: raise ValueError('本机桥消息超出限制')
    return struct.pack('<I',len(data))+data


def read_message(stream):
    header = stream.read(4)
    if not header: return None
    if len(header) != 4: raise ValueError('本机桥消息头不完整')
    length = struct.unpack('<I',header)[0]
    if not 1 <= length <= MAX_MESSAGE_BYTES: raise ValueError('本机桥消息超出限制')
    data = stream.read(length)
    if len(data) != length: raise ValueError('本机桥消息不完整')
    return json.loads(data)


def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    if os.name != 'nt': temporary.chmod(0o600)
    temporary.replace(path)


def registration_plan(host_path, data_dir, origins, *, platform=sys.platform, home=None):
    host_path, data_dir = Path(host_path).resolve(), Path(data_dir).resolve()
    origins = checked_origins(origins)
    home = Path(home) if home else Path.home()
    manifest = {'name':HOST_NAME,'description':'Creative Agent 本机参考收藏桥','path':str(host_path),'type':'stdio','allowed_origins':origins}
    if platform == 'darwin':
        manifests = [home/'Library/Application Support'/browser/'NativeMessagingHosts'/f'{HOST_NAME}.json' for browser in ('Google/Chrome','Microsoft Edge')]
        registry = []
    elif platform == 'win32':
        manifests = [data_dir/'native-messaging'/f'{HOST_NAME}.json']
        registry = [fr'Software\{browser}\NativeMessagingHosts\{HOST_NAME}' for browser in (r'Google\Chrome',r'Microsoft\Edge')]
    else:
        manifests = [home/'.config'/browser/'NativeMessagingHosts'/f'{HOST_NAME}.json' for browser in ('google-chrome','microsoft-edge')]
        registry = []
    return {'manifest':manifest,'manifests':manifests,'registry':registry,'data_dir':data_dir}


def register_host(host_path, worker_path, data_dir, origins, *, platform=sys.platform, home=None, desktop_path=None, desktop_pid=None):
    if not Path(host_path).is_file() or not Path(worker_path).is_file():
        raise ValueError('缺少已安装的本机桥或工作进程，请修复桌面安装')
    plan = registration_plan(host_path,data_dir,origins,platform=platform,home=home)
    # Host default config lives in the per-user data directory. A small launcher
    # path is not necessary; the binary resolves the same OS-specific location.
    atomic_json(plan['data_dir']/'native-host.json', {'worker_path':str(Path(worker_path).resolve()),'data_dir':str(plan['data_dir']), 'allowed_origins':checked_origins(origins),'protocol_version':1,'desktop_path':str(Path(desktop_path).resolve()) if desktop_path else None,'desktop_pid':desktop_pid})
    for path in plan['manifests']: atomic_json(path,plan['manifest'])
    if plan['registry']:
        import winreg
        for registry in plan['registry']:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,registry) as key:
                winreg.SetValueEx(key,'',0,winreg.REG_SZ,str(plan['manifests'][0]))
    atomic_json(plan['data_dir']/'native-registration.json',{'host_path':str(Path(host_path).resolve()),'manifests':[str(p) for p in plan['manifests']],'registry':plan['registry']})
    return {'state':'registered','host_name':HOST_NAME,'manifest_paths':[str(p) for p in plan['manifests']]}


def unregister_host(data_dir):
    root = Path(data_dir)
    tracking = root/'native-registration.json'
    if not tracking.exists(): return {'state':'not_registered'}
    record = json.loads(tracking.read_text())
    for path in record['manifests']:
        path = Path(path)
        if path.exists():
            current = json.loads(path.read_text())
            # Do not remove a newer or another installation's registration.
            if current.get('name') == HOST_NAME and current.get('path') == record['host_path']: path.unlink()
    if sys.platform == 'win32':
        import winreg
        for registry in record['registry']:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry) as key: current = winreg.QueryValue(key,'')
                if current in record['manifests']: winreg.DeleteKey(winreg.HKEY_CURRENT_USER,registry)
            except FileNotFoundError: pass
    tracking.unlink()
    return {'state':'unregistered'}


def proxy_request(data_dir, origin, request, *, vault=None):
    from .secrets import SecretStore
    import httpx
    root = Path(data_dir)
    try:
        config = json.loads((root/'native-host.json').read_text())
        if origin not in checked_origins(config['allowed_origins']): raise ValueError('扩展来源不受信任')
        endpoint = json.loads((root/'native-endpoint.json').read_text())
        port = endpoint['port']
        if not isinstance(port,int) or not 1024 <= port <= 65535: raise ValueError('本机应用端口无效')
        secret = (vault or SecretStore()).get(bridge_secret_ref(root))
        if not secret: raise ValueError('系统密钥库中未找到收藏连接密钥')
        response = httpx.post(f'http://127.0.0.1:{port}/browser-bridge/rpc',json={'origin':origin,'request':request},headers={'X-Creative-Bridge-Key':secret},timeout=12,trust_env=False)
        if response.status_code == 401: return {'ok':False,'code':'bridge_unavailable','message':'收藏连接已变更，请重新打开桌面应用'}
        result = response.json()
        if len(json.dumps(result).encode()) > MAX_MESSAGE_BYTES: raise ValueError('保存回执过大，请在桌面应用查看')
        return result
    except (FileNotFoundError,httpx.TransportError):
        if request.get('operation') == 'open_workbench' and focus_workbench(root, launch=True):
            return {'ok':True,'state':'desktop_starting','message':'正在打开创意工作台，请稍后刷新连接'}
        return {'ok':False,'code':'desktop_offline','message':'桌面应用尚未启动，请打开创意工作台后重试'}
    except (ValueError,KeyError,TypeError):
        return {'ok':False,'code':'bridge_unavailable','message':'本机收藏连接不可用，请在桌面设置中修复连接'}


def focus_workbench(data_dir, *, launch=False):
    """Activate only the locally registered application, never a browser token URL."""
    import subprocess
    try:
        config = json.loads((Path(data_dir)/'native-host.json').read_text())
        desktop = Path(config.get('desktop_path') or '')
        if not desktop.is_file(): return False
        if sys.platform == 'darwin':
            bundle = next((parent for parent in desktop.parents if parent.suffix == '.app'),None)
            if not bundle: return False
            return subprocess.run(['open','-a',str(bundle)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3).returncode == 0
        if sys.platform == 'win32':
            pid = config.get('desktop_pid')
            if isinstance(pid,int) and pid > 0:
                script = f'Add-Type -AssemblyName Microsoft.VisualBasic; if ([Microsoft.VisualBasic.Interaction]::AppActivate({pid})) {{ exit 0 }} else {{ exit 1 }}'
                result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3,creationflags=0x08000000)
                if result.returncode == 0: return True
            if launch:
                subprocess.Popen([str(desktop)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
                return True
        return False
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):
        return False
