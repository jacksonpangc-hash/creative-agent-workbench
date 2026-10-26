"""Focused offline protection cases for the 0.5.4 loop; not live evidence."""
from datetime import UTC, datetime, timedelta
import pytest
from creative_agent.store import Store
from creative_agent.action_budget import plan_reservation


def test_unknown_price_is_optional_and_an_explicit_service_limit_is_preserved(tmp_path):
 s=Store(tmp_path/'db');p=s.create_project('p','5')['id']
 assert plan_reservation(s,p,{'currency':'CNY'}, {},1).amount is None
 q=plan_reservation(s,p,{'currency':'CNY','unpriced_task_limit':'0.20'}, {},1)
 assert str(q.amount)=='0.20' and s.budget(p)['reserved']=='0'


def test_model_success_is_bound_to_configuration_and_modality(tmp_path):
 from creative_agent.model_receipts import record_model_success, latest_model_use
 s=Store(tmp_path/'db');p=s.create_project('p')['id']
 b=s.put(p,'model',{'provider':'example','model_id':'text','secret_ref':'opaque-version-1'})
 record_model_success(s,p,b,{'input_type':'text','request_id':'provider-1','usage':{'input_tokens':20}})
 assert latest_model_use(s,p,b)['text']['request_id']=='provider-1'
 assert 'images' not in latest_model_use(s,p,b)
 rotated={**b,'secret_ref':'opaque-version-2'}
 assert latest_model_use(s,p,rotated)=={}


def test_cached_rights_do_not_require_fresh_api_check(tmp_path):
 from creative_agent.cmm import CMMService
 from test_industry import Keys, rights
 s=Store(tmp_path/'db');p=s.create_project('p')['id'];svc=CMMService(s,Keys())
 c=svc.connect(p,api_key='fixture-key')
 r={**rights(),"model_process":True};svc.update_rights(p,r)
 svc._mutate_connection(p,lambda c:c.update(permission_checked_at=(datetime.now(UTC)-timedelta(days=3)).isoformat(),capabilities={}))
 svc.authorize_cached_content(p,{'rights_snapshot':r},['retain_metadata','model_process'])
 with pytest.raises(ValueError):svc.authorize_remote_request(p,'extract_video_copywriting')
 svc.update_rights(p,{**r,'model_process':False})
 with pytest.raises(ValueError):svc.authorize_cached_content(p,{'rights_snapshot':r},['retain_metadata','model_process'])
