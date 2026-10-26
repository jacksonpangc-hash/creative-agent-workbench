"""Portable bundles use actual Store/media/archives, never supplier calls."""

import hashlib
import importlib.util
import json
import stat
import zipfile

import pytest
from PIL import Image

from creative_agent.contracts import CostQuote
from creative_agent.media import MediaService
from creative_agent.scripts import Scripts
from creative_agent.store import Conflict, Store


def portability():
    assert importlib.util.find_spec("creative_agent.portability"), "Portability service is missing"
    from creative_agent import portability as module

    return module


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "source.db")
    p = store.create_project("产品项目", "10")["id"]
    media = MediaService(store, tmp_path / "source-media")
    image = tmp_path / "source.png"
    Image.new("RGB", (12, 8), "purple").save(image)
    asset = media.ingest(p, image, source_kind="own", title="真实产品图")
    scripts = Scripts(store)
    script = scripts.create(
        p,
        {
            "title": "脚本v1",
            "objective": "开头对比",
            "rows": [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "dialogue": "原台词",
                    "references": [{"asset_id": asset["id"]}],
                }
            ],
        },
    )
    confirmed = scripts.revise(
        p,
        script["id"],
        1,
        {
            "title": "已确认脚本",
            "objective": "开头对比",
            "confirmed": True,
            "rows": [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "dialogue": "确认台词",
                    "reshoot": "补拍确认台词对应的产品展示",
                    "references": [{"asset_id": asset["id"]}],
                }
            ],
        },
    )
    task = scripts.produce(p, script["id"], 2, "production", "编导")
    scripts.revise(
        p,
        script["id"],
        2,
        {
            "title": "新草稿",
            "objective": "新版本",
            "rows": [{"start_ms": 0, "end_ms": 1000, "dialogue": "之后的草稿"}],
        },
    )
    scripts.save_facts(
        p, 0, {"product": "产品", "claims": [{"statement": "产品事实", "source": "标签", "confirmed": True}]}
    )
    store.put(
        p,
        "model",
        {"provider": "deepseek", "has_key": True, "secret_ref": p + ":secret-version", "enabled": True},
    )
    job = store.create_job(
        p,
        "analysis",
        {
            "asset_ids": [asset["id"]],
            "binding_snapshot": {"secret_ref": "never-export-key-ref", "has_key": True},
        },
        CostQuote(amount="1", basis="confirmed"),
        "charge",
    )
    store.transition(p, job["id"], "running")
    store.transition(p, job["id"], "submitting")
    return store, media, p, asset, confirmed, task, job


def target(tmp_path):
    store = Store(tmp_path / "target.db")
    p = store.create_project("恢复项目")["id"]
    return store, MediaService(store, tmp_path / "target-media"), p


def test_v21_frozen_creative_scope_and_feedback_restore_without_replay(source,tmp_path):
    from creative_agent.creative_tasks import TaskSpec,freeze_selection
    store,media,p,asset,script,task,job=source
    spec=TaskSpec(output_type='scripts',own_asset_ids=[asset['id']],history_script_ids=[script['id']])
    snapshot=freeze_selection(store,p,{'project_id':p,'asset_ids':[asset['id']]},spec)
    session=store.put(p,'session',{'scope':snapshot['scope'],'selection_snapshot':snapshot,'turns':[]})
    archive=tmp_path/'v21.zip';portability().export_bundle(store,media,p,archive)
    dst,dm,q=target(tmp_path)
    portability().import_bundle(dst,dm,archive,target_project_id=q)
    restored=dst.get(q,'session',session['id'])
    assert restored['selection_snapshot']['project_id']==q
    assert restored['selection_snapshot']['own_assets'][0]['id']==asset['id']
    assert dst.get(q,'script',script['id'])['version']==3
    assert dst.jobs(q)==[]
    # Foreign reusable pools must be rejected by export, even without selected references.
    store.put(p,'session',{'scope':{'project_id':p,'asset_ids':[]},'task_spec':{'own_asset_ids':['other-project-asset']},'turns':[]})
    with pytest.raises(ValueError,match='引用|范围'):portability().export_bundle(store,media,p,tmp_path/'invalid.zip')


