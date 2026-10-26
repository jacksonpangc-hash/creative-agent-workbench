"""Authorization boundary tests use locally generated keys, never customer credentials."""
import json
import os
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from creative_agent.commercial_license import LicenseError, LicenseRuntime, runtime_from_config, sign_lease
from creative_agent.license_service import (
    LicenseDatabase,
    LicenseService,
    create_license_service,
    hash_password,
)


class Vault:
    def __init__(self): self.data = {}
    def get(self, key): return self.data.get(key)
    def set(self, key, value): self.data[key] = value
    def delete(self, key): self.data.pop(key, None)

@pytest.fixture(params=["sqlite"] + (["postgres"] if os.environ.get("CREATIVE_TEST_LICENSE_POSTGRES_DSN") else []))
def setup(tmp_path, request):
    key = Ed25519PrivateKey.generate()
    clock = [1_800_000_000.0]
    dsn = str(tmp_path / 'licenses.sqlite')
    schema = None
    if request.param == "postgres":
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        admin = os.environ["CREATIVE_TEST_LICENSE_POSTGRES_DSN"]
        schema = "creative_license_test_" + uuid.uuid4().hex
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        dsn = make_conninfo(admin, options="-csearch_path=" + schema)
    try:
        service = LicenseService(LicenseDatabase(dsn), key, now=lambda: clock[0])
        assert service.db.postgres is (request.param == "postgres"), "requested backend was not used"
        yield key, clock, service
    finally:
        if schema:
            with psycopg.connect(admin, autocommit=True) as db:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

def runtime(tmp_path, setup, vault=None, transport=None):
    key, clock, _ = setup
    return LicenseRuntime(public_key=key.public_key(), service_url='https://license.example', cache_path=tmp_path/'lease.json', vault=vault or Vault(), now=lambda:clock[0], transport=transport)

def test_first_activation_single_device_and_renewal(setup):
    _, clock, service = setup
    item = service.issue('Customer', 'strong-customer-password', 3)
    assert item['expires_at'] is None
    result = service.login(item['id'], 'strong-customer-password', 'device-A')
    assert result['expires_at'] == 1_800_259_200
    with pytest.raises(LicenseError): service.login(item['id'], 'strong-customer-password', 'device-B')
    renewed = service.renew(item['id'], 30)
    assert renewed['expires_at'] == 1_802_851_200
    clock[0] = 1_803_000_000
    assert service.renew(item['id'], 3)['expires_at'] == 1_803_259_200

@pytest.mark.parametrize('days',[1, 7, 90, 0, -3])
def test_periods_are_strict(setup, days):
    with pytest.raises(ValueError): setup[2].issue('Customer', 'strong-customer-password', days)

def test_lease_expiry_tamper_device_and_conservation(tmp_path, setup):
    key, clock, service = setup
    client = runtime(tmp_path, setup)
    item = service.issue('Customer', 'strong-customer-password', 3)
    data = service.login(item['id'], 'strong-customer-password', client.device_id)
    client.accept(data)
    client.require_work()
    clock[0] += 86400
    with pytest.raises(LicenseError): client.require_work()
    client.allow_action('read')
    client.allow_action('conservation_export')
    clock[0] -= 86400
    other = runtime(tmp_path/'other', setup)
    with pytest.raises(LicenseError): other.accept(data)
    data['lease'] = data['lease'][:-5] + 'abcde'
    with pytest.raises(LicenseError): client.accept(data)

def test_offline_lease_bounded_by_license_expiry(tmp_path, setup):
    _, clock, service = setup
    client = runtime(tmp_path, setup)
    item = service.issue('Customer', 'strong-customer-password', 3)
    data = service.login(item['id'], 'strong-customer-password', client.device_id)
    clock[0] += 3*86400-10
    data = service.refresh(item['id'],data['refresh_token'],client.device_id)
    client.accept(data)
    clock[0] += 11
    with pytest.raises(LicenseError): client.require_work()

