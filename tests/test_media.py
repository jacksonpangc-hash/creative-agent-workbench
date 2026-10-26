"""Catch unsafe intake, fake timeline indexing and report matching by filename."""
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from creative_agent.contracts import AnalysisScope
from creative_agent.media import MediaService, MetricImporter, SearchService, validate_remote_url
from creative_agent.store import Store


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    p = store.create_project("media")
    return store, p, MediaService(store, tmp_path / "media")


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "reference.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=1",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=1", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[out]", "-map", "[out]", "-c:v", "libx264", str(path)],
                   capture_output=True, check=True)
    return path


def test_ingestion_copies_hashes_and_deduplicates_without_using_source_path(env, tmp_path):
    store, p, media = env
    image = tmp_path / "product.png"
    Image.new("RGB", (40, 60), "red").save(image)
    one = media.ingest(p["id"], image, title="product")
    two = media.ingest(p["id"], image, title="duplicate")
    image.unlink()
    assert one["id"] == two["id"]
    assert media.file(p["id"], one["id"]).exists()
    assert one["media_type"] == "image"
    assert one["metadata"]["width"] == 40
    assert len(store.list(p["id"], "asset")) == 1


def test_real_video_inspection_preserves_every_frame_time_and_scene_bounds(env, video):
    store, p, media = env
    asset = media.ingest(p["id"], video)
    frames = json.loads(Path(asset["metadata"]["frame_index_path"]).read_text())
    assert len(frames) == 20
    assert frames[0]["time_ms"] == 0
    assert frames[-1]["time_ms"] == 1900
    scenes = media.scenes(p["id"], asset["id"])
    assert len(scenes) == 2
    assert [s["start_ms"] for s in scenes] == [0, 1000]
    assert scenes[-1]["end_ms"] == 2000
    assert asset["metadata"]["observation"] == "decoded_frame_index_not_model_observation"


def test_subtitles_and_search_respect_exact_selected_scope(env, tmp_path):
    store, p, media = env
    path = tmp_path / "image.png"
    Image.new("RGB", (40, 40)).save(path)
    asset = media.ingest(p["id"], path, title="吸尘器")
    media.add_subtitles(p["id"], asset["id"], "1\n00:00:00,100 --> 00:00:00,900\n沙发缝隙里的灰尘\n")
    scope = AnalysisScope(project_id=p["id"], asset_ids=[asset["id"]])
    search = SearchService(store)
    assert len(search.search(scope, "缝隙", "exact")) == 1
    other = store.create_project("private")
    assert not search.search(AnalysisScope(project_id=other["id"], discovery=True), "缝隙", "exact")
    assert not search.search(AnalysisScope(project_id=p["id"]), "缝隙", "exact")
    with pytest.raises(ValueError, match="向量"):
        search.search(scope, "狭窄位置", "semantic")


def test_remote_intake_rejects_private_and_non_http_destinations():
    for url in ("file:///etc/passwd", "http://127.0.0.1/a", "https://localhost/a", "http://169.254.169.254/a", "http://[::1]/a"):
        with pytest.raises(ValueError):
            validate_remote_url(url)


def test_invalid_media_is_not_accepted_from_filename_extension(env, tmp_path):
    _, p, media = env
    path = tmp_path / "pretend.mp4"
    path.write_text("not media")
    with pytest.raises(ValueError, match="媒体"):
        media.ingest(p["id"], path)


def test_csv_import_matches_platform_id_and_deduplicates_daily_rows(env, tmp_path):
    store, p, media = env
    path = tmp_path / "image.png"
    Image.new("RGB", (40, 40)).save(path)
    asset = media.ingest(p["id"], path, metadata={"platform_id": "platform-1", "account_id": "account-a"})
    importer = MetricImporter(store)
    csv = "素材ID,账户ID,日期,消耗,支付金额,展示次数,点击次数\nplatform-1,account-a,2026-10-18,100,200,1000,20\nunknown,account-a,2026-10-18,50,80,200,2\n"
    result = importer.import_csv(p["id"], csv)
    assert result["matched"] == 1 and result["unmatched"] == 1
    importer.import_csv(p["id"], csv)
    assert len(store.list(p["id"], "metric")) == 2
    rows = importer.for_assets(p["id"], [asset["id"]])
    assert len(rows) == 1
    assert rows[0]["spend"] == "100"
    assert rows[0]["source"] == "user_supplied_qianchuan_export"


def test_import_is_validated_before_any_row_is_written(env):
    store, p, _ = env
    with pytest.raises(ValueError):
        MetricImporter(store).import_csv(p["id"], "asset_id,date,spend\none,2026-10-18,10\ntwo,2026-10-18,-1\n")
    assert store.list(p["id"], "metric") == []


def test_excerpt_frames_are_decoded_from_the_selected_clip(env, video):
    _,p,media=env
    a=media.ingest(p['id'],video)
    frames=media.frames_for_interval(p['id'],a['id'],500,1)
    assert frames and all(500 <= f['time_ms'] < 1500 for f in frames)
    for f in frames:
        with Image.open(f['path']) as image: assert image.width>0


def test_imported_subtitles_replace_asr_provenance_without_losing_previous_text(env):
    from creative_agent.agent import AnalysisAgent
    store, p, media = env
    asset = store.put(p['id'], 'asset', {'title': '口播', 'media_type': 'video', 'metadata': {
        'duration_ms': 2000, 'transcript': '重复识别',
        'subtitles': [{'start_ms': 0, 'end_ms': 2000, 'text': '重复识别'}],
        'transcript_source': 'whisper.cpp-local', 'transcript_provenance': 'local_asr',
        'transcription': {'method': 'whisper.cpp-local', 'needs_review': True, 'warnings': ['重复'], 'reviewed': False}}})
    saved = media.add_subtitles(p['id'], asset['id'], '1\n00:00:00,100 --> 00:00:01,500\n用户提供的字幕\n')
    evidence = AnalysisAgent.spoken_evidence([saved])[0]
    assert evidence['source'] == 'user_import'
    assert evidence['provenance'] == 'subtitle'
    assert evidence['transcript'] == '用户提供的字幕'
    assert not evidence['transcription'].get('warnings')
    previous = saved['metadata']['transcript_history'][-1]
    assert previous['transcript'] == '重复识别'
    assert previous['transcription']['needs_review'] is True
