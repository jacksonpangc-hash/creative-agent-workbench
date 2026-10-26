"""Signed commercial leases. No signing secret or trusted development license ships here."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


FEATURES = frozenset({'workspace.use','product.manage','assets.import','assets.analyze','discovery.public','discovery.paid',
                      'video.breakdown','creative.generate','script.generate','script.export','providers.configure'})


class LicenseError(ValueError):
    pass


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _decode(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def sign_lease(key, claims):
    payload = json.dumps(claims, separators=(',', ':'), sort_keys=True).encode()
    return _encode(payload) + '.' + _encode(key.sign(payload))


def verify_lease(key, token, device_id, now):
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError()
        payload, signature = token.split('.')
        raw = _decode(payload)
        key.verify(_decode(signature), raw)
        claims = json.loads(raw)
        if claims['aud'] != 'creative-workbench' or claims['device_id'] != device_id:
            raise ValueError()
        issued, expires, end = (claims[k] for k in ('issued_at', 'lease_expires_at', 'license_expires_at'))
        if any(type(x) not in (int, float) for x in (issued, expires, end)):
            raise ValueError()
        if not (issued <= now + 60 and now < expires <= end and 0 < expires-issued <= 86400):
            raise ValueError()
        if not isinstance(claims['license_id'], str) or type(claims['generation']) is not int:
            raise ValueError()
        if not isinstance(claims.get('features'),list) or any(not isinstance(x,str) or x not in FEATURES for x in claims['features']):
            raise ValueError()
        return claims
    except Exception as exc:
        raise LicenseError('授权凭据无效、过期或不属于此设备，请联网刷新授权') from exc


class LicenseRuntime:
    def __init__(self, *, public_key, service_url, cache_path, vault, now=time.time, transport=None, monotonic=time.monotonic):
        parsed = urlsplit(service_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('授权服务必须使用无凭证的 HTTPS 地址')
        self.key, self.service_url = public_key, service_url.rstrip('/')
        self.cache_path, self.vault, self.now = Path(cache_path).resolve(), vault, now
        self.transport = transport
        self.monotonic = monotonic
        self.wall_anchor, self.tick_anchor = now(), monotonic()
        self.lock = threading.RLock()
        self._transaction_depth = 0
        self.stop_event = threading.Event()
        self.thread = None
        self.device_namespace = 'commercial-license:' + hashlib.sha256(public_key.public_bytes_raw()).hexdigest()[:20]
        workspace_id = hashlib.sha256(str(self.cache_path.parent).encode('utf-8')).hexdigest()
        self.namespace = self.device_namespace + ':workspace:' + workspace_id
        self.device_id = vault.get(self.device_namespace + ':device')
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            vault.set(self.device_namespace + ':device', self.device_id)
        self.last_error = None

    @contextmanager
    def _workspace_transaction(self):
        # Called under self.lock. The OS lock also serializes separate runtimes
        # and processes, including the remote first-login request. Its file has
        # no credentials; SecretStore remains authoritative for ownership.
        # This assumes the same OS-user filesystem/keychain trust boundary:
        # callers must not unlink/replace the lock file while the app is running.
        if self._transaction_depth:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return
        fd = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.cache_path.parent / '.license-owner.lock', os.O_RDWR | os.O_CREAT, 0o600)
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
        except Exception as exc:
            if fd is not None:
                os.close(fd)
            raise LicenseError('无法锁定工作空间授权状态，请关闭其他实例后重试') from exc
        self._transaction_depth = 1
        try:
            yield
        finally:
            self._transaction_depth = 0
            try:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _effective_now(self):
        wall = self.now()
        expected = self.wall_anchor + self.monotonic() - self.tick_anchor
        if wall + 5 < expected:
            raise LicenseError("系统时间发生回退或停滞，请校准时间")
        return max(wall, expected)

    def _check_owner(self, license_id, *, required=False):
        owner = self.vault.get(self.namespace + ':owner')
        if owner and owner != license_id:
            raise LicenseError('此工作空间属于其他授权账号，请使用原账号登录，或为新账号创建独立工作空间')
        if required and not owner:
            raise LicenseError('此工作空间尚未绑定授权，请使用原账号联网激活；新账号请使用独立工作空间')
        return owner

    def _state(self):
        try:
            return json.loads(self.vault.get(self.namespace + ':state') or '{}')
        except Exception as exc:
            raise LicenseError('系统密钥库中的授权状态损坏') from exc

    def _save(self, state):
        self.vault.set(self.namespace + ':state', json.dumps(state))

    def accept(self, data):
        with self.lock, self._workspace_transaction():
            now = self._effective_now()
            claims = verify_lease(self.key, data['lease'], self.device_id, now)
            owner = self._check_owner(claims['license_id'])
            state = self._state()
            if now + 5 < state.get('high_water', 0):
                raise LicenseError('系统时间发生回退，请校准时间后联网验证')
            if claims['issued_at'] < state.get('issued_at', 0):
                raise LicenseError('拒绝回放旧授权')
            if claims['license_id'] == state.get('license_id') and claims['generation'] < state.get('generation', 0):
                raise LicenseError('授权版本已撤销')
            if not owner:
                self.vault.set(self.namespace + ':owner', claims['license_id'])
            state.update(high_water=max(now,state.get('high_water',0)), issued_at=claims['issued_at'],
                         license_id=claims['license_id'], generation=claims['generation'], revoked=False)
            if data.get('refresh_token'):
                self.vault.set(self.namespace + ':refresh', data['refresh_token'])
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd,'w') as out:
                json.dump({'lease':data['lease']}, out)
            os.replace(temporary,self.cache_path)
            self._save(state)
            self.last_error = None
            return self.status()

    def _claims(self):
        with self.lock, self._workspace_transaction():
            state = self._state()
            now = self._effective_now()
            if state.get('revoked'):
                raise LicenseError('授权已停用或设备已迁移，请联系管理员')
            if now + 5 < state.get('high_water', 0):
                raise LicenseError('系统时间发生回退，请校准时间后联网验证')
            # Persist each observed wall-clock advance, including expired/invalid reads.
            state['high_water'] = max(now, state.get('high_water', 0))
            self._save(state)
            try:
                token = json.loads(self.cache_path.read_text())['lease']
            except Exception as exc:
                raise LicenseError('尚未取得有效授权，请登录激活') from exc
            claims = verify_lease(self.key,token,self.device_id,now)
            self._check_owner(claims['license_id'], required=True)
            if claims['issued_at'] < state.get('issued_at',0) or claims['generation'] < state.get('generation',0):
                raise LicenseError('缓存授权已被替代')
            return claims

    def require_work(self, feature: str | None = None):
        claims=self._claims()
        if feature is not None and feature not in claims['features']:
            raise LicenseError('当前授权未开放此功能：'+str(feature))

    def allow_action(self, action):
        if action not in {'read', 'conservation_export'}:
            self.require_work()

    def status(self):
        result = {'mode':'commercial','device_id':self.device_id,'can_work':False,
                  'refresh_after_seconds':60,'last_error':self.last_error}
        try:
            claims=self._claims()
            result.update(can_work=True, license_id=claims['license_id'],
                          expires_at=claims['license_expires_at'], offline_until=claims['lease_expires_at'],features=claims['features'])
        except (LicenseError,ValueError) as exc:
            result['reason']=str(exc)
        return result

    def revoke(self, reason):
        with self.lock, self._workspace_transaction():
            state=self._state(); state['revoked']=True
            self._save(state)
            self.last_error=reason

    def _request(self, path, payload):
        try:
            with httpx.Client(timeout=10, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                response=client.post(self.service_url+path,json=payload)
            if response.status_code in (401,403,409,410):
                self.revoke('授权服务拒绝了当前授权')
                raise LicenseError('授权已失效、停用或设备不匹配')
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            self.last_error='无法连接授权服务，将使用仍有效的离线凭据'
            raise LicenseError(self.last_error) from exc

    def login(self, license_id, password):
        with self.lock, self._workspace_transaction():
            self._check_owner(license_id)
            return self.accept(self._request('/v1/login',{'license_id':license_id,'password':password,'device_id':self.device_id}))

    def _credentials(self):
        state=self._state()
        refresh_token=self.vault.get(self.namespace + ':refresh')
        if not refresh_token or not state.get('license_id'):
            raise LicenseError('请先登录激活授权')
        self._check_owner(state['license_id'], required=True)
        return {'license_id':state['license_id'],'refresh_token':refresh_token,'device_id':self.device_id}

    def refresh(self):
        # Serialize activation/release/heartbeat so an in-flight old lease cannot
        # undo a release or replace a newly activated account.
        with self.lock, self._workspace_transaction():
            return self.accept(self._request('/v1/refresh',self._credentials()))

    def release(self):
        with self.lock, self._workspace_transaction():
            result=self._request('/v1/release',self._credentials())
            if result.get('released') is not True:
                raise LicenseError('服务未确认设备释放，请重试')
            self.revoke('本机授权已释放，可在另一设备登录')
            self.vault.delete(self.namespace + ':refresh')
            return self.status()

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        def heartbeat():
            while not self.stop_event.is_set():
                try: self.refresh()
                except Exception as exc: self.last_error=str(exc)
                self.stop_event.wait(60)
        self.thread=threading.Thread(target=heartbeat,name='commercial-license-heartbeat',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()


class InternalRuntime:
    def require_work(self, feature: str | None = None): pass
    def allow_action(self, action): pass
    def status(self): return {'mode':'internal','can_work':True,'reason':'内部开发模式，未验证商业授权'}
    def start(self): pass
    def stop(self): pass


def runtime_from_config(data_dir, vault, *, config_path=None, mode=None):
    # Bundled config always wins over environment and caller's internal mode.
    bundled = Path(__file__).with_name('commercial-license.json')
    marker = Path(__file__).with_name('commercial-release.json')
    if marker.exists() and not bundled.is_file():
        raise ValueError('商业发行包缺少强制授权配置，拒绝进入内部模式')
    path = bundled if bundled.exists() else Path(config_path) if config_path else None
    if path is None:
        if mode == 'internal': return InternalRuntime()
        raise ValueError('未配置商业授权；源码开发必须显式指定 internal 模式')
    config=json.loads(path.read_text())
    if config.get('required') is not True:
        raise ValueError('商业发行包必须强制授权')
    key=Ed25519PublicKey.from_public_bytes(_decode(config['public_key']))
    return LicenseRuntime(public_key=key,service_url=config['service_url'],
                          cache_path=Path(data_dir)/'license-lease.json',vault=vault)
