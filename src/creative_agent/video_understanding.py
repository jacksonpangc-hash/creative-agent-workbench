"""Callable, local video-understanding workflow for explicitly planned new jobs.

Image-capable providers receive actual JPEGs and aligned text; no text-only model
is presented as having seen video. This module never calls a remote service,
downloads ASR weights, or infers that native-video transport includes audio.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from bisect import bisect_left
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image

from .evidence_policy import PIPELINE_VERSION, content_hash, transcript_policy
from .model_catalog import model_availability
from .providers import ProviderClient


def plan_video_route(binding):
    """Freeze the supported route before fee reservation; never rewrite old jobs."""
    config = binding.model_dump() if hasattr(binding, 'model_dump') else binding
    status = model_availability(config)['video_analysis']
    if not status['available']:
        raise ValueError(status['reason'])
    sampled = status['route'] == 'sampled_frames'
    return {
        'version': 'video-understanding-v1', 'workflow_id': 'builtin.video-understanding',
        'route': status['route'], 'label': status['label'],
        'model': {k: config.get(k) for k in ('provider', 'model_id')},
        'max_frames': 36 if sampled else None,
        'sampling': 'scene_boundaries_dense_opening_distributed_actual_pts' if sampled else 'provider_native_video',
        'opening_window_ms': 3000 if sampled else None,
        'transcript_policy': 'timestamped_if_available',
        'steps': (['decode_timestamped_frames', 'load_timestamped_transcript', 'vision_observation'] if sampled
                  else ['load_native_video', 'load_timestamped_transcript', 'native_observation']),
        'limitations': (['每条素材最多36张抽样帧，覆盖场景、前3秒和全片分布；不是逐帧观察，可能漏掉短暂字幕、动作或转场。',
                         '只按提供的时间戳文字稿分析语音；未转写的声音、音乐和语气不在观察范围。'] if sampled
                        else ['已实现原生视频输入，但不保证供应商观察全部帧或处理音轨；未提供时间戳文字稿时语音信息不完整。']),
    }


class VideoUnderstanding:
    def __init__(self, store, media):
        self.store, self.media = store, media

    @staticmethod
    def _transcript(asset):
        policy = transcript_policy(asset, 'parse')
        metadata = asset.get('metadata', {})
        if not policy['allowed']:
            policy = {**policy, 'segments': []}
            return {'transcript': '', 'subtitles': [], 'transcript_provenance': None,
                    'transcript_policy': policy, 'audio_observation': 'missing_timestamped_transcript'}
        segments = policy['segments']
        source = policy['source']
        return {'transcript': '\n'.join(item['text'] for item in segments), 'subtitles': deepcopy(segments),
                'transcript_provenance': 'local_asr' if source in {'faster-whisper-local', 'whisper.cpp-local'} else 'subtitle',
                'transcription': deepcopy(metadata.get('transcription') or {}), 'transcript_source': source,
                'transcript_policy': policy, 'audio_observation': 'timestamped_text_only'}

    def _sample(self, p, asset, max_frames, interval=None):
        path = self.media.file(p, asset['id'])
        if not asset.get('metadata', {}).get('frame_index_path'):
            raise ValueError('视频缺少有效的时间索引，请先重新处理素材')
        index_path = Path(asset['metadata']['frame_index_path']).resolve()
        if not index_path.is_relative_to(path.parent) or not index_path.is_file():
            raise ValueError('视频帧索引越界或已丢失')
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if (not isinstance(index, list) or not index
                or any(not isinstance(f, dict) or type(f.get('index')) is not int or f['index'] < 0
                       or type(f.get('time_ms')) is not int or f['time_ms'] < 0 for f in index)):
            raise ValueError('视频帧索引无效')
        if interval:
            index=[f for f in index if interval['start_ms']<=f['time_ms']<interval['end_ms']]
            if not index:raise ValueError('所选片段没有可观察帧，请调整时间范围')
        times = [f['time_ms'] for f in index]
        if times != sorted(times):
            raise ValueError('视频帧时间无效')
        # Guarantee distributed coverage first. Give scene starts and the opening
        # separate budgets so a long video cannot consume every slot at its start.
        spread = [round(i * (len(index) - 1) / 11) for i in range(12)]
        scenes = self.media.scenes(p, asset['id'])
        starts = [min(bisect_left(times, s['start_ms']), len(index) - 1) for s in scenes]
        if len(starts) > 12:
            starts = [starts[round(i * (len(starts) - 1) / 11)] for i in range(12)]
        opening = [min(bisect_left(times, t), len(index) - 1)
                   for t in range(0, min(3000, asset['metadata']['duration_ms']), 250)]
        selected = set(spread + starts + opening)
        for i in range(max_frames):
            if len(selected) >= min(max_frames, len(index)):
                break
            selected.add(round(i * (len(index) - 1) / max(1, max_frames - 1)))
        selected = sorted(selected)[:max_frames]
        frame_indices = [index[i]['index'] for i in selected]
        key = hashlib.sha256(json.dumps({'version': 3, 'sha256': asset['sha256'], 'frames': frame_indices}).encode()).hexdigest()[:24]
        directory = path.parent / ('understanding-' + key)
        manifest_path = directory / 'manifest.json'
        if manifest_path.is_file():
            try:
                rows = json.loads(manifest_path.read_text(encoding='utf-8'))
                valid_cache = isinstance(rows, list) and len(rows) == len(frame_indices)
                for expected_index, row in zip(frame_indices, rows):
                    if not isinstance(row, dict):
                        valid_cache = False; break
                    cached_path = Path(row.get('path', '')).resolve()
                    if (not cached_path.is_relative_to(directory) or not cached_path.is_file()
                            or row.get('source_frame_index') != expected_index
                            or type(row.get('time_ms')) is not int or type(row.get('pts')) is not int
                            or not re.fullmatch(r'\d+/\d+', row.get('time_base', ''))
                            or row.get('sha256') != self._digest(cached_path)):
                        valid_cache = False; break
                    identity = {'asset_sha256': asset['sha256'], 'source_frame_index': expected_index,
                        'pts': row['pts'], 'time_base': row['time_base'], 'file_sha256': row['sha256']}
                    if row.get('frame_id') != 'frame_' + content_hash(identity)[:32]:
                        valid_cache = False; break
                if valid_cache:
                    return rows, len(index)
            except (ValueError, TypeError, OSError):
                pass  # Invalid local caches are re-decoded from verified media.
        # One real decode selects input frame indices. showinfo records the PTS
        # of the frames actually written, not requested seek targets.
        with tempfile.TemporaryDirectory(prefix='understanding-', dir=path.parent) as work:
            expression = '+'.join(f'eq(n,{i})' for i in frame_indices)
            decoded = self.media._run(['-i', path, '-map', '0:v:0', '-an', '-vf',
                f"select='{expression}',scale='min(1280,iw)':-2,showinfo", '-vsync', '0', Path(work) / '%06d.jpg'])
            decoded_pts = list(re.finditer(
                r'Parsed_showinfo_\d+.*?\bn:\s*\d+.*?\bpts:\s*(-?\d+).*?pts_time:([\d.e+\-]+)', decoded.stderr))
            timestamps = [round(float(m[2]) * 1000) for m in decoded_pts]
            time_base_match = re.search(r'config in time_base:\s*(\d+/\d+)', decoded.stderr)
            if not time_base_match:
                raise ValueError('视频实际解码时间基缺失')
            time_base = time_base_match[1]
            files = sorted(Path(work).glob('*.jpg'))
            if not files or len(files) != len(timestamps) or len(files) != len(frame_indices):
                raise ValueError('视频实际解码帧与时间戳不一致')
            directory.mkdir(exist_ok=True)
            rows = []
            for i, (filename, time_ms) in enumerate(zip(files, timestamps)):
                target = directory / filename.name
                filename.replace(target)
                digest = self._digest(target)
                with Image.open(target) as image:
                    width, height = image.size
                frame_id = 'frame_' + content_hash({'asset_sha256': asset['sha256'],
                    'source_frame_index': frame_indices[i], 'pts': int(decoded_pts[i][1]),
                    'time_base': time_base, 'file_sha256': digest})[:32]
                rows.append({'path': str(target), 'time_ms': time_ms, 'pts_ms': time_ms,
                    'source_frame_index': frame_indices[i], 'pts': int(decoded_pts[i][1]),
                    'time_base': time_base, 'frame_id': frame_id,
                    'sha256': digest, 'width': width, 'height': height})
            manifest_path.write_text(json.dumps(rows), encoding='utf-8')
        return rows, len(index)

    def prepare(self, project_id, asset_id, binding, *, plan=None, interval=None):
        """Prepare project-scoped real media; suitable for preflight and execution."""
        expected = plan_video_route(binding)
        if plan is not None and plan != expected:
            raise ValueError('视频理解路由快照与当前模型不一致，请创建新任务')
        plan = deepcopy(expected if plan is None else plan)
        asset = self.store.get(project_id, 'asset', asset_id)
        if asset['media_type'] != 'video':
            raise ValueError('内置视频理解需要视频素材')
        if interval and not (type(interval.get('start_ms')) is int and type(interval.get('end_ms')) is int and 0<=interval['start_ms']<interval['end_ms']<=asset['metadata']['duration_ms']):raise ValueError('所选片段时间越界')
        native = plan['route'] == 'native_video'
        path = self.media.file(project_id, asset_id)
        if not native:
            asset = self.media.inspect(project_id, asset_id)
        frames, frame_count = ([], None) if native else self._sample(project_id, asset, plan['max_frames'],interval)
        images = [f['path'] for f in frames]
        video = str(path) if native else None
        if native and interval:
            clip=path.parent/f"analysis-{interval['start_ms']}-{interval['end_ms']}.mp4"
            if not clip.exists():self.media._run(['-y','-ss',str(interval['start_ms']/1000),'-i',path,'-t',str((interval['end_ms']-interval['start_ms'])/1000),'-map','0:v:0','-map','0:a?','-c:v','libx264','-c:a','aac',clip])
            video=str(clip)
        ProviderClient.validate_chat(binding, has_images=bool(images), has_video=native)
        ProviderClient.validate_chat_files(binding, images=images, video=video)
        timestamps = [f['time_ms'] for f in frames]
        duration = asset['metadata']['duration_ms']
        gaps = [b - a for a, b in zip([0, *timestamps], [*timestamps, duration])] if frames else []
        speech = self._transcript(asset)
        if interval:
            speech['transcription']={k:v for k,v in speech.get('transcription',{}).items() if k in {'method','reviewed','needs_review'}}
            speech['subtitles']=[r for r in speech['subtitles'] if r['start_ms']>=interval['start_ms'] and r['end_ms']<=interval['end_ms']]
            speech['transcript']='\n'.join(r['text'] for r in speech['subtitles'])
            speech['transcript_policy']={**speech['transcript_policy'],'segments':deepcopy(speech['subtitles'])}
            if not speech['subtitles']:speech['audio_observation']='missing_timestamped_transcript'
        limitations = list(plan['limitations'])
        if speech['audio_observation'] == 'missing_timestamped_transcript':
            limitations.append('缺少可靠时间戳文字稿，本次不能据此判断语音内容；可先导入字幕或运行已配置的本地转写。')
        evidence = {'provenance': plan['route'], 'workflow': plan, 'observed_timestamps_ms': timestamps,
            'frame_observations': [{'image_number': i + 1, 'time_ms': f['time_ms'], 'source_frame_index': f['source_frame_index']}
                                   for i, f in enumerate(frames)],
            'coverage': {'sampled_frame_count': len(frames), 'decoded_frame_count': frame_count,
                         'complete_frame_coverage': False, 'duration_ms': duration,
                         'max_gap_ms': max(gaps) if gaps else None},
            'limitations': limitations, **speech}
        evidence['coverage']['audio_intervals']=[{'start_ms':r['start_ms'],'end_ms':r['end_ms']} for r in speech['subtitles']]
        evidence['coverage']['audio_basis']='timestamped_transcript'
        evidence['coverage']['visual_basis']='actual_sampled_frames' if frames else 'native_video_provider_coverage_unknown'
        if interval:
            evidence['requested_range']=interval
            evidence['media_time_mapping']={'source_start_ms':interval['start_ms'],'video_starts_at_zero':native}
        manifest = self._manifest(project_id, asset, path, video, frames, binding, evidence, interval)
        evidence.update(input_manifest_id=manifest['id'], pipeline_version=PIPELINE_VERSION,
            asset_sha256=asset['sha256'], model_binding_revision=manifest['model_binding_revision'])
        evidence['frame_observations'] = [{'image_number': i + 1, **{k: v for k, v in f.items() if k != 'path'}}
                                          for i, f in enumerate(frames)]
        return {'images': images, 'video': video, 'evidence': evidence, 'input_manifest': manifest}

    @staticmethod
    def _digest(path):
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    def _manifest(self, project_id, asset, path, video, frames, binding, evidence, interval):
        actual_hash = self._digest(path)
        if actual_hash != asset['sha256']:
            raise ValueError('素材文件与登记版本不一致，请重新导入')
        config = binding.model_dump() if hasattr(binding, 'model_dump') else binding
        # Whitelist only routing facts. Endpoint query strings and credentials
        # never enter input manifests or diagnostic exports.
        selected = {k: config.get(k) for k in ('id', 'provider', 'model_id', 'protocol', 'region', 'role',
            'revision', 'updated_at', 'max_output_tokens') if config.get(k) is not None}
        endpoint = urlsplit(config.get('base_url') or '')
        if endpoint.hostname:
            selected['endpoint'] = urlunsplit((endpoint.scheme, endpoint.hostname +
                (':' + str(endpoint.port) if endpoint.port else ''), endpoint.path, '', ''))
        if not selected.get('protocol'):
            selected['protocol'] = {'anthropic': 'anthropic-messages', 'gemini': 'gemini-generate-content'}.get(config.get('provider'), 'openai-chat-completions')
        revision = 'binding_' + content_hash(selected)[:32]
        # ffmpeg probe emits stream facts without processing or remote traffic.
        probe = self.media._run(['-i', path, '-map', '0:v:0', '-frames:v', '0', '-f', 'null', '-'])
        has_audio = bool(re.search(r'Stream #.*Audio:', probe.stderr))
        policy = deepcopy(evidence['transcript_policy'])
        segment_ids = {s['segment_id'] for s in evidence['subtitles']}
        # Only text actually submitted to this request is referenceable.
        policy['segments'] = [s for s in policy['segments'] if s['segment_id'] in segment_ids]
        limits = interval or {'start_ms': 0, 'end_ms': asset['metadata']['duration_ms']}
        manifest = {'object_type': 'MediaInputManifest', 'project_id': project_id, 'asset_id': asset['id'],
            'asset_sha256': actual_hash, 'pipeline_version': PIPELINE_VERSION,
            'duration_ms': asset['metadata']['duration_ms'], 'audio_track_state': 'present' if has_audio else 'absent',
            'audio_input_submitted': bool(video and has_audio), 'acoustic_understanding_available': False, 'route': evidence['provenance'], 'requested_range': deepcopy(limits),
            'frames': [{k: v for k, v in f.items() if k != 'path'} for f in frames],
            'transcript': policy, 'transcript_runtime': {k: v for k, v in (asset.get('metadata', {}).get('transcription') or {}).items()
                if k in {'method', 'version', 'model', 'model_sha256', 'language', 'decode_config', 'execution_state', 'quality_state', 'review_state'}},
            'coverage': deepcopy(evidence['coverage']), 'sampling': evidence['workflow']['sampling'],
            'model_binding': selected, 'model_binding_revision': revision,
            'native_video': {'start_ms': limits['start_ms'], 'end_ms': limits['end_ms'],
                'sha256': self._digest(video), 'time_origin_ms': limits['start_ms']} if video else None}
        # Untimed or rejected raw transcript is intentionally absent.
        fingerprint = content_hash(manifest)
        identifier = 'input_' + fingerprint[:32]
        try:
            cached = self.store.get(project_id, 'media_input_manifest', identifier)
        except ValueError:
            cached = None
        if cached:
            return {**cached, 'cache_reused': True}
        return self.store.put_if_absent(project_id, 'media_input_manifest', {**manifest, 'manifest_sha256': fingerprint,
            'execution_state': 'pending', 'provider_request_id': None, 'cache_reused': False}, identifier)
