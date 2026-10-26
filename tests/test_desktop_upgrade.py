"""Commercial desktop behavior; network is blocked by the shared fixture."""
import pytest
from fastapi.testclient import TestClient

from creative_agent.action_budget import plan_reservation
from creative_agent.api import create_app
from creative_agent.store import Store, BudgetError


class Vault:
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value
    def delete(self, key): self.values.pop(key, None)


def test_unknown_price_without_budget_keeps_cost_unknown_and_can_queue(tmp_path):
    store = Store(tmp_path/'data.db'); p = store.create_project('P')['id']
    quote = plan_reservation(store, p, {'currency': 'USD'}, {}, 2)
    assert quote.amount is None and quote.currency == 'USD'
    job = store.create_job(p, 'analysis', {'max_model_requests': 2}, quote, 'unknown')
    assert job['actual_cost'] is None and job['quote']['amount'] is None
    assert store.budget(p)['unknown_jobs'] == 1
    store.transition(p, job['id'], 'running')
    store.recover()
    assert store.job(p, job['id'])['state'] == 'needs_attention'


def test_project_limit_does_not_require_a_second_unknown_price_limit(tmp_path):
    store = Store(tmp_path/'data.db'); p = store.create_project('P', '99')['id']
    quote = plan_reservation(store, p, {'currency': 'CNY'}, {}, 1)
    assert quote.amount is None
    store.create_job(p, 'analysis', {}, quote, 'no-second-gate')


def test_explicit_zero_budget_still_blocks_external_but_not_local_work(tmp_path):
    store = Store(tmp_path/'data.db'); p = store.create_project('P', '0')['id']
    with pytest.raises(ValueError, match='额度'):
        plan_reservation(store, p, {'currency': 'CNY'}, {}, 1)
    from creative_agent.contracts import CostQuote
    local = store.create_job(p, 'transcription', {}, CostQuote(amount='0', basis='本地处理'), 'local')
    assert local['actual_cost'] == '0'


def test_known_estimate_without_limit_is_allowed_and_preserves_old_jobs(tmp_path):
    store = Store(tmp_path/'data.db'); p = store.create_project('P')['id']
    quote = plan_reservation(store, p, {'price_input_million':'2', 'price_output_million':'8'}, {}, 1)
    assert quote.amount > 0
    job = store.create_job(p, 'analysis', {}, quote, 'known')
    before = store.job(p, job['id'])
    Store(store.path)
    assert store.job(p, job['id']) == before


def test_unknown_cost_cannot_bypass_explicit_zero_at_store_boundary(tmp_path):
    from creative_agent.contracts import CostQuote
    store = Store(tmp_path/'data.db'); p = store.create_project('P', '0')['id']
    with pytest.raises(BudgetError):
        store.create_job(p, 'analysis', {}, CostQuote(amount=None, basis='费用未知'), 'bypass')


def test_unpriced_model_action_can_start_without_extra_settings(tmp_path):
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer upgrade'}) as client:
        p = client.post('/api/projects', json={'name':'P'}).json()['id']
        model = client.post(f'/api/projects/{p}/models', json={
            'provider':'deepseek', 'role':'strategy', 'model_id':'deepseek-flash',
            'label':'我的服务', 'api_key':'offline-only',
        }).json()
        body = {'question':'写一条完整脚本', 'binding_id':model['id'],
                'task_intent':'script', 'idempotency_key':'start'}
        result = client.post(f'/api/projects/{p}/actions', json=body)
        assert result.status_code == 200, result.text
        job = app.state.store.job(p, result.json()['id'])
        assert job['quote']['amount'] is None and job['actual_cost'] is None
        assert client.post(f'/api/projects/{p}/actions', json=body).json()['id'] == job['id']


