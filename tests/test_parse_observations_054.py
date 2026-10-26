"""Parse must retain specific input references and keep modalities separate."""
import json
import subprocess

import imageio_ffmpeg
import pytest

from creative_agent.contracts import ModelBinding
from creative_agent.media import MediaService
from creative_agent.store import Store
from creative_agent.video_understanding import plan_video_route
from creative_agent.workflows import Workflows


@pytest.fixture
def video_env(tmp_path):
    store = Store(tmp_path/'db'); p = store.create_project('p')['id']
    media = MediaService(store, tmp_path/'media')
    source = tmp_path/'video.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=128x96:r=2:d=3', '-c:v', 'libx264', str(source)], capture_output=True, check=True)
    asset = media.ingest(p, source)
    media.add_subtitles(p, asset['id'], '1\n00:00:00,000 --> 00:00:01,000\n真实台词\n')
    return store, p, media, asset


class Parser:
    binding = ModelBinding(provider='openai', model_id='gpt-4.1', label='vision', role='strategy')
    def __init__(self, invalid=False): self.invalid = invalid
    def chat(self, system, question, **kwargs):
        supplied = json.loads(question)
        manifest = supplied['input_manifest']
        frame = manifest['frames'][0]
        visual = {'claim_kind': 'visual_observation', 'statement': '显示测试图形', 'interpretation': 'observed',
            'field_ids': ['visual'], 'support': [{'frame_id': frame['frame_id'], 'time_ms': frame['time_ms']}]}
        spoken = {'claim_kind': 'spoken_content', 'statement': '真实台词', 'interpretation': 'observed',
            'field_ids': ['spoken'], 'support': [{'transcript_segment_id': manifest['transcript']['segments'][0]['segment_id']}]}
        if self.invalid: spoken['support'] = visual['support']
        return {'request_id': 'parse-request', 'usage': {}, 'data': {'fields': {
            '画面': {'value': '显示测试图形', 'basis': 'observed'}, '台词': {'value': '真实台词', 'basis': 'observed'}},
            'summary': '有画面和台词', 'observations': [visual, spoken]}}


def template():
    return {'fields': ['画面', '台词'], 'definitions': [
        {'field_id': identifier, 'name': name, 'level': 'overall', 'type': 'text', 'basis': 'observed', 'unit': ''}
        for identifier, name in [('visual', '画面'), ('spoken', '台词')]]}


def test_parse_uses_manifest_and_preserves_visual_and_spoken_support(video_env):
    store, p, media, asset = video_env
    result = Workflows(store, media).parse(p, [asset['id']], ['画面', '台词'], Parser(), template_snapshot=template())
    row = result['rows'][0]
    assert row['input_manifest_id']
    assert row['cells']['visual']['claim_kind'] == 'visual_observation'
    assert row['cells']['spoken']['claim_kind'] == 'spoken_content'
    assert len(row['observations']) == 2
    assert all(o['input_manifest_id'] == row['input_manifest_id'] for o in row['observations'])
    assert store.get(p, 'media_input_manifest', row['input_manifest_id'])['execution_state'] == 'succeeded'
    from creative_agent.material_readiness import get_readiness
    assert get_readiness(store,p,asset['id'])['visual']['observations']==1


def test_parse_rejects_spoken_conclusion_using_frame_support(video_env):
    store, p, media, asset = video_env
    with pytest.raises(ValueError, match='模态'):
        Workflows(store, media).parse(p, [asset['id']], ['画面', '台词'], Parser(invalid=True), template_snapshot=template())
    assert store.list(p, 'parse')[-1]['state'] == 'needs_attention'


def test_parse_cannot_send_suspect_text_or_promote_unsupported_observed_cells(video_env):
    store, p, media, asset = video_env
    current = store.get(p, 'asset', asset['id'])
    current['metadata'].update(transcript_source='whisper.cpp-local', transcription={
        'execution_state': 'succeeded', 'quality_state': 'suspect', 'review_state': 'unreviewed'})
    store.put(p, 'asset', current, asset['id'])
    class NoSpeech(Parser):
        def chat(self, system, question, **kwargs):
            inputs = json.loads(question)
            assert inputs['subtitles'] == [] and inputs['input_manifest']['transcript']['segments'] == []
            return {'data': {'fields': {'台词': {'value': '未提供的台词', 'basis': 'observed'}}, 'observations': []}}
    row = Workflows(store, media).parse(p, [asset['id']], ['画面', '台词'], NoSpeech(), template_snapshot=template())['rows'][0]
    assert row['cells']['spoken']['value'] is None
    assert row['cells']['spoken']['state'] == 'not_observed'


def test_source_context_cannot_expand_the_persisted_analysis_scope(video_env):
    store, p, media, asset = video_env
    other = store.put(p, 'asset', {**asset, 'title': '另一条素材'})
    session = store.put(p, 'session', {'scope': {'asset_ids': [asset['id']]}})
    context = {'session_id': session['id'], 'scope': {'asset_ids': [other['id']]}}
    with pytest.raises(ValueError, match='范围'):
        Workflows(store, media).parse(p, [other['id']], ['画面', '台词'], Parser(),
                                    source_context=context, template_snapshot=template())

def test_analysis_observation_accepts_the_specific_evidence_contract_without_legacy_findings(video_env):
    from creative_agent.agent import AnalysisAgent
    from creative_agent.contracts import AnalysisScope
    store,p,media,asset=video_env
    class Observer(Parser):
        def chat(self,system,question,**kwargs):
            record=json.loads(question.split('证据ID与帧时间：\n',1)[1].split('\n除',1)[0])
            manifest=record['input_manifest'];frame=manifest['frames'][0]
            return {'request_id':'observed-request','usage':{},'data':{'summary':'抽样画面显示测试图形',
                'observations':[{'claim_kind':'visual_observation','statement':'测试图形在画面中',
                    'interpretation':'observed','support':[{'frame_id':frame['frame_id'],'time_ms':frame['time_ms']}]}]}}
    client=Observer();scope=AnalysisScope(project_id=p,asset_ids=[asset['id']])
    evidence,_=AnalysisAgent(store,media).observe(scope,[asset],client,video_route=plan_video_route(client.binding))
    assert evidence[0]['observations'][0]['support'][0]['frame_id']
    assert evidence[0]['observation']['summary']=='抽样画面显示测试图形'
    assert evidence[0]['observation']['findings'][0]['evidence_ids']==[evidence[0]['observations'][0]['observation_id']]
    cached,_=AnalysisAgent(store,media).observe(scope,[asset],client,video_route=plan_video_route(client.binding))
    assert cached[0]['cache_reused'] is True
    assert evidence[0]['observations'][0]['observation_id'] in {e['id'] for e in cached}