def test_disable_and_transfer_revoke_persistently(setup):
    key, clock, service = setup
    item = service.issue('Customer','strong-customer-password',30)
    data = service.login(item['id'],'strong-customer-password','old-device')
    service.transfer(item['id'],'new-device')
    with pytest.raises(LicenseError): service.refresh(item['id'],data['refresh_token'],'old-device')
    with pytest.raises(LicenseError): service.login(item['id'],'strong-customer-password','old-device')
    fresh = service.login(item['id'],'strong-customer-password','new-device')
    service.disable(item['id'])
    reopened = LicenseService(service.db,key,now=lambda:clock[0])
    with pytest.raises(LicenseError): reopened.refresh(item['id'],fresh['refresh_token'],'new-device')
    assert [x['action'] for x in reopened.audit(item['id'])] == ['issue','activate','transfer','login','disable']

def test_cache_restart_rollback_and_known_revocation(tmp_path, setup):
    _, clock, service = setup
    vault = Vault(); client = runtime(tmp_path,setup,vault)
    item = service.issue('Customer','strong-customer-password',30)
    client.accept(service.login(item['id'],'strong-customer-password',client.device_id))
    clock[0] += 500
    client.require_work()
    runtime(tmp_path,setup,vault).require_work()
    clock[0] -= 100
    with pytest.raises(LicenseError): runtime(tmp_path,setup,vault).require_work()
    clock[0] += 100
    client.revoke('disabled')
    with pytest.raises(LicenseError): runtime(tmp_path,setup,vault).require_work()

def test_corrupt_cache_fails_closed(tmp_path, setup):
    vault=Vault(); client=runtime(tmp_path,setup,vault)
    (tmp_path/'lease.json').write_text('{broken')
    with pytest.raises(LicenseError): runtime(tmp_path,setup,vault).require_work()

def test_admin_auth_and_public_schema(setup):
    _, _, service=setup
    app=create_license_service(service,admin_password_hash=hash_password('operator-password-long'))
    client=TestClient(app)
    assert client.post('/admin/licenses',json={'customer':'Alice','password':'password-long','days':3}).status_code==401
    assert client.post('/admin/login',json={'password':'wrong'}).status_code==401
    token=client.post('/admin/login',json={'password':'operator-password-long'}).json()['token']
    headers={'Authorization':'Bearer '+token}
    response=client.post('/admin/licenses',headers=headers,json={'customer':'Alice','password':'password-long','days':3})
    assert response.status_code==200
    item=response.json()
    assert 'password_hash' not in item
    activation=client.post('/v1/login',json={'license_id':item['id'],'password':'password-long','device_id':'desktop-A'})
    assert activation.status_code==200
    assert set(activation.json())=={'lease','refresh_token','expires_at','refresh_after_seconds'}
    assert client.get('/admin/licenses',headers=headers).json()[0]['id']==item['id']
    assert client.post('/v1/login',json={'license_id':item['id'],'password':'wrong','device_id':'desktop-A'}).status_code==403


def test_config_requires_explicit_internal_mode(tmp_path):
    with pytest.raises(ValueError): runtime_from_config(tmp_path,Vault())
    internal=runtime_from_config(tmp_path,Vault(),mode='internal')
    assert internal.status()['mode']=='internal'
    internal.require_work()

def test_network_refresh_revokes_known_denial_and_preserves_offline_on_network_failure(tmp_path,setup):
    import httpx
    _, clock, service=setup
    status=[200]
    def handler(request):
        body=json.loads(request.content)
        if status[0]==503: return httpx.Response(503)
        if status[0]==403: return httpx.Response(403,json={'detail':'disabled'})
        data=service.login(body['license_id'],body['password'],body['device_id']) if request.url.path.endswith('login') else service.refresh(body['license_id'],body['refresh_token'],body['device_id'])
        return httpx.Response(200,json=data)
    vault=Vault()
    client=runtime(tmp_path,setup,vault,httpx.MockTransport(handler))
    item=service.issue('Alice','strong-customer-password',30)
    client.login(item['id'],'strong-customer-password')
    assert all('strong-customer-password' not in str(v) for v in vault.data.values())
    status[0]=503
    with pytest.raises(LicenseError): client.refresh()
    client.require_work()
    status[0]=403
    with pytest.raises(LicenseError): client.refresh()
    with pytest.raises(LicenseError): runtime(tmp_path,setup,vault).require_work()


