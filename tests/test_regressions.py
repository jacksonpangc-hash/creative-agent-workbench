import pytest
from PIL import Image
from creative_agent.store import Store
from creative_agent.contracts import CostQuote, AnalysisScope
from creative_agent.media import MediaService, SearchService, MetricImporter
from creative_agent.secrets import SecretStore


def test_invalid_settlement_cannot_partially_finish_job(tmp_path):
    s=Store(tmp_path/'db'); p=s.create_project('p',budget='5')['id']
    j=s.create_job(p,'test',{},CostQuote(amount='1',basis='test'),'one');s.transition(p,j['id'],'running')
    with pytest.raises(ValueError): s.finish(p,j['id'],result={'ok':True},actual_cost='-1')
    assert s.job(p,j['id'])['state']=='running'


def test_failed_inspection_does_not_leave_ghost_asset(tmp_path,monkeypatch):
    s=Store(tmp_path/'db'); p=s.create_project('p')['id']; m=MediaService(s,tmp_path/'media')
    a=tmp_path/'a.png';Image.new('RGB',(8,8)).save(a)
    monkeypatch.setattr(m,'inspect',lambda *a: (_ for _ in ()).throw(ValueError('bad')))
    with pytest.raises(ValueError): m.ingest(p,a)
    assert s.list(p,'asset')==[]


def test_spend_filters_require_matching_measured_report(tmp_path):
    s=Store(tmp_path/'db'); p=s.create_project('p')['id']; m=MediaService(s,tmp_path/'media')
    a=tmp_path/'a.png';Image.new('RGB',(8,8)).save(a);asset=m.ingest(p,a)
    scope=AnalysisScope(project_id=p,discovery=True,min_spend='10',min_roas='2')
    assert SearchService(s).search(scope)==[]
    MetricImporter(s).import_csv(p,f'asset_id,date,spend,revenue\n{asset["id"]},2026-10-18,20,60')
    assert len(SearchService(s).search(scope))==1


def test_keyring_no_plaintext_fallback_and_no_other_service():
    class Backend:
        priority=1
        def set_password(self,service,key,value): assert service=='creative-material-agent';self.value=value
        def get_password(self,service,key): return self.value
    backend=Backend();s=SecretStore(backend);s.set('reference','secret');assert s.get('reference')=='secret'
    backend.priority=0
    with pytest.raises(ValueError,match='密钥库'): s.set('reference','secret')

def test_batch_reservation_is_all_or_nothing_under_concurrency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from creative_agent.store import BudgetError
    s=Store(tmp_path/'db');p=s.create_project('p',budget='5')['id'];q=CostQuote(amount='2',basis='batch')
    def attempt(k):
        try: return s.create_jobs(p,'generation',[({'i':i},q,f'{k}:{i}') for i in range(2)])
        except BudgetError: return []
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,['a','b']))
    assert sorted(map(len,results))==[0,2]
    assert len(s.jobs(p))==2


def test_remote_media_request_pins_validated_ip_and_tls_name(tmp_path,monkeypatch):
    import socket
    import httpx
    from creative_agent import media as module
    s=Store(tmp_path/'db');p=s.create_project('p')['id'];m=MediaService(s,tmp_path/'media')
    calls=[]
    def resolver(*a,**kw):
        calls.append(a)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
    monkeypatch.setattr(module.socket,'getaddrinfo',resolver)
    def transport(request):
        assert request.url.host=='93.184.216.34'
        assert request.headers['host']=='example.org'
        assert request.extensions['sni_hostname']=='example.org'
        return httpx.Response(200,headers={'Content-Type':'text/html'},text='wrong format')
    with pytest.raises(ValueError,match='媒体'):
        m.download(p,'https://example.org/media.mp4',rights_confirmed=True,client=httpx.Client(transport=httpx.MockTransport(transport)))

def test_production_keyring_does_not_accept_positive_priority_plain_fallback(monkeypatch):
    import keyring
    class Chainer:
        priority=10
        backends=[]
    Chainer.__module__='keyring.backends.chainer'
    monkeypatch.setattr(keyring,'get_keyring',lambda:Chainer())
    with pytest.raises(ValueError,match='密钥库'):
        SecretStore().set('ref','secret')

def test_project_export_removes_signed_url_credentials(tmp_path):
    s=Store(tmp_path/'db');p=s.create_project('p')['id']
    s.put(p,'asset',{'metadata':{'source_url':'https://example.org/private.png?X-Amz-Credential=ACCESS&X-Amz-Signature=SIGNED-PRIVATE-ACCESS'}})
    exported=s.export_project(p)
    assert exported['records']['asset'][0]['metadata']['source_url']=='https://example.org/private.png'

def test_active_billing_exposure_cannot_be_settled_away(tmp_path):
    from creative_agent.store import Conflict
    s=Store(tmp_path/'db');p=s.create_project('p',budget='5')['id'];j=s.create_job(p,'generation',{},CostQuote(amount='2',basis='test'),'active')
    s.transition(p,j['id'],'running');s.transition(p,j['id'],'submitting')
    with pytest.raises(Conflict):s.settle(p,j['id'],'0')
    assert s.budget(p)['reserved']=='2'
