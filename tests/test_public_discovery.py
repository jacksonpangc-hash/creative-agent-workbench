"""Local behavior only: these fixtures do not certify live platform compatibility."""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative_agent.store import NotFound, Store


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')
    p = store.create_project('公开参考')['id']
    q = store.create_project('隔离项目')['id']
    return store, p, q


def service(store):
    module = importlib.import_module('creative_agent.discovery')
    return module.Discovery(store)


def capture(url='https://www.douyin.com/video/1234567890123456789', **changes):
    return {'url': url, 'title': '用户主动收集的页面', 'author': '公开作者',
            'metrics': [{'name': 'likes', 'raw': '1.2万', 'basis': 'visible_page', 'as_of': '2026-10-23T01:00:00Z'}], **changes}


def test_durable_query_plan_has_official_links_and_no_invented_results(workspace):
    store, p, q = workspace
    plan = service(store).create_plan(p, '  洗衣液 去污  ', ['douyin', 'kuaishou'])
    assert plan['query'] == '洗衣液 去污'
    assert plan['mode'] == 'user_assisted'
    assert plan['links'] == [
        {'platform': 'douyin', 'url': 'https://www.douyin.com/search/%E6%B4%97%E8%A1%A3%E6%B6%B2%20%E5%8E%BB%E6%B1%A1'},
        {'platform': 'kuaishou', 'url': 'https://www.kuaishou.com/search/video?searchKey=%E6%B4%97%E8%A1%A3%E6%B6%B2%20%E5%8E%BB%E6%B1%A1'}]
    assert 'results' not in plan
    assert service(Store(store.path)).plans(p)[0]['id'] == plan['id']
    assert service(store).plans(q) == []
    with pytest.raises(ValueError): service(store).create_plan(p, '', ['douyin'])


@pytest.mark.parametrize('url,platform,kind,pid,state', [
    ('分享 https://www.douyin.com/video/1234567890123456789?foo=secret 复制打开', 'douyin', 'video', '1234567890123456789', 'resolved'),
    ('https://www.iesdouyin.com/share/video/1234567890123456789/', 'douyin', 'video', '1234567890123456789', 'resolved'),
    ('https://www.douyin.com/?modal_id=1234567890123456789', 'douyin', 'video', '1234567890123456789', 'resolved'),
    ('https://www.kuaishou.com/short-video/3xhello123', 'kuaishou', 'video', '3xhello123', 'resolved'),
    ('https://v.douyin.com/AbC123/', 'douyin', 'unknown', None, 'pending'),
    ('https://v.kuaishou.com/AbCd/', 'kuaishou', 'unknown', None, 'pending'),
    ('https://www.douyin.com/user/ABC123', 'douyin', 'account', 'ABC123', 'resolved'),
])
def test_url_identity_never_guesses_short_share_ids(workspace, url, platform, kind, pid, state):
    store, p, _ = workspace
    row = service(store).collect(p, [capture(url)])[0]
    assert (row['platform'], row['entity_type'], row['platform_entity_id'], row['identity_state']) == (platform, kind, pid, state)
    assert row['supplier'] == 'public_browser'
    assert row['media_state'] == 'missing'
    assert row['can_visually_analyze'] is False


@pytest.mark.parametrize('url', ['https://evil.example/video/1', 'https://douyin.com.evil.test/video/1', 'http://127.0.0.1:80/private', 'javascript:alert(1)', 'https://user:password@www.douyin.com/video/123'])
def test_collection_rejects_unknown_and_credential_urls_without_network(workspace, url):
    store, p, _ = workspace
    with pytest.raises(ValueError): service(store).collect(p, [capture(url)])
    assert service(store).references(p) == []


