"""Local upload receipts must survive slow/failed media inspection and safe retries."""
import io
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg
import pytest

from creative_agent.media import MediaService
from creative_agent.store import Store


@pytest.fixture
def upload_env(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    project = store.create_project("local uploads")["id"]
    return store, project, MediaService(store, tmp_path / "media")


@pytest.fixture
def local_video(tmp_path):
    path = tmp_path / "local.mp4"
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=10:d=1.6", "-c:v", "libx264", str(path),
    ], capture_output=True, check=True)
    return path


def require_staging(media):
    assert callable(getattr(media, "stage_upload", None)), "upload needs a durable receipt before decoding"
    assert callable(getattr(media, "process_upload", None)), "queued local inspection must be executable"


def test_real_video_receipt_persists_original_without_starting_a_decoder(upload_env, local_video, monkeypatch):
    store, project, media = upload_env
    require_staging(media)
    def deny_decoding(*args, **kwargs):
        raise AssertionError("receipt must not wait for FFmpeg")
    monkeypatch.setattr(imageio_ffmpeg, "read_frames", deny_decoding)
    monkeypatch.setattr(media, "_run", deny_decoding)

    asset = media.stage_upload(project, local_video, title="user video.mp4")

    assert asset["inspection"] == "pending"
    assert asset["metadata"]["processing"]["state"] == "queued"
    assert "duration_ms" not in asset["metadata"]
    assert "width" not in asset["metadata"]
    assert media.file(project, asset["id"]).read_bytes() == local_video.read_bytes()
    assert store.list(project, "scene") == []
    assert store.get(project, "asset", asset["id"])["title"] == "user video.mp4"


def test_pending_receipt_can_be_processed_after_service_restart(upload_env, local_video):
    store, project, media = upload_env
    require_staging(media)
    asset = media.stage_upload(project, local_video)
    restarted = MediaService(Store(store.path), media.root)

    ready = restarted.process_upload(project, asset["id"])

    assert ready["inspection"] == "ready"
    assert ready["metadata"]["processing"]["state"] == "ready"
    assert ready["metadata"]["duration_ms"] == 1600
    assert ready["metadata"]["frame_count"] == 16
    assert (ready["metadata"]["width"], ready["metadata"]["height"]) == (160, 120)
    assert restarted.file(project, asset["id"], poster=True).is_file()
    assert restarted.scenes(project, asset["id"])


def test_invalid_video_marks_only_its_receipt_failed_and_preserves_uploaded_bytes(upload_env, local_video, tmp_path):
    store, project, media = upload_env
    require_staging(media)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a decodable video")
    asset = media.stage_upload(project, broken)
    next_asset = media.stage_upload(project, local_video)

    with pytest.raises(ValueError, match="媒体"):
        media.process_upload(project, asset["id"])

    failed = store.get(project, "asset", asset["id"])
    assert failed["inspection"] == "failed"
    assert failed["metadata"]["processing"]["state"] == "failed"
    assert media.file(project, asset["id"]).read_bytes() == b"not a decodable video"
    assert store.list(project, "scene") == []
    assert media.process_upload(project, next_asset["id"])["inspection"] == "ready"


def test_parallel_retry_receipts_are_idempotent_within_project_and_source(upload_env, local_video):
    store, project, media = upload_env
    require_staging(media)
    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(executor.map(lambda _: media.stage_upload(project, local_video), range(12)))
    assert len({asset["id"] for asset in receipts}) == 1
    assert len(store.list(project, "asset")) == 1
    assert len(list((media.root / project).glob("*/original.mp4"))) == 1
    other_source = media.stage_upload(project, local_video, source_kind="inspiration")
    other_project = store.create_project("separate")["id"]
    separated = media.stage_upload(other_project, local_video)
    assert len({receipts[0]["id"], other_source["id"], separated["id"]}) == 3


