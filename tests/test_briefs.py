import pytest
from test_agent import setup, Answer
from creative_agent.agent import AnalysisAgent
from creative_agent.contracts import AnalysisScope, GenerationRequest, CostQuote
from creative_agent.workflows import Workflows


def new_session(store,p,media,a):
    s=AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]),'directed','比较开头',Answer())
    return s


def test_brief_freezes_conclusion_and_rejects_foreign_session(setup):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup; session=new_session(store,p,media,a); briefs=Briefs(store)
    b=briefs.create(p,session['id'],0,[0],title='新开头',objective='测试开头',hypotheses=['测试留存'])
    assert b['version']==1 and b['source']['turn_index']==0
    assert b['source']['answer_snapshot']==session['turns'][0]['answer']
    assert b['source']['scope']['asset_ids']==[a['id']]
    foreign=store.create_project('other')['id']
    with pytest.raises(ValueError): briefs.create(foreign,session['id'],0,[0],title='x',objective='x')


def test_brief_revisions_are_immutable_and_stale_edits_rejected(setup):
    from creative_agent.briefs import Briefs
    from creative_agent.store import Conflict
    store,p,media,a=setup; session=new_session(store,p,media,a); service=Briefs(store)
    b=service.create(p,session['id'],0,[0],title='test',objective='A')
    edited=service.revise(p,b['id'],1,title='test',objective='B',constraints=[],hypotheses=['H'],references=[],confirmed=True)
    assert edited['version']==2 and edited['revisions'][0]['objective']=='A'
    assert edited['confirmed']
    with pytest.raises(Conflict): service.revise(p,b['id'],1,title='test',objective='C',constraints=[],hypotheses=[],references=[],confirmed=False)
    assert store.get(p,'brief',b['id'])['objective']=='B'


def test_brief_rejects_references_outside_scope_and_invalid_clip(setup):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup; service=Briefs(store); session=new_session(store,p,media,a)
    b=service.create(p,session['id'],0,[0],title='test',objective='A')
    other=store.put(p,'asset',{**a,'title':'not selected'})
    for ref in ({'asset_id':other['id']},{'asset_id':a['id'],'start_ms':0,'end_ms':999999}):
        with pytest.raises(ValueError): service.revise(p,b['id'],1,title='test',objective='A',constraints=[],hypotheses=[],references=[ref],confirmed=True)


def test_generation_snapshots_confirmed_brief_and_retry_keeps_original_revision(setup):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup;store.update_project(p,budget='20')
    session=new_session(store,p,media,a);service=Briefs(store)
    b=service.create(p,session['id'],0,[0],title='test',objective='A')
    video=store.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':10000}})
    req=GenerationRequest(project_id=p,reference_asset_id=video['id'],reference_duration=5,prompt='A',brief_id=b['id'])
    workflow=Workflows(store,media); quote=CostQuote(amount='1',basis='test')
    with pytest.raises(ValueError,match='确认'): workflow.queue_generation(req,'model',quote,'k')
    b=service.revise(p,b['id'],1,title='test',objective='B',constraints=[],hypotheses=[],references=[],confirmed=True)
    job=workflow.queue_generation(req,'model',quote,'k')[0]
    assert job['payload']['brief_snapshot']['version']==2
    service.revise(p,b['id'],2,title='test',objective='C',constraints=[],hypotheses=[],references=[],confirmed=False)
    retried=workflow.queue_generation(req,'model',quote,'k')[0]
    assert retried['id']==job['id'] and retried['payload']['brief_snapshot']['objective']=='B'
    foreign=store.create_project('other',budget='10')['id'];v=store.put(foreign,'asset',{**video,'project_id':foreign})
    with pytest.raises(ValueError): workflow.queue_generation(req.model_copy(update={'project_id':foreign,'reference_asset_id':v['id']}),'model',quote,'bad')
    assert store.budget(foreign)['reserved']=='0'


def test_review_returns_creative_lineage_without_causal_claim(setup):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup;session=new_session(store,p,media,a)
    b=Briefs(store).create(p,session['id'],0,[0],title='test',objective='A')
    asset=store.put(p,'asset',{**a,'brief_id':b['id'],'brief_version':1,'generation_job_id':'local-job'})
    result=Workflows(store,media).review(p,[asset['id']])
    assert result['lineage'][0]['session_id']==session['id']
    assert result['lineage'][0]['brief_version']==1
    assert '不能证明' in result['interpretation']


def test_generation_uses_frozen_brief_in_supplier_prompt(setup,monkeypatch):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup;store.update_project(p,budget='20')
    session=new_session(store,p,media,a);service=Briefs(store)
    b=service.create(p,session['id'],0,[0],title='test',objective='已确认目标')
    b=service.revise(p,b['id'],1,title='test',objective='已确认目标',constraints=['保留结构'],hypotheses=['检验钩子'],references=[],confirmed=True)
    video=store.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':10000}})
    req=GenerationRequest(project_id=p,reference_asset_id=video['id'],reference_duration=5,prompt='补充要求',brief_id=b['id'])
    workflow=Workflows(store,media);job=workflow.queue_generation(req,'model',CostQuote(amount='1',basis='test'),'k')[0]
    service.revise(p,b['id'],2,title='test',objective='尚未确认新版',constraints=[],hypotheses=[],references=[],confirmed=False)
    monkeypatch.setattr(media,'clip',lambda *a: 'local.mp4')
    class Supplier:
        def submit_video(self,prompt,*args,**kwargs):
            assert '已确认目标' in prompt and '保留结构' in prompt and '检验钩子' in prompt
            assert '尚未确认新版' not in prompt
            return {'request_id':'local-contract'}
    assert workflow.run_generation(p,job['id'],Supplier())['state']=='submitted'


def test_converged_inspiration_brief_retains_constraints_hypotheses_and_script(setup,monkeypatch):
    from creative_agent.briefs import Briefs
    store,p,media,a=setup;store.update_project(p,budget='20')
    result={'phase':'converge','questions':[],'directions':['A'],'script_outline':['提出痛点','演示产品'],'brief':{'objective':'检验新结构','constraints':['禁止夸大效果'],'hypotheses':['检验前三秒留存']}}
    session=store.put(p,'session',{'mode':'inspiration','scope':{'asset_ids':[a['id']]},'turns':[{'answer':{'summary':'概要','findings':[{'type':'hypothesis','text':'比较转化','evidence_ids':[]}],'result':result},'evidence':[]}]})
    service=Briefs(store);b=service.create(p,session['id'],0,[0],title='方案A',objective='检验新结构')
    assert b['constraints']==['禁止夸大效果']
    assert b['hypotheses']==['检验前三秒留存','比较转化']
    assert b['script_outline']==['提出痛点','演示产品']
    b=service.revise(p,b['id'],1,title=b['title'],objective=b['objective'],constraints=b['constraints'],hypotheses=b['hypotheses'],references=[],confirmed=True)
    assert b['script_outline']==['提出痛点','演示产品']
    v=store.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':10000}})
    req=GenerationRequest(project_id=p,reference_asset_id=v['id'],reference_duration=5,prompt='制作',brief_id=b['id'])
    workflow=Workflows(store,media);job=workflow.queue_generation(req,'model',CostQuote(amount='1',basis='test'),'script')[0]
    monkeypatch.setattr(media,'clip',lambda *a:'local.mp4')
    class Supplier:
        def submit_video(self,prompt,*args,**kwargs):
            assert all(t in prompt for t in ['提出痛点','禁止夸大效果','检验前三秒留存'])
            return {'request_id':'local-contract'}
    assert workflow.run_generation(p,job['id'],Supplier())['state']=='submitted'
