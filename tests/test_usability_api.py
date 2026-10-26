import io
from PIL import Image
from fastapi.testclient import TestClient
from creative_agent.api import create_app
from creative_agent.contracts import ModelBinding

H={'Authorization':'Bearer local-usability-test'}

def test_background_upload_returns_receipt_before_decoding_and_only_one_job(tmp_path,monkeypatch):
 app=create_app(tmp_path,token='local-usability-test',run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  p=c.post('/api/projects',headers=H,json={'name':'p','budget':'0'}).json()['id']
  image=io.BytesIO();Image.new('RGB',(24,24)).save(image,format='PNG')
  monkeypatch.setattr(app.state.media,'_run',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('decoding before receipt')))
  response=c.post(f'/api/projects/{p}/assets?background=true',headers=H,files={'file':('a.png',image.getvalue(),'image/png')})
  assert response.status_code==200
  a=response.json();assert a['inspection']=='pending' and 'path' not in a
  assert a['processing_job']['state']=='queued'
  again=c.post(f'/api/projects/{p}/assets?background=true',headers=H,files={'file':('a.png',image.getvalue(),'image/png')}).json()
  assert again['id']==a['id']
  assert again['processing_job']['id']==a['processing_job']['id']
  assert len(app.state.store.jobs(p))==1
  app.state.execute(p,a['processing_job']['id'])
  assert app.state.store.get(p,'asset',a['id'])['inspection']=='ready'
  assert app.state.store.job(p,a['processing_job']['id'])['state']=='succeeded'

def test_gpt_video_analysis_freezes_built_in_route_before_reserving(tmp_path):
 import subprocess
 from pathlib import Path
 class Keys:
  def __init__(self):self.items={}
  def set(self,k,v):self.items[k]=v
  def get(self,k):return self.items.get(k)
  def delete(self,k):self.items.pop(k,None)
 app=create_app(tmp_path,token='local-usability-test',secret_store=Keys(),run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  p=c.post('/api/projects',headers=H,json={'name':'p','budget':'10'}).json()['id']
  model=c.post(f'/api/projects/{p}/models',headers=H,json={'provider':'openai','role':'strategy','label':'GPT','model_id':'gpt-4.1','api_key':'test-offline'}).json()
  video=Path(tmp_path)/'a.mp4'
  subprocess.run([app.state.media.ffmpeg,'-y','-loglevel','error','-f','lavfi','-i','color=c=blue:s=80x80:r=4:d=1','-c:v','libx264',str(video)],check=True)
  asset=app.state.media.ingest(p,video)
  r=c.post(f'/api/projects/{p}/actions',headers=H,json={'kind':'analysis','asset_ids':[asset['id']],'question':'比较镜头','mode':'directed','quote':'1','binding_id':model['id'],'idempotency_key':'gpt-video','native_video':True})
  assert r.status_code==200,r.text
  payload=app.state.store.job(p,r.json()['id'])['payload']
  assert payload['video_route']['workflow_id']=='builtin.video-understanding'
  assert payload['video_route']['route']=='sampled_frames'
  assert payload['native_video'] is False

def test_parser_uses_frozen_frame_route_and_actual_timestamps(tmp_path):
 import subprocess
 from creative_agent.workflows import Workflows
 from creative_agent.store import Store
 from creative_agent.media import MediaService
 from creative_agent.video_understanding import plan_video_route
 from creative_agent.contracts import ModelBinding
 s=Store(tmp_path/'parse.sqlite3');p=s.create_project('p')['id'];media=MediaService(s,tmp_path/'media')
 video=tmp_path/'parse.mp4';subprocess.run([media.ffmpeg,'-y','-loglevel','error','-f','lavfi','-i','color=c=blue:s=80x80:r=4:d=1','-c:v','libx264',str(video)],check=True)
 a=media.ingest(p,video)
 class Client:
  binding=ModelBinding(provider='openai',role='strategy',label='GPT',model_id='gpt-4.1')
  def chat(self,*args,images=(),video=None):
   assert images and video is None
   return {'data':{'fields':{'开头':'蓝色'},'scenes':[]}}
 client=Client()
 result=Workflows(s,media).parse(p,[a['id']],['开头'],client,video_route=plan_video_route(client.binding))
 assert result['rows'][0]['provenance']=='sampled_frames'
 assert result['rows'][0]['observed_timestamps_ms']
 assert result['rows'][0]['observation_details']['workflow']['workflow_id']=='builtin.video-understanding'

def test_upload_size_error_is_visible_to_allowed_browser_origin(tmp_path,monkeypatch):
 monkeypatch.setenv('CREATIVE_DEV','1')
 app=create_app(tmp_path,token='local-usability-test',run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  r=c.post('/api/projects/p/assets',headers={**H,'Origin':'http://127.0.0.1:5173','Content-Length':str(1024**3+1048577)},content=b'x')
  assert r.status_code==413
  assert r.headers.get('access-control-allow-origin')=='http://127.0.0.1:5173'

def test_failed_media_can_retry_processing_without_uploading_again(tmp_path):
 app=create_app(tmp_path,token='local-usability-test',run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  p=c.post('/api/projects',headers=H,json={'name':'p','budget':'0'}).json()['id']
  other=c.post('/api/projects',headers=H,json={'name':'other'}).json()['id']
  image=tmp_path/'retry.png';Image.new('RGB',(16,16)).save(image)
  a=app.state.media.stage_upload(p,image);a['inspection']='failed';app.state.store.put(p,'asset',a,a['id'])
  r=c.post(f'/api/projects/{p}/assets/{a["id"]}/process',headers=H,json={'idempotency_key':'explicit-retry'})
  assert r.status_code==200
  same=c.post(f'/api/projects/{p}/assets/{a["id"]}/process',headers=H,json={'idempotency_key':'explicit-retry'})
  assert same.json()['id']==r.json()['id']
  assert c.post(f'/api/projects/{other}/assets/{a["id"]}/process',headers=H,json={'idempotency_key':'cross'}).status_code==404
  app.state.execute(p,r.json()['id'])
  assert app.state.store.get(p,'asset',a['id'])['inspection']=='ready'

def test_cancelled_ingest_keeps_original_and_can_be_explicitly_reprocessed(tmp_path):
 app=create_app(tmp_path,token='local-usability-test',run_jobs=False)
 with TestClient(app,base_url='http://127.0.0.1:8765') as c:
  p=c.post('/api/projects',headers=H,json={'name':'p'}).json()['id']
  b=io.BytesIO();Image.new('RGB',(12,12)).save(b,format='PNG')
  a=c.post(f'/api/projects/{p}/assets?background=true',headers=H,files={'file':('a.png',b.getvalue(),'image/png')}).json()
  c.post(f'/api/projects/{p}/jobs/{a["processing_job"]["id"]}/cancel',headers=H)
  assert app.state.store.get(p,'asset',a['id'])['inspection']=='failed'
  assert app.state.media.file(p,a['id']).read_bytes()==b.getvalue()
  retry=c.post(f'/api/projects/{p}/assets/{a["id"]}/process',headers=H,json={'idempotency_key':'after-cancel'})
  assert retry.status_code==200
  app.state.execute(p,retry.json()['id'])
  assert app.state.store.get(p,'asset',a['id'])['inspection']=='ready'
