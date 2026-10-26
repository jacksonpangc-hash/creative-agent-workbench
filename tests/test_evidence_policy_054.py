"""Failure scenarios for 0.5.4 evidence boundaries; run in final combined check."""
from copy import deepcopy

import pytest


def asset(**transcription):
    return {'id': 'a', 'sha256': 'a' * 64, 'metadata': {'duration_ms': 2000,
        'transcript_source': 'whisper.cpp-local',
        'subtitles': [{'start_ms': 0, 'end_ms': 1000, 'text': '这是原片台词'}],
        'transcription': transcription}}


def test_legacy_asr_is_unknown_and_cannot_silently_enter_positive_index():
    from creative_agent.evidence_policy import transcript_policy
    result = transcript_policy(asset(reviewed=False))
    assert result['quality_state'] == 'unknown' and result['allowed'] is False
    assert result['review_state'] == 'unreviewed'


@pytest.mark.parametrize('quality', ['suspect', 'unusable', 'unknown'])
def test_quality_gate_is_shared_across_retrieve_parse_and_spoken_uses(quality):
    from creative_agent.evidence_policy import transcript_policy
    for purpose in ['retrieve', 'parse', 'script', 'spoken_evidence', 'quote']:
        assert not transcript_policy(asset(quality_state=quality, execution_state='succeeded'), purpose)['allowed']


def test_automatic_usable_text_is_retrievable_but_not_word_exact_evidence():
    from creative_agent.evidence_policy import transcript_policy
    a = asset(quality_state='usable', execution_state='succeeded')
    assert transcript_policy(a)['allowed']
    assert not transcript_policy(a, 'quote')['allowed']
    a['metadata']['transcription']['review_state'] = 'corrected'
    assert transcript_policy(a, 'quote')['allowed']
    assert transcript_policy(a)['segments'][0]['segment_id'].startswith('segment_')


def test_invalid_time_repetition_and_language_conflict_fail_quality_gate():
    from creative_agent.evidence_policy import transcript_policy
    a = asset(quality_state='usable', execution_state='succeeded')
    a['metadata']['subtitles'][0]['end_ms'] = 2200
    assert transcript_policy(a)['quality_state'] == 'unusable'
    a = asset(quality_state='usable', execution_state='succeeded', language='en')
    a['metadata']['speech_language'] = 'zh'
    assert transcript_policy(a)['quality_state'] == 'suspect'
    a = asset(quality_state='usable', execution_state='succeeded')
    a['metadata']['subtitles'] = [{'start_ms': n * 300, 'end_ms': (n + 1) * 300, 'text': '循环重复。'} for n in range(4)]
    assert transcript_policy(a)['quality_state'] == 'suspect'


def manifest():
    return {'id': 'input_1', 'project_id': 'p', 'asset_id': 'a', 'asset_sha256': 'a'*64,
        'pipeline_version': 'video-observation-v2', 'route': 'sampled_frames', 'duration_ms': 2000,
        'requested_range': {'start_ms': 0, 'end_ms': 2000}, 'model_binding_revision': 'binding_1',
        'frames': [{'frame_id': 'frame_1', 'time_ms': 120, 'sha256': 'b'*64}],
        'transcript': {'allowed': True, 'quality_state': 'usable', 'review_state': 'corrected',
            'segments': [{'segment_id': 'segment_1', 'start_ms': 0, 'end_ms': 1000}]}}


def observation(kind='visual_observation', support=None):
    return {'claim_kind': kind, 'statement': '看见杯盖', 'interpretation': 'observed',
            'support': support if support is not None else [{'frame_id': 'frame_1', 'time_ms': 120}]}


def test_observation_requires_specific_input_of_correct_modality_and_version():
    from creative_agent.evidence_policy import validate_observations
    m = manifest()
    assert validate_observations(m, [observation()])[0]['input_manifest_id'] == m['id']
    bad = [observation(support=[{'asset_id': 'a'}]), observation(support=[{'frame_id': 'foreign'}]),
        observation('spoken_content'), observation(support=[{'frame_id': 'frame_1', 'time_ms': 999}]),
        {**observation(), 'asset_sha256': 'c'*64}, {**observation(), 'input_manifest_id': 'foreign'},
        {**observation(), 'pipeline_version': 'old'}, observation('acoustic_observation')]
    for row in bad:
        with pytest.raises(ValueError):
            validate_observations(m, [row])


def test_suspect_text_and_out_of_bounds_native_ranges_are_rejected():
    from creative_agent.evidence_policy import validate_observations
    m = manifest()
    row = observation('spoken_content', [{'transcript_segment_id': 'segment_1', 'start_ms': 0, 'end_ms': 1000}])
    assert validate_observations(m, [row])[0]['quality_state'] == 'usable'
    m['transcript']['allowed'] = False
    with pytest.raises(ValueError): validate_observations(m, [row])
    m['route'] = 'native_video'
    m['native_video'] = {'start_ms': 0, 'end_ms': 2000, 'sha256': 'a'*64}
    with pytest.raises(ValueError): validate_observations(m, [observation(support=[{'type': 'native_video', 'start_ms': 0, 'end_ms': 2100}])])


def test_screen_numbers_remain_uncertain_and_never_become_product_facts():
    from creative_agent.evidence_policy import validate_observations
    m = manifest()
    row = observation('screen_text'); row['statement'] = '售价 9.9 元'
    out = validate_observations(m, [row])[0]
    assert out['quality_state'] == 'suspect' and out['allowed_uses'] == []
    with pytest.raises(ValueError): validate_observations(m, [observation('product_fact')])


def test_mixed_support_and_nested_native_cross_version_are_not_accepted():
    from creative_agent.evidence_policy import validate_observations
    m = manifest()
    with pytest.raises(ValueError):
        validate_observations(m, [observation(support=[{'frame_id': 'frame_1', 'transcript_segment_id': 'segment_1'}])])
    m['route'] = 'native_video'
    m['native_video'] = {'start_ms': 0, 'end_ms': 2000, 'sha256': 'a'*64}
    with pytest.raises(ValueError):
        validate_observations(m, [observation(support=[{'native_video': {
            'start_ms': 0, 'end_ms': 1000, 'asset_sha256': 'c'*64}}])])
