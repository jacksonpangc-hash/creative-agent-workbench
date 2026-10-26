"""Version/lineage tests use real SQLite; no supplier calls."""
import pytest
from creative_agent.store import Store, Conflict, NotFound


@pytest.fixture
def setup(tmp_path):
    from creative_agent.scripts import Scripts
    store=Store(tmp_path/'db');p=store.create_project('p')['id'];q=store.create_project('q')['id']
    return store,Scripts(store),p,q


def content(title='试拍'):
    return {'title':title,'objective':'测试开头','rows':[{'start_ms':0,'end_ms':2000,'dialogue':'先看使用场景','visual':'产品近景','tone':'自然','notes':'','reshoot':'拍摄产品近景','references':[]}],'confirmed':False}


def test_script_revision_conflict_and_production_snapshot(setup):
    store,service,p,q=setup
    script=service.create(p,content())
    with pytest.raises(ValueError,match='确认'):
        service.produce(p,script['id'],1,'task1','负责人')
    confirmed=service.revise(p,script['id'],1,{**content(),'confirmed':True})
    task=service.produce(p,script['id'],2,'task1','负责人')
    assert service.produce(p,script['id'],2,'task1','负责人')['id']==task['id']
    revised=service.revise(p,script['id'],2,content('新版本'))
    assert revised['version']==3
    assert task['script_snapshot']['version']==2 and task['script_snapshot']['title']=='试拍'
    assert confirmed['confirmed']
    with pytest.raises(Conflict): service.revise(p,script['id'],2,content('覆盖他人'))
    with pytest.raises(NotFound): service.get(q,script['id'])


def test_script_references_and_comments_cannot_cross_project_or_version(setup):
    store,service,p,q=setup
    other=store.put(q,'asset',{'metadata':{'duration_ms':2000}})
    body=content();body['rows'][0]['references']=[{'asset_id':other['id']}]
    with pytest.raises(ValueError): service.create(p,body)
    script=service.create(p,content())
    comment=service.comment(p,script['id'],1,'local','修改台词',row_index=0)
    assert comment['script_version']==1 and comment['row_index']==0
    with pytest.raises(ValueError): service.comment(p,script['id'],99,'local','不存在版本')
    with pytest.raises(ValueError): service.comment(p,script['id'],1,'local','不存在行',row_index=2)
    with pytest.raises(NotFound): service.comment(q,script['id'],1,'local','越权')


def test_script_from_session_freezes_source_and_requires_explicit_confirmation(setup):
    store,service,p,q=setup
    session=store.put(p,'session',{'scope':{'project_id':p,'asset_ids':[]},'turns':[{'answer':{'summary':'A','result':{'scripts':[content('完整脚本')]}},'evidence':[],'created_at':'source-time'}]})
    script=service.from_session(p,session['id'],0)
    assert script['confirmed'] is False
    assert [r['dialogue'] for r in script['rows']]==['先看使用场景']
    assert script['source']['answer_snapshot']['summary']=='A'
    assert script['timing_estimated'] is False
    assert script['rows'][0]['end_ms']==2000
    with pytest.raises(NotFound): service.from_session(q,session['id'],0)


def test_product_facts_revision_and_invalid_claims(setup):
    store,service,p,q=setup
    saved=service.save_facts(p,0,{'product':'牙膏','claims':[{'statement':'清洁牙齿','source':'产品说明书','confirmed':True}],'constraints':['禁止编造疗效']})
    assert saved['version']==1
    with pytest.raises(Conflict): service.save_facts(p,0,{'product':'覆盖'})
    with pytest.raises(ValueError): service.save_facts(p,1,{'product':'牙膏','claims':[{'statement':'一周见效','source':'','confirmed':True}]})
    assert service.facts(q)['version']==0