def test_material_question_is_local_even_from_script_entry(tmp_path):
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer upgrade'}) as c:
        p = c.post('/api/projects', json={'name':'P'}).json()['id']
        app.state.store.put(p, 'asset', {'title':'绿豆饼', 'media_type':'video', 'inspection':'ready',
            'metadata':{'duration_ms':57000, 'frame_count':1714}})
        other = app.state.store.create_project('Other')['id']
        app.state.store.put(other, 'asset', {'title':'其他项目私有素材', 'metadata':{}})
        payload = {'task_intent':'script', 'question':'请帮我查询一下我的素材问题。', 'idempotency_key':'inspect'}
        preview = c.post(f'/api/projects/{p}/actions/preview', json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()['local_only'] is True and preview.json()['max_model_requests'] == 0
        result = c.post(f'/api/projects/{p}/actions', json=payload)
        assert result.status_code == 200, result.text
        assert result.json()['local_only'] is True
        session = app.state.store.get(p, 'session', result.json()['session_id'])
        assert '绿豆饼' in str(session) and '台词待识别' in str(session)
        assert '其他项目私有素材' not in str(session)
        assert session['turns'][0]['answer']['result']['local_status'][0]['asset_id']
        assert len(app.state.store.jobs(p)) == 0
        assert c.post(f'/api/projects/{p}/actions', json=payload).json()['session_id'] == session['id']


def test_compare_request_is_not_forced_to_scripts_by_entry(tmp_path):
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer upgrade'}) as c:
        p = c.post('/api/projects', json={'name':'P'}).json()['id']
        m = c.post(f'/api/projects/{p}/models', json={'provider':'deepseek','role':'strategy',
            'model_id':'deepseek-flash','label':'M','api_key':'offline'}).json()
        result = c.post(f'/api/projects/{p}/actions', json={'question':'先比较开头，不要写脚本',
            'task_intent':'script','binding_id':m['id'],'idempotency_key':'compare'})
        assert result.status_code == 200, result.text
        spec = app.state.store.job(p, result.json()['id'])['payload'].get('task_spec')
        assert spec is None or spec['output_type'] != 'scripts'


def test_frame_count_never_implies_video_understanding_or_transcript(tmp_path):
    from creative_agent.material_readiness import get_readiness
    store = Store(tmp_path/'data.db'); p = store.create_project('P')['id']
    asset = store.put(p, 'asset', {'title':'V','media_type':'video','inspection':'ready',
        'metadata':{'frame_count':1714,'duration_ms':57000}})
    ready = get_readiness(store, p, asset['id'])
    assert ready['file']['state'] == 'ready'
    assert ready['transcript']['state'] == 'pending'
    assert ready['visual']['state'] == 'pending'
    assert '帧已索引' not in ready['summary']


def test_failed_transcription_and_verified_silence_are_different(tmp_path):
    from creative_agent.material_readiness import get_readiness
    store = Store(tmp_path/'data.db'); p = store.create_project('P')['id']
    def make(execution):
        return store.put(p,'asset',{'title':'V','media_type':'video','inspection':'ready',
            'metadata':{'duration_ms':1000,'transcription':{'execution_state':execution,'speech_detected':False}}})
    failed, silent = make('failed'), make('succeeded')
    assert get_readiness(store,p,failed['id'])['transcript']['state'] == 'failed'
    assert get_readiness(store,p,silent['id'])['transcript']['state'] == 'no_speech'


def test_user_can_disable_optional_limit_without_erasing_historical_usage(tmp_path):
    from creative_agent.contracts import CostQuote
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer upgrade'}) as c:
        p = c.post('/api/projects', json={'name':'P','budget':'5','currency':'USD'}).json()['id']
        job = app.state.store.create_job(p,'analysis',{},CostQuote(amount='2',currency='USD',basis='历史预留'),'old')
        changed = c.put(f'/api/projects/{p}',json={'name':'P','budget':None})
        assert changed.status_code == 200, changed.text
        assert changed.json()['budget'] is None and changed.json()['currency'] == 'USD'
        assert app.state.store.budget(p)['reserved'] == '2'
        assert app.state.store.job(p,job['id'])['actual_cost'] is None


def test_preview_cannot_be_used_with_changed_material_version(tmp_path):
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer upgrade'}) as c:
        p = c.post('/api/projects',json={'name':'P'}).json()['id'];store=app.state.store
        asset=store.put(p,'asset',{'title':'V','media_type':'video','inspection':'ready','metadata':{}})
        body={'task_intent':'script','question':'查看我的素材状态','idempotency_key':'preview'}
        plan=c.post(f'/api/projects/{p}/actions/preview',json=body).json()
        store.put(p,'asset',{**asset,'title':'changed'},asset['id'])
        r=c.post(f'/api/projects/{p}/actions',json={**body,'plan_id':plan['id']})
        assert r.status_code == 409 and len(store.jobs(p)) == 0


def test_upload_queues_local_speech_automatically_without_paid_job(tmp_path, monkeypatch):
    import time
    from creative_agent.transcription import Transcriber
    app = create_app(tmp_path, token='upgrade', secret_store=Vault(), run_jobs=True)
    def prepared(p, aid):
        asset=app.state.store.get(p,'asset',aid)
        return app.state.store.put(p,'asset',{**asset,'inspection':'ready','metadata':{'duration_ms':1000}},aid)
    monkeypatch.setattr(app.state.media,'process_upload',prepared)
    called=[]
    def transcribed(self,p,ids,**kwargs):
        called.extend(ids)
        return self.store.put(p,'transcription',{'state':'succeeded','asset_ids':ids,'completed':1})
    monkeypatch.setattr(Transcriber,'run',transcribed)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer upgrade'}) as c:
        p=c.post('/api/projects',json={'name':'P'}).json()['id']
        uploaded=c.post(f'/api/projects/{p}/assets?background=true',files={'file':('video.mp4',b'local-fixture','video/mp4')})
        assert uploaded.status_code==200,uploaded.text
        deadline=time.monotonic()+3
        while not called and time.monotonic()<deadline:time.sleep(.02)
        assert called==[uploaded.json()['id']]
        assert all(j['quote']['amount']=='0' for j in app.state.store.jobs(p))


def test_reconciliation_keeps_supplier_currency_and_separate_totals(tmp_path):
    from creative_agent.contracts import CostQuote
    app=create_app(tmp_path,token='upgrade',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer upgrade'}) as c:
        store=app.state.store;p=store.create_project('P')['id']
        job=store.create_job(p,'analysis',{},CostQuote(amount=None,currency='USD',basis='未知报价'),'usd')
        store.transition(p,job['id'],'running');store.finish(p,job['id'],result={})
        response=c.post(f'/api/projects/{p}/billing/reconcile',json={'job_id':job['id'],
            'actual_cost':'0.02','evidence':'账单确认 USD','bill_reference':'invoice-test'})
        assert response.status_code==200,response.text
        receipt=store.get(p,'billing_reconciliation',response.json()['reconciliation_id'])
        assert receipt['currency']=='USD'
        assert store.budget(p)['spent']=='0'
        assert store.budget(p)['by_currency']['USD']['spent']=='0.02'


def test_preview_freezes_model_configuration_and_honors_explicit_round_count(tmp_path):
    app=create_app(tmp_path,token='upgrade',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer upgrade'}) as c:
        p=c.post('/api/projects',json={'name':'P'}).json()['id']
        config={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'first'}
        m=c.post(f'/api/projects/{p}/models',json=config).json()
        body={'question':'写一条完整脚本','task_intent':'script','binding_id':m['id'],
            'discussion_only':True,'task_spec':{'output_type':'scripts','count':1,'max_generation_rounds':3},'idempotency_key':'frozen'}
        plan=c.post(f'/api/projects/{p}/actions/preview',json=body).json()
        assert plan['max_model_requests']==3
        c.post(f'/api/projects/{p}/models',json={**config,'id':m['id'],'api_key':'second'})
        r=c.post(f'/api/projects/{p}/actions',json={**body,'plan_id':plan['id']})
        assert r.status_code==409 and app.state.store.jobs(p)==[]


def test_verified_no_speech_is_a_local_result_not_a_transcription_failure(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from creative_agent.transcription import Transcriber
    from creative_agent.material_readiness import get_readiness
    import creative_agent.asr_runtime as runtime
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setattr(runtime,'runtime_signature',lambda:{'version':'test'})
    def silent(*args,**kwargs):raise runtime.NoSpeechDetected('silence')
    monkeypatch.setattr(runtime,'transcribe',silent)
    store=Store(tmp_path/'data.db');p=store.create_project('P')['id']
    a=store.put(p,'asset',{'title':'Silent','media_type':'video','inspection':'ready',
        'metadata':{'duration_ms':1000,'transcription_attempt':{'execution_state':'failed','failure_reason':'older attempt'}}})
    media=SimpleNamespace(file=lambda *args:tmp_path/'silent.mp4',ffmpeg='offline')
    result=Transcriber(store,media).run(p,[a['id']])
    assert result['state']=='succeeded' and result['errors']==[]
    assert get_readiness(store,p,a['id'])['transcript']['state']=='no_speech'


def test_imported_user_transcript_is_not_overwritten_by_automatic_speech(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from creative_agent.transcription import Transcriber
    import creative_agent.asr_runtime as runtime
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    def denied():raise AssertionError('Explicit user subtitles must be preserved before runtime work')
    monkeypatch.setattr(runtime,'runtime_signature',denied)
    store=Store(tmp_path/'data.db');p=store.create_project('P')['id']
    a=store.put(p,'asset',{'title':'V','media_type':'video','metadata':{'duration_ms':1000,
        'transcript_source':'user_import','subtitles':[{'start_ms':0,'end_ms':1000,'text':'用户提供的台词'}]}})
    result=Transcriber(store,SimpleNamespace()).run(p,[a['id']])
    assert result['cached']==1 and result['errors']==[]
    assert store.get(p,'asset',a['id'])['metadata']['subtitles'][0]['text']=='用户提供的台词'


def test_no_speech_result_cannot_invalidate_a_concurrent_user_correction(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from creative_agent.transcription import Transcriber
    from creative_agent.evidence_policy import transcript_policy
    import creative_agent.asr_runtime as runtime
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setattr(runtime,'runtime_signature',lambda:{'version':'fixture'})
    store=Store(tmp_path/'data.db');p=store.create_project('P')['id']
    a=store.put(p,'asset',{'title':'V','media_type':'video','metadata':{'duration_ms':1000}})
    def corrected(*args,**kwargs):
        fresh=store.get(p,'asset',a['id']);fresh['metadata'].update(transcript_source='user_corrected',
            subtitles=[{'start_ms':0,'end_ms':1000,'text':'人工校正'}],
            transcription={'execution_state':'succeeded','reviewed':True,'quality_state':'usable'})
        store.put(p,'asset',fresh,a['id'])
        raise runtime.NoSpeechDetected('silence')
    monkeypatch.setattr(runtime,'transcribe',corrected)
    result=Transcriber(store,SimpleNamespace(file=lambda *a:tmp_path/'file',ffmpeg='fixture')).run(p,[a['id']])
    assert result['state']=='needs_attention'
    assert transcript_policy(store.get(p,'asset',a['id']))['allowed'] is True


def test_packaged_transcription_ignores_development_model_environment(tmp_path,monkeypatch):
    import sys
    import creative_agent.asr_runtime as runtime
    from creative_agent.transcription import Transcriber
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setenv('CREATIVE_WHISPER_MODEL',str(tmp_path/'old-missing-model'))
    monkeypatch.setattr(runtime,'resource_directory',lambda:tmp_path/'bundled-asr')
    assert Transcriber.model_path()==tmp_path/'bundled-asr'


def test_bundled_ffmpeg_empty_map_name_still_identifies_missing_audio(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import creative_agent.asr_runtime as runtime
    monkeypatch.setattr(runtime,'resource_directory',lambda:tmp_path)
    monkeypatch.setattr(runtime,'_run_process',lambda *args,**kwargs:SimpleNamespace(returncode=234,
        stderr=b"Stream map '' matches no streams.\nFailed to set value '0:a:0' for option 'map'"))
    with pytest.raises(runtime.NoSpeechDetected) as result:
        runtime.transcribe(tmp_path/'silent.mp4',1000,'offline')
    assert result.value.reason=='no_audio'


def test_batch_estimate_does_not_charge_every_video_frame_for_every_request(tmp_path):
    store=Store(tmp_path/'data.db');p=store.create_project('P')['id']
    assets=[store.put(p,'asset',{'title':str(i),'media_type':'video','metadata':{'transcript':'示例台词'}})['id'] for i in range(4)]
    binding={'currency':'CNY','price_input_million':'1','price_output_million':'1','max_output_tokens':4096}
    two=plan_reservation(store,p,binding,{'asset_ids':assets[:2]},3)
    four=plan_reservation(store,p,binding,{'asset_ids':assets},5)
    assert four.amount < two.amount*3

def test_supplier_insufficient_balance_is_actionable_and_never_automatically_retried(tmp_path,monkeypatch):
    import httpx
    import creative_agent.api as api
    from creative_agent.providers import ProviderClient
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(402,json={'error':{'type':'insufficient_balance_error','message':'balance missing; private-key must not be echoed'}})
    class Bounded(ProviderClient):
        def __init__(self,binding,key,**kwargs):
            super().__init__(binding,key,client=httpx.Client(transport=httpx.MockTransport(transport)),**kwargs)
    monkeypatch.setattr(api,'ProviderClient',Bounded)
    app=create_app(tmp_path,token='upgrade',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer upgrade'}) as c:
        p=c.post('/api/projects',json={'name':'P'}).json()['id']
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'offline'}).json()
        result=c.post(f'/api/projects/{p}/actions',json={'binding_id':m['id'],'question':'给一个新的开场建议','discussion_only':True,'idempotency_key':'balance'})
        assert result.status_code==200,result.text
        app.state.execute(p,result.json()['id']);job=app.state.store.job(p,result.json()['id'])
        assert '余额不足' in job['error'] and '服务商' in job['error']
        assert len(calls)==1 and job['actual_cost'] is None and 'private-key' not in str(job)

def test_material_status_uses_the_durable_speech_queue_without_hiding_existing_text(tmp_path):
    from creative_agent.contracts import CostQuote
    from creative_agent.material_readiness import get_readiness
    s=Store(tmp_path/'db');p=s.create_project('P')['id']
    a=s.put(p,'asset',{'title':'V','media_type':'video','inspection':'ready','metadata':{'duration_ms':1000}})
    job=s.create_job(p,'transcription',{'asset_ids':[a['id']]},CostQuote(amount='0',basis='本地识别'),'speech')
    assert get_readiness(s,p,a['id'])['transcript']['state']=='queued'
    s.transition(p,job['id'],'running')
    assert get_readiness(s,p,a['id'])['transcript']['state']=='running'
    s.put(p,'asset',{**a,'metadata':{'duration_ms':1000,'transcript':'用户台词','transcript_source':'user_import','subtitles':[{'start_ms':0,'end_ms':1000,'text':'用户台词'}]}},a['id'])
    assert get_readiness(s,p,a['id'])['transcript']['state']=='ready'


def test_local_check_continuation_starts_explicit_empty_scope_discussion_without_losing_reply(tmp_path,monkeypatch):
    from creative_agent.providers import ProviderClient
    calls=[]
    def answer(self,system,question,**media):
        calls.append(media)
        if self.before_request:self.before_request()
        return {'data':{'summary':'可以先选一条素材，比较它的开头。','findings':[],
            'result':{'comparisons':[],'recommendations':['先选一条素材']}},'usage':{},'request_id':'offline-reply'}
    monkeypatch.setattr(ProviderClient,'chat',answer)
    app=create_app(tmp_path,token='upgrade',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer upgrade'}) as c:
        p=c.post('/api/projects',json={'name':'P'}).json()['id']
        local=c.post(f'/api/projects/{p}/actions',json={'question':'查看我的素材状态','idempotency_key':'local'}).json()
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'offline'}).json()
        body={'kind':'followup','session_id':local['session_id'],'binding_id':m['id'],'question':'下一步该怎么做','idempotency_key':'after-local'}
        job=c.post(f'/api/projects/{p}/actions',json=body)
        assert job.status_code==200,job.text
        app.state.execute(p,job.json()['id'])
        finished=app.state.store.job(p,job.json()['id'])
        assert finished['state']=='succeeded',finished.get('error')
        assert finished['kind']=='analysis' and finished['payload']['local_status_origin_id']==local['session_id']
        result=app.state.store.get(p,'session',finished['result']['record_id'])
        assert result['id']!=local['session_id'] and result['discussion_only'] is True
        assert result['scope']['asset_ids']==[] and '先选一条素材' in result['turns'][0]['answer']['summary']
        assert app.state.store.get(p,'session',local['session_id'])['local_only'] is True
        assert finished['actual_cost'] is None and len(calls)==1 and not calls[0].get('images')
        again=c.post(f'/api/projects/{p}/actions',json=body)
        assert again.json()['id']==finished['id'] and len(calls)==1
