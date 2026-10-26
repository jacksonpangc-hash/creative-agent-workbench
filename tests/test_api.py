import io
from PIL import Image
from fastapi.testclient import TestClient
from creative_agent.api import create_app

class Keys:
    def __init__(self): self.data={}
    def set(self,k,v): self.data[k]=v
    def get(self,k): return self.data.get(k)
    def delete(self,k): self.data.pop(k,None)

def client(tmp_path):
    app=create_app(tmp_path,token='test-session',secret_store=Keys(),run_jobs=False)
    return TestClient(app,base_url='http://127.0.0.1:8765')

H={'Authorization':'Bearer test-session'}

def test_loopback_auth_origin_and_export(tmp_path):
    with client(tmp_path) as c:
        assert c.get('/api/projects').status_code==401
        assert c.get('/api/projects',headers={**H,'Host':'evil.example'}).status_code==403
        assert c.post('/api/projects',headers={**H,'Origin':'https://evil.example'},json={'name':'x'}).status_code==403
        p=c.post('/api/projects',headers=H,json={'name':'产品','budget':'20'}).json()['id']
        saved=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'deepseek','label':'策略','model_id':'deepseek-flash','role':'strategy','api_key':'never-export-this'}).json()
        assert saved['has_key'] and 'api_key' not in saved
        assert 'never-export-this' not in c.get(f'/api/projects/{p}/export',headers=H).text
        assert c.get(f'/api/projects/{p}/state',headers=H).json()['models'][0]['has_key']
        invalid=c.post(f'/api/projects/{p}/models',headers=H,json={'api_key':'never-export-this'})
        assert invalid.status_code==422 and 'never-export-this' not in invalid.text


def test_upload_is_project_scoped_and_no_path_is_exposed(tmp_path):
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p'}).json()['id']
        q=c.post('/api/projects',headers=H,json={'name':'q'}).json()['id']
        image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
        response=c.post(f'/api/projects/{p}/assets',headers=H,files={'file':('../../outside.png',image.getvalue(),'image/png')})
        assert response.status_code==200
        a=response.json();assert 'path' not in a and 'poster_path' not in a
        assert c.get(f'/api/projects/{q}/assets/{a["id"]}/file',headers=H).status_code==404
        assert c.get(f'/api/projects/{p}/assets/{a["id"]}/file',headers=H).headers['content-type']=='image/png'
        assert c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','question':'q','asset_ids':[a['id']],'quote':'1','binding_id':'absent','idempotency_key':'missing-binding'}).status_code==404

def test_unsupported_generation_rejected_before_reserving(tmp_path):
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p','budget':'10'}).json()['id']
        # A legacy config may predate save-time eligibility checks.
        b=c.app.state.store.put(p,'model',{'provider':'google','label':'future','model_id':'unsupported-test','role':'video_generation','protocol':'google','has_key':True})['id']
        response=c.post(f'/api/projects/{p}/generation',headers=H,json={'binding_id':b,'quote':'1','idempotency_key':'unsupported','request':{'project_id':p,'reference_asset_id':'absent'}})
        assert response.status_code==410
        assert c.get(f'/api/projects/{p}/state',headers=H).json()['budget']['reserved']=='0'

def test_body_limits_reject_before_parsing(tmp_path):
    with client(tmp_path) as c:
        response=c.post('/api/projects',headers={**H,'Content-Length':str(3000000),'Content-Type':'application/json'},content=b'{}')
        assert response.status_code==413
        response=c.post('/api/projects',headers={**H,'Content-Type':'application/json'},content=(b' ' * 1000000 for _ in range(3)))
        assert response.status_code==413

def test_queued_task_keeps_original_model_and_credential_version(tmp_path):
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p','budget':'10'}).json()['id']
        body={'provider':'minimax','label':'model','model_id':'MiniMax-M3','role':'strategy','api_key':'original-key'}
        b=c.post(f'/api/projects/{p}/models',headers=H,json=body).json()['id']
        result=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'parse','binding_id':b,'asset_ids':['not-here'],'quote':'1','idempotency_key':'snapshot'})
        # Valid scope is needed for queue; ingest actual owned image.
        image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
        aid=c.post(f'/api/projects/{p}/assets',headers=H,files={'file':('a.png',image.getvalue(),'image/png')}).json()['id']
        j=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'parse','binding_id':b,'asset_ids':[aid],'fields':['test'],'quote':'1','idempotency_key':'snapshot'}).json()
        c.post(f'/api/projects/{p}/models',headers=H,json={**body,'id':b,'model_id':'MiniMax-M3.1-Flash-Preview','api_key':'new-key'})
        stored=c.app.state.store.job(p,j['id'])
        assert stored['payload']['binding_snapshot']['model_id']=='MiniMax-M3'
        assert stored['payload']['binding_snapshot']['secret_ref']!=c.app.state.store.get(p,'model',b)['secret_ref']
        assert c.delete(f'/api/projects/{p}/models/{b}',headers=H).status_code==409

