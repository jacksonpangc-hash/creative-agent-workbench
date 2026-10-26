"""Real local video decoding and BYOK wire contracts; no provider quality claims."""
import base64
import hashlib
import importlib
import json
import subprocess
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest
from PIL import Image

from creative_agent.agent import AnalysisAgent
from creative_agent.contracts import AnalysisScope, ModelBinding
from creative_agent.media import MediaService
from creative_agent.model_catalog import model_availability
from creative_agent.providers import ProviderClient
from creative_agent.store import Store


def capability():
    assert importlib.util.find_spec('creative_agent.video_understanding'), '缺少可调用的内置视频理解工作流'
    return importlib.import_module('creative_agent.video_understanding')


def binding(provider='openai', model='gpt-4.1'):
    return ModelBinding(provider=provider, model_id=model, label='visual', role='strategy')


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / 'db')
    p = store.create_project('video')['id']
    media = MediaService(store, tmp_path / 'media')
    path = tmp_path / 'video.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=128x96:r=7:d=6', '-c:v', 'libx264', str(path)],
                   capture_output=True, check=True)
    asset = media.ingest(p, path)
    return store, p, media, asset


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-4.1'), ('anthropic', 'claude-sonnet-5-5')])
def test_image_capable_model_is_eligible_for_tool_assisted_video_without_claiming_native_support(provider, model):
    statuses = model_availability(binding(provider, model).model_dump())
    assert statuses.get('video_analysis', {}).get('available') is True
    assert statuses['video_analysis']['route'] == 'sampled_frames'
    assert statuses['video_input']['available'] is False


def test_route_planner_keeps_native_models_and_rejects_text_only_models():
    plan = capability().plan_video_route(binding('kimi', 'kimi-k2.6'))
    assert plan['route'] == 'native_video'
    assert plan['workflow_id'] == 'builtin.video-understanding'
    with pytest.raises(ValueError, match='视频|图片|型号'):
        capability().plan_video_route(binding('minimax', 'MiniMax-M2.7'))


def test_builtin_workflow_decodes_real_frames_with_actual_pts_and_timestamped_transcript(env):
    store, p, media, a = env
    media.add_subtitles(p, a['id'], '1\n00:00:00,100 --> 00:00:00,900\n真实文字稿\n')
    module = capability()
    result = module.VideoUnderstanding(store, media).prepare(p, a['id'], binding(), plan=module.plan_video_route(binding()))
    assert result['video'] is None and 1 < len(result['images']) <= 36
    evidence = result['evidence']
    timestamps = evidence['observed_timestamps_ms']
    assert len(timestamps) == len(result['images'])
    assert timestamps == sorted(set(timestamps))
    assert 143 in timestamps  # 7 fps: actual PTS, not the requested 250 ms seek target.
    assert all(t in [round(n * 1000 / 7) for n in range(42)] for t in timestamps)
    assert evidence['transcript'] == '真实文字稿'
    assert evidence['subtitles'][0]['start_ms'] == 100
    assert evidence['transcript_provenance'] == 'subtitle'
    assert evidence['workflow']['route'] == 'sampled_frames'
    assert evidence['coverage']['complete_frame_coverage'] is False
    assert evidence['limitations'] and evidence['coverage']['max_gap_ms'] > 0
    for path in result['images']:
        with Image.open(path) as frame:
            assert frame.size == (128, 96)


def test_missing_timestamped_transcript_is_disclosed_and_no_speech_is_invented(env):
    store, p, media, a = env
    raw = store.get(p, 'asset', a['id'])
    raw['metadata']['transcript'] = 'untimed text is not timestamped speech evidence'
    store.put(p, 'asset', raw, a['id'])
    e = capability().VideoUnderstanding(store, media).prepare(p, a['id'], binding())['evidence']
    assert e['transcript'] == '' and e['subtitles'] == []
    assert e['audio_observation'] == 'missing_timestamped_transcript'
    assert any('语音' in item for item in e['limitations'])


