import json
from pathlib import Path

import pytest


def test_strict_timestamp_decoder():
    from creative_agent.asr_runtime import decode_transcription
    payload={'result':{'language':'zh'},'transcription':[{'offsets':{'from':200,'to':1400},'text':' 授权台词 '}]}
    assert decode_transcription(json.dumps(payload),2000)==([{'start_ms':200,'end_ms':1400,'text':'授权台词'}],'zh')
    for bad in ['{}','{"transcription": []}',json.dumps({'transcription':[{'offsets':{'from':False,'to':100},'text':'x'}]}),json.dumps({'transcription':[{'offsets':{'from':0,'to':3000},'text':'x'}]})]:
        with pytest.raises(ValueError): decode_transcription(bad,2000)


def test_resource_discovery_frozen_and_development(tmp_path,monkeypatch):
    import creative_agent.asr_runtime as runtime
    monkeypatch.setattr(runtime.sys,'_MEIPASS',str(tmp_path),raising=False)
    directory=tmp_path/'asr';directory.mkdir()
    for name in ['whisper-cli','ggml-small.bin','manifest.json']: (directory/name).write_text('{}')
    assert runtime.resource_directory()==directory


def test_batch_continues_after_bad_media_and_invalidates_embedding(tmp_path,monkeypatch):
    import creative_agent.asr_runtime as runtime
    from creative_agent.store import Store
    from creative_agent.transcription import Transcriber
    store=Store(tmp_path/'db');p=store.create_project('p')['id']
    bad=store.put(p,'asset',{'media_type':'video','metadata':{'duration_ms':0}})
    good=store.put(p,'asset',{'media_type':'video','metadata':{'duration_ms':2000},'embedding':[1]})
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setattr(runtime, 'runtime_signature', lambda: {'version': 'fixture'})
    monkeypatch.setattr(runtime,'transcribe',lambda *args:([{'start_ms':0,'end_ms':1000,'text':'hello'}],{'method':'whisper.cpp-local','model':'small','language':'en'}))
    result=Transcriber(store,type('Media',(),{'file':lambda *args:Path('/fake'),'ffmpeg':'ffmpeg'})()).run(p,[bad['id'],good['id']])
    assert result['state']=='needs_attention' and result['completed']==1 and len(result['errors'])==1
    assert 'embedding' not in store.get(p,'asset',good['id'])
    assert store.get(p,'asset',good['id'])['metadata']['transcript_provenance']=='local_asr'


def test_packaging_declares_executable_and_model_resources():
    text=(Path(__file__).resolve().parents[1]/'scripts/package_worker.py').read_text()
    # PyInstaller rewrites/signs Mach-O BINARY entries, invalidating the pinned
    # ASR provenance hash. The runtime needs the prepared executable's bytes.
    assert "'--add-data',str(asr_archive)+':asr'" in text and "asr/'executable.zip'" in text
    assert "'--add-binary',str(asr/'whisper-cli')" not in text
    assert 'prepare_asr' in text


def test_verified_frozen_asr_data_gains_execute_permission_after_hash_check(tmp_path,monkeypatch):
    import hashlib
    import os
    import creative_agent.asr_runtime as runtime
    if os.name=='nt':pytest.skip('POSIX executable permissions')
    directory=tmp_path/'asr';directory.mkdir()
    binary=directory/'whisper-cli';binary.write_bytes(b'#!/bin/sh\nprintf verified-frozen-asr')
    binary.chmod(0o600)
    (directory/'ggml-small.bin').write_bytes(b'model')
    (directory/'ggml-silero-v6.2.0.bin').write_bytes(b'vad')
    (directory/'silero-vad-LICENSE.txt').write_bytes(b'license')
    digest=lambda name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
    manifest={'version':'v1.9.5','commit':runtime.WHISPER_COMMIT,'binary_sha256':digest('whisper-cli'),
        'model_sha256':digest('ggml-small.bin'),'vad':{'filename':'ggml-silero-v6.2.0.bin',
        'compatible_whisper_commit':runtime.WHISPER_COMMIT,'sha256':digest('ggml-silero-v6.2.0.bin'),
        'license_sha256':digest('silero-vad-LICENSE.txt')}}
    (directory/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(runtime.sys,'_MEIPASS',str(tmp_path),raising=False)
    monkeypatch.setattr(runtime,'VAD_SHA256',manifest['vad']['sha256'])
    monkeypatch.setattr(runtime,'VAD_LICENSE_SHA256',manifest['vad']['license_sha256'])
    assert runtime.runtime_signature()['binary_sha256']==digest('whisper-cli')
    assert os.access(binary,os.X_OK)
    # A bad payload must never be made executable as a repair shortcut.
    binary.chmod(0o600);binary.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='不匹配'):runtime.runtime_signature()
    assert not os.access(binary,os.X_OK)


