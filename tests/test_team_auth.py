"""Team boundaries: real JWT signatures; never client supplied identity."""

import importlib.util
import time

import pytest

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(name) for name in ("jwt", "cryptography", "psycopg")),
    reason="requires optional team dependencies",
)


def test_oidc_verifies_signature_issuer_audience_expiry_and_algorithm():
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from creative_agent.team_auth import OIDCVerifier

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid="one", use="sig", alg="RS256")
    verifier = OIDCVerifier("https://identity.example/realms/team", "creative", jwks={"keys": [jwk]})
    claims = {
        "sub": "alice",
        "iss": verifier.issuer,
        "aud": "creative",
        "exp": int(time.time()) + 60,
        "iat": int(time.time()),
        "nbf": int(time.time()) - 1,
    }
    encode = lambda c, k=key: jwt.encode(c, k, algorithm="RS256", headers={"kid": "one"})
    assert verifier.verify(encode(claims)) == "alice"
    for bad in [
        encode({**claims, "iss": "https://evil.example"}),
        encode({**claims, "aud": "other"}),
        encode({**claims, "exp": 1}),
        encode(claims, other),
        jwt.encode(claims, "", algorithm="none"),
    ]:
        with pytest.raises(ValueError):
            verifier.verify(bad)


def test_role_permissions_are_granular_and_fail_closed():
    from creative_agent.team_auth import ROLE_PERMISSIONS, authorize_route

    assert "read" in ROLE_PERMISSIONS["viewer"]
    assert "external_model" not in ROLE_PERMISSIONS["editor"]
    assert "paid" not in ROLE_PERMISSIONS["analyst"]
    assert "secrets" not in ROLE_PERMISSIONS["operator"]
    assert authorize_route("GET", "/api/projects/p/assets") == {"read"}
    assert authorize_route("POST", "/api/projects/p/actions") == {"write", "external_model"}
    assert authorize_route("GET", "/api/projects/p/export") == {"read", "export"}
    assert authorize_route("PUT", "/api/projects/p") == {"budget"}
    with pytest.raises(PermissionError):
        authorize_route("POST", "/api/projects/p/industry/tasks/x/run")
    with pytest.raises(PermissionError):
        authorize_route("POST", "/api/unknown-new-endpoint")


def test_team_master_key_is_required_and_ciphertext_binds_scope():
    from cryptography.fernet import Fernet

    from creative_agent.team_vault import decrypt_secret, encrypt_secret

    key = Fernet.generate_key()
    ciphertext = encrypt_secret(key, "org", "p", "ref", "private-value")
    assert b"private-value" not in ciphertext
    assert decrypt_secret(key, "org", "p", "ref", ciphertext) == "private-value"
    with pytest.raises(ValueError):
        decrypt_secret(key, "org", "other", "ref", ciphertext)
    with pytest.raises(ValueError):
        decrypt_secret(Fernet.generate_key(), "org", "p", "ref", ciphertext)


def test_cmm_external_guard_runs_before_permission_transport(tmp_path):
    import httpx
    from test_industry import Keys

    from creative_agent.cmm import CMMService
    from creative_agent.store import Store

    def denied():
        raise PermissionError("lease revoked")

    store = Store(tmp_path / "local.sqlite")
    p = store.create_project("p", "0")["id"]
    service = CMMService(store, Keys(), before_request=denied)
    service.connect(p, api_key="test-key")
    calls = []
    with (
        httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))) as client,
        pytest.raises(PermissionError, match="lease revoked"),
    ):
        service.permissions(p, "检查权益", client=client)
    assert calls == []


def test_budget_mutations_need_separate_confirmation():
    from creative_agent.team_auth import require_confirmation

    for method, path in [("POST", "/api/projects"), ("PUT", "/api/projects/p")]:
        with pytest.raises(PermissionError):
            require_confirmation(method, path, {})
        require_confirmation(method, path, {"x-confirm-budget-change": "true"})
    require_confirmation("POST", "/api/projects/p/assets", {})


def test_public_oidc_config_and_team_csp_allow_only_configured_token_origin(tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from creative_agent.store import Store
    from creative_agent.team_runtime import create_team_app

    key = tmp_path / "master"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    static = tmp_path / "ui"
    static.mkdir()
    (static / "index.html").write_text("<html>team</html>")

    class Verifier:
        issuer = "https://identity.example/realms/creative"
        audience = "creative-api"

    app = create_team_app(
        tmp_path / "data",
        store=Store(tmp_path / "contract.sqlite"),
        verifier=Verifier(),
        public_origin="https://creative.example",
        master_key_file=key,
        static_dir=static,
        oidc_client_id="creative-browser",
        authorization_endpoint="https://identity.example/authorize",
        token_endpoint="https://identity.example/token",
    )
    with TestClient(app, base_url="https://creative.example") as client:
        config = client.get("/api/team/config")
        assert config.status_code == 200 and config.json()["mode"] == "team"
        assert config.json()["client_id"] == "creative-browser"
        response = client.get("/")
        assert "connect-src 'self' https://identity.example" in response.headers["content-security-policy"]
        assert "http://127.0.0.1" not in response.headers["content-security-policy"]


def test_approved_team_roles_review_and_download_permissions():
    from creative_agent.team_auth import ROLE_PERMISSIONS, authorize_route

    assert "review" in ROLE_PERMISSIONS["reviewer"]
    assert "write" not in ROLE_PERMISSIONS["reviewer"]
    assert "write" in ROLE_PERMISSIONS["creator"]
    assert "review" not in ROLE_PERMISSIONS["creator"]
    assert "budget" in ROLE_PERMISSIONS["manager"]
    assert authorize_route("POST", "/api/projects/p/scripts/s/comments") == {"review"}
    assert authorize_route("GET", "/api/projects/p/assets/a/file") == {"read", "export"}
    assert authorize_route("GET", "/api/projects/p/assets/a/clip") == {"read", "export"}


def test_oidc_keeps_issuer_exact_and_does_not_guess_jwks_for_generic_issuer():
    from creative_agent.team_auth import OIDCVerifier

    assert (
        OIDCVerifier("https://identity.example/issuer/", "aud", jwks={"keys": []}).issuer
        == "https://identity.example/issuer/"
    )
    with pytest.raises(ValueError, match="JWKS"):
        OIDCVerifier("https://identity.example/issuer", "aud")


def test_team_cancel_is_write_and_cost_settlement_is_budget_authority():
    from creative_agent.team_auth import authorize_route

    assert authorize_route("POST", "/api/projects/p/jobs/j/cancel") == {"write"}
    assert authorize_route("POST", "/api/projects/p/jobs/j/settle") == {"read", "budget"}


def test_team_vault_cannot_silently_store_personal_key_as_shared(tmp_path):
    from cryptography.fernet import Fernet

    from creative_agent.team_auth import Actor, current_actor
    from creative_agent.team_vault import TeamVault

    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)

    class Scope:
        def actor(self):
            return current_actor.get()

        def require(self, *args):
            return {"secrets"}

    vault = TeamVault(Scope(), key)
    token = current_actor.set(Actor("alice", "a"))
    try:
        with pytest.raises(PermissionError, match="共享"):
            vault.set("project:model:key", "personal-key-must-not-be-shared")
    finally:
        current_actor.reset(token)
