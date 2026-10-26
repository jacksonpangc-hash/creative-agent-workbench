import sys
from types import SimpleNamespace
import pytest
from creative_agent.store import Store
from creative_agent.media import MediaService


def test_local_transcription_requires_existing_model_and_never_downloads(tmp_path,monkeypatch):
    from creative_agent.transcription import Transcriber
    store=Store(tmp_path/'db');p=store.create_project('p')['id'];media=MediaService(store,tmp_path/'media')
    a=store.put(p,'asset',{'media_type':'video','metadata':{'duration_ms':2000}})
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setenv('CREATIVE_WHISPER_MODEL',str(tmp_path/'missing'))
    with pytest.raises(ValueError,match='模型'): Transcriber(store,media).run(p,[a['id']])
    model_dir=tmp_path/'model';model_dir.mkdir();(model_dir/'model.bin').write_bytes(b'test')
    monkeypatch.setenv('CREATIVE_WHISPER_MODEL',str(model_dir))
    monkeypatch.setattr(media,'file',lambda *args:tmp_path/'authorized.mp4')
    calls=[]
    class Model:
        def __init__(self,path,**kwargs):
            assert path==str(model_dir);assert kwargs['local_files_only'] is True
        def transcribe(self,path,**kwargs):
            calls.append(path)
            return iter([SimpleNamespace(start=0.2,end=1.4,text='授权台词')]),SimpleNamespace(language='zh')
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    result=Transcriber(store,media).run(p,[a['id']])
    saved=store.get(p,'asset',a['id'])
    assert saved['metadata']['transcript']=='授权台词'
    assert saved['metadata']['subtitles'][0]['start_ms']==200
    assert saved['metadata']['transcription']['method']=='faster-whisper-local'
    assert saved['metadata']['transcript_source']=='faster-whisper-local'
    assert saved['metadata']['transcript_provenance']=='local_asr'
    assert result['completed']==1 and len(calls)==1


def test_bundled_cache_reuses_only_same_media_engine_parameters_and_subtitles(tmp_path, monkeypatch):
    import hashlib
    from creative_agent.transcription import Transcriber
    import creative_agent.asr_runtime as runtime
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL', raising=False)
    store = Store(tmp_path/'db'); p = store.create_project('p')['id']
    video = tmp_path / 'source.mp4'; video.write_bytes(b'real-file-identity')
    a = store.put(p, 'asset', {'media_type': 'video', 'sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
        'metadata': {'duration_ms': 2000}})
    media = SimpleNamespace(file=lambda *args: video, ffmpeg='ffmpeg')
    signature = {'version': 'fixture', 'vad': 'v1'}
    monkeypatch.setattr(runtime, 'runtime_signature', lambda: dict(signature))
    calls = []
    def transcribe(*args, **kwargs):
        calls.append(1)
        return [{'start_ms': 0, 'end_ms': 1000, 'text': '原始台词'}], {'method': 'whisper.cpp-local',
            'execution_state': 'succeeded', 'quality_state': 'usable', 'language': 'zh'}
    monkeypatch.setattr(runtime, 'transcribe', transcribe)
    transcriber = Transcriber(store, media)
    first = transcriber.run(p, [a['id']])
    repeated = transcriber.run(p, [a['id']])
    assert first['completed'] == 1 and repeated['cached'] == 1 and len(calls) == 1
    signature['vad'] = 'v2'
    transcriber.run(p, [a['id']])
    assert len(calls) == 2
    fresh = store.get(p, 'asset', a['id'])
    fresh['metadata']['subtitles'][0]['text'] = '人工新台词'
    fresh['metadata']['transcription']['review_state'] = 'corrected'
    store.put(p, 'asset', fresh, a['id'])
    transcriber.run(p, [a['id']])
    assert len(calls) == 2
    assert store.get(p, 'asset', a['id'])['metadata']['subtitles'][0]['text'] == '人工新台词'


def test_failed_attempt_preserves_prior_subtitles_and_records_distinct_states(tmp_path, monkeypatch):
    import creative_agent.asr_runtime as runtime
    from creative_agent.transcription import Transcriber
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL', raising=False)
    store = Store(tmp_path/'db'); p = store.create_project('p')['id']
    a = store.put(p, 'asset', {'media_type': 'video', 'metadata': {'duration_ms': 2000,
        'subtitles': [{'start_ms': 0, 'end_ms': 1000, 'text': '既有台词'}]}})
    monkeypatch.setattr(runtime, 'runtime_signature', lambda: {'version': 'fixture'})
    def fail(*args, **kwargs): raise ValueError('VAD 未检测到语音')
    monkeypatch.setattr(runtime, 'transcribe', fail)
    result = Transcriber(store, SimpleNamespace(file=lambda *a: tmp_path/'media', ffmpeg='ffmpeg')).run(p, [a['id']])
    assert result['execution_state'] == 'failed' and result['quality_state'] == 'unusable'
    saved = store.get(p, 'asset', a['id'])
    assert saved['metadata']['subtitles'][0]['text'] == '既有台词'
    assert saved['metadata']['transcription_attempt']['failure_reason'] == 'VAD 未检测到语音'