def test_dedup_scope_metrics_history_and_pending_identity(workspace):
    store, p, q = workspace
    d = service(store)
    first = d.collect(p, [capture()])[0]
    second = d.collect(p, [capture('https://www.douyin.com/?modal_id=1234567890123456789', metrics=[])])[0]
    assert first['id'] == second['id']
    assert len(second['observations']) == 2
    assert second['observations'][0]['metrics'][0]['raw'] == '1.2万'
    assert second['observations'][1]['metrics'] == []
    assert 'sales' not in second
    assert d.collect(q, [capture()])[0]['id'] != first['id']
    pending = d.collect(p, [capture('https://v.douyin.com/ABC/')])[0]
    assert pending['id'] != first['id']
    assert d.collect(p, [capture('https://v.douyin.com/ABC/?x=1')])[0]['id'] == pending['id']
    with pytest.raises(ValueError): d.collect(p, [capture(metrics=[{'name': 'sales', 'raw': '10', 'basis': 'visible_page', 'as_of':'now'}])])
    with pytest.raises(ValueError): d.collect(p, [capture(cookie='secret')])


def test_attach_requires_same_project_real_file_and_updates_missing_state(workspace, tmp_path):
    store, p, q = workspace
    d = service(store)
    ref = d.collect(p, [capture()])[0]
    foreign = store.put(q, 'asset', {'path': str(tmp_path/'x.mp4')})
    with pytest.raises(NotFound): d.attach(p, ref['id'], foreign['id'])
    missing = store.put(p, 'asset', {'path': str(tmp_path/'missing.mp4')})
    with pytest.raises(ValueError): d.attach(p, ref['id'], missing['id'])
    video = tmp_path/'local.mp4'; video.write_bytes(b'local-fixture')
    asset = store.put(p, 'asset', {'path': str(video)})
    linked = d.attach(p, ref['id'], asset['id'])
    assert linked['asset_id'] == asset['id'] and linked['can_visually_analyze'] is True
    video.unlink()
    assert d.references(p)[0]['can_visually_analyze'] is False


def test_pairing_is_origin_bound_single_use_expiring_and_project_scoped(workspace):
    _store, p, q = workspace
    module = importlib.import_module('creative_agent.discovery_api')
    instant = [1000.]
    pairs = module.Pairings(clock=lambda: instant[0])
    origin = 'chrome-extension://' + 'a'*32
    other = 'chrome-extension://' + 'b'*32
    pairing = pairs.create(p)
    with pytest.raises(ValueError): pairs.redeem(pairing['code'], 'https://evil.test')
    token = pairs.redeem(pairing['code'], origin)
    with pytest.raises(ValueError): pairs.redeem(pairing['code'], origin)
    with pytest.raises(ValueError): pairs.consume(token['import_token'], other)
    assert pairs.consume(token['import_token'], origin) == p
    with pytest.raises(ValueError): pairs.consume(token['import_token'], origin)
    pairing = pairs.create(q); instant[0] += 121
    with pytest.raises(ValueError): pairs.redeem(pairing['code'], origin)
    pairing = pairs.create(q)
    for _ in range(5):
        with pytest.raises(ValueError): pairs.redeem('invalid', origin)
    with pytest.raises(ValueError): pairs.redeem(pairing['code'], origin)


def test_local_api_plan_pair_import_and_attach_do_not_expose_session_bearer(workspace):
    store, p, q = workspace
    api = importlib.import_module('creative_agent.discovery_api')
    app = FastAPI(); api.register_discovery(app, store)
    client = TestClient(app)
    origin = 'chrome-extension://' + 'a'*32
    paired = client.post(f'/api/projects/{p}/discovery/pairing', json={}).json()
    assert len(paired['code']) >= 8 and 'import_token' not in paired
    assert client.post('/extension/pair', headers={'origin':'https://evil.test'}, json={'code':paired['code']}).status_code == 403
    token = client.post('/extension/pair', headers={'origin':origin}, json={'code':paired['code']}).json()
    response = client.post('/extension/import', headers={'origin':origin}, json={'import_token':token['import_token'], 'captures':[capture()]})
    assert response.status_code == 200, response.text
    assert response.json()['references'][0]['project_id'] == p
    assert client.get(f'/api/projects/{q}/discovery/references').json() == []
    assert client.post('/extension/import', headers={'origin':origin}, json={'import_token':token['import_token'], 'captures':[capture()]}).status_code == 400
    assert client.options('/extension/import', headers={'origin':origin}).headers['access-control-allow-origin'] == origin


