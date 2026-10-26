"""Independent licensing service; customer media and provider keys are not accepted."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .commercial_license import FEATURES, LicenseError, sign_lease

PERIODS = {3, 30, 180, 365}


def hash_password(password):
    if not isinstance(password,str) or not 12 <= len(password) <= 512:
        raise ValueError('密码长度必须为 12–512 字符')
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def verify_password(password, encoded):
    try:
        kind, salt, expected=encoded.split('$')
        if kind != 'scrypt' or len(password)>512: return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return hmac.compare_digest(actual.hex(),expected)
    except (ValueError,TypeError): return False


def _digest(value): return hashlib.sha256(value.encode()).hexdigest()


class _Connection:
    def __init__(self, db, postgres): self.db, self.postgres=db,postgres
    def execute(self, sql, args=()):
        return self.db.execute(sql.replace('?', '%s') if self.postgres else sql,args)


class LicenseDatabase:
    """One transaction per operation; row locks on PostgreSQL, write lock on SQLite."""
    def __init__(self, dsn):
        self.dsn=str(dsn)
        self.postgres=self.dsn.startswith(('postgresql://','postgres://'))
        if not self.postgres and re.match(r'^(?:host|hostaddr|dbname|user|password|port|service|options|sslmode|connect_timeout|application_name)\s*=', self.dsn):
            from psycopg import ProgrammingError
            from psycopg.conninfo import conninfo_to_dict
            try:
                conninfo_to_dict(self.dsn)
            except ProgrammingError:
                raise ValueError('PostgreSQL连接配置无效') from None
            self.postgres=True
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS licenses (
                id TEXT PRIMARY KEY, customer TEXT NOT NULL, password_hash TEXT NOT NULL,
                days INTEGER NOT NULL, features TEXT NOT NULL, activated_at DOUBLE PRECISION, expires_at DOUBLE PRECISION,
                device_id TEXT, generation INTEGER NOT NULL DEFAULT 1, disabled INTEGER NOT NULL DEFAULT 0,
                refresh_hash TEXT, created_at DOUBLE PRECISION NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS license_audit (
                id TEXT PRIMARY KEY, license_id TEXT NOT NULL, action TEXT NOT NULL,
                at DOUBLE PRECISION NOT NULL, detail TEXT NOT NULL, sequence INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, expires_at DOUBLE PRECISION NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS login_limits (
                identity TEXT PRIMARY KEY, started_at DOUBLE PRECISION NOT NULL, failures INTEGER NOT NULL)''')

    @contextmanager
    def transaction(self):
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            connection=psycopg.connect(self.dsn,row_factory=dict_row)
        else:
            connection=sqlite3.connect(self.dsn,timeout=10)
            connection.row_factory=sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
        try:
            yield _Connection(connection,self.postgres)
            connection.commit()
        except BaseException:
            connection.rollback(); raise
        finally: connection.close()


class LicenseService:
    def __init__(self, db, private_key, *, now=time.time):
        self.db, self.private_key, self.now=db,private_key,now

    @staticmethod
    def _period(days):
        if type(days) is not int or days not in PERIODS: raise ValueError('仅支持 3、30、180、365 天')

    def _row(self, db, license_id):
        row=db.execute('SELECT * FROM licenses WHERE id=?'+(' FOR UPDATE' if self.db.postgres else ''),(license_id,)).fetchone()
        if row is None: raise LicenseError('授权不存在或凭证无效')
        return dict(row)

    def _audit(self, db, license_id, action, detail=None):
        sequence=db.execute('SELECT COUNT(*) AS n FROM license_audit WHERE license_id=?',(license_id,)).fetchone()['n'] + 1
        db.execute('INSERT INTO license_audit VALUES (?,?,?,?,?,?)',
                   (str(uuid.uuid4()),license_id,action,self.now(),json.dumps(detail or {}),sequence))

    @staticmethod
    def _public(row):
        result={k:v for k,v in row.items() if k not in {'password_hash','refresh_hash'}}
        result['features']=json.loads(result['features'])
        return result

    def issue(self, customer, password, days, features=None):
        self._period(days)
        features=sorted(FEATURES) if features is None else features
        if not isinstance(features,list) or any(not isinstance(x,str) or x not in FEATURES for x in features):
            raise ValueError('授权包含未知功能')
        features=sorted(set(features))
        if not customer or len(customer)>200: raise ValueError('客户名称不能为空或超过 200 字符')
        password_hash=hash_password(password)
        license_id=str(uuid.uuid4())
        with self.db.transaction() as db:
            db.execute('INSERT INTO licenses (id,customer,password_hash,days,features,created_at) VALUES (?,?,?,?,?,?)',
                       (license_id,customer,password_hash,days,json.dumps(features),self.now()))
            self._audit(db,license_id,'issue',{'days':days,'features':features})
            return self._public(self._row(db,license_id))

    def list(self):
        with self.db.transaction() as db:
            return [self._public(dict(r)) for r in db.execute('SELECT * FROM licenses ORDER BY created_at DESC').fetchall()]

    def _active(self, row, device_id):
        if row['disabled'] or row['device_id'] != device_id or row['expires_at'] <= self.now():
            raise LicenseError('授权已过期、停用或绑定其他设备')

    def _lease(self, row, refresh_token=None):
        now=self.now()
        claims={'aud':'creative-workbench','license_id':row['id'],'device_id':row['device_id'],
                'generation':row['generation'],'issued_at':now,'license_expires_at':row['expires_at'],
                'lease_expires_at':min(now+86400,row['expires_at']),'features':json.loads(row['features'])}
        result={'lease':sign_lease(self.private_key,claims),'expires_at':row['expires_at'],'refresh_after_seconds':60}
        if refresh_token is not None: result['refresh_token']=refresh_token
        return result

    def login(self, license_id, password, device_id):
        if not device_id or len(device_id)>200: raise LicenseError('设备标识无效')
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            if not verify_password(password,row['password_hash']) or row['disabled']:
                raise LicenseError('授权不存在或凭证无效')
            first=row['activated_at'] is None
            if first:
                # Transfers before activation reserve a device; never silently overwrite it.
                if row['device_id'] and row['device_id']!=device_id: raise LicenseError('设备不匹配')
                db.execute('UPDATE licenses SET activated_at=?,expires_at=?,device_id=? WHERE id=?',
                           (self.now(),self.now()+row['days']*86400,device_id,license_id))
                row=self._row(db,license_id)
            if not first and row['device_id'] is None:
                db.execute('UPDATE licenses SET device_id=? WHERE id=?',(device_id,license_id))
                row=self._row(db,license_id)
            self._active(row,device_id)
            token=secrets.token_urlsafe(48)
            db.execute('UPDATE licenses SET refresh_hash=? WHERE id=?',(_digest(token),license_id))
            self._audit(db,license_id,'activate' if first else 'login')
            return self._lease(row,token)

    def refresh(self, license_id, refresh_token, device_id):
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            if not row['refresh_hash'] or not hmac.compare_digest(row['refresh_hash'],_digest(refresh_token)):
                raise LicenseError('授权凭证已撤销')
            self._active(row,device_id)
            return self._lease(row)

    def release(self, license_id, refresh_token, device_id):
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            if (row['device_id'] != device_id or not row['refresh_hash'] or
                not hmac.compare_digest(row['refresh_hash'],_digest(refresh_token))):
                raise LicenseError('当前设备未持有有效释放凭证')
            db.execute('UPDATE licenses SET device_id=NULL,refresh_hash=NULL,generation=generation+1 WHERE id=?',(license_id,))
            self._audit(db,license_id,'release',{'device_id':device_id})
            return {'released':True}

    def renew(self, license_id, days):
        self._period(days)
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            if row['activated_at'] is None: raise ValueError('首次激活前不能续期；请开通所需套餐')
            expiry=max(row['expires_at'],self.now())+days*86400
            db.execute('UPDATE licenses SET expires_at=? WHERE id=?',(expiry,license_id))
            self._audit(db,license_id,'renew',{'days':days,'expires_at':expiry})
            return self._public(self._row(db,license_id))

    def disable(self, license_id):
        with self.db.transaction() as db:
            self._row(db,license_id)
            db.execute('UPDATE licenses SET disabled=1,generation=generation+1,refresh_hash=NULL WHERE id=?',(license_id,))
            self._audit(db,license_id,'disable')
            return self._public(self._row(db,license_id))

    def enable(self, license_id):
        with self.db.transaction() as db:
            self._row(db,license_id)
            db.execute('UPDATE licenses SET disabled=0,generation=generation+1,refresh_hash=NULL WHERE id=?',(license_id,))
            self._audit(db,license_id,'enable')
            return self._public(self._row(db,license_id))

    def admin_release(self, license_id):
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            db.execute('UPDATE licenses SET device_id=NULL,refresh_hash=NULL,generation=generation+1 WHERE id=?',(license_id,))
            self._audit(db,license_id,'admin_release',{'device_id':row['device_id']})
            return self._public(self._row(db,license_id))

    def transfer(self, license_id, device_id):
        if not device_id or len(device_id)>200: raise ValueError('新设备标识无效')
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            if row['disabled']: raise ValueError('停用授权不可迁移')
            db.execute('UPDATE licenses SET device_id=?,generation=generation+1,refresh_hash=NULL WHERE id=?',(device_id,license_id))
            self._audit(db,license_id,'transfer',{'old_device':row['device_id'],'new_device':device_id})
            return self._public(self._row(db,license_id))

    def set_features(self, license_id, features):
        if not isinstance(features,list) or any(not isinstance(feature,str) or feature not in FEATURES for feature in features):
            raise ValueError('授权包含未知功能')
        features=sorted(set(features))
        with self.db.transaction() as db:
            row=self._row(db,license_id)
            previous=json.loads(row['features'])
            if previous==features: return self._public(row)
            db.execute('UPDATE licenses SET features=?,generation=generation+1,refresh_hash=NULL WHERE id=?',(json.dumps(features),license_id))
            self._audit(db,license_id,'set_features',{'previous':previous,'features':features})
            return self._public(self._row(db,license_id))

    def audit(self, license_id):
        with self.db.transaction() as db:
            return [dict(x) for x in db.execute('SELECT * FROM license_audit WHERE license_id=? ORDER BY sequence',(license_id,)).fetchall()]

    def rate_check(self, identity):
        with self.db.transaction() as db:
            # Atomic upsert serializes concurrent attempts, including absent rows.
            db.execute('''INSERT INTO login_limits(identity,started_at,failures) VALUES (?,?,1)
                ON CONFLICT(identity) DO UPDATE SET
                failures=CASE WHEN login_limits.started_at<? THEN 1 ELSE login_limits.failures+1 END,
                started_at=CASE WHEN login_limits.started_at<? THEN ? ELSE login_limits.started_at END''',
                (identity,self.now(),self.now()-300,self.now()-300,self.now()))
            row=db.execute('SELECT failures FROM login_limits WHERE identity=?',(identity,)).fetchone()
            blocked=row['failures']>10
        if blocked: raise HTTPException(429,'登录尝试过于频繁，请稍后重试')


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Password(Input):
    password: SecretStr = Field(min_length=1,max_length=512)

class AdminLogin(Password):
    otp: str = Field(default='',max_length=6)

class Issue(Password):
    features: list[str] | None=Field(default=None,max_length=len(FEATURES))
    customer: str=Field(min_length=1,max_length=200)
    days: int=Field(strict=True)

class Login(Password):
    license_id: str=Field(min_length=1,max_length=100)
    device_id: str=Field(min_length=1,max_length=200)

class Refresh(Input):
    license_id: str=Field(min_length=1,max_length=100)
    refresh_token: SecretStr=Field(min_length=1,max_length=200)
    device_id: str=Field(min_length=1,max_length=200)

class Renewal(Input):
    days: int=Field(strict=True)

class FeatureUpdate(Input):
    features: list[str]=Field(max_length=len(FEATURES))


class Transfer(Input):
    device_id: str=Field(min_length=1,max_length=200)


def create_license_service(service, *, admin_password_hash, console_dir=None, admin_totp_secret=None):
    if not admin_password_hash or not re.fullmatch(r'scrypt\$[0-9a-f]{32}\$[0-9a-f]{64}',admin_password_hash):
        raise ValueError('管理员密码哈希格式无效')
    if admin_totp_secret is not None:
        from .admin_mfa import secret_bytes
        factor_id=hashlib.sha256(secret_bytes(admin_totp_secret)).hexdigest()
        with service.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS admin_mfa_counter(identifier text PRIMARY KEY,last_counter bigint NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS admin_security_config(identifier text PRIMARY KEY,revision text NOT NULL)')
            revision=_digest(factor_id+admin_password_hash)
            previous=db.execute('SELECT revision FROM admin_security_config WHERE identifier=?',('admin',)).fetchone()
            if not previous or previous['revision']!=revision:
                db.execute('DELETE FROM admin_sessions')
                db.execute('INSERT INTO admin_security_config VALUES (?,?) ON CONFLICT(identifier) DO UPDATE SET revision=excluded.revision',('admin',revision))
    if not admin_password_hash or not re.fullmatch(r'scrypt\$[0-9a-f]{32}\$[0-9a-f]{64}',admin_password_hash):
        raise ValueError('必须配置管理员 scrypt 密码哈希，无默认账号或口令')
    app=FastAPI(title='Creative Workbench Licensing',docs_url=None,redoc_url=None,openapi_url=None)

    console_root=Path(console_dir) if console_dir else Path(__file__).resolve().parents[2]/'deploy'/'license'

    @app.get('/admin')
    def console(): return FileResponse(console_root/'console.html',media_type='text/html')
    @app.get('/admin/console.js')
    def console_js(): return FileResponse(console_root/'console.js',media_type='application/javascript')
    @app.get('/admin/console.css')
    def console_css(): return FileResponse(console_root/'console.css',media_type='text/css')

    @app.exception_handler(LicenseError)
    async def license_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=403)
    @app.exception_handler(ValueError)
    async def input_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=400)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc): return JSONResponse({'detail':'输入字段无效'},status_code=422)

    @app.middleware('http')
    async def secure_headers(request,call_next):
        try:
            length=int(request.headers.get('content-length','0') or 0)
            if length<0: raise ValueError()
        except ValueError:
            return JSONResponse({'detail':'请求长度无效'},status_code=400)
        if length>16384:
            return JSONResponse({'detail':'请求过大'},status_code=413)
        body=bytearray()
        async for chunk in request.stream():
            if len(body)+len(chunk)>16384:
                return JSONResponse({'detail':'请求过大'},status_code=413)
            body.extend(chunk)
        request._body=bytes(body)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def admin(request:Request):
        authorization=request.headers.get('authorization','')
        token=authorization[7:] if authorization.startswith('Bearer ') else ''
        with service.db.transaction() as db:
            row=db.execute('SELECT expires_at FROM admin_sessions WHERE token_hash=?',(_digest(token),)).fetchone()
        if not row or row['expires_at']<=service.now(): raise HTTPException(401,'管理员身份未验证')

    @app.post('/admin/login')
    def admin_login(body:AdminLogin,request:Request):
        service.rate_check('admin:'+(request.client.host if request.client else 'unknown'))
        if not verify_password(body.password.get_secret_value(),admin_password_hash): raise HTTPException(401,'管理员身份未验证')
        if admin_totp_secret is not None:
            from .admin_mfa import totp_counter
            counter=totp_counter(admin_totp_secret,body.otp,service.now())
            if counter is None:raise HTTPException(401,'管理员验证码不正确或已过期')
            with service.db.transaction() as db:
                result=db.execute('INSERT INTO admin_mfa_counter(identifier,last_counter) VALUES (?,?) ON CONFLICT(identifier) DO UPDATE SET last_counter=excluded.last_counter WHERE admin_mfa_counter.last_counter<excluded.last_counter',(factor_id,counter))
                if result.rowcount != 1:raise HTTPException(401,'此验证码已经使用，请等待新的验证码')
        token=secrets.token_urlsafe(48)
        with service.db.transaction() as db:
            db.execute('DELETE FROM admin_sessions WHERE expires_at<=?',(service.now(),))
            db.execute('INSERT INTO admin_sessions VALUES (?,?)',(_digest(token),service.now()+8*3600))
        return {'token':token,'expires_in':8*3600}

    @app.post('/admin/logout',dependencies=[Depends(admin)])
    def logout(request:Request):
        with service.db.transaction() as db:
            db.execute('DELETE FROM admin_sessions WHERE token_hash=?',(_digest(request.headers['authorization'][7:]),))
        return {'ok':True}

    @app.get('/admin/config',dependencies=[Depends(admin)])
    def console_config(): return {'features':sorted(FEATURES),'period_days':sorted(PERIODS)}
    @app.post('/admin/licenses/{license_id}/enable',dependencies=[Depends(admin)])
    def enable(license_id:str): return service.enable(license_id)
    @app.post('/admin/licenses/{license_id}/release',dependencies=[Depends(admin)])
    def admin_release(license_id:str): return service.admin_release(license_id)
    @app.get('/admin/licenses',dependencies=[Depends(admin)])
    def listing(): return service.list()
    @app.post('/admin/licenses',dependencies=[Depends(admin)])
    def issue(body:Issue): return service.issue(body.customer,body.password.get_secret_value(),body.days,features=body.features)
    @app.post('/admin/licenses/{license_id}/renew',dependencies=[Depends(admin)])
    def renew(license_id:str,body:Renewal): return service.renew(license_id,body.days)
    @app.post('/admin/licenses/{license_id}/disable',dependencies=[Depends(admin)])
    def disable(license_id:str): return service.disable(license_id)
    @app.post('/admin/licenses/{license_id}/features',dependencies=[Depends(admin)])
    def features(license_id:str,body:FeatureUpdate): return service.set_features(license_id,body.features)
    @app.post('/admin/licenses/{license_id}/transfer',dependencies=[Depends(admin)])
    def transfer(license_id:str,body:Transfer): return service.transfer(license_id,body.device_id)
    @app.get('/admin/licenses/{license_id}/audit',dependencies=[Depends(admin)])
    def audit(license_id:str): return service.audit(license_id)
    @app.post('/v1/login')
    def login(body:Login,request:Request):
        service.rate_check('customer:'+_digest(body.license_id))
        return service.login(body.license_id,body.password.get_secret_value(),body.device_id)
    @app.post('/v1/refresh')
    def refresh(body:Refresh): return service.refresh(body.license_id,body.refresh_token.get_secret_value(),body.device_id)
    @app.post('/v1/release')
    def release(body:Refresh): return service.release(body.license_id,body.refresh_token.get_secret_value(),body.device_id)
    return app


def application():
    """Uvicorn factory: secrets are operator-mounted files, never package defaults."""
    import os
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from .license_deployment import validate_public_endpoint
    endpoint=validate_public_endpoint(os.environ.get('LICENSE_PUBLIC_URL',''),os.environ.get('LICENSE_TLS_CERT_FILE',''))
    if os.environ.get('LICENSE_PUBLIC_HOST') != endpoint['host']: raise ValueError('LICENSE_PUBLIC_HOST 必须与正式授权服务URL域名一致')
    dsn=Path(os.environ['LICENSE_DATABASE_URL_FILE']).read_text().strip() if os.environ.get('LICENSE_DATABASE_URL_FILE') else os.environ['LICENSE_DATABASE_URL']
    if not dsn.startswith(('postgresql://','postgres://')):
        raise ValueError('生产入口必须配置 PostgreSQL')
    key=load_pem_private_key(Path(os.environ['LICENSE_SIGNING_KEY_FILE']).read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey): raise ValueError('必须使用 Ed25519 密钥')
    admin_hash=Path(os.environ['LICENSE_ADMIN_HASH_FILE']).read_text().strip()
    if not re.fullmatch(r'scrypt\$[0-9a-f]{32}\$[0-9a-f]{64}',admin_hash): raise ValueError('管理员密码哈希格式无效')
    factor_file=os.environ.get('LICENSE_ADMIN_TOTP_FILE')
    if not factor_file:raise ValueError('正式服务必须配置 LICENSE_ADMIN_TOTP_FILE 管理员二次验证')
    factor=Path(factor_file).read_text().strip()
    return create_license_service(LicenseService(LicenseDatabase(dsn),key),admin_password_hash=admin_hash,console_dir=os.environ.get('LICENSE_CONSOLE_DIR'),admin_totp_secret=factor)
