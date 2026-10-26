import json
import pytest
from PIL import Image
from creative_agent.contracts import AnalysisScope
from creative_agent.store import Store
from creative_agent.media import MediaService
from creative_agent.agent import AnalysisAgent, Instructions, Reports


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'db')
    p = store.create_project('test')['id']
    media = MediaService(store, tmp_path / 'media')
    path = tmp_path / 'a.png'
    Image.new('RGB', (8,8)).save(path)
    asset = media.ingest(p, path)
    return store,p,media,asset


class Answer:
    def __init__(self): self.questions=[]
    def chat(self, system, question, **kwargs):
        self.questions.append(question)
        data={'summary': '观察', 'findings': [{'type': 'hypothesis', 'text': '值得测试', 'evidence_ids': []}]}
        if not kwargs:
            task=json.loads(question)['task']
            data['result']={
                'directed': {'comparisons':[], 'recommendations':['验证假设']},
                'exploration': {'patterns':[], 'differences':[], 'opportunities':['验证假设'], 'evidence_gaps':['无投放数据']},
                'inspiration': {'phase':'diverge', 'questions':['产品是什么？'], 'directions':['A','B'], 'script_outline':[], 'brief':None},
            }[task['mode']]
        return {'data':data, 'usage':{}, 'request_id':'test'}


def test_analysis_uses_selected_assets_and_freezes_scope_for_followups(setup):
    store,p,media,asset = setup
    foreign = store.create_project('foreign')['id']
    other = store.put(foreign, 'asset', {'title':'private', 'source_kind':'own'})
    fake = Answer()
    agent = AnalysisAgent(store, media)
    scope = AnalysisScope(project_id=p, asset_ids=[asset['id'], other['id']])
    with pytest.raises(ValueError, match='范围'):
        agent.analyze(scope, 'directed', 'question', fake)
    scope.asset_ids=[asset['id']]
    session=agent.analyze(scope, 'directed', 'question', fake)
    assert session['scope']['asset_ids']==[asset['id']]
    assert session['turns'][0]['evidence'][0]['asset_id']==asset['id']
    agent.followup(p, session['id'], 'continue', fake)
    assert len(store.get(p,'session',session['id'])['turns'])==2
    assert 'private' not in ''.join(fake.questions)


def test_outside_citations_are_rejected(setup):
    store,p,media,asset=setup
    class Bad(Answer):
        def chat(self,*args,**kwargs):
            return {'data': {'summary':'bad','findings':[{'type':'fact','text':'X','evidence_ids':['outside']}]},'usage':{}}
    with pytest.raises(ValueError,match='证据'):
        AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[asset['id']]),'exploration','q',Bad())
    assert store.list(p,'session')==[]


def test_instructions_variables_favorites_and_report_exports(setup):
    store,p,media,asset=setup
    inst=Instructions(store)
    saved=inst.save(p, title='开头分析', template='分析 {{品类}}', variables=['品类'], favorite=True)
    assert inst.render(p,saved['id'],{'品类':'护肤'})=='分析 护肤'
    with pytest.raises(ValueError): inst.render(p,saved['id'],{})
    session=AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[asset['id']]),'inspiration','q',Answer())
    reports=Reports(store)
    note=reports.from_session(p,session['id'], '报告')
    edited=reports.edit(p,note['id'],'修订\n'+note['markdown'])
    assert edited['scope']['asset_ids']==[asset['id']]
    assert reports.export(p,note['id'],'md').startswith('修订'.encode())
    assert reports.export(p,note['id'],'pdf').startswith(b'%PDF')
    assert reports.export(p,note['id'],'xlsx').startswith(b'PK')

def test_all_200_assets_and_groups_reach_synthesis_without_scope_widening(setup):
    store,p,media,asset=setup
    assets=[asset]+[store.put(p,'asset',{**asset,'title':f'asset-{i}'}) for i in range(199)]
    fake=Answer();scope=AnalysisScope(project_id=p,asset_ids=[a['id'] for a in assets])
    groups=[{'title':'A','asset_ids':scope.asset_ids[:100]},{'title':'B','asset_ids':scope.asset_ids[100:]}]
    result=AnalysisAgent(store,media).analyze(scope,'exploration','compare',fake,groups=groups)
    assert len(result['turns'][0]['evidence'])==200
    assert len(fake.questions)==201
    assert all(a['id'] in fake.questions[-1] for a in assets)
    assert result['groups']==groups

def test_concurrent_paid_followups_are_both_retained(setup):
    from threading import Barrier
    from concurrent.futures import ThreadPoolExecutor
    store,p,media,asset=setup;agent=AnalysisAgent(store,media)
    session=agent.analyze(AnalysisScope(project_id=p,asset_ids=[asset['id']]),'directed','initial',Answer())
    barrier=Barrier(2)
    class ConcurrentAnswer(Answer):
        def chat(self,*a,**kw):
            barrier.wait(timeout=3)
            return super().chat(*a,**kw)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda q:agent.followup(p,session['id'],q,ConcurrentAnswer()),['first','second']))
    turns=store.get(p,'session',session['id'])['turns']
    assert len(turns)==3
    assert {t['question'] for t in turns[1:]}=={'first','second'}

def test_instructions_preserve_declared_model_applicability(setup):
    store,p,_,_=setup
    saved=Instructions(store).save(p,title='视频结构',template='分析所选视频结构',models=['kimi-k2.6'],scene='短视频',job='编导')
    assert saved['models']==['kimi-k2.6']


def test_report_retains_structured_mode_results_and_turn_snapshot(setup):
    store,p,media,a=setup
    result={'phase':'converge','questions':[],'directions':['方向A'],'script_outline':['独特脚本开头','展示可信证明'],'brief':{'objective':'测试产品价值','constraints':['禁止夸大效果'],'hypotheses':['检验留存率']}}
    session=store.put(p,'session',{'mode':'inspiration','scope':{'asset_ids':[a['id']]},'turns':[{'created_at':'source-v1','answer':{'summary':'概要','findings':[],'result':result},'evidence':[]}]})
    reports=Reports(store);note=reports.from_session(p,session['id'],'最终报告')
    text=reports.export(p,note['id'],'md').decode()
    for expected in ['独特脚本开头','禁止夸大效果','检验留存率']:assert expected in text
    assert note['result_snapshot']==result and note['turn_index']==0


def test_observation_cache_reuses_media_but_invalidates_transcript_and_model(setup):
    from types import SimpleNamespace
    store,p,media,asset=setup
    fake=Answer();fake.binding=SimpleNamespace(provider='test',model_id='vision-v1',base_url=None,protocol='openai')
    scope=AnalysisScope(project_id=p,asset_ids=[asset['id']]);agent=AnalysisAgent(store,media)
    agent.analyze(scope,'directed','first',fake)
    second=agent.analyze(scope,'directed','second',fake)
    assert len(fake.questions)==3
    assert second['turns'][0]['evidence'][0]['cache_reused'] is True
    fresh=store.get(p,'asset',asset['id']);fresh['metadata']['transcript']='new evidence';store.put(p,'asset',fresh,asset['id'])
    agent.analyze(scope,'directed','third',fake)
    assert len(fake.questions)==5
    fake.binding.model_id='vision-v2'
    agent.analyze(scope,'directed','fourth',fake)
    assert len(fake.questions)==7
