"""Explicit, free reference-media acquisition. No browser credentials or paid fallback."""
from __future__ import annotations

import importlib.metadata
import math
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .contracts import CostQuote
from .discovery import identity
from .media import validate_remote_url
from .store import Conflict, NotFound, now


_FAILURES = {
    'adapter_unavailable': '取片组件未安装，请补充本地视频文件。',
    'unsupported_platform': '当前免费取片组件不支持这个平台，请补充本地视频文件。',
    'unresolved_identity': '未能确认链接对应的作品，请打开作品页或补充本地视频文件。',
    'identity_mismatch': '取得的作品身份与所选参考不一致，未关联视频。',
    'unsupported_media': '链接未提供可处理的单条视频，请补充本地视频文件。',
    'login_required': '平台要求登录或验证，未读取浏览器凭证，请补充本地视频文件。',
    'platform_restricted': '平台限制了本次获取，请补充本地视频文件。',
    'not_available': '作品已删除、不可见或链接已失效，请补充本地视频文件。',
    'network_error': '网络连接失败或超时，可重试或补充本地视频文件。',
    'too_large': '视频超过本地文件大小上限，请补充符合上限的视频文件。',
    'invalid_file': '取得的文件为空或不是可读取的视频，请补充本地视频文件。',
    'media_processing_failed': '视频已保存，但本地处理未完成，可重试或补充视频文件。',
    'permission_required': '当前工作权限未通过检查，尚未开始获取视频。',
    'unknown_error': '未能取得视频，原因尚未确认，请补充本地视频文件。',
}


class MediaFetchError(ValueError):
    def __init__(self, code):
        self.code = code if code in _FAILURES else 'unknown_error'
        super().__init__(_FAILURES[self.code])


def classify_failure(error):
    """Persist bounded categories, never downloader diagnostics or signed URLs."""
    code = error.code if isinstance(error, MediaFetchError) else None
    if code is None:
        message = str(error).lower()
        if any(word in message for word in ('cookie', 'sign in', 'log in', 'login', 'captcha')):
            code = 'login_required'
        elif '超过本地文件大小上限' in message or 'max-filesize' in message:
            code = 'too_large'
        elif any(word in message for word in ('requested format', 'unsupported url', 'no video formats')):
            code = 'unsupported_media'
        elif any(word in message for word in ('403', '429', 'forbidden', 'rate limit', 'geo restrict')):
            code = 'platform_restricted'
        elif any(word in message for word in ('404', 'not available', 'not found', 'removed', 'private video')):
            code = 'not_available'
        elif isinstance(error, (httpx.TimeoutException, httpx.NetworkError, TimeoutError)) or any(
                word in message for word in ('timed out', 'connection refused', 'unable to download', 'name resolution')):
            code = 'network_error'
        else:
            code = 'unknown_error'
    return {'code': code, 'message': _FAILURES[code], 'next_action': 'upload_file'}


class _QuietLogger:
    # yt-dlp diagnostics can include transient signed links; do not log them.
    def debug(self, *_args): pass
    def warning(self, *_args): pass
    def error(self, *_args): pass


