"""Persistent browser grants: these cases precede the 0.5.4 bridge implementation."""
import io
import json
import struct

import pytest

from creative_agent.browser_grants import BrowserGrants, BridgeError
from creative_agent.native_messaging import encode_message, read_message, registration_plan
from creative_agent.store import Store

DEV_ID = 'a' * 32
ORIGIN = f'chrome-extension://{DEV_ID}/'


@pytest.fixture
def bridge(tmp_path):
    store = Store(tmp_path / 'creative.sqlite3')
    project = store.create_project('收藏项目')
    clock = [1000]
    grants = BrowserGrants(store, [ORIGIN], clock=lambda: clock[0])
    return store, project, grants, clock


def connect(grants, project):
    pending = grants.dispatch(ORIGIN, {'operation':'get_status', 'installation_id':'installation-0001', 'protocol_version':1})
    assert pending['state'] == 'pending_confirmation'
    grants.approve(pending['connection_id'], [project['id']])
    return {'connection_id':pending['connection_id'], 'installation_id':'installation-0001', 'protocol_version':1}


def test_connection_requires_desktop_confirmation_and_survives_restart(bridge):
    store, project, grants, _ = bridge
    connection = connect(grants, project)
    again = BrowserGrants(store, [ORIGIN])
    # clock must represent the same validity period, not today's wall clock.
    again.clock = grants.clock
    assert again.dispatch(ORIGIN, {**connection, 'operation':'get_status'})['state'] == 'connected'
    assert again.dispatch(ORIGIN, {**connection, 'operation':'list_allowed_projects'})['projects'][0]['id'] == project['id']


def test_repeated_save_is_one_receipt_and_one_observation(bridge):
    store, project, grants, _ = bridge
    connection = connect(grants, project)
    request = {**connection, 'operation':'save_references', 'project_id':project['id'], 'request_id':'request-0001',
               'captures':[{'url':'https://www.douyin.com/video/12345', 'title':'办公杯'}]}
    first = grants.dispatch(ORIGIN, request)
    assert first['receipt']['created'] == 1
    assert grants.dispatch(ORIGIN, request) == first
    assert grants.dispatch(ORIGIN, {**connection, 'operation':'get_import_receipt', 'request_id':'request-0001', 'project_id':project['id']}) == first
    assert len(store.list(project['id'], 'public_reference')[0]['observations']) == 1
    with pytest.raises(BridgeError, match='幂等'):
        grants.dispatch(ORIGIN, {**request, 'captures':[{'url':'https://www.douyin.com/video/999'}]})


def test_author_profile_is_saved_but_not_counted_as_a_missing_video(bridge):
    _, project, grants, _ = bridge
    connection = connect(grants, project)
    result = grants.dispatch(ORIGIN, {**connection, 'operation':'save_references',
        'project_id':project['id'], 'request_id':'video-and-author', 'captures':[
            {'url':'https://www.douyin.com/video/12345'},
            {'url':'https://www.douyin.com/user/public-author'}]})
    assert result['receipt']['created'] == 2
    assert result['receipt']['needs_video'] == 1


def test_origin_scope_revocation_protocol_and_expiration_fail_closed(bridge):
    store, project, grants, clock = bridge
    connection = connect(grants, project)
    other = store.create_project('其他')
    for delta in [{'project_id':other['id']}, {'protocol_version':2}, {'installation_id':'other-installation'}]:
        with pytest.raises(BridgeError):
            grants.dispatch(ORIGIN, {**connection, 'operation':'save_references','project_id':project['id'],'request_id':'request-0002','captures':[{'url':'https://www.douyin.com/video/12345'}], **delta})
    with pytest.raises(BridgeError):
        grants.dispatch('chrome-extension://'+'b'*32+'/', {**connection,'operation':'get_status'})
    grants.revoke(connection['connection_id'])
    assert grants.dispatch(ORIGIN, {**connection,'operation':'get_status'})['state'] == 'revoked'
    with pytest.raises(BridgeError):
        grants.dispatch(ORIGIN, {**connection, 'operation':'list_allowed_projects'})


