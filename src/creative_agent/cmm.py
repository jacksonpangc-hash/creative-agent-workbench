"""CMM-only project service. Offline contracts are never live-account verification."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from .cmm_schema import (
    RESPONSE_SCHEMA_READY,
    SCHEMA_GAP,
    SCHEMAS,
    SEARCH,
    TOOLS,
    VERSION,
    parse_response,
    permission_payload,
    validate_query,
)
from .connectors import CMMConnector
from .contracts import decimal
from .industry import Industry, SourceRights, clean_raw
from .store import Conflict, NotFound, dumps, export_safe, now, uid


class CMMService:
    def __init__(self, store, vault, *, before_request=None):
        self.store, self.vault, self.before_request = store, vault, before_request

    def _connection(self, p):
        rows = self.store.list(p, "cmm_connection")
        return rows[0] if rows else None

    def _view(self, c):
        return {
            **export_safe(c, reset_credentials=False),
            "has_key": bool(c.get("secret_ref")) and c.get("has_key", True),
            "response_schema_ready": dict(RESPONSE_SCHEMA_READY),
        }

    def connect(self, p, *, api_key, label="蝉妈妈"):
        self.store.project(p)
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 8192:
            raise ValueError("需要有效的蝉妈妈 API Key")
        if not isinstance(label, str) or not 1 <= len(label) <= 100:
            raise ValueError("连接名称无效")
        ref = f"{p}:cmm:{uid()}"
        try:
            self.vault.set(ref, api_key)
        except Exception:  # noqa: BLE001 - system keyring backend exceptions vary
            raise ValueError("系统密钥库写入失败，不会明文保存") from None
        old = None
        try:
            with self.store.connection(True) as db:
                self.store._project(db, p)
                row = db.execute(
                    "SELECT data FROM records WHERE project_id=? AND kind='cmm_connection'", (p,)
                ).fetchone()
                old = json.loads(row["data"]) if row else None
                stamp = now()
                c = {
                    "id": old["id"] if old else uid(),
                    "project_id": p,
                    "created_at": old["created_at"] if old else stamp,
                    "updated_at": stamp,
                    "provider": "cmm",
                    "label": label,
                    "secret_ref": ref,
                    "revision": uid(),
                    "status": "unverified",
                    "live_verified": False,
                    "capabilities": {},
                    "rights": None,
                }
                if old:
                    db.execute(
                        "UPDATE records SET data=?,updated_at=? WHERE project_id=? AND kind='cmm_connection' AND id=?",
                        (dumps(c), stamp, p, c["id"]),
                    )
                else:
                    db.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        (c["id"], p, "cmm_connection", dumps(c), stamp, stamp),
                    )
        except Exception:
            self.vault.delete(ref)
            raise
        if old:
            self.vault.delete(old["secret_ref"])
        return self._view(c)

    def _point_usage(self, db, p):
        rows = [(r['kind'], json.loads(r['data'])) for r in db.execute(
            "SELECT kind,data FROM records WHERE project_id=? AND kind IN ('cmm_budget','cmm_job','cmm_call_receipt')", (p,))]
        limit = next((decimal(r['limit_points']) for kind, r in rows if kind == 'cmm_budget'), None)
        charges = [r for kind, r in rows if kind in {'cmm_job', 'cmm_call_receipt'}]
        reserved = sum((decimal(r['reserved_points']) for r in charges), Decimal(0))
        return {'unit': 'points', 'limit_points': str(limit) if limit is not None else None,
                'reserved_points': str(reserved),
                'remaining_points': str(max(Decimal(0), limit - reserved)) if limit is not None else None,
                'calls_attempted': sum(r.get('calls_attempted', 0) for r in charges),
                'actual_points': None, 'billing_verified': False}

    def configure_budget(self, p, *, limit_points):
        limit = decimal(limit_points)
        if limit < 0:
            raise ValueError('累计积分上限不能为负')
        with self.store.connection(True) as db:
            self.store._project(db, p)
            usage = self._point_usage(db, p)
            if limit < decimal(usage['reserved_points']):
                raise ValueError('累计积分上限不能低于已预留的积分；未知扣费不能自动清零')
            existing = db.execute("SELECT id FROM records WHERE project_id=? AND kind='cmm_budget'", (p,)).fetchone()
            stamp = now(); identifier = existing['id'] if existing else uid()
            value = {'id': identifier, 'project_id': p, 'limit_points': str(limit), 'updated_at': stamp}
            if existing:
                db.execute('UPDATE records SET data=?,updated_at=? WHERE project_id=? AND id=?',
                           (dumps(value), stamp, p, identifier))
            else:
                db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                           (identifier, p, 'cmm_budget', dumps(value), stamp, stamp))
            return self._point_usage(db, p)

    def overview(self, p):
        c = self._connection(p)
        jobs = [self.job(p, j["id"]) for j in self.store.list(p, "cmm_job")]
        with self.store.connection() as db:
            usage = self._point_usage(db, p)
        return {
            "connection": self._view(c) if c else None,
            "jobs": jobs,
            "capabilities": c.get("capabilities", {}) if c else {},
            "usage": usage,
            "tariffs": self.tariffs(p),
            "response_schema_ready": dict(RESPONSE_SCHEMA_READY),
            "gaps": [
                "真实账户响应与收费未验证",
                "未知响应 schema 不会被当作空结果",
                "积分预留为调用上限估计，不是已核实扣费",
            ],
        }

    def _required_connection(self, p):
        c = self._connection(p)
        if not c:
            raise ValueError("请先保存蝉妈妈 API Key")
        return c

    def _mutate_connection(self, p, change):
        with self.store.connection(True) as db:
            row = db.execute(
                "SELECT data FROM records WHERE project_id=? AND kind='cmm_connection'", (p,)
            ).fetchone()
            if not row:
                raise ValueError("请先保存蝉妈妈 API Key")
            current = json.loads(row["data"])
            change(current)
            current["updated_at"] = now()
            db.execute(
                "UPDATE records SET data=?,updated_at=? WHERE project_id=? AND kind='cmm_connection' AND id=?",
                (dumps(current), current["updated_at"], p, current["id"]),
            )
        return current

    def permissions(self, p, intent, *, client=None):
        self._guard()
        # Saving a key is offline. Even metadata checks need a verified tariff.
        rate = self._tariff(p, "permission_check")
        if rate != 0:
            raise ValueError("权益检查费用未确认为0积分，暂不提交；请先核对供应商收费说明")
        # Mutate the current connection atomically: no stale key reference or consent can be restored.
        c = self._mutate_connection(
            p, lambda current: current.update(capabilities={op: False for op in SCHEMAS}, status="unverified")
        )
        key = self.vault.get(c.get("secret_ref"))
        with (
            httpx.Client(timeout=60, trust_env=False, follow_redirects=False)
            if client is None
            else nullcontext(client)
        ) as http:
            data = permission_payload(CMMConnector(key, client=http).permissions(intent))

        def grant(current):
            if current["revision"] != c["revision"]:
                raise ValueError("检查期间凭证发生变化，请重新检查")
            current.update(
                capabilities={op: data["rights"].get(op) is True for op in SCHEMAS},
                status="permissions_checked",
                group_id=clean_raw(data["group_id"], [key]),
                version=clean_raw(data["version"], [key]),
                permission_checked_at=now(),
                permission_schema_version=VERSION,
            )

        return self._view(self._mutate_connection(p, grant))

    @staticmethod
    def search_query(*, kind, keyword, start_date, end_date, category="", **unused):
        if kind not in SEARCH:
            raise ValueError("不支持的搜索类型")
        operation = SEARCH[kind]
        a, b = ("start_date", "end_date") if kind == "qianchuan" else ("create_start_time", "create_end_time")
        query = {"keyword": keyword, a: str(start_date), b: str(end_date), "page": 1}
        if category:
            query["star_category" if kind == "popular" else "category_id"] = category
        validate_query(operation, query)
        return {"operation": operation, "query": query}

    def preflight(self, p, operation, query):
        self.store.project(p)
        validate_query(operation, query)
        c = self._connection(p)
        rate = self.tariffs(p).get(operation)
        return {
            "max_points_per_call": rate["max_points_per_call"] if rate and rate.get("verified") else None,
            "tariff_verified": bool(rate and rate.get("verified")),
            "tariff_basis": rate.get("basis") if rate else None,
            "tariff_evidence": rate.get("evidence") if rate else None,
            "operation": operation,
            "query": query,
            "schema_version": VERSION,
            "authorized": bool(c and c.get("capabilities", {}).get(operation) is True),
            "response_schema_ready": RESPONSE_SCHEMA_READY.get(operation, False),
            "disabled_reason": None if RESPONSE_SCHEMA_READY.get(operation) else SCHEMA_GAP,
            "will_call_provider": False,
            "live_verified": False,
        }

    def update_rights(self, p, rights):
        r = SourceRights.model_validate(rights)
        Industry._require(r.model_dump(mode="json"), [])
        return self._view(
            self._mutate_connection(p, lambda current: current.update(rights=r.model_dump(mode="json")))
        )

    def tariffs(self, p):
        self.store.project(p)
        # Public short-video data tariff is 1–10 points; reserve the maximum.
        # This is an estimate, never a verified actual charge.
        public = {
            op: {
                "operation": op,
                "max_points_per_call": "10",
                "evidence": "https://ai.chanmama.com/skills",
                "basis": "公开短视频数据 1–10积分/次，按最高值预留",
                "verified": True,
                "actual_verified": False,
            }
            for op in [*SEARCH.values(), "video_detail_info"]
        }
        for op in ("product_category_search", "author_category_search", "video_search"):
            public[op] = {
                "operation": op,
                "max_points_per_call": "0",
                "evidence": "https://ai.chanmama.com/skills",
                "basis": "公开基础名称/ID检索0积分",
                "verified": True,
                "actual_verified": False,
            }
        for row in reversed(self.store.list(p, "cmm_tariff")):
            public[row["operation"]] = row
        return public

    def configure_tariff(self, p, *, operation, max_points_per_call, evidence, verified):
        if (
            operation not in {*SCHEMAS, "permission_check"}
            or verified is not True
            or not isinstance(evidence, str)
            or not evidence.strip()
            or len(evidence) > 2000
        ):
            raise ValueError("需要明确核对积分费率和依据")
        amount = decimal(max_points_per_call)
        if amount < 0:
            raise ValueError("积分费率不能为负")
        old = next((x for x in self.store.list(p, "cmm_tariff") if x["operation"] == operation), None)
        return self.store.put(
            p,
            "cmm_tariff",
            {
                "operation": operation,
                "max_points_per_call": str(amount),
                "evidence": evidence,
                "verified": True,
                "actual_verified": False,
                "basis": "用户明确核验的每次最大积分",
            },
            old["id"] if old else None,
        )

    def _tariff(self, p, operation):
        rate = self.tariffs(p).get(operation)
        if not rate or rate.get("verified") is not True:
            raise ValueError("此操作积分费率未知；需要先明确核验，禁止自动收费调用")
        return decimal(rate["max_points_per_call"])

    def _access(self, p, operation, *, revision=None, snapshot=None, actions=None):
        c = self._required_connection(p)
        if revision and c["revision"] != revision:
            raise ValueError("凭证已更换，旧任务不可提交")
        if c.get("capabilities", {}).get(operation) is not True:
            raise ValueError("蝉妈妈未明确授权此 API；未知权限默认禁止")
        # Do not cache an indefinite permission grant.
        checked = c.get("permission_checked_at")
        if not checked or datetime.fromisoformat(checked) + timedelta(hours=24) <= datetime.now(UTC):
            raise ValueError("API 权限检查已过期，请重新检查权益")
        required = actions or (
            ["retain_metadata", "model_process"]
            if operation in TOOLS or operation == "video_understanding_task_info"
            else ["search", "retain_metadata"]
        )
        if not c.get("rights"):
            raise ValueError("需要明确确认来源内容保留及处理授权")
        Industry._require(c["rights"], required)
        if snapshot:
            Industry._require(snapshot, required)
        return c

    def authorize_remote_request(self, p, operation, **kwargs):
        return self._access(p, operation, **kwargs)

    def authorize_cached_content(self, p, reference, actions=None):
        required = actions or ['retain_metadata', 'model_process']
        saved = reference.get('rights_snapshot')
        if not saved: raise ValueError('已存内容缺少留存和使用依据')
        Industry._require(saved, required)
        c = self._required_connection(p)
        current = c.get('rights')
        if not current: raise ValueError('请核对已存内容的当前使用授权')
        Industry._require(current, required)
        if reference.get('revoked') or saved.get('revoked') or current.get('revoked'):
            raise ValueError('此内容授权已撤销')
        # Contract subscription conditions are independent from the API probe TTL.
        for r in (saved, current):
            if r.get('requires_active_subscription'):
                expiry = r.get('subscription_valid_until')
                if not expiry or datetime.fromisoformat(expiry) <= datetime.now(UTC):
                    raise ValueError('该内容的持续订阅授权需要续期')
        return reference

    def create_job(
        self,
        p,
        *,
        operation,
        query,
        max_calls,
        max_points,
        idempotency_key,
        target_count=20,
        parent_reference_id=None,
    ):
        validate_query(operation, query)
        if not RESPONSE_SCHEMA_READY.get(operation):
            raise ValueError(SCHEMA_GAP)
        if (
            type(max_calls) is not int
            or not 1 <= max_calls <= 100
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 150
        ):
            raise ValueError("需要1–100次调用上限和幂等标识")
        if type(target_count) is not int or not 1 <= target_count <= 1000:
            raise ValueError("目标数量无效")
        ceiling = decimal(max_points)
        if ceiling < 0:
            raise ValueError("积分上限不能为负")
        rate = self._tariff(p, operation)
        if rate > ceiling:
            raise ValueError("积分上限不足以覆盖一次最大收费")
        c = self._access(p, operation)
        parent = self.validated_evidence(p, [parent_reference_id])[0] if parent_reference_id else None
        if operation == "video_understanding":
            self._access(p, "video_understanding_task_info")
            self._tariff(p, "video_understanding_task_info")
        identity = {
            "operation": operation,
            "query": query,
            "max_calls": max_calls,
            "max_points": str(ceiling),
            "target_count": target_count,
            "connection_revision": c["revision"],
        }
        if parent:
            identity.update(
                parent_reference_id=parent_reference_id, parent_rights_snapshot=parent["rights_snapshot"]
            )
        fingerprint = hashlib.sha256(dumps(identity).encode()).hexdigest()
        # All CMM jobs, point reservations and idempotency decisions are scoped and serialized.
        with self.store.connection(True) as db:
            self.store._project(db, p)
            for r in db.execute("SELECT data FROM records WHERE project_id=? AND kind='cmm_job'", (p,)):
                existing = json.loads(r["data"])
                if existing["idempotency_key"] == idempotency_key:
                    if existing["fingerprint"] != fingerprint:
                        raise Conflict("幂等标识已用于不同 CMM 查询计划")
                    return self._job_view(p, existing)
            usage = self._point_usage(db, p)
            if (usage['limit_points'] is not None
                    and decimal(usage['reserved_points']) + ceiling > decimal(usage['limit_points'])):
                raise ValueError('已达到本项目累计积分上限，未提交供应商；请先核对已有用量')
            stamp = now()
            j = {
                **identity,
                "id": uid(),
                "project_id": p,
                "created_at": stamp,
                "updated_at": stamp,
                "idempotency_key": idempotency_key,
                "fingerprint": fingerprint,
                "state": "queued",
                "reserved_points": str(ceiling),
                "attempted_points_upper": "0",
                "actual_points": None,
                "calls_attempted": 0,
                "provider_task_id": None,
                "result": None,
                "error": None,
                "rights_snapshot": c["rights"],
                "schema_version": VERSION,
                "live_verified": False,
            }
            db.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)", (j["id"], p, "cmm_job", dumps(j), stamp, stamp)
            )
        return self._job_view(p, j)

    def _atomic(self, p, jid, change):
        with self.store.connection(True) as db:
            row = db.execute(
                "SELECT data FROM records WHERE project_id=? AND kind='cmm_job' AND id=?", (p, jid)
            ).fetchone()
            if not row:
                raise NotFound("CMM任务不存在")
            j = json.loads(row["data"])
            change(j)
            j["updated_at"] = now()
            db.execute(
                "UPDATE records SET data=?,updated_at=? WHERE project_id=? AND id=?",
                (dumps(j), j["updated_at"], p, jid),
            )
        return j

    def _job_view(self, p, j):
        safe = export_safe(j, reset_credentials=False)
        safe.pop("fingerprint", None)
        safe.pop("attempt_owner", None)
        # Revocation remains authoritative even while external requests are in flight.
        if j.get("result"):
            try:
                self.validate_job_access(p, j, actions=["retain_metadata"])
            except ValueError:
                safe["result"] = None
                safe["content_restricted"] = True
        safe["results"] = (safe.get("result") or {}).get("results", [])
        return safe

    def job(self, p, jid):
        return self._job_view(p, self.store.get(p, "cmm_job", jid))

    def _claim(self, p, jid, *, poll=False):
        claimed = []

        def mutate(j):
            allowed = {"waiting", "needs_attention"} if poll else {"queued"}
            if j["state"] not in allowed or poll and not j.get("provider_task_id"):
                return
            op = "video_understanding_task_info" if poll else j["operation"]
            self._access(p, op, revision=j["connection_revision"], snapshot=j["rights_snapshot"])
            self.validate_job_access(p, j)
            rate = self._tariff(p, op)
            if j["calls_attempted"] >= j["max_calls"] or decimal(
                j["attempted_points_upper"]
            ) + rate > decimal(j["max_points"]):
                j["state"] = "needs_attention"
                j["error"] = "调用或积分上限已达；不会继续请求"
                return
            j.update(state="polling" if poll else "submitting", error=None, attempt_owner=uid())
            claimed.append((op, rate))

        j = self._atomic(p, jid, mutate)
        return j, claimed[0] if claimed else None

    def run(self, p, jid, *, client=None):
        return self._execute(p, jid, client=client, poll=False)

    def poll(self, p, jid, *, client=None):
        return self._execute(p, jid, client=client, poll=True)

    def _execute(self, p, jid, *, client=None, poll=False):
        j, claim = self._claim(p, jid, poll=poll)
        if not claim:
            return self._job_view(p, j)
        op, rate = claim
        owner = j["attempt_owner"]
        attempted = False
        try:
            c = self._access(p, op, revision=j["connection_revision"], snapshot=j["rights_snapshot"])
            self.validate_job_access(p, j)
            key = self.vault.get(c["secret_ref"])
            if not key:
                raise ValueError("凭证不可用")
            query = {"task_id": j["provider_task_id"]} if poll else j["query"]
            self._guard()

            # Persist a call reservation BEFORE I/O. A crash after this point is uncertain, never auto-retry.
            def reserve(row):
                if row.get("attempt_owner") != owner or row["state"] != ("polling" if poll else "submitting"):
                    raise Conflict("任务已恢复、取消或由其他执行者领取")
                if row["calls_attempted"] >= row["max_calls"] or decimal(
                    row["attempted_points_upper"]
                ) + rate > decimal(row["max_points"]):
                    raise ValueError("调用或积分上限已达，禁止追加预留")
                row["calls_attempted"] += 1
                row["attempted_points_upper"] = str(decimal(row["attempted_points_upper"]) + rate)

            self._atomic(p, jid, reserve)
            attempted = True
            schema = {
                "api": op,
                "authorized": True,
                "properties": {k: {} for k in SCHEMAS[op][0]},
                "required": list(SCHEMAS[op][1]),
            }
            with (
                httpx.Client(timeout=240, trust_env=False, follow_redirects=False)
                if client is None
                else nullcontext(client)
            ) as http:
                response = CMMConnector(key, client=http).execute(op, query, permission_schema=schema)
            if op == "video_understanding":
                # Save the documented task identity even if a future status/result schema is unknown.
                # This allows polling the same task, never re-submitting to obtain another ID.
                envelope = response.get("data")
                task = (
                    envelope
                    if isinstance(envelope, dict) and envelope.get("task_id")
                    else envelope.get("data")
                    if isinstance(envelope, dict)
                    else None
                )
                task_id = task.get("task_id") if isinstance(task, dict) else None
                if isinstance(task_id, str) and 0 < len(task_id) <= 2000 and key not in task_id:

                    def remember_task(row):
                        if row.get("attempt_owner") == owner:
                            row.update(provider_task_id=task_id)

                    self._atomic(p, jid, remember_task)
            parsed = clean_raw(parse_response(op, response), [key])
            if poll and parsed.get("task_id") != j["provider_task_id"]:
                raise ValueError("CMM task schema 标识与原任务不一致")
            self._access(p, op, revision=j["connection_revision"], snapshot=j["rights_snapshot"])
            self.validate_job_access(p, j)

            def finish(row):
                if row.get("attempt_owner") != owner:
                    return
                row["attempt_owner"] = None
                if parsed.get("task_id"):
                    row["provider_task_id"] = parsed["task_id"]
                if row["state"] == "cancelled":
                    return
                if parsed.get("status") in {"pending", "running", "retrying"}:
                    row["state"] = "waiting"
                    return
                if parsed.get("status") == "failed":
                    row.update(
                        state="failed",
                        error="蝉妈妈异步任务失败；费用待核对",
                        reserved_points=row["attempted_points_upper"],
                    )
                    return
                if "results" in parsed:
                    received = parsed["results"]
                    items = [
                        {**item, "source_kind": "industry", "provider": "cmm", "fetched_at": now(),
                         "operation": row["operation"], "schema_version": VERSION,
                         "provenance": {"provider": "cmm", "operation": row["operation"],
                                        "response_schema_version": parsed["response_schema_version"],
                                        "content_basis": item["content_basis"]}, "untrusted_source_content": True}
                        for item in received[:row["target_count"]]
                    ]
                    row.update(state="succeeded", reserved_points=row["attempted_points_upper"],
                               result={**parsed, "results": items, "received_count": len(received),
                                       "selected_limit": row["target_count"],
                                       "target_reached": len(items) >= row["target_count"]})
                    return
                result_id = f"{row['id']}:content"
                item = {
                    **parsed,
                    "id": result_id,
                    "source_kind": "industry",
                    "provider": "cmm",
                    "title": parsed.get("title")
                    or {
                        "transcript": "蝉妈妈口播文案",
                        "storyboard_transcript": "蝉妈妈分镜语音",
                        "visual_analysis": "蝉妈妈画面理解",
                    }.get(parsed["content_basis"], "蝉妈妈参考"),
                    "source_url": (
                        row["query"].get("video_url") or (row["query"].get("video_urls") or [None])[0]
                    ),
                    "fetched_at": now(),
                    "operation": row["operation"],
                    "schema_version": VERSION,
                    "provenance": {
                        "provider": "cmm",
                        "operation": row["operation"],
                        "schema_version": VERSION,
                        "provider_task_id": row.get("provider_task_id"),
                        "content_basis": parsed["content_basis"],
                    },
                    "metrics": {},
                    "untrusted_source_content": True,
                }
                row.update(
                    state="succeeded",
                    result={**parsed, "results": [item]},
                    reserved_points=row["attempted_points_upper"],
                )

            self._atomic(p, jid, finish)
        except Exception as exc:  # noqa: BLE001 - uncertain paid outcomes require durable safe state
            reason = (
                "响应 schema 未核验"
                if isinstance(exc, ValueError) and "schema" in str(exc)
                else "请求或授权校验未完成"
            )

            def failure(row):
                if row.get("attempt_owner") != owner:
                    return
                row["attempt_owner"] = None
                if row["state"] == "cancelled":
                    return
                # Do not persist raw HTTP/provider errors: they can echo keys and signed URLs.
                row.update(
                    state="needs_attention" if attempted else "failed",
                    error="CMM " + reason + "；可能已计费，未自动重试，请核对账户和账单"
                    if attempted
                    else "调用前授权、预算或凭证检查失败，未请求供应商",
                    reserved_points=row["attempted_points_upper"]
                    if not attempted
                    else row["reserved_points"],
                )

            self._atomic(p, jid, failure)
        return self.job(p, jid)

    def cancel(self, p, jid):
        def change(j):
            if j["state"] in {"succeeded", "failed", "cancelled"}:
                return
            j.update(
                attempt_owner=None,
                state="cancelled",
                reserved_points=j["attempted_points_upper"],
                error="已停止本地提交与轮询；供应商任务可能继续，已请求部分费用待核对",
            )

        return self._job_view(p, self._atomic(p, jid, change))

    def recover(self, p, jid):
        def change(j):
            if j["state"] in {"submitting", "polling"}:
                j.update(
                    attempt_owner=None,
                    state="needs_attention",
                    error="上次请求结果未知；仅已知 task_id 可继续轮询，绝不重发提交",
                )

        return self._job_view(p, self._atomic(p, jid, change))

    def select(self, p, jid, result_ids):
        j = self.store.get(p, "cmm_job", jid)
        self.validate_job_access(p, j, actions=["retain_metadata"])
        items = {r["id"]: r for r in (j.get("result") or {}).get("results", [])}
        if not result_ids or len(result_ids) > 100 or any(rid not in items for rid in result_ids):
            raise ValueError("所选结果不属于本项目任务")
        refs = []
        with self.store.connection(True) as db:
            existing = [
                json.loads(r["data"])
                for r in db.execute(
                    "SELECT data FROM records WHERE project_id=? AND kind='cmm_reference'", (p,)
                )
            ]
            for rid in dict.fromkeys(result_ids):
                ref = next((x for x in existing if x["job_id"] == jid and x["result_id"] == rid), None)
                if not ref:
                    stamp = now()
                    ref = {
                        "id": uid(),
                        "project_id": p,
                        "created_at": stamp,
                        "updated_at": stamp,
                        "job_id": jid,
                        "result_id": rid,
                        "rights_snapshot": j["rights_snapshot"],
                        **(
                            {
                                "parent_reference_id": j["parent_reference_id"],
                                "parent_rights_snapshot": j["parent_rights_snapshot"],
                            }
                            if j.get("parent_reference_id")
                            else {}
                        ),
                        **{k: v for k, v in items[rid].items() if k != "id"},
                    }
                    db.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        (ref["id"], p, "cmm_reference", dumps(ref), stamp, stamp),
                    )
                    existing.append(ref)
                refs.append(export_safe(ref, reset_credentials=False))
        return refs

    def references(self, p):
        refs = []
        for ref in self.store.list(p, "cmm_reference"):
            try:
                refs.extend(self.validated_evidence(p, [ref["id"]], actions=["retain_metadata"]))
            except ValueError:
                refs.append({"id": ref["id"], "job_id": ref["job_id"], "content_restricted": True})
        return refs

    def validated_evidence(self, p, reference_ids, *, actions=None, _seen=None):
        out = []
        seen = set() if _seen is None else set(_seen)
        for rid in reference_ids:
            if rid in seen or len(seen) > 50:
                raise ValueError("CMM 来源 lineage 循环或过深")
            branch = seen | {rid}
            ref = self.store.get(p, "cmm_reference", rid)
            j = self.store.get(p, "cmm_job", ref["job_id"])
            self.validate_job_access(
                p, j, actions=actions or ["retain_metadata", "model_process"], _seen=branch
            )
            self.authorize_cached_content(p, ref, actions or ["retain_metadata", "model_process"])
            out.append(export_safe(ref, reset_credentials=False))
        return out

    def _guard(self):
        if not self.before_request:
            return
        result = self.before_request()
        if inspect.isawaitable(result):
            asyncio.run(result)

    def reference_content(self, p, rid, *, kind):
        ref = self.validated_evidence(p, [rid])[0]
        op = {
            "copywriting": "extract_video_copywriting",
            "storyboard": "video_storyboard",
            "understanding": "video_understanding",
        }.get(kind)
        if not op:
            raise ValueError("未知视频内容处理类型")
        url = ref.get("source_url")
        if url:
            query = {"video_urls": [url]} if kind == "understanding" else {"video_url": url}
        elif kind == "storyboard" and ref.get("aweme_id"):
            query = {"video_id": ref["aweme_id"]}
        else:
            raise ValueError("参考缺少可核验的视频链接；千川 material_id 不可代替视频链接或蝉妈妈视频 ID")
        preview = self.preflight(p, op, query)
        rate = self.tariffs(p).get(op)
        return {
            **preview,
            "reference_id": rid,
            "max_points_per_call": rate["max_points_per_call"] if rate and rate.get("verified") else None,
            "tariff_verified": bool(rate and rate.get("verified")),
        }

    def create_reference_content(self, p, rid, *, kind, max_calls, max_points, idempotency_key):
        preview = self.reference_content(p, rid, kind=kind)
        return self.create_job(
            p,
            operation=preview["operation"],
            query=preview["query"],
            max_calls=max_calls,
            max_points=max_points,
            idempotency_key=idempotency_key,
            parent_reference_id=rid,
        )

    def validate_export(self, p):
        for j in self.store.list(p, "cmm_job"):
            if j.get("result"):
                self.validate_job_access(p, j, actions=["retain_metadata", "export_metadata"])
        for ref in self.store.list(p, "cmm_reference"):
            self.validated_evidence(p, [ref["id"]], actions=["retain_metadata", "export_metadata"])
        return True

    def validate_job_access(self, p, j, *, actions=None, _seen=None):
        required = actions or (
            ["retain_metadata", "model_process"]
            if j["operation"] in TOOLS or j["operation"] == "video_understanding_task_info"
            else ["search", "retain_metadata"]
        )
        self.authorize_cached_content(p, j, required)
        if j.get("parent_reference_id"):
            if not j.get("parent_rights_snapshot"):
                raise ValueError("衍生 CMM 来源缺少原始授权快照")
            Industry._require(j["parent_rights_snapshot"], required)
            self.validated_evidence(p, [j["parent_reference_id"]], actions=required, _seen=_seen)
        return True
