"""Pinned 2026-10-14 official request contracts; unknown output shapes fail closed."""

import ipaddress
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

VERSION = "2026-10-14"
SEARCH = {
    "commerce": "video_commerce_video_library_custom_search_top_selling_video",
    "popular": "video_library_custom_search_video",
    "qianchuan": "video_library_custom_search",
}
TOOLS = {"extract_video_copywriting", "video_storyboard", "video_understanding"}
SCHEMAS = {
    SEARCH["commerce"]: (
        {
            "keyword",
            "create_start_time",
            "create_end_time",
            "category_id",
            "page",
            "sort",
            "durations",
            "follower_count",
            "digg",
            "product_amount",
            "product_volume",
            "product_price",
            "blue_words",
            "is_ai",
            "is_star_aweme",
            "aweme_graph_type",
            "is_hot",
            "is_cooperation_aweme",
            "is_live_author",
        },
        {"create_start_time", "create_end_time"},
    ),
    SEARCH["popular"]: (
        {
            "keyword",
            "create_start_time",
            "create_end_time",
            "star_category",
            "page",
            "sort",
            "durations",
            "follower_count",
            "digg",
            "gender_type",
            "age_types",
            "province",
            "blue_words",
            "is_ai",
            "is_star_aweme",
            "aweme_graph_type",
            "is_hot",
            "is_cooperation_aweme",
            "is_live_author",
        },
        {"create_start_time", "create_end_time"},
    ),
    SEARCH["qianchuan"]: (
        {"keyword", "start_date", "end_date", "category_id", "page", "sort_field", "type"},
        {"start_date", "end_date"},
    ),
    "product_category_search": ({"keyword"}, {"keyword"}),
    "author_category_search": ({"keyword"}, {"keyword"}),
    "video_search": ({"keyword"}, {"keyword"}),
    "video_detail_info": ({"aweme_id"}, {"aweme_id"}),
    "extract_video_copywriting": ({"video_url", "video_id"}, {"video_url"}),
    "video_storyboard": ({"video_url", "video_id"}, set()),
    "video_understanding": ({"video_urls", "custom_prompt"}, {"video_urls"}),
    "video_understanding_task_info": ({"task_id"}, {"task_id"}),
}


LIVE_RESPONSE_VERSION = "2026-10-24-chinese-video-v1"
VERIFIED_DATA_OPERATIONS = {SEARCH["commerce"], SEARCH["popular"], "video_search", "video_detail_info"}
RESPONSE_SCHEMA_READY = {
    op: op in TOOLS | {"video_understanding_task_info"} | VERIFIED_DATA_OPERATIONS for op in SCHEMAS
}
SCHEMA_GAP = "需完成首次账号返回格式验证；官方未公开此操作完整响应 schema，禁止预先收费调用"


def share_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("视频地址无效")
    u = urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in {None, 443}:
        raise ValueError("视频链接需要无凭证的 HTTPS 公网地址")
    h = u.hostname.lower()
    if h == "localhost" or "." not in h or h.endswith((".local", ".internal", ".localhost")):
        raise ValueError("禁止内网视频地址")
    try:
        if not ipaddress.ip_address(h).is_global:
            raise ValueError("禁止内网视频地址")
    except ValueError as exc:
        if str(exc) == "禁止内网视频地址":
            raise
    return value


def validate_query(operation, query):
    if operation not in SCHEMAS or not isinstance(query, dict):
        raise ValueError("未支持的 CMM operation/schema")
    allowed, required = SCHEMAS[operation]
    if set(query) - allowed or any(k not in query or query[k] in (None, "") for k in required):
        raise ValueError("CMM 参数与官方 schema 不符")
    if operation == "video_storyboard" and not (query.get("video_url") or query.get("video_id")):
        raise ValueError("分镜需要视频链接或蝉妈妈视频 ID")
    if "video_url" in query:
        share_url(query["video_url"])
    if operation == "video_understanding":
        urls = query["video_urls"]
        if not isinstance(urls, list) or not 1 <= len(urls) <= 20:
            raise ValueError("画面理解需要非空链接数组，最多20条")
        for url in urls:
            share_url(url)
        if "custom_prompt" in query and (
            not isinstance(query["custom_prompt"], str) or len(query["custom_prompt"]) > 10000
        ):
            raise ValueError("自定义要求无效")
    if operation in SEARCH.values():
        a, b = (
            ("start_date", "end_date")
            if operation == SEARCH["qianchuan"]
            else ("create_start_time", "create_end_time")
        )
        if date.fromisoformat(query[a]) > date.fromisoformat(query[b]):
            raise ValueError("开始日期不得晚于结束日期")
        if "page" in query and (type(query["page"]) is not int or not 1 <= query["page"] <= 100):
            raise ValueError("页码需要为1–100的整数")
    for key in ("keyword", "video_id", "aweme_id", "task_id"):
        if key in query and (
            not isinstance(query[key], str) or not query[key].strip() or len(query[key]) > 2000
        ):
            raise ValueError("CMM 字符串参数无效")
    return query


