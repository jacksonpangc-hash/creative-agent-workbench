from fastapi.testclient import TestClient
from creative_agent.api import create_app
from creative_agent.task_planning import plan_request
from test_refactor_053_budget import Vault

def test_manual_revision_freezes_version_and_cannot_expand_scope(tmp_path):
    app=create_app(tmp_path,token='rea-test',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer rea-test'}) as c:
        p=c.post('/api/projects',json={'name':'P'}).json()['id'];s=app.state.store
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'offline-test'}).json()
        script=s.put(p,'script',{'version':4,'title':'手工稿','objective':'试拍','rows':[{'dialogue':'人工原文','references':[],'own_references':[]}]})
        body={'kind':'analysis','binding_id':m['id'],'question':'换个开头','script_revision':{'script_id':script['id'],'version':4},'scope':{'project_id':p,'asset_ids':['not-authorized']},'idempotency_key':'manual'}
        r=c.post(f'/api/projects/{p}/actions',json=body);assert r.status_code==200,r.text
        payload=s.job(p,r.json()['id'])['payload']
        assert payload['script_revision_snapshot']['rows'][0]['dialogue']=='人工原文'
        assert payload['scope']['asset_ids']==[] and payload['task_spec']['output_type']=='scripts'
        assert payload['task_spec']['count']==1
        assert s.get(p,'script',script['id'])['version']==4
        other=c.post('/api/projects',json={'name':'other'}).json()['id']
        assert c.post(f'/api/projects/{other}/actions',json={**body,'idempotency_key':'outside'}).status_code==404

def test_followup_plan_does_not_inherit_ten_script_requirement(tmp_path):
    from creative_agent.store import Store
    s=Store(tmp_path/'test.db');p=s.create_project('P')['id']
    session=s.put(p,'session',{'scope':{'asset_ids':[]},'turns':[{'task_spec':{'output_type':'scripts','count':10}}]})
    result=plan_request(s,p,{'kind':'followup','session_id':session['id'],'question':'只比较这几个开头，不要写脚本'})
    assert result['output_type']=='comparison' and result['count']==1

def test_scoped_read_routes_and_offline_library_require_current_session(tmp_path):
    app=create_app(tmp_path,token='rea-test',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as c:
        h={'Authorization':'Bearer rea-test'}
        p=c.post('/api/projects',headers=h,json={'name':'P'}).json()['id']
        for suffix in ['activity','assets?limit=2','outcomes','patterns?query=食品']:
            assert c.get(f'/api/projects/{p}/{suffix}').status_code==401
            r=c.get(f'/api/projects/{p}/{suffix}',headers=h);assert r.status_code==200,r.text
        data=c.get(f'/api/projects/{p}/patterns?market=US&category=beauty-personal-care',headers=h).json()
        assert data['total']==2 and all(row['metrics_status']=='not_provided' for row in data['items'])
        assert c.get(f'/api/projects/{p}/assets?limit=101',headers=h).status_code in {400,422}

def test_local_media_ticket_streams_ranges_and_rechecks_media_identity(tmp_path):
    import io
    from PIL import Image
    app=create_app(tmp_path,token='rea-test',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as c:
        h={'Authorization':'Bearer rea-test'};p=c.post('/api/projects',headers=h,json={'name':'P'}).json()['id']
        image=io.BytesIO();Image.new('RGB',(16,16)).save(image,format='PNG')
        aid=c.post(f'/api/projects/{p}/assets',headers=h,files={'file':('a.png',image.getvalue(),'image/png')}).json()['id']
        r=c.post(f'/api/projects/{p}/media-ticket/{aid}',headers=h,json={});assert r.status_code==200,r.text
        url=r.json()['path'];assert 'rea-test' not in url and '/media-streams/' in url
        stream=c.get(url,headers={'Range':'bytes=0-7'});assert stream.status_code==206 and stream.content==image.getvalue()[:8]
        assert c.head(url).status_code==200
        assert c.get(url+'/extra').status_code==401
        assert c.get(url,headers={'Origin':'https://evil.example'}).status_code==403
        original=app.state.store.get(p,'asset',aid);app.state.store.put(p,'asset',{**original,'sha256':'changed'},aid)
        assert c.get(url).status_code in {400,409}
        assert c.get('/api/media-streams/unknown').status_code==400

from test_parse_continuity import parsed_env  # noqa: E402,F401

def test_parse_continuation_queue_reuses_only_selected_current_evidence(tmp_path,parsed_env):
    store,_licensing,p,_q,asset,parsed=parsed_env
    app=create_app(tmp_path/'runtime',token='rea-test',secret_store=Vault(),run_jobs=False,store_override=store)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer rea-test'}) as c:
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'offline-test'}).json()
        body={'kind':'analysis','binding_id':m['id'],'question':'参考所选内容写一条脚本','parse_context':{'parse_id':parsed['id'],'version':2},'scope':{'project_id':p,'asset_ids':[asset['id']],'selected_segments':[{'asset_id':asset['id'],'start_ms':0,'end_ms':1000}]},'idempotency_key':'parse-continue'}
        r=c.post(f'/api/projects/{p}/actions',json=body);assert r.status_code==200,r.text
        payload=store.job(p,r.json()['id'])['payload']
        import json
        assert 'OUTSIDE_' not in json.dumps(payload['parse_context_snapshot'])
        assert payload['max_model_requests']==1
        assert c.post(f'/api/projects/{p}/actions',json={**body,'parse_context':{'parse_id':parsed['id'],'version':1},'idempotency_key':'stale'}).status_code==400

