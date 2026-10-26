"""Bundled media policy tests use synthetic files, never a claimed real build."""
import hashlib
import importlib.util
import json
import platform
import os
import zipfile
import subprocess
from pathlib import Path

import pytest


def runtime():
    assert importlib.util.find_spec("creative_agent.media_runtime"), "bundled media runtime missing"
    from creative_agent import media_runtime
    return media_runtime


def test_frozen_media_missing_never_uses_unverified_path(tmp_path, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "/unverified/ffmpeg")
    with pytest.raises(ValueError, match="媒体程序"):
        module.ffmpeg_executable()


def test_frozen_media_validates_hash_and_sets_decoder_path(tmp_path, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "/prior-test-value")
    folder = tmp_path / "media-runtime"; folder.mkdir()
    binary = folder / "ffmpeg"; binary.write_bytes(b"synthetic-binary")
    binary.chmod(0o600)
    manifest = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "nonfree": False}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    assert module.ffmpeg_executable() == str(binary)
    assert os.access(binary, os.X_OK)
    assert module.os.environ["IMAGEIO_FFMPEG_EXE"] == str(binary)
    binary.write_bytes(b"changed")
    with pytest.raises(ValueError, match="完整性"):
        module.ffmpeg_executable()


def test_nonfree_candidate_is_rejected_even_with_matching_hash(tmp_path, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path), raising=False)
    folder = tmp_path / "media-runtime"; folder.mkdir()
    binary = folder / "ffmpeg"; binary.write_bytes(b"synthetic-binary")
    (folder / "manifest.json").write_text(json.dumps({"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "nonfree": True}))
    with pytest.raises(ValueError, match="再分发"):
        module.ffmpeg_executable()


def test_frozen_archive_preserves_verified_executable_bytes(tmp_path, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path), raising=False)
    folder = tmp_path / 'media-runtime'; folder.mkdir()
    blob = b'verified-media-executable'
    (folder / 'manifest.json').write_text(json.dumps({'nonfree': False, 'binary_sha256': hashlib.sha256(blob).hexdigest()}))
    with zipfile.ZipFile(folder / 'executable.zip', 'w') as archive:
        archive.writestr('ffmpeg', blob)
    executable = Path(module.ffmpeg_executable())
    assert executable.read_bytes() == blob
    assert os.access(executable, os.X_OK)


@pytest.mark.parametrize('name,blob', [('ffmpeg', b'tampered'), ('../ffmpeg', b'verified-media-executable')])
def test_frozen_archive_rejects_tampering_and_traversal_before_writing(tmp_path, monkeypatch, name, blob):
    module = runtime()
    monkeypatch.setattr(module.sys, '_MEIPASS', str(tmp_path), raising=False)
    folder = tmp_path / 'media-runtime'; folder.mkdir()
    (folder / 'manifest.json').write_text(json.dumps({'nonfree': False, 'binary_sha256': hashlib.sha256(b'verified-media-executable').hexdigest()}))
    with zipfile.ZipFile(folder / 'executable.zip', 'w') as archive:
        archive.writestr(name, blob)
    with pytest.raises(ValueError, match='完整性'):
        module.ffmpeg_executable()
    assert not (folder / 'ffmpeg').exists() and not (tmp_path / 'ffmpeg').exists()


def package_policy():
    source = Path(__file__).resolve().parents[1] / 'scripts/package_config.py'
    spec = importlib.util.spec_from_file_location('media_package_policy', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert callable(getattr(module, 'media_inputs', None)), 'media distribution preflight missing'
    return module


def candidate(tmp_path):
    folder = tmp_path / '.state/media-runtime'; folder.mkdir(parents=True)
    (folder / 'ffmpeg').write_bytes(b'synthetic-executable')
    (folder / 'corresponding-sources.zip').write_bytes(b'synthetic-source-archive')
    (folder / 'ffmpeg-license.txt').write_text('GNU General Public License version 2 or later')
    (folder / 'NOTICES.txt').write_text('Synthetic policy test')
    metadata = {'nonfree': False, 'architecture': platform.machine(),
                'binary_sha256': hashlib.sha256((folder / 'ffmpeg').read_bytes()).hexdigest(),
                'source_archive_sha256': hashlib.sha256((folder / 'corresponding-sources.zip').read_bytes()).hexdigest()}
    (folder / 'manifest.json').write_text(json.dumps(metadata))
    return folder, metadata


def test_package_refuses_missing_or_nonredistributable_media(tmp_path):
    policy = package_policy()
    with pytest.raises(ValueError, match='媒体程序'):
        policy.media_inputs(tmp_path)
    folder, metadata = candidate(tmp_path)
    metadata['nonfree'] = True
    (folder / 'manifest.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='再分发'):
        policy.media_inputs(tmp_path)


def test_package_requires_matching_binary_source_archive_and_license(tmp_path):
    policy = package_policy()
    folder, _ = candidate(tmp_path)
    assert policy.media_inputs(tmp_path) == folder
    (folder / 'corresponding-sources.zip').write_bytes(b'changed')
    with pytest.raises(ValueError, match='源码'):
        policy.media_inputs(tmp_path)


def test_inspected_media_binary_jpeg_preserves_visible_pixels(tmp_path):
    """Exercise an actual built encoder; a synthetic gradient is not business proof."""
    from PIL import Image, ImageChops, ImageStat
    binary = Path(__file__).resolve().parents[1] / '.state/media-runtime/ffmpeg'
    if not binary.is_file():
        pytest.skip('requires the host-specific inspected media build')
    original = Image.new('RGB', (240, 427))
    original.putdata([(x, y * 255 // 426, (x + y) * 255 // 665) for y in range(427) for x in range(240)])
    source, target = tmp_path / 'gradient.png', tmp_path / 'gradient.jpg'
    original.save(source)
    subprocess.run([str(binary), '-nostdin', '-loglevel', 'error', '-y', '-i', str(source),
                    '-frames:v', '1', '-pix_fmt', 'yuv420p', '-q:v', '2', str(target)],
                   check=True, timeout=30, capture_output=True)
    decoded = Image.open(target).convert('RGB')
    assert max(ImageStat.Stat(ImageChops.difference(original, decoded)).mean) < 6
