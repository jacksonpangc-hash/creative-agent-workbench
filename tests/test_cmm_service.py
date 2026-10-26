"""Offline CMM contracts, not commercial integration validation."""

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_industry import Keys, rights

from creative_agent.store import Conflict, NotFound, Store


def service():
    assert importlib.util.find_spec("creative_agent.cmm"), "CMM-only service is missing"
    from creative_agent.cmm import CMMService

    return CMMService


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path / "db.sqlite3")
    p = s.create_project("P", "0", "CNY")["id"]
    q = s.create_project("Q", "0", "CNY")["id"]
    v = Keys()
    svc = service()(s, v)
    # Explicit synthetic tariff for offline transport tests; not a vendor claim.
    for project in (p, q):
        svc.configure_tariff(project, operation="permission_check", max_points_per_call="0",
                             evidence="offline-test-only", verified=True)
    return svc, s, p, q, v


def test_connection_singleton_vault_only_unverified(env):
    svc, s, p, q, v = env
    c = svc.connect(p, api_key="private-key")
    assert c["status"] == "unverified" and c["has_key"]
    assert not any(c["capabilities"].values())
    assert "private-key" not in json.dumps(s.export_project(p))
    assert "secret_ref" not in c
    again = svc.connect(p, api_key="rotated-key")
    assert again["id"] == c["id"]
    assert len(s.list(p, "cmm_connection")) == 1
    assert len(v.data) == 1
    assert svc.overview(q)["connection"] is None


def test_unpriced_permission_check_never_reaches_provider(tmp_path):
    store = Store(tmp_path / "unpriced.sqlite")
    p = store.create_project("test")["id"]
    svc = service()(store, Keys())
    svc.connect(p, api_key="test-only-key")
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(
        200, json={"code": 0, "data": {"group_id": 9, "version": "2026-10-14", "rights": {}}}
    ))) as client, pytest.raises(ValueError, match="费率|费用"):
        svc.permissions(p, "检查权益", client=client)
    assert calls == []


def transport(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))


def test_permissions_exact_rights_unknown_denied_and_no_false_live_claim(env):
    svc, _s, p, _q, _v = env
    svc.connect(p, api_key="private-key")
    with transport(
        {
            "code": 0,
            "data": {
                "group_id": 9,
                "version": "2026-10-14",
                "rights": {"extract_video_copywriting": True, "video_storyboard": "yes", "video": True},
            },
        }
    ) as client:
        c = svc.permissions(p, "获取视频文案", client=client)
    assert c["capabilities"]["extract_video_copywriting"] is True
    assert c["capabilities"]["video_storyboard"] is False
    assert c["status"] == "permissions_checked" and not c["live_verified"]
    assert "private-key" not in json.dumps(c)
    with (
        transport({"code": 0, "data": {"rights": ["video"]}}) as client,
        pytest.raises(ValueError, match="schema"),
    ):
        svc.permissions(p, "检查", client=client)
    assert not any(svc.overview(p)["connection"]["capabilities"].values())


def test_connector_rejects_business_error_without_echoing_key():
    from creative_agent.connectors import CMMConnector
    from creative_agent.providers import ProviderError

    with (
        transport({"code": 1001, "msg": "private-key rejected"}) as client,
        pytest.raises(ProviderError) as err,
    ):
        CMMConnector("private-key", client=client).permissions("检查")
    assert "private-key" not in str(err.value)


def test_preflight_correct_documented_parameters_and_share_links(env):
    svc, _s, p, _q, _v = env
    svc.connect(p, api_key="private-key")
    r = svc.search_query(
        kind="qianchuan", keyword="洗面奶", category="12", start_date="2026-10-01", end_date="2026-10-18"
    )
    assert r == {
        "operation": "video_library_custom_search",
        "query": {
            "keyword": "洗面奶",
            "start_date": "2026-10-01",
            "end_date": "2026-10-18",
            "category_id": "12",
            "page": 1,
        },
    }
    r = svc.preflight(p, "extract_video_copywriting", {"video_url": "https://v.douyin.com/abc/"})
    assert r["query"]["video_url"] == "https://v.douyin.com/abc/"
    assert r["will_call_provider"] is False
    for query in ({"video_id": "123"}, {"material_id": "123"}, {"video_url": "http://127.0.0.1/a"}):
        with pytest.raises(ValueError):
            svc.preflight(p, "extract_video_copywriting", query)
    with pytest.raises(ValueError):
        svc.search_query(kind="popular", keyword="x", start_date="2026-10-18", end_date="2026-10-01")


def ready(svc, p, operations=("extract_video_copywriting",)):
    svc.connect(p, api_key="private-key")
    with transport(
        {
            "code": 0,
            "data": {"group_id": 9, "version": "2026-10-14", "rights": {op: True for op in operations}},
        }
    ) as client:
        svc.permissions(p, "测试契约", client=client)
    svc.update_rights(p, rights())