def test_cached_parse_agent_uses_existing_observation_without_new_media_processing(parsed_env):
    from creative_agent.agent import AnalysisAgent
    from creative_agent.contracts import AnalysisScope
    from creative_agent.parse_continuity import prepare_parse_continuation
    from test_rea_task_contracts import LocalReply
    store,licensing,p,_q,asset,parsed=parsed_env
    cached=prepare_parse_continuation(store,licensing,p,parsed['id'],2,[asset['id']],[{'asset_id':asset['id'],'start_ms':0,'end_ms':1000}])
    agent=AnalysisAgent(store,None)
    def unexpected(*args,**kwargs):raise AssertionError('must not reobserve cached media')
    agent.observe=unexpected
    client=LocalReply({'summary':'所选开头','findings':[],'content':'解释所选开头'})
    result=agent.analyze(AnalysisScope.model_validate(cached['scope']),'directed','只解释所选片段',client,cached_parse=cached)
    import json
    payload=json.dumps(client.payloads[-1])
    assert 'INSIDE_VISUAL' in payload and 'OUTSIDE_' not in payload
    assert result['turns'][0]['parse_context']=={'parse_id':parsed['id'],'version':2}

def test_manual_revision_cannot_replace_original_product_with_current_project_product(tmp_path):
    from creative_agent.agent import AnalysisAgent
    from creative_agent.contracts import AnalysisScope
    from creative_agent.scripts import Scripts
    from test_rea_task_contracts import LocalReply
    from creative_agent.store import Store
    import pytest
    store=Store(tmp_path/'db');p=store.create_project('P')['id'];scripts=Scripts(store)
    scripts.save_facts(p,0,{'product':'商品B','product_id':'B','sku_id':'','claims':[],'constraints':[]})
    original=store.put(p,'script',{'version':1,'title':'商品A稿','product_id':'A','sku_id':'','objective':'试拍','rows':[{'dialogue':'A原稿','visual':'A产品'}]})
    client=LocalReply()
    with pytest.raises(ValueError,match='商品|产品'):
        AnalysisAgent(store,None).analyze(AnalysisScope(project_id=p),'inspiration','换个开头',client,discussion_only=True,script_context=original)
    assert not client.payloads