def permission_payload(response):
    d = response.get("data") if isinstance(response, dict) else None
    if (
        not isinstance(d, dict)
        or not isinstance(d.get("rights"), dict)
        or not (
            type(d.get("group_id")) is int or isinstance(d.get("group_id"), str) and d["group_id"].isdigit()
        )
        or not isinstance(d.get("version"), str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["version"])
    ):
        raise ValueError("CMM permission schema 未核验；所有能力保持关闭")
    return d


def _reported_metric(value, *, kind="exact", unit=""):
    base = {"kind": "missing", "value": None, "source": "cmm", "unit": unit, "object_type": "video"}
    if value is None or value == "" or value in ("--", "-", "暂无"):
        return base
    if kind == "range":
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError("CMM metric schema 无效")  # noqa: TRY004 - upstream schema failure maps to request validation
        # Preserve the supplier's range/censored text; do not invent exact sales.
        return {**base, "kind": "range", "raw_value": str(value), "lower": None, "upper": None}
    if kind == "index":
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("CMM index schema 无效") from None
        if not number.is_finite() or number < 0:
            raise ValueError("CMM index schema 无效")
        return {**base, "kind": "index", "value": str(value), "raw_value": str(value)}
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("CMM metric schema 无效")  # noqa: TRY004 - upstream schema failure maps to request validation
    return {**base, "kind": kind, "value": value}


def _video_item(row):
    if not isinstance(row, dict) or not isinstance(row.get("视频信息"), dict):
        raise ValueError("CMM video record schema 未核验")
    video = row["视频信息"]
    identifier = video.get("视频ID")
    title = video.get("视频标题")
    if not isinstance(identifier, str) or not identifier or not isinstance(title, str):
        raise ValueError("CMM video identity schema 未核验")
    author = row.get("达人信息", {})
    product = row.get("带货商品信息", {})
    if not isinstance(author, dict) or not isinstance(product, dict):
        raise ValueError("CMM entity schema 未核验")
    link = video.get("抖音播放地址")
    platform_id = None
    if link:
        if not isinstance(link, str):
            raise ValueError("CMM video URL schema 未核验")
        parsed = urlsplit(link)
        match = re.fullmatch(r"/(?:share/)?video/(\d+)/?", parsed.path)
        if parsed.scheme == "https" and parsed.hostname in {"www.iesdouyin.com", "www.douyin.com"} and match:
            platform_id = match[1]
    source = (
        f"https://www.douyin.com/video/{platform_id}" if platform_id
        else f"https://www.chanmama.com/awemeRank/{quote(identifier, safe='')}.html"
    )
    metrics = {
        name: _reported_metric(video.get(field), kind=kind, unit=unit)
        for name, field, kind, unit in [
            ("likes", "点赞数", "exact", "次"),
            ("comments", "评论数", "exact", "次"),
            ("shares", "分享数", "exact", "次"),
            ("favorites", "收藏数", "exact", "次"),
            ("sales", "视频销量", "range", "件"),
            ("revenue", "视频销售额", "range", "元"),
            ("sales_index", "视频销量指数", "index", "指数"),
            ("revenue_index", "视频销售额指数", "index", "指数"),
            ("estimated_exposure", "预估曝光", "estimated", "次"),
        ]
    }
    return {
        "id": f"cmm:video:{identifier}", "supplier_video_id": identifier,
        "platform": "douyin", "platform_video_id": platform_id, "entity_type": "video",
        "title": title or None, "author": author.get("达人名称"), "author_id": author.get("达人ID"),
        "product_id": product.get("商品ID"), "product_title": product.get("商品标题"),
        "published_at": video.get("视频发布时间"), "duration": video.get("视频时长"),
        "source_url": source, "metrics": metrics, "content_basis": "metadata",
        "text": None, "media_url": None,
        "response_schema_version": LIVE_RESPONSE_VERSION,
    }