def job(svc, p, **changes):
    body = {
        "operation": "extract_video_copywriting",
        "query": {"video_url": "https://v.douyin.com/abc/"},
        "max_calls": 2,
        "max_points": "8",
        "idempotency_key": "one",
    }
    body.update(changes)
    return svc.create_job(p, **body)


def tariff(svc, p, op="extract_video_copywriting", cost="4"):
    svc.configure_tariff(
        p, operation=op, max_points_per_call=cost, evidence="用户核对正式账户收费表", verified=True
    )


def test_unknown_tariff_and_source_rights_stop_before_paid_call(env):
    svc, s, p, q, _v = env
    ready(svc, p)
    with pytest.raises(ValueError, match="积分"):
        job(svc, p)
    tariff(svc, p)
    svc.update_rights(p, {**rights(), "model_process": False})
    with pytest.raises(ValueError, match="授权"):
        job(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    j = job(svc, p)
    assert j["state"] == "queued" and j["max_points"] == "8"
    assert j["reserved_points"] == "8" and j["actual_points"] is None
    assert s.project(p)["budget"] == "0"  # Points never become money charges.
    with pytest.raises(NotFound):
        svc.job(q, j["id"])


def test_cumulative_point_limit_reserves_atomically_across_jobs(env):
    svc, s, p, q, _v = env
    ready(svc, p); tariff(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    svc.configure_budget(p, limit_points="10")
    def attempt(i):
        try:
            return job(svc, p, idempotency_key=f"parallel-{i}", max_points="8")["id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        ids = list(workers.map(attempt, range(2)))
    assert len([x for x in ids if x]) == 1
    assert svc.overview(p)["usage"]["remaining_points"] == "2"
    assert svc.overview(q)["usage"]["limit_points"] is None
    first = next(x for x in ids if x)
    svc.cancel(p, first)  # Nothing submitted: only this reservation may be released.
    assert svc.overview(p)["usage"]["remaining_points"] == "10"
    assert job(svc, p, idempotency_key="after-cancel", max_points="8")["state"] == "queued"


def test_imported_real_call_reservations_do_not_disappear_on_restart_or_key_rotation(env):
    svc, s, p, _q, v = env
    ready(svc, p); tariff(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    # Receipt fixture models prior calls, not a verified vendor bill.
    s.put(p, "cmm_call_receipt", {"reserved_points": "30", "calls_attempted": 3, "actual_points": None})
    svc.configure_budget(p, limit_points="30")
    restarted = service()(s, v)
    assert restarted.overview(p)["usage"]["reserved_points"] == "30"
    assert restarted.overview(p)["usage"]["remaining_points"] == "0"
    with pytest.raises(ValueError, match="累计积分"):
        job(restarted, p, max_points="4")
    with pytest.raises(ValueError, match="已预留"):
        restarted.configure_budget(p, limit_points="29")
    restarted.connect(p, api_key="replacement-test-key")
    assert restarted.overview(p)["usage"]["remaining_points"] == "0"
    assert restarted.overview(p)["usage"]["actual_points"] is None


def test_point_budget_api_is_project_local_and_does_not_contact_supplier(tmp_path):
    from fastapi.testclient import TestClient
    from creative_agent.api import create_app
    app = create_app(tmp_path, token='local-test', run_jobs=False)
    p = app.state.store.create_project('P')['id']
    q = app.state.store.create_project('Q')['id']
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        endpoint = f'/api/projects/{p}/cmm/budget'
        assert client.put(endpoint, json={'limit_points':'30'}).status_code == 401
        headers = {'Authorization':'Bearer local-test'}
        saved = client.put(endpoint, headers=headers, json={'limit_points':'30'})
        assert saved.status_code == 200
        assert saved.json()['limit_points'] == '30'
        assert client.get(f'/api/projects/{q}/cmm', headers=headers).json()['usage']['limit_points'] is None
        assert client.put(endpoint, headers=headers, json={'limit_points':'-1'}).status_code == 422


def test_verified_detail_transcript_selects_into_analysis_without_visual_observation(env):
    from creative_agent.agent import AnalysisAgent
    from creative_agent.contracts import AnalysisScope
    svc, _s, p, _q, _v = env
    ready(svc, p, operations=('video_detail_info',))
    svc.update_rights(p, {**rights(), 'model_process': True})
    pending = job(svc, p, operation='video_detail_info', query={'aweme_id':'supplier-video'},
                  max_calls=1, max_points='10')
    payload = {'code':0,'data':{'data':{
        '视频信息':{'视频id':'supplier-video','视频标题':'详情测试','发布时间':'2026-10-01 10:00:00',
                    '视频时长（毫秒）':12000},
        '达人信息':{}, '视频脚本文案':'先展示包装，再说清食用场景。'}}}
    with transport(payload) as client:
        completed = svc.run(p, pending['id'], client=client)
    assert completed['state'] == 'succeeded'
    picked = svc.select(p, pending['id'], [completed['results'][0]['id']])
    scope = AnalysisScope(project_id=p, reference_ids=[picked[0]['id']])
    evidence = svc.validated_evidence(p, scope.reference_ids)
    accepted = AnalysisAgent.external_evidence(scope, evidence)
    assert accepted[0]['text'] == '先展示包装，再说清食用场景。'
    assert accepted[0]['content_basis'] == 'supplier_transcript'
    assert accepted[0].get('media_url') is None
    assert not accepted[0].get('observed_timestamps_ms')
    invalid = {**accepted[0], 'content_basis':'metadata'}
    with pytest.raises(ValueError, match='元数据'):
        AnalysisAgent.external_evidence(scope, [invalid])


def test_copywriting_evidence_idempotency_scope_rights_and_restart(env):
    svc, s, p, q, v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)
    assert job(svc, p)["id"] == j["id"]
    with pytest.raises(Conflict):
        job(svc, p, query={"video_url": "https://v.douyin.com/other/"})
    requests = []

    def reply(r):
        requests.append(json.loads(r.content))
        return httpx.Response(200, json={"code": 0, "data": "真实口播文本 private-key"})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        result = svc.run(p, j["id"], client=client)
    assert result["state"] == "succeeded" and result["calls_attempted"] == 1
    assert result["reserved_points"] == "4" and result["actual_points"] is None
    assert result["result"]["text"] == "真实口播文本 [REDACTED]"
    assert requests == [
        {"api": "extract_video_copywriting", "query": {"video_url": "https://v.douyin.com/abc/"}}
    ]
    rid = result["result"]["results"][0]["id"]
    refs = svc.select(p, j["id"], [rid])
    assert svc.select(p, j["id"], [rid])[0]["id"] == refs[0]["id"]
    restarted = service()(s, v)
    evidence = restarted.validated_evidence(p, [refs[0]["id"]])
    assert evidence[0]["source_kind"] == "industry" and evidence[0]["content_basis"] == "transcript"
    assert "asset_id" not in evidence[0] and evidence[0]["text"] == "真实口播文本 [REDACTED]"
    assert not s.list(p, "asset")
    with pytest.raises(NotFound):
        restarted.validated_evidence(q, [refs[0]["id"]])
    svc.update_rights(p, {**rights(), "model_process": False})
    with pytest.raises(ValueError, match="授权"):
        restarted.validated_evidence(p, [refs[0]["id"]])


def test_uncertain_submission_not_retried_or_released_and_atomic_claim(env):
    svc, _s, p, _q, _v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p, max_calls=1, max_points="4")
    count = []

    def error(r):
        count.append(1)
        raise httpx.ReadTimeout("private-key")

    with (
        httpx.Client(transport=httpx.MockTransport(error)) as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        outcomes = list(pool.map(lambda _: svc.run(p, j["id"], client=client), range(2)))
    assert len(count) == 1
    assert svc.job(p, j["id"])["state"] == "needs_attention"
    assert svc.job(p, j["id"])["reserved_points"] == "4"
    assert "private-key" not in json.dumps(outcomes)
    with transport({"code": 0, "data": "must not retry"}) as client:
        assert svc.run(p, j["id"], client=client)["state"] == "needs_attention"
    assert svc.cancel(p, j["id"])["reserved_points"] == "4"


def test_unknown_qianchuan_schema_blocks_before_reservation_or_paid_request(env):
    svc, s, p, _q, _v = env
    op = "video_library_custom_search"
    ready(svc, p, (op,))
    query = {"keyword": "洗面奶", "start_date": "2026-10-01", "end_date": "2026-10-18"}
    preview = svc.preflight(p, op, query)
    assert preview["response_schema_ready"] is False
    assert "首次账号返回格式验证" in preview["disabled_reason"]
    with pytest.raises(ValueError, match="首次账号返回格式验证"):
        job(svc, p, operation=op, query=query, max_calls=1, max_points="10")
    assert s.list(p, "cmm_job") == []


def test_verified_search_preserves_rows_limit_and_metadata_source_on_reopen(env):
    from copy import deepcopy
    from test_cmm_live_schema import response
    from creative_agent.cmm_schema import SEARCH

    svc, store, p, q, vault = env
    operation = SEARCH["commerce"]
    ready(svc, p, (operation,))
    payload = response()
    other = deepcopy(payload["data"]["data"]["list"][0])
    other["视频信息"]["视频ID"] = "other-supplier-id"
    payload["data"]["data"]["list"].append(other)
    created = job(svc, p, operation=operation,
                  query={"create_start_time": "2026-10-01", "create_end_time": "2026-10-18"},
                  max_points="10", max_calls=1, target_count=1)
    with transport(payload) as client:
        result = svc.run(p, created["id"], client=client)
    assert result["state"] == "succeeded"
    assert len(result["results"]) == 1 and result["result"]["received_count"] == 2
    assert result["results"][0]["content_basis"] == "metadata"
    assert result["results"][0]["media_url"] is None
    saved = service()(store, vault).job(p, created["id"])
    assert saved["results"] == result["results"]
    assert svc.select(p, created["id"], [saved["results"][0]["id"]])[0]["metrics"]["sales"]["raw_value"] == "1000~2500"
    with pytest.raises(NotFound):
        svc.job(q, created["id"])


def test_async_task_persist_poll_without_resubmit_and_local_cancel(env):
    svc, s, p, _q, v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "3")
    tariff(svc, p, "video_understanding_task_info", "1")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/abc/"]},
        max_calls=3,
        max_points="5",
    )
    with transport({"code": 0, "data": {"data": {"task_id": "task-1", "status": "pending"}}}) as client:
        result = svc.run(p, j["id"], client=client)
    assert result["state"] == "waiting" and result["provider_task_id"] == "task-1"
    svc = service()(s, v)
    calls = []

    def reply(r):
        calls.append(json.loads(r.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"data": {"task_id": "task-1", "status": "succeeded", "result": "# 画面分析"}},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        result = svc.poll(p, j["id"], client=client)
    assert calls == [{"api": "video_understanding_task_info", "query": {"task_id": "task-1"}}]
    assert result["state"] == "succeeded" and result["calls_attempted"] == 2
    assert result["reserved_points"] == "4"
    assert result["result"]["results"][0]["content_basis"] == "visual_analysis"
    assert result["result"]["results"][0]["text"] == "# 画面分析"


def test_cmm_api_offline_end_to_end_routes_and_project_boundaries(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from creative_agent.industry_api import register_industry

    s = Store(tmp_path / "db.sqlite3")
    p = s.create_project("P", "0")["id"]
    q = s.create_project("Q", "0")["id"]
    v = Keys()
    app = FastAPI()
    register_industry(app, s, None, v, run_jobs=False)
    with TestClient(app) as c:
        assert c.put(f"/api/projects/{p}/cmm/connection", json={"api_key": "private-key"}).status_code == 200
        assert c.get(f"/api/projects/{q}/cmm").json()["connection"] is None
        svc = app.state.cmm
        svc.configure_tariff(p, operation="permission_check", max_points_per_call="0",
                             evidence="offline-test-only", verified=True)
        with transport(
            {
                "code": 0,
                "data": {
                    "group_id": 9,
                    "version": "2026-10-14",
                    "rights": {"extract_video_copywriting": True},
                },
            }
        ) as http:
            svc.permissions(p, "test", client=http)
        assert (
            c.patch(
                f"/api/projects/{p}/cmm/connection/rights", json={**rights(), "model_process": True}
            ).status_code
            == 200
        )
        assert (
            c.put(
                f"/api/projects/{p}/cmm/tariffs/extract_video_copywriting",
                json={"max_points_per_call": "4", "evidence": "正式收费表", "verified": True},
            ).status_code
            == 200
        )
        r = c.post(
            f"/api/projects/{p}/cmm/jobs",
            json={
                "operation": "extract_video_copywriting",
                "query": {"video_url": "https://v.douyin.com/abc/"},
                "max_calls": 1,
                "max_points": "4",
                "idempotency_key": "api",
            },
        )
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        assert c.get(f"/api/projects/{q}/cmm/jobs/{jid}").status_code == 404
        with transport({"code": 0, "data": "口播证据"}) as http:
            svc.run(p, jid, client=http)
        result = c.get(f"/api/projects/{p}/cmm/jobs/{jid}").json()
        rid = result["result"]["results"][0]["id"]
        selected = c.post(f"/api/projects/{p}/cmm/jobs/{jid}/select", json={"result_ids": [rid]}).json()
        assert c.get(f"/api/projects/{p}/cmm/references").json()[0]["id"] == selected[0]["id"]
        assert c.get(f"/api/projects/{q}/cmm/references").json() == []


def test_all_legacy_new_submissions_disabled_history_readable(env):
    from test_industry import plan

    from creative_agent.industry import ConnectionInput, Industry

    _svc, s, p, _q, v = env
    legacy = Industry(s, None, v)
    for provider, operation in [
        ("datax", "tiktok.ads.search"),
        ("tikhub", "douyin.video_search_v1"),
        ("cmm", "video_commerce_video_library_custom_search_top_selling_video"),
    ]:
        body = ConnectionInput(
            provider=provider,
            label="旧来源",
            api_key="private-key",
            api_authorized=True,
            authorization_evidence="原授权",
            rights=rights(),
            operation=operation,
        )
        with pytest.raises(ValueError, match="蝉妈妈|CMM"):
            legacy.connect(p, body)
    archived = s.put(
        p,
        "industry_connection",
        {
            "provider": "datax",
            "operation": "tiktok.ads.search",
            "platform": "tiktok",
            "label": "历史数据",
            "rights": rights(),
            "has_key": False,
            "api_authorized": True,
        },
    )
    assert legacy.overview(p)["connections"][0]["label"] == "历史数据"
    assert legacy.overview(p)["connections"][0]["search_enabled"] is False
    with pytest.raises(ValueError, match="蝉妈妈|CMM"):
        legacy.create_search(p, plan(archived["id"]))


def test_storyboard_preserves_real_scene_timing_and_never_claims_visual_observation(env):
    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_storyboard",))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_storyboard", "3")
    j = job(svc, p, operation="video_storyboard", query={"video_id": "cmm-id"}, max_calls=1, max_points="3")
    payload = {
        "code": 0,
        "data": {
            "video_title": "镜头",
            "video_url": "https://v.douyin.com/a/",
            "duration": 4,
            "result": [
                {"st": 0, "ed": 4, "clip_video_url": "https://example.com/clip.mp4", "voice": "开场台词"}
            ],
        },
    }
    with transport(payload) as client:
        r = svc.run(p, j["id"], client=client)
    assert r["state"] == "succeeded"
    item = r["results"][0]
    assert item["content_basis"] == "storyboard_transcript" and item["scenes"][0]["st"] == 0
    assert "visual_observations" not in item and item["text"] == "[0–4] 开场台词"


def test_async_submission_task_id_without_status_is_recoverable(env):
    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "1")
    tariff(svc, p, "video_understanding_task_info", "0")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/a/"]},
        max_calls=2,
        max_points="1",
    )
    with transport({"code": 0, "data": {"task_id": "async-id"}}) as client:
        r = svc.run(p, j["id"], client=client)
    assert r["state"] == "waiting" and r["provider_task_id"] == "async-id"