def test_pair_token_expiry_and_concurrent_redemption_are_atomic():
    from concurrent.futures import ThreadPoolExecutor

    from creative_agent.discovery_api import Pairings
    clock = [0.]
    pairs = Pairings(clock=lambda: clock[0])
    origin = 'chrome-extension://' + 'a'*32
    code = pairs.create('project')['code']
    def redeem():
        try: return pairs.redeem(code, origin)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: redeem(), range(4)))
    assert len([r for r in results if r]) == 1
    token = next(r['import_token'] for r in results if r)
    clock[0] = 301
    with pytest.raises(ValueError): pairs.consume(token, origin)


def test_batch_validation_is_all_or_nothing_and_does_not_preserve_sensitive_queries(workspace):
    store, p, _ = workspace
    d = service(store)
    with pytest.raises(ValueError): d.collect(p, [capture(), capture('https://evil.test/')])
    assert d.references(p) == []
    row = d.collect(p, [capture('https://www.douyin.com/?modal_id=123&token=private')])[0]
    assert row['url'] == 'https://www.douyin.com/video/123'
    assert 'private' not in str(row)


def test_manual_link_collection_is_not_mislabeled_as_browser_capture(workspace):
    store, p, _ = workspace
    from creative_agent.discovery_api import register_discovery
    app = FastAPI(); register_discovery(app, store)
    response = TestClient(app).post(f'/api/projects/{p}/discovery/references', json=[capture(metrics=[])])
    assert response.status_code == 200
    row = response.json()[0]
    assert row['collection_channel'] == 'manual_link'
    assert row['observations'][0]['source'] == 'user_manual_link'


def test_integrated_local_pairing_keeps_host_origin_and_session_auth_guards(tmp_path):
    from creative_agent.api import create_app
    app = create_app(tmp_path, token='local-session', run_jobs=False)
    client = TestClient(app, base_url='http://127.0.0.1:8765')
    auth = {'Authorization':'Bearer local-session'}
    p = client.post('/api/projects', headers=auth, json={'name':'扩展项目'}).json()['id']
    endpoint = f'/api/projects/{p}/discovery/pairing'
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers={**auth, 'Origin':'https://evil.test'}).status_code == 403
    pairing = client.post(endpoint, headers=auth)
    assert pairing.status_code == 200, pairing.text
    code = pairing.json()['code']
    origin = 'chrome-extension://'+'a'*32
    assert client.post('/extension/pair', headers={'Origin':'https://evil.test'}, json={'code':code}).status_code == 403
    assert client.post('/extension/pair', headers={'Origin':origin, 'Host':'evil.test'}, json={'code':code}).status_code == 403
    assert client.post('/extension/pair/extra', headers={'Origin':origin}, json={'code':code}).status_code == 403
    paired = client.post('/extension/pair', headers={'Origin':origin}, json={'code':code})
    assert paired.status_code == 200, paired.text
    token = paired.json()['import_token']
    response = client.post('/extension/import', headers={'Origin':origin}, json={'import_token':token, 'captures':[capture()]})
    assert response.status_code == 200, response.text
    assert response.json()['project_id'] == p
    assert client.get(f'/api/projects/{p}/discovery/references', headers={'Origin':origin}).status_code == 403
    assert client.get(f'/api/projects/{p}/discovery/references', headers=auth).json()[0]['platform'] == 'douyin'


def test_reference_does_not_offer_failed_or_pending_media_for_visual_analysis(workspace, tmp_path):
    store, p, _ = workspace
    d = service(store)
    ref = d.collect(p, [capture()])[0]
    video = tmp_path/'incomplete.mp4'; video.write_bytes(b'incomplete')
    for state in ('pending', 'failed'):
        asset = store.put(p, 'asset', {'path':str(video), 'inspection':state})
        with pytest.raises(ValueError): d.attach(p, ref['id'], asset['id'])
    asset = store.put(p, 'asset', {'path':str(video), 'inspection':'ready'})
    d.attach(p, ref['id'], asset['id'])
    store.put(p, 'asset', {**asset, 'inspection':'failed'}, asset['id'])
    assert d.references(p)[0]['can_visually_analyze'] is False


