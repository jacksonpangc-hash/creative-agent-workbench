"""Read views must preserve scope, evidence versions and canonical identities."""

import importlib.util
import json

import pytest

from creative_agent.contracts import CostQuote
from creative_agent.evidence_policy import transcript_policy
from creative_agent.industry import Industry
from creative_agent.licensing_access import LicensedAccess
from creative_agent.store import NotFound, Store, dumps


class Keys:
    def get(self, _key):
        return None


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "db")
    p = store.create_project("Current project")["id"]
    q = store.create_project("Other project")["id"]
    access = LicensedAccess(store, Industry(store, None, Keys()))
    return store, p, q, access


def views(env):
    assert importlib.util.find_spec("creative_agent.material_views"), "Scoped material read views are missing"
    from creative_agent.material_views import MaterialViews

    return MaterialViews(env[0], env[3])


def asset(store, p, title="First", source="own"):
    return store.put(p, "asset", {
        "title": title, "source_kind": source, "media_type": "video", "inspection": "ready",
        "sha256": title + "-media-v1", "path": "/Users/test/private/video.mp4",
        "metadata": {"duration_ms": 2000, "width": 720, "transcript": title + " words",
                     "transcript_source": "user_import",
                     "subtitles": [{"start_ms": 0, "end_ms": 1000, "text": title + " words"}]},
    })


def evidence(store, p, a, label=None):
    policy = transcript_policy(a)
    manifest = store.put(p, "media_input_manifest", {
        "asset_id": a["id"], "asset_sha256": a["sha256"], "transcript": policy,
        "execution_state": "succeeded", "coverage": {"visual_basis": "actual_sampled_frames"},
    })
    return {"id": "e-" + a["id"], "asset_id": a["id"], "asset_sha256": a["sha256"],
            "input_manifest_id": manifest["id"], "provenance": "sampled_frames",
            "observation": {"summary": label or a["title"] + " observation"},
            "coverage": {"visual_basis": "actual_sampled_frames"}}


def session(store, p, assets, entries):
    return store.put(p, "session", {
        "title": "Analysis", "mode": "directed", "scope": {"project_id": p, "asset_ids": [a["id"] for a in assets]},
        "turns": entries,
    })


def turn(evs, text="First finding", **extra):
    return {"question": "Compare materials", "created_at": "2026-10-26T01:00:00Z", "evidence": evs,
            "answer": {"summary": text, "content": text + " full content", "findings": [
                {"type": "fact", "text": text, "evidence_ids": [evs[0]["id"]]}]}, **extra}


def test_analysis_results_are_stable_distinct_and_reuse_original_record_ids(env):
    store, p, _q, _access = env
    a = asset(store, p)
    ev = evidence(store, p, a)
    saved = session(store, p, [a], [turn([ev]), turn([ev], "Second finding")])
    parsed = store.put(p, "parse", {"asset_ids": [a["id"]], "fields": ["Hook"],
                                     "rows": [{**ev, "fields": {"Hook": "A hook"}}], "version": 3})
    api = views(env)
    result = api.analysis_results(p, a["id"])
    assert len(result["results"]) == 3
    assert len({r["id"] for r in result["results"]}) == 3
    assert result == api.analysis_results(p, a["id"])
    sessions = [r for r in result["results"] if r["kind"] == "session"]
    assert {r["record"]["id"] for r in sessions} == {saved["id"]}
    assert {r["turn_index"] for r in sessions} == {0, 1}
    assert all(len(r["record"]["turns"]) == 1 for r in sessions)
    parsing = next(r for r in result["results"] if r["kind"] == "parse")
    assert parsing["record"]["id"] == parsed["id"] and parsing["version"] == 3
    assert store.get(p, "session", saved["id"])["turns"][0]["answer"]["content"] == "First finding full content"


