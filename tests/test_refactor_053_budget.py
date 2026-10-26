import pytest
from fastapi.testclient import TestClient

from creative_agent.api import create_app
from creative_agent.store import Store
from creative_agent.action_budget import plan_reservation


class Vault:
    def __init__(self): self.values = {}
    def get(self, k): return self.values.get(k)
    def set(self, k, v): self.values[k] = v
    def delete(self, k): self.values.pop(k, None)


def test_unknown_price_no_longer_requires_a_second_amount(tmp_path):
    s = Store(tmp_path/'data.db'); p = s.create_project('P', '5')['id']
    assert plan_reservation(s,p,{'currency':'CNY'},{},2).amount is None
    q = plan_reservation(s,p,{'currency':'CNY','unpriced_task_limit':'0.5'},{},2)
    assert str(q.amount) == '0.5' and '不是预计扣费' in q.basis
    assert s.budget(p)['reserved'] == '0'


def test_known_price_derives_and_preserves_currency_and_cap(tmp_path):
    s=Store(tmp_path/'data.db');p=s.create_project('P','5')['id']
    b={'currency':'CNY','price_input_million':'2','price_output_million':'8','max_output_tokens':4096}
    q=plan_reservation(s,p,b,{'question':'比较开头'},2)
    assert 0 < q.amount < 5 and '估算' in q.basis
    with pytest.raises(ValueError,match='币种'):plan_reservation(s,p,{**b,'currency':'USD'},{},1)
    with pytest.raises(ValueError,match='超出'):plan_reservation(s,p,{**b,'price_output_million':'9999'},{},2)


def test_optional_budget_absent_preserves_unknown_not_fake_zero(tmp_path):
    s=Store(tmp_path/'data.db');p=s.create_project('P')['id']
    assert plan_reservation(s,p,{}, {},1).amount is None


def test_task_intent_and_auto_quote_are_server_owned_and_idempotent(tmp_path):
    app=create_app(tmp_path,token='refactor-test',secret_store=Vault(),run_jobs=False)
    h={'Authorization':'Bearer refactor-test'}
    with TestClient(app,base_url='http://127.0.0.1:8765',headers=h) as c:
        p=c.post('/api/projects',json={'name':'P','budget':'5'}).json()['id']
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'configured','api_key':'offline-test','unpriced_task_limit':'0.5'}).json()
        c.put(f'/api/projects/{p}/preferences',json={'default_binding_id':m['id'],'default_quote':'','expected_version':0})
        body={'task_intent':'script','question':'介绍这款点心','idempotency_key':'same'}
        one=c.post(f'/api/projects/{p}/actions',json=body)
        assert one.status_code==200,one.text
        job=app.state.store.job(p,one.json()['id'])
        assert job['payload']['task_spec']['output_type']=='answer'
        assert job['payload']['mode']=='inspiration' and job['payload']['discussion_only']
        assert job['payload']['binding_id']==m['id'] and job['quote']['amount']=='0.5'
        two=c.post(f'/api/projects/{p}/actions',json=body)
        assert two.status_code==200 and two.json()['id']==one.json()['id']
        conflict=c.post(f'/api/projects/{p}/actions',json={**body,'question':'不同目标'})
        assert conflict.status_code==409
        assert len(app.state.store.jobs(p))==1


def test_default_service_missing_does_not_silently_change_payer(tmp_path):
    app=create_app(tmp_path,token='refactor-test',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer refactor-test'}) as c:
        p=c.post('/api/projects',json={'name':'P','budget':'5'}).json()['id']
        app.state.store.put_if_absent(p,'preferences',{'default_binding_id':'deleted','default_quote':''},'preferences-'+p)
        r=c.post(f'/api/projects/{p}/actions',json={'task_intent':'script','question':'想法','idempotency_key':'missing'})
        assert r.status_code==400 and '默认' in r.text


def test_script_adjustment_freezes_current_saved_edit_not_old_generated_answer(tmp_path):
    app=create_app(tmp_path,token='refactor-test',secret_store=Vault(),run_jobs=False)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer refactor-test'}) as c:
        p=c.post('/api/projects',json={'name':'P','budget':'5'}).json()['id'];s=app.state.store
        m=c.post(f'/api/projects/{p}/models',json={'provider':'deepseek','role':'strategy','model_id':'deepseek-flash','label':'M','api_key':'offline-test','unpriced_task_limit':'0.5'}).json()
        session=s.put(p,'session',{'mode':'inspiration','analysis_kind':'spoken','discussion_only':True,'scope':{'project_id':p,'asset_ids':[]},'turns':[{'task_spec':{'output_type':'scripts'}}]})
        script=s.put(p,'script',{'version':2,'title':'人工已改稿','source':{'session_id':session['id']},'rows':[{'dialogue':'人工修改必须保留'}]})
        body={'kind':'followup','session_id':session['id'],'binding_id':m['id'],'question':'换个开头','script_revision':{'script_id':script['id'],'version':2},'idempotency_key':'revise'}
        r=c.post(f'/api/projects/{p}/actions',json=body);assert r.status_code==200,r.text
        snapshot=s.job(p,r.json()['id'])['payload']['script_revision_snapshot']
        assert snapshot['version']==2 and snapshot['rows'][0]['dialogue']=='人工修改必须保留'
        wrong=c.post(f'/api/projects/{p}/actions',json={**body,'session_id':'other','idempotency_key':'wrong'})
        assert wrong.status_code==400 and '不对应' in wrong.text
