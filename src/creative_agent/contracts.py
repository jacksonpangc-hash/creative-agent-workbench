from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal["own", "inspiration", "raw", "finished", "account", "pushed", "industry"]
AnalysisMode = Literal["directed", "exploration", "inspiration"]
Currency = Literal["CNY", "USD"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("需要有效数字") from exc
    if not result.is_finite():
        raise ValueError("不允许 NaN 或无限值")
    return result


class EvidenceRef(Contract):
    asset_id: str
    scene_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    observation: str = ""
    provenance: Literal["metadata", "subtitle", "image", "sampled_frames", "native_video", "report"] = "metadata"

    @model_validator(mode="after")
    def check_interval(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("证据结束时间必须晚于开始时间")
        return self


class AnalysisScope(Contract):
    project_id: str = Field(min_length=1)
    source_kinds: list[SourceKind] = Field(default_factory=lambda: ["own", "inspiration", "raw", "finished", "account", "pushed", "industry"])
    asset_ids: list[str] = Field(default_factory=list, max_length=200)
    reference_ids: list[str] = Field(default_factory=list, max_length=200)
    selected_segments: list[EvidenceRef] = Field(default_factory=list,max_length=200)
    group_ids: list[str] = Field(default_factory=list, max_length=20)
    discovery: bool = False
    date_basis: Literal['published', 'imported', 'statistics'] = 'published'
    start_date: date | None = None
    end_date: date | None = None
    query: str = ""
    min_spend: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    min_roas: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    account_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_dates(self):
        if any(r.asset_id not in self.asset_ids for r in self.selected_segments):raise ValueError('片段素材必须在当前所选范围内')
        if len(self.asset_ids) + len(self.reference_ids) > 200:
            raise ValueError("素材和行业参考合计不能超过200条")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("开始日期不得晚于结束日期")
        return self

    def allows(self, asset: dict, statistics_dates=None) -> bool:
        if asset.get("project_id") != self.project_id or asset.get("source_kind") not in self.source_kinds:
            return False
        if not self.discovery and asset.get("id") not in self.asset_ids:
            return False
        if self.account_ids and asset.get("metadata", {}).get("account_id") not in self.account_ids:
            return False
        if self.start_date or self.end_date:
            dates = statistics_dates if self.date_basis == 'statistics' else [
                asset.get('created_at') if self.date_basis == 'imported' else asset.get('metadata', {}).get('published_at')]
            valid = []
            for value in dates or []:
                try: valid.append(date.fromisoformat(str(value)[:10]))
                except ValueError: continue
            if not any((not self.start_date or d >= self.start_date) and (not self.end_date or d <= self.end_date) for d in valid):
                return False
        return True




class Metric(Contract):
    kind: Literal["exact", "range", "estimated", "index", "missing"] = "missing"
    value: Decimal | None = Field(default=None, allow_inf_nan=False)
    lower: Decimal | None = Field(default=None, allow_inf_nan=False)
    upper: Decimal | None = Field(default=None, allow_inf_nan=False)
    unit: str = ""
    source: str = ""

    @model_validator(mode="after")
    def validate_metric(self):
        if self.kind in {"exact", "estimated", "index"} and self.value is None:
            raise ValueError("该指标必须提供数值")
        if self.kind == "range" and (self.lower is None or self.upper is None or self.lower > self.upper):
            raise ValueError("需要有效的上下界")
        if self.kind == "missing" and any(v is not None for v in (self.value, self.lower, self.upper)):
            raise ValueError("缺失指标不能包含数值")
        return self

    def display(self):
        if self.kind == "missing":
            return "缺失"
        value = f"{self.lower:f}–{self.upper:f}" if self.kind == "range" else f"{self.value:f}"
        return f"{value} {self.unit}".strip()


class GenerationRequest(Contract):
    project_id: str
    brief_id: str | None = None
    script_id: str | None = None
    script_version: int | None = Field(default=None, ge=1)
    reference_asset_id: str
    reference_video_url: str | None = Field(default=None, max_length=8192)
    reference_url_confirmed: bool = False
    reference_image_ids: list[str] = Field(default_factory=list, max_length=6)
    reference_start_ms: int = Field(default=0, ge=0)
    reference_duration: float = Field(default=15, gt=0, le=15, allow_inf_nan=False)
    prompt: str = ""
    mode: Literal["faithful", "reasoning"] = "faithful"
    duration: int = Field(default=5, ge=1, le=15)
    batch: int = Field(default=1, ge=1, le=8)
    ratio: Literal["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"] = "9:16"
    resolution: str = "720p"
    audio: bool = True


    @model_validator(mode="after")
    def script_pair(self):
        if self.reference_video_url and not self.reference_url_confirmed:
            raise ValueError("请确认公开链接对应当前选择的参考片段，并已获准发送给生成供应商")
        if bool(self.script_id) != (self.script_version is not None):
            raise ValueError("脚本与版本必须同时指定")
        return self


class CostQuote(Contract):
    # None is unknown external pricing; zero remains a known free/local action.
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: Currency = "CNY"
    basis: str = Field(min_length=1)


class ModelBinding(Contract):
    id: str = ""
    provider: str
    label: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=180)
    role: Literal["strategy", "image_input", "video_input", "video_generation", "image_generation", "embedding", "transcription", "speech"]
    region: str = ""
    base_url: str = ""
    protocol: Literal["", "openai", "anthropic", "google", "dashscope", "ark", "minimax_video", "kling_omni"] = ""
    price_input_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_output_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_video_second: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    unpriced_task_limit: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: Currency = "CNY"
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    enabled: bool = True

    @model_validator(mode="after")
    def check_role(self):
        require_capability(self.provider, self.role)
        from .model_catalog import check_connection
        self.protocol, self.base_url = check_connection(self.provider, self.role, self.protocol, self.base_url)
        return self


def provider_catalog() -> list[dict]:
    """Documented capability ceilings; individual model/account access still needs validation."""
    common = ["strategy", "image_input"]
    entries = [
        ("alibaba", "阿里云百炼", "中国", common + ["video_input", "video_generation", "image_generation", "embedding"], "dashscope", "https://dashscope.aliyuncs.com", "", "地区与模型分别开通"),
        ("volcengine", "火山引擎", "中国", common + ["video_input", "video_generation", "image_generation", "embedding"], "ark", "https://ark.cn-beijing.volces.com/api/v3", "", "填写账户模型或接入点；人物素材另需授权"),
        ("zhipu", "智谱", "中国", common + ["video_input", "video_generation", "image_generation", "embedding"], "openai", "https://open.bigmodel.cn/api/paas/v4", "glm-4.6v", "生成型号和限制需分别核验"),
        ("minimax", "MiniMax", "中国", common + ["video_input", "video_generation", "speech"], "anthropic", "https://api.minimax.io/anthropic", "MiniMax-M3", "M3.1 Preview 不作为普通 API 默认；H3 另选生成接口"),
        ("kimi", "Kimi", "中国", common + ["video_input"], "openai", "https://api.moonshot.cn/v1", "kimi-k2.6", "可理解视频；不提供视频生成入口"),
        ("deepseek", "DeepSeek", "中国", common, "openai", "https://api.deepseek.com", "deepseek-flash", "图片理解；不标为原生视频理解"),
        ("openai", "OpenAI", "美国", common + ["image_generation", "embedding", "transcription", "speech"], "openai", "https://api.openai.com/v1", "", "Sora/Videos API 已于 2026-09-24关闭"),
        ("google", "Google", "美国", common + ["video_input", "video_generation", "image_generation", "embedding"], "google", "https://generativelanguage.googleapis.com/v1beta", "gemini-3.8-flash", "视频理解与生成分别选择模型/接口"),
        ("anthropic", "Anthropic", "美国", common, "anthropic", "https://api.anthropic.com/v1", "", "通过帧图像分析，不标为原生视频输入"),
        ("kling", "可灵 Kling", "中国", ["video_generation"], "kling_omni", "https://api-singapore.klingai.com", "kling-video-o1", "O1视频参考需要客户HTTPS片段URL；有视频时最多4图、无声、3–10秒；不自动上传"),
    ]
    from .model_catalog import model_profiles
    return [dict(id=i, name=n, country=c, capabilities=roles, protocol=proto, base_url=url, models=model_profiles(i),
                 example_model=model, note=note, verified_at="2026-10-19", live_verified=False)
            for i, n, c, roles, proto, url, model, note in entries]


def require_capability(provider: str, capability: str):
    entry = next((p for p in provider_catalog() if p["id"] == provider), None)
    if not entry or capability not in entry["capabilities"]:
        raise ValueError(f"{provider} 不支持已核验的 {capability} 用途")


def aggregate_metrics(rows: list[dict]) -> dict:
    currencies = {r.get("currency", "CNY") for r in rows if r.get("spend") is not None or r.get("revenue") is not None}
    if len(currencies) > 1:
        raise ValueError("不能直接合计不同币种")
    currency = next(iter(currencies), "CNY")
    totals = {k: Decimal(0) for k in ("spend", "revenue", "impressions", "clicks", "conversions")}
    counts = {k: 0 for k in totals}
    pairs = {"roas": [Decimal(0), Decimal(0), 0], "ctr": [Decimal(0), Decimal(0), 0],
             "cvr": [Decimal(0), Decimal(0), 0]}
    missing_rows = 0
    for row in rows:
        present = {k: decimal(row[k]) for k in totals if row.get(k) is not None}
        if not present:
            missing_rows += 1
        for key, value in present.items():
            if value < 0:
                raise ValueError("投放指标不能为负数")
            totals[key] += value
            counts[key] += 1
        for name, num, den in (("roas", "revenue", "spend"), ("ctr", "clicks", "impressions"), ("cvr", "conversions", "clicks")):
            if num in present and den in present:
                pairs[name][0] += present[num]
                pairs[name][1] += present[den]
                pairs[name][2] += 1
    result = {k: Metric(kind="exact", value=v, unit=currency if k in {"spend", "revenue"} else "").model_dump(mode="json")
              if counts[k] else Metric().model_dump(mode="json") for k, v in totals.items()}
    for name, (num, den, count) in pairs.items():
        result[name] = (Metric(kind="exact", value=num / den).model_dump(mode="json")
                        if count and den > 0 else Metric().model_dump(mode="json"))
        result[name]["paired_rows"] = count
    result.update(missing_rows=missing_rows, rows=len(rows), currency=currency)
    return result