def test_production_completion_requires_local_finished_asset_and_tracks_version(setup):
    store,service,p,q=setup
    script=service.create(p,{**content(),'confirmed':True})
    task=service.produce(p,script['id'],1,'production','创作者')
    foreign=store.put(q,'asset',{'title':'foreign'})
    with pytest.raises(ValueError): service.update_task(p,task['id'],1,'approved',foreign['id'])
    task=service.update_task(p,task['id'],1,'in_progress')
    asset=store.put(p,'asset',{'title':'成片','source_kind':'finished'})
    task=service.update_task(p,task['id'],2,'in_review',asset['id'])
    done=service.update_task(p,task['id'],3,'approved',asset['id'])
    assert done['finished_asset_id']==asset['id']
    assert done['script_snapshot']['version']==1
    with pytest.raises(Conflict): service.update_task(p,task['id'],2,'in_progress')


def test_product_facts_reach_strategy_with_frozen_version(setup,tmp_path):
    from creative_agent.agent import AnalysisAgent
    from creative_agent.contracts import AnalysisScope
    from creative_agent.media import MediaService
    from test_agent import Answer
    import json
    store,service,p,q=setup
    service.save_facts(p,0,{'product':'牙膏','claims':[{'statement':'清洁牙齿','source':'说明书','confirmed':True}]})
    answer=Answer()
    session=AnalysisAgent(store,MediaService(store,tmp_path/'media')).analyze(AnalysisScope(project_id=p),'inspiration','构思',answer,discussion_only=True)
    context=json.loads(answer.questions[-1])['context']
    assert context['product_facts']['product']=='牙膏'
    assert context['product_facts']['version']==1
    assert session['turns'][0]['product_facts_snapshot']['version']==1


def test_review_tracks_manual_production_script_snapshot(setup):
    from creative_agent.workflows import Workflows
    store,service,p,q=setup
    script=service.create(p,{**content(),'confirmed':True})
    task=service.produce(p,script['id'],1,'lineage','我')
    service.update_task(p,task['id'],1,'in_progress')
    asset=store.put(p,'asset',{'title':'成片','source_kind':'finished'})
    service.update_task(p,task['id'],2,'in_review',asset['id'])
    report=Workflows(store,None).review(p,[asset['id']])
    assert report['lineage'][0]['script_id']==script['id']
    assert report['lineage'][0]['script_version']==1


def test_generation_freezes_confirmed_script_version(setup):
    from creative_agent.workflows import Workflows
    from creative_agent.contracts import GenerationRequest,CostQuote
    store,service,p,q=setup
    store.update_project(p,budget='100',currency='CNY')
    asset=store.put(p,'asset',{'media_type':'video','metadata':{'duration_ms':15000}})
    script=service.create(p,content())
    args=dict(project_id=p,reference_asset_id=asset['id'],script_id=script['id'],script_version=1)
    flow=Workflows(store,None)
    with pytest.raises(ValueError,match='确认'):
        flow.queue_generation(GenerationRequest(**args),'model',CostQuote(amount='1',basis='test'),'draft')
    service.revise(p,script['id'],1,{**content(),'confirmed':True})
    args['script_version']=2
    jobs=flow.queue_generation(GenerationRequest(**args),'model',CostQuote(amount='1',basis='test'),'frozen')
    service.revise(p,script['id'],2,content('新版'))
    assert jobs[0]['payload']['script_snapshot']['version']==2
    assert jobs[0]['payload']['script_snapshot']['title']=='试拍'
    assert flow.queue_generation(GenerationRequest(**args),'model',CostQuote(amount='1',basis='test'),'frozen')[0]['id']==jobs[0]['id']
    with pytest.raises(ValueError): GenerationRequest(**{**args,'script_version':None})
    with pytest.raises(NotFound):
        flow.queue_generation(GenerationRequest(**{**args,'project_id':q}),'model',CostQuote(amount='1',basis='test'),'foreign')


def test_generation_reference_url_requires_explicit_segment_confirmation():
    from creative_agent.contracts import GenerationRequest
    with pytest.raises(ValueError,match='片段'):
        GenerationRequest(project_id='p',reference_asset_id='a',reference_video_url='https://cdn.example/clip.mp4')
    request=GenerationRequest(project_id='p',reference_asset_id='a',reference_video_url='https://cdn.example/clip.mp4',reference_url_confirmed=True)
    assert request.reference_url_confirmed