def test_internal_mode_cannot_override_bundled_commercial_config(tmp_path,setup,monkeypatch):
    import creative_agent.commercial_license as module
    key,_,_=setup
    from creative_agent.commercial_license import _encode
    fake=tmp_path/'bundle'/'commercial_license.py'; fake.parent.mkdir()
    config={'required':True,'service_url':'https://license.example','public_key':_encode(key.public_key().public_bytes_raw())}
    fake.with_name('commercial-license.json').write_text(json.dumps(config))
    monkeypatch.setattr(module,'__file__',str(fake))
    client=module.runtime_from_config(tmp_path,Vault(),mode='internal')
    assert client.status()['mode']=='commercial'
    with pytest.raises(LicenseError): client.require_work()


def test_admin_disable_renew_transfer_endpoints_and_token_expiry(setup):
    _,clock,service=setup
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('operator-password-long')))
    login=client.post('/admin/login',json={'password':'operator-password-long'}).json()
    headers={'Authorization':'Bearer '+login['token']}
    item=client.post('/admin/licenses',headers=headers,json={'customer':'Alice','password':'password-long','days':30}).json()
    service.login(item['id'],'password-long','A')
    assert client.post('/admin/licenses/'+item['id']+'/renew',headers=headers,json={'days':180}).json()['expires_at']==1_818_144_000
    assert client.post('/admin/licenses/'+item['id']+'/transfer',headers=headers,json={'device_id':'B'}).json()['device_id']=='B'
    assert client.post('/admin/licenses/'+item['id']+'/disable',headers=headers,json={}).json()['disabled']==1
    clock[0]+=28801
    assert client.get('/admin/licenses',headers=headers).status_code==401


def test_local_license_api_uses_existing_auth_and_hides_password_validation(tmp_path,setup):
    from fastapi import FastAPI

    from creative_agent.license_api import register_license
    app=FastAPI()
    app.state.token='local-session-token'
    client_runtime=runtime(tmp_path,setup)
    register_license(app,client_runtime)
    client=TestClient(app)
    assert client.get('/api/license/status').status_code==401
    headers={'Authorization':'Bearer local-session-token'}
    assert client.get('/api/license/status',headers=headers).json()['can_work'] is False
    response=client.post('/api/license/login',headers=headers,json={'license_id':'id','password':'x'*600})
    assert response.status_code==422
    assert 'x'*600 not in response.text
    assert client.post('/api/license/refresh',headers=headers,json={}).status_code==403


def test_monotonic_clock_prevents_frozen_clock_extending_lease(tmp_path,setup):
    key,clock,service=setup
    ticks=[100.0]
    client=LicenseRuntime(public_key=key.public_key(),service_url='https://license.example',cache_path=tmp_path/'lease.json',vault=Vault(),now=lambda:clock[0],monotonic=lambda:ticks[0])
    item=service.issue('Alice','strong-customer-password',3)
    client.accept(service.login(item['id'],'strong-customer-password',client.device_id))
    ticks[0]+=86401
    with pytest.raises(LicenseError): client.require_work()

def test_service_rate_limits_and_rejects_secret_echo(setup):
    service=setup[2]
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('operator-password-long')))
    for _ in range(10):
        assert client.post('/admin/login',json={'password':'wrong-password'}).status_code==401
    assert client.post('/admin/login',json={'password':'operator-password-long'}).status_code==429
    marker='secret-value-'*50
    response=client.post('/v1/login',json={'license_id':'unknown','password':marker,'device_id':'A'})
    assert response.status_code==422
    assert marker not in response.text


def test_admin_password_hash_malformed_is_configuration_error(setup):
    with pytest.raises(ValueError): create_license_service(setup[2],admin_password_hash='scrypt$broken')


def test_service_body_size_and_invalid_length_are_rejected(setup):
    client=TestClient(create_license_service(setup[2],admin_password_hash=hash_password('operator-password-long')))
    response=client.post('/admin/login',content=b'x'*17000,headers={'content-type':'application/json'})
    assert response.status_code==413
    response=client.post('/admin/login',content=b'{}',headers={'content-length':'invalid','content-type':'application/json'})
    assert response.status_code==400