def test_billable_requests_require_caller_idempotency_key(tmp_path):
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p'}).json()['id']
        assert c.post(f'/api/projects/{p}/actions',headers=H,json={'binding_id':'x','quote':'1'}).status_code==422

def test_mixed_native_input_is_preflight_rejected_without_reservation(tmp_path):
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p','budget':'10'}).json()['id']
        b=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'deepseek','label':'vision','model_id':'deepseek-flash','role':'strategy','api_key':'key'}).json()['id']
        image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
        a=c.post(f'/api/projects/{p}/assets',headers=H,files={'file':('a.png',image.getvalue(),'image/png')}).json()
        # Distinct metadata kinds exercise preflight; no provider or FFmpeg call is made.
        s=c.app.state.store;raw=s.get(p,'asset',a['id']);v=s.put(p,'asset',{**raw,'media_type':'video'})
        response=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','binding_id':b,'asset_ids':[a['id'],v['id']],'question':'q','native_video':True,'quote':'1','idempotency_key':'native-mix'})
        assert response.status_code==400
        assert s.jobs(p)==[] and s.budget(p)['reserved']=='0'

def test_generation_history_favorite_is_persistent_and_scoped(tmp_path):
    from creative_agent.contracts import CostQuote
    with client(tmp_path) as c:
        p=c.post('/api/projects',headers=H,json={'name':'p','budget':'10'}).json()['id']
        q=c.post('/api/projects',headers=H,json={'name':'q'}).json()['id']
        j=c.app.state.store.create_job(p,'generation',{},CostQuote(amount='1',basis='test'),'fav')
        assert c.post(f'/api/projects/{p}/jobs/{j["id"]}/favorite',headers=H,json={'favorite':True}).status_code==200
        assert j['id'] in c.get(f'/api/projects/{p}/state',headers=H).json()['favorites']
        assert c.post(f'/api/projects/{q}/jobs/{j["id"]}/favorite',headers=H,json={'favorite':True}).status_code==404


def test_brief_routes_version_conflicts_and_project_isolation(tmp_path):
    with client(tmp_path) as c:
        store=c.app.state.store
        p=store.create_project('p')['id'];q=store.create_project('q')['id']
        s=store.put(p,'session',{'scope':{'project_id':p,'asset_ids':[]},'turns':[{'created_at':'version1','answer':{'summary':'test','findings':[{'type':'hypothesis','text':'H','evidence_ids':[]}]},'evidence':[]}]})
        url=f'/api/projects/{p}/briefs'
        created=c.post(url,headers=H,json={'session_id':s['id'],'turn_index':0,'finding_indexes':[0],'title':'B','objective':'O'})
        assert created.status_code==200
        b=created.json();assert b['version']==1
        assert c.get(f'/api/projects/{q}/briefs/{b["id"]}',headers=H).status_code==404
        values={'expected_version':1,'title':'B','objective':'changed','constraints':[],'hypotheses':[],'references':[],'confirmed':True}
        assert c.put(url+'/'+b['id'],headers=H,json=values).json()['version']==2
        assert c.put(url+'/'+b['id'],headers=H,json=values).status_code==409
        assert c.get(f'/api/projects/{p}/state',headers=H).json()['briefs'][0]['version']==2


def test_parse_handoff_is_project_scoped_and_frozen(tmp_path):
    with client(tmp_path) as c:
        store=c.app.state.store;p=store.create_project('p',budget='10')['id'];q=store.create_project('q')['id']
        image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
        a=c.post(f'/api/projects/{p}/assets',headers=H,files={'file':('a.png',image.getvalue(),'image/png')}).json()
        s=store.put(p,'session',{'scope':{'asset_ids':[a['id']]},'turns':[{'answer':{'summary':'source'},'evidence':[{'asset_id':a['id']}]}]})
        foreign=store.put(q,'session',{'turns':[]})
        model=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'minimax','label':'test','model_id':'MiniMax-M3','role':'image_input','api_key':'test-only'}).json()
        payload={'kind':'parse','binding_id':model['id'],'asset_ids':[a['id']],'fields':['钩子'],'quote':'1','idempotency_key':'handoff','source_session_id':s['id'],'source_turn_index':0}
        result=c.post(f'/api/projects/{p}/actions',headers=H,json=payload)
        assert result.status_code==200
        assert result.json()['payload']['source_context']['answer_snapshot']['summary']=='source'
        bad=c.post(f'/api/projects/{p}/actions',headers=H,json={**payload,'idempotency_key':'bad','source_session_id':foreign['id']})
        assert bad.status_code==404