@pytest.mark.parametrize('corrupt',[None,'hash','path'])
def test_frozen_asr_archive_restores_only_the_verified_executable(tmp_path,monkeypatch,corrupt):
    import hashlib
    import zipfile
    import creative_agent.asr_runtime as runtime
    directory=tmp_path/'asr';directory.mkdir()
    prepared=b'prepared executable bytes'
    manifest={'binary_sha256':hashlib.sha256(prepared).hexdigest()}
    (directory/'manifest.json').write_text(json.dumps(manifest))
    (directory/'ggml-small.bin').write_bytes(b'model')
    with zipfile.ZipFile(directory/'executable.zip','w') as bundle:
        bundle.writestr('../whisper-cli' if corrupt=='path' else 'whisper-cli',b'changed' if corrupt=='hash' else prepared)
    monkeypatch.setattr(runtime.sys,'_MEIPASS',str(tmp_path),raising=False)
    if corrupt:
        with pytest.raises(ValueError):runtime.resource_directory()
        assert not (directory/'whisper-cli').exists()
        assert not (tmp_path/'whisper-cli').exists()
    else:
        assert runtime.resource_directory()==directory
        assert (directory/'whisper-cli').read_bytes()==prepared


def test_silent_track_rejected_before_decoder(tmp_path,monkeypatch):
    import wave
    from types import SimpleNamespace

    import creative_agent.asr_runtime as runtime
    monkeypatch.setattr(runtime,'resource_directory',lambda:tmp_path)
    def run(args,**kwargs):
        with wave.open(args[-1],'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'));out.writeframes(b'\x00\x00'*16000)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess,'run',run)
    with pytest.raises(ValueError,match='静音'): runtime.transcribe(tmp_path/'input.mp4',1000,'ffmpeg')


def test_decoder_rejects_segment_start_beyond_media_end():
    from creative_agent.asr_runtime import decode_transcription
    with pytest.raises(ValueError):
        decode_transcription(json.dumps({'transcription':[{'offsets':{'from':2050,'to':2100},'text':'x'}]}),2000)


@pytest.mark.parametrize('payload',[{'transcription':[{'offsets':None,'text':'x'}]}, {'transcription':[{'offsets':{'from':0,'to':500},'text':'x'}],'result':[]}, {'transcription':[{'offsets':{'from':0,'to':500},'text':'x'}],'result':{'language':None}}])
def test_decoder_rejects_malformed_metadata(payload):
    from creative_agent.asr_runtime import decode_transcription
    with pytest.raises(ValueError): decode_transcription(json.dumps(payload),1000)


def test_distribution_includes_vendor_notices():
    root=Path(__file__).resolve().parents[1]
    assert 'THIRD_PARTY_NOTICES.txt' in (root/'scripts/package_worker.py').read_text()
    assert 'THIRD_PARTY_NOTICES.txt' in (root/'scripts/prepare_asr.py').read_text()


def test_existing_endpoint_preflight_finds_bundled_model(tmp_path,monkeypatch):
    import creative_agent.asr_runtime as runtime
    from creative_agent.transcription import Transcriber
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL',raising=False)
    monkeypatch.setattr(runtime,'resource_directory',lambda:tmp_path)
    assert Transcriber.model_path()==tmp_path


def test_prepare_asr_rebuilds_scalar_cache_for_current_host(tmp_path):
    import importlib.util
    script=Path(__file__).resolve().parents[1]/'scripts/prepare_asr.py'
    spec=importlib.util.spec_from_file_location('prepare_asr_native_test',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'bin').mkdir();(tmp_path/'bin/whisper-cli').write_bytes(b'executable')
    (tmp_path/'CMakeCache.txt').write_text('GGML_NATIVE:BOOL=OFF\n')
    assert module.needs_native_build(tmp_path) is True
    (tmp_path/'CMakeCache.txt').write_text('GGML_NATIVE:BOOL=ON\nCMAKE_OSX_DEPLOYMENT_TARGET:STRING=12.0\nGGML_ACCELERATE:BOOL=OFF\n')
    (tmp_path/'creative-build-host.json').write_text(json.dumps(module.host_fingerprint()))
    assert module.needs_native_build(tmp_path) is False
    (tmp_path/'bin/whisper-cli').unlink()
    assert module.needs_native_build(tmp_path) is True