def test_video_workflow_rejects_foreign_assets_and_changed_route_snapshots(env):
    store, p, media, a = env
    foreign = store.create_project('foreign')['id']
    module = capability()
    workflow = module.VideoUnderstanding(store, media)
    with pytest.raises(ValueError):
        workflow.prepare(foreign, a['id'], binding())
    plan = module.plan_video_route(binding())
    plan['route'] = 'native_video'
    with pytest.raises(ValueError, match='路由|快照'):
        workflow.prepare(p, a['id'], binding(), plan=plan)


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-4.1'), ('anthropic', 'claude-sonnet-5-5')])
def test_analysis_route_sends_real_images_and_transcript_to_vision_adapter_and_freezes_provenance(env, provider, model):
    store, p, media, a = env
    media.add_subtitles(p, a['id'], '1\n00:00:00,100 --> 00:00:00,900\n来自字幕的口播\n')
    observed = []
    def transport(request):
        body = json.loads(request.content)
        message = body['messages'][-1]['content']
        content = message[0]['text']
        if '逐条观察' in content:
            supplied, _ = json.JSONDecoder().raw_decode(content.split('\n', 1)[1])
            manifest = supplied['input_manifest']
            frame = manifest['frames'][0]
            segment = manifest['transcript']['segments'][0]
            assert manifest['asset_id'] == a['id'] and manifest['asset_sha256'] == a['sha256']
            assert segment['text'] == '来自字幕的口播'
            observed.append((message, manifest))
            data = {'summary': '观察', 'findings': [{'type': 'fact', 'text': '已提供字幕', 'evidence_ids': [a['id']]}],
                'observations': [
                    {'claim_kind': 'visual_observation', 'statement': '测试图形画面', 'interpretation': 'observed',
                     'asset_id': manifest['asset_id'], 'asset_sha256': manifest['asset_sha256'],
                     'input_manifest_id': manifest['id'],
                     'support': [{'frame_id': frame['frame_id'], 'time_ms': frame['time_ms']}]},
                    {'claim_kind': 'spoken_content', 'statement': segment['text'], 'interpretation': 'observed',
                     'asset_id': manifest['asset_id'], 'asset_sha256': manifest['asset_sha256'],
                     'input_manifest_id': manifest['id'],
                     'support': [{'transcript_segment_id': segment['segment_id'],
                                  'start_ms': segment['start_ms'], 'end_ms': segment['end_ms']}]}]}
        else:
            data = {'summary': '策略', 'findings': [], 'result': {'comparisons': [], 'recommendations': ['验证假设']}}
        if provider == 'anthropic':
            return httpx.Response(200, json={'id':'wire-test', 'content': [{'type':'text','text':json.dumps(data)}], 'usage':{}})
        return httpx.Response(200, json={'id':'wire-test', 'choices':[{'message':{'content':json.dumps(data)}}], 'usage':{}})
    config = binding(provider, model)
    provider_client = ProviderClient(config, 'test-key', httpx.Client(transport=httpx.MockTransport(transport)))
    plan = capability().plan_video_route(config)
    session = AnalysisAgent(store, media).analyze(AnalysisScope(project_id=p, asset_ids=[a['id']]),
        'directed', '分析开头', provider_client, video_route=plan)
    assert len(observed) == 1
    message, manifest = observed[0]
    assert '来自字幕的口播' in message[0]['text']
    if provider == 'anthropic':
        assert all(item['type'] == 'image' and item['source']['data'] for item in message[1:])
        image_bytes = [base64.b64decode(item['source']['data']) for item in message[1:]]
    else:
        assert all(item['type'] == 'image_url' and item['image_url']['url'].startswith('data:image/jpeg;base64,') for item in message[1:])
        image_bytes = [base64.b64decode(item['image_url']['url'].split(',', 1)[1]) for item in message[1:]]
    assert len(image_bytes) == len(manifest['frames'])
    assert [hashlib.sha256(value).hexdigest() for value in image_bytes] == [frame['sha256'] for frame in manifest['frames']]
    e = session['turns'][0]['evidence'][0]
    assert e['workflow'] == plan and e['provenance'] == 'sampled_frames'
    assert e['input_manifest_id'] == manifest['id']
    assert [item['claim_kind'] for item in e['observations']] == ['visual_observation', 'spoken_content']
    assert e['observations'][0]['support'][0]['frame_id'] == manifest['frames'][0]['frame_id']
    assert e['observations'][1]['support'][0]['transcript_segment_id'] == manifest['transcript']['segments'][0]['segment_id']
    assert all(item['input_manifest_id'] == manifest['id'] for item in e['observations'])
    assert session['video_route'] == plan
    old = json.loads(json.dumps(e))
    media.add_subtitles(p, a['id'], '1\n00:00:00,100 --> 00:00:00,900\n后续修改\n')
    assert store.get(p, 'session', session['id'])['turns'][0]['evidence'][0] == old


