"""Offline regression coverage; no live-platform compatibility claim."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative_agent.discovery import Discovery, identity
from creative_agent.store import NotFound, Store


VIDEO = 'https://www.douyin.com/video/1234567890123456789'


class LocalMedia:
    MAX_FILE_BYTES = 1024
    VIDEO_EXT = {'.mp4', '.webm'}
    ffmpeg = 'unused-ffmpeg'

    def __init__(self, store, root):
        self.store, self.root = store, root
        self.processed = []

    def stage_upload(self, project, path, **kwargs):
        dest = self.root / project / 'saved.mp4'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(path).read_bytes())
        return self.store.put(project, 'asset', {'path': str(dest), 'media_type': 'video',
            'source_kind': kwargs['source_kind'], 'metadata': {}, 'inspection': 'pending'})

    def process_upload(self, project, aid):
        self.processed.append(aid)
        row = self.store.get(project, 'asset', aid)
        row['inspection'] = 'ready'
        return self.store.put(project, 'asset', row, aid)


class Download:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def fetch(self, reference, directory, progress, max_bytes):
        from creative_agent.media_fetch import MediaFetchError
        self.calls.append(reference['id'])
        progress('downloading', 20, None)
        if self.error:
            raise MediaFetchError(self.error)
        path = directory / 'media.mp4'
        path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'fixture' * 3)
        return {'identity': identity(VIDEO), 'path': path, 'adapter_version': 'fixture'}


@pytest.fixture
def scene(tmp_path):
    from creative_agent.media_fetch import ReferenceMedia
    store = Store(tmp_path / 'db')
    p, q = [store.create_project(name)['id'] for name in ('p', 'q')]
    discovery = Discovery(store)
    ref = discovery.collect(p, [{'url': VIDEO}])[0]
    media, download = LocalMedia(store, tmp_path / 'media'), Download()
    return store, p, q, discovery, ref, media, download, ReferenceMedia(store, media, discovery, downloader=download)


def test_only_explicit_rights_confirmation_creates_free_durable_task(scene):
    store, p, q, discovery, ref, _media, download, fetch = scene
    assert store.jobs(p) == [] and download.calls == []
    with pytest.raises(ValueError, match='允许'):
        fetch.request(p, ref['id'], False, 'one')
    with pytest.raises(NotFound):
        fetch.request(q, ref['id'], True, 'one')
    job = fetch.request(p, ref['id'], True, 'one')
    assert job['kind'] == 'public_reference_media' and job['state'] == 'queued'
    assert job['quote']['amount'] == '0' and download.calls == []
    assert fetch.request(p, ref['id'], True, 'one')['id'] == job['id']
    assert fetch.request(p, ref['id'], True, 'another-key')['id'] == job['id']
    assert discovery.references(p)[0]['media_fetch']['state'] == 'queued'


def test_download_stays_on_reference_and_preserves_reference_only_usage(scene):
    store, p, _q, discovery, ref, media, download, fetch = scene
    job = fetch.request(p, ref['id'], True, 'one')
    result = fetch.run(p, job['id'])
    assert result['state'] == 'succeeded' and len(download.calls) == 1
    saved = discovery.references(p)[0]
    assert saved['id'] == ref['id'] and saved['can_visually_analyze'] is True
    asset = store.get(p, 'asset', saved['asset_id'])
    assert media.processed == [asset['id']]
    assert asset['source_kind'] == 'inspiration'
    assert asset['metadata']['usage'] == 'reference_only'
    assert asset['metadata']['reuse_allowed'] is False
    assert fetch.run(p, job['id']) == result and len(download.calls) == 1
    assert fetch.request(p, ref['id'], True, 'again')['id'] == job['id']


def test_known_and_unknown_failures_are_persisted_without_losing_reference(scene):
    store, p, _q, discovery, ref, _media, download, fetch = scene
    download.error = 'login_required'
    result = fetch.run(p, fetch.request(p, ref['id'], True, 'one')['id'])
    assert result['state'] == 'failed'
    assert result['result']['failure']['code'] == 'login_required'
    assert result['result']['failure']['next_action'] == 'upload_file'
    assert len(discovery.references(p)) == 1 and discovery.references(p)[0]['media_state'] == 'missing'
    def unknown(*args):
        raise RuntimeError('private signed URL and account details must never be stored')
    download.fetch = unknown
    result = fetch.run(p, fetch.request(p, ref['id'], True, 'two')['id'])
    assert result['result']['failure']['code'] == 'unknown_error'
    assert 'private signed' not in str(store.jobs(p))


def test_recovery_resumes_saved_asset_without_another_download(scene):
    from creative_agent.media_fetch import ReferenceMedia
    store, p, _q, discovery, ref, media, download, fetch = scene
    job = fetch.request(p, ref['id'], True, 'one')
    path = media.root / 'checkpoint.mp4'; path.parent.mkdir(); path.write_bytes(b'fixture')
    asset = media.stage_upload(p, path, source_kind='inspiration')
    store.transition(p, job['id'], 'running')
    store.checkpoint_result(p, job['id'], {'reference_id': ref['id'], 'asset_id': asset['id']})
    store.recover()
    resumed = ReferenceMedia(Store(store.path), media, discovery, downloader=download).run(p, job['id'])
    assert resumed['state'] == 'succeeded' and download.calls == []
    assert discovery.references(p)[0]['asset_id'] == asset['id']


def test_shortlink_identity_merge_preserves_canonical_history_and_alias(scene):
    store, p, _q, discovery, ref, _media, download, fetch = scene
    short = discovery.collect(p, [{'url': 'https://v.douyin.com/Chosen/', 'title': 'selected'}])[0]
    done = fetch.run(p, fetch.request(p, short['id'], True, 'short')['id'])
    assert done['state'] == 'succeeded'
    rows = discovery.references(p)
    assert len(rows) == 1 and rows[0]['id'] == ref['id']
    assert len(rows[0]['observations']) == 2
    assert discovery.reference(p, short['id'])['id'] == ref['id']
    assert discovery.collect(p, [{'url': 'https://v.douyin.com/Chosen/'}])[0]['id'] == ref['id']
    assert fetch.request(p, short['id'], True, 'short')['id'] == done['id']
    other = discovery.collect(p, [{'url': 'https://www.douyin.com/video/987654321'}])[0]
    with pytest.raises(ValueError, match='身份'):
        discovery.resolve_identity(p, other['id'], identity(VIDEO))


def test_endpoint_uses_dispatch_only_after_confirmation_and_stays_scoped(scene):
    from creative_agent.discovery_api import register_discovery
    store, p, q, _d, ref, media, _download, _fetch = scene
    dispatched = []
    app = FastAPI()
    register_discovery(app, store, media, dispatch=lambda p, j: dispatched.append(j['id']) or j)
    client = TestClient(app)
    endpoint = f'/api/projects/{p}/discovery/references/{ref["id"]}/media'
    assert client.post(endpoint, json={'rights_confirmed': False, 'idempotency_key': 'x'}).status_code == 400
    assert client.post(endpoint.replace(p, q), json={'rights_confirmed': True, 'idempotency_key': 'x'}).status_code == 404
    response = client.post(endpoint, json={'rights_confirmed': True, 'idempotency_key': 'x'})
    assert response.status_code == 200 and dispatched == [response.json()['id']]
    assert client.get(endpoint).json()['job']['id'] == response.json()['id']


def test_ytdlp_checks_identity_single_video_and_size_before_download(tmp_path):
    from creative_agent.media_fetch import MediaFetchError, YtDlpBackend
    reference = {'id': 'r', **identity(VIDEO)}
    downloaded, options = [], []
    info = {'id': '1234567890123456789', 'extractor_key': 'Douyin', 'ext': 'mp4',
            'vcodec': 'h264', 'url': 'https://cdn.example/media.mp4', 'filesize': 40}
    class YDL:
        def __init__(self, opts): options.append(opts)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            assert download is False
            return dict(info)
        def process_ie_result(self, result, download):
            downloaded.append(result['id'])
            (tmp_path / 'media.mp4').write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 20)
            return {**result, 'filepath': str(tmp_path / 'media.mp4')}
        def prepare_filename(self, result): return str(tmp_path / 'media.mp4')
    backend = YtDlpBackend(ydl_factory=YDL, url_validator=lambda url: None)
    for changes, code in [({'id': 'other'}, 'identity_mismatch'), ({'_type': 'playlist'}, 'unsupported_media'),
                           ({'vcodec': 'none'}, 'unsupported_media'), ({'filesize': 9999}, 'too_large')]:
        old = dict(info); info.update(changes)
        with pytest.raises(MediaFetchError) as exc:
            backend.fetch(reference, tmp_path, lambda *args: None, 1024)
        assert exc.value.code == code
        info.clear(); info.update(old)
    assert downloaded == []
    backend.fetch(reference, tmp_path, lambda *args: None, 1024)
    assert downloaded == ['1234567890123456789']
    assert options[-1]['cookiefile'] is None and options[-1]['cookiesfrombrowser'] is None
    assert options[-1]['cachedir'] is False and options[-1]['noplaylist'] is True
    assert options[-1]['allowed_extractors'] == ['Douyin']
    with pytest.raises(MediaFetchError) as exc:
        options[-1]['progress_hooks'][0]({'downloaded_bytes': 2048})
    assert exc.value.code == 'too_large'


def test_unsupported_kuaishou_and_unknown_backend_errors_have_distinct_classification(tmp_path):
    from creative_agent.media_fetch import MediaFetchError, YtDlpBackend, classify_failure
    with pytest.raises(MediaFetchError) as exc:
        YtDlpBackend().fetch(identity('https://www.kuaishou.com/short-video/abc'), tmp_path, lambda *a: None, 100)
    assert exc.value.code == 'unsupported_platform'
    assert classify_failure(RuntimeError('random'))['code'] == 'unknown_error'
    assert classify_failure(RuntimeError('Fresh cookies (not necessarily logged in) are needed'))['code'] == 'login_required'
    assert classify_failure(RuntimeError('HTTP Error 403: Forbidden'))['code'] == 'platform_restricted'


def test_failed_processing_retry_reuses_asset_checkpoint_and_never_redownloads(scene):
    store, p, _q, discovery, ref, media, download, fetch = scene
    process = media.process_upload
    def fail(*args):
        raise ValueError('decoder failure')
    media.process_upload = fail
    failed = fetch.run(p, fetch.request(p, ref['id'], True, 'first')['id'])
    assert failed['result']['failure']['code'] == 'media_processing_failed'
    media.process_upload = process
    succeeded = fetch.run(p, fetch.request(p, ref['id'], True, 'retry')['id'])
    assert succeeded['state'] == 'succeeded' and len(download.calls) == 1
    assert discovery.references(p)[0]['asset_id'] == failed['result']['asset_id']


def test_shortlink_redirect_cannot_change_platform_or_escape_public_platform(monkeypatch):
    import creative_agent.media_fetch as module
    class Response:
        is_redirect = True
        headers = {'location': 'https://127.0.0.1/private'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, *args): return Response()
    monkeypatch.setattr(module.httpx, 'Client', Client)
    backend = module.YtDlpBackend(url_validator=lambda url: None)
    with pytest.raises(module.MediaFetchError) as exc:
        backend.resolve_reference(identity('https://v.douyin.com/Chosen/'))
    assert exc.value.code == 'identity_mismatch'


def test_extension_receipt_reports_observed_created_and_updated_counts(scene):
    from creative_agent.discovery_api import register_discovery
    store, p, _q, _d, _ref, _media, _download, _fetch = scene
    app = FastAPI(); register_discovery(app, store)
    client = TestClient(app)
    origin = 'chrome-extension://' + 'a' * 32
    paired = app.state.discovery_pairings.create(p)
    redeemed = app.state.discovery_pairings.redeem(paired['code'], origin)
    response = client.post('/extension/import', headers={'Origin': origin}, json={
        'import_token': redeemed['import_token'], 'captures': [{'url': VIDEO},
        {'url': 'https://www.douyin.com/video/987654321'}]})
    assert response.status_code == 200
    assert response.json()['receipt'] == {'created': 1, 'updated': 1, 'skipped': 0, 'failed': 0}
    assert response.json()['project_id'] == p