def test_async_external_guard_is_awaited_before_any_network(env):
    svc, _s, p, _q, _v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)
    reached = []

    async def deny():
        reached.append("guard")
        raise ValueError("policy denied")

    svc.before_request = deny
    with transport({"code": 0, "data": "should not call"}) as client:
        r = svc.run(p, j["id"], client=client)
    assert reached == ["guard"] and r["calls_attempted"] == 0 and r["state"] == "failed"


def test_selected_reference_content_preflight_uses_its_own_url_and_rights(env):
    svc, _s, p, q, _v = env
    ready(svc, p, ("extract_video_copywriting", "video_storyboard"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)
    with transport({"code": 0, "data": "文本"}) as client:
        r = svc.run(p, j["id"], client=client)
    ref = svc.select(p, j["id"], [r["results"][0]["id"]])[0]
    preview = svc.reference_content(p, ref["id"], kind="storyboard")
    assert preview["operation"] == "video_storyboard" and preview["query"] == {
        "video_url": "https://v.douyin.com/abc/"
    }
    assert preview["will_call_provider"] is False
    with pytest.raises(NotFound):
        svc.reference_content(q, ref["id"], kind="storyboard")
    svc.update_rights(p, {**rights(), "model_process": False})
    with pytest.raises(ValueError):
        svc.reference_content(p, ref["id"], kind="storyboard")


def test_normalizers_preserve_missing_metrics_and_sales_ranges_on_archived_records(env):
    from test_industry import ad

    from creative_agent.industry import Industry

    _svc, s, p, _q, v = env
    legacy = Industry(s, None, v)
    c = {
        "id": "legacy",
        "provider": "datax",
        "platform": "tiktok",
        "operation": "tiktok.ads.search",
        "rights": rights(),
    }
    item = legacy._normalize(c, ad(), p, "historical", {"start_date": "2026-10-01", "end_date": "2026-10-18"})
    assert item["metrics"]["likes"]["value"] == "0"
    assert item["metrics"]["comments"]["value"] is None and item["metrics"]["sales"]["value"] is None
    assert item["metrics"]["audience"]["kind"] == "range"
    assert item["metrics"]["audience"]["value"] is None


def test_historical_source_revocation_still_blocks_results_model_and_export(env):
    from test_industry import plan

    from creative_agent.contracts import CostQuote
    from creative_agent.industry import Industry

    _svc, s, p, _q, v = env
    legacy = Industry(s, None, v)
    c = s.put(
        p,
        "industry_connection",
        {
            "provider": "datax",
            "platform": "tiktok",
            "operation": "tiktok.ads.search",
            "rights": {**rights(), "model_process": True, "export_metadata": True},
        },
    )
    query = plan(c["id"]).model_dump(mode="json")
    j = s.create_job(
        p,
        "industry_search",
        {
            "connection_snapshot": c,
            "plan": {
                **query,
                "allowed_actions": ["search", "retain_metadata", "model_process", "export_metadata"],
            },
        },
        CostQuote(amount="0", currency="CNY", basis="历史记录"),
        "archived",
    )
    r = s.put(p, "industry_result", {"job_id": j["id"], "title": "历史真实资料"})
    ref = s.put(p, "industry_reference", {"job_id": j["id"], "source_result_id": r["id"]})
    assert legacy.results(p, j["id"])[0]["title"] == "历史真实资料"
    legacy.validate_model_use(p, reference_ids=[ref["id"]])
    c["rights"] = {**rights(), "retain_metadata": False}
    s.put(p, "industry_connection", c, c["id"])
    assert legacy.search(p, j["id"])["content_restricted"] is True
    with pytest.raises(ValueError):
        legacy.results(p, j["id"])
    with pytest.raises(ValueError):
        legacy.validate_model_use(p, reference_ids=[ref["id"]])
    with pytest.raises(ValueError):
        legacy.validate_export(p)
    with pytest.raises(ValueError):
        legacy.run(p, j["id"])


def test_cmm_export_requires_current_and_snapshot_export_rights(env):
    svc, _s, p, _q, _v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)
    with transport({"code": 0, "data": "protected"}) as client:
        svc.run(p, j["id"], client=client)
    with pytest.raises(ValueError):
        svc.validate_export(p)
    svc.update_rights(p, {**rights(), "model_process": True, "export_metadata": True})
    with pytest.raises(ValueError):
        svc.validate_export(p)  # Cannot broaden immutable task consent.


