"""Bounded external supplementation. Saved keys alone never authorize queries."""
from datetime import date,timedelta
import hashlib
from .contracts import decimal
from .store import dumps, now, NotFound


class DataRequestPlanner:
 def __init__(self,store,cmm,authorize=None):self.store,self.cmm,self.authorize=store,cmm,authorize

 def plan(self,p,intent,query,*,local_reference_count=0):
  try:prefs=self.store.get(p,'preferences','preferences-'+p)
  except NotFound:prefs={}
  reason='普通改稿沿用本稿资料'
  enabled=intent in {'new_script','find_references','new_direction'} and not local_reference_count and prefs.get('allow_external_references') is True
  if not enabled:
   return {'state':'local_only','reason':reason if intent=='revise_current' else '使用本地资料；未满足新增查询条件','new_external_queries':0}
  keyword=query.strip()[:120];today=date.today()
  request=self.cmm.search_query(kind='commerce',keyword=keyword,start_date=today-timedelta(days=30),end_date=today)
  c=self.cmm._connection(p) or {}
  identity={'provider':'cmm','account_revision':c.get('revision'),'operation':request['operation'],'query':request['query'],
   'rights_version':hashlib.sha256(dumps(c.get('rights')).encode()).hexdigest(),'schema_version':'2026-10-14','policy_version':'supplement-v1'}
  key=hashlib.sha256(dumps(identity).encode()).hexdigest()
  record={'intent':intent,**identity,'cache_key':key,'max_search_calls':2,'max_content_calls':3,'created_at':now(),'new_external_queries':0}
  # Reuse only legally accessible saved content; stale entitlement does not by
  # itself revoke retained content, while rights expiry/revocation still does.
  for old in self.store.list(p,'data_request_plan'):
   if old.get('cache_key')==key and old.get('state')=='completed':
    try:
     for rid in old.get('reference_ids',[]):self.cmm.validated_evidence(p,[rid])
     return {**old,'cache_reused':True,'new_external_queries':0}
    except ValueError:pass
  try:
   if self.authorize:self.authorize()
   self.cmm.authorize_remote_request(p,request['operation'])
   preview=self.cmm.preflight(p,request['operation'],request['query'])
   if not preview['response_schema_ready']:raise ValueError('此查询的真实响应适配尚未完成')
   rate=self.cmm._tariff(p,request['operation'])
   usage=self.cmm.overview(p)['usage']
   if usage.get('limit_points') is None:raise ValueError('请先确认数据查询积分上限')
   available=decimal(usage['limit_points'])-decimal(usage['reserved_points'])
   if available<rate:raise ValueError('现有积分已占用，暂停新增查询')
   record.update(state='ready',max_points=str(available),reason='本地暂无适合参考，按已授权来源补充')
  except ValueError as error:record.update(state='blocked',reason=str(error))
  return self.store.put(p,'data_request_plan',record)

 def execute(self,p,plan):
  if plan.get('state')!='ready':return plan
  if self.authorize:self.authorize()
  # A single synchronous search is within the two-page ceiling. Additional
  # pages/details require their own persisted child jobs and available budget.
  job=self.cmm.create_job(p,operation=plan['operation'],query=plan['query'],max_calls=1,
   max_points=str(self.cmm._tariff(p,plan['operation'])),target_count=20,idempotency_key='context-search-'+plan['cache_key'])
  if job['state']=='queued':job=self.cmm.run(p,job['id'])
  ids=[]
  if job['state']=='succeeded':
   results=(job.get('result') or {}).get('results',[])
   # Metadata is only a browsing candidate. It never becomes observed content.
   selected=self.cmm.select(p,job['id'],[r['id'] for r in results[:3]]) if results else []
   ids=[r['id'] for r in selected]
  children=[job['id']];content_ids=[];gaps=[]
  used=decimal(job.get('reserved_points','0'))
  for rid in ids[:3]:
   try:
    preview=self.cmm.reference_content(p,rid,kind='copywriting')
    if not preview.get('tariff_verified') or not preview.get('response_schema_ready'):raise ValueError('内容接口费率或响应尚未核验')
    rate=decimal(preview['max_points_per_call'])
    if used+rate>decimal(plan['max_points']):raise ValueError('本次已达到内容处理积分上限')
    if self.authorize:self.authorize()
    child=self.cmm.create_reference_content(p,rid,kind='copywriting',max_calls=1,max_points=str(rate),idempotency_key='context-content-'+plan['cache_key']+'-'+rid)
    children.append(child['id']);used+=rate
    if child['state']=='queued':child=self.cmm.run(p,child['id'])
    if child['state']=='succeeded':
     contents=(child.get('result') or {}).get('results',[])
     if contents:content_ids.extend(r['id'] for r in self.cmm.select(p,child['id'],[r['id'] for r in contents[:1]]))
    else:gaps.append('内容请求尚未完成；不重复提交或自动轮询')
   except ValueError as error:gaps.append(str(error))
  result={**plan,'state':'completed' if job['state']=='succeeded' else 'needs_attention','child_job_ids':children,
   'reference_ids':ids+content_ids,'content_state':'provider_text_available' if content_ids else 'metadata_only','content_gaps':gaps,
   'new_external_queries':sum(self.cmm.job(p,jid).get('calls_attempted',0) for jid in children)}
  return self.store.put(p,'data_request_plan',result,plan['id'])
