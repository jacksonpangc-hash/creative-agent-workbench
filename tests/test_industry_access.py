"""License revocation must protect cached and derived content at the real HTTP boundary."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_industry import Keys, archived_scope, rights

from creative_agent.api import create_app
from creative_agent.contracts import CostQuote
from creative_agent.industry import SourceRights

H = {"Authorization": "Bearer test-session"}


@pytest.fixture
def licensed_app(tmp_path):
    app = create_app(tmp_path, token="test-session", secret_store=Keys(), run_jobs=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        s, svc = app.state.store, app.state.industry
        p = s.create_project("P", "10", "USD")["id"]
        png = io.BytesIO()
        Image.new("RGB", (8, 8)).save(png, format="PNG")
        owned_path = tmp_path / "owned.png"
        owned_path.write_bytes(png.getvalue())
        owned = app.state.media.ingest(p, owned_path, title="Owned visible", source_kind="own")
        licensed = {
            **rights(),
            "download_media": True,
            "retain_media": True,
            "model_process": True,
            "derivative_create": True,
            "export_metadata": True,
            "export_media": True,
        }
        conn, job, row = archived_scope(
            svc,
            p,
            allowed=licensed,
            title="Licensed hidden",
            images=["https://93.184.216.34/x.png"],
            format="image",
        )
        ref = s.put(
            p,
            "industry_reference",
            {
                "job_id": job["id"],
                "result_id": row["id"],
                "selection_reason": "source",
                "rights_snapshot": licensed,
            },
        )
        licensed_path = tmp_path / "licensed.png"
        Image.new("RGB", (9, 9), color="red").save(licensed_path)
        asset = app.state.media.ingest(p, licensed_path, title="Licensed hidden", source_kind="industry")
        ref["asset_id"] = asset["id"]
        s.put(p, "industry_reference", ref, ref["id"])
        asset["metadata"].update(
            industry_reference_ids=[ref["id"]],
            industry_reference_id=ref["id"],
            source_result_id=row["id"],
            provenance=row["provenance"],
        )
        s.put(p, "asset", asset, asset["id"])
        session = s.put(
            p,
            "session",
            {
                "title": "Licensed hidden session",
                "scope": {"project_id": p, "asset_ids": [asset["id"]]},
                "turns": [
                    {
                        "question": "q",
                        "answer": {
                            "summary": "Licensed hidden",
                            "findings": [{"type": "hypothesis", "text": "x", "evidence_ids": []}],
                            "result": {"script_outline": ["Licensed hidden dialogue"]},
                        },
                        "evidence": [],
                    }
                ],
            },
        )
        parse = s.put(
            p,
            "parse",
            {
                "asset_ids": [owned["id"]],
                "source_context": {"session_id": session["id"]},
                "rows": [],
                "fields": [],
            },
        )
        brief = s.put(
            p,
            "brief",
            {"title": "Licensed hidden brief", "source": {"session_id": session["id"]}, "version": 1},
        )
        note = s.put(
            p,
            "note",
            {
                "title": "Licensed hidden note",
                "markdown": "Licensed hidden",
                "session_id": session["id"],
                "scope": session["scope"],
                "revisions": [],
            },
        )
        script = s.put(
            p,
            "script",
            {
                "title": "Licensed hidden script",
                "source": {"session_id": session["id"]},
                "rows": [],
                "version": 1,
            },
        )
        production = s.put(
            p, "production", {"script_id": script["id"], "script_snapshot": script, "status": "planned"}
        )
        cached = s.put(p, "search", {"asset_ids": [owned["id"], asset["id"]], "scores": [1, 1]})

        def revoke(**changes):
            svc.update_rights(
                p,
                conn["id"],
                SourceRights(**{**licensed, "retain_media": False, "retain_metadata": False, **changes}),
            )

        yield client, p, asset, owned, session, parse, brief, note, script, production, cached, revoke


def test_state_and_search_hide_revoked_sources_and_all_cached_derivations(licensed_app):
    c, p, _asset, owned, _session, _parse, _brief, _note, _script, _production, cached, revoke = licensed_app
    revoke()
    state = c.get(f"/api/projects/{p}/state", headers=H)
    assert state.status_code == 200
    assert "Licensed hidden" not in state.text
    assert [a["id"] for a in state.json()["assets"]] == [owned["id"]]
    for key in ("sessions", "parses", "briefs", "notes"):
        assert state.json()[key] == []
    result = c.post(f"/api/projects/{p}/search", headers=H, json={"scope": {"discovery": True}})
    assert result.status_code == 200
    assert [a["id"] for a in result.json()] == [owned["id"]]
    cached_result = c.get(f"/api/projects/{p}/search-results/{cached['id']}", headers=H)
    assert cached_result.status_code == 200
    assert [a["id"] for a in cached_result.json()] == [owned["id"]]


@pytest.mark.parametrize("suffix", ["file", "file?poster=true", "scenes", "frames", "clip"])
def test_media_routes_deny_revoked_source_before_reading_or_processing(licensed_app, suffix):
    c, p, asset, owned, *rest = licensed_app
    rest[-1]()
    response = c.get(f"/api/projects/{p}/assets/{asset['id']}/{suffix}", headers=H)
    assert response.status_code == 400
    assert "授权" in response.text
    assert c.get(f"/api/projects/{p}/assets/{owned['id']}/file", headers=H).status_code == 200


def test_derived_read_edit_export_and_creation_routes_deny_revoked_source(licensed_app):
    c, p, _asset, _owned, session, parse, brief, note, script, _production, _cached, revoke = licensed_app
    revoke()
    requests = [
        ("get", f"briefs/{brief['id']}", None),
        ("get", f"notes/{note['id']}/export", None),
        ("get", f"parses/{parse['id']}/export", None),
        ("get", f"scripts/{script['id']}", None),
        ("get", f"scripts/{script['id']}/comments", None),
        ("post", "notes", {"session_id": session["id"]}),
        ("put", f"notes/{note['id']}", {"markdown": "edited"}),
        ("post", "scripts/from-session", {"session_id": session["id"], "turn_index": 0}),
    ]
    for method, suffix, body in requests:
        response = getattr(c, method)(
            f"/api/projects/{p}/{suffix}", headers=H, **({"json": body} if body else {})
        )
        assert response.status_code == 400, (suffix, response.text)
        assert "Licensed hidden" not in response.text
    assert c.get(f"/api/projects/{p}/scripts", headers=H).json() == []
    assert c.get(f"/api/projects/{p}/production", headers=H).json() == []
    assert c.get(f"/api/projects/{p}/export", headers=H).status_code == 400


def test_generation_rechecks_revocation_before_loading_provider_or_running(licensed_app, monkeypatch):
    c, p, asset, _owned, *rest = licensed_app
    s = c.app.state.store
    job = s.create_job(
        p,
        "generation",
        {
            "request": {
                "project_id": p,
                "reference_asset_id": asset["id"],
                "reference_image_ids": [],
                "prompt": "q",
            },
            "binding_id": "absent",
        },
        CostQuote(amount="1", currency="USD", basis="test"),
        "generation-before-revoke",
    )
    rest[-1]()
    c.app.state.execute(p, job["id"])
    saved = s.job(p, job["id"])
    assert saved["state"] == "cancelled"
    assert "停用" in saved["error"] or "历史" in saved["error"] or "取消" in saved["error"]
    assert saved["actual_cost"] == "0"


def test_confirmed_generation_snapshot_uses_its_sources_not_later_script_revisions(licensed_app):
    from creative_agent.licensing_access import LicensedAccess

    c, p, _asset, owned, _session, _parse, _brief, _note, script, _production, _cached, revoke = licensed_app
    s = c.app.state.store
    old_snapshot = {
        "id": script["id"],
        "project_id": p,
        "version": 1,
        "rows": [{"references": [{"asset_id": owned["id"]}]}],
    }
    job = s.create_job(
        p,
        "generation",
        {
            "request": {
                "project_id": p,
                "reference_asset_id": owned["id"],
                "script_id": script["id"],
                "script_version": 1,
            },
            "script_snapshot": old_snapshot,
        },
        CostQuote(amount="1", currency="USD", basis="test"),
        "frozen-script",
    )
    revoke()
    assert LicensedAccess(s, c.app.state.industry).check(p, job, "job") == job


def test_revoked_job_can_be_cancelled_without_returning_cached_content(licensed_app):
    c, p, _asset, _owned, session, *rest = licensed_app
    s = c.app.state.store
    job = s.create_job(
        p,
        "analysis",
        {"session_id": session["id"], "question": "Licensed hidden"},
        CostQuote(amount="1", currency="USD", basis="test"),
        "cancel-revoked",
    )
    rest[-1]()
    response = c.post(f"/api/projects/{p}/jobs/{job['id']}/cancel", headers=H)
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert "Licensed hidden" not in response.text
    assert response.json()["content_restricted"] is True


def test_scene_thumbnail_and_generation_poll_check_revoked_lineage(licensed_app):
    c, p, asset, _owned, *rest = licensed_app
    s = c.app.state.store
    scene = next(scene for scene in s.list(p, "scene") if scene["asset_id"] == asset["id"])
    job = s.create_job(
        p,
        "generation",
        {"request": {"project_id": p, "reference_asset_id": asset["id"]}, "binding_id": "absent"},
        CostQuote(amount="1", currency="USD", basis="test"),
        "poll-revoked",
    )
    rest[-1]()
    response = c.get(f"/api/projects/{p}/assets/{asset['id']}/scene/{scene['id']}", headers=H)
    assert response.status_code == 400
    response = c.post(f"/api/projects/{p}/jobs/{job['id']}/poll", headers=H)
    assert response.status_code == 400
    assert "授权" in response.text
