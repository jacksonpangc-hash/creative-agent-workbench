"""Regression: retirement, local speech before inference, reusable instructions."""
import pytest
from fastapi.testclient import TestClient
from creative_agent.api import create_app
from creative_agent.store import Store
from creative_agent.contracts import CostQuote

class Keys:
 def __init__(self): self.items={}
 def set(self,k,v): self.items[k]=v
 def get(self,k): return self.items.get(k)
 def delete(self,k): self.items.pop(k,None)
H={'Authorization':'Bearer convergence-test'}
@pytest.fixture
def setup(tmp_path):
 app=create_app(tmp_path,token='convergence-test',secret_store=Keys(),run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  p=c.post('/api/projects',headers=H,json={'name':'test','budget':'10'}).json()['id']
  yield app,c,p

def test_generation_cannot_be_configured_or_replayed(setup):
 app,c,p=setup
 r=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'minimax','role':'video_generation','label':'old','model_id':'MiniMax-Hailuo-2.3','api_key':'offline-test'})
 assert r.status_code==410,r.text
 j=app.state.store.create_job(p,'generation',{'legacy':True},CostQuote(amount='1',basis='old'),'old')
 assert c.post(f'/api/projects/{p}/jobs/{j["id"]}/resume',headers=H).status_code==410
 app.state.execute(p,j['id'])
 assert app.state.store.job(p,j['id'])['state']=='cancelled'
 assert app.state.store.job(p,j['id'])['actual_cost']=='0'

def test_analysis_transcribes_locally_before_first_model_call(setup,monkeypatch):
 from creative_agent.transcription import Transcriber
 from creative_agent.providers import ProviderClient
 app,c,p=setup;s=app.state.store
 a=s.put(p,'asset',{'title':'voice','media_type':'video','source_kind':'own','inspection':'ready','metadata':{'duration_ms':1000}})
 m=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'deepseek','role':'strategy','label':'spoken','model_id':'deepseek-flash','api_key':'offline-test'}).json()
 calls=[]
 def transcribe(self,p,ids,**kwargs):
  calls.append('local');fresh=s.get(p,'asset',ids[0]);fresh['metadata'].update(transcript='绿豆饼',subtitles=[{'start_ms':0,'end_ms':900,'text':'绿豆饼'}],transcription={'method':'whisper.cpp-local','execution_state':'succeeded','quality_state':'usable','reviewed':False});s.put(p,'asset',fresh,fresh['id']);return {'id':'t','state':'succeeded','errors':[]}
 def chat(self,*args,**kwargs):
  calls.append('model');return {'data':{'summary':'观察','findings':[],'next_tests':[],'result':{'comparisons':[],'recommendations':['测试开头']}}}
 monkeypatch.setattr(Transcriber,'run',transcribe);monkeypatch.setattr(ProviderClient,'chat',chat)
 r=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','analysis_kind':'spoken','asset_ids':[a['id']],'question':'比较口播','mode':'directed','quote':'1','binding_id':m['id'],'idempotency_key':'voice'})
 assert r.status_code==200,r.text
 assert calls==[]
 app.state.execute(p,r.json()['id']);assert calls==['local','model']
 assert s.job(p,r.json()['id'])['state']=='succeeded'

def test_templates_remain_separate_from_instructions_without_duplicate_create(setup):
 app,c,p=setup;body={'title':'开头拆解','fields':['开头','卖点']}
 one=c.post(f'/api/projects/{p}/templates',headers=H,json=body);two=c.post(f'/api/projects/{p}/templates',headers=H,json=body)
 assert one.status_code==200 and one.json()['id']==two.json()['id']
 records=c.get(f'/api/projects/{p}/state',headers=H).json()['instructions']
 assert records==[]
 templates=c.get(f'/api/projects/{p}/state',headers=H).json()['templates']
 assert len(templates)==1 and templates[0]['fields']==body['fields']

def test_store_put_if_absent_keeps_edited_note_and_is_scoped(tmp_path):
 s=Store(tmp_path/'test.db');p=s.create_project('A')['id'];q=s.create_project('B')['id']
 one=s.put_if_absent(p,'note',{'markdown':'first'},'identity');s.put(p,'note',{'markdown':'edited'},one['id'])
 assert s.put_if_absent(p,'note',{'markdown':'first'},'identity')['markdown']=='edited'
 with pytest.raises(ValueError):s.put_if_absent(q,'note',{'markdown':'foreign'},'identity')