def test_import_preview_multiline_is_local_bounded_and_never_writes(workspace, monkeypatch):
    import socket
    store, p, q = workspace
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('import must not fetch'))
    d = service(store)
    text = '好物分享 https://v.douyin.com/ABC/\n快手 https://www.kuaishou.com/short-video/abc\n重复 https://v.douyin.com/ABC/?x=1\n坏链接 https://evil.test/video/2'
    preview = d.preview_import(p, 'text', text)
    assert len(preview['rows']) == 4
    assert preview['rows'][2]['duplicate'] == 'batch'
    assert preview['rows'][3]['errors'] and not preview['can_confirm']
    assert preview['rows'][0]['reference']['media_state'] == 'missing'
    assert d.references(p) == d.references(q) == []
    with pytest.raises(ValueError, match='200'):
        d.preview_import(p, 'text', '\n'.join(f'https://www.douyin.com/video/{i}' for i in range(201)))
    # The separate extension boundary must remain 50.
    with pytest.raises(ValueError, match='50'):
        d.collect(p, [capture()] * 51)


def test_import_csv_ranges_missing_values_dates_provenance_and_ignored_columns(workspace):
    store, p, _ = workspace
    d = service(store)
    content = '标题,作者,链接,点赞,播放,发布时间,采集时间,销量\n清洁实测,小李,https://www.douyin.com/video/123,1万–2万,,2026-10-20,2026-10-23T01:00:00Z,10万+\n'
    preview = d.preview_import(p, 'csv', content)
    assert preview['ignored_columns'] == ['销量']
    assert preview['can_confirm']
    row = preview['rows'][0]['reference']
    assert row['title'] == '清洁实测' and row['author'] == '小李'
    assert row['published_at'] == '2026-10-20'
    obs = row['observations'][0]
    assert obs['captured_at'] == '2026-10-23T01:00:00Z'
    assert obs['metrics'] == [{'name':'likes', 'raw':'1万–2万', 'basis':'user_import', 'as_of':'2026-10-23T01:00:00Z', 'state':'range'}]
    assert obs['field_provenance']['title'] == 'user_import'
    assert not any(m['name'] == 'views' for m in obs['metrics'])
    with pytest.raises(ValueError, match='忽略'):
        d.import_references(p, 'csv', content)
    imported = d.import_references(p, 'csv', content, ignore_unknown=True)
    assert imported[0]['source_kind'] == 'inspiration'
    assert imported[0]['usage'] == 'reference_only'
    assert imported[0]['observations'][0]['source'] == 'user_import'


def test_import_json_dedup_reopen_preserves_original_observations_and_attachment(workspace, tmp_path):
    import json
    store, p, q = workspace
    d = service(store)
    old = d.collect(p, [capture()])[0]
    video = tmp_path / 'video.mp4'; video.write_bytes(b'local')
    asset = store.put(p, 'asset', {'path':str(video), 'source_kind':'inspiration'})
    d.attach(p, old['id'], asset['id'])
    content = json.dumps([{'source_url':capture()['url'], 'title':'', 'author':'', 'metrics':[{'name':'likes', 'raw':'未知', 'as_of':'2026-10-24T01:00:00Z', 'basis':'visible_page'}]}])
    preview = d.preview_import(p, 'json', content)
    assert preview['rows'][0]['duplicate'] == 'existing'
    saved = d.import_references(p, 'json', content)[0]
    assert saved['id'] == old['id'] and saved['title'] == old['title']
    assert saved['asset_id'] == asset['id'] and saved['can_visually_analyze']
    assert saved['observations'][0] == old['observations'][0]
    assert saved['observations'][-1]['metrics'][0]['basis'] == 'user_import'
    assert saved['observations'][-1]['metrics'][0]['state'] == 'unknown'
    assert service(Store(store.path)).references(p)[0]['id'] == old['id']
    assert d.references(q) == []
    assert d.import_references(q, 'json', content)[0]['id'] != old['id']


@pytest.mark.parametrize('content', [
    '[{"url":"https://www.douyin.com/video/123","likes":-1}]',
    '[{"url":"https://www.douyin.com/video/123","captured_at":"yesterday"}]',
    '[{"url":"https://www.douyin.com/video/123","metrics":[{"name":"likes","raw":"20","as_of":"bad"}]}]',
    '[42]',
])
def test_import_invalid_rows_are_actionable_and_confirmation_does_not_partially_save(workspace, content):
    store, p, _ = workspace
    d = service(store)
    preview = d.preview_import(p, 'json', content)
    assert preview['rows'][0]['errors'] and not preview['can_confirm']
    with pytest.raises(ValueError): d.import_references(p, 'json', content)
    assert d.references(p) == []


