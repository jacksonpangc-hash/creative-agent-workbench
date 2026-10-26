"""Select the exact inspected media executable in distributed workers."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import imageio_ffmpeg


def ffmpeg_executable():
    frozen = hasattr(sys, "_MEIPASS")
    root = Path(sys._MEIPASS) if frozen else Path(__file__).resolve().parents[2] / ".state"
    directory = root / "media-runtime"
    executable = directory / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    manifest = directory / "manifest.json"
    archive = directory / "executable.zip"
    if not manifest.is_file() or not (executable.is_file() or frozen and archive.is_file()):
        if frozen:
            raise ValueError("内置媒体程序缺失，请重新安装完整应用")
        return imageio_ffmpeg.get_ffmpeg_exe()
    try:
        metadata = json.loads(manifest.read_text())
    except (OSError, ValueError):
        raise ValueError("内置媒体程序完整性记录损坏") from None
    if metadata.get("nonfree") is not False:
        raise ValueError("内置媒体程序未通过再分发检查")
    if not executable.is_file():
        # PyInstaller rewrites Mach-O inputs even when declared as data.
        # An archive keeps the inspected bytes intact until this private extraction.
        try:
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if (len(entries) != 1 or entries[0].filename != executable.name
                        or entries[0].file_size > 100 * 1024 * 1024):
                    raise ValueError("内置媒体程序完整性检查失败")
                blob = bundle.read(entries[0])
            if hashlib.sha256(blob).hexdigest() != metadata.get("binary_sha256"):
                raise ValueError("内置媒体程序完整性检查失败")
            with tempfile.NamedTemporaryFile(dir=directory, prefix="media-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(blob)
            try:
                temporary.replace(executable)
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, zipfile.BadZipFile, KeyError):
            raise ValueError("内置媒体程序完整性检查失败") from None
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if metadata.get("binary_sha256") != digest:
        raise ValueError("内置媒体程序完整性检查失败")
    if frozen and sys.platform != "win32":
        # Data extraction preserves the inspected bytes without Mach-O rewriting.
        # Only this verified executable inside the application's private temp tree is changed.
        executable.chmod(executable.stat().st_mode | 0o100)
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(executable)
    return str(executable)