def test_native_route_has_no_fabricated_observation_timestamps(env):
    store, p, media, a = env
    e = capability().VideoUnderstanding(store, media).prepare(p, a['id'], binding('kimi', 'kimi-k2.6'))
    assert e['images'] == [] and Path(e['video']).is_file()
    assert e['evidence']['observed_timestamps_ms'] == []
    assert e['evidence']['provenance'] == 'native_video'


def test_input_manifest_has_stable_hashed_frames_and_changes_on_subtitle_revision(env):
    store, p, media, a = env
    workflow = capability().VideoUnderstanding(store, media)
    first = workflow.prepare(p, a['id'], binding())
    m = first['input_manifest']
    assert m['id'] == first['evidence']['input_manifest_id']
    assert store.get(p, 'media_input_manifest', m['id'])['manifest_sha256'] == m['manifest_sha256']
    assert all(len(f['sha256']) == 64 and f['frame_id'].startswith('frame_') and f['width'] == 128 for f in m['frames'])
    assert m['frames'][1]['time_ms'] in first['evidence']['observed_timestamps_ms']
    repeated = workflow.prepare(p, a['id'], binding())['input_manifest']
    assert repeated['id'] == m['id'] and repeated['frames'] == m['frames']
    media.add_subtitles(p, a['id'], '1\n00:00:00,100 --> 00:00:00,900\n修订字幕\n')
    changed = workflow.prepare(p, a['id'], binding())['input_manifest']
    assert changed['id'] != m['id'] and changed['frames'] == m['frames']


def test_persist_observations_refuses_cross_project_manifests(env):
    from creative_agent.evidence_policy import persist_observations
    store, p, media, a = env
    m = capability().VideoUnderstanding(store, media).prepare(p, a['id'], binding())['input_manifest']
    foreign = store.create_project('foreign')['id']
    with pytest.raises(ValueError): persist_observations(store, foreign, m['id'], [])


@pytest.mark.parametrize('with_whole_asset_support', [False, True])
def test_model_reply_cannot_replace_granular_observation_support_with_whole_asset_id(env, with_whole_asset_support):
    store, p, media, asset = env
    def transport(request):
        message = json.loads(request.content)['messages'][-1]['content']
        supplied, _ = json.JSONDecoder().raw_decode(message[0]['text'].split('\n', 1)[1])
        assert supplied['input_manifest']['frames']
        data = {'summary': '旧格式回答', 'findings': [
            {'type': 'fact', 'text': '整条视频不能代替具体帧', 'evidence_ids': [asset['id']]}]}
        if with_whole_asset_support:
            data['observations'] = [{'claim_kind': 'visual_observation', 'statement': '缺少具体帧',
                'interpretation': 'observed', 'support': [{'asset_id': asset['id']}]}]
        return httpx.Response(200, json={'id': 'unsupported-wire',
            'choices': [{'message': {'content': json.dumps(data)}}], 'usage': {}})
    config = binding()
    client = ProviderClient(config, 'test-key', httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(ValueError, match='依据|引用'):
        AnalysisAgent(store, media).analyze(AnalysisScope(project_id=p, asset_ids=[asset['id']]),
            'directed', '分析开头', client, video_route=capability().plan_video_route(config))
    assert not [row for row in store.list(p, 'observation') if row.get('object_type') == 'Observation']
