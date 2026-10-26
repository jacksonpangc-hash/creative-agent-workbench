"""Model eligibility must agree before settings, key storage and billed actions."""
import io

import pytest
from PIL import Image

from creative_agent.contracts import ModelBinding
from creative_agent.providers import ProviderClient
from test_api import client, H, Keys
from fastapi.testclient import TestClient
from creative_agent.api import create_app


def test_cross_provider_protocol_is_rejected_before_keychain_write(tmp_path):
    keys = Keys()
    with TestClient(create_app(tmp_path, token='test-session', secret_store=keys, run_jobs=False), base_url='http://127.0.0.1:8765') as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p'}).json()['id']
        existing_keys = dict(keys.data)
        response = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'deepseek', 'label': 'invalid', 'model_id': 'deepseek-flash',
            'role': 'strategy', 'protocol': 'minimax_video', 'api_key': 'private-test-key'})
        assert response.status_code == 422
        assert keys.data == existing_keys
        assert 'private-test-key' not in keys.data.values()
        assert c.get(f'/api/projects/{p}/state', headers=H).json()['models'] == []
        assert 'private-test-key' not in response.text


@pytest.mark.parametrize('model_id', ['MiniMax-M2.7', 'future-unverified'])
def test_non_video_minimax_model_cannot_be_saved_for_video(tmp_path, model_id):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p'}).json()['id']
        response = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'minimax', 'label': 'invalid', 'model_id': model_id,
            'role': 'video_input', 'protocol': 'anthropic', 'api_key': 'key'})
        assert response.status_code in {400, 422}
        assert c.get(f'/api/projects/{p}/state', headers=H).json()['models'] == []


@pytest.mark.parametrize('role,model_id,protocol,url', [
    ('video_input', 'MiniMax-M3', 'anthropic', 'https://api.minimax.io/anthropic'),
    ('video_generation', 'MiniMax-H3', 'minimax_video', 'https://api.minimax.io'),
])
def test_minimax_purpose_automatically_resolves_the_matching_interface(tmp_path, role, model_id, protocol, url):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p'}).json()['id']
        response = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'minimax', 'label': 'model', 'model_id': model_id, 'role': role, 'api_key': 'key'})
        if role == 'video_generation':
            assert response.status_code == 410
            assert c.app.state.store.list(p,'model') == []
            return
        assert response.status_code == 200
        saved = response.json()
        assert saved['protocol'] == protocol and saved['base_url'] == url
        assert saved['availability'][role]['available']


def test_unknown_model_does_not_inherit_native_video_from_its_provider():
    b = ModelBinding(provider='kimi', label='unknown', model_id='unknown-model', role='strategy')
    with pytest.raises(ValueError, match='型号'):
        ProviderClient.validate_chat(b, has_video=True)


def test_known_kimi_video_payload_is_accepted_without_network():
    b = ModelBinding(provider='kimi', label='Kimi', model_id='kimi-k2.6', role='video_input')
    ProviderClient.validate_chat(b, has_video=True, has_images=True)


@pytest.mark.parametrize('kind', ['analysis', 'parse'])
def test_image_only_model_cannot_analyze_video_even_with_frames_flag(tmp_path, kind):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        b = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'deepseek', 'label': '图片模型', 'model_id': 'deepseek-flash', 'role': 'image_input', 'api_key': 'key'}).json()['id']
        image = io.BytesIO(); Image.new('RGB', (8, 8)).save(image, format='PNG')
        a = c.post(f'/api/projects/{p}/assets', headers=H, files={'file': ('a.png', image.getvalue(), 'image/png')}).json()
        s = c.app.state.store
        v = s.put(p, 'asset', {**s.get(p, 'asset', a['id']), 'media_type': 'video'})
        response = c.post(f'/api/projects/{p}/actions', headers=H, json={
            'kind': kind, 'binding_id': b, 'asset_ids': [v['id']], 'question': 'q', 'fields': ['开头'],
            'native_video': False, 'quote': '1', 'idempotency_key': 'no-fallback'})
        assert response.status_code == 400
        assert s.jobs(p) == [] and s.budget(p)['reserved'] == '0'


