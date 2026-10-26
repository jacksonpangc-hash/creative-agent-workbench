"""Shared, fail-closed policy for transcript use and granular video observations.

Validation proves that a reference was in the submitted inputs. It cannot prove
that a model's natural-language interpretation is correct or a product claim true.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter

PIPELINE_VERSION = 'video-observation-v2'
QUALITY_STATES = {'unknown', 'usable', 'suspect', 'unusable'}
EXECUTION_STATES = {'pending', 'running', 'succeeded', 'failed', 'cancelled'}


def content_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def transcript_policy(asset, purpose='retrieve'):
    """Return permitted uses and versioned segments; never upgrade legacy ASR.

    `quote` and `spoken_evidence` mean word-exact quotation; `parse`, `script`,
    `retrieve`, `inspiration`, `structure` allow labelled automatic usable text.
    Unknown purposes fail closed. Source statements never establish product facts.
    """
    metadata = asset.get('metadata') if isinstance(asset.get('metadata'), dict) else {}
    raw = metadata.get('subtitles')
    provenance = metadata.get('transcription') if isinstance(metadata.get('transcription'), dict) else {}
    source = metadata.get('transcript_source') or provenance.get('method') or 'unknown'
    review = 'corrected' if provenance.get('review_state') == 'corrected' or provenance.get('reviewed') is True else 'unreviewed'
    execution = provenance.get('execution_state') or ('succeeded' if raw else 'pending')
    if execution not in EXECUTION_STATES:
        execution = 'failed'
    declared = provenance.get('quality_state')
    quality = declared if declared in QUALITY_STATES else 'unknown'
    # Explicit user subtitle import is usable text with known provenance, but
    # importing a file alone is not a claim that every word was manually checked.
    if source in {'user_import', 'user_corrected', 'manual'} and declared not in QUALITY_STATES:
        quality = 'usable'
    if review == 'corrected' and declared not in {'suspect', 'unusable'}:
        quality = 'usable'
    reasons = []
    duration = metadata.get('duration_ms')
    valid_duration = type(duration) in (int, float) and math.isfinite(duration) and duration > 0
    valid = valid_duration and isinstance(raw, list) and bool(raw)
    previous = -1
    for row in raw if valid_duration and isinstance(raw, list) else []:
        if (not isinstance(row, dict) or type(row.get('start_ms')) is not int or type(row.get('end_ms')) is not int
                or not 0 <= row['start_ms'] < row['end_ms'] <= duration or row['start_ms'] < previous
                or not isinstance(row.get('text'), str) or not row['text'].strip()):
            valid = False
            break
        previous = row['start_ms']
    if not valid:
        quality = 'unusable'
        reasons.append('empty_or_invalid_timestamped_transcript')
    elif quality != 'unusable':
        normalized = [re.sub(r'[\W_]+', '', row['text']).casefold() for row in raw]
        counts = Counter(t for t in normalized if t)
        repeated = max(counts.values(), default=0)
        if repeated >= 3 and repeated * 2 > len(raw):
            quality = 'suspect'; reasons.append('abnormal_repetition')
        # Conservative risk flags, not estimated recognition accuracy.
        for row in raw:
            characters = len(re.sub(r'\s', '', row['text']))
            seconds = (row['end_ms'] - row['start_ms']) / 1000
            if characters / seconds > 35 or (seconds > 20 and characters < 3):
                quality = 'suspect'; reasons.append('abnormal_text_duration_ratio'); break
        if provenance.get('no_speech') is True or provenance.get('speech_detected') is False:
            quality = 'suspect'; reasons.append('text_in_no_speech_region')
        if any(row.get('no_speech') is True for row in raw):
            quality = 'suspect'; reasons.append('text_in_no_speech_region')
        config = provenance.get('decode_config') if isinstance(provenance.get('decode_config'), dict) else {}
        requested = metadata.get('speech_language') or config.get('requested_language', 'auto')
        detected = provenance.get('language', 'unknown')
        if requested not in {'auto', 'unknown', None} and detected not in {requested, 'unknown', 'mixed_or_unknown', None}:
            quality = 'suspect'; reasons.append('language_conflict')
        if provenance.get('warnings') and review != 'corrected':
            quality = 'suspect'; reasons.append('runtime_quality_warning')
    if execution != 'succeeded':
        reasons.append('execution_not_succeeded')
    if quality == 'unknown': reasons.append('quality_not_evaluated')
    revision = content_hash({'asset_sha256': asset.get('sha256'), 'source': source,
                             'subtitles': raw if valid else [], 'transcription': provenance})
    segments = []
    if valid:
        for row in raw:
            segment = {k: row[k] for k in ('start_ms', 'end_ms', 'text')}
            segment['segment_id'] = 'segment_' + content_hash({'asset_id': asset.get('id'),
                'asset_sha256': asset.get('sha256'), 'revision': revision, **segment})[:32]
            segments.append(segment)
    uses = ['retrieve', 'inspiration', 'structure', 'parse', 'script'] if quality == 'usable' and execution == 'succeeded' else []
    if uses and review == 'corrected': uses.extend(['quote', 'spoken_evidence'])
    return {'allowed': purpose in uses, 'quality_state': quality, 'review_state': review,
            'execution_state': execution, 'allowed_uses': uses, 'reasons': list(dict.fromkeys(reasons)),
            'segments': segments, 'transcript_revision': revision, 'source': source,
            'language': provenance.get('language', 'unknown'),
            'not_product_fact': True, 'word_exact': review == 'corrected' and quality == 'usable'}


def validate_observations(manifest, observations):
    """Validate model output against one immutable MediaInputManifest.

    Support: {frame_id,time_ms}, {transcript_segment_id,start_ms,end_ms}, or
    {type:'native_video',start_ms,end_ms}. No whole-video ID substitutions.
    """
    if not isinstance(observations, list): raise ValueError('观察结果必须是列表')
    frames = {f['frame_id']: f for f in manifest.get('frames', [])}
    transcript = manifest.get('transcript') or {}
    segments = {s['segment_id']: s for s in transcript.get('segments', [])}
    output = []
    visual = {'visual_observation', 'screen_text'}
    spoken = {'spoken_content', 'transcript_content', 'spoken_evidence'}
    for raw in observations:
        if not isinstance(raw, dict): raise ValueError('观察结果格式无效')
        for key, expected in [('asset_id', manifest['asset_id']), ('asset_sha256', manifest['asset_sha256']),
                ('input_manifest_id', manifest['id']), ('pipeline_version', manifest['pipeline_version']),
                ('model_binding_revision', manifest['model_binding_revision'])]:
            if key in raw and raw[key] != expected: raise ValueError('观察引用了不同素材、媒体版本或输入清单')
        kind, interpretation = raw.get('claim_kind'), raw.get('interpretation')
        if kind not in visual | spoken | {'creative_intent', 'creative_proposal', 'product_fact', 'provider_content', 'acoustic_observation'}:
            raise ValueError('观察类型无效')
        if interpretation not in {'observed', 'inferred', 'proposed', 'unknown'}:
            raise ValueError('观察的事实/推断/提案状态无效')
        statement = raw.get('statement')
        if not isinstance(statement, str) or not statement.strip(): raise ValueError('观察缺少结论文字')
        support = raw.get('support', [])
        if not isinstance(support, list): raise ValueError('观察依据格式无效')
        if interpretation == 'observed' and (not support or kind not in visual | spoken):
            raise ValueError('该结论没有可验证的画面或台词依据')
        checked = []
        for item in support:
            if not isinstance(item, dict): raise ValueError('观察依据格式无效')
            if item.get('asset_id', manifest['asset_id']) != manifest['asset_id'] or item.get('asset_sha256', manifest['asset_sha256']) != manifest['asset_sha256']:
                raise ValueError('观察依据串素材或媒体版本')
            modes = sum([bool('frame_id' in item), bool('transcript_segment_id' in item or 'segment_id' in item),
                         bool(item.get('type') == 'native_video' or 'native_video' in item)])
            if modes != 1:
                raise ValueError('每项观察依据只能引用一种实际输入模态')
            if 'frame_id' in item:
                if kind not in visual | {'creative_intent', 'creative_proposal'} or item['frame_id'] not in frames:
                    raise ValueError('画面依据不存在或模态错误')
                frame = frames[item['frame_id']]
                if 'time_ms' in item and (type(item['time_ms']) is not int or item['time_ms'] != frame['time_ms']):
                    raise ValueError('帧时间与真实PTS不一致')
                checked.append({'frame_id': frame['frame_id'], 'time_ms': frame['time_ms']})
            elif 'transcript_segment_id' in item or 'segment_id' in item:
                sid = item.get('transcript_segment_id', item.get('segment_id'))
                if kind not in spoken | {'creative_intent', 'creative_proposal'} or sid not in segments or not transcript.get('allowed'):
                    raise ValueError('台词依据不存在、质量不可用或模态错误')
                if kind == 'spoken_evidence' and transcript.get('review_state') != 'corrected':
                    raise ValueError('逐字依据需要人工校对台词')
                segment = segments[sid]
                for field in ('start_ms', 'end_ms'):
                    if field in item and (type(item[field]) is not int or item[field] != segment[field]):
                        raise ValueError('字幕段时间与实际输入不一致')
                checked.append({'transcript_segment_id': sid, 'start_ms': segment['start_ms'], 'end_ms': segment['end_ms']})
            elif item.get('type') == 'native_video' or 'native_video' in item:
                bounds = item.get('native_video', item)
                if not isinstance(bounds, dict): raise ValueError('原生视频依据格式无效')
                for identity_key in ('asset_id', 'asset_sha256'):
                    if identity_key in bounds and bounds[identity_key] != manifest[identity_key]:
                        raise ValueError('原生视频依据串素材或媒体版本')
                native = manifest.get('native_video') or {}
                start, end = bounds.get('start_ms'), bounds.get('end_ms')
                if (kind not in visual | {'creative_intent', 'creative_proposal'} or manifest.get('route') != 'native_video'
                        or not native or type(start) is not int or type(end) is not int
                        or not native['start_ms'] <= start < end <= native['end_ms']):
                    raise ValueError('原生视频依据模态错误或时间越界')
                checked.append({'type': 'native_video', 'start_ms': start, 'end_ms': end})
            else:
                raise ValueError('观察必须引用具体帧、字幕段或原生视频范围')
        quality = raw.get('quality_state', 'usable' if checked else 'unknown')
        if quality not in QUALITY_STATES: raise ValueError('观察质量状态无效')
        if interpretation == 'unknown': quality = 'unknown'
        # OCR has no independent numeric verification in this pipeline.
        if kind == 'screen_text' and re.search(r'\d|[￥¥$%]|[零一二三四五六七八九十百千万亿]+[元克斤毫升]', statement):
            quality = 'suspect'
        uses = ['retrieve', 'inspiration'] if quality == 'usable' and checked and interpretation in {'observed', 'inferred'} else []
        if interpretation == 'proposed' and quality == 'usable': uses = ['inspiration']
        result = {'object_type': 'Observation', 'asset_id': manifest['asset_id'], 'asset_sha256': manifest['asset_sha256'],
            'pipeline_version': manifest['pipeline_version'], 'input_manifest_id': manifest['id'],
            'model_binding_revision': manifest['model_binding_revision'], 'claim_kind': kind,
            'statement': statement.strip(), 'support': checked, 'interpretation': interpretation,
            'quality_state': quality, 'allowed_uses': uses}
        result['observation_id'] = 'obs_' + content_hash(result)[:32]
        output.append(result)
    return output


def persist_observations(store, project_id, manifest_id, observations, *, provider_request_id=None, execution_state='succeeded'):
    manifest = store.get(project_id, 'media_input_manifest', manifest_id)
    # Current primary media must still exist at this exact version.
    asset = store.get(project_id, 'asset', manifest['asset_id'])
    if asset.get('sha256') != manifest['asset_sha256']: raise ValueError('素材媒体版本已变化，请重新观察')
    if transcript_policy(asset)['transcript_revision'] != manifest['transcript']['transcript_revision']:
        raise ValueError('台词版本已变化，请重新观察')
    checked = validate_observations(manifest, observations)
    if execution_state not in {'succeeded', 'failed', 'cancelled'}: raise ValueError('观察执行状态无效')
    if execution_state != 'succeeded' and checked: raise ValueError('未成功完成的观察不能作为有效依据')
    saved = []
    for row in checked:
        record = {**row, 'execution_state': execution_state, 'review_state': 'unreviewed',
                  'provider_request_id': provider_request_id}
        saved.append(store.put_if_absent(project_id, 'observation', record, row['observation_id']))
    update_manifest_execution(store, project_id, manifest_id, execution_state, provider_request_id=provider_request_id)
    return saved


def update_manifest_execution(store, project_id, manifest_id, execution_state, *, provider_request_id=None, error=None):
    """Called by orchestration after actual transport, never by media preparation."""
    if execution_state not in EXECUTION_STATES: raise ValueError('输入清单执行状态无效')
    manifest = store.get(project_id, 'media_input_manifest', manifest_id)
    attempts = list(manifest.get('transport_receipts') or [])
    if execution_state in {'succeeded', 'failed', 'cancelled'}:
        receipt = {'execution_state': execution_state, 'provider_request_id': provider_request_id, 'error': error}
        if receipt not in attempts:
            attempts.append(receipt)
    return store.put(project_id, 'media_input_manifest', {**manifest, 'execution_state': execution_state,
        'provider_request_id': provider_request_id, 'error': error, 'transport_receipts': attempts}, manifest_id)