def test_failed_processing_can_be_explicitly_retried_without_losing_original(upload_env, local_video, monkeypatch):
    store, project, media = upload_env
    require_staging(media)
    asset = media.stage_upload(project, local_video)
    original_run = media._run
    def interrupted(*args, **kwargs):
        raise ValueError("local decoder interrupted")
    monkeypatch.setattr(media, "_run", interrupted)
    with pytest.raises(ValueError):
        media.process_upload(project, asset["id"])
    assert store.get(project, "asset", asset["id"])["inspection"] == "failed"
    monkeypatch.setattr(media, "_run", original_run)

    ready = media.process_upload(project, asset["id"])

    assert ready["inspection"] == "ready"
    assert ready["metadata"]["processing"]["state"] == "ready"
    assert "error" not in ready["metadata"]["processing"]
    assert len(store.list(project, "asset")) == 1


def test_receipt_rejects_oversize_before_persisting_any_asset(upload_env, local_video):
    store, project, media = upload_env
    require_staging(media)
    media.MAX_FILE_BYTES = local_video.stat().st_size - 1
    with pytest.raises(ValueError, match="超过"):
        media.stage_upload(project, local_video)
    assert store.list(project, "asset") == []
    assert list(media.root.rglob("original.mp4")) == []


def test_receiving_upload_stream_persists_bytes_without_an_extra_temporary_copy(upload_env, local_video):
    store, project, media = upload_env
    assert callable(getattr(media, "stage_upload_stream", None)), "multipart spool must stream directly into media storage"
    incoming = io.BytesIO(local_video.read_bytes())

    asset = media.stage_upload_stream(project, incoming, "../../user-video.mp4")

    assert media.file(project, asset["id"]).read_bytes() == local_video.read_bytes()
    assert asset["title"] == "user-video.mp4"
    assert not incoming.closed
    assert len(store.list(project, "asset")) == 1
    assert len(list(media.root.rglob("original.mp4"))) == 1


def test_interrupted_stream_removes_partial_bytes_and_never_creates_a_receipt(upload_env):
    store, project, media = upload_env
    assert callable(getattr(media, "stage_upload_stream", None)), "multipart spool must stream directly into media storage"
    class Interrupted:
        def __init__(self):
            self.first = True
        def read(self, _size):
            if self.first:
                self.first = False
                return b"partial bytes"
            raise OSError("local read interrupted")
    with pytest.raises(OSError, match="interrupted"):
        media.stage_upload_stream(project, Interrupted(), "video.mp4")
    assert store.list(project, "asset") == []
    assert list(media.root.rglob("original.mp4")) == []


@pytest.mark.parametrize("operation", ["inspect", "scenes", "frames", "clip"])
def test_pending_receipt_cannot_be_used_as_decoded_evidence(upload_env, local_video, operation):
    _, project, media = upload_env
    asset = media.stage_upload(project, local_video)
    with pytest.raises(ValueError, match="处理"):
        if operation == "clip":
            media.clip(project, asset["id"], 0, 1)
        else:
            getattr(media, operation)(project, asset["id"])


def test_receipt_ingestion_does_not_wait_for_another_files_running_decoder(upload_env, local_video, monkeypatch):
    import threading
    store, project, media = upload_env
    first = media.stage_upload(project, local_video)
    decoding, release = threading.Event(), threading.Event()
    original_run = media._run
    def paused_decoder(*args, **kwargs):
        decoding.set()
        assert release.wait(5), "receipt unexpectedly blocked behind the decoder"
        return original_run(*args, **kwargs)
    monkeypatch.setattr(media, "_run", paused_decoder)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(media.process_upload, project, first["id"])
        try:
            assert decoding.wait(5)
            next_receipt = media.stage_upload(project, local_video, source_kind="raw")
            assert next_receipt["metadata"]["processing"]["state"] == "queued"
            assert not worker.done()
            assert len(store.list(project, "asset")) == 2
        finally:
            release.set()
        assert worker.result(timeout=10)["inspection"] == "ready"


def test_existing_direct_ingest_removes_partial_copy_when_disk_write_fails(upload_env, local_video, monkeypatch):
    store, project, media = upload_env
    def incomplete_copy(_source, destination):
        destination.write_bytes(b"partial media")
        raise OSError("disk write interrupted")
    monkeypatch.setattr(shutil, "copyfile", incomplete_copy)
    with pytest.raises((OSError, ValueError)):
        media.ingest(project, local_video)
    assert store.list(project, "asset") == []
    assert list(media.root.rglob("original.mp4")) == []