def _positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class YtDlpBackend:
    def __init__(self, *, ydl_factory=None, url_validator=validate_remote_url):
        self.ydl_factory, self.url_validator = ydl_factory, url_validator

    def _resolve(self, reference):
        selected = identity(reference['url'])
        if selected['entity_type'] == 'account':
            raise MediaFetchError('unsupported_media')
        if selected['identity_state'] == 'resolved':
            self.url_validator(selected['url'])
            return selected
        # Follow only official public-platform redirects. Never load browser cookies,
        # credentials, page script, or a generic extractor to guess a short-link ID.
        url = selected['url']
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            for _attempt in range(5):
                self.url_validator(url)
                with client.stream('GET', url) as response:
                    if response.is_redirect:
                        try:
                            resolved = identity(urljoin(url, response.headers.get('location', '')))
                        except ValueError:
                            raise MediaFetchError('identity_mismatch') from None
                        if resolved['platform'] != selected['platform']:
                            raise MediaFetchError('identity_mismatch')
                        if resolved['entity_type'] == 'video':
                            return resolved
                        url = resolved['url']
                        continue
                    if response.status_code in (401,):
                        raise MediaFetchError('login_required')
                    if response.status_code in (403, 429):
                        raise MediaFetchError('platform_restricted')
                    if response.status_code in (404, 410):
                        raise MediaFetchError('not_available')
                    raise MediaFetchError('unresolved_identity')
        raise MediaFetchError('unresolved_identity')

    def _validate_info(self, info, selected, maximum):
        if not isinstance(info, dict) or info.get('_type', 'video') != 'video' or info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming'}:
            raise MediaFetchError('unsupported_media')
        if str(info.get('id', '')) != selected['platform_entity_id'] or str(info.get('extractor_key', '')).lower() != 'douyin':
            raise MediaFetchError('identity_mismatch')
        if info.get('ext') not in {'mp4', 'webm', 'mov', 'mkv', 'm4v'} or info.get('vcodec') == 'none' or info.get('requested_formats'):
            raise MediaFetchError('unsupported_media')
        for field in ('filesize', 'filesize_approx'):
            if _positive_number(info.get(field)) and info[field] > maximum:
                raise MediaFetchError('too_large')
        urls = [info.get('url')] + [part.get('url') for part in info.get('fragments', [])]
        for url in urls:
            if not isinstance(url, str) or not url.startswith('https://'):
                raise MediaFetchError('unsupported_media')
            try:
                self.url_validator(url)
            except ValueError:
                raise MediaFetchError('unsupported_media') from None

    def resolve_reference(self, reference):
        if reference['platform'] != 'douyin':
            raise MediaFetchError('unsupported_platform')
        return self._resolve(reference)

    def fetch(self, reference, directory, progress, max_bytes):
        if reference['platform'] != 'douyin':
            # The installed official yt-dlp has DouyinIE, no Kuaishou extractor.
            # Keep the saved Kuaishou reference and offer a file; do not use generic.
            raise MediaFetchError('unsupported_platform')
        selected = self.resolve_reference(reference)
        if reference.get('platform_entity_id') and selected['platform_entity_id'] != reference['platform_entity_id']:
            raise MediaFetchError('identity_mismatch')
        factory = self.ydl_factory
        if factory is None:
            try:
                from yt_dlp import YoutubeDL
            except ImportError:
                raise MediaFetchError('adapter_unavailable') from None
            factory = YoutubeDL
        def update(data):
            downloaded = data.get('downloaded_bytes')
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            if (_positive_number(downloaded) and downloaded > max_bytes
                    or _positive_number(total) and total > max_bytes):
                raise MediaFetchError('too_large')
            progress('downloading', int(downloaded) if _positive_number(downloaded) else 0,
                     int(total) if _positive_number(total) else None)
        options = {'outtmpl': str(directory / 'media.%(ext)s'), 'paths': {'home': str(directory)},
            'format': 'best[ext=mp4][vcodec!=none]/best[vcodec!=none]', 'noplaylist': True,
            'allowed_extractors': ['Douyin'], 'cookiefile': None, 'cookiesfrombrowser': None,
            'cachedir': False, 'usenetrc': False, 'enable_file_urls': False,
            'socket_timeout': 20, 'retries': 0, 'fragment_retries': 0, 'extractor_retries': 0,
            'concurrent_fragment_downloads': 1, 'skip_unavailable_fragments': False,
            'max_filesize': max_bytes, 'overwrites': False, 'continuedl': True,
            'writeinfojson': False, 'writethumbnail': False, 'writesubtitles': False,
            'quiet': True, 'no_warnings': True, 'logger': _QuietLogger(), 'progress_hooks': [update]}
        progress('resolving', 0, None)
        with factory(options) as downloader:
            info = downloader.extract_info(selected['url'], download=False)
            self._validate_info(info, selected, max_bytes)
            progress('downloading', 0, None)
            result = downloader.process_ie_result(info, download=True)
            self._validate_info(result, selected, max_bytes)
            path = Path(result.get('filepath') or downloader.prepare_filename(result)).resolve()
        if not path.is_relative_to(directory.resolve()) or path.is_symlink() or not path.is_file():
            raise MediaFetchError('invalid_file')
        size = path.stat().st_size
        if size > max_bytes:
            raise MediaFetchError('too_large')
        with path.open('rb') as stream:
            header = stream.read(16)
        if size == 0 or not (header[4:8] == b'ftyp' or header.startswith(b'\x1aE\xdf\xa3')):
            raise MediaFetchError('invalid_file')
        try:
            version = importlib.metadata.version('yt-dlp')
        except importlib.metadata.PackageNotFoundError:
            version = 'unavailable'
        return {'identity': selected, 'path': path, 'adapter_version': version}


