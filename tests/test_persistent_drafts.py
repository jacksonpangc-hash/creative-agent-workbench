from fastapi.testclient import TestClient
from creative_agent.api import create_app

class Vault:
 def __init__(self):self.data={}
 def get(self,k):return self.data.get(k)
 def set(self,k,v):self.data[k]=v
 def delete(self,k):self.data.pop(k,None)

def client(tmp_path):
 return TestClient(create_app(tmp_path,token='drafts',secret_store=Vault(),run_jobs=False),base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer drafts'})

def test_draft_survives_new_runtime_and_another_local_origin(tmp_path):
 with client(tmp_path) as c:
  p=c.post('/api/projects',json={'name':'P'}).json()['id']
  saved=c.put(f'/api/projects/{p}/drafts/inspiration',json={'expected_version':0,'operation_id':'first','content':{'question':'下次继续写这个开头','analysisMode':'inspiration','analysisKind':'spoken'}})
  assert saved.status_code==200,saved.text
  assert saved.json()['version']==1
 with client(tmp_path) as c:
  result=c.get(f'/api/projects/{p}/drafts').json()
  assert result['active_identity']=='inspiration'
  assert result['drafts'][0]['content']['question']=='下次继续写这个开头'

def test_conflicting_edit_keeps_both_drafts_and_replay_does_not_duplicate(tmp_path):
 with client(tmp_path) as c:
  p=c.post('/api/projects',json={'name':'P'}).json()['id'];url=f'/api/projects/{p}/drafts/directed'
  original={'expected_version':0,'operation_id':'one','content':{'question':'第一窗口'}}
  assert c.put(url,json=original).status_code==200
  body={'expected_version':0,'operation_id':'two','content':{'question':'第二窗口'}}
  conflict=c.put(url,json=body);assert conflict.status_code==200,conflict.text
  assert conflict.json()['recovered_from']=='directed'
  assert conflict.json()['identity']!='directed'
  assert c.put(url,json=body).json()['id']==conflict.json()['id']
  drafts=c.get(f'/api/projects/{p}/drafts').json()['drafts']
  assert {d['content']['question'] for d in drafts}=={'第一窗口','第二窗口'}

def test_draft_cannot_reference_another_project_or_store_credentials(tmp_path):
 with client(tmp_path) as c:
  p=c.post('/api/projects',json={'name':'P'}).json()['id'];other=c.post('/api/projects',json={'name':'Q'}).json()['id']
  app=c.app;a=app.state.store.put(other,'asset',{'title':'private','metadata':{}})
  for content in [{'question':'test','selected':[a['id']]},{'question':'test','api_key':'should never persist'}]:
   r=c.put(f'/api/projects/{p}/drafts/inspiration',json={'expected_version':0,'operation_id':'unsafe','content':content})
   assert r.status_code in {400,404,422},r.text
  assert c.get(f'/api/projects/{p}/drafts').json()['drafts']==[]

def test_reusing_draft_operation_with_different_content_is_conflict(tmp_path):
 with client(tmp_path) as c:
  p=c.post('/api/projects',json={'name':'P'}).json()['id'];url=f'/api/projects/{p}/drafts/inspiration'
  body={'expected_version':0,'operation_id':'same','content':{'question':'Original'}}
  assert c.put(url,json=body).status_code==200
  body['content']['question']='Changed'
  assert c.put(url,json=body).status_code==409

def test_restored_project_drafts_remain_editable_with_their_imported_record_ids(tmp_path):
 from creative_agent.portability import export_bundle,import_bundle
 from creative_agent.drafts import Drafts,DraftWrite
 from creative_agent.store import Store
 from creative_agent.media import MediaService
 s=Store(tmp_path/'source.db');p=s.create_project('P')['id'];media=MediaService(s,tmp_path/'media')
 Drafts(s).save(p,'inspiration',DraftWrite(expected_version=0,operation_id='save',content={'question':'备份前的草稿'}))
 archive=tmp_path/'backup.zip';export_bundle(s,media,p,archive)
 target=Store(tmp_path/'target.db');q=target.create_project('Q')['id'];tm=MediaService(target,tmp_path/'restored-media')
 import_bundle(target,tm,archive,target_project_id=q)
 Drafts(target).save(q,'inspiration',DraftWrite(expected_version=1,operation_id='edited-after-restore',content={'question':'恢复后继续编辑'}))
 assert Drafts(target).list(q)['drafts'][0]['content']['question']=='恢复后继续编辑'
 assert Drafts(target).list(q)['drafts'][0]['version']==2

def test_script_editor_draft_is_kept_out_of_conversation_default_and_retains_unfinished_rows(tmp_path):
 with client(tmp_path) as c:
  p=c.post('/api/projects',json={'name':'P'}).json()['id']
  conversation={'expected_version':0,'operation_id':'goal','content':{'question':'创作目标'}}
  assert c.put(f'/api/projects/{p}/drafts/inspiration',json=conversation).status_code==200
  cache={'active':'__new','drafts':{'__new':{'title':'正在修改的脚本','objective':'还没写完','rows':[{'start_ms':1000,'end_ms':0,'dialogue':'保留未完成台词','visual':''}]}}}
  body={'expected_version':0,'operation_id':'edit','content':{'scriptDrafts':cache}}
  response=c.put(f'/api/projects/{p}/drafts/script-editor',json=body)
  assert response.status_code==200,response.text
  result=c.get(f'/api/projects/{p}/drafts').json()
  assert result['active_identity']=='inspiration'
  assert next(d for d in result['drafts'] if d['identity']=='script-editor')['content']['scriptDrafts']==cache
  assert c.app.state.store.list(p,'script')==[]

def test_continuation_identities_are_saved_and_cannot_reference_another_project(tmp_path):
    from creative_agent.drafts import Drafts,DraftWrite
    from creative_agent.store import Store,NotFound
    import pytest
    s=Store(tmp_path/'continuation.db');p=s.create_project('P')['id'];q=s.create_project('Q')['id']
    script=s.put(p,'script',{'version':2,'rows':[]});parsed=s.put(p,'parse',{'version':3,'rows':[]});other=s.put(q,'script',{'version':1})
    content={'question':'换个开头','revisionContext':{'script_id':script['id'],'version':2},'parseContext':{'parse_id':parsed['id'],'version':3}}
    saved=Drafts(s).save(p,'inspiration',DraftWrite(expected_version=0,operation_id='one',content=content))
    assert saved['content']['revisionContext']==content['revisionContext'] and saved['content']['parseContext']==content['parseContext']
    with pytest.raises(NotFound):Drafts(s).save(p,'inspiration',DraftWrite(expected_version=1,operation_id='foreign',content={'revisionContext':{'script_id':other['id'],'version':1}}))
