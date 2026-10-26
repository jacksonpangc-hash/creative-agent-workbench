"""Explicit team entry point; never repurpose the local bearer server for LAN use."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .team_auth import Actor, authorize_route, current_actor, require_confirmation
from .team_vault import TeamVault, shared_credential_consent
from .team_worker import external_guard


def create_team_app(
    data_dir,
    *,
    store,
    verifier,
    public_origin,
    master_key_file,
    static_dir=None,
    oidc_client_id=None,
    authorization_endpoint=None,
    token_endpoint=None,
):
    from .api import create_app

    origin = urlsplit(public_origin)
    if (
        origin.scheme != "https"
        or not origin.netloc
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
        or origin.username
    ):
        raise ValueError("团队入口必须配置准确的 HTTPS origin")
    public_origin = f"https://{origin.netloc}"
    if oidc_client_id:
        for endpoint in (authorization_endpoint, token_endpoint):
            parsed = urlsplit(endpoint or "")
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("浏览器 OIDC 登录需要显式配置无凭证的 HTTPS authorization/token endpoint")
    oidc_config = {
        "mode": "team",
        "enabled": bool(oidc_client_id),
        "issuer": getattr(verifier, "issuer", None),
        "client_id": oidc_client_id,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "audience": getattr(verifier, "audience", None),
        "credential_scope": "project_shared_only",
    }
    vault = TeamVault(store, master_key_file)
    app = create_app(
        data_dir,
        secret_store=vault,
        run_jobs=False,
        static_dir=static_dir,
        store_override=store,
        security_profile="team",
        external_guard=external_guard,
    )

    # Reuse the domain endpoint after FastAPI validation/body limits, then apply
    # the status-specific privilege. Reading a raw body in outer middleware
    # would bypass the API's streaming request size enforcement.
    def production_endpoint(original):
        def guarded(p, tid, body):
            store.require(p, "review" if body.status == "approved" else "write")
            return original(p, tid, body)

        return guarded

    for route in app.router.routes:
        if getattr(route, "path", None) == "/api/projects/{p}/production/{tid}" and "PUT" in route.methods:
            route.dependant.call = production_endpoint(route.dependant.call)

    import psycopg

    @app.exception_handler(psycopg.errors.InsufficientPrivilege)
    async def database_permission_error(request, exc):
        return JSONResponse({"detail": "当前成员无权修改内容或执行本人审批"}, status_code=403)

    def secure_response(response):
        token_origin = ""
        if oidc_client_id:
            parsed = urlsplit(token_endpoint)
            token_origin = " " + parsed.scheme + "://" + parsed.netloc
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'"
            + token_origin
            + "; frame-ancestors 'none'; object-src 'none'"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.middleware("http")
    async def team_security(request: Request, call_next):
        if request.headers.get("host") != origin.netloc or request.url.scheme != "https":
            return JSONResponse({"detail": "团队服务仅接受配置的 HTTPS 主机"}, status_code=403)
        browser_origin = request.headers.get("origin")
        if browser_origin and browser_origin != public_origin:
            return JSONResponse({"detail": "禁止跨来源访问"}, status_code=403)
        if request.method == "OPTIONS":
            if browser_origin != public_origin:
                return JSONResponse({"detail": "来源未授权"}, status_code=403)
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": public_origin,
                    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization,Content-Type,X-Confirm-Budget-Change,X-Confirm-Shared-Credential",
                    "Vary": "Origin",
                },
            )
        if not request.url.path.startswith("/api/"):
            request.state.team_authorized = True
            return secure_response(await call_next(request))
        if request.method == "GET" and request.url.path == "/api/team/config":
            return JSONResponse(oidc_config, headers={"Cache-Control": "no-store"})
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"detail": "需要 OIDC access token"}, status_code=401)
        try:
            subject = verifier.verify(auth[7:])
        except ValueError:
            return JSONResponse({"detail": "OIDC 身份验证失败"}, status_code=401)
        memberships = store.memberships(subject)
        if not memberships:
            return JSONResponse({"detail": "当前身份没有有效组织成员资格"}, status_code=403)
        match = re.match(r"^/api/projects/([^/]+)", request.url.path)
        selected = request.query_params.get("organization")
        if selected and selected not in memberships:
            return JSONResponse({"detail": "组织范围未授权"}, status_code=403)
        candidates = [selected] if selected else memberships
        # A project ID is only a selector. Resolve its organization through the
        # verified subject's current database membership and project grants.
        actor = None
        if match:
            for organization in candidates:
                probe = Actor(subject, organization)
                token = current_actor.set(probe)
                try:
                    store.require(match[1], "read")
                    actor = probe
                    break
                except PermissionError:
                    pass
                finally:
                    current_actor.reset(token)
            if actor is None:
                return JSONResponse({"detail": "项目范围未授权"}, status_code=403)
        else:
            actor = Actor(subject, candidates[0])
        token = current_actor.set(actor)
        shared_token = shared_credential_consent.set(
            request.headers.get("x-confirm-shared-credential") == "true"
        )
        try:
            required = authorize_route(request.method, request.url.path)
            if (
                request.method == "POST"
                and re.fullmatch(r"/api/projects/[^/]+/models", request.url.path)
                and not shared_credential_consent.get()
            ):
                raise PermissionError("团队模型仅支持明确确认的项目共享凭证，个人凭证尚不支持")
            require_confirmation(request.method, request.url.path, request.headers)
            if match:
                store.require(match[1], *required)
            elif required != {"read"}:
                store.require_org_admin()
            request.state.actor_id = subject
            request.state.team_authorized = True
            response = await call_next(request)
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
            return secure_response(response)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        finally:
            shared_credential_consent.reset(shared_token)
            current_actor.reset(token)

    @app.get("/api/team/session")
    def session():
        actor = store.actor()
        return {
            "subject": actor.subject,
            "organization_id": actor.organization_id,
            "organizations": store.memberships(actor.subject),
        }

    # The local SPA wildcard is registered by create_app. Team API extensions
    # must precede it so FastAPI can actually reach the session endpoint.
    catchalls = [route for route in app.router.routes if getattr(route, "path", None) == "/{path:path}"]
    app.router.routes[:] = [route for route in app.router.routes if route not in catchalls] + catchalls
    return app