def test_legacy_bad_binding_is_reported_as_unavailable_without_breaking_state(tmp_path):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p'}).json()['id']
        s = c.app.state.store
        old = s.put(p, 'model', {'provider': 'deepseek', 'label': '旧配置', 'model_id': 'deepseek-flash',
                              'role': 'strategy', 'protocol': 'minimax_video', 'has_key': True})
        response = c.get(f'/api/projects/{p}/state', headers=H)
        assert response.status_code == 200
        saved = response.json()['models'][0]
        assert not saved['availability']['video_input']['available']
        assert not saved['availability']['image_input']['available']
        assert saved['availability']['image_input']['reason']
        assert s.get(p, 'model', old['id']) == old


def test_catalog_gives_specific_models_and_reasons_instead_of_vendor_ceilings(tmp_path):
    with client(tmp_path) as c:
        providers = c.get('/api/catalog', headers=H).json()['providers']
        mini = next(p for p in providers if p['id'] == 'minimax')
        assert next(m for m in mini['models'] if m['id'] == 'MiniMax-M3')['availability']['video_input']['available']
        assert not next(m for m in mini['models'] if m['id'] == 'MiniMax-M2.7')['availability']['video_input']['available']
        deep = next(p for p in providers if p['id'] == 'deepseek')
        assert not deep['models'][0]['availability']['video_input']['available']
        assert deep['models'][0]['availability']['image_input']['available']


def test_changing_provider_cannot_silently_reuse_the_previous_provider_key(tmp_path):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p'}).json()['id']
        old = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'deepseek', 'label': 'model', 'model_id': 'deepseek-flash', 'role': 'image_input', 'api_key': 'key'}).json()
        response = c.post(f'/api/projects/{p}/models', headers=H, json={
            'id': old['id'], 'provider': 'kimi', 'label': 'model', 'model_id': 'kimi-k2.6', 'role': 'video_input'})
        assert response.status_code == 400
        assert c.app.state.store.get(p, 'model', old['id'])['provider'] == 'deepseek'


def test_unknown_ark_model_cannot_get_video_generation_from_legacy_provider_label():
    b = ModelBinding(provider='volcengine', label='unknown endpoint', model_id='ep-unknown', role='video_generation')
    with pytest.raises(ValueError, match='型号'):
        ProviderClient.validate_video(b, 5, '720p')


@pytest.mark.parametrize('strategy_provider,strategy_model', [('deepseek','deepseek-flash'),('kimi','kimi-k2.6')])
def test_reasoning_requires_a_video_model_before_any_reservation(tmp_path, strategy_provider, strategy_model):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        def save(provider, model_id, role):
            return c.post(f'/api/projects/{p}/models', headers=H, json={
                'provider': provider, 'model_id': model_id, 'role': role, 'label': model_id, 'api_key': 'key'}).json()['id']
        # Old generation configurations remain readable but cannot create tasks.
        generator = c.app.state.store.put(p,'model',{'provider':'minimax','model_id':'MiniMax-H3','role':'video_generation','has_key':True})['id']
        strategy = save(strategy_provider, strategy_model, 'image_input')
        s = c.app.state.store
        video = s.put(p, 'asset', {'media_type': 'video', 'metadata': {'duration_ms': 5000}})
        response = c.post(f'/api/projects/{p}/generation', headers=H, json={
            'binding_id': generator, 'strategy_binding_id': strategy, 'quote': '1', 'idempotency_key': 'reasoning',
            'request': {'project_id': p, 'reference_asset_id': video['id'], 'reference_duration': 5,
                        'mode': 'reasoning', 'prompt': 'test', 'resolution': '768P'}})
        assert response.status_code == 410
        assert s.jobs(p) == [] and s.budget(p)['reserved'] == '0'


def test_video_input_route_is_automatic_for_a_known_video_model(tmp_path):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        b = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'kimi', 'label': 'video', 'model_id': 'kimi-k2.6', 'role': 'video_input', 'api_key': 'key'}).json()['id']
        image = io.BytesIO(); Image.new('RGB', (8, 8)).save(image, format='PNG')
        a = c.post(f'/api/projects/{p}/assets', headers=H, files={'file': ('a.png', image.getvalue(), 'image/png')}).json()
        s = c.app.state.store
        video = s.put(p, 'asset', {**s.get(p, 'asset', a['id']), 'media_type': 'video'})
        response = c.post(f'/api/projects/{p}/actions', headers=H, json={
            'kind': 'parse', 'binding_id': b, 'asset_ids': [video['id']], 'fields': ['开头'],
            'native_video': False, 'quote': '1', 'idempotency_key': 'automatic'})
        assert response.status_code == 200
        assert s.job(p, response.json()['id'])['payload']['native_video'] is True