def test_licensed_access_checks_cmm_cached_lineage_on_read_model_and_export(env):
    from creative_agent.industry import Industry
    from creative_agent.licensing_access import LicensedAccess

    svc, s, p, q, v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)
    with transport({"code": 0, "data": "禁止撤权后读取"}) as http:
        r = svc.run(p, j["id"], client=http)
    ref = svc.select(p, j["id"], [r["results"][0]["id"]])[0]
    access = LicensedAccess(s, Industry(s, None, v), cmm=svc)
    note = {"scope": {"project_id": p, "reference_ids": [ref["id"]]}, "external_evidence": [ref]}
    assert access.check(p, note, model=True) == note
    with pytest.raises(ValueError):
        access.check(p, note, export=True)
    with pytest.raises(NotFound):
        access.check(q, {"reference_ids": [ref["id"]]}, model=True)
    svc.update_rights(p, {**rights(), "retain_metadata": False})
    with pytest.raises(ValueError):
        access.check(p, note)
    with pytest.raises(ValueError):
        access.check(p, {"cmm_job_id": j["id"]})
    assert access.visible(p, [note]) == []


def test_concurrent_key_save_keeps_exactly_one_connection_and_active_vault_entry(env):
    from threading import Barrier

    svc, s, p, _q, v = env
    barrier = Barrier(2)
    original = v.set

    def delayed_set(k, value):
        original(k, value)
        barrier.wait(3)

    v.set = delayed_set
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda key: svc.connect(p, api_key=key), ("first-key", "second-key")))
    assert saved[0]["id"] == saved[1]["id"]
    assert len(s.list(p, "cmm_connection")) == 1 and len(v.data) == 1


