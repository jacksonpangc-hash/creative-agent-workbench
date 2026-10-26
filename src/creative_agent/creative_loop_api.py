"""Project-local supporting operations; no supplier requests or user-chosen file paths."""
from pathlib import Path
import tempfile
import shutil
from fastapi import UploadFile, File, Form
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import Field
from .contracts import Contract, decimal
from .store import now, NotFound
from .drafts import Drafts, DraftWrite


class Reconciliation(Contract):
 job_id: str = Field(min_length=1,max_length=180)
 actual_cost: str
 evidence: str = Field(min_length=1,max_length=3000)
 bill_reference: str = Field(min_length=1,max_length=300)


def register_creative_loop(app,store,media,commercial):
 @app.get('/api/projects/{p}/drafts')
 def drafts(p:str):
  commercial.require_work('workspace.use');return Drafts(store).list(p)

 @app.put('/api/projects/{p}/drafts/{identity}')
 def save_draft(p:str,identity:str,body:DraftWrite):
  commercial.require_work('workspace.use');return Drafts(store).save(p,identity,body)

 @app.get('/api/projects/{p}/knowledge')
 def knowledge_status(p:str):
  commercial.require_work('workspace.use');store.project(p)
  return app.state.knowledge.status(p)

 @app.get('/api/projects/{p}/knowledge/jobs')
 def index_jobs(p:str):
  commercial.require_work('workspace.use');return app.state.knowledge.jobs(p)

 @app.post('/api/projects/{p}/knowledge/jobs/{job_id}/cancel')
 def cancel_index(p:str,job_id:str):
  commercial.require_work('assets.analyze');return app.state.knowledge.cancel(p,job_id)

 @app.post('/api/projects/{p}/knowledge/jobs/{job_id}/retry')
 def retry_index(p:str,job_id:str):
  commercial.require_work('assets.analyze');return app.state.knowledge.retry(p,job_id)

 @app.post('/api/projects/{p}/knowledge/search')
 def search(p:str,body:dict):
  commercial.require_work('workspace.use')
  from .creative_context import CreativeContextService
  if set(body)-{'query','product_id','sku_id'}:raise ValueError('查询条件无效')
  query=str(body.get('query','')).strip()
  if not query or len(query)>2000:raise ValueError('请填写要查找的内容')
  return CreativeContextService(store,media,index=app.state.knowledge).build_context(p,'find_references',query=query,product_id=body.get('product_id'),sku_id=body.get('sku_id'))

 @app.get('/api/projects/{p}/contexts/{context_id}')
 def context(p:str,context_id:str):
  commercial.require_work('workspace.use')
  from .creative_context import CreativeContextService
  service=CreativeContextService(store,media,index=app.state.knowledge)
  value=service.snapshot(p,context_id)
  if value.get('archived_restored'):
   from .licensing_access import LicensedAccess
   LicensedAccess(store,app.state.industry,cmm=app.state.cmm).check(p,value)
   return value
  return service.revalidate_context(p,context_id)

 @app.post('/api/projects/{p}/knowledge/exclude')
 def exclude(p:str,body:dict):
  commercial.require_work('assets.import')
  kind=body.get('source_kind');identifier=body.get('source_record_id')
  if kind not in {'asset','public_reference','cmm_reference'}:raise ValueError('这条资料暂不支持直接排除')
  record=store.get(p,kind,identifier)
  return store.put(p,kind,{**record,'excluded':True},identifier)

 @app.post('/api/projects/{p}/billing/reconcile')
 def reconcile(p:str,body:Reconciliation):
  commercial.require_work('providers.configure')
  job=store.job(p,body.job_id)
  if job['state'] in {'queued','running','submitting','submitted','polling','cancelling'}:
   raise ValueError('任务仍在进行，请结束后核账')
  actual=decimal(body.actual_cost)
  if actual<0:raise ValueError('账单金额不能为负')
  # Retain a durable basis before changing any existing occupation.
  receipt=store.put(p,'billing_reconciliation',{'job_id':body.job_id,'previous_actual_cost':job.get('actual_cost'),
   'actual_cost':str(actual),'currency':job['quote']['currency'],'evidence':body.evidence,
   'bill_reference':body.bill_reference,'reconciled_at':now(),'state':'recorded'})
  settled=store.settle(p,body.job_id,str(actual))
  store.put(p,'billing_reconciliation',{**receipt,'state':'applied'},receipt['id'])
  return {'job':settled,'reconciliation_id':receipt['id'],'budget':store.budget(p)}

 @app.get('/api/projects/{p}/backup')
 def backup(p:str):
  # Own-data preservation remains available after license expiry. Tool-level
  # source export rights are still enforced by export_bundle.
  from .portability import export_bundle
  temporary=Path(tempfile.mkdtemp(prefix='creative-project-backup-'))
  try:
   dest=temporary/'project-backup.zip';export_bundle(store,media,p,dest)
   return FileResponse(dest,media_type='application/zip',filename='creative-project-backup.zip',background=BackgroundTask(shutil.rmtree,temporary))
  except BaseException:
   shutil.rmtree(temporary);raise

 @app.post('/api/backups/restore')
 async def restore(file:UploadFile=File(...),name:str=Form('恢复的项目')):
  commercial.require_work('assets.import')
  from .portability import import_bundle,MAX_TOTAL
  temporary=Path(tempfile.mkdtemp(prefix='creative-project-restore-'))
  try:
   archive=temporary/'backup.zip';size=0
   with archive.open('wb') as f:
    while block:=await file.read(1024*1024):
     size+=len(block)
     if size>MAX_TOTAL:raise ValueError('备份超过本机恢复大小限制')
     f.write(block)
   # A new, empty project is mandatory; existing customer records are never overwritten.
   project=store.create_project(name)
   try:result=import_bundle(store,media,archive,target_project_id=project['id'])
   except Exception:
    # Leave only an empty, visibly named project on a rejected backup; never erase customer data.
    raise
   return {'project':project,'result':result,'credentials_rebind_required':True,'browser_reconnect_required':True}
  finally:shutil.rmtree(temporary)
