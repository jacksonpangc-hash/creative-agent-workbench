"""CMM portable history retains typed lineage, never credentials or replay grants."""

import json
import zipfile

import httpx
import pytest
from test_industry import Keys, rights

from creative_agent.cmm import CMMService
from creative_agent.media import MediaService
from creative_agent.portability import export_bundle, import_bundle
from creative_agent.store import Store


@pytest.fixture
def portable(tmp_path):
    store = Store(tmp_path / "source.sqlite")
    p = store.create_project("source", "0")["id"]
    v = Keys()
    media = MediaService(store, tmp_path / "source-media")
    cmm = CMMService(store, v)
    cmm.connect(p, api_key="portable-private-key")
    cmm.configure_tariff(p, operation="permission_check", max_points_per_call="0",
                         evidence="offline-test-only", verified=True)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "group_id": "9",
                        "version": "2026-10-14",
                        "rights": {"extract_video_copywriting": True},
                    },
                },
            )
        )
    ) as client:
        cmm.permissions(p, "已授权备份", client=client)
    cmm.update_rights(p, {**rights(), "model_process": True, "export_metadata": True})
    cmm.configure_tariff(
        p, operation="extract_video_copywriting", max_points_per_call="1", evidence="核验收费", verified=True
    )
    j = cmm.create_job(
        p,
        operation="extract_video_copywriting",
        query={"video_url": "https://v.douyin.com/a/"},
        max_calls=1,
        max_points="1",
        idempotency_key="source",
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"code": 0, "data": "许可范围内口播"})
        )
    ) as client:
        r = cmm.run(p, j["id"], client=client)
    parent = cmm.select(p, j["id"], [r["results"][0]["id"]])[0]
    child = cmm.create_reference_content(
        p, parent["id"], kind="copywriting", max_calls=1, max_points="1", idempotency_key="derived"
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 0, "data": "衍生口播"}))
    ) as client:
        r = cmm.run(p, child["id"], client=client)
    ref = cmm.select(p, child["id"], [r["results"][0]["id"]])[0]
    store.put(
        p,
        "session",
        {
            "title": "来源引用",
            "scope": {"project_id": p, "reference_ids": [ref["id"]]},
            "source_job_id": child["id"],
            "cmm_job_id": child["id"],
            "reference_id": ref["id"],
            "external_evidence": [ref],
        },
    )
    return store, media, cmm, p, parent, ref


def test_cmm_bundle_roundtrip_typed_lineage_no_secrets_no_auth_grants_no_replay(portable, tmp_path):
    s, m, _cmm, p, parent, ref = portable
    bundle = tmp_path / "cmm.zip"
    exported = export_bundle(s, m, p, bundle)
    assert exported["media_files"] == 0
    with zipfile.ZipFile(bundle) as archive:
        snapshot = json.loads(archive.read("snapshot.json"))
    assert "portable-private-key" not in json.dumps(snapshot)
    assert "secret_ref" not in json.dumps(snapshot)
    dest = Store(tmp_path / "dest.sqlite")
    q = dest.create_project("target", "0")["id"]
    dm = MediaService(dest, tmp_path / "dest-media")
    import_bundle(dest, dm, bundle, target_project_id=q)
    conn = dest.list(q, "cmm_connection")[0]
    assert conn["has_key"] is False and conn["requires_rebind"] is True
    assert (
        conn["status"] == "unverified" and not any(conn["capabilities"].values()) and conn["rights"] is None
    )
    jobs = dest.list(q, "cmm_job")
    assert all(j["state"] == "archived" and j["replay_allowed"] is False for j in jobs)
    assert dest.get(q, "cmm_reference", ref["id"])["parent_reference_id"] == parent["id"]
    restored = CMMService(dest, Keys())
    assert restored.overview(q)["connection"]["has_key"] is False
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        for j in jobs:
            assert restored.run(q, j["id"], client=client)["state"] == "archived"
    assert calls == []
    with pytest.raises(ValueError):
        restored.validated_evidence(q, [ref["id"]])
    assert restored.references(q)[0]["content_restricted"] is True
    assert dest.jobs(q) == []


def test_cmm_bundle_cross_project_parent_graph_rejected_before_export(portable, tmp_path):
    s, m, _cmm, p, _parent, ref = portable
    q = s.create_project("other", "0")["id"]
    foreign = s.put(q, "cmm_reference", {"job_id": "other", "rights_snapshot": rights()})
    ref["parent_reference_id"] = foreign["id"]
    s.put(p, "cmm_reference", ref, ref["id"])
    with pytest.raises(ValueError):
        export_bundle(s, m, p, tmp_path / "invalid.zip")
    assert not (tmp_path / "invalid.zip").exists()


def test_cmm_bundle_export_still_requires_original_and_current_export_permission(portable, tmp_path):
    s, m, cmm, p, _parent, _ref = portable
    cmm.update_rights(p, {**rights(), "model_process": True, "export_metadata": False})
    with pytest.raises(ValueError):
        export_bundle(s, m, p, tmp_path / "unauthorized.zip")


def test_cmm_import_rejects_missing_typed_reference_before_writing_target(portable, tmp_path):
    import hashlib

    s, m, _cmm, p, _parent, _ref = portable
    source = tmp_path / "valid.zip"
    export_bundle(s, m, p, source)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        snapshot = json.loads(archive.read("snapshot.json"))
    snapshot["records"]["session"][0]["scope"]["reference_ids"] = ["ffffffff-ffff-ffff-ffff-ffffffffffff"]
    data = json.dumps(snapshot, ensure_ascii=False).encode()
    manifest["snapshot"].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("snapshot.json", data)
    dest = Store(tmp_path / "rejected.sqlite")
    q = dest.create_project("target", "0")["id"]
    media = MediaService(dest, tmp_path / "rejected-media")
    with pytest.raises(ValueError, match="来源列表"):
        import_bundle(dest, media, tampered, target_project_id=q)
    assert dest.export_project(q)["records"] == {} and dest.jobs(q) == []