def test_native_frames_and_user_only_registration(tmp_path):
    payload = {'operation':'get_status','title':'中文'}
    assert read_message(io.BytesIO(encode_message(payload))) == payload
    with pytest.raises(ValueError):
        read_message(io.BytesIO(struct.pack('<I', 300000)))
    plan = registration_plan(tmp_path/'host', tmp_path, [ORIGIN], platform='darwin', home=tmp_path/'home')
    assert len(plan['manifests']) == 2
    assert all(str(path).startswith(str(tmp_path/'home')) for path in plan['manifests'])
    assert plan['manifest']['allowed_origins'] == [ORIGIN]
    with pytest.raises(ValueError):
        registration_plan(tmp_path/'host', tmp_path, ['chrome-extension://*/'], platform='darwin')


def test_failed_batch_is_atomic_and_does_not_burn_request_id(bridge):
    store, project, grants, _ = bridge
    connection = connect(grants, project)
    request = {**connection,'operation':'save_references','project_id':project['id'],'request_id':'request-retry',
               'captures':[{'url':'https://www.douyin.com/video/12345'},{'url':'https://evil.example/video/1'}]}
    with pytest.raises(ValueError): grants.dispatch(ORIGIN,request)
    assert store.list(project['id'],'public_reference') == []
    request['captures'].pop()
    assert grants.dispatch(ORIGIN,request)['receipt']['created'] == 1


def test_expired_grant_cannot_save_and_can_request_new_confirmation(bridge):
    store, project, grants, clock = bridge
    connection = connect(grants, project)
    clock[0] += 181 * 86400
    assert grants.dispatch(ORIGIN,{**connection,'operation':'get_status'})['state'] == 'expired'
    with pytest.raises(BridgeError): grants.dispatch(ORIGIN,{**connection,'operation':'list_allowed_projects'})
    request = {**connection,'operation':'get_status','request_connection':True};request.pop('connection_id')
    assert grants.dispatch(ORIGIN,request)['state'] == 'pending_confirmation'


def test_bridge_key_rejects_root_bearer_and_never_leaks_secrets(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from creative_agent.browser_bridge_api import register_browser_bridge
    class Vault:
        def __init__(self): self.data = {}
        def get(self,key): return self.data.get(key)
        def set(self,key,value): self.data[key] = value
    app = FastAPI();app.state.security_profile = 'local'
    vault = Vault();store = Store(tmp_path/'creative.sqlite3')
    register_browser_bridge(app,store,vault,tmp_path,allowed_origins=[ORIGIN])
    client = TestClient(app)
    body = {'origin':ORIGIN,'request':{'operation':'get_status','installation_id':'installation-0001','protocol_version':1}}
    assert client.post('/browser-bridge/rpc',json=body,headers={'Authorization':'Bearer root-token'}).status_code == 401
    key = next(iter(vault.data.values()))
    response = client.post('/browser-bridge/rpc',json=body,headers={'X-Creative-Bridge-Key':key})
    assert response.json()['state'] == 'pending_confirmation'
    assert key not in response.text
    assert client.post('/browser-bridge/rpc',json=body,headers={'X-Creative-Bridge-Key':key,'Origin':ORIGIN}).status_code == 401


def test_registration_refresh_and_uninstall_preserve_other_installation(tmp_path):
    from creative_agent.native_messaging import register_host, unregister_host
    root=tmp_path/'data';host=tmp_path/'host';worker=tmp_path/'worker';host.touch();worker.touch()
    plan=register_host(host,worker,root,[ORIGIN],platform='darwin',home=tmp_path/'home')
    manifest=__import__('pathlib').Path(plan['manifest_paths'][0])
    changed=json.loads(manifest.read_text());changed['path']='/other/installation/host';manifest.write_text(json.dumps(changed))
    unregister_host(root)
    assert manifest.is_file()
    assert not __import__('pathlib').Path(plan['manifest_paths'][1]).exists()