def test_partial_local_transcription_keeps_result_and_does_not_report_success(setup,monkeypatch):
 from creative_agent.transcription import Transcriber
 app,c,p=setup;s=app.state.store
 a=s.put(p,'asset',{'title':'voice','media_type':'video','source_kind':'own','inspection':'ready','metadata':{'duration_ms':1000}})
 monkeypatch.setattr(Transcriber,'run',lambda self,p,ids,**kwargs:{'id':'t','state':'needs_attention','errors':[{'asset_id':a['id'],'error':'没有音轨'}]})
 r=c.post(f'/api/projects/{p}/transcriptions',headers=H,json={'asset_ids':[a['id']],'idempotency_key':'partial'})
 assert r.status_code==200,r.text
 app.state.execute(p,r.json()['id']);j=s.job(p,r.json()['id'])
 assert j['state']=='failed' and j['result']['errors'][0]['error']=='没有音轨' and j['actual_cost']=='0'

def test_contextual_parse_accepts_mixed_local_and_upstream_evidence(setup,tmp_path):
 from PIL import Image
 app,c,p=setup;s=app.state.store
 image=tmp_path/'local.png';Image.new('RGB',(12,12)).save(image)
 a=app.state.media.ingest(p,image)
 m=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'openai','role':'strategy','label':'vision','model_id':'gpt-4.1','api_key':'offline-test'}).json()
 session=s.put(p,'session',{'mode':'directed','scope':{'project_id':p,'asset_ids':[a['id']]},'turns':[{'answer':{'summary':'mixed','findings':[]},'evidence':[{'id':a['id'],'asset_id':a['id']},{'id':'upstream-evidence','provider':'cmm','text':'素材台词'}]}]})
 r=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'parse','asset_ids':[a['id']],'fields':['开头'],'quote':'1','binding_id':m['id'],'idempotency_key':'mixed','source_session_id':session['id']})
 assert r.status_code==200,r.text
 assert s.job(p,r.json()['id'])['payload']['source_context']['session_id']==session['id']

def test_retired_submitted_generation_can_only_poll_existing_task(setup,monkeypatch):
 from creative_agent.providers import ProviderClient
 app,c,p=setup;s=app.state.store
 j=s.create_job(p,'generation',{'binding_id':'old-model','binding_snapshot':{'project_id':p,'label':'legacy','provider':'minimax','model_id':'MiniMax-Hailuo-2.3','role':'video_generation','key_ref':'old-key'}},CostQuote(amount='1',basis='old'),'already-submitted')
 s.transition(p,j['id'],'running');s.transition(p,j['id'],'submitting');s.transition(p,j['id'],'submitted',provider_request_id='existing-remote-id')
 calls=[]
 monkeypatch.setattr(ProviderClient,'__init__',lambda self,*args,**kwargs:None)
 monkeypatch.setattr(app.state.workflow,'resume_generation',lambda project,jid,client:calls.append((project,jid)))
 app.state.execute(p,j['id'])
 assert calls==[(p,j['id'])]
 assert s.job(p,j['id'])['provider_request_id']=='existing-remote-id'


def test_model_output_limit_keeps_billed_job_pending_and_explains_limit(setup, monkeypatch):
 import httpx
 from creative_agent.providers import ProviderClient
 app,c,p=setup;s=app.state.store
 m=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'deepseek','role':'strategy','label':'bounded','model_id':'deepseek-flash','api_key':'offline-test'}).json()
 calls=[]
 def transport(request):
  calls.append(request)
  return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':''}}],
                                'usage':{'prompt_tokens':5,'completion_tokens':1024}})
 original_init=ProviderClient.__init__
 def offline_client(self,*args,**kwargs):
  kwargs['client']=httpx.Client(transport=httpx.MockTransport(transport))
  original_init(self,*args,**kwargs)
 monkeypatch.setattr(ProviderClient,'__init__',offline_client)
 r=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','discussion_only':True,
   'question':'一起讨论创意','mode':'inspiration','quote':'1','binding_id':m['id'],'idempotency_key':'output-limit'})
 assert r.status_code==200,r.text
 app.state.execute(p,r.json()['id'])
 j=s.job(p,r.json()['id'])
 assert j['state']=='needs_attention' and j['actual_cost'] is None
 assert s.budget(p)['reserved']=='1'
 assert '输出上限' in j['error'] and '可能已计费' in j['error'] and '不会自动重试' in j['error']
 assert s.list(p,'session')==[] and s.list(p,'script')==[] and len(calls)==1
