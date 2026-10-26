from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import math
import re
import shutil
import socket
import subprocess
import threading
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import imageio_ffmpeg
from PIL import Image, UnidentifiedImageError
from rapidfuzz.fuzz import WRatio

from .contracts import AnalysisScope, SourceKind, decimal
from .media_runtime import ffmpeg_executable
from .store import Store, now, provenance_url, uid


def validate_remote_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅支持无凭证的 HTTP(S) 媒体地址")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("媒体地址端口不受支持")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析媒体地址") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("禁止读取本机、内网或保留地址")
    return addresses[0][4][0]


class MediaService:
    MAX_FILE_BYTES = 1024 * 1024 * 1024
    IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

    def __init__(self, store: Store, root: Path | str):
        self.store = store
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg_executable()
        self._upload_lock = threading.Lock()
        self._upload_processing = threading.BoundedSemaphore(1)

    def stage_upload(self, project_id, path, *, source_kind: SourceKind = "own", title=None):
        """Persist a local receipt without starting a decoder or waiting for indexing."""
        self.store.project(project_id)
        if source_kind not in {"own", "inspiration", "raw", "finished", "account", "pushed", "industry"}:
            raise ValueError("素材来源无效")
        source = Path(path).resolve()
        if not source.is_file() or not 0 < source.stat().st_size <= self.MAX_FILE_BYTES:
            raise ValueError("媒体文件不存在、为空或超过 1GB")
        with source.open("rb") as incoming:
            return self.stage_upload_stream(project_id, incoming, source.name, source_kind=source_kind, title=title)

    def stage_upload_stream(self, project_id, incoming, filename, *, source_kind: SourceKind = "own", title=None):
        """Copy a multipart spool once while calculating hashes; the caller owns its stream."""
        self.store.project(project_id)
        if source_kind not in {"own", "inspiration", "raw", "finished", "account", "pushed", "industry"}:
            raise ValueError("素材来源无效")
        filename = Path(filename).name
        extension = Path(filename).suffix.lower()
        if extension not in self.IMAGE_EXT | self.VIDEO_EXT:
            raise ValueError("不支持该媒体格式")
        # A response may be lost while the first upload is still being received.
        # Serialize receipt commits so that retrying it cannot create a second asset.
        # Decode work uses a separate gate and never holds this receipt lock.
        with self._upload_lock:
            directory = self.root / project_id / uid()
            destination = directory / ("original" + extension)
            try:
                directory.mkdir(parents=True)
                sha256, md5, total = hashlib.sha256(), hashlib.md5(), 0
                with destination.open("wb") as output:
                    while chunk := incoming.read(1024 * 1024):
                        total += len(chunk)
                        if total > self.MAX_FILE_BYTES:
                            raise ValueError("媒体文件超过 1GB")
                        sha256.update(chunk)
                        md5.update(chunk)
                        output.write(chunk)
                if not total:
                    raise ValueError("媒体文件为空")
                digest = sha256.hexdigest()
                for old in self.store.list(project_id, "asset"):
                    if old["sha256"] == digest and old["source_kind"] == source_kind:
                        shutil.rmtree(directory, ignore_errors=True)
                        return old
                return self.store.put(project_id, "asset", {
                    "title": (title or filename)[:300], "source_kind": source_kind, "sha256": digest,
                    "md5": md5.hexdigest(), "media_type": "image" if extension in self.IMAGE_EXT else "video",
                    "path": str(destination), "poster_path": str(directory / "poster.jpg"), "size_bytes": total,
                    "metadata": {"processing": {"state": "queued"}}, "inspection": "pending"})
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    def process_upload(self, project_id, asset_id):
        """Decode a durable receipt locally; retain bytes and a truthful error on failure."""
        with self._upload_processing:
            asset = self.store.get(project_id, "asset", asset_id)
            if asset["inspection"] == "ready":
                return asset
            asset["metadata"]["processing"] = {"state": "running"}
            asset["inspection"] = "pending"
            self.store.put(project_id, "asset", asset, asset_id)
            try:
                path = self.file(project_id, asset_id)
                poster = path.parent / "poster.jpg"
                if asset["media_type"] == "image":
                    with Image.open(path) as image:
                        image.load()
                        details = {"width": image.width, "height": image.height, "duration_ms": 0,
                                   "animation_frames": getattr(image, "n_frames", 1)}
                        image.thumbnail((1280, 1280))
                        image.convert("RGB").save(poster, quality=88)
                else:
                    reader = imageio_ffmpeg.read_frames(str(path))
                    try:
                        info = next(reader)
                    finally:
                        reader.close()
                    details = {"width": info["size"][0], "height": info["size"][1],
                               "duration_ms": round(info["duration"] * 1000), "fps": info["fps"]}
                    self._run(["-i", path, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", poster])
                asset["metadata"].update(details)
                self.store.put(project_id, "asset", asset, asset_id)
                ready = self.inspect(project_id, asset_id)
                ready["metadata"]["processing"] = {"state": "ready"}
                return self.store.put(project_id, "asset", ready, asset_id)
            except Exception as exc:
                failed = self.store.get(project_id, "asset", asset_id)
                failed["inspection"] = "failed"
                failed["metadata"]["processing"] = {
                    "state": "failed", "error": "媒体文件无法读取或处理，请核对格式后重试"}
                for scene in self.store.list(project_id, "scene"):
                    if scene["asset_id"] == asset_id:
                        self.store.delete(project_id, "scene", scene["id"])
                self.store.put(project_id, "asset", failed, asset_id)
                raise ValueError("媒体文件无法读取或处理") from exc

    def _run(self, args, timeout=600):
        try:
            return subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-y", *map(str, args)],
                                  capture_output=True, text=True, errors="replace", timeout=timeout, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("媒体处理失败：格式无效、编码不受支持或处理超时") from exc

    def ingest(self, project_id, path, *, source_kind: SourceKind = "own", title=None, metadata=None, process=True):
        self.store.project(project_id)
        if source_kind not in {"own", "inspiration", "raw", "finished", "account", "pushed", "industry"}:
            raise ValueError("素材来源无效")
        source = Path(path).resolve()
        if not source.is_file() or not 0 < source.stat().st_size <= self.MAX_FILE_BYTES:
            raise ValueError("媒体文件不存在、为空或超过 1GB")
        extension = source.suffix.lower()
        if extension not in self.IMAGE_EXT | self.VIDEO_EXT:
            raise ValueError("不支持该媒体格式")
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with source.open("rb") as stream:
            md5 = hashlib.file_digest(stream, "md5").hexdigest()
        for old in self.store.list(project_id, "asset"):
            if old["sha256"] == digest and old["source_kind"] == source_kind:
                return old
        directory = self.root / project_id / uid()
        directory.mkdir(parents=True)
        destination = directory / ("original" + extension)
        record = None
        try:
            shutil.copyfile(source, destination)
            if extension in self.IMAGE_EXT:
                with Image.open(destination) as image:
                    image.load()
                    details = dict(width=image.width, height=image.height, duration_ms=0,
                                   animation_frames=getattr(image, "n_frames", 1))
                    image.thumbnail((1280, 1280))
                    image.convert("RGB").save(directory / "poster.jpg", quality=88)
                media_type = "image"
            else:
                reader = imageio_ffmpeg.read_frames(str(destination))
                try:
                    info = next(reader)
                finally:
                    reader.close()
                details = dict(width=info["size"][0], height=info["size"][1],
                               duration_ms=round(info["duration"] * 1000), fps=info["fps"])
                self._run(["-i", destination, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", directory / "poster.jpg"])
                media_type = "video"
            record = self.store.put(project_id, "asset", dict(
                title=(title or source.name)[:300], source_kind=source_kind, sha256=digest, md5=md5,
                media_type=media_type, path=str(destination), poster_path=str(directory / "poster.jpg"),
                size_bytes=destination.stat().st_size, metadata={**(metadata or {}), **details}, inspection="pending"))
            return self.inspect(project_id, record["id"]) if process else record
        except (ValueError, OSError, RuntimeError, UnidentifiedImageError) as exc:
            if record:
                for scene in self.store.list(project_id, "scene"):
                    if scene["asset_id"] == record["id"]:
                        self.store.delete(project_id, "scene", scene["id"])
                self.store.delete(project_id, "asset", record["id"])
            shutil.rmtree(directory, ignore_errors=True)
            raise ValueError("媒体文件无法读取或处理") from exc

    def file(self, project_id, asset_id, poster=False):
        asset = self.store.get(project_id, "asset", asset_id)
        result = Path(asset["poster_path"] if poster else asset["path"]).resolve()
        if not result.is_relative_to(self.root / project_id) or not result.is_file():
            raise ValueError("媒体路径越界或文件已丢失")
        return result

    def inspect(self, project_id, asset_id):
        asset = self.store.get(project_id, "asset", asset_id)
        if asset["inspection"] == "ready":
            return asset
        if asset["inspection"] == "failed":
            raise ValueError("媒体处理失败，请先重试本地处理")
        if "duration_ms" not in asset.get("metadata", {}):
            raise ValueError("素材仍在本地处理中，请等待索引完成")
        path = self.file(project_id, asset_id)
        duration = asset["metadata"]["duration_ms"]
        boundaries = [0]
        if asset["media_type"] == "video":
            result = self._run(["-i", path, "-map", "0:v:0", "-an", "-vf",
                                "showinfo,select='gt(scene,0.32)',showinfo", "-f", "null", "-"])
            frames = []
            for line in result.stderr.splitlines():
                match = re.search(r"Parsed_showinfo_(\d+).*?n:\s*(\d+).*?pts_time:([\d.e+\-]+)", line)
                if match:
                    time_ms = round(float(match[3]) * 1000)
                    if match[1] == "0":
                        frames.append(dict(index=int(match[2]), time_ms=time_ms))
                    elif 0 < time_ms < duration:
                        boundaries.append(time_ms)
            if not frames:
                raise ValueError("媒体没有可解码的视频帧")
            index_path = path.parent / "frame-index.json"
            index_path.write_text(json.dumps(frames), encoding="utf-8")
            asset["metadata"].update(frame_count=len(frames), frame_index_path=str(index_path),
                                     observation="decoded_frame_index_not_model_observation")
            boundaries = sorted(set(boundaries))
        else:
            asset["metadata"]["observation"] = "image_not_yet_model_observed"
        old_scenes = [s for s in self.store.list(project_id, "scene") if s["asset_id"] == asset_id]
        for old in old_scenes:
            self.store.delete(project_id, "scene", old["id"])
        for index, start in enumerate(boundaries):
            end = boundaries[index + 1] if index + 1 < len(boundaries) else duration
            poster = path.parent / f"scene-{index}.jpg"
            if asset["media_type"] == "video":
                self._run(["-ss", f"{start / 1000:.3f}", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", poster])
            else:
                shutil.copyfile(asset["poster_path"], poster)
            with Image.open(poster) as image:
                small = image.convert("RGB").resize((8, 8))
                fingerprint = [round(c / 255, 4) for c in small.tobytes()]
            self.store.put(project_id, "scene", dict(asset_id=asset_id, index=index, start_ms=start,
                           end_ms=end, poster_path=str(poster), fingerprint=fingerprint,
                           description="", observation="local_scene_detection"))
        asset["inspection"] = "ready"
        return self.store.put(project_id, "asset", asset, asset_id)

    def scenes(self, project_id, asset_id):
        asset = self.store.get(project_id, "asset", asset_id)
        if asset["inspection"] != "ready":
            raise ValueError("素材尚未处理完成，暂不能读取画面证据")
        return sorted([s for s in self.store.list(project_id, "scene") if s["asset_id"] == asset_id],
                      key=lambda scene: scene["start_ms"])

    def frames(self, project_id, asset_id, max_frames=36):
        """Return sampled observation frames with explicit timestamps; never claim full-frame vision."""
        asset = self.store.get(project_id, "asset", asset_id)
        if asset["inspection"] != "ready":
            raise ValueError("素材尚未处理完成，暂不能读取画面证据")
        if asset["media_type"] == "image":
            return [dict(path=str(self.file(project_id, asset_id)), time_ms=0)]
        all_frames = json.loads(Path(asset["metadata"]["frame_index_path"]).read_text())
        scenes = [s for s in self.store.list(project_id, "scene") if s["asset_id"] == asset_id]
        # Cover scene boundaries, dense opening and distributed whole-video observations.
        targets = {s["start_ms"] for s in scenes}
        targets.update(t for t in range(0, min(3000, asset["metadata"]["duration_ms"]), 250))
        targets.update(all_frames[round(i * (len(all_frames) - 1) / max(1, max_frames - 1))]["time_ms"]
                       for i in range(max_frames))
        output = []
        for time_ms in sorted(targets):
            image = self.file(project_id, asset_id).parent / f"frame-{time_ms}.jpg"
            if not image.exists():
                self._run(["-ss", f"{time_ms / 1000:.3f}", "-i", asset["path"], "-frames:v", "1",
                           "-vf", "scale='min(1280,iw)':-2", image])
            if image.exists():
                output.append(dict(path=str(image), time_ms=time_ms))
        return output

    def clip(self, project_id, asset_id, start_ms, duration):
        asset = self.store.get(project_id, "asset", asset_id)
        if asset["inspection"] != "ready":
            raise ValueError("素材尚未处理完成，暂不能创建参考片段")
        if asset["media_type"] != "video" or not 0 < duration <= 15 or start_ms < 0:
            raise ValueError("仿拍参考必须是有效的 15 秒以内视频片段")
        if start_ms + duration * 1000 > asset["metadata"]["duration_ms"] + 1:
            raise ValueError("参考片段超出原片时长")
        target = self.file(project_id, asset_id).parent / f"clip-{start_ms}-{duration:g}.mp4"
        if not target.exists():
            self._run(["-ss", f"{start_ms / 1000:.3f}", "-i", asset["path"], "-t", str(duration),
                       "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", target])
        return target

    def frames_for_interval(self, project_id, asset_id, start_ms, duration):
        clip = self.clip(project_id, asset_id, start_ms, duration)
        count = min(36, max(2, round(duration * 4)))
        frames = []
        for i in range(count):
            relative_ms = round(i * duration * 1000 / count)
            path = clip.parent / f'{clip.stem}-sample-{relative_ms}.jpg'
            if not path.exists():
                self._run(['-ss', f'{relative_ms / 1000:.3f}', '-i', clip, '-frames:v', '1', '-vf', "scale='min(1280,iw)':-2", path])
            if path.is_file(): frames.append({'path': str(path), 'time_ms': start_ms + relative_ms})
        if not frames: raise ValueError('选定片段没有可观察的参考帧')
        return frames

    def add_subtitles(self, project_id, asset_id, text):
        asset = self.store.get(project_id, "asset", asset_id)
        if len(text) > 2_000_000:
            raise ValueError("字幕文件过大")
        pattern = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)
        segments = []
        for m in pattern.finditer(text.strip()):
            start = ((int(m[1] or 0) * 60 + int(m[2])) * 60 + int(m[3])) * 1000 + int(m[4])
            end = ((int(m[5] or 0) * 60 + int(m[6])) * 60 + int(m[7])) * 1000 + int(m[8])
            if end <= start:
                raise ValueError("字幕时间范围无效")
            segments.append(dict(start_ms=start, end_ms=end, text=re.sub(r"<[^>]+>", "", m[9]).strip()))
        if not segments:
            raise ValueError("未读到有效 SRT/VTT 字幕")
        metadata = asset["metadata"]
        if metadata.get("transcript"):
            previous = {key: metadata[key] for key in (
                "transcript", "subtitles", "transcript_source", "transcript_provenance", "transcription"
            ) if key in metadata}
            previous["replaced_at"] = now()
            history = metadata.get("transcript_history")
            metadata["transcript_history"] = [*(history if isinstance(history, list) else []), previous]
        metadata.pop("transcription", None)
        metadata.update(subtitles=segments, transcript="\n".join(s["text"] for s in segments),
                        transcript_source="user_import", transcript_provenance="subtitle")
        return self.store.put(project_id, "asset", asset, asset_id)

    def download(self, project_id, url, *, rights_confirmed, title=None, source_kind="inspiration", client=None):
        if not rights_confirmed:
            raise ValueError("请确认具备下载、留存和模型处理授权")
        client = client or httpx.Client(timeout=60, follow_redirects=False, trust_env=False)
        temp = self.root / (uid() + ".part")
        try:
            for _ in range(5):
                address = validate_remote_url(url)
                parsed = urlsplit(url)
                pinned = httpx.URL(url).copy_with(host=address)
                with client.stream("GET", pinned, headers={"Host": parsed.netloc}, extensions={"sni_hostname": parsed.hostname}) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";")[0]
                    ext = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/jpeg": ".jpg",
                           "image/png": ".png", "image/webp": ".webp"}.get(mime)
                    if not ext:
                        raise ValueError("链接未返回直接媒体文件；需要正式数据解析通道")
                    total = 0
                    with temp.open("wb") as output:
                        for chunk in response.iter_bytes(65536):
                            total += len(chunk)
                            if total > self.MAX_FILE_BYTES:
                                raise ValueError("媒体文件超过 1GB")
                            output.write(chunk)
                    destination = temp.with_suffix(ext)
                    temp.rename(destination)
                    try:
                        return self.ingest(project_id, destination, title=title or "链接素材", source_kind=source_kind,
                                           metadata={"source_url": provenance_url(url), "rights_confirmed": True})
                    finally:
                        destination.unlink(missing_ok=True)
            raise ValueError("媒体重定向次数过多")
        finally:
            temp.unlink(missing_ok=True)


class MetricImporter:
    ALIASES = {"素材ID": "platform_id", "material_id": "platform_id", "video_id": "platform_id", "账户ID": "account_id",
               "日期": "date", "消耗": "spend", "支付金额": "revenue", "展示次数": "impressions", "点击次数": "clicks",
               "转化次数": "conversions", "币种": "currency", "归因窗口": "attribution_window"}

    def __init__(self, store):
        self.store = store

    def import_csv(self, project_id, text):
        project = self.store.project(project_id)
        assets = self.store.list(project_id, "asset")
        old = self.store.list(project_id, "metric")
        rows = []
        for raw in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
            row = {self.ALIASES.get(k.strip(), k.strip()): v.strip() if v else None for k, v in raw.items() if k}
            if not row.get("date") or not any(row.get(k) for k in ("asset_id", "platform_id", "md5")):
                raise ValueError("报表需要日期与 asset_id / 素材ID / md5；不会按文件名猜测匹配")
            date.fromisoformat(row["date"])
            row["currency"] = row.get("currency") or project["currency"]
            if row["currency"] not in {"CNY", "USD"}:
                raise ValueError("报表币种无效")
            for key in ("spend", "revenue", "impressions", "clicks", "conversions"):
                if row.get(key) is not None:
                    value = decimal(row[key].replace(",", ""))
                    if value < 0 or (key in {"impressions", "clicks", "conversions"} and value != int(value)):
                        raise ValueError("报表指标无效")
                    row[key] = str(value)
            matches = [a for a in assets if
                       (row.get("asset_id") == a["id"] or
                        (row.get("platform_id") and row["platform_id"] == a["metadata"].get("platform_id") and
                         row.get("account_id") == a["metadata"].get("account_id")) or
                        (row.get("md5") and row["md5"].lower() == a["md5"]))]
            row["asset_id"] = matches[0]["id"] if len(matches) == 1 else None
            row["source"] = "user_supplied_qianchuan_export"
            row["attribution_window"] = row.get("attribution_window") or "unspecified"
            key = [row.get("platform_id") or row.get("md5") or raw.get("asset_id"), row.get("account_id"), row["date"], row["attribution_window"]]
            row["import_key"] = json.dumps(key, ensure_ascii=False)
            rows.append(row)
        if not rows or len(rows) > 100_000:
            raise ValueError("报表为空或行数超过100000")
        # All input validated before the first write; update by stable platform/account/day/window key.
        for row in rows:
            previous = next((r for r in old if r.get("import_key") == row["import_key"]), None)
            saved = self.store.put(project_id, "metric", row, previous["id"] if previous else None)
            old = [r for r in old if r.get("import_key") != row["import_key"]] + [saved]
        return dict(imported=len(rows), matched=sum(bool(r["asset_id"]) for r in rows),
                    unmatched=sum(not r["asset_id"] for r in rows))

    def for_assets(self, project_id, asset_ids, start_date=None, end_date=None):
        return [r for r in self.store.list(project_id, "metric") if r.get("asset_id") in asset_ids
                and (not start_date or r["date"] >= str(start_date)) and (not end_date or r["date"] <= str(end_date))]


class SearchService:
    def __init__(self, store):
        self.store = store

    def search(self, scope: AnalysisScope, query="", method="exact", vector=None):
        if method not in {"exact", "fuzzy", "semantic"}:
            raise ValueError("搜索方式无效")
        if method == "semantic" and vector is None:
            raise ValueError("语义搜索需要先配置检索向量模型并完成索引")
        if scope.group_ids:
            ids = set(scope.asset_ids)
            for gid in scope.group_ids:
                for aid in self.store.get(scope.project_id, 'group', gid)['asset_ids']:
                    self.store.get(scope.project_id, 'asset', aid)
                    ids.add(aid)
            if len(ids) > 200: raise ValueError('分组素材并集超过200条')
            scope = scope.model_copy(update={'asset_ids': list(ids), 'discovery': False, 'group_ids': []})
        statistics = {}
        if scope.date_basis == 'statistics':
            for row in self.store.list(scope.project_id, 'metric'):
                if not scope.account_ids or row.get('account_id') in scope.account_ids:
                    statistics.setdefault(row.get('asset_id'), []).append(row.get('date'))
        assets = [a for a in self.store.list(scope.project_id, "asset") if scope.allows(a, statistics.get(a['id'], []))]
        results = []
        for asset in assets:
            if scope.min_spend is not None or scope.min_roas is not None:
                from .contracts import aggregate_metrics
                measured = MetricImporter(self.store).for_assets(scope.project_id, [asset['id']],
                    scope.start_date if scope.date_basis == 'statistics' else None, scope.end_date if scope.date_basis == 'statistics' else None)
                if scope.account_ids: measured = [r for r in measured if r.get('account_id') in scope.account_ids]
                agg = aggregate_metrics(measured)
                if scope.min_spend is not None and (agg['spend']['kind'] != 'exact' or decimal(agg['spend']['value']) < scope.min_spend):
                    continue
                if scope.min_roas is not None and (agg['roas']['kind'] != 'exact' or decimal(agg['roas']['value']) < scope.min_roas):
                    continue
            text = " ".join([asset["title"], asset.get("description", ""), asset.get("parsed_text", ""), asset["metadata"].get("transcript", ""),
                             " ".join(asset.get("tags", []))]).casefold()
            if method == "semantic":
                embedding = asset.get("embedding")
                if not embedding or len(embedding) != len(vector):
                    continue
                denom = math.sqrt(sum(v * v for v in vector) * sum(v * v for v in embedding))
                score = sum(a * b for a, b in zip(vector, embedding)) / denom if denom else 0
            elif method == "fuzzy":
                score = WRatio(query.casefold(), text) / 100 if query else 1
                if score < 0.6:
                    continue
            else:
                if query.casefold() not in text:
                    continue
                score = 1
            results.append({**asset, "search_score": round(score, 5)})
        return sorted(results, key=lambda a: a["search_score"], reverse=True)

