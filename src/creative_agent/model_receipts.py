"""Successful normal use is scoped to one immutable account/model configuration."""
import hashlib
from .store import now, dumps


def configuration_revision(binding):
 return hashlib.sha256(dumps({k:binding.get(k) for k in ('id','provider','model_id','protocol','base_url','secret_ref','region')}).encode()).hexdigest()


def record_model_success(store, project_id, binding, call):
 revision=configuration_revision(binding)
 modality=call['input_type']
 identity='model-use-'+hashlib.sha256(f'{project_id}:{revision}:{modality}'.encode()).hexdigest()
 item={'binding_id':binding['id'],'binding_revision':revision,'input_type':modality,'succeeded_at':now(),
       'request_id':call.get('request_id'),'token_usage':call.get('usage',{}),'charge_state':'unreconciled','source':'normal_task'}
 try: store.get(project_id,'model_use_receipt',identity)
 except ValueError:return store.put_if_absent(project_id,'model_use_receipt',item,identity)
 return store.put(project_id,'model_use_receipt',item,identity)


def latest_model_use(store,project_id,binding):
 revision=configuration_revision(binding)
 return {r['input_type']:{k:r.get(k) for k in ('succeeded_at','request_id','token_usage','charge_state')}
         for r in store.list(project_id,'model_use_receipt') if r.get('binding_revision')==revision}
