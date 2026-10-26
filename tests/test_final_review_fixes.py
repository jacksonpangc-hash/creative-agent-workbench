"""Regressions from the final enterprise integration review."""

# ruff: noqa: F811 - imported pytest fixtures are injected by parameter name
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from test_agent import Answer, setup  # noqa: F401 - fixtures
from test_api import H, client
from test_industry import archived_scope, env, rights  # noqa: F401 - fixtures
from test_industry_access import licensed_app  # noqa: F401 - fixtures

from creative_agent.agent import AnalysisAgent
from creative_agent.contracts import AnalysisScope
from creative_agent.industry import SelectionInput
from creative_agent.licensing_access import LicensedAccess


def test_parse_lineage_restricts_owned_asset_and_cached_semantic_evidence(licensed_app):
    c, p, _asset, owned, _session, parsed, *_rest, revoke = licensed_app
    s = c.app.state.store
    owned["parsed_record_id"] = parsed["id"]
    s.put(p, "asset", owned, owned["id"])
    revoke()
    access = LicensedAccess(s, c.app.state.industry)
    for value, kind in [(owned, "asset"), ({"parse_id": parsed["id"]}, None)]:
        with pytest.raises(ValueError, match="授权"):
            access.check(p, value, kind, model=True)
    assert c.get(f"/api/projects/{p}/assets/{owned['id']}/file", headers=H).status_code == 400


def test_observation_cache_hashes_every_metadata_field_sent_to_model(setup):
    s, p, media, asset = setup
    answer = Answer()
    answer.binding = SimpleNamespace(provider="test", model_id="vision-v1", base_url=None, protocol="openai")
    agent = AnalysisAgent(s, media)
    scope = AnalysisScope(project_id=p, asset_ids=[asset["id"]])
    agent.observe(scope, [asset], answer)
    changed = s.get(p, "asset", asset["id"])
    changed["metadata"]["rights_note"] = "new source permission and restrictions"
    agent.observe(scope, [changed], answer)
    assert len(answer.questions) == 2
    assert "new source permission" in answer.questions[-1]


def test_spoken_evidence_preserves_actual_asr_method_and_review_status():
    result = AnalysisAgent.spoken_evidence(
        [
            {
                "id": "a",
                "title": "video",
                "metadata": {
                    "transcript": "自动识别",
                    "transcript_source": "user_import",
                    "duration_ms":1000,
                    "subtitles": [{"start_ms":0,"end_ms":1000,"text":"自动识别"}],
                    "transcription": {
                        "method": "faster-whisper-local",
                        "execution_state":"succeeded", "quality_state":"usable",
                        "model": "local-zh",
                        "language": "zh",
                        "reviewed": False,
                    },
                },
            }
        ]
    )[0]
    assert result["source"] == "faster-whisper-local"
    assert result["provenance"] == "local_asr"
    assert result["reviewed"] is False and result["transcription"]["model"] == "local-zh"


