"""Project-scoped licensed reference search. HTTP contracts are not live validation."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import Field, model_validator

from .contracts import Contract, Currency, Metric, decimal
from .media import validate_remote_url
from .store import SECRET_KEYS, NotFound, dumps, export_safe, now, provenance_url, uid

ACTIONS = (
    "search",
    "retain_metadata",
    "download_media",
    "retain_media",
    "model_process",
    "derivative_create",
    "export_metadata",
    "export_media",
)
CMM_OP = "video_commerce_video_library_custom_search_top_selling_video"
TIKHUB_OP = "douyin.video_search_v1"
DATAX_OP = "tiktok.ads.search"
DATAX_DOUYIN_OP = "douyin.videos.search"
CATALOG = [
    {
        "provider": "cmm",
        "platform": "douyin",
        "operation": CMM_OP,
        "label": "蝉妈妈带货视频关键词检索",
        "response_mapping_required": True,
        "category_filter": "server",
        "date_filter": "server",
        "docs": "https://cdn-cmm-ai-open.chanmama.com/.well-known/agent-skills/cmm-api/references/video.md",
    },
    {
        "provider": "tikhub",
        "platform": "douyin",
        "operation": TIKHUB_OP,
        "label": "TikHub 抖音视频搜索 V1",
        "response_mapping_required": False,
        "category_filter": "unsupported",
        "date_filter": "local_published",
        "docs": "https://docs.tikhub.io/370212779e0.md",
    },
    {
        "provider": "datax",
        "platform": "douyin",
        "operation": DATAX_DOUYIN_OP,
        "label": "DataX 抖音视频关键词搜索",
        "response_mapping_required": False,
        "category_filter": "local_ext_category",
        "date_filter": "local_published",
        "docs": "https://dataxapi.com/openapi.json",
    },
    {
        "provider": "datax",
        "platform": "tiktok",
        "operation": DATAX_OP,
        "label": "DataX TikTok 欧洲广告透明库（非抖音）",
        "response_mapping_required": False,
        "category_filter": "local_ext_category",
        "date_filter": "local_first_shown",
        "docs": "https://dataxapi.com/openapi.json",
    },
]


class SourceRights(Contract):
    revoked: bool = False
    requires_active_subscription: bool = False
    subscription_valid_until: datetime | None = None
    search: bool = False
    retain_metadata: bool = False
    download_media: bool = False
    retain_media: bool = False
    model_process: bool = False
    derivative_create: bool = False
    export_metadata: bool = False
    export_media: bool = False
    expires_at: datetime
    evidence: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def aware(self):
        if self.expires_at.tzinfo is None:
            raise ValueError("授权到期时间必须包含时区")
        if not self.evidence.strip():
            raise ValueError("需要授权依据")
        return self


class MetricMapping(Contract):
    path: str = Field(min_length=1, max_length=200)
    kind: Literal["exact", "range", "estimated", "index"]
    unit: str = ""


class ResponseMapping(Contract):
    reviewed: bool = False
    evidence: str = Field(min_length=1, max_length=2000)
    items_path: str = Field(min_length=1, max_length=200)
    id_path: str = Field(min_length=1, max_length=200)
    title_path: str = Field(min_length=1, max_length=200)
    url_path: str = ""
    published_path: str = ""
    media_path: str = ""
    entity_id_path: str = ""
    entity_name_path: str = ""
    metrics: dict[str, MetricMapping] = Field(default_factory=dict, max_length=40)


class ConnectionInput(Contract):
    provider: Literal["cmm", "tikhub", "datax"]
    label: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1, max_length=8192)
    operation: str = ""
    api_authorized: bool = False
    authorization_evidence: str = Field(min_length=1, max_length=2000)
    rights: SourceRights
    response_mapping: ResponseMapping | None = None


class SearchPlanInput(Contract):
    connection_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)
    category: str = Field(default="", max_length=200)
    start_date: date
    end_date: date
    target_count: int = Field(default=20, ge=1, le=1000)
    max_calls: int = Field(default=1, ge=1, le=100)
    quote: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: Currency
    allowed_actions: list[
        Literal[
            "search",
            "retain_metadata",
            "download_media",
            "retain_media",
            "model_process",
            "derivative_create",
            "export_metadata",
            "export_media",
        ]
    ]
    idempotency_key: str = Field(min_length=1, max_length=150)

    @model_validator(mode="after")
    def dates(self):
        if self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        if not self.query.strip():
            raise ValueError("需要检索关键词")
        return self


class SelectionInput(Contract):
    result_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    import_media: bool = False
    attempt_key: str | None = Field(default=None, min_length=1, max_length=150)


def public(value):
    safe = export_safe(value)
    if isinstance(value, dict) and "has_key" in value:
        safe["has_key"] = value["has_key"]
    return safe


def path(data, dotted, default=None):
    if not dotted:
        return default
    for key in dotted.split("."):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def clean_raw(value, secrets=()):
    """Retain source data, never echoed auth or cached credential-bearing headers."""
    if isinstance(value, dict):
        return {
            k: clean_raw(v, secrets)
            for k, v in value.items()
            if k.lower() not in SECRET_KEYS | {"secret_ref", "auth_info", "headers", "params"}
            and not any(secret and secret in k for secret in secrets)
        }
    if isinstance(value, list):
        return [clean_raw(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def metric(value=None, *, kind="exact", unit="", source=""):
    if value is None:
        return Metric(source=source, unit=unit).model_dump(mode="json")
    if kind == "range":
        if isinstance(value, dict):
            lo, hi = value.get("lower"), value.get("upper")
        else:
            match = re.fullmatch(r"\s*([\d,.]+)([KkMm]?)\s*[–—-]\s*([\d,.]+)([KkMm]?)\s*", str(value))
            if not match:
                return {**Metric(source=source, unit=unit).model_dump(mode="json"), "raw_value": value}

            def number(v, suffix):
                return decimal(v.replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[suffix.lower()]

            lo, hi = number(match[1], match[2]), number(match[3], match[4])
        return Metric(kind="range", lower=lo, upper=hi, source=source, unit=unit).model_dump(mode="json")
    return Metric(kind=kind, value=decimal(value), unit=unit, source=source).model_dump(mode="json")


def timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).isoformat()
    try:
        datetime.fromisoformat(str(value))
        return str(value)
    except ValueError:
        raise ValueError("来源时间字段与文档不一致") from None


class Industry:
    def __init__(self, store, media, vault, *, before_request=None):
        self.store, self.media, self.vault = store, media, vault
        self.before_request = before_request

    def connect(self, p, body: ConnectionInput):
        self.store.project(p)
        raise ValueError("新建行业连接已收敛至蝉妈妈 CMM 设置；不再使用旧来源或手工响应映射")

    def connection_view(self, c):
        return {
            **public(c),
            "search_enabled": False,
            "disabled_reason": "历史来源只读；新任务请使用统一蝉妈妈 CMM 服务",
            "legacy": True,
        }

    @staticmethod
    def _require(rights, actions):
        r = SourceRights.model_validate(rights)
        if r.revoked: raise ValueError("来源授权已撤销")
        if r.requires_active_subscription and (r.subscription_valid_until is None or r.subscription_valid_until.tzinfo is None or r.subscription_valid_until <= datetime.now(UTC)):
            raise ValueError("此内容需要有效的持续订阅授权")
        if r.expires_at <= datetime.now(UTC):
            raise ValueError("来源授权已到期，禁止继续查询、选择或导入")
        if any(not getattr(r, action) for action in actions):
            raise ValueError("来源缺少本次操作的明确授权：" + ", ".join(actions))

    @staticmethod
    def _adapter_ready(c):
        if c["provider"] == "cmm":
            mapping = c.get("response_mapping")
            if not mapping or not mapping.get("reviewed"):
                raise ValueError("CMM 缺少已核对的正式响应 schema 映射；搜索未接通")
            ResponseMapping.model_validate(mapping)

    def update_rights(self, p, cid, body: SourceRights):
        c = self.store.get(p, "industry_connection", cid)
        c["rights"] = body.model_dump(mode="json")
        return self.connection_view(self.store.put(p, "industry_connection", c, cid))

    def create_search(self, p, body: SearchPlanInput):
        self.store.project(p)
        raise ValueError("新建行业任务仅支持统一蝉妈妈 CMM 服务；旧来源历史只读")

    def _job(self, p, jid):
        j = self.store.job(p, jid)
        if j["kind"] != "industry_search":
            raise NotFound("行业查询任务不存在")
        return j

    def search(self, p, jid):
        j = self._job(p, jid)
        events = self.store.events(p, jid)
        refs = [r for r in self.store.list(p, "industry_reference") if r["job_id"] == jid]
        progress = {
            "calls_attempted": sum(e["type"] == "industry_call" for e in events),
            "rows_received": sum(e["data"]["count"] for e in events if e["type"] == "industry_rows"),
            "results_saved": sum(r["job_id"] == jid for r in self.store.list(p, "industry_result")),
            "selected": len(refs),
            "media_imported": sum(bool(r.get("asset_id")) for r in refs),
        }
        try:
            self._current(p, j, ["retain_metadata"])
        except ValueError:
            return {
                **{
                    k: j[k]
                    for k in (
                        "id",
                        "project_id",
                        "kind",
                        "state",
                        "error",
                        "quote",
                        "actual_cost",
                        "created_at",
                        "updated_at",
                    )
                },
                "content_restricted": True,
                "progress": progress,
            }
        return {
            **public(j),
            "plan": public(j["payload"]["plan"]),
            "stop_reason": (j.get("result") or {}).get("stop_reason"),
            "progress": progress,
        }

    def results(self, p, jid):
        self._current(p, self._job(p, jid), ["retain_metadata"])
        return [public(r) for r in self.store.list(p, "industry_result") if r["job_id"] == jid]

    def overview(self, p):
        references = []
        for ref in self.store.list(p, "industry_reference"):
            try:
                self._current(p, self._job(p, ref["job_id"]), ["retain_metadata"])
                references.append(public(ref))
            except ValueError:
                references.append({"id": ref["id"], "job_id": ref["job_id"], "content_restricted": True})
        return {
            "catalog": [
                {**c, "response_mapping_required": False, "legacy": True}
                for c in CATALOG
                if c["provider"] == "cmm"
            ],
            "connections": [self.connection_view(c) for c in self.store.list(p, "industry_connection")],
            "search_jobs": [
                self.search(p, j["id"]) for j in self.store.jobs(p) if j["kind"] == "industry_search"
            ],
            "references": references,
        }

    def _current(self, p, j, actions):
        snap = j["payload"]["connection_snapshot"]
        current = self.store.get(p, "industry_connection", snap["id"])
        self._require(snap["rights"], actions)
        self._require(current["rights"], actions)
        if not set(actions) <= set(j["payload"]["plan"]["allowed_actions"]):
            raise ValueError("操作超出不可变查询计划允许的权限")
        return current

    def run(self, p, jid, *, client=None):
        self._job(p, jid)
        raise ValueError("旧行业任务仅保留历史；新任务请使用统一蝉妈妈 CMM 服务")

    def _normalize(self, c, row, p, jid, plan):
        if not isinstance(row, dict):
            raise ValueError("来源返回非对象记录")  # noqa: TRY004 - invalid external contract
        provider = c["provider"]
        source = c["operation"]
        metrics = {
            name: metric(source=source)
            for name in ("likes", "comments", "shares", "views", "sales", "revenue", "audience")
        }
        category = None
        if provider == "datax" and c["operation"] == DATAX_DOUYIN_OP:
            item = row
            identity, title, url = item.get("id"), item.get("title") or item.get("text"), item.get("url")
            entity = {"type": "creator", "id": path(item, "author.id"), "name": path(item, "author.name")}
            published = timestamp(item.get("published_at"))
            period = {"start": None, "end": None, "basis": "cumulative_at_fetch"}
            # The post schema provides image URLs but no video download field. A cover is not the video.
            urls = item.get("images") or [] if item.get("type") == "image" else []
            media_url = next((u for u in urls if isinstance(u, str)), None)
            for name in ("likes", "comments", "shares", "views"):
                approx = next(
                    (x for x in item.get("approximate") or [] if x and x.get("field") == "metrics." + name),
                    None,
                )
                if approx and approx.get("kind") == "lower_bound":
                    metrics[name] = {
                        **metric(source=source),
                        "raw_value": path(item, "metrics." + name),
                        "qualifier": "lower_bound",
                    }
                else:
                    metrics[name] = metric(
                        path(item, "metrics." + name), kind="estimated" if approx else "exact", source=source
                    )
            metrics["index"] = metric(path(item, "ext.index"), kind="index", source=source)
            category = path(item, "ext.category")
        elif provider == "datax":
            item = row
            identity, title, url = item.get("id"), item.get("title"), item.get("url")
            entity = {
                "type": "advertiser",
                "id": path(item, "advertiser.id"),
                "name": path(item, "advertiser.name"),
            }
            published = timestamp(item.get("first_shown_at"))
            period = {"start": published, "end": timestamp(item.get("last_shown_at")), "basis": "ad_delivery"}
            urls = item.get("videos") or item.get("images") or []
            media_url = next((u for u in urls if isinstance(u, str)), None)
            for name in ("likes", "comments", "shares"):
                approx = next(
                    (x for x in item.get("approximate") or [] if x and x.get("field") == "ext." + name), None
                )
                if approx and approx.get("kind") == "lower_bound":
                    metrics[name] = {
                        **metric(source=source),
                        "raw_value": path(item, "ext." + name),
                        "qualifier": "lower_bound",
                    }
                else:
                    metrics[name] = metric(
                        path(item, "ext." + name), kind="estimated" if approx else "exact", source=source
                    )
            metrics["audience"] = metric(
                path(item, "ext.audience_range"), kind="range", source=source, unit="people"
            )
            category = path(item, "ext.category")
        elif provider == "tikhub":
            item = row.get("aweme_info")
            if not isinstance(item, dict):
                raise ValueError("TikHub 搜索记录缺少文档规定的 aweme_info")
            identity, title, url = item.get("aweme_id"), item.get("desc"), item.get("share_url")
            entity = {
                "type": "creator",
                "id": path(item, "author.uid"),
                "name": path(item, "author.nickname"),
            }
            published = timestamp(item.get("create_time"))
            period = {"start": None, "end": None, "basis": "cumulative_at_fetch"}
            urls = path(item, "video.play_addr.url_list", [])
            media_url = next((u for u in urls if isinstance(u, str)), None)
            for name, field in (
                ("likes", "digg_count"),
                ("comments", "comment_count"),
                ("shares", "share_count"),
                ("views", "play_count"),
            ):
                metrics[name] = metric(path(item, "statistics." + field), source=source)
        else:
            item, m = row, c["response_mapping"]
            identity, title, url = (
                path(item, m["id_path"]),
                path(item, m["title_path"]),
                path(item, m["url_path"]),
            )
            entity = {
                "type": "creator",
                "id": path(item, m["entity_id_path"]),
                "name": path(item, m["entity_name_path"]),
            }
            published = timestamp(path(item, m["published_path"]))
            period = {"start": plan["start_date"], "end": plan["end_date"], "basis": "query_period"}
            media_url = path(item, m["media_path"])
            for name, mapping in m["metrics"].items():
                metrics[name] = metric(
                    path(item, mapping["path"]), kind=mapping["kind"], unit=mapping["unit"], source=source
                )
        if not isinstance(identity, str) or not identity:
            raise ValueError("来源没有稳定字符串标识；禁止推测身份或合并")
        if title is not None and not isinstance(title, str):
            raise ValueError("来源标题类型未核验")
        return {
            "job_id": jid,
            "connection_id": c["id"],
            "provider": provider,
            "platform": c["platform"],
            "platform_id": identity,
            "title": title,
            "source_url": url,
            "entity": entity,
            "published_at": published,
            "category": category,
            "statistics_period": period,
            "fetched_at": now(),
            "metrics": metrics,
            "media_url": media_url,
            "raw": clean_raw(row),
            "provenance": {
                "operation": source,
                "docs": next(x["docs"] for x in CATALOG if x["provider"] == provider),
            },
            "rights_snapshot": c["rights"],
            "selection_reasons": [],
        }

    @staticmethod
    def _in_scope(row, plan):
        published = row["published_at"]
        # Missing dates cannot be represented as an in-range result.
        if not published or not plan["start_date"] <= published[:10] <= plan["end_date"]:
            return False
        if plan["category"] and row["provider"] == "datax":
            return row["category"] == plan["category"]
        return True

    def _insert(self, db, p, kind, data):
        record = {**data, "id": uid(), "project_id": p, "created_at": now(), "updated_at": now()}
        db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            (record["id"], p, kind, dumps(record), record["created_at"], record["updated_at"]),
        )
        return record

    def _save_result(self, p, data):
        with self.store.connection(True) as db:
            rows = db.execute("SELECT data FROM records WHERE project_id=? AND kind='industry_result'", (p,))
            for row in rows:
                old = json.loads(row["data"])
                if (old["job_id"], old["platform"], old["platform_id"]) == (
                    data["job_id"],
                    data["platform"],
                    data["platform_id"],
                ):
                    return old
            return self._insert(db, p, "industry_result", data)

    def select(self, p, jid, body: SelectionInput, *, media_client=None):
        raise ValueError("历史来源只读；请使用统一蝉妈妈 CMM 选择参考")

    def _download_licensed_media(self, p, job, row, actions, client):
        owned = client is None
        client = client or httpx.Client(timeout=60, follow_redirects=False, trust_env=False)
        token = uid()
        temp = self.media.root / (token + ".part")
        url = row["media_url"]
        try:
            for _ in range(5):
                self._current(p, job, actions)
                address = validate_remote_url(url)
                parsed = urlsplit(url)
                pinned = httpx.URL(url).copy_with(host=address)
                if self.before_request:
                    self.before_request()
                with client.stream(
                    "GET",
                    pinned,
                    headers={"Host": parsed.netloc},
                    extensions={"sni_hostname": parsed.hostname},
                ) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    extension = {
                        "video/mp4": ".mp4",
                        "video/webm": ".webm",
                        "video/quicktime": ".mov",
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                    }.get(response.headers.get("content-type", "").split(";")[0])
                    if extension is None:
                        raise ValueError("来源未返回实际媒体文件")
                    count = 0
                    with temp.open("wb") as output:
                        for chunk in response.iter_bytes(65536):
                            count += len(chunk)
                            if count > self.media.MAX_FILE_BYTES:
                                raise ValueError("来源媒体超过大小限制")
                            output.write(chunk)
                # The response may finish after revocation. Do not ingest its bytes without a new check.
                self._current(p, job, actions)
                destination = temp.with_suffix(extension)
                temp.rename(destination)
                try:
                    asset = self.media.ingest(
                        p,
                        destination,
                        source_kind="industry",
                        title=row["title"] or row["platform_id"],
                        metadata={
                            "source_url": provenance_url(url),
                            "rights_confirmed": True,
                            "industry_import_token": token,
                        },
                    )
                    try:
                        self._current(p, job, actions)
                    except ValueError:
                        if asset["metadata"].get("industry_import_token") == token:
                            self._discard_new_media(p, asset)
                        raise
                    return asset, token
                finally:
                    destination.unlink(missing_ok=True)
            raise ValueError("来源媒体重定向次数过多")
        finally:
            temp.unlink(missing_ok=True)
            if owned:
                client.close()

    def _discard_new_media(self, p, asset):
        # Called only with a unique import ownership token; never delete an existing deduplicated asset.
        directory = Path(asset["path"]).parent.resolve()
        if not directory.is_relative_to(self.media.root / p):
            raise ValueError("新导入媒体路径越界")
        for scene in self.store.list(p, "scene"):
            if scene["asset_id"] == asset["id"]:
                self.store.delete(p, "scene", scene["id"])
        self.store.delete(p, "asset", asset["id"])
        shutil.rmtree(directory)

    def _attach_asset_source(self, p, job, asset, row, ref, actions):
        with self.store.connection(True) as db:
            self._current(p, job, actions)
            saved = json.loads(
                db.execute(
                    "SELECT data FROM records WHERE id=? AND project_id=? AND kind='asset'", (asset["id"], p)
                ).fetchone()["data"]
            )
            metadata = saved["metadata"]
            sources = metadata.setdefault("industry_sources", [])
            if not any(x["reference_id"] == ref["id"] for x in sources):
                sources.append(
                    {
                        "reference_id": ref["id"],
                        "result_id": row["id"],
                        "connection_id": row["connection_id"],
                        "platform": row["platform"],
                        "platform_id": row["platform_id"],
                        "provenance": row["provenance"],
                        "rights_snapshot": ref["rights_snapshot"],
                        "selection_reason": ref["selection_reason"],
                    }
                )
            metadata["industry_reference_ids"] = list(
                dict.fromkeys(
                    [
                        *metadata.get("industry_reference_ids", []),
                        *(
                            [metadata["industry_reference_id"]]
                            if metadata.get("industry_reference_id")
                            else []
                        ),
                        ref["id"],
                    ]
                )
            )
            for key, value in {
                "industry_reference_id": ref["id"],
                "source_result_id": row["id"],
                "provenance": row["provenance"],
                "rights_snapshot": ref["rights_snapshot"],
                "selection_reason": ref["selection_reason"],
                "platform_id": row["platform_id"],
                "platform": row["platform"],
            }.items():
                metadata.setdefault(key, value)
            saved["updated_at"] = now()
            db.execute(
                "UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?",
                (dumps(saved), saved["updated_at"], asset["id"], p),
            )

    def validate_asset_access(self, p, aid, *, export=False):
        asset = self.store.get(p, "asset", aid)
        metadata = asset.get("metadata", {})
        refs = metadata.get("industry_reference_ids") or [metadata.get("industry_reference_id")]
        refs = [r for r in refs if r]
        if asset.get("source_kind") == "industry" and not refs:
            raise ValueError("行业媒体缺少可核验的授权来源")
        for rid in refs:
            ref = self.store.get(p, "industry_reference", rid)
            if ref.get("asset_id") != aid:
                raise ValueError("行业媒体与来源引用不一致")
            self._current(
                p,
                self._job(p, ref["job_id"]),
                ["retain_metadata", "retain_media"] + (["export_media"] if export else []),
            )
        return True

    def validate_model_use(self, p, *, asset_ids=(), reference_ids=(), derivative=False):
        """Call at every model-tool boundary, including followups, generation and embeddings."""
        refs = list(reference_ids)
        for aid in asset_ids:
            asset = self.store.get(p, "asset", aid)
            metadata = asset.get("metadata", {})
            related = metadata.get("industry_reference_ids") or [metadata.get("industry_reference_id")]
            related = [r for r in related if r]
            if asset.get("source_kind") == "industry" and not related:
                raise ValueError("行业媒体缺少可核验来源授权，不允许模型处理")
            refs.extend(related)
        for rid in set(refs):
            ref = self.store.get(p, "industry_reference", rid)
            job = self._job(p, ref["job_id"])
            actions = ["retain_metadata", "model_process"] + (["derivative_create"] if derivative else [])
            if ref.get("asset_id"):
                actions.append("retain_media")
            self._current(p, job, actions)
        return True

    def validate_export(self, p):
        """Guard whole-project export: snapshots and retained source metadata also require export rights."""
        self.store.project(p)
        from .cmm import CMMService

        CMMService(self.store, self.vault).validate_export(p)
        for job in self.store.jobs(p):
            if job["kind"] == "industry_search":
                self._current(p, job, ["export_metadata"])
        for ref in self.store.list(p, "industry_reference"):
            if ref.get("asset_id"):
                self._current(p, self._job(p, ref["job_id"]), ["export_metadata", "export_media"])
        return True