def test_multi_material_turn_and_parse_are_cropped_before_returning_text(env):
    store, p, _q, _access = env
    a, b = asset(store, p), asset(store, p, "OTHER_PRIVATE")
    ea, eb = evidence(store, p, a), evidence(store, p, b)
    t = turn([ea, eb], "OTHER_PRIVATE summary")
    t["task_spec"] = {"own_asset_ids": [b["id"]], "constraints": "OTHER_PRIVATE task metadata"}
    t["answer"]["findings"] = [
        {"type": "fact", "text": "First supported finding", "evidence_ids": [ea["id"]]},
        {"type": "fact", "text": "OTHER_PRIVATE finding", "evidence_ids": [eb["id"]]},
        {"type": "hypothesis", "text": "OTHER_PRIVATE mixed", "evidence_ids": [ea["id"], eb["id"]]},
    ]
    session(store, p, [a, b], [t, turn([eb], "OTHER_PRIVATE later turn")])
    store.put(p, "parse", {"asset_ids": [a["id"], b["id"]], "fields": ["Hook"],
                           "source_context": {"answer_snapshot": "OTHER_PRIVATE source"},
                           "rows": [{**ea, "fields": {"Hook": "First hook"}},
                                    {**eb, "fields": {"Hook": "OTHER_PRIVATE hook"}}]})
    result = views(env).analysis_results(p, a["id"])
    assert len(result["results"]) == 2
    assert "OTHER_PRIVATE" not in json.dumps(result)
    s = next(r["record"] for r in result["results"] if r["kind"] == "session")
    assert s["scope"]["asset_ids"] == [a["id"]]
    assert s["turns"][0]["answer"]["findings"][0]["text"] == "First supported finding"
    parsed = next(r["record"] for r in result["results"] if r["kind"] == "parse")
    assert parsed["asset_ids"] == [a["id"]] and len(parsed["rows"]) == 1


@pytest.mark.parametrize("change", ["transcript", "media", "manifest_project", "manifest_failed"])
def test_stale_or_unverifiable_evidence_is_not_exposed_as_current(env, change):
    store, p, q, _access = env
    a = asset(store, p)
    ev = evidence(store, p, a)
    session(store, p, [a], [turn([ev])])
    store.put(p, "parse", {"asset_ids": [a["id"]], "fields": [], "rows": [{**ev, "fields": {}}]})
    api = views(env)
    assert len(api.analysis_results(p, a["id"])["results"]) == 2
    if change == "transcript":
        a["metadata"]["subtitles"][0]["text"] = "Changed timed words"
        store.put(p, "asset", a, a["id"])
    elif change == "media":
        store.put(p, "asset", {**a, "sha256": "different-media"}, a["id"])
    else:
        manifest = store.get(p, "media_input_manifest", ev["input_manifest_id"])
        if change == "manifest_project":
            manifest["project_id"] = q
            with store.connection(True) as db:
                db.execute("UPDATE records SET data=? WHERE id=?", (dumps(manifest), manifest["id"]))
        else:
            store.put(p, "media_input_manifest", {**manifest, "execution_state": "failed"}, manifest["id"])
    assert api.analysis_results(p, a["id"])["results"] == []


def test_legacy_spoken_text_is_readable_only_while_its_media_and_timed_snapshot_match(env):
    store, p, _q, _access = env
    a = asset(store, p)
    ev = {"id": a["id"], "asset_id": a["id"], "asset_sha256": a["sha256"], "provenance": "subtitle",
          "transcript": a["metadata"]["transcript"], "subtitles": a["metadata"]["subtitles"]}
    session(store, p, [a], [turn([ev])])
    api = views(env)
    assert len(api.analysis_results(p, a["id"])["results"]) == 1
    a["metadata"]["subtitles"][0]["text"] = "New words"
    store.put(p, "asset", a, a["id"])
    assert api.analysis_results(p, a["id"])["results"] == []


def test_unversioned_legacy_spoken_text_is_not_presented_as_current(env):
    store, p, _q, _access = env
    a = asset(store, p)
    ev = {"id": a["id"], "asset_id": a["id"], "provenance": "subtitle",
          "transcript": a["metadata"]["transcript"], "subtitles": a["metadata"]["subtitles"]}
    session(store, p, [a], [turn([ev])])
    assert views(env).analysis_results(p, a["id"])["results"] == []


