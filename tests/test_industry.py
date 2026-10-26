"""Industry tests use real persistence/adapters; only external HTTP is replaced."""

import importlib.util
import json

import pytest

from creative_agent.media import MediaService
from creative_agent.store import NotFound, Store


class Keys:
    def __init__(self):
        self.data = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


def module():
    assert importlib.util.find_spec("creative_agent.industry"), "Industry source service is missing"
    from creative_agent import industry

    return industry


def rights(**changes):
    return dict(
        search=True,
        retain_metadata=True,
        download_media=False,
        retain_media=False,
        model_process=False,
        derivative_create=False,
        export_metadata=False,
        export_media=False,
        expires_at="2099-01-01T00:00:00Z",
        evidence="Customer reviewed licensed source agreement",
        **changes,
    )


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path / "db.sqlite3")
    p = s.create_project("P", "1", "USD")["id"]
    q = s.create_project("Q", "1", "USD")["id"]
    vault = Keys()
    media = MediaService(s, tmp_path / "media")
    svc = module().Industry(s, media, vault)
    return svc, s, p, q, vault


def connection(svc, p, **changes):
    body = {
        "provider": "datax",
        "label": "Licensed ads",
        "api_key": "test-account-key",
        "api_authorized": True,
        "authorization_evidence": "Paid API subscription reviewed",
        "rights": rights(),
    }
    if changes.get("provider", "datax") == "datax":
        body["operation"] = "tiktok.ads.search"
    body.update(changes)
    return svc.connect(p, module().ConnectionInput(**body))


def plan(cid, **changes):
    body = {
        "connection_id": cid,
        "query": "洗面奶",
        "category": "",
        "start_date": "2026-10-01",
        "end_date": "2026-10-19",
        "target_count": 2,
        "max_calls": 2,
        "quote": "0.004",
        "currency": "USD",
        "allowed_actions": ["search", "retain_metadata"],
        "idempotency_key": "search-one",
    }
    body.update(changes)
    return module().SearchPlanInput(**body)


def datax_response(items, *, more=False, cursor=None, amount="0.002"):
    return {
        "code": "ok",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "data": {"items": items},
        "warnings": [],
        "page": {"next_cursor": cursor, "cursor_expires_at": None, "has_more": more, "returned": len(items)},
        "meta": {
            "capability": "tiktok.ads.search",
            "result_status": "complete",
            "fetched_at": "2026-10-19T00:00:00Z",
            "cached": False,
            "replayed": False,
            "unavailable": {},
            "billing": {"amount": amount, "currency": "USD", "balance_after": None},
        },
    }


def ad(aid="ad-1", **changes):
    item = {
        "id": aid,
        "url": "https://library.tiktok.com/ads/" + aid,
        "title": "洗面奶",
        "text": None,
        "format": "video",
        "advertiser": {"id": "shop-1", "name": "护肤品牌", "url": None, "avatar_url": None},
        "images": None,
        "videos": None,
        "first_shown_at": "2026-10-05T00:00:00Z",
        "last_shown_at": "2026-10-18T00:00:00Z",
        "approximate": None,
        "ext": {
            "likes": 0,
            "comments": None,
            "shares": 12,
            "duration": 15,
            "cover_url": None,
            "objective": None,
            "regions": ["FR"],
            "audience_range": "10K–20K",
            "category": "Beauty",
        },
    }
    item.update(changes)
    return item


def archived_scope(svc, p, *, allowed=None, **ad_changes):
    """Historical data fixture: no new provider submission, transport or license inference."""
    from creative_agent.contracts import CostQuote

    allowed = allowed or rights()
    c = svc.store.put(
        p,
        "industry_connection",
        {
            "provider": "datax",
            "platform": "tiktok",
            "operation": "tiktok.ads.search",
            "rights": allowed,
            "label": "Archived source",
            "has_key": False,
        },
    )
    actions = [k for k, v in allowed.items() if v is True]
    j = svc.store.create_job(
        p,
        "industry_search",
        {"connection_snapshot": c, "plan": plan(c["id"], allowed_actions=actions).model_dump(mode="json")},
        CostQuote(amount="0", currency=svc.store.project(p)["currency"], basis="historical fixture"),
        "archive:" + c["id"],
    )
    svc.store.transition(p, j["id"], "running")
    svc.store.finish(p, j["id"], result={"stop_reason": "archived"}, actual_cost="0")
    normalized = svc._normalize(c, ad(**ad_changes), p, j["id"], j["payload"]["plan"])
    row = svc.store.put(p, "industry_result", normalized)
    return c, j, row


