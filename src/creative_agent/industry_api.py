"""Authenticated historical industry reads and the unified CMM service."""

from fastapi.responses import JSONResponse

from .industry import ConnectionInput, Industry, SearchPlanInput, SelectionInput, SourceRights
from .store import Conflict, NotFound


def register_industry(app, store, media, vault, *, run_jobs=True, external_guard=None):
    industry = Industry(store, media, vault, before_request=external_guard)
    app.state.industry = industry
    if ValueError not in app.exception_handlers:

        @app.exception_handler(ValueError)
        async def industry_error(request, exc):
            return JSONResponse(
                {"detail": str(exc)},
                status_code=404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400,
            )

    @app.get("/api/projects/{p}/industry")
    def overview(p: str):
        return industry.overview(p)

    @app.post("/api/projects/{p}/industry/connections")
    def connect(p: str, body: ConnectionInput):
        return industry.connect(p, body)

    @app.patch("/api/projects/{p}/industry/connections/{cid}/rights")
    def rights(p: str, cid: str, body: SourceRights):
        return industry.update_rights(p, cid, body)

    @app.post("/api/projects/{p}/industry/searches")
    def search(p: str, body: SearchPlanInput):
        return industry.create_search(p, body)

    @app.get("/api/projects/{p}/industry/searches/{jid}")
    def status(p: str, jid: str):
        return industry.search(p, jid)

    @app.get("/api/projects/{p}/industry/searches/{jid}/results")
    def results(p: str, jid: str):
        return industry.results(p, jid)

    @app.post("/api/projects/{p}/industry/searches/{jid}/select")
    def select(p: str, jid: str, body: SelectionInput):
        store.job(p, jid)
        return industry.select(p, jid, body)

    @app.post("/api/projects/{p}/industry/searches/{jid}/run")
    def run(p: str, jid: str):
        return industry.run(p, jid)

    from .cmm_api import register_cmm

    register_cmm(app, store, vault, run_jobs=run_jobs, external_guard=external_guard)
    return industry