def test_native_cpu_manifest_falls_back_when_intel_sysctls_absent(monkeypatch):
    import importlib.util
    import subprocess
    script=Path(__file__).resolve().parents[1]/'scripts/prepare_asr.py'
    spec=importlib.util.spec_from_file_location('prepare_asr_cpu_test',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin')
    def read(args,**kwargs):
        if 'machdep.cpu.brand_string' in args:raise subprocess.CalledProcessError(1,args)
        return 'MacBookPro18,1\n'
    monkeypatch.setattr(module.subprocess,'check_output',read)
    assert module.host_cpu()=='MacBookPro18,1 (CPU feature sysctls unavailable; native build for this host)'


def test_native_cache_cannot_be_relabelled_for_another_cpu(tmp_path):
    import importlib.util
    script=Path(__file__).resolve().parents[1]/'scripts/prepare_asr.py'
    spec=importlib.util.spec_from_file_location('prepare_asr_fingerprint_test',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'bin').mkdir();(tmp_path/'bin/whisper-cli').write_bytes(b'executable')
    (tmp_path/'CMakeCache.txt').write_text('GGML_NATIVE:BOOL=ON\nCMAKE_OSX_DEPLOYMENT_TARGET:STRING=12.0\nGGML_ACCELERATE:BOOL=OFF\n')
    host={'architecture':'x86_64','system':'Darwin','cpu_requirements':'CPU-A'}
    (tmp_path/'creative-build-host.json').write_text(json.dumps(host))
    assert module.needs_native_build(tmp_path,host) is False
    assert module.needs_native_build(tmp_path,{**host,'cpu_requirements':'CPU-B'}) is True
    (tmp_path/'creative-build-host.json').unlink()
    assert module.needs_native_build(tmp_path,host) is True


def test_whisper_final_decode_window_end_is_clipped_with_explicit_provenance():
    from creative_agent.asr_runtime import decode_transcription
    raw=json.dumps({'result':{'language':'zh'},'transcription':[
        {'offsets':{'from':0,'to':2500},'text':'测试开头'},
        {'offsets':{'from':48000,'to':60000},'text':'测试结尾'}]})
    rows,language=decode_transcription(raw,57130)
    assert language=='zh'
    assert rows[-1]=={'start_ms':48000,'end_ms':57130,'text':'测试结尾','original_end_ms':60000,'timing_adjustment':'whisper_final_window_clipped_to_media'}


def test_nonfinal_window_overrun_is_still_rejected():
    from creative_agent.asr_runtime import decode_transcription
    raw=json.dumps({'transcription':[{'offsets':{'from':0,'to':60000},'text':'x'},{'offsets':{'from':1000,'to':2000},'text':'y'}]})
    with pytest.raises(ValueError):decode_transcription(raw,57130)


def test_macos14_asr_cache_is_rebuilt_for_declared_macos12_target(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_asr.py'
    spec = importlib.util.spec_from_file_location('prepare_asr_macos_target', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    host = {'architecture': 'x86_64', 'system': 'Darwin', 'cpu_requirements': 'CPU-A'}
    (tmp_path / 'bin').mkdir(); (tmp_path / 'bin/whisper-cli').write_bytes(b'executable')
    (tmp_path / 'creative-build-host.json').write_text(json.dumps(host))
    (tmp_path / 'CMakeCache.txt').write_text('GGML_NATIVE:BOOL=ON\nCMAKE_OSX_DEPLOYMENT_TARGET:STRING=14.0\nGGML_ACCELERATE:BOOL=ON\n')
    assert module.needs_native_build(tmp_path, host) is True


def test_dominant_exact_repetition_adds_review_warning_without_dropping_text():
    from creative_agent.asr_runtime import transcript_warnings
    rows=[{'text':'重复台词'} for _ in range(3)]+[{'text':'其他台词'}]
    assert transcript_warnings(rows)==['识别结果有较多相同片段，请核对原音频后再使用；尚未确认内容准确性。']
    assert len(rows)==4
    assert transcript_warnings([{'text':'甲'},{'text':'甲'},{'text':'乙'},{'text':'丙'}])==[]


def test_pcm_silence_ranges_skip_long_gaps_and_keep_original_timeline():
    import array
    from creative_agent.asr_runtime import speech_ranges
    samples = array.array('h', [0] * 16000 + [1000] * 16000 + [0] * 32000 + [1000] * 16000)
    ranges = speech_ranges(samples, 16000)
    assert len(ranges) == 2
    assert ranges[0][0] > 0 and ranges[0][1] <= 36000
    assert ranges[1][0] >= 60000 and ranges[1][1] == len(samples)
    assert speech_ranges(array.array('h', [0] * 16000), 16000) == []


def test_bundled_decoder_uses_official_vad_once_and_preserves_source_timestamps(tmp_path, monkeypatch):
    import array
    import wave
    from types import SimpleNamespace
    import creative_agent.asr_runtime as runtime
    (tmp_path / 'manifest.json').write_text(json.dumps({'version': 'v1.9.5', 'model_sha256': 'fixture',
        'vad': {'filename': 'ggml-silero-v6.2.0.bin', 'sha256': 'vad-fixture'}}))
    monkeypatch.setattr(runtime, 'resource_directory', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'runtime_signature', lambda: {'version': 'fixture'})
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[0] == 'ffmpeg':
            samples = array.array('h', [1000] * 16000 + [0] * 32000 + [1000] * 16000)
            with wave.open(args[-1], 'wb') as output:
                output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                output.writeframes(samples.tobytes())
        else:
            assert args[args.index('-mc') + 1] == '0'
            assert args[args.index('-l') + 1] == 'zh'
            assert '--vad' in args and args[args.index('--vad-model') + 1].endswith('ggml-silero-v6.2.0.bin')
            assert '-nf' in args
            prefix = Path(args[args.index('-of') + 1])
            prefix.with_suffix('.json').write_text(json.dumps({'result': {'language': 'zh'},
                'transcription': [{'offsets': {'from': 0, 'to': 500}, 'text': '原音台词'},
                                  {'offsets': {'from': 3000, 'to': 3500}, 'text': '结尾台词'}]}))
        return SimpleNamespace(returncode=0, stderr=b'Including segment 0: 0.00 - 0.60\nIncluding segment 1: 3.00 - 3.60')
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    rows, provenance = runtime.transcribe(tmp_path / 'input.mp4', 4000, 'ffmpeg', language='zh')
    assert len(calls) == 2 and len(rows) == 2
    assert rows[0]['start_ms'] == 0 and rows[1]['start_ms'] == 3000
    assert provenance['decode_config']['text_context_tokens'] == 0
    assert provenance['quality_state'] == 'usable'
    assert provenance['execution_state'] == 'succeeded' and provenance['review_state'] == 'unreviewed'
    assert provenance['needs_review'] is True


def test_repetition_warning_normalizes_spacing_and_punctuation_without_rewriting():
    from creative_agent.asr_runtime import transcript_warnings
    rows = [{'text': text} for text in ['这款牙膏。', '这款牙膏！', '这 款 牙 膏', '另一句']]
    assert transcript_warnings(rows)
    assert rows[1]['text'] == '这款牙膏！'


def test_official_vad_configuration_is_required_and_pinned(tmp_path, monkeypatch):
    import creative_agent.asr_runtime as runtime
    monkeypatch.setattr(runtime, 'resource_directory', lambda: tmp_path)
    (tmp_path / 'manifest.json').write_text(json.dumps({'version': 'v1.9.5'}))
    with pytest.raises(ValueError, match='VAD'): runtime.runtime_signature()


def test_transcription_cancel_before_start_does_not_decode_or_replace_existing_text(tmp_path, monkeypatch):
    from creative_agent.store import Store
    from creative_agent.transcription import Transcriber
    import creative_agent.asr_runtime as runtime
    store = Store(tmp_path/'db'); p = store.create_project('p')['id']
    a = store.put(p, 'asset', {'media_type': 'video', 'sha256': 'a'*64,
        'metadata': {'duration_ms': 1000, 'transcript': '保留人工台词'}})
    monkeypatch.delenv('CREATIVE_WHISPER_MODEL', raising=False)
    monkeypatch.setattr(runtime, 'transcribe', lambda *a, **kw: pytest.fail('cancelled work decoded'))
    result = Transcriber(store, object()).run(p, [a['id']], cancelled=lambda: True)
    assert result['execution_state'] == 'cancelled'
    assert store.get(p, 'asset', a['id'])['metadata']['transcript'] == '保留人工台词'