@pytest.mark.parametrize(
    "provider,operation",
    [
        ("datax", "tiktok.ads.search"),
        ("datax", "douyin.videos.search"),
        ("tikhub", "douyin.video_search_v1"),
        ("cmm", "video_commerce_video_library_custom_search_top_selling_video"),
    ],
)
def test_retired_connection_submission_rejected_without_vault_or_paid_calls(env, provider, operation):
    svc, s, p, _q, vault = env
    with pytest.raises(ValueError, match="CMM"):
        connection(svc, p, provider=provider, operation=operation)
    assert not vault.data and s.list(p, "industry_connection") == []


def test_historical_task_new_search_replay_and_selection_are_rejected(env):
    svc, s, p, q, _vault = env
    conn, j, row = archived_scope(svc, p)
    assert svc.results(p, j["id"])[0]["id"] == row["id"]
    assert svc.overview(p)["connections"][0]["search_enabled"] is False
    with pytest.raises(ValueError, match="CMM"):
        svc.create_search(p, plan(conn["id"]))
    with pytest.raises(ValueError, match="CMM"):
        svc.run(p, j["id"])
    with pytest.raises(ValueError, match="CMM"):
        svc.select(p, j["id"], module().SelectionInput(result_ids=[row["id"]], reason="retired"))
    with pytest.raises(NotFound):
        svc.results(q, j["id"])
    assert len(s.jobs(p)) == 1 and s.list(p, "industry_reference") == []


def test_archived_datax_missing_metrics_ranges_and_identity_are_preserved(env):
    svc, _s, p, _q, _v = env
    _c, _j, row = archived_scope(svc, p)
    assert row["metrics"]["likes"]["value"] == "0"
    assert row["metrics"]["comments"]["kind"] == "missing"
    assert row["metrics"]["sales"]["kind"] == "missing"
    assert row["metrics"]["audience"]["kind"] == "range"
    assert row["metrics"]["audience"]["lower"] == "10000" and row["metrics"]["audience"]["upper"] == "20000"
    assert row["platform_id"] == "ad-1" and row["platform"] == "tiktok"


def test_archived_source_missing_dates_never_enter_date_scope(env):
    svc, _s, p, _q, _v = env
    _c, j, row = archived_scope(svc, p, first_shown_at=None)
    assert row["published_at"] is None
    assert not svc._in_scope(row, j["payload"]["plan"])


def test_archived_metadata_revocation_hides_results_and_cached_reference(env):
    svc, s, p, _q, _v = env
    c, j, row = archived_scope(svc, p)
    s.put(p, "industry_reference", {"job_id": j["id"], "result_id": row["id"]})
    svc.update_rights(p, c["id"], module().SourceRights(**{**rights(), "retain_metadata": False}))
    assert svc.search(p, j["id"])["content_restricted"]
    assert svc.overview(p)["references"][0]["content_restricted"]
    with pytest.raises(ValueError):
        svc.results(p, j["id"])
    with pytest.raises(ValueError):
        svc.validate_export(p)


def test_raw_redaction_removes_echoed_secrets_from_keys_values_and_headers():
    raw = module().clean_raw(
        {
            "title": "active-key suffix",
            "active-key-field": "leak",
            "headers": {"authorization": "active-key"},
            "nested": {"api_key": "x", "text": "active-key"},
        },
        ["active-key"],
    )
    assert "active-key" not in json.dumps(raw)
    assert raw["title"] == "[REDACTED] suffix" and raw["nested"]["text"] == "[REDACTED]"


@pytest.mark.parametrize(
    "metric_kind,raw,want",
    [("range", "10K–20K", "range"), ("exact", None, "missing"), ("index", "8", "index")],
)
def test_history_metric_semantics_not_exact_sales(metric_kind, raw, want):
    result = module().metric(raw, kind=metric_kind)
    assert result["kind"] == want
    if want != "index":
        assert result["value"] is None