def test_import_preview_and_confirmation_api_use_existing_project_references(workspace):
    store, p, q = workspace
    from creative_agent.discovery_api import register_discovery
    app = FastAPI(); register_discovery(app, store)
    client = TestClient(app)
    payload = {'format':'text', 'content':'https://www.douyin.com/video/123\nhttps://v.kuaishou.com/ABC/'}
    endpoint = f'/api/projects/{p}/discovery/references'
    preview = client.post(endpoint+'/preview', json=payload)
    assert preview.status_code == 200, preview.text
    assert client.get(endpoint).json() == []
    result = client.post(endpoint+'/import', json=payload)
    assert result.status_code == 200, result.text
    assert len(result.json()) == 2
    assert client.get(f'/api/projects/{q}/discovery/references').json() == []


def test_import_preserves_dash_unknown_ranges_and_metric_observation_context(workspace):
    import json
    store, p, _ = workspace
    content = json.dumps([{'url':'https://www.douyin.com/video/123', 'metrics':[
        {'name':'views','raw':'—','as_of':'2026-10-24T00:00:00Z'},
        {'name':'likes','raw':'1万–2万','lower':10000,'upper':20000,'unit':'次','period':'累计','scope':'作品','nature':'user_reported','as_of':'2026-10-24T00:00:00Z'},
        {'name':'comments','raw':'-','as_of':'2026-10-24T00:00:00Z'}]}])
    preview = service(store).preview_import(p, 'json', content)
    assert preview['can_confirm'], preview
    metrics = preview['rows'][0]['reference']['observations'][0]['metrics']
    assert metrics[0]['state'] == metrics[2]['state'] == 'unknown'
    assert metrics[1]['lower'] == 10000 and metrics[1]['upper'] == 20000
    assert metrics[1]['unit'] == '次' and metrics[1]['period'] == '累计'
    assert metrics[1]['scope'] == '作品' and metrics[1]['nature'] == 'user_reported'


def test_import_mixed_invalid_batch_and_alias_collisions_never_partially_write(workspace):
    import json
    store, p, _ = workspace
    d = service(store)
    content = json.dumps([{'url':'https://www.douyin.com/video/123'}, {'url':'https://www.douyin.com/video/456','source_url':'https://www.douyin.com/video/789'}])
    preview = d.preview_import(p, 'json', content)
    assert not preview['can_confirm'] and '重复映射' in preview['rows'][1]['errors'][0]
    with pytest.raises(ValueError): d.import_references(p, 'json', content)
    assert d.references(p) == []


def test_share_text_preserves_supplied_caption_and_source_path_not_private_queries(workspace):
    store, p, _ = workspace
    content = '厨房去污实测 https://www.iesdouyin.com/share/video/123/?token=private 复制打开'
    row = service(store).import_references(p, 'text', content)[0]
    assert row['title'] == '厨房去污实测'
    assert row['observations'][0]['source_url'] == 'https://www.iesdouyin.com/share/video/123/'
    assert 'private' not in str(row)


@pytest.mark.parametrize('change', [{'metrics':False}, {'metrics':0}, {'captured_at':False}, {'published_at':0}, {'title':False}, {'author':False}, {'duration_seconds':True}])
def test_import_falsey_wrong_types_are_invalid_not_missing(workspace, change):
    import json
    store, p, _ = workspace
    preview = service(store).preview_import(p, 'json', json.dumps([{'url':'https://www.douyin.com/video/123', **change}]))
    assert not preview['can_confirm'] and preview['rows'][0]['errors']


def test_existing_active_collection_preserves_supplied_capture_time(workspace):
    store, p, _ = workspace
    row = service(store).collect(p, [capture(captured_at='2026-10-23T01:00:00Z')])[0]
    assert row['observations'][0]['captured_at'] == '2026-10-23T01:00:00Z'
    with pytest.raises(ValueError, match='时间'):
        service(store).collect(p, [capture(captured_at='yesterday')])