def test_public_status_mutation_cannot_enable_unverified_parser_or_other_project(env):
    from creative_agent.cmm_schema import RESPONSE_SCHEMA_READY

    svc, _s, p, q, _v = env
    op = "video_library_custom_search"
    ready(svc, p, (op,))
    snapshot = svc.overview(p)
    try:
        snapshot["response_schema_ready"][op] = True
        assert svc.overview(q)["response_schema_ready"][op] is False
        with pytest.raises(ValueError, match="首次账号返回格式验证"):
            job(
                svc,
                p,
                operation=op,
                query={"start_date": "2026-10-01", "end_date": "2026-10-18"},
                max_points="10",
            )
    finally:
        RESPONSE_SCHEMA_READY[op] = False


def test_permission_metadata_never_persists_echoed_credentials(env):
    svc, s, p, _q, _v = env
    svc.connect(p, api_key="private-key")
    with (
        transport(
            {
                "code": 0,
                "data": {
                    "group_id": "private-key",
                    "version": "private-key",
                    "rights": {"extract_video_copywriting": True},
                },
            }
        ) as client,
        pytest.raises(ValueError, match="schema"),
    ):
        svc.permissions(p, "检查", client=client)
    assert "private-key" not in json.dumps(s.export_project(p))
    assert not any(svc.overview(p)["connection"]["capabilities"].values())


