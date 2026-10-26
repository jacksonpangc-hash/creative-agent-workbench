"""Team identity: OIDC cryptographic verification and explicit route permissions.

Organization, project membership and grants are read from PostgreSQL, never JWT
custom claims or caller controlled X-Organization/X-Role headers.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Actor:
    subject: str
    organization_id: str
    worker: bool = False


current_actor: ContextVar[Actor | None] = ContextVar("team_actor", default=None)
ROLE_PERMISSIONS = {
    "viewer": frozenset({"read"}),
    "reviewer": frozenset({"read", "review"}),
    "creator": frozenset({"read", "write", "external_model"}),
    "manager": frozenset(
        {"read", "write", "review", "external_model", "paid", "export", "secrets", "budget", "members"}
    ),
    "editor": frozenset({"read", "write"}),
    "analyst": frozenset({"read", "write", "external_model"}),
    "operator": frozenset({"read", "write", "external_model", "paid"}),
    "admin": frozenset(
        {"read", "write", "review", "external_model", "paid", "export", "secrets", "budget", "members"}
    ),
}
ALL_PERMISSIONS = frozenset().union(*ROLE_PERMISSIONS.values())


class OIDCVerifier:
    def __init__(self, issuer, audience, *, jwks_url=None, jwks=None, algorithms=("RS256",)):
        import jwt

        if urlsplit(issuer).scheme != "https" or not audience:
            raise ValueError("OIDC issuer 必须为 HTTPS，且必须配置 audience")
        if not algorithms or not set(algorithms) <= {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
            raise ValueError("OIDC 只允许明确列出的非对称签名算法")
        self.issuer, self.audience, self.algorithms = issuer, audience, list(algorithms)
        self.keys = jwks
        # Endpoint is operator configuration, never JWT jku/x5u or browser input.
        if jwks is None and not jwks_url:
            raise ValueError("必须显式配置 OIDC JWKS HTTPS URL")
        endpoint = jwks_url
        if jwks is None:
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.fragment:
                raise ValueError("JWKS 必须为无凭证 HTTPS 地址")
        self.client = None if jwks is not None else jwt.PyJWKClient(endpoint, timeout=10, lifespan=300)

    def verify(self, token):
        import jwt

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms or not header.get("kid"):
                raise ValueError("OIDC 签名算法或密钥编号无效")
            if self.keys is not None:
                matches = [
                    k
                    for k in self.keys["keys"]
                    if k.get("kid") == header["kid"]
                    and k.get("use", "sig") == "sig"
                    and k.get("alg", header["alg"]) == header["alg"]
                ]
                if len(matches) != 1:
                    raise ValueError("OIDC 签名密钥不唯一或未知")
                key = jwt.PyJWK.from_dict(matches[0], algorithm=header["alg"]).key
            else:
                key = self.client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"] or len(claims["sub"]) > 512:
                raise ValueError("OIDC subject 无效")
            return claims["sub"]
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("OIDC 身份验证失败") from exc


# Explicit allowlist: new endpoints require a conscious permission decision.
_READ = re.compile(
    r"^/api/projects/[^/]+/(state|briefs(?:/[^/]+)?|assets(?:/[^/]+/(?:file|scenes|frames|scene/[^/]+|clip))?|search-results/[^/]+|jobs/[^/]+/events|clusters|scripts(?:/[^/]+(?:/comments)?)?|product-facts|production(?:/[^/]+)?|industry(?:/(?:connections|searches(?:/[^/]+(?:/results)?)?))?)$"
)
_WRITE = re.compile(
    r"^/api/projects/[^/]+/(briefs(?:/[^/]+)?|assets(?:/[^/]+(?:/(?:subtitles|process))?)?|download|transcriptions|reports/import|search|notes(?:/[^/]+)?|instructions(?:/[^/]+(?:/render)?)?|templates|groups|review|scripts(?:/[^/]+(?:/(?:confirm|comments|reviews|production))?)?|product-facts|production(?:/[^/]+)?|industry/(?:searches|searches/[^/]+/select))$"
)


def authorize_route(method, path):
    if path.endswith("/run") or re.search(r"/jobs/[^/]+/(resume|reconcile|poll)$", path):
        # Direct provider execution belongs exclusively to a leased worker.
        raise PermissionError("团队任务只能由持有租约的 worker 执行或由管理员离线核对")
    if method == "GET":
        if path in {"/api/catalog", "/api/projects", "/api/team/session"}:
            return {"read"}
        if re.fullmatch(r"/api/projects/[^/]+(?:/[^/]+/[^/]+)?/export", path):
            return {"read", "export"}
        if re.fullmatch(r"/api/projects/[^/]+/assets/[^/]+/(file|clip)", path):
            return {"read", "export"}
        if _READ.fullmatch(path):
            return {"read"}
    if method == "POST" and re.fullmatch(r"/api/projects/[^/]+/jobs/[^/]+/cancel", path):
        return {"write"}
    if method == "POST" and re.fullmatch(r"/api/projects/[^/]+/jobs/[^/]+/settle", path):
        return {"read", "budget"}
    if method == "POST" and path == "/api/projects":
        return {"budget"}
    if method in {"PUT", "PATCH"} and re.fullmatch(r"/api/projects/[^/]+", path):
        return {"budget"}
    if method in {"POST", "PUT", "PATCH", "DELETE"} and re.fullmatch(
        r"/api/projects/[^/]+/(models(?:/[^/]+)?|industry/connections(?:/[^/]+(?:/rights)?)?|cmm/permissions)",
        path,
    ):
        return {"write", "secrets"}
    if method == "POST" and re.fullmatch(r"/api/projects/[^/]+/(actions|generation|industry/searches)", path):
        return {"write", "external_model"}
    if method == "POST" and re.fullmatch(r"/api/projects/[^/]+/scripts/[^/]+/comments", path):
        return {"review"}
    if method == "PUT" and re.fullmatch(r"/api/projects/[^/]+/production/[^/]+", path):
        # Validated body status is authorized by the endpoint wrapper. Approval
        # requires review; ordinary task progress requires write.
        return {"read"}
    if method in {"POST", "PUT", "PATCH", "DELETE"} and _WRITE.fullmatch(path):
        return {"write"}
    if method == "POST" and re.fullmatch(r"/api/projects/[^/]+/jobs/[^/]+/(favorite|archive)", path):
        return {"write"}
    raise PermissionError("团队接口尚未配置授权规则")


def require_confirmation(method, path, headers):
    """Budget authority alone does not constitute confirmation of a budget change."""
    if (
        (method == "POST" and path == "/api/projects")
        or (method in {"PUT", "PATCH"} and re.fullmatch(r"/api/projects/[^/]+", path))
    ) and headers.get("x-confirm-budget-change") != "true":
        raise PermissionError("预算变更需要单独确认；确认后提交 X-Confirm-Budget-Change: true")