def test_semantic_search_without_explicit_binding_selects_indexed_compatible_model(tmp_path):
    with client(tmp_path) as c:
        s = c.app.state.store
        p = s.create_project("p", "10")["id"]
        model = c.post(
            f"/api/projects/{p}/models",
            headers=H,
            json={
                "provider": "zhipu",
                "label": "embedding",
                "model_id": "embedding-3",
                "role": "embedding",
                "api_key": "test-only",
            },
        ).json()
        s.put(
            p,
            "asset",
            {
                "title": "indexed",
                "source_kind": "own",
                "media_type": "image",
                "metadata": {},
                "embedding": [1, 0],
                "embedding_model": "embedding-3",
                "embedding_provider": "zhipu",
            },
        )
        response = c.post(
            f"/api/projects/{p}/actions",
            headers=H,
            json={
                "kind": "search",
                "question": "商品",
                "quote": "1",
                "idempotency_key": "auto-search",
                "scope": {"project_id": p, "discovery": True},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["payload"]["binding_id"] == model["id"]
        assert s.budget(p)["reserved"] == "1"


def test_retired_metadata_reference_cannot_upgrade_or_mutate_source(env):
    svc, s, p, _q, _v = env
    _c, j, row = archived_scope(svc, p)
    ref = s.put(
        p, "industry_reference", {"job_id": j["id"], "result_id": row["id"], "status": "metadata_only"}
    )
    before = s.export_project(p)["records"]
    for attempt in ("first", "explicit-retry"):
        with pytest.raises(ValueError, match="CMM"):
            svc.select(
                p,
                j["id"],
                SelectionInput(
                    result_ids=[row["id"]], reason="retired import", import_media=True, attempt_key=attempt
                ),
            )
    assert s.export_project(p)["records"] == before
    assert s.get(p, "industry_reference", ref["id"])["status"] == "metadata_only"


def test_concurrent_retired_imports_never_download(env):
    svc, s, p, _q, _v = env
    _c, j, row = archived_scope(svc, p)
    calls = []

    def attempt(_):
        with pytest.raises(ValueError, match="CMM"):
            svc.select(
                p,
                j["id"],
                SelectionInput(result_ids=[row["id"]], reason="retired", import_media=True),
                media_client=httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, range(2)))
    assert calls == [] and s.list(p, "asset") == []


def test_import_task_endpoint_rejects_all_attempt_keys_without_creating_job(tmp_path):
    from creative_agent.contracts import CostQuote

    with client(tmp_path) as c:
        s = c.app.state.store
        p = s.create_project("p", "10")["id"]
        source = s.create_job(p, "industry_search", {}, CostQuote(amount="0", basis="source"), "source")
        endpoint = f"/api/projects/{p}/industry/searches/{source['id']}/select"
        for attempt in ("first", "confirmed-second"):
            response = c.post(
                endpoint,
                headers=H,
                json={
                    "result_ids": ["result"],
                    "reason": "retired",
                    "import_media": True,
                    "attempt_key": attempt,
                },
            )
            assert response.status_code == 400
        assert len(s.jobs(p)) == 1


def test_state_includes_migrated_archived_jobs_without_replay(tmp_path):
    from creative_agent.contracts import CostQuote

    with client(tmp_path) as c:
        s = c.app.state.store
        p = s.create_project("p", "10")["id"]
        live = s.create_job(p, "analysis", {}, CostQuote(amount="0", basis="local"), "live")
        historical = s.put(
            p,
            "migration_job",
            {
                "kind": "generation",
                "state": "archived",
                "original_state": "submitting",
                "replay_allowed": False,
                "payload": {},
                "quote": {"amount": "1", "currency": "CNY"},
                "actual_cost": None,
            },
        )
        jobs = c.get(f"/api/projects/{p}/state", headers=H).json()["jobs"]
        assert {job["id"] for job in jobs} == {live["id"], historical["id"]}
        assert next(job for job in jobs if job["id"] == historical["id"])["replay_allowed"] is False


@pytest.mark.parametrize("outside_index", [False, True])
def test_automatic_search_skips_older_unindexed_binding_and_explicit_choice_still_errors(
    tmp_path, outside_index
):
    with client(tmp_path) as c:
        s = c.app.state.store
        p = s.create_project("p", "10")["id"]
        models = []
        for provider, model_id in [("openai", "text-embedding-3-small"), ("zhipu", "embedding-3")]:
            models.append(
                c.post(
                    f"/api/projects/{p}/models",
                    headers=H,
                    json={
                        "provider": provider,
                        "label": provider,
                        "model_id": model_id,
                        "role": "embedding",
                        "api_key": "test-only",
                    },
                ).json()
            )
        indexed = s.put(
            p,
            "asset",
            {
                "title": "indexed with B",
                "source_kind": "own",
                "media_type": "image",
                "metadata": {},
                "embedding": [1, 0],
                "embedding_model": "embedding-3",
                "embedding_provider": "zhipu",
            },
        )
        # A has an index only outside the selected scope; it must not qualify for this search.
        if outside_index:
            s.put(
                p,
                "asset",
                {
                    "title": "outside scope A",
                    "source_kind": "own",
                    "media_type": "image",
                    "metadata": {},
                    "embedding": [1, 0],
                    "embedding_model": "text-embedding-3-small",
                    "embedding_provider": "openai",
                },
            )
        body = {
            "kind": "search",
            "question": "商品",
            "quote": "1",
            "idempotency_key": "scope-index-auto",
            "scope": {"project_id": p, "asset_ids": [indexed["id"]]},
        }
        response = c.post(f"/api/projects/{p}/actions", headers=H, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["payload"]["binding_id"] == models[1]["id"]
        explicit = c.post(
            f"/api/projects/{p}/actions",
            headers=H,
            json={**body, "binding_id": models[0]["id"], "idempotency_key": "scope-index-explicit"},
        )
        assert explicit.status_code == 400 and "索引" in explicit.text
        assert len(s.jobs(p)) == 1