def test_poll_honors_atomic_call_and_point_limits_before_transport(env):
    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "3")
    tariff(svc, p, "video_understanding_task_info", "1")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/a/"]},
        max_calls=1,
        max_points="3",
    )
    with transport({"code": 0, "data": {"task_id": "task-limited"}}) as client:
        svc.run(p, j["id"], client=client)
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        result = svc.poll(p, j["id"], client=client)
    assert calls == [] and result["calls_attempted"] == 1 and result["state"] == "needs_attention"
    assert result["reserved_points"] == "3" and result["provider_task_id"] == "task-limited"


def test_midflight_revocation_never_saves_provider_content(env):
    svc, s, p, _q, _v = env
    ready(svc, p)
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p)
    j = job(svc, p)

    def revoked(r):
        svc.update_rights(p, {**rights(), "retain_metadata": False, "model_process": False})
        return httpx.Response(200, json={"code": 0, "data": "撤权后绝不保留的内容"})

    with httpx.Client(transport=httpx.MockTransport(revoked)) as client:
        result = svc.run(p, j["id"], client=client)
    assert result["state"] == "needs_attention" and result["result"] is None
    assert "撤权后绝不保留的内容" not in json.dumps(s.export_project(p))


def test_derived_content_keeps_parent_lineage_and_original_expiry_authoritative(env, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import creative_agent.industry as industry_module

    svc, _s, p, _q, _v = env
    ready(svc, p, ("extract_video_copywriting", "video_storyboard"))
    original_rights = {
        **rights(),
        "model_process": True,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    svc.update_rights(p, original_rights)
    tariff(svc, p)
    tariff(svc, p, "video_storyboard", "3")
    parent = job(svc, p)
    with transport({"code": 0, "data": "原始授权口播"}) as client:
        r = svc.run(p, parent["id"], client=client)
    parent_ref = svc.select(p, parent["id"], [r["results"][0]["id"]])[0]
    # Account consent renews, but does not expand the original reference consent.
    svc.update_rights(p, {**rights(), "model_process": True, "export_metadata": True})
    child = svc.create_reference_content(
        p, parent_ref["id"], kind="storyboard", max_calls=1, max_points="3", idempotency_key="derived"
    )
    assert child["parent_reference_id"] == parent_ref["id"]
    with transport(
        {
            "code": 0,
            "data": {
                "video_title": "衍生分镜",
                "video_url": "https://v.douyin.com/abc/",
                "duration": 2,
                "result": [
                    {"st": 0, "ed": 2, "clip_video_url": "https://example.com/a.mp4", "voice": "衍生语音"}
                ],
            },
        }
    ) as client:
        r = svc.run(p, child["id"], client=client)
    child_ref = svc.select(p, child["id"], [r["results"][0]["id"]])[0]
    assert child_ref["parent_reference_id"] == parent_ref["id"]
    assert svc.validated_evidence(p, [child_ref["id"]])[0]["text"] == "[0–2] 衍生语音"
    future = datetime.now(UTC) + timedelta(hours=2)

    class FutureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(industry_module, "datetime", FutureDatetime)
    assert svc.job(p, child["id"])["content_restricted"] is True
    with pytest.raises(ValueError, match="到期"):
        svc.validated_evidence(p, [child_ref["id"]])
    with pytest.raises(ValueError):
        svc.select(p, child["id"], [r["results"][0]["id"]])


def test_async_known_task_id_survives_unknown_status_without_resubmission(env):
    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "1")
    tariff(svc, p, "video_understanding_task_info", "0")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/a/"]},
        max_calls=2,
        max_points="1",
    )
    with transport(
        {"code": 0, "data": {"data": {"task_id": "known-id", "status": "unknown-future-state"}}}
    ) as client:
        result = svc.run(p, j["id"], client=client)
    assert result["state"] == "needs_attention" and result["provider_task_id"] == "known-id"
    calls = []

    def poll_reply(r):
        calls.append(json.loads(r.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"task_id": "known-id", "status": "succeeded", "result": "恢复的上游画面分析"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(poll_reply)) as client:
        result = svc.poll(p, j["id"], client=client)
    assert result["state"] == "succeeded"
    assert calls == [{"api": "video_understanding_task_info", "query": {"task_id": "known-id"}}]


def test_recovery_fences_stale_poll_before_reserve_finish_and_limits(env):
    from threading import Event, Lock

    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "4")
    tariff(svc, p, "video_understanding_task_info", "4")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/a/"]},
        max_calls=2,
        max_points="8",
    )
    with transport({"code": 0, "data": {"task_id": "fenced-task"}}) as client:
        svc.run(p, j["id"], client=client)
    first_guard = Event()
    release_first = Event()
    second_http = Event()
    release_second = Event()
    lock = Lock()
    guards = []
    requests = []

    def guard():
        with lock:
            guards.append(1)
            first = len(guards) == 1
        if first:
            first_guard.set()
            assert release_first.wait(5)

    svc.before_request = guard

    def reply(r):
        with lock:
            requests.append(json.loads(r.content))
            first = len(requests) == 1
        if first:
            second_http.set()
            assert release_second.wait(5)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "fenced-task",
                    "status": "succeeded",
                    "result": "owner" if first else "stale",
                },
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(reply)) as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        a = pool.submit(svc.poll, p, j["id"], client=client)
        assert first_guard.wait(3)
        svc.recover(p, j["id"])
        b = pool.submit(svc.poll, p, j["id"], client=client)
        assert second_http.wait(3)
        release_first.set()
        try:
            stale = a.result(timeout=3)
            assert stale["calls_attempted"] == 2
            assert stale["state"] == "polling"
            assert len(requests) == 1
        finally:
            release_second.set()
        final = b.result(timeout=3)
    assert final["state"] == "succeeded" and final["result"]["text"] == "owner"
    assert final["calls_attempted"] == 2 and final["attempted_points_upper"] == "8"