@pytest.mark.parametrize('provider,model_id', [('deepseek','deepseek-flash'),('kimi','kimi-k2.6')])
def test_legacy_queued_video_does_not_bypass_current_input_rules(tmp_path, monkeypatch, provider, model_id):
    import time
    from creative_agent.contracts import CostQuote
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs)
        return {'data': {'fields': {'开头': 'test'}}}
    monkeypatch.setattr(ProviderClient, 'chat', chat)
    keys = Keys()
    with TestClient(create_app(tmp_path, token='test-session', secret_store=keys, run_jobs=True), base_url='http://127.0.0.1:8765') as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        bid = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': provider, 'label': 'old', 'model_id': model_id, 'role': 'image_input', 'api_key': 'key'}).json()['id']
        image = io.BytesIO(); Image.new('RGB', (8, 8)).save(image, format='PNG')
        a = c.post(f'/api/projects/{p}/assets', headers=H, files={'file': ('a.png', image.getvalue(), 'image/png')}).json()
        s = c.app.state.store
        video = s.put(p, 'asset', {**s.get(p, 'asset', a['id']), 'media_type': 'video'})
        payload = {'kind': 'parse', 'binding_id': bid, 'binding_snapshot': s.get(p, 'model', bid),
                   'asset_ids': [video['id']], 'fields': ['开头'], 'native_video': False, 'quote': '1', 'idempotency_key': 'legacy'}
        job = s.create_job(p, 'parse', payload, CostQuote(amount='1', basis='legacy'), 'legacy')
        c.post(f'/api/projects/{p}/jobs/{job["id"]}/resume', headers=H)
        for _ in range(100):
            current = s.job(p, job['id'])
            if current['state'] not in {'queued','running','submitting'}: break
            time.sleep(.01)
        assert calls == []
        assert current['state'] == 'failed'
        assert current['payload'] == job['payload']


def test_whole_video_batch_size_is_checked_before_reserving(tmp_path):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        b = c.post(f'/api/projects/{p}/models', headers=H, json={
            'provider': 'minimax', 'label': 'video', 'model_id': 'MiniMax-M3', 'role': 'video_input', 'api_key': 'key'}).json()['id']
        image = io.BytesIO(); Image.new('RGB', (8, 8)).save(image, format='PNG')
        a = c.post(f'/api/projects/{p}/assets', headers=H, files={'file': ('a.png', image.getvalue(), 'image/png')}).json()
        s = c.app.state.store
        raw = s.get(p, 'asset', a['id'])
        from pathlib import Path
        with Path(raw['path']).open('ab') as f: f.truncate(51 * 1024 * 1024)
        video = s.put(p, 'asset', {**raw, 'media_type': 'video'})
        response = c.post(f'/api/projects/{p}/actions', headers=H, json={
            'kind': 'parse', 'binding_id': b, 'asset_ids': [video['id']], 'fields': ['开头'], 'quote': '1', 'idempotency_key': 'too-large'})
        assert response.status_code == 400
        assert '大小' in response.text
        assert s.jobs(p) == [] and s.budget(p)['reserved'] == '0'


def test_disabled_embedding_config_cannot_enqueue_paid_indexing(tmp_path):
    with client(tmp_path) as c:
        p = c.post('/api/projects', headers=H, json={'name': 'p', 'budget': '10'}).json()['id']
        s = c.app.state.store
        b = s.put(p, 'model', {'provider':'openai','model_id':'text-embedding-3-small','role':'embedding','label':'off','enabled':False,'has_key':True})
        a = s.put(p, 'asset', {'media_type':'image'})
        response = c.post(f'/api/projects/{p}/actions', headers=H, json={
            'kind':'embedding','binding_id':b['id'],'asset_ids':[a['id']],'quote':'1','idempotency_key':'off'})
        assert response.status_code == 400
        assert s.jobs(p) == [] and s.budget(p)['reserved'] == '0'


def test_disabled_chat_config_cannot_be_used_for_text_followup():
    b = ModelBinding(provider='deepseek', model_id='deepseek-flash', label='off', role='strategy', enabled=False)
    with pytest.raises(ValueError, match='停用'):
        ProviderClient.validate_chat(b)
