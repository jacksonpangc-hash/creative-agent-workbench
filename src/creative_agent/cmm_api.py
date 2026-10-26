"""CMM routes share the parent app authentication and external-call policy."""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import BackgroundTasks
from pydantic import Field

from .cmm import CMMService
from .contracts import Contract
from .industry import SourceRights


class Connection(Contract):
    api_key: str = Field(min_length=1, max_length=8192)
    label: str = Field(default="蝉妈妈", min_length=1, max_length=100)


class Intent(Contract):
    intent: str = Field(min_length=1, max_length=1000)


class PointBudget(Contract):
    limit_points: Decimal = Field(ge=0, allow_inf_nan=False)


class Preflight(Contract):
    operation: str
    query: dict


class Job(Preflight):
    max_calls: int = Field(ge=1, le=100)
    max_points: Decimal = Field(ge=0, allow_inf_nan=False)
    idempotency_key: str = Field(min_length=1, max_length=150)
    target_count: int = Field(default=20, ge=1, le=1000)


class Search(Contract):
    kind: Literal["commerce", "popular", "qianchuan"]
    keyword: str = Field(min_length=1, max_length=500)
    category: str = Field(default="", max_length=200)
    start_date: date
    end_date: date
    max_calls: int = Field(ge=1, le=100)
    max_points: Decimal = Field(ge=0, allow_inf_nan=False)
    target_count: int = Field(default=20, ge=1, le=1000)
    idempotency_key: str = Field(min_length=1, max_length=150)


class Selection(Contract):
    result_ids: list[str] = Field(min_length=1, max_length=100)


class Tariff(Contract):
    max_points_per_call: Decimal = Field(ge=0, allow_inf_nan=False)
    evidence: str = Field(min_length=1, max_length=2000)
    verified: bool = False


class ReferenceContent(Contract):
    kind: Literal["copywriting", "storyboard", "understanding"]


class ReferenceContentJob(ReferenceContent):
    max_calls: int = Field(ge=1, le=100)
    max_points: Decimal = Field(ge=0, allow_inf_nan=False)
    idempotency_key: str = Field(min_length=1, max_length=150)


class Category(Contract):
    keyword: str = Field(min_length=1, max_length=500)
    kind: Literal["product", "author"] = "product"
    idempotency_key: str = Field(min_length=1, max_length=150)


def register_cmm(app, store, vault, *, run_jobs=True, external_guard=None):
    cmm = CMMService(store, vault, before_request=external_guard)
    app.state.cmm = cmm
    prefix = "/api/projects/{p}/cmm"

    @app.get(prefix)
    def overview(p: str):
        return cmm.overview(p)

    @app.put(prefix + '/budget')
    def point_budget(p: str, body: PointBudget):
        return cmm.configure_budget(p, limit_points=body.limit_points)

    @app.put(prefix + "/connection")
    def connect(p: str, body: Connection):
        return cmm.connect(p, **body.model_dump())

    @app.patch(prefix + "/connection/rights")
    def rights(p: str, body: SourceRights):
        return cmm.update_rights(p, body.model_dump(mode="json"))

    @app.post(prefix + "/permissions")
    def permissions(p: str, body: Intent):
        return cmm.permissions(p, body.intent)

    @app.post(prefix + "/preflight")
    def preflight(p: str, body: Preflight):
        return cmm.preflight(p, body.operation, body.query)

    @app.put(prefix + "/tariffs/{operation}")
    def tariff(p: str, operation: str, body: Tariff):
        return cmm.configure_tariff(p, operation=operation, **body.model_dump())

    @app.post(prefix + "/jobs")
    def create(p: str, body: Job, background_tasks: BackgroundTasks):
        j = cmm.create_job(p, **body.model_dump())
        if run_jobs and j["state"] == "queued":
            background_tasks.add_task(cmm.run, p, j["id"])
        return j

    @app.post(prefix + "/searches")
    def search(p: str, body: Search, background_tasks: BackgroundTasks):
        query = cmm.search_query(**body.model_dump())
        j = cmm.create_job(
            p,
            **query,
            max_calls=body.max_calls,
            max_points=body.max_points,
            target_count=body.target_count,
            idempotency_key=body.idempotency_key,
        )
        if run_jobs and j["state"] == "queued":
            background_tasks.add_task(cmm.run, p, j["id"])
        return j

    @app.get(prefix + "/jobs/{jid}")
    def job(p: str, jid: str):
        return cmm.job(p, jid)

    @app.get(prefix + "/jobs/{jid}/results")
    def results(p: str, jid: str):
        return cmm.job(p, jid).get("results", [])

    @app.post(prefix + "/jobs/{jid}/run")
    def run(p: str, jid: str, background_tasks: BackgroundTasks):
        j = cmm.job(p, jid)
        if run_jobs and j["state"] == "queued":
            background_tasks.add_task(cmm.run, p, jid)
        return j

    @app.post(prefix + "/jobs/{jid}/poll")
    def poll(p: str, jid: str, background_tasks: BackgroundTasks):
        j = cmm.job(p, jid)
        if run_jobs and j["state"] in {"waiting", "needs_attention"} and j["provider_task_id"]:
            background_tasks.add_task(cmm.poll, p, jid)
        return j

    @app.post(prefix + "/jobs/{jid}/cancel")
    def cancel(p: str, jid: str):
        return cmm.cancel(p, jid)

    @app.post(prefix + "/jobs/{jid}/recover")
    def recover(p: str, jid: str):
        return cmm.recover(p, jid)

    @app.post(prefix + "/jobs/{jid}/select")
    def select(p: str, jid: str, body: Selection):
        return cmm.select(p, jid, body.result_ids)

    @app.get(prefix + "/references")
    def references(p: str):
        return cmm.references(p)

    @app.post(prefix + "/categories")
    def category(p: str, body: Category, background_tasks: BackgroundTasks):
        op = "author_category_search" if body.kind == "author" else "product_category_search"
        j = cmm.create_job(
            p,
            operation=op,
            query={"keyword": body.keyword},
            max_calls=1,
            max_points="0",
            idempotency_key=body.idempotency_key,
        )
        if run_jobs and j["state"] == "queued":
            background_tasks.add_task(cmm.run, p, j["id"])
        return j

    @app.post(prefix + "/references/{rid}/content/preflight")
    def content_preflight(p: str, rid: str, body: ReferenceContent):
        return cmm.reference_content(p, rid, kind=body.kind)

    @app.post(prefix + "/references/{rid}/content")
    def content_job(p: str, rid: str, body: ReferenceContentJob, background_tasks: BackgroundTasks):
        j = cmm.create_reference_content(p, rid, **body.model_dump())
        if run_jobs and j["state"] == "queued":
            background_tasks.add_task(cmm.run, p, j["id"])
        return j

    return cmm