def test_preflight_shows_offline_fee_bound_and_unknown_tool_rate(env):
    svc, _s, p, _q, _v = env
    svc.connect(p, api_key="private-key")
    preview = svc.preflight(p, "extract_video_copywriting", {"video_url": "https://v.douyin.com/a/"})
    assert preview["max_points_per_call"] is None and preview["tariff_verified"] is False
    tariff(svc, p, cost="4")
    preview = svc.preflight(p, "extract_video_copywriting", {"video_url": "https://v.douyin.com/a/"})
    assert preview["max_points_per_call"] == "4" and preview["tariff_verified"] is True
    assert preview["tariff_evidence"] == "用户核对正式账户收费表"
    assert preview["tariff_basis"] and preview["will_call_provider"] is False


def test_stale_inflight_poll_response_cannot_replace_new_owner_result(env):
    from threading import Event

    svc, _s, p, _q, _v = env
    ready(svc, p, ("video_understanding", "video_understanding_task_info"))
    svc.update_rights(p, {**rights(), "model_process": True})
    tariff(svc, p, "video_understanding", "1")
    tariff(svc, p, "video_understanding_task_info", "1")
    j = job(
        svc,
        p,
        operation="video_understanding",
        query={"video_urls": ["https://v.douyin.com/a/"]},
        max_calls=3,
        max_points="3",
    )
    with transport({"code": 0, "data": {"task_id": "finish-fenced"}}) as client:
        svc.run(p, j["id"], client=client)
    started = Event()
    release = Event()

    def stale_reply(r):
        started.set()
        assert release.wait(5)
        return httpx.Response(
            200,
            json={"code": 0, "data": {"task_id": "finish-fenced", "status": "succeeded", "result": "stale"}},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(stale_reply)) as stale_client,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        old = pool.submit(svc.poll, p, j["id"], client=stale_client)
        assert started.wait(3)
        svc.recover(p, j["id"])
        try:
            with transport(
                {
                    "code": 0,
                    "data": {"task_id": "finish-fenced", "status": "succeeded", "result": "new owner"},
                }
            ) as client:
                current = svc.poll(p, j["id"], client=client)
            assert current["result"]["text"] == "new owner"
        finally:
            release.set()
        after = old.result(timeout=3)
    assert after["result"]["text"] == "new owner"
    assert after["calls_attempted"] == 3 and after["attempted_points_upper"] == "3"


def test_consent_update_during_key_rotation_never_restores_stale_vault_reference(env, monkeypatch):
    from creative_agent.industry import SourceRights

    svc, s, p, _q, v = env
    svc.connect(p, api_key="old-private-key")
    original = SourceRights.model_validate
    rotated = []

    def validate_then_rotate(value, *args, **kwargs):
        if not rotated:
            rotated.append(svc.connect(p, api_key="new-private-key"))
        return original(value, *args, **kwargs)

    monkeypatch.setattr(SourceRights, "model_validate", validate_then_rotate)
    svc.update_rights(p, rights())
    saved = s.list(p, "cmm_connection")[0]
    assert saved["revision"] == rotated[0]["revision"]
    assert v.get(saved["secret_ref"]) == "new-private-key"
    assert len(v.data) == 1