def test_signed_feature_permissions_are_enforced_at_runtime(tmp_path,setup):
    _,_,service=setup
    client=runtime(tmp_path,setup)
    item=service.issue('Reader','strong-customer-password',30,features=['assets.analyze'])
    client.accept(service.login(item['id'],'strong-customer-password',client.device_id))
    client.require_work('assets.analyze')
    with pytest.raises(LicenseError): client.require_work('creative.generate')
    with pytest.raises(LicenseError): client.require_work('future.unknown')
    client.allow_action('conservation_export')
    with pytest.raises(ValueError): service.issue('Bad','strong-customer-password',3,features=['future.unknown'])


def test_admin_can_issue_feature_subset_without_accepting_unknown_features(setup):
    service=setup[2]
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('operator-password-long')))
    token=client.post('/admin/login',json={'password':'operator-password-long'}).json()['token']
    headers={'Authorization':'Bearer '+token}
    body={'customer':'Reader','password':'password-long','days':3,'features':['assets.analyze']}
    response=client.post('/admin/licenses',headers=headers,json=body)
    assert response.status_code==200
    assert response.json()['features']==['assets.analyze']
    body['features']=['future.unknown']
    assert client.post('/admin/licenses',headers=headers,json=body).status_code==400

@pytest.mark.parametrize('change',[
    {'lease_expires_at':1_800_086_401},
    {'issued_at':1_800_000_120},
    {'aud':'another-product'},
    {'features':['unknown-feature']},
])
def test_even_signed_out_of_policy_claims_are_rejected(tmp_path,setup,change):
    key,clock,_=setup
    client=runtime(tmp_path,setup)
    claims={'aud':'creative-workbench','license_id':'license','device_id':client.device_id,
            'issued_at':1_800_000_000,'lease_expires_at':1_800_086_400,'license_expires_at':1_802_592_000,
            'generation':1,'features':['assets.analyze']}
    claims.update(change)
    with pytest.raises(LicenseError): client.accept({'lease':sign_lease(key,claims)})


def test_first_activation_concurrent_devices_only_one_wins(setup):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    service=setup[2]
    item=service.issue('Alice','strong-customer-password',30)
    barrier=threading.Barrier(2)
    def activate(device):
        barrier.wait()
        try: service.login(item['id'],'strong-customer-password',device); return True
        except LicenseError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(activate,['A','B']))
    assert sorted(outcomes)==[False,True]

def test_normal_device_release_rebinds_without_resetting_period(setup):
    _,clock,service=setup
    item=service.issue('Alice','strong-customer-password',30)
    first=service.login(item['id'],'strong-customer-password','A')
    assert service.release(item['id'],first['refresh_token'],'A')=={'released':True}
    with pytest.raises(LicenseError): service.refresh(item['id'],first['refresh_token'],'A')
    clock[0]+=86400
    next_device=service.login(item['id'],'strong-customer-password','B')
    assert next_device['expires_at']==1_802_592_000
    with pytest.raises(LicenseError): service.login(item['id'],'strong-customer-password','A')


def test_local_release_is_authenticated_and_stops_work_persistently(tmp_path,setup):
    import httpx
    from fastapi import FastAPI

    from creative_agent.license_api import register_license
    service=setup[2]
    def handler(request):
        data=json.loads(request.content)
        return httpx.Response(200,json=service.release(data['license_id'],data['refresh_token'],data['device_id']))
    vault=Vault(); local=runtime(tmp_path,setup,vault,httpx.MockTransport(handler))
    item=service.issue('Alice','strong-customer-password',30)
    local.accept(service.login(item['id'],'strong-customer-password',local.device_id))
    app=FastAPI();app.state.token='session';register_license(app,local)
    client=TestClient(app)
    assert client.post('/api/license/release',json={}).status_code==401
    response=client.post('/api/license/release',headers={'Authorization':'Bearer session'},json={})
    assert response.status_code==200
    assert response.json()['can_work'] is False
    with pytest.raises(LicenseError): runtime(tmp_path,setup,vault).require_work()
    assert vault.get(local.namespace+':refresh') is None


def test_service_release_endpoint_requires_matching_device(setup):
    service=setup[2]
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('operator-password-long')))
    item=service.issue('Alice','strong-customer-password',30)
    first=service.login(item['id'],'strong-customer-password','A')
    body={'license_id':item['id'],'refresh_token':first['refresh_token'],'device_id':'B'}
    assert client.post('/v1/release',json=body).status_code==403
    body['device_id']='A'
    assert client.post('/v1/release',json=body).json()=={'released':True}