def test_cross_project_assets_and_lineage_are_denied(env):
    store, p, q, _access = env
    a, foreign = asset(store, p), asset(store, q, "Other project")
    ev = evidence(store, p, a)
    bad = {**ev, "input_manifest_id": evidence(store, q, foreign)["input_manifest_id"]}
    session(store, p, [a], [turn([bad])])
    api = views(env)
    with pytest.raises(NotFound):
        api.asset(p, foreign["id"])
    with pytest.raises(NotFound):
        api.analysis_results(p, foreign["id"])
    assert api.analysis_results(p, a["id"])["results"] == []


def test_evidence_cannot_expand_the_original_session_scope(env):
    store, p, _q, _access = env
    a, b = asset(store, p), asset(store, p, "Other")
    session(store, p, [b], [turn([evidence(store, p, a)])])
    assert views(env).analysis_results(p, a["id"])["results"] == []


def test_expired_licensed_material_is_hidden_from_all_read_views(env):
    from test_industry import archived_scope, rights

    store, p, _q, access = env
    allowed = {**rights(), "retain_media": True}
    conn, job, row = archived_scope(access.industry, p, allowed=allowed)
    a = asset(store, p, "Licensed", "industry")
    ref = store.put(p, "industry_reference", {"job_id": job["id"], "result_id": row["id"], "asset_id": a["id"]})
    a["metadata"]["industry_reference_ids"] = [ref["id"]]
    store.put(p, "asset", a, a["id"])
    saved = session(store, p, [a], [turn([evidence(store, p, a)])])
    api = views(env)
    assert api.asset(p, a["id"])["id"] == a["id"]
    conn["rights"]["expires_at"] = "2000-01-01T00:00:00Z"
    store.put(p, "industry_connection", conn, conn["id"])
    with pytest.raises(ValueError):
        api.asset(p, a["id"])
    with pytest.raises(ValueError):
        api.analysis_results(p, a["id"])
    assert api.assets(p)["items"] == []
    assert saved["id"] not in {r["id"] for r in api.outcomes(p)["items"]}


def test_asset_pages_filter_before_counting_and_never_include_full_text(env):
    store, p, q, _access = env
    a = asset(store, p, "Alpha")
    b = asset(store, p, "Alpha second", "upload")
    asset(store, p, "Beta")
    asset(store, q, "Alpha foreign")
    api = views(env)
    page = api.assets(p, offset=0, limit=1, query="ALPHA")
    following = api.assets(p, offset=page["next_offset"], limit=1, query="ALPHA")
    assert page["total"] == following["total"] == 2
    assert {page["items"][0]["id"], following["items"][0]["id"]} == {a["id"], b["id"]}
    assert following["next_offset"] is None
    assert api.assets(p, source="upload")["total"] == 1
    assert api.assets(p, offset=100)["items"] == []
    assert "transcript" not in page["items"][0].get("metadata", {})
    assert "parsed_text" not in page["items"][0]


@pytest.mark.parametrize("offset,limit", [(-1, 10), (0, 0), (0, 101), (True, 10), (0, 1.2)])
def test_asset_pagination_rejects_invalid_bounds(env, offset, limit):
    with pytest.raises(ValueError):
        views(env).assets(env[1], offset=offset, limit=limit)


def test_safe_full_asset_removes_nested_credentials_urls_and_embedded_local_paths(env):
    store, p, _q, _access = env
    a = asset(store, p)
    a["metadata"].update({"api_key": "private-key", "poster_path": "/tmp/poster.jpg",
                          "debug": "file /Users/test/private/raw.txt; see https://example.com/a?token=PRIVATE_EMBEDDED",
                          "access_token": "private-token", "accessToken": "PRIVATE_CAMEL"})
    a["source_url"] = "https://user:pass@example.com/video?token=private#secret"
    with store.connection(True) as db:
        db.execute("UPDATE records SET data=? WHERE id=?", (dumps(a), a["id"]))
    safe = views(env).asset(p, a["id"])
    text = json.dumps(safe)
    assert "private-key" not in text and "private-token" not in text and "/Users/" not in text
    assert "poster_path" not in text and "api_key" not in text
    assert "PRIVATE_EMBEDDED" not in text and "PRIVATE_CAMEL" not in text
    assert safe["source_url"] == "https://example.com/video"
    assert safe["metadata"]["transcript"] == "First words"


