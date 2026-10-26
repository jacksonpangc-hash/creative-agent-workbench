import pytest
from types import SimpleNamespace
from creative_agent.store import Store
from creative_agent.workflows import Workflows


def test_semantic_clips_use_timed_text_and_never_color_fallback(tmp_path):
    from creative_agent.semantic_clips import SemanticClips
    store=Store(tmp_path/'db');p=store.create_project('p')['id']
    assets=[]
    for title,spoken in [('A','展示产品去污'),('B','展示去污产品'),('C','讲解配送')]:
        a=store.put(p,'asset',{'title':title,'source_kind':'own','metadata':{'duration_ms':1000,'transcript_source':'user_import','subtitles':[{'start_ms':0,'end_ms':1000,'text':spoken}]}})
        store.put(p,'scene',{'asset_id':a['id'],'start_ms':0,'end_ms':1000,'fingerprint':[0.5,0.5]})
        assets.append(a)
    flow=Workflows(store,None)
    assert flow.clusters(p)==[]
    class Embedding:
        binding=SimpleNamespace(provider='zhipu',model_id='embedding-3',base_url=None)
        def embed(self,text): return [0.,1.] if '配送' in text else [1.,0.]
    result=SemanticClips(store).index(p,[a['id'] for a in assets],Embedding())
    assert result['indexed']==3
    clusters=flow.clusters(p)
    assert sorted(c['count'] for c in clusters)==[1,2]
    assert all(c['method']=='timed_text_embedding' for c in clusters)
    # Changed transcript invalidates cached scene meaning.
    a=assets[0];a['metadata']['subtitles'][0]['text']='完全改变'
    store.put(p,'asset',a,a['id'])
    assert sum(c['count'] for c in flow.clusters(p))==2


def test_semantic_clip_missing_timed_evidence_and_invalid_vector_refuse(tmp_path):
    from creative_agent.semantic_clips import SemanticClips
    store=Store(tmp_path/'db');p=store.create_project('p')['id']
    a=store.put(p,'asset',{'title':'A','metadata':{}})
    store.put(p,'scene',{'asset_id':a['id'],'start_ms':0,'end_ms':1000,'fingerprint':[1]})
    class Client:
        binding=SimpleNamespace(provider='zhipu',model_id='embedding-3',base_url=None)
        def embed(self,text): pytest.fail('must not call without evidence')
    with pytest.raises(ValueError,match='时间'): SemanticClips(store).index(p,[a['id']],Client())


def test_timed_asr_evidence_keeps_machine_source_and_review_status(tmp_path):
    from creative_agent.semantic_clips import SemanticClips
    store=Store(tmp_path/'db');p=store.create_project('p')['id']
    asset=store.put(p,'asset',{'metadata':{'duration_ms':1000,'subtitles':[{'start_ms':0,'end_ms':1000,'text':'识别台词'}],
        'transcription':{'method':'faster-whisper-local','model':'local-zh','reviewed':False,'execution_state':'succeeded','quality_state':'usable'}}})
    scene=store.put(p,'scene',{'asset_id':asset['id'],'start_ms':0,'end_ms':1000})
    parts,_,_=SemanticClips(store).evidence(p,scene)
    assert parts[0]['source']=='faster-whisper-local'
    assert parts[0]['review_state']=='unreviewed' and parts[0]['quality_state']=='usable'