def test_commercial_marker_requires_bundled_config_even_internal(tmp_path,monkeypatch):
    import creative_agent.commercial_license as module
    fake=tmp_path/'bundle'/'commercial_license.py';fake.parent.mkdir()
    fake.with_name('commercial-release.json').write_text('{"require_license":true}')
    monkeypatch.setattr(module,'__file__',str(fake))
    with pytest.raises(ValueError,match='商业发行包'): module.runtime_from_config(tmp_path,Vault(),mode='internal')


def test_operator_console_and_enable_admin_release_are_protected(setup):
    service=setup[2]
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('operator-password-long')))
    page=client.get('/admin')
    assert page.status_code==200
    assert '授权管理' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert "frame-ancestors 'none'" in page.headers['content-security-policy']
    assert client.get('/admin/console.js').status_code==200
    assert client.get('/admin/config').status_code==401
    token=client.post('/admin/login',json={'password':'operator-password-long'}).json()['token']
    headers={'Authorization':'Bearer '+token}
    assert 'workspace.use' in client.get('/admin/config',headers=headers).json()['features']
    item=service.issue('Alice','strong-customer-password',30)
    old=service.login(item['id'],'strong-customer-password','A')
    path='/admin/licenses/'+item['id']
    assert client.post(path+'/release',json={}).status_code==401
    assert client.post(path+'/disable',headers=headers,json={}).status_code==200
    assert client.post(path+'/enable',headers=headers,json={}).json()['disabled']==0
    with pytest.raises(LicenseError): service.refresh(item['id'],old['refresh_token'],'A')
    assert client.post(path+'/release',headers=headers,json={}).json()['device_id'] is None
    assert service.login(item['id'],'strong-customer-password','B')['expires_at']==1_802_592_000
    assert [x['action'] for x in service.audit(item['id'])][-4:]==['disable','enable','admin_release','login']


def test_workspace_rejects_other_account_before_remote_login_and_keeps_owner(tmp_path,setup):
    import httpx
    service=setup[2];vault=Vault();calls=[]
    def handler(request):
        calls.append(request.url.path)
        body=json.loads(request.content)
        return httpx.Response(200,json=service.login(body['license_id'],body['password'],body['device_id']))
    client=runtime(tmp_path/'workspace',setup,vault,httpx.MockTransport(handler))
    owner=service.issue('Owner','strong-customer-password',30)
    foreign=service.issue('Foreign','strong-customer-password',30)
    client.login(owner['id'],'strong-customer-password')
    before_cache=client.cache_path.read_bytes();before_vault=dict(vault.data)
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):
        client.login(foreign['id'],'strong-customer-password')
    assert calls==['/v1/login']
    assert client.cache_path.read_bytes()==before_cache and vault.data==before_vault
    assert next(x for x in service.list() if x['id']==foreign['id'])['activated_at'] is None
    reopened=runtime(tmp_path/'workspace',setup,vault,httpx.MockTransport(handler))
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):
        reopened.login(foreign['id'],'strong-customer-password')
    assert calls==['/v1/login']
    assert reopened.status()['license_id']==owner['id']


def test_workspace_rejects_foreign_signed_accept_and_cache_replay(tmp_path,setup):
    service=setup[2];vault=Vault();client=runtime(tmp_path/'workspace',setup,vault)
    owner=service.issue('Owner','strong-customer-password',30)
    other=service.issue('Other','strong-customer-password',30)
    client.accept(service.login(owner['id'],'strong-customer-password',client.device_id))
    foreign=service.login(other['id'],'strong-customer-password',client.device_id)
    before_cache=client.cache_path.read_bytes();before_vault=dict(vault.data)
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):client.accept(foreign)
    assert client.cache_path.read_bytes()==before_cache and vault.data==before_vault
    client.cache_path.write_text(json.dumps({'lease':foreign['lease']}))
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):
        runtime(tmp_path/'workspace',setup,vault).require_work()
    client.cache_path.write_bytes(before_cache)
    assert runtime(tmp_path/'workspace',setup,vault).status()['license_id']==owner['id']


