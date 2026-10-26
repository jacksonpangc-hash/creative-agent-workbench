"""Local transcription with revision-aware caching and cooperative cancellation.

The desktop defaults to bundled whisper.cpp. A previously configured optional
faster-whisper installation remains readable; failures never switch engines.
"""
import math
import os
import time
from pathlib import Path

from .evidence_policy import content_hash, transcript_policy
from .store import now


def _legacy_model_directory():
    import sys
    return '' if getattr(sys,'frozen',False) else os.environ.get('CREATIVE_WHISPER_MODEL','')


class Transcriber:
    def __init__(self, store, media):
        self.store, self.media = store, media

    @staticmethod
    def model_path():
        configured = _legacy_model_directory()
        if not configured:
            from .asr_runtime import resource_directory
            return resource_directory()
        path = Path(configured).expanduser().resolve()
        if not path.is_dir() or not (path / 'model.bin').is_file():
            raise ValueError('当前开发转写模型不可用。桌面版请使用完整安装包的内置口播识别。')
        return path

    def run(self, p, asset_ids, *, cancelled=None, force=False):
        """Process a bounded batch; callback cancellation reaches the owned CLI.

        `force` explicitly requests re-recognition. User corrections are preserved
        by default. Failed attempts never erase a previously usable transcript.
        """
        from .asr_runtime import TranscriptionCancelled, NoSpeechDetected
        assets = [self.store.get(p, 'asset', aid) for aid in dict.fromkeys(asset_ids)]
        if not 1 <= len(assets) <= 200 or any(a['media_type'] != 'video' for a in assets):
            raise ValueError('转写需要1–200条视频素材')
        if _legacy_model_directory() and not (cancelled and cancelled()):
            self.model_path()
        record = self.store.put(p, 'transcription', {'asset_ids': [a['id'] for a in assets],
            'completed': 0, 'cached': 0, 'state': 'running', 'execution_state': 'running',
            'quality_state': 'unknown', 'review_state': 'unreviewed', 'errors': [], 'asset_results': []})
        started = time.monotonic()
        legacy_model, signature = None, None
        cancelled_run = False
        for asset in assets:
            asset_started = time.monotonic()
            if cancelled and cancelled():
                cancelled_run = True
                break
            try:
                duration = asset.get('metadata', {}).get('duration_ms')
                if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                    raise ValueError('素材时长无效，请先完成本地媒体处理')
                policy = transcript_policy(asset)
                old = asset.get('metadata', {}).get('transcription') or {}
                if policy['allowed'] and (policy['review_state'] == 'corrected' or policy['source'] in {'user_import','user_corrected','manual','user_subtitle'}) and not force:
                    record['completed'] += 1; record['cached'] += 1
                    record['asset_results'].append({'asset_id': asset['id'], 'execution_state': 'succeeded',
                        'quality_state': policy['quality_state'], 'review_state': 'corrected',
                        'cache_reused': True, 'reason': 'preserved_corrected_transcript'})
                    continue
                language = asset.get('metadata', {}).get('speech_language', 'auto')
                configured = _legacy_model_directory()
                if signature is None:
                    if configured:
                        path = self.model_path()
                        stat = (path / 'model.bin').stat()
                        signature = {'method': 'faster-whisper-local', 'model': str(path),
                            'model_size': stat.st_size, 'model_mtime_ns': stat.st_mtime_ns, 'beam_size': 5}
                    else:
                        from .asr_runtime import runtime_signature
                        signature = runtime_signature()
                cache_key = content_hash({'asset_sha256': asset.get('sha256'), 'duration_ms': duration,
                    'language': language, 'runtime': signature})
                if asset.get('sha256'):
                    import hashlib
                    with self.media.file(p, asset['id']).open('rb') as media_stream:
                        actual_hash = hashlib.file_digest(media_stream, 'sha256').hexdigest()
                    if actual_hash != asset['sha256']:
                        raise ValueError('素材文件与登记版本不一致，请重新导入')
                subtitles_hash = content_hash(asset.get('metadata', {}).get('subtitles', []))
                if (not force and old.get('cache_key') == cache_key and old.get('subtitles_sha256') == subtitles_hash
                        and old.get('execution_state') == 'succeeded' and asset.get('sha256')):
                    record['completed'] += 1; record['cached'] += 1
                    record['asset_results'].append({'asset_id': asset['id'], 'execution_state': 'succeeded',
                        'quality_state': policy['quality_state'], 'review_state': policy['review_state'], 'cache_reused': True})
                    continue
                if configured:
                    if legacy_model is None:
                        try:
                            from faster_whisper import WhisperModel
                        except ImportError:
                            raise ValueError('未安装可选本地转写组件 faster-whisper') from None
                        legacy_model = WhisperModel(str(path), device='cpu', compute_type='int8', local_files_only=True)
                    segments, info = legacy_model.transcribe(str(self.media.file(p, asset['id'])), beam_size=5)
                    subtitles = []
                    for segment in segments:
                        if cancelled and cancelled(): raise TranscriptionCancelled('本地转写已取消')
                        if not math.isfinite(segment.start) or not math.isfinite(segment.end): raise ValueError('转写时间无效')
                        start, end = round(segment.start * 1000), round(segment.end * 1000)
                        if not 0 <= start < end <= duration + 100: raise ValueError('转写时间超出素材范围')
                        end = min(end, duration)
                        if segment.text.strip(): subtitles.append({'start_ms': start, 'end_ms': end, 'text': segment.text.strip()})
                    provenance = {'method': 'faster-whisper-local', 'model': path.name, 'language': info.language,
                        'execution_state': 'succeeded', 'quality_state': 'usable', 'review_state': 'unreviewed',
                        'reviewed': False, 'runtime_signature': signature}
                else:
                    from .asr_runtime import transcribe
                    options = {'language': language} if language != 'auto' else {}
                    if cancelled is not None: options['cancelled'] = cancelled
                    subtitles, provenance = transcribe(self.media.file(p, asset['id']), duration, self.media.ffmpeg, **options)
                if cancelled and cancelled(): raise TranscriptionCancelled('本地转写已取消')
                if not subtitles: raise ValueError('没有识别到可用口播，请核对音轨或手工提供字幕')
                fresh = self.store.get(p, 'asset', asset['id'])
                if fresh.get('sha256') != asset.get('sha256') or content_hash(fresh.get('metadata', {}).get('subtitles', [])) != subtitles_hash:
                    raise ValueError('转写期间素材或字幕已修改，已保留新资料，请重新操作')
                provenance = {**provenance, 'execution_state': 'succeeded', 'review_state': 'unreviewed',
                    'created_at': now(), 'reviewed': False, 'cache_key': cache_key,
                    'subtitles_sha256': content_hash(subtitles), 'elapsed_seconds': round(time.monotonic() - asset_started, 3),
                    'audio_duration_ms': provenance.get('audio_duration_ms', duration), 'failure_reason': None}
                candidate = {**fresh, 'metadata': {**fresh.get('metadata', {}), 'subtitles': subtitles,
                    'transcript_source': provenance['method'], 'transcription': provenance}}
                evaluated = transcript_policy(candidate)
                provenance['quality_state'] = evaluated['quality_state']
                provenance['quality_reasons'] = evaluated['reasons']
                fresh['metadata'].update(subtitles=subtitles, transcript='\n'.join(s['text'] for s in subtitles),
                    transcript_source=provenance['method'], transcript_provenance='local_asr', transcription=provenance)
                fresh.pop('embedding', None)
                self.store.put(p, 'asset', fresh, asset['id'])
                record['completed'] += 1
                record['asset_results'].append({'asset_id': asset['id'], 'execution_state': 'succeeded',
                    'quality_state': evaluated['quality_state'], 'review_state': evaluated['review_state'],
                    'cache_reused': False, 'elapsed_seconds': provenance['elapsed_seconds']})
            except TranscriptionCancelled:
                cancelled_run = True
                break
            except NoSpeechDetected as exc:
                fresh=self.store.get(p,'asset',asset['id'])
                if fresh.get('sha256')!=asset.get('sha256') or content_hash(fresh.get('metadata',{}).get('subtitles',[]))!=content_hash(asset.get('metadata',{}).get('subtitles',[])):
                    record['errors'].append({'asset_id':asset['id'],'error':'识别期间字幕已更新，已保留人工内容'})
                    record['asset_results'].append({'asset_id':asset['id'],'execution_state':'failed','quality_state':'unusable','review_state':'unreviewed','failure_reason':'concurrent_correction'})
                    continue
                provenance={'method':'whisper.cpp-local','execution_state':'succeeded','quality_state':'unusable',
                    'review_state':'unreviewed','no_speech':True,'speech_detected':False,'reason':exc.reason,'created_at':now(),
                    'cache_key':cache_key,'subtitles_sha256':content_hash(fresh.get('metadata',{}).get('subtitles',[]))}
                fresh.setdefault('metadata',{})['transcription']=provenance
                fresh['metadata']['transcription_attempt']=provenance
                self.store.put(p,'asset',fresh,asset['id'])
                record['completed']+=1
                record['asset_results'].append({'asset_id':asset['id'],**provenance})
            except Exception as exc:  # per-asset isolation; no text from an error is used as evidence
                error = str(exc) if isinstance(exc, ValueError) else '本地转写失败'
                record['errors'].append({'asset_id': asset['id'], 'error': error})
                result = {'asset_id': asset['id'], 'execution_state': 'failed', 'quality_state': 'unusable',
                    'review_state': 'unreviewed', 'failure_reason': error,
                    'audio_duration_ms': asset.get('metadata', {}).get('duration_ms'),
                    'elapsed_seconds': round(time.monotonic() - asset_started, 3)}
                record['asset_results'].append(result)
                fresh = self.store.get(p, 'asset', asset['id'])
                fresh.setdefault('metadata', {})['transcription_attempt'] = {**result, 'created_at': now()}
                self.store.put(p, 'asset', fresh, asset['id'])
            finally:
                record = self.store.put(p, 'transcription', record, record['id'])
        execution = 'cancelled' if cancelled_run else 'failed' if record['errors'] else 'succeeded'
        qualities = {row['quality_state'] for row in record['asset_results']}
        quality = next(iter(qualities)) if len(qualities) == 1 else 'unknown'
        return self.store.put(p, 'transcription', {**record, 'execution_state': execution,
            'state': 'cancelled' if cancelled_run else 'needs_attention' if record['errors'] else 'succeeded',
            'quality_state': quality, 'elapsed_seconds': round(time.monotonic() - started, 3)}, record['id'])

    def _bundled(self, p, asset_ids, **kwargs):
        """Compatibility entry point; defaults still use bundled whisper.cpp."""
        return self.run(p, asset_ids, **kwargs)
