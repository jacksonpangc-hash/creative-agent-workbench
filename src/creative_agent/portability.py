"""Media-inclusive, checksummed, insert-only project backup and migration.

Uses the public Store contract, so a scoped PostgreSQL Store can be the target.
Historical jobs are records, never queue entries or requests to a supplier.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath

from .store import SECRET_KEYS, Conflict, dumps, export_safe, now, reject_secrets, uid

FORMAT = "creative-agent-media-bundle"
MAX_JSON = 32 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024 * 1024
POINTERS = {
    "asset_id": "asset",
    "reference_asset_id": "asset",
    "finished_asset_id": "asset",
    "source_result_id": "industry_result",
    "industry_reference_id": "industry_reference",
    "reference_id": ("industry_reference", "cmm_reference", "public_reference"),
    "parent_reference_id": "cmm_reference",
    "cmm_reference_id": "cmm_reference",
    "cmm_job_id": "cmm_job",
    "source_job_id": ("job", "cmm_job"),
    "session_id": "session",
    "source_session_id": "session",
    "brief_id": "brief",
    "script_id": "script",
    "scene_id": "scene",
    "group_id": "group",
    "connection_id": ("industry_connection", "cmm_connection"),
    "binding_id": "model",
    "strategy_binding_id": "model",
    "generation_job_id": "job",
    "job_id": ("job", "cmm_job"),
    "context_snapshot_id": "creative_context_snapshot",
    "previous_context_id": "creative_context_snapshot",
}
LIST_POINTERS = {
    "asset_ids": "asset",
    "own_asset_ids": "asset",
    "history_script_ids": "script",
    "reference_image_ids": "asset",
    "group_ids": "group",
    "industry_reference_ids": ("industry_reference", "cmm_reference"),
    "reference_ids": ("industry_reference", "cmm_reference", "public_reference"),
}


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _safe_json(value):
    if isinstance(value, dict):
        return {
            k: _safe_json(v)
            for k, v in value.items()
            if k.lower() not in SECRET_KEYS | {"secret_ref", "auth_info", "headers", "credentials", "token"}
        }
    if isinstance(value, list):
        return [_safe_json(v) for v in value]
    return value


def _events(store, p, jid):
    rows, cursor = [], 0
    while True:
        batch = store.events(p, jid, after=cursor)
        if not batch:
            return rows
        rows.extend(batch)
        cursor = batch[-1]["id"]


def _pointer_identities(expected, identities):
    kinds = (expected,) if isinstance(expected, str) else expected
    return set().union(*(identities.get(k, set()) for k in kinds))


def _validate_scope(value, p, identities, *, key=None, record_kind=None):
    if isinstance(value, dict):
        if value.get("project_id", p) != p:
            raise ValueError("快照含跨项目来源")
        if key in {'assets','own_assets','script_history'} and value.get('id') not in identities.get('script' if key=='script_history' else 'asset',set()):
            raise ValueError('选材快照含跨项目或丢失引用')
        for name, item in value.items():
            if name == "raw":
                continue  # Provider vocabulary is not our normalized lineage.
            # CMM group_id is remote BI membership metadata, not a local material group.
            if name == "group_id" and record_kind == "cmm_connection" and key is None:
                continue
            expected = POINTERS.get(name)
            if (
                expected
                and item
                and (not isinstance(item, str) or item not in _pointer_identities(expected, identities))
            ):
                raise ValueError("来源引用不属于本项目或已丢失：" + name)
            if name in LIST_POINTERS and (
                not isinstance(item, list)
                or any(
                    not isinstance(i, str) or i not in _pointer_identities(LIST_POINTERS[name], identities)
                    for i in item
                )
            ):
                raise ValueError("来源列表含跨项目或丢失引用：" + name)
            _validate_scope(item, p, identities, key=name)
    elif isinstance(value, list):
        for item in value:
            _validate_scope(item, p, identities, key=key)


def _file_fields(value, pointer=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "path" or key.endswith("_path")) and isinstance(item, str) and item:
                yield [*pointer, key], item
            elif isinstance(item, (dict, list)):
                yield from _file_fields(item, (*pointer, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _file_fields(item, (*pointer, i))


def _source_file(media, p, value):
    file = Path(value).absolute()
    root = (media.root / p).resolve()
    if not file.is_relative_to(root):
        raise ValueError("媒体路径属于其他项目或范围外位置")
    current = file
    while current != root:
        if current.is_symlink():
            raise ValueError("禁止导出符号链接媒体")
        current = current.parent
    if not file.is_file() or not file.resolve().is_relative_to(root):
        raise ValueError("媒体文件丢失或路径越界")
    return file


def validate_bundle_export(store, media, project_id):
    """Mandatory tool-level checks; an optional caller callback can only add restrictions."""
    from .industry import Industry
    from .licensing_access import LicensedAccess

    industry = Industry(store, media, None)
    industry.validate_export(project_id)
    access = LicensedAccess(store, industry)
    for kind, rows in store.export_project(project_id)["records"].items():
        for row in rows:
            access.check(project_id, row, kind, export=True)
    for job in store.jobs(project_id):
        access.check(project_id, job, "job", export=True)
    return True


def export_bundle(store, media, project_id, destination, *, authorize=None):
    """Write a new ZIP after mandatory licensing checks, plus optional caller checks."""
    project = store.project(project_id)
    exported = store.export_project(project_id)
    # Connections/derived indexes are local capabilities, never restored grants.
    skipped={'browser_grant','browser_connection','browser_import_receipt','browser_import_batch','model_use_receipt','knowledge_chunk','index_job','index_outbox'}
    exported['records']={k:v for k,v in exported['records'].items() if k not in skipped and not k.startswith('browser_')}
    records = {kind: exported["records"][kind] if kind == "creative_context_snapshot" else store.list(project_id, kind)
               for kind in exported["records"]}
    validate_bundle_export(store, media, project_id)
    if authorize:
        authorize(project_id)
    jobs = store.jobs(project_id)
    identities = {kind: {r["id"] for r in rows} for kind, rows in records.items()}
    identities["job"] = {j["id"] for j in jobs} | identities.get("migration_job", set())
    files = {}
    for kind, rows in records.items():
        for row in rows:
            _validate_scope(row, project_id, identities, record_kind=kind)
            if kind == "asset" and not row.get("path"):
                raise ValueError("素材缺少实际媒体，不能伪造完整备份")
            for pointer, value in _file_fields(row):
                file = _source_file(media, project_id, value)
                digest = _hash(file)
                if kind == "asset" and pointer == ["path"] and row.get("sha256") != digest:
                    raise ValueError("原始媒体与素材 checksum 不一致")
                suffix = file.suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                    raise ValueError("媒体后缀不安全")
                name = f"media/{digest}{suffix}"
                entry = files.setdefault(
                    name,
                    {
                        "name": name,
                        "sha256": digest,
                        "size": file.stat().st_size,
                        "bindings": [],
                        "source": file,
                    },
                )
                entry["bindings"].append({"kind": kind, "record_id": row["id"], "pointer": pointer})
    archived_jobs = []
    for job in jobs:
        _validate_scope(job, project_id, identities)
        archived_jobs.append(
            _safe_json(export_safe({**job, "events": _events(store, project_id, job["id"])}))
        )
    snapshot = {
        "format": FORMAT,
        "version": 1,
        "source_project": export_safe(project),
        "source_budget": store.budget(project_id),
        "records": _safe_json(exported["records"]),
        "jobs": archived_jobs,
        "exported_at": now(),
    }
    payload = dumps(snapshot).encode("utf-8")
    if len(payload) > MAX_JSON:
        raise ValueError("项目元数据超过备份大小限制")
    manifest = {
        "format": FORMAT,
        "version": 1,
        "bundle_id": uid(),
        "source_project_id": project_id,
        "snapshot": {
            "name": "snapshot.json",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "media": [{k: v for k, v in entry.items() if k != "source"} for entry in files.values()],
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise Conflict("备份已存在，禁止覆盖旧快照")
    temporary = destination.with_name(destination.name + "." + uid() + ".part")
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", dumps(manifest))
            output.writestr("snapshot.json", payload)
            for entry in files.values():
                if _hash(entry["source"]) != entry["sha256"]:
                    raise Conflict("备份期间媒体发生变化，请停止写入后重试")
                output.write(entry["source"], entry["name"])
        # Atomic no-replace publication preserves any concurrently-created prior backup.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "sha256": _hash(destination),
        "bundle_id": manifest["bundle_id"],
        "media_files": len(files),
        "records": sum(map(len, records.values())),
        "archived_jobs": len(archived_jobs),
    }


def _member_name(name):
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(p in {"", "..", "."} for p in name.split("/"))
        or path.is_absolute()
    ):
        raise ValueError("归档路径越界或不规范")
    return name


def _read_checked(archive, entry):
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("size"), int)
        or not 0 <= entry["size"] <= MAX_TOTAL
        or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
    ):
        raise ValueError("校验清单格式无效")
    name = _member_name(entry["name"])
    try:
        if archive.getinfo(name).file_size != entry["size"]:
            raise ValueError("归档条目大小与清单不一致")
        data = archive.read(name)
    except (KeyError, zipfile.BadZipFile):
        raise ValueError("归档条目缺失或校验失败") from None
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("checksum 校验失败，禁止导入")
    return data


def _rewrite(value, old, new, facts_old, facts_new, id_mapping=None):
    id_mapping = id_mapping or {}
    if isinstance(value, dict):
        return {
            id_mapping.get(k, k): new
            if k == "project_id" and v == old
            else facts_new
            if k == "id" and v == facts_old
            else _rewrite(v, old, new, facts_old, facts_new, id_mapping)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_rewrite(v, old, new, facts_old, facts_new, id_mapping) for v in value]
    if isinstance(value, str):
        return id_mapping.get(value, value)
    return value


def _pointer_parent(record, pointer):
    if (
        not isinstance(pointer, list)
        or not pointer
        or not isinstance(pointer[-1], str)
        or not (pointer[-1] == "path" or pointer[-1].endswith("_path"))
    ):
        raise ValueError("媒体绑定路径无效")
    node = record
    for part in pointer[:-1]:
        if (
            isinstance(node, dict)
            and isinstance(part, str)
            and part in node
            or isinstance(node, list)
            and isinstance(part, int)
            and not isinstance(part, bool)
            and 0 <= part < len(node)
        ):
            node = node[part]
        else:
            raise ValueError("媒体绑定指向不存在的快照位置")
    if not isinstance(node, dict):
        raise ValueError("媒体绑定目标不是字段")  # noqa: TRY004 - invalid external bundle
    return node, pointer[-1]


def import_bundle(store, media, archive_path, *, target_project_id, expected_sha256=None):
    with tempfile.TemporaryDirectory(prefix="creative-bundle-verify-") as verify:
        return _import_bundle(
            store,
            media,
            archive_path,
            target_project_id=target_project_id,
            expected_sha256=expected_sha256,
            verify_root=Path(verify),
        )


def _stream_checked(archive, entry, destination):
    if (
        not isinstance(entry.get("size"), int)
        or not 0 <= entry["size"] <= MAX_TOTAL
        or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
    ):
        raise ValueError("媒体校验清单格式无效")
    count = 0
    digest = hashlib.sha256()
    with archive.open(entry["name"]) as source, destination.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > entry["size"]:
                raise ValueError("媒体 checksum 大小校验失败")
            digest.update(chunk)
            output.write(chunk)
    if count != entry["size"] or digest.hexdigest() != entry["sha256"]:
        raise ValueError("媒体 checksum 校验失败")
    return destination


def _import_bundle(store, media, archive_path, *, target_project_id, expected_sha256, verify_root):
    """Restore once into an explicitly selected empty project, with no runnable jobs."""
    store.project(target_project_id)
    archive_path = Path(archive_path)
    digest = _hash(archive_path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("整个归档的 checksum 校验失败")
    if store.export_project(target_project_id)["records"] or store.jobs(target_project_id):
        raise Conflict("目标项目非空或已完成迁移，禁止重复导入")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        names = [_member_name(info.filename) for info in members]
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError("归档含重复或大小写冲突条目")
        if sum(i.file_size for i in members) > MAX_TOTAL:
            raise ValueError("归档解压总量超过限制")
        for info in members:
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise ValueError("归档禁止目录、符号链接和特殊文件")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MAX_JSON:
            raise ValueError("归档缺少有效清单")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("format") != FORMAT
            or manifest.get("version") != 1
            or not re.fullmatch(r"[0-9a-f-]{36}", str(manifest.get("bundle_id", "")))
        ):
            raise ValueError("不支持的归档格式或版本")
        if manifest.get("snapshot", {}).get("size", MAX_JSON + 1) > MAX_JSON:
            raise ValueError("快照大小超过限制")
        entries = manifest.get("media")
        if not isinstance(entries, list):
            raise ValueError("媒体校验清单缺失")  # noqa: TRY004 - invalid external bundle
        expected = {"manifest.json", "snapshot.json", *(entry.get("name") for entry in entries)}
        if (
            set(names) != expected
            or len(expected) != len(entries) + 2
            or manifest["snapshot"].get("name") != "snapshot.json"
        ):
            raise ValueError("归档含清单外或重复文件")
        snapshot = json.loads(_read_checked(archive, manifest["snapshot"]))
        if snapshot.get("format") != FORMAT or snapshot.get("version") != 1:
            raise ValueError("项目快照格式无效")
        old = manifest["source_project_id"]
        if snapshot.get("source_project", {}).get("id") != old:
            raise ValueError("清单与快照项目不一致")
        records = deepcopy(snapshot.get("records", {}))
        jobs = snapshot.get("jobs", [])
        if not isinstance(records, dict) or not isinstance(jobs, list):
            raise ValueError("快照记录结构无效")  # noqa: TRY004 - invalid external bundle
        reject_secrets(snapshot)
        if _safe_json(export_safe(snapshot)) != snapshot:
            raise ValueError("快照含凭证、原路径或未清理的授权地址")
        identities = {kind: {r["id"] for r in rows} for kind, rows in records.items()}
        identities["job"] = {j["id"] for j in jobs} | identities.get("migration_job", set())
        all_ids = [r["id"] for rows in records.values() for r in rows] + [j["id"] for j in jobs]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("快照记录标识重复")
        by_id = {r["id"]: r for rows in records.values() for r in rows}
        for kind, rows in records.items():
            for record in rows:
                _validate_scope(record, old, identities, record_kind=kind)
        for job in jobs:
            _validate_scope(job, old, identities)
        checked_media = []
        bindings_seen = set()
        for entry in entries:
            if not re.fullmatch(r"media/[0-9a-f]{64}\.[a-z0-9]{1,10}", entry["name"]) or entry["name"].split(
                "/"
            )[1].split(".")[0] != entry.get("sha256"):
                raise ValueError("媒体必须使用唯一校验值文件名，禁止路径折叠")
            data = _stream_checked(archive, entry, verify_root / str(len(checked_media)))
            if not isinstance(entry.get("bindings"), list) or not entry["bindings"]:
                raise ValueError("媒体必须绑定真实记录")
            for binding in entry["bindings"]:
                rid = binding.get("record_id")
                kind = binding.get("kind")
                pointer = binding.get("pointer")
                if rid not in identities.get(kind, set()):
                    raise ValueError("媒体绑定跨项目或记录不存在")
                marker = (rid, dumps(pointer))
                if marker in bindings_seen:
                    raise ValueError("同一媒体字段重复绑定")
                bindings_seen.add(marker)
                _pointer_parent(by_id[rid], pointer)
            checked_media.append((entry, data))
        if any((a["id"], dumps(["path"])) not in bindings_seen for a in records.get("asset", [])):
            raise ValueError("素材没有经 checksum 验证的实际媒体")
    # Only now, after ALL checksums/scope/path validation, touch destination media or records.
    destination = media.root / target_project_id
    if destination.is_symlink() or (destination / "restored").is_symlink():
        raise ValueError("目标媒体目录禁止符号链接")
    destination.mkdir(parents=True, exist_ok=True)
    lock = destination / ".migration.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Conflict("目标项目正在迁移，禁止并发导入") from None
    os.close(descriptor)
    restored = []
    final = destination / "restored" / manifest["bundle_id"]
    staging = None
    try:
        if (
            store.export_project(target_project_id)["records"]
            or store.jobs(target_project_id)
            or final.exists()
        ):
            raise Conflict("目标项目已有数据或历史迁移")
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination))
        for entry, data in checked_media:
            file = staging / Path(entry["name"]).name
            shutil.copyfile(data, file)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(final)
        staging = None
        for entry, _ in checked_media:
            for binding in entry["bindings"]:
                parent, field = _pointer_parent(by_id[binding["record_id"]], binding["pointer"])
                parent[field] = str(final / Path(entry["name"]).name)
        facts_old, facts_new = "facts-" + old, "facts-" + target_project_id
        # IDs are global within one SQLite workspace. Restoring beside the
        # source must allocate new identities and rewrite every frozen pointer,
        # including source-version dictionary keys, without replacing originals.
        with store.connection() as db:
            occupied = {r[0] for r in db.execute('SELECT id FROM records UNION SELECT id FROM jobs')}
        id_mapping = {identifier: uid() if identifier in occupied else identifier for identifier in all_ids}
        id_mapping[facts_old] = facts_new
        for kind, rows in records.items():
            for record in rows:
                fixed = _rewrite(record, old, target_project_id, facts_old, facts_new, id_mapping)
                rid = id_mapping[record['id']]
                if kind in {"model", "industry_connection", "cmm_connection"}:
                    fixed.update(has_key=False, requires_rebind=True)
                    if kind == "model":
                        fixed["enabled"] = False
                    if kind == "cmm_connection":
                        fixed.update(
                            status="unverified",
                            live_verified=False,
                            rights=None,
                            permission_checked_at=None,
                            capabilities={op: False for op in fixed.get("capabilities", {})},
                        )
                if kind == "creative_context_snapshot":
                    fixed.update(state='archived_restored', archived_restored=True,
                        archival=True, audit_only=True, restored_at=now(),
                        model_use_requires_revalidation=True, replay_allowed=False,
                        source_snapshot_hash=record.get('source_snapshot_hash') or record.get('snapshot_hash'))
                if kind == "cmm_tariff":
                    fixed.update(verified=False, requires_reverification=True)
                if kind == "cmm_job":
                    fixed.update(
                        state="archived",
                        original_state=record["state"],
                        archival=True,
                        replay_allowed=False,
                        attempt_owner=None,
                        requires_reconciliation=record.get("actual_points") is None,
                    )
                store.restore_record(target_project_id, kind, fixed, rid)
                restored.append((kind, rid))
        for job in jobs:
            fixed = _rewrite(job, old, target_project_id, facts_old, facts_new, id_mapping)
            fixed.update(
                state="archived",
                original_state=job["state"],
                archival=True,
                replay_allowed=False,
                requires_reconciliation=job["actual_cost"] is None,
            )
            store.restore_record(target_project_id, "migration_job", fixed, id_mapping[job['id']])
            restored.append(("migration_job", id_mapping[job['id']]))
        receipt = {
            "bundle_id": manifest["bundle_id"],
            "bundle_sha256": digest,
            "source_project_id": old,
            "target_project_id": target_project_id,
            "imported_at": now(),
            "records": len(restored),
            "media_files": len(checked_media),
            "source_budget": snapshot.get("source_budget"),
            "accounting_reconciliation_required": True,
            "replay_allowed": False,
            "id_mapping": id_mapping,
        }
        store.restore_record(
            target_project_id, "migration_receipt", receipt, "migration-" + manifest["bundle_id"] + '-' + target_project_id
        )
        return receipt
    except Exception:
        for kind, rid in reversed(restored):
            store.delete(target_project_id, kind, rid)
        if final.is_dir():
            shutil.rmtree(final)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)
