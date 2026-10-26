import json
import pytest
from PIL import Image
from creative_agent.contracts import GenerationRequest, CostQuote
from creative_agent.store import Store
from creative_agent.media import MediaService
from creative_agent.workflows import Workflows

@pytest.fixture
def setup(tmp_path):
    s=Store(tmp_path/'db'); p=s.create_project('p',budget='50')['id']; m=MediaService(s,tmp_path/'media')
    path=tmp_path/'a.png'; Image.new('RGB',(10,10),'red').save(path); a=m.ingest(p,path)
    return s,p,m,a

class Parser:
    def chat(self,system,question,**kw):
        return {'data':{'fields':{'钩子':'产品特写'},'summary':'解析'},'usage':{}}

def test_custom_parser_retains_evidence_and_missing_fields(setup):
    s,p,m,a=setup; w=Workflows(s,m)
    result=w.parse(p,[a['id']],['钩子','卖点'],Parser())
    row=result['rows'][0]
    assert row['fields']=={'钩子':'产品特写','卖点':None}
    assert row['provenance']=='image'
    assert w.export_parse(p,result['id'],'xlsx').startswith(b'PK')
    with pytest.raises(ValueError): w.parse(p,[a['id']]*101,['钩子'],Parser())

def test_batch_persists_once_and_does_not_resubmit_after_request_id(setup):
    s,p,m,a=setup
    video=s.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':10000}})
    w=Workflows(s,m)
    req=GenerationRequest(project_id=p,reference_asset_id=video['id'],reference_duration=5,batch=2)
    jobs=w.queue_generation(req,'binding',CostQuote(amount='3',basis='user reserve'),'click-1')
    again=w.queue_generation(req,'binding',CostQuote(amount='3',basis='user reserve'),'click-1')
    assert [j['id'] for j in jobs]==[j['id'] for j in again]
    assert s.budget(p)['reserved']=='6'
    s.transition(p,jobs[0]['id'],'running');s.transition(p,jobs[0]['id'],'submitting');s.transition(p,jobs[0]['id'],'submitted',provider_request_id='remote')
    class Client:
        def submit_video(self,*a,**kw): pytest.fail('must not resubmit')
        def poll_video(self,request_id):
            assert request_id=='remote'; return {'state':'succeeded','url':'https://example.org/out.mp4','usage':{}}
    w.resume_generation(p,jobs[0]['id'],Client())
    assert s.job(p,jobs[0]['id'])['state']=='succeeded'
    assert s.job(p,jobs[1]['id'])['state']=='queued'

def test_scene_clusters_count_source_assets_once_and_review_keeps_missing(setup):
    s,p,m,a=setup
    w=Workflows(s,m)
    # Pixel fingerprints alone never qualify as semantic observations.
    assert w.clusters(p,source_kinds=['own'])==[]
    result=w.review(p,[a['id']])
    assert result['metrics']['roas']['kind']=='missing'
    assert '相关' in result['interpretation']

def test_100_mixed_asset_parser_retains_every_row_and_observation_mode(setup,monkeypatch):
    s,p,m,a=setup
    ids=[s.put(p,'asset',{**a,'media_type':'video' if i%2 else 'image','title':str(i)})['id'] for i in range(100)]
    monkeypatch.setattr(m,'frames',lambda *args:[{'path':a['path'],'time_ms':0}])
    result=Workflows(s,m).parse(p,ids,['钩子'],Parser())
    assert len(result['rows'])==100
    assert {r['provenance'] for r in result['rows']}=={'image','sampled_frames'}

def test_cancel_during_submission_keeps_returned_remote_id(setup,monkeypatch):
    s,p,m,a=setup
    v=s.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':10000}})
    w=Workflows(s,m);req=GenerationRequest(project_id=p,reference_asset_id=v['id'],reference_duration=5)
    j=w.queue_generation(req,'model',CostQuote(amount='2',basis='test'),'cancel-race')[0]
    monkeypatch.setattr(m,'clip',lambda *args: a['path'])
    class Client:
        def submit_video(self,*args,**kw):
            s.cancel(p,j['id'])
            return {'request_id':'billable-task','state':'submitted'}
        def cancel_video(self,rid): assert rid=='billable-task';return {'state':'cancelling'}
    result=w.run_generation(p,j['id'],Client())
    assert result['provider_request_id']=='billable-task'
    assert result['state']=='cancelling'


def test_reasoning_observes_selected_clip_before_submission(setup,monkeypatch):
    s,p,m,a=setup
    v=s.put(p,'asset',{**a,'media_type':'video','metadata':{'duration_ms':100000}})
    w=Workflows(s,m);req=GenerationRequest(project_id=p,reference_asset_id=v['id'],reference_start_ms=41500,reference_duration=2,mode='reasoning')
    j=w.queue_generation(req,'video',CostQuote(amount='2',basis='test'),'reasoning',strategy_binding_id='strategy')[0]
    monkeypatch.setattr(m,'clip',lambda *args: a['path'])
    monkeypatch.setattr(m,'frames',lambda *args:[{'path':a['path'],'time_ms':0},{'path':a['path'],'time_ms':50000}])
    monkeypatch.setattr(m,'frames_for_interval',lambda *args:[{'path':a['path'],'time_ms':41500}],raising=False)
    class Strategy:
        def chat(self,system,prompt,**kw):
            assert kw['video']==a['path']
            assert kw['images']==[]
            return {'data':{'prompt':'reasoned draft'}}
    class Video:
        def submit_video(self,prompt,images,clip,**kw):
            assert prompt=='reasoned draft'
            assert clip==a['path']
            return {'request_id':'generated'}
    result=w.run_generation(p,j['id'],Video(),Strategy())
    assert result['state']=='submitted'
    assert s.events(p,j['id'])[-2]['type']=='state'

def test_parsed_observations_are_available_for_real_search_indexing(setup):
    from creative_agent.media import SearchService
    from creative_agent.contracts import AnalysisScope
    s,p,m,a=setup
    Workflows(s,m).parse(p,[a['id']],['钩子'],Parser())
    found=SearchService(s).search(AnalysisScope(project_id=p,discovery=True),'产品特写')
    assert [r['id'] for r in found]==[a['id']]


def test_parse_retains_analysis_context_and_passes_it_to_observer(setup):
    s,p,m,a=setup
    session=s.put(p,'session',{'scope':{'asset_ids':[a['id']]}})
    context={'session_id':session['id'],'turn_index':0,'scope':session['scope'],'answer_snapshot':{'summary':'检查开头卖点'}}
    class ContextParser(Parser):
        def chat(self,system,question,**kw):
            assert '检查开头卖点' in question
            return super().chat(system,question,**kw)
    result=Workflows(s,m).parse(p,[a['id']],['钩子'],ContextParser(),source_context=context)
    assert result['source_context']==context