def test_distinct_workspaces_have_independent_owners_and_refresh_on_same_device(tmp_path,setup):
    service=setup[2];vault=Vault()
    first=runtime(tmp_path/'one',setup,vault);second=runtime(tmp_path/'two',setup,vault)
    assert first.device_id==second.device_id
    a=service.issue('A','strong-customer-password',30);b=service.issue('B','strong-customer-password',30)
    first.accept(service.login(a['id'],'strong-customer-password',first.device_id))
    token_a=vault.get(first.namespace+':refresh')
    second.accept(service.login(b['id'],'strong-customer-password',second.device_id))
    assert first.namespace!=second.namespace
    assert vault.get(first.namespace+':refresh')==token_a
    assert runtime(tmp_path/'one',setup,vault).status()['license_id']==a['id']
    assert runtime(tmp_path/'two',setup,vault).status()['license_id']==b['id']
    second.revoke('test revocation')
    first.require_work()
    with pytest.raises(LicenseError):second.require_work()


def test_workspace_owner_survives_expiry_and_canonical_path_alias(tmp_path,setup):
    service=setup[2];clock=setup[1];vault=Vault()
    workspace=tmp_path/'workspace';workspace.mkdir()
    client=runtime(workspace,setup,vault)
    owner=service.issue('Owner','strong-customer-password',3)
    foreign=service.issue('Foreign','strong-customer-password',30)
    client.accept(service.login(owner['id'],'strong-customer-password',client.device_id))
    alias=tmp_path/'alias';alias.symlink_to(workspace,target_is_directory=True)
    reopened=runtime(alias,setup,vault)
    assert reopened.namespace==client.namespace
    clock[0]+=3*86400+1
    with pytest.raises(LicenseError):reopened.require_work()
    reopened.allow_action('read');reopened.allow_action('conservation_export')
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):
        reopened.accept(service.login(foreign['id'],'strong-customer-password',client.device_id))


def test_parallel_first_logins_cannot_claim_one_workspace_for_two_accounts(tmp_path,setup):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import httpx
    service=setup[2];vault=Vault();calls=[]
    first_entered=threading.Event();release_first=threading.Event();second_entered=threading.Event()
    first_account=service.issue('First','strong-customer-password',30)
    second_account=service.issue('Second','strong-customer-password',30)
    def handler(request):
        body=json.loads(request.content);calls.append(body['license_id'])
        if body['license_id']==first_account['id']:
            first_entered.set();assert release_first.wait(3)
        else:second_entered.set()
        return httpx.Response(200,json=service.login(body['license_id'],body['password'],body['device_id']))
    one=runtime(tmp_path/'workspace',setup,vault,httpx.MockTransport(handler))
    two=runtime(tmp_path/'workspace',setup,vault,httpx.MockTransport(handler))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(one.login,first_account['id'],'strong-customer-password')
        assert first_entered.wait(2)
        second=pool.submit(two.login,second_account['id'],'strong-customer-password')
        try:side_effect=second_entered.wait(.15)
        finally:release_first.set()
        if side_effect:
            for job in (first,second):
                try:job.result(timeout=3)
                except LicenseError:pass
            pytest.fail('A second account reached remote activation before the workspace owner was pinned')
        assert first.result(timeout=3)['license_id']==first_account['id']
        with pytest.raises(LicenseError,match='原账号|独立工作空间'):second.result(timeout=3)
    assert calls==[first_account['id']]
    assert next(x for x in service.list() if x['id']==second_account['id'])['activated_at'] is None


def test_releasing_workspace_license_does_not_release_workspace_owner(tmp_path,setup):
    import httpx
    service=setup[2];vault=Vault()
    def handler(request):
        body=json.loads(request.content)
        return httpx.Response(200,json=service.release(body['license_id'],body['refresh_token'],body['device_id']))
    client=runtime(tmp_path/'workspace',setup,vault,httpx.MockTransport(handler))
    account=service.issue('Owner','strong-customer-password',30)
    client.accept(service.login(account['id'],'strong-customer-password',client.device_id))
    client.release()
    foreign=service.issue('Foreign','strong-customer-password',30)
    with pytest.raises(LicenseError,match='原账号|独立工作空间'):
        runtime(tmp_path/'workspace',setup,vault).login(foreign['id'],'strong-customer-password')
    client.allow_action('read');client.allow_action('conservation_export')
    client.accept(service.login(account['id'],'strong-customer-password',client.device_id))
    client.require_work()