def test_archived_generation_keeps_frozen_brief_version_for_review(tmp_path,monkeypatch):
    from creative_agent.contracts import CostQuote
    with client(tmp_path) as c:
        store=c.app.state.store;p=store.create_project('p',budget='10')['id']
        image=tmp_path/'out.png';Image.new('RGB',(8,8)).save(image)
        a=c.app.state.media.ingest(p,image)
        origin=store.put(p,'session',{'scope':{'project_id':p,'asset_ids':[a['id']]},'turns':[]})
        brief=store.put(p,'brief',{'version':3,'source':{'session_id':origin['id'],'turn_index':1}})
        job=store.create_job(p,'generation',{'brief_snapshot':{**brief,'version':2}},CostQuote(amount='1',basis='test'),'archive')
        store.transition(p,job['id'],'running');store.transition(p,job['id'],'submitting');store.transition(p,job['id'],'submitted',provider_request_id='local-fixture')
        store.finish(p,job['id'],result={'url':'https://example.org/output.mp4'})
        monkeypatch.setattr(c.app.state.media,'download',lambda *args,**kwargs:a)
        result=c.post(f'/api/projects/{p}/jobs/{job["id"]}/archive',headers=H,json={'rights_confirmed':True})
        assert result.status_code==200
        assert result.json()['brief_version']==2
        review=c.post(f'/api/projects/{p}/review',headers=H,json={'asset_ids':[a['id']]}).json()
        assert review['lineage'][0]['brief_id']==brief['id']
        assert review['lineage'][0]['session_id']==origin['id']


def test_spoken_auto_routes_text_model_and_freezes_intent(tmp_path):
    with client(tmp_path) as c:
        s=c.app.state.store;p=s.create_project('p',budget='10')['id']
        image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG')
        a=c.post(f'/api/projects/{p}/assets',headers=H,files={'file':('a.png',image.getvalue(),'image/png')}).json()
        c.post(f'/api/projects/{p}/assets/{a["id"]}/subtitles',headers=H,json={'text':'1\n00:00:00,000 --> 00:00:01,000\n真实的产品介绍'})
        m=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'minimax','label':'口播','model_id':'MiniMax-M2.7','role':'strategy','api_key':'fixture'})
        assert m.status_code==200
        body={'kind':'analysis','analysis_kind':'spoken','question':'分析话术','asset_ids':[a['id']],'quote':'1','idempotency_key':'spoken'}
        result=c.post(f'/api/projects/{p}/actions',headers=H,json=body)
        assert result.status_code==200,result.text
        j=s.job(p,result.json()['id'])
        assert j['payload']['binding_id']==m.json()['id']
        assert j['payload']['analysis_kind']=='spoken'
        assert j['payload']['native_video'] is False
        assert c.post(f'/api/projects/{p}/actions',headers=H,json={**body,'analysis_kind':'video','idempotency_key':'visual'}).status_code==400
        assert len(s.jobs(p))==1


def test_spoken_missing_transcript_fails_without_reservation(tmp_path):
    with client(tmp_path) as c:
        s=c.app.state.store;p=s.create_project('p',budget='10')['id']
        a=s.put(p,'asset',{'title':'missing transcript','media_type':'video','source_kind':'own','metadata':{}})
        c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'minimax','label':'口播','model_id':'MiniMax-M3','role':'strategy','api_key':'fixture'})
        r=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','analysis_kind':'spoken','question':'口播','asset_ids':[a['id']],'quote':'1','idempotency_key':'missing-text'})
        assert r.status_code==400 and ('文字稿' in r.text or '字幕' in r.text)
        assert s.budget(p)['reserved']=='0'


def test_explicit_discussion_needs_no_assets_but_freezes_empty_scope(tmp_path):
    with client(tmp_path) as c:
        s=c.app.state.store;p=s.create_project('p',budget='10')['id']
        c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'minimax','label':'思考','model_id':'MiniMax-M3','role':'strategy','api_key':'fixture'})
        body={'kind':'analysis','discussion_only':True,'question':'构思方向','mode':'inspiration','quote':'1','idempotency_key':'discussion'}
        r=c.post(f'/api/projects/{p}/actions',headers=H,json=body)
        assert r.status_code==200,r.text
        assert s.job(p,r.json()['id'])['payload']['scope']['asset_ids']==[]
        assert c.post(f'/api/projects/{p}/actions',headers=H,json={**body,'discussion_only':False,'idempotency_key':'empty'}).status_code==400