def test_restore_record_is_insert_only_scoped_and_rejects_secrets(tmp_path):
    s = Store(tmp_path / "db")
    p = s.create_project("p")["id"]
    q = s.create_project("q")["id"]
    assert hasattr(s, "restore_record"), "Store lacks insert-only restore contract"
    saved = s.restore_record(p, "script", {"title": "snapshot"}, "original-uuid")
    assert saved["id"] == "original-uuid"
    with pytest.raises(Conflict):
        s.restore_record(q, "script", {"title": "overwrite"}, "original-uuid")
    with pytest.raises(ValueError):
        s.restore_record(p, "model", {"api_key": "never-store"}, "secret")
    with pytest.raises(ValueError):
        s.restore_record(p, "script", {"project_id": q}, "foreign")


def test_roundtrip_real_media_confirmed_versions_and_archived_charged_jobs(source, tmp_path):
    s, media, p, asset, confirmed, task, job = source
    module = portability()
    archive = tmp_path / "bundle.zip"
    manifest = module.export_bundle(s, media, p, archive)
    assert (
        manifest["media_files"] >= 2
        and manifest["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(archive) as z:
        snapshot = json.loads(z.read("snapshot.json"))
        assert "secret_ref" not in json.dumps(snapshot) and "never-export-key-ref" not in json.dumps(snapshot)
        assert snapshot["records"]["model"][0]["has_key"] is False
    t, tm, tp = target(tmp_path)
    receipt = module.import_bundle(t, tm, archive, target_project_id=tp, expected_sha256=manifest["sha256"])
    restored = t.get(tp, "asset", asset["id"])
    assert tm.file(tp, restored["id"]).read_bytes() == media.file(p, asset["id"]).read_bytes()
    assert all(__import__("pathlib").Path(scene["poster_path"]).is_file() for scene in t.list(tp, "scene"))
    assert restored["path"].startswith(str(tm.root / tp)) and str(media.root) not in json.dumps(
        t.export_project(tp)
    )
    restored_task = t.get(tp, "production", task["id"])
    assert restored_task["script_snapshot"]["confirmed"] is True
    assert (
        restored_task["script_snapshot"]["version"] == 2
        and restored_task["script_snapshot"]["rows"][0]["dialogue"] == "确认台词"
    )
    assert Scripts(t).version(tp, confirmed["id"], 1)["rows"][0]["dialogue"] == "原台词"
    assert Scripts(t).facts(tp)["product"] == "产品"
    assert t.get(tp, "model", s.list(p, "model")[0]["id"])["enabled"] is False
    assert t.jobs(tp) == [] and t.pending_jobs() == []
    archived = t.job(tp, job["id"])
    assert (
        archived["state"] == "archived"
        and archived["original_state"] == "submitting"
        and archived["replay_allowed"] is False
    )
    assert archived["quote"]["amount"] == "1" and archived["actual_cost"] is None
    with pytest.raises(ValueError):
        t.transition(tp, job["id"], "queued")
    with pytest.raises(Conflict):
        module.import_bundle(t, tm, archive, target_project_id=tp)
    assert receipt["source_project_id"] == p and receipt["target_project_id"] == tp


def rewrite_archive(archive, out, mutate=None, extra=None):
    with zipfile.ZipFile(archive) as z:
        entries = [(i, z.read(i)) for i in z.infolist()]
    with zipfile.ZipFile(out, "w") as z:
        for info, data in entries:
            if mutate:
                data = mutate(info.filename, data)
            z.writestr(info, data)
        if extra:
            for name, data in extra:
                z.writestr(name, data)
    return out


def test_corrupt_media_fails_before_target_records_or_media_are_written(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    archive = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, archive)
    bad = rewrite_archive(
        archive, tmp_path / "bad.zip", mutate=lambda n, b: b + b"corrupt" if n.startswith("media/") else b
    )
    t, tm, tp = target(tmp_path)
    with pytest.raises(ValueError, match="校验|checksum"):
        module.import_bundle(t, tm, bad, target_project_id=tp)
    assert t.export_project(tp)["records"] == {}
    assert not list((tm.root / tp).glob("**/original*"))


@pytest.mark.parametrize(
    "unsafe", ["../escape", "/absolute", "C:/windows", "media/../escape", "media\\escape"]
)
def test_rejects_traversal_and_unlisted_entries(source, tmp_path, unsafe):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    bad = rewrite_archive(a, tmp_path / "bad.zip", extra=[(unsafe, b"bad")])
    t, tm, tp = target(tmp_path)
    with pytest.raises(ValueError):
        module.import_bundle(t, tm, bad, target_project_id=tp)
    assert t.export_project(tp)["records"] == {}


def test_archive_symlinks_duplicate_members_and_foreign_source_paths_are_rejected(source, tmp_path):
    s, m, p, asset, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    link = zipfile.ZipInfo("media/symlink")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink = rewrite_archive(a, tmp_path / "symlink.zip", extra=[(link, b"../../outside")])
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = rewrite_archive(a, tmp_path / "duplicate.zip", extra=[("snapshot.json", b"{}")])
    t, tm, tp = target(tmp_path)
    for candidate in [symlink, duplicate]:
        with pytest.raises(ValueError):
            module.import_bundle(t, tm, candidate, target_project_id=tp)
    other = s.create_project("other")["id"]
    foreign = m.root / other / "secret.png"
    foreign.parent.mkdir()
    Image.new("RGB", (8, 8)).save(foreign)
    s.put(p, "asset", {**asset, "path": str(foreign)}, asset["id"])
    with pytest.raises(ValueError):
        module.export_bundle(s, m, p, tmp_path / "foreign.zip")


def test_export_denies_cross_project_normalized_lineage_and_requires_licensed_export_guard(source, tmp_path):
    s, m, p, asset, *_ = source
    module = portability()
    other = s.create_project("other")["id"]
    foreign = s.put(other, "note", {"markdown": "foreign"})
    note = s.put(p, "note", {"source_session_id": foreign["id"]})
    with pytest.raises(ValueError):
        module.export_bundle(s, m, p, tmp_path / "cross.zip")
    s.delete(p, "note", note["id"])
    s.put(p, "asset", {**asset, "source_kind": "industry"}, asset["id"])
    with pytest.raises(ValueError, match="授权"):
        module.export_bundle(s, m, p, tmp_path / "licensed.zip")


def test_import_rejects_nonempty_target_and_archive_checksum_mismatch(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    t, tm, tp = target(tmp_path)
    with pytest.raises(ValueError):
        module.import_bundle(t, tm, a, target_project_id=tp, expected_sha256="0" * 64)
    t.put(tp, "note", {"markdown": "keep existing"})
    with pytest.raises(Conflict):
        module.import_bundle(t, tm, a, target_project_id=tp)
    assert t.list(tp, "note")[0]["markdown"] == "keep existing"


def test_content_addressed_media_names_cannot_flatten_into_a_collision(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    with zipfile.ZipFile(a) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(entries["manifest.json"])
    first, second = manifest["media"][:2]
    old = second["name"]
    second["name"] = "media/subdir/" + first["name"].split("/")[-1]
    entries[second["name"]] = entries.pop(old)
    entries["manifest.json"] = json.dumps(manifest).encode()
    bad = tmp_path / "collision.zip"
    with zipfile.ZipFile(bad, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    t, tm, tp = target(tmp_path)
    with pytest.raises(ValueError):
        module.import_bundle(t, tm, bad, target_project_id=tp)
    assert t.export_project(tp)["records"] == {}


def test_symlink_target_media_directory_cannot_escape_restore_root(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    t, tm, tp = target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tm.root / tp).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        module.import_bundle(t, tm, a, target_project_id=tp)
    assert list(outside.iterdir()) == [] and t.export_project(tp)["records"] == {}


def test_changed_original_media_cannot_keep_an_old_asset_checksum(source, tmp_path):
    s, m, p, asset, *_ = source
    Image.new("RGB", (12, 8), "green").save(m.file(p, asset["id"]))
    with pytest.raises(ValueError, match="checksum|校验"):
        portability().export_bundle(s, m, p, tmp_path / "changed.zip")


def test_restore_write_failure_rolls_back_only_new_records_and_files(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)

    class FailingTarget(Store):
        attempts = 0

        def restore_record(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                raise RuntimeError("permission changed during restore")
            return super().restore_record(*args, **kwargs)

    t = FailingTarget(tmp_path / "rollback.db")
    tp = t.create_project("target")["id"]
    tm = MediaService(t, tmp_path / "rollback-media")
    with pytest.raises(RuntimeError):
        module.import_bundle(t, tm, a, target_project_id=tp)
    assert t.export_project(tp)["records"] == {} and t.jobs(tp) == []
    assert not list((tm.root / tp).glob("**/*.png")) and not list((tm.root / tp).glob("**/*.jpg"))
    assert not (tm.root / tp / ".migration.lock").exists()


def test_forged_cross_project_snapshot_is_rejected_before_target_writes(source, tmp_path):
    s, m, p, *_ = source
    module = portability()
    a = tmp_path / "bundle.zip"
    module.export_bundle(s, m, p, a)
    with zipfile.ZipFile(a) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    snapshot = json.loads(entries["snapshot.json"])
    snapshot["records"]["script"][0]["revisions"][0]["project_id"] = "foreign-project"
    entries["snapshot.json"] = json.dumps(snapshot, ensure_ascii=False).encode()
    manifest = json.loads(entries["manifest.json"])
    manifest["snapshot"].update(
        size=len(entries["snapshot.json"]), sha256=hashlib.sha256(entries["snapshot.json"]).hexdigest()
    )
    entries["manifest.json"] = json.dumps(manifest).encode()
    bad = tmp_path / "foreign-snapshot.zip"
    with zipfile.ZipFile(bad, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    t, tm, tp = target(tmp_path)
    with pytest.raises(ValueError):
        module.import_bundle(t, tm, bad, target_project_id=tp)
    assert t.export_project(tp)["records"] == {}


def test_export_callback_cannot_bypass_mandatory_licensed_rights(source, tmp_path):
    s, m, p, asset, *_ = source
    s.put(p, "asset", {**asset, "source_kind": "industry"}, asset["id"])
    with pytest.raises(ValueError, match="授权"):
        portability().export_bundle(s, m, p, tmp_path / "bypass.zip", authorize=lambda project: None)


def test_runnable_cli_exports_and_restores_real_media_without_queueing_jobs(source, tmp_path, capsys):
    import shutil

    assert importlib.util.find_spec("creative_agent.tools_cli"), "Runnable migration CLI is missing"
    from creative_agent.tools_cli import main

    s, m, p, asset, *_ = source
    data = tmp_path / "cli-source"
    data.mkdir()
    shutil.copyfile(s.path, data / "creative.sqlite3")
    (data / "media").symlink_to(m.root, target_is_directory=True)
    output = tmp_path / "cli-bundle.zip"
    assert main(["--data-dir", str(data), "export", "--project", p, "--output", str(output)]) == 0
    digest = json.loads(capsys.readouterr().out)["sha256"]
    target_data = tmp_path / "cli-target"
    t = Store(target_data / "creative.sqlite3")
    tp = t.create_project("restore")["id"]
    assert (
        main(
            [
                "--data-dir",
                str(target_data),
                "import",
                "--archive",
                str(output),
                "--target-project",
                tp,
                "--sha256",
                digest,
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["target_project_id"] == tp
    tm = MediaService(t, target_data / "media")
    assert tm.file(tp, asset["id"]).read_bytes() == m.file(p, asset["id"]).read_bytes()
    assert t.jobs(tp) == []


def test_snapshot_size_mismatch_rejected_before_decompression():
    from types import SimpleNamespace

    class OversizedArchive:
        def getinfo(self, name):
            return SimpleNamespace(file_size=64 * 1024 * 1024)

        def read(self, name):
            pytest.fail("unbounded snapshot was decompressed before size validation")

    with pytest.raises(ValueError, match="大小"):
        portability()._read_checked(OversizedArchive(), {
            "name": "snapshot.json", "size": 1, "sha256": "0" * 64,
        })


def test_context_linked_script_backup_preserves_audit_snapshot_without_reactivating_it(source, tmp_path):
    from creative_agent.creative_context import CreativeContextService
    from creative_agent.knowledge_index import KnowledgeIndex
    store, media, p, asset, script, *_ = source
    context_service = CreativeContextService(store, media, index=KnowledgeIndex(store, media, embeddings=None))
    context = context_service.build_context(p, 'new_script', query='已确认产品', selected_asset_ids=[])
    # The audit snapshot must preserve structured support and original versions,
    # even though restored references are never automatically current evidence.
    context['chunks']['own'] = [{
        'chunk_id': 'historical-chunk', 'project_id': p, 'source_kind': 'asset',
        'source_record_id': asset['id'], 'asset_id': asset['id'], 'asset_sha256': asset['sha256'],
        'source_version': 'historical-source-version', 'content_hash': 'historical-content-hash',
        'start_ms': 100, 'end_ms': 900, 'text': '历史时码内容',
        'support': [{'frame_id': 'historical-frame', 'time_ms': 100}], 'quality_state': 'usable'}]
    context['source_versions'] = {asset['id']: 'historical-source-version'}
    from creative_agent.knowledge_index import digest
    context['snapshot_hash'] = digest({k: v for k, v in context.items() if k != 'snapshot_hash'})
    with store.connection(True) as db:
        db.execute('UPDATE retrieval_snapshot SET data=? WHERE id=?', (json.dumps(context), context['id']))
    current_script = store.get(p, 'script', script['id'])
    store.put(p, 'script', {**current_script, 'context_snapshot_id': context['id']}, script['id'])
    store.put(p, 'browser_grant', {'grant_id': 'must-not-revive', 'enabled': True})
    exported = store.export_project(p)
    assert exported['records']['creative_context_snapshot'][0]['chunks']['own'][0]['support'] == context['chunks']['own'][0]['support']
    bundle = tmp_path / 'context-script.zip'
    portability().export_bundle(store, media, p, bundle)
    with zipfile.ZipFile(bundle) as archive:
        payload = json.loads(archive.read('snapshot.json'))
        assert payload['records']['creative_context_snapshot'][0]['id'] == context['id']
        assert 'browser_grant' not in payload['records']
        assert 'knowledge_chunk' not in payload['records']
    restored_store, restored_media, q = target(tmp_path)
    portability().import_bundle(restored_store, restored_media, bundle, target_project_id=q)
    restored_script = restored_store.get(q, 'script', script['id'])
    restored_service = CreativeContextService(restored_store, restored_media,
        index=KnowledgeIndex(restored_store, restored_media, embeddings=None))
    historical = restored_service.snapshot(q, restored_script['context_snapshot_id'])
    assert historical['project_id'] == q
    assert historical['selection_snapshot']['product_facts_snapshot']['id'] == 'facts-' + q
    assert historical['chunks']['own'][0]['source_record_id'] == asset['id']
    assert historical['chunks']['own'][0]['source_version'] == 'historical-source-version'
    assert historical['chunks']['own'][0]['support'] == context['chunks']['own'][0]['support']
    assert historical['state'] == 'archived_restored' and historical['archived_restored'] is True
    assert historical['model_use_requires_revalidation'] is True and historical['replay_allowed'] is False
    assert restored_store.list(q, 'browser_grant') == []
    with restored_store.connection() as db:
        assert db.execute('SELECT count(*) FROM retrieval_snapshot WHERE project_id=?', (q,)).fetchone()[0] == 0
    with pytest.raises(ValueError, match='恢复.*历史|历史.*恢复'):
        restored_service.revalidate_context(q, historical['id'])
    with pytest.raises(ValueError, match='恢复.*历史|历史.*恢复'):
        restored_service.build_context(q, 'revise_current', previous_context_id=historical['id'], query='缩短开头')
    # Historical evidence is readable and manual drafts remain editable, but a
    # restored snapshot cannot turn a new script into verified current work.
    edited = Scripts(restored_store).revise(q, restored_script['id'], restored_script['version'],
        {'title': '恢复后人工改稿'})
    assert edited['title'] == '恢复后人工改稿' and not edited['confirmed']
    with pytest.raises(ValueError, match='恢复.*历史|历史.*恢复'):
        Scripts(restored_store).revise(q, edited['id'], edited['version'], {'confirmed': True})


def test_export_rejects_script_with_missing_creative_context_snapshot(source, tmp_path):
    store, media, p, _, script, *_ = source
    current_script = store.get(p, 'script', script['id'])
    store.put(p, 'script', {**current_script, 'context_snapshot_id': 'missing-context'}, script['id'])
    with pytest.raises(ValueError, match='context_snapshot_id'):
        portability().export_bundle(store, media, p, tmp_path / 'missing-context.zip')


def test_restore_into_same_workspace_preserves_original_and_remaps_links(source, tmp_path):
    s, media, p, asset, script, _, job = source
    session = s.put(p, 'session', {'scope': {'project_id': p, 'asset_ids': [asset['id']]},
        'turns': [], 'selection_snapshot': {'own_assets': [asset]}})
    original = s.get(p, 'script', script['id'])
    s.put(p, 'script', {**original, 'source': {'session_id': session['id']}}, script['id'])
    bundle = tmp_path / 'same-workspace.zip'
    portability().export_bundle(s, media, p, bundle)
    q = s.create_project('恢复的新项目')['id']
    receipt = portability().import_bundle(s, media, bundle, target_project_id=q)
    restored_asset = s.list(q, 'asset')[0]
    restored_script = s.list(q, 'script')[0]
    assert restored_asset['id'] != asset['id']
    assert restored_script['id'] != script['id']
    assert restored_script['source']['session_id'] == s.list(q, 'session')[0]['id']
    assert restored_script['revisions'][0]['rows'][0]['references'][0]['asset_id'] == restored_asset['id']
    assert media.file(q, restored_asset['id']).read_bytes() == media.file(p, asset['id']).read_bytes()
    assert s.get(p, 'script', script['id'])['title'] == original['title']
    assert s.job(q, receipt['id_mapping'][job['id']])['replay_allowed'] is False
    assert Scripts(s).facts(q)['product'] == '产品'