class ReferenceMedia:
    KIND = 'public_reference_media'

    def __init__(self, store, media, discovery, *, downloader=None, external_guard=None):
        self.store, self.media, self.discovery = store, media, discovery
        self.downloader = downloader or YtDlpBackend()
        self.external_guard = external_guard
        self.lock = threading.RLock()

    def _update(self, p, rid, job_id, state, stage, downloaded=0, total=None, failure=None):
        with self.discovery.lock:
            reference = self.discovery.reference(p, rid)
            reference.pop('media_state', None)
            reference.pop('can_visually_analyze', None)
            reference['media_job_id'] = job_id
            reference['media_fetch'] = {'state': state, 'stage': stage, 'downloaded_bytes': downloaded,
                                        'total_bytes': total, 'updated_at': now()}
            if failure:
                reference['media_fetch']['failure'] = failure
            return self.store.put(p, 'public_reference', reference, reference['id'])

    def request(self, p, rid, rights_confirmed, idempotency_key):
        self.store.project(p)
        reference = self.discovery.reference(p, rid)
        if rights_confirmed is not True:
            raise ValueError('请确认允许下载、留存并分析这个参考视频')
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 120:
            raise ValueError('需要有效的幂等键')
        if reference['entity_type'] == 'account':
            raise ValueError('请选择单条视频作品，不能获取整个账号')
        key = 'public-reference-media:' + idempotency_key
        with self.lock:
            # Check key reuse before collapsing repeat requests for the same work.
            for job in self.store.jobs(p):
                if job['idempotency_key'] == key:
                    if job['kind'] != self.KIND or self.discovery.reference(p, job['payload']['reference_id'])['id'] != reference['id']:
                        raise Conflict('幂等键已用于其他请求')
                    return job
            resume_asset = None
            for job in self.store.jobs(p):
                if job['kind'] != self.KIND:
                    continue
                try:
                    same = self.discovery.reference(p, job['payload']['reference_id'])['id'] == reference['id']
                except NotFound:
                    same = False
                if same and (job['state'] in {'queued', 'running'} or job['state'] == 'succeeded' and reference['can_visually_analyze']):
                    return job
                if same and resume_asset is None and job['state'] == 'failed' and (job.get('result') or {}).get('asset_id'):
                    try:
                        asset = self.store.get(p, 'asset', job['result']['asset_id'])
                        if asset.get('path') and Path(asset['path']).is_file():
                            resume_asset = asset['id']
                    except NotFound:
                        pass
            payload = {'reference_id': reference['id'], 'url': reference['url'], 'rights_confirmed': True}
            if resume_asset:
                payload['resume_asset_id'] = resume_asset
            job = self.store.create_job(p, self.KIND,
                payload,
                CostQuote(amount='0', currency=self.store.project(p)['currency'], basis='用户主动授权的免费参考取片'), key)
            self._update(p, reference['id'], job['id'], 'queued', 'queued')
            return job

    def status(self, p, rid):
        reference = self.discovery.reference(p, rid)
        job = self.store.job(p, reference['media_job_id']) if reference.get('media_job_id') else None
        return {'reference': reference, 'job': job}

    def run(self, p, job_id):
        job = self.store.job(p, job_id)
        if job['kind'] != self.KIND:
            raise ValueError('不是参考取片任务')
        if job['state'] != 'queued':
            return job
        try:
            self.store.transition(p, job_id, 'running')
        except Conflict:
            return self.store.job(p, job_id)
        rid = job['payload']['reference_id']
        checkpoint = job.get('result') or {}
        directory = self.media.root / p / 'reference-fetch' / job_id
        try:
            reference = self.discovery.reference(p, rid)
            if job['payload'].get('rights_confirmed') is not True:
                raise MediaFetchError('permission_required')
            if self.external_guard:
                try:
                    self.external_guard()
                except Exception:
                    raise MediaFetchError('permission_required') from None
            aid = checkpoint.get('asset_id') or job['payload'].get('resume_asset_id')
            if aid:
                checkpoint = {'reference_id': reference['id'], 'asset_id': aid}
                self.store.checkpoint_result(p, job_id, checkpoint)
            if aid is None and reference['can_visually_analyze']:
                aid = reference['asset_id']
            if aid is None:
                directory.mkdir(parents=True, exist_ok=True)
                last = [0.0, None]
                def progress(stage, downloaded=0, total=None):
                    # Bound SQLite writes while retaining per-stage progress for recovery.
                    instant = time.monotonic()
                    if stage != last[1] or instant - last[0] >= .75:
                        self._update(p, rid, job_id, 'running', stage, downloaded, total)
                        last[:] = [instant, stage]
                if hasattr(self.downloader, 'resolve_reference'):
                    progress('resolving')
                    selected = self.downloader.resolve_reference(reference)
                    reference = self.discovery.resolve_identity(p, rid, selected)
                    rid = reference['id']
                    if reference['can_visually_analyze']:
                        aid = reference['asset_id']
                if aid is not None:
                    fetched = None
                else:
                    fetched = self.downloader.fetch(reference, directory, progress, self.media.MAX_FILE_BYTES)
                    reference = self.discovery.resolve_identity(p, rid, fetched['identity'])
                    rid = reference['id']
                # A short link may resolve to a work already saved and processed.
                if reference['can_visually_analyze']:
                    aid = reference['asset_id']
                else:
                    file = Path(fetched['path']).resolve()
                    if not file.is_relative_to(directory.resolve()) or not file.is_file() or file.suffix.lower() not in self.media.VIDEO_EXT:
                        raise MediaFetchError('invalid_file')
                    if not 0 < file.stat().st_size <= self.media.MAX_FILE_BYTES:
                        raise MediaFetchError('too_large' if file.stat().st_size else 'invalid_file')
                    asset = self.media.stage_upload(p, file, source_kind='inspiration', title=reference.get('title') or '收藏参考')
                    aid = asset['id']
                    # Do not overwrite an existing asset's current reuse permissions.
                    # This receipt only grants reference analysis, never advertising reuse.
                    asset['metadata'].setdefault('usage', 'reference_only')
                    asset['metadata'].setdefault('reuse_allowed', False)
                    asset['metadata'].setdefault('source_url', reference['url'])
                    asset['metadata']['reference_media'] = {'reference_id': rid, 'job_id': job_id,
                        'platform': reference['platform'], 'platform_entity_id': reference['platform_entity_id'],
                        'rights_confirmed': True, 'scope': 'download_retain_analyze_reference',
                        'adapter': 'yt-dlp', 'adapter_version': fetched['adapter_version']}
                    self.store.put(p, 'asset', asset, aid)
                checkpoint = {'reference_id': rid, 'asset_id': aid}
                self.store.checkpoint_result(p, job_id, checkpoint)
            self._update(p, rid, job_id, 'running', 'processing')
            try:
                asset = self.media.process_upload(p, aid)
            except Exception:
                raise MediaFetchError('media_processing_failed') from None
            if asset.get('media_type') != 'video' or asset.get('inspection') != 'ready':
                raise MediaFetchError('invalid_file')
            self.discovery.attach(p, rid, aid)
            self._update(p, rid, job_id, 'succeeded', 'available')
            shutil.rmtree(directory, ignore_errors=True)
            return self.store.finish(p, job_id, result={'reference_id': rid, 'asset_id': aid}, actual_cost='0')
        except Exception as error:
            failure = classify_failure(error)
            self._update(p, rid, job_id, 'failed', 'failed', failure=failure)
            # Keep a durable asset checkpoint for processing retry and the original
            # saved reference. Discard failed partial downloads so repeated retries
            # cannot quietly accumulate a full file per failed task.
            shutil.rmtree(directory, ignore_errors=True)
            return self.store.finish(p, job_id, result={**checkpoint, 'reference_id': rid, 'failure': failure},
                                     error=failure['message'], actual_cost='0')