def _data_response(operation, data):
    if not isinstance(data, dict) or "data" not in data:
        raise ValueError("CMM data envelope schema 未核验")
    body = data["data"]
    if operation == "video_detail_info":
        if not isinstance(body, dict) or not isinstance(body.get("视频信息"), dict):
            raise ValueError("CMM detail schema 未核验")
        detail = body["视频信息"]
        milliseconds = detail.get("视频时长（毫秒）")
        if type(milliseconds) not in {int, float} or not math.isfinite(milliseconds) or milliseconds < 0:
            raise ValueError("CMM detail duration schema 未核验")
        video = {**detail, "视频ID": detail.get("视频id"),
                 "视频发布时间": detail.get("发布时间"), "视频时长": milliseconds / 1000}
        author = body.get("达人信息", {})
        if not isinstance(author, dict):
            raise ValueError("CMM detail author schema 未核验")  # noqa: TRY004
        item = _video_item({"视频信息": video, "达人信息": {**author, "达人ID": author.get("达人id")}})
        products = detail.get("带货商品")
        item["products"] = None
        if products is not None:
            if not isinstance(products, list) or any(not isinstance(p, dict) or not isinstance(p.get("商品ID"), str) or not isinstance(p.get("商品名称"), str) for p in products):
                raise ValueError("CMM detail product schema 未核验")
            item["products"] = [{"supplier_product_id": p["商品ID"], "title": p["商品名称"]} for p in products]
        text = body.get("视频脚本文案")
        if text is not None and not isinstance(text, str):
            raise ValueError("CMM detail transcript schema 未核验")
        if text and text.strip():
            item.update(text=text, content_basis="supplier_transcript")
        return {"results": [item], "content_basis": item["content_basis"], "response_schema_version": LIVE_RESPONSE_VERSION}
    if operation == "video_search":
        if not isinstance(body, list):
            raise ValueError("CMM lookup list schema 未核验")  # noqa: TRY004 - upstream schema failure maps to request validation
        result = []
        for row in body:
            if not isinstance(row, dict) or not isinstance(row.get("video_id"), str) or not row["video_id"] or not isinstance(row.get("desc"), str):
                raise ValueError("CMM lookup identity schema 未核验")
            result.append({"id": "cmm:video:" + row["video_id"], "supplier_video_id": row["video_id"],
                           "title": row["desc"] or None, "content_basis": "metadata", "metrics": {}, "text": None})
        return {"results": result, "content_basis": "metadata", "response_schema_version": LIVE_RESPONSE_VERSION}
    if not isinstance(body, dict) or not isinstance(body.get("list"), list) or not isinstance(body.get("分页信息"), dict):
        raise ValueError("CMM video list schema 未核验")
    paging = body["分页信息"]
    if any(type(paging.get(k)) is not int or paging[k] < (1 if k in {"page", "size"} else 0)
           for k in ("page", "size", "totalCount", "totalPage")):
        raise ValueError("CMM pagination schema 未核验")
    return {"results": [_video_item(row) for row in body["list"]], "content_basis": "metadata",
            "pagination": {"page": paging["page"], "size": paging["size"], "total_count": paging["totalCount"], "total_pages": paging["totalPage"]},
            "response_schema_version": LIVE_RESPONSE_VERSION}


def parse_response(operation, response):
    """No list-envelope guesswork: only formats explicitly stated in official references."""
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0:
        raise ValueError("CMM code 非成功，禁止按成功解析")
    if "data" not in response:
        raise ValueError("CMM response schema 缺少 data")
    data = response["data"]
    if operation in VERIFIED_DATA_OPERATIONS:
        return _data_response(operation, data)
    if operation == "extract_video_copywriting":
        if not isinstance(data, str) or not data.strip():
            raise ValueError("CMM copywriting schema 应为非空纯文本")
        return {"text": data, "content_basis": "transcript"}
    if operation == "video_storyboard":
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("video_title"), str)
            or not isinstance(data.get("video_url"), str)
            or type(data.get("duration")) not in {int, float}
            or not isinstance(data.get("result"), list)
        ):
            raise ValueError("CMM storyboard schema 未核验")
        if data["duration"] < 0:
            raise ValueError("CMM storyboard schema 时长无效")
        for scene in data["result"]:
            if (
                not isinstance(scene, dict)
                or type(scene.get("st")) not in {int, float}
                or type(scene.get("ed")) not in {int, float}
                or not 0 <= scene["st"] < scene["ed"] <= data["duration"]
                or not isinstance(scene.get("voice"), str)
                or not isinstance(scene.get("clip_video_url"), str)
            ):
                raise ValueError("CMM storyboard scene schema 未核验")
            share_url(scene["clip_video_url"])
        return {
            "title": data["video_title"],
            "scenes": data["result"],
            "duration": data["duration"],
            "text": "\n".join(f"[{x['st']}–{x['ed']}] {x['voice']}" for x in data["result"]),
            "content_basis": "storyboard_transcript",
        }
    if operation in {"video_understanding", "video_understanding_task_info"}:
        # Both locations are explicitly accepted by the official read-only helper.
        task = (
            data
            if isinstance(data, dict) and data.get("task_id")
            else data.get("data")
            if isinstance(data, dict)
            else None
        )
        if isinstance(task, dict) and operation == "video_understanding" and "status" not in task:
            task = {**task, "status": "pending"}
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("task_id"), str)
            or not task["task_id"]
            or task.get("status") not in {"pending", "running", "retrying", "succeeded", "failed"}
        ):
            raise ValueError("CMM task schema 未核验")
        if task["status"] == "succeeded" and (
            not isinstance(task.get("result"), str) or not task["result"].strip()
        ):
            raise ValueError("CMM task result schema 应为 Markdown 字符串")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "text": task.get("result") if task["status"] == "succeeded" else None,
            "content_basis": "visual_analysis",
        }
    raise ValueError("CMM response schema 尚未公开或实账核验，禁止猜测为空结果；请完成真实账户响应核验")