def test_new_model_reuses_provider_key_only_within_project_and_endpoint(tmp_path):
    with client(tmp_path) as c:
        s=c.app.state.store;p=s.create_project('p')['id'];q=s.create_project('q')['id']
        body={'provider':'minimax','label':'视频','model_id':'MiniMax-M3','role':'strategy','api_key':'fixture-secret'}
        first=c.post(f'/api/projects/{p}/models',headers=H,json=body).json()
        second=c.post(f'/api/projects/{p}/models',headers=H,json={**body,'label':'口播','model_id':'MiniMax-M2.7','api_key':''})
        assert second.status_code==200 and second.json()['has_key'] is True
        assert s.get(p,'model',first['id'])['secret_ref']!=s.get(p,'model',second.json()['id'])['secret_ref']
        foreign=c.post(f'/api/projects/{q}/models',headers=H,json={**body,'api_key':''})
        assert foreign.status_code==400
        assert c.delete(f'/api/projects/{p}/models/{first["id"]}',headers=H).status_code==200
        assert c.get(f'/api/projects/{p}/state',headers=H).json()['models'][0]['has_key'] is True


def test_script_api_uses_server_actor_and_rejects_version_conflicts(tmp_path):
    with client(tmp_path) as c:
        s=c.app.state.store;p=s.create_project('p')['id'];q=s.create_project('q')['id']
        body={'title':'脚本','objective':'试拍','rows':[{'start_ms':0,'end_ms':3000,'dialogue':'开头','reshoot':'补拍开场动作'}]}
        r=c.post(f'/api/projects/{p}/scripts',headers=H,json=body)
        assert r.status_code==200,r.text
        script=r.json();url=f'/api/projects/{p}/scripts/{script["id"]}'
        assert c.put(url,headers=H,json={**body,'expected_version':1,'confirmed':True}).json()['version']==2
        assert c.put(url,headers=H,json={**body,'expected_version':1}).status_code==409
        assert c.get(f'/api/projects/{q}/scripts/{script["id"]}',headers=H).status_code==404
        comment=c.post(url+'/comments',headers=H,json={'version':2,'text':'已检查','verdict':'approved'})
        assert comment.json()['actor_id']=='local'
        assert c.post(url+'/comments',headers=H,json={'version':2,'text':'冒用','actor_id':'admin'}).status_code==422
        assert c.get(f'/api/projects/{p}/scripts',headers=H).json()[0]['id']==script['id']
        task=c.post(url+'/production',headers=H,json={'version':2,'assignee':'我','idempotency_key':'make'})
        assert task.status_code==200
        assert c.get(f'/api/projects/{p}/production',headers=H).json()[0]['script_snapshot']['version']==2


def test_team_profile_requires_authenticated_outer_middleware_and_store(tmp_path):
    import pytest
    from creative_agent.store import Store
    with pytest.raises(ValueError): create_app(tmp_path/'bad',security_profile='team',run_jobs=False)
    shared=Store(tmp_path/'shared.sqlite3')
    app=create_app(tmp_path/'team',store_override=shared,security_profile='team',secret_store=Keys(),run_jobs=False)
    assert app.state.store is shared and callable(app.state.execute)
    with TestClient(app,base_url='https://team.example') as c:
        assert c.get('/api/projects',headers=H).status_code==401
    app2=create_app(tmp_path/'team2',store_override=shared,security_profile='team',secret_store=Keys(),run_jobs=False)
    @app2.middleware('http')
    async def authenticated(request,call_next):
        request.state.team_authorized=True
        request.state.actor_id='verified-subject'
        return await call_next(request)
    with TestClient(app2,base_url='https://team.example') as c:
        assert c.get('/api/projects').status_code==200


def test_transcription_queue_is_local_free_with_bundled_model(tmp_path,monkeypatch):
    from creative_agent.transcription import Transcriber
    app=create_app(tmp_path/'data',token='test',run_jobs=False)
    client=TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer test'})
    p=app.state.store.create_project('p')['id']
    a=app.state.store.put(p,'asset',{'media_type':'video','source_kind':'own','metadata':{'duration_ms':1000}})
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setattr(Transcriber,'model_path',lambda:tmp_path)
    body={'asset_ids':[a['id']],'idempotency_key':'asr'}
    response=client.post(f'/api/projects/{p}/transcriptions',json=body)
    assert response.status_code==200,response.text
    assert response.json()['kind']=='transcription'
    assert response.json()['quote']['amount']=='0'


def test_retired_industry_selection_cannot_create_a_new_media_import(tmp_path):
    with client(tmp_path) as c:
        store=c.app.state.store;p=store.create_project('p',budget='10')['id']
        from creative_agent.contracts import CostQuote
        source=store.create_job(p,'industry_search',{},CostQuote(amount='0',basis='historical fixture'),'source')
        response=c.post(f'/api/projects/{p}/industry/searches/{source["id"]}/select',headers=H,json={'result_ids':['result'],'reason':'relevant','import_media':True})
        assert response.status_code==400,response.text
        assert len(store.jobs(p))==1
        assert store.job(p,source['id'])['state']=='queued'