def test_activity_changes_on_inserts_updates_deletions_and_job_progress_without_content(env):
    store, p, q, _access = env
    api = views(env)
    initial = api.activity(p)
    assert initial["jobs"] == [] and initial == api.activity(p)
    a = asset(store, p)
    inserted = api.activity(p)["revision"]
    assert inserted != initial["revision"]
    asset(store, q, "Unrelated")
    assert api.activity(p)["revision"] == inserted
    store.put(p, "asset", {**a, "title": "Updated"}, a["id"])
    updated = api.activity(p)["revision"]
    assert updated != inserted
    store.delete(p, "asset", a["id"])
    assert api.activity(p)["revision"] != updated
    job = store.create_job(p, "analysis", {"question": "PRIVATE_PAYLOAD"},
                           CostQuote(amount="0", basis="local test"), "local-job")
    queued = api.activity(p)
    assert queued["revision"] != initial["revision"]
    store.transition(p, job["id"], "running")
    running = api.activity(p)
    assert running["revision"] != queued["revision"]
    assert running["jobs"][0]["state"] == "running"
    assert "PRIVATE_PAYLOAD" not in json.dumps(running)
    assert "payload" not in running["jobs"][0] and "result" not in running["jobs"][0]


def test_activity_does_not_need_to_decode_large_or_damaged_job_content(env):
    store, p, _q, _access = env
    job = store.create_job(p, "analysis", {}, CostQuote(amount="0", basis="local test"), "metadata-only")
    with store.connection(True) as db:
        db.execute("UPDATE jobs SET payload=?,result=? WHERE id=?", ("invalid large old payload", "invalid old result", job["id"]))
    result = views(env).activity(p)
    assert result["jobs"][0]["id"] == job["id"]
    assert result["jobs"][0]["state"] == "queued"
    assert "invalid" not in json.dumps(result)


def test_outcome_summaries_reference_canonical_objects_without_copying_body(env):
    store, p, q, _access = env
    saved = []
    for kind in ("session", "parse", "script", "note"):
        row = store.put(p, kind, {"title": kind + " title", "version": 4, "body": "PRIVATE_BODY"})
        saved.append((kind, row["id"]))
    store.put(q, "note", {"title": "Foreign", "body": "OTHER_PROJECT"})
    result = views(env).outcomes(p)
    assert {(r["kind"], r["id"]) for r in result["items"]} == set(saved)
    assert all(r["version"] == 4 for r in result["items"])
    assert "PRIVATE_BODY" not in json.dumps(result) and "OTHER_PROJECT" not in json.dumps(result)


def test_reads_never_enqueue_index_work_or_mutate_source_records(env):
    store, p, _q, _access = env
    a = asset(store, p)
    session(store, p, [a], [turn([evidence(store, p, a)])])
    with store.connection() as db:
        before = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                  for table in ("records", "jobs", "knowledge_outbox", "index_job")}
    api = views(env)
    api.asset(p, a["id"])
    api.assets(p)
    api.analysis_results(p, a["id"])
    api.activity(p)
    api.outcomes(p)
    with store.connection() as db:
        after = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                 for table in before}
    assert after == before


def test_read_views_support_store_backends_without_local_index_tables(env):
    store, p, _q, access = env
    a = asset(store, p)
    with store.connection(True) as db:
        db.execute("DROP TABLE knowledge_chunk")

    class BackendWithoutLocalIndex:
        """Use the shared Store protocol, as the Postgres store does."""
        project = store.project
        get = store.get
        list = store.list
        jobs = store.jobs
        connection = store.connection

    from creative_agent.material_views import MaterialViews

    api = MaterialViews(BackendWithoutLocalIndex(), access)
    assert api.asset(p, a["id"])["id"] == a["id"]
    assert api.assets(p)["items"][0]["id"] == a["id"]
    assert api.activity(p)["revision"]
