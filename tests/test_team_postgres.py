"""Run against disposable PostgreSQL using a distinct non-owner, non-bypass role.

CREATIVE_TEST_POSTGRES_ADMIN_DSN must name a disposable database; this suite drops
its public schema. It never uses workstation credentials implicitly.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

ADMIN = os.environ.get("CREATIVE_TEST_POSTGRES_ADMIN_DSN")
pytestmark = pytest.mark.skipif(not ADMIN, reason="requires explicitly supplied disposable real PostgreSQL")


@pytest.fixture()
def team():
    import psycopg
    from psycopg.conninfo import make_conninfo

    from creative_agent.postgres_store import PostgresStore, migrate
    from creative_agent.team_auth import Actor, current_actor

    with psycopg.connect(ADMIN, autocommit=True) as db:
        db.execute("DROP SCHEMA public CASCADE")
        db.execute("CREATE SCHEMA public")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='creative_test_runtime'").fetchone():
            db.execute("CREATE ROLE creative_test_runtime LOGIN PASSWORD 'disposable-runtime-test-only'")
    migrate(ADMIN, "creative_test_runtime")
    runtime = make_conninfo(ADMIN, user="creative_test_runtime", password="disposable-runtime-test-only")
    store = PostgresStore(runtime)
    with psycopg.connect(ADMIN) as db:
        for org, subject in [("a", "alice"), ("b", "bob")]:
            db.execute(
                "INSERT INTO team_organizations(id,name,budget,currency) VALUES(%s,%s,%s,%s)",
                (org, org, "10", "CNY"),
            )
            db.execute(
                "INSERT INTO team_members(organization_id,subject,role) VALUES(%s,%s,%s)",
                (org, subject, "admin"),
            )

    @contextmanager
    def scope(subject="alice", org="a", worker=False):
        token = current_actor.set(Actor(subject, org, worker))
        try:
            yield store
        finally:
            current_actor.reset(token)

    with scope():
        p = store.create_project("one", "10")["id"]
    with scope("bob", "b"):
        other = store.create_project("two", "10")["id"]
    return store, scope, p, other, runtime


def test_nonowner_rls_records_jobs_exports_vectors_and_membership_revocation(team):
    import psycopg

    from creative_agent.contracts import CostQuote
    from creative_agent.store import NotFound

    store, scope, p, other, runtime = team
    with scope():
        asset = store.put(p, "asset", {"title": "private", "embedding": [1, 2]})
        job = store.create_job(p, "analysis", {}, CostQuote(amount="0", basis="test reservation"), "one")
        assert store.export_project(p)["records"]["asset"][0]["title"] == "private"
        with pytest.raises(NotFound):
            store.get(other, "asset", asset["id"])
    with scope("bob", "b"):
        assert store.projects()[0]["id"] == other
        with pytest.raises(NotFound):
            store.job(p, job["id"])
        with store.connection() as db:
            assert db.execute("SELECT * FROM records").fetchall() == []
            assert db.execute("SELECT * FROM jobs").fetchall() == []
    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_members SET active=false WHERE subject='alice'")
    with scope():
        assert store.projects() == []
        with pytest.raises((NotFound, PermissionError)):
            store.get(p, "asset", asset["id"])
    with psycopg.connect(runtime) as db:
        assert db.execute("SELECT * FROM records").fetchall() == []
        assert db.execute("SELECT * FROM jobs").fetchall() == []
        assert db.execute("SELECT * FROM projects").fetchall() == []
        role = db.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        assert role == (False, False)


def test_atomic_org_project_caps_idempotency_and_actor_payer_snapshot(team):
    from creative_agent.contracts import CostQuote
    from creative_agent.store import BudgetError

    store, scope, p, _, _ = team
    with scope():
        second = store.create_project("second", "10")["id"]

    def reserve(project, key):
        with scope():
            try:
                return store.create_job(
                    project, "analysis", {}, CostQuote(amount="6", basis="test reservation"), key
                )
            except BudgetError:
                return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda x: reserve(*x), [(p, "one"), (second, "two")]))
    assert sum(x is not None for x in results) == 1
    winner = next(x for x in results if x)
    with scope():
        again = store.create_job(
            winner["project_id"],
            "analysis",
            {},
            CostQuote(amount="6", basis="test reservation"),
            winner["idempotency_key"],
        )
        assert again["id"] == winner["id"]
        snapshot = store.job_identity(winner["project_id"], winner["id"])
        assert snapshot["actor_id"] == "alice" and snapshot["payer_id"] == "a"
        assert set(snapshot["required_permissions"]) >= {"write", "external_model", "paid"}


def test_parallel_cas_and_claim_and_ambiguous_recovery(team):
    import psycopg

    from creative_agent.contracts import CostQuote
    from creative_agent.scripts import Scripts
    from creative_agent.store import Conflict

    store, scope, p, _, _ = team
    with scope():
        record = Scripts(store)._write(p, "script", {"title": "v1"})
        job = store.create_job(p, "analysis", {}, CostQuote(amount="1", basis="test reservation"), "leased")

    def cas(title):
        with scope():
            try:
                return Scripts(store)._write(p, "script", {"title": title}, record["id"], expected=1)
            except Conflict:
                return None

    with ThreadPoolExecutor(2) as pool:
        assert sum(x is not None for x in pool.map(cas, ["v2", "v3"])) == 1
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda w: store.claim(w, lease_seconds=30), ["w1", "w2"]))
    claim = next(x for x in claims if x)
    assert sum(x is not None for x in claims) == 1
    with scope(worker=True):
        store.assert_lease(claim)
        store.transition(p, job["id"], "running")
        store.transition(p, job["id"], "submitting")
    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_queue SET lease_until=now()-interval '1 second'")
    assert store.claim("w3") is None
    with scope():
        assert store.job(p, job["id"])["state"] == "needs_attention"
    with scope(worker=True), pytest.raises(Conflict):
        store.assert_lease(claim)


def test_revocation_between_enqueue_and_worker_claim_prevents_execution(team):
    import psycopg

    from creative_agent.contracts import CostQuote

    store, scope, p, _, _ = team
    with scope():
        job = store.create_job(p, "analysis", {}, CostQuote(amount="1", basis="test reservation"), "revoked")
    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_members SET active=false WHERE subject='alice'")
    assert store.claim("worker") is None
    with psycopg.connect(ADMIN) as db:
        state = db.execute("SELECT state FROM jobs WHERE id=%s", (job["id"],)).fetchone()[0]
        assert state == "failed"
        assert db.execute("SELECT actual_cost FROM jobs WHERE id=%s", (job["id"],)).fetchone()[0] == "0"


def test_team_http_uses_verified_identity_and_ignores_role_headers(team, tmp_path):
    import psycopg
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from creative_agent.team_runtime import create_team_app

    store, _scope, p, other, _ = team
    master = tmp_path / "master"
    master.write_bytes(Fernet.generate_key())
    master.chmod(0o600)

    class Verifier:
        def verify(self, token):
            if token != "valid-alice":
                raise ValueError("invalid")
            return "alice"

    app = create_team_app(
        tmp_path / "data",
        store=store,
        verifier=Verifier(),
        public_origin="https://creative.example",
        master_key_file=master,
    )
    with TestClient(app, base_url="https://creative.example") as client:
        assert client.get("/api/projects").status_code == 401
        headers = {"Authorization": "Bearer valid-alice", "X-Role": "admin", "X-Organization": "b"}
        assert client.get("/api/projects", headers=headers).json()[0]["id"] == p
        assert client.get(f"/api/projects/{other}/state", headers=headers).status_code == 403
        assert (
            client.get("/api/projects", headers={**headers, "Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.get("/api/projects", headers={**headers, "Host": "evil.example"}).status_code == 403
        with psycopg.connect(ADMIN) as db:
            db.execute("UPDATE team_members SET role='member' WHERE subject='alice'")
            db.execute(
                "INSERT INTO team_project_members(project_id,subject,role) VALUES(%s,'alice','viewer')", (p,)
            )
        assert (
            client.put(
                f"/api/projects/{p}", headers=headers, json={"name": "bad", "budget": "100"}
            ).status_code
            == 403
        )
        assert client.get(f"/api/projects/{p}/export", headers=headers).status_code == 403
        assert client.get(f"/api/projects/{p}/state", headers=headers).status_code == 200


def test_worker_executes_once_with_lease_and_rechecks_revocation(team):
    from creative_agent.contracts import CostQuote
    from creative_agent.team_worker import TeamWorker, external_guard

    store, scope, p, _, _ = team
    with scope():
        job = store.create_job(p, "analysis", {}, CostQuote(amount="1", basis="test reservation"), "worker")
    calls = []

    def execute(project, jid):
        external_guard()
        calls.append(jid)
        store.transition(project, jid, "running")
        store.finish(project, jid, result={"ok": True})

    worker = TeamWorker(store, execute, worker_id="test-worker")
    assert worker.run_once()
    assert not worker.run_once()
    assert calls == [job["id"]]
    with pytest.raises(PermissionError):
        external_guard()


def test_member_grants_secrets_media_and_budget_are_independent(team, tmp_path):
    import psycopg
    from cryptography.fernet import Fernet

    from creative_agent.contracts import CostQuote
    from creative_agent.store import NotFound
    from creative_agent.team_admin import TeamAdmin
    from creative_agent.team_vault import TeamVault

    store, scope, p, other, _ = team
    admin = TeamAdmin(ADMIN)
    admin.member("a", "charlie")
    admin.project_member(p, "charlie", "analyst", grants=["export"])
    master = tmp_path / "key"
    master.write_bytes(Fernet.generate_key())
    master.chmod(0o600)
    vault = TeamVault(store, master)
    with scope():
        private = store.put(p, "asset", {"title": "one", "embedding": [1, 2]})
        from creative_agent.team_vault import shared_credential_consent

        consent = shared_credential_consent.set(True)
        try:
            vault.set(p + ":model:secret", "test-private-value")
        finally:
            shared_credential_consent.reset(consent)
    with scope("charlie"):
        assert store.get(p, "asset", private["id"])["title"] == "one"
        assert store.export_project(p)["records"]["asset"]
        with pytest.raises(PermissionError):
            vault.get(p + ":model:secret")
        with pytest.raises(PermissionError):
            store.create_job(p, "analysis", {}, CostQuote(amount="1", basis="test reservation"), "paid")
        with pytest.raises(PermissionError):
            store.update_project(p, budget="100")
        with pytest.raises(NotFound):
            store.project(other)
    admin.project_member(p, "charlie", "viewer")
    with scope("charlie"), pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.put(p, "asset", {"title": "forbidden"})
    admin.revoke("a", "charlie")
    assert store.memberships("charlie") == []
    with psycopg.connect(ADMIN) as db:
        ciphertext = db.execute("SELECT ciphertext FROM team_secrets").fetchone()[0]
        assert b"test-private-value" not in bytes(ciphertext)


def test_permission_revoked_during_job_stops_next_request_without_refund(team):
    import psycopg

    from creative_agent.contracts import CostQuote
    from creative_agent.team_admin import TeamAdmin
    from creative_agent.team_worker import TeamWorker, external_guard

    store, scope, p, _, _ = team
    with scope():
        job = store.create_job(p, "analysis", {}, CostQuote(amount="2", basis="two calls"), "mid-revoke")
    calls = []

    def execute(project, jid):
        store.transition(project, jid, "running")
        store.transition(project, jid, "submitting")
        external_guard()
        calls.append("first")
        TeamAdmin(ADMIN).revoke("a", "alice")
        external_guard()
        calls.append("second")

    assert TeamWorker(store, execute).run_once()
    assert calls == ["first"]
    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_queue SET lease_until=now()-interval '1 second'")
    assert store.claim("next-worker") is None
    with psycopg.connect(ADMIN) as db:
        row = db.execute("SELECT state,actual_cost FROM jobs WHERE id=%s", (job["id"],)).fetchone()
        assert row == ("needs_attention", None)


def test_stale_lease_cannot_write_or_call_provider_after_safe_reclaim(team):
    import psycopg

    from creative_agent.contracts import CostQuote
    from creative_agent.store import Conflict
    from creative_agent.team_worker import active_lease, external_guard

    store, scope, p, _, _ = team
    with scope():
        store.create_job(p, "transcription", {}, CostQuote(amount="0", basis="local"), "safe-reclaim")
    first = store.claim("worker-one")
    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_queue SET lease_until=now()-interval '1 second'")
    second = store.claim("worker-two")
    assert first["job_id"] == second["job_id"] and first["lease_token"] != second["lease_token"]
    token = active_lease.set((store, first))
    try:
        with scope(worker=True), pytest.raises(Conflict):
            store.put(p, "asset", {"title": "stale"})
        with scope(worker=True), pytest.raises(Conflict):
            external_guard()
    finally:
        active_lease.reset(token)


def test_signed_jwt_http_media_export_and_vector_isolation(team, tmp_path):
    import time

    import jwt
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from PIL import Image

    from creative_agent.team_auth import OIDCVerifier
    from creative_agent.team_runtime import create_team_app

    store, scope, p, other, _ = team
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing.public_key(), as_dict=True)
    jwk.update(kid="integration", alg="RS256", use="sig")
    verifier = OIDCVerifier("https://identity.example/realms/creative", "creative-api", jwks={"keys": [jwk]})
    claims = {
        "sub": "alice",
        "iss": verifier.issuer,
        "aud": verifier.audience,
        "exp": int(time.time()) + 60,
        "iat": int(time.time()),
    }
    token = jwt.encode(claims, signing, algorithm="RS256", headers={"kid": "integration"})
    master = tmp_path / "master"
    master.write_bytes(Fernet.generate_key())
    master.chmod(0o600)
    app = create_team_app(
        tmp_path / "data",
        store=store,
        verifier=verifier,
        public_origin="https://creative.example",
        master_key_file=master,
    )
    picture = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "red").save(picture)
    with scope():
        own = app.state.media.ingest(p, picture)
        own = store.put(
            p,
            "asset",
            {**own, "embedding": [1, 0], "embedding_model": "one", "embedding_provider": "test"},
            own["id"],
        )
    with scope("bob", "b"):
        foreign = app.state.media.ingest(other, picture)
        store.put(other, "asset", {**foreign, "embedding": [0, 1]}, foreign["id"])
    with TestClient(app, base_url="https://creative.example") as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get(f"/api/projects/{p}/assets/{own['id']}/file", headers=headers).status_code == 200
        assert (
            client.get(f"/api/projects/{p}/assets/{foreign['id']}/file", headers=headers).status_code == 404
        )
        assert (
            client.get(f"/api/projects/{other}/assets/{foreign['id']}/file", headers=headers).status_code
            == 403
        )
        exported = client.get(f"/api/projects/{p}/export", headers=headers)
        assert exported.status_code == 200
        assert (
            foreign["id"] not in exported.text
            and "secret_ref" not in exported.text
            and "local_path" not in exported.text
        )
        assert len(exported.json()["records"]["asset"]) == 1
        bad = jwt.encode(
            {**claims, "aud": "other"}, signing, algorithm="RS256", headers={"kid": "integration"}
        )
        assert client.get("/api/projects", headers={"Authorization": "Bearer " + bad}).status_code == 401
        assert client.get("/api/team/session", headers=headers).json()["subject"] == "alice"


def test_reviewer_can_review_but_cannot_edit_or_self_approve(team):
    import psycopg

    from creative_agent.scripts import Scripts
    from creative_agent.team_admin import TeamAdmin

    store, scope, p, _, _ = team
    admin = TeamAdmin(ADMIN)
    admin.member("a", "creator")
    admin.project_member(p, "creator", "creator")
    admin.member("a", "reviewer")
    admin.project_member(p, "reviewer", "reviewer")
    with scope("creator"):
        script = Scripts(store)._write(p, "script", {"title": "script", "rows": [], "confirmed": True})
        task = Scripts(store)._write(
            p,
            "production",
            {
                "script_id": script["id"],
                "script_version": 1,
                "script_snapshot": store.get(p, "script", script["id"]),
                "status": "in_review",
                "finished_asset_id": None,
            },
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            store.put(
                p, "production", {**store.get(p, "production", task["id"]), "status": "approved"}, task["id"]
            )
    with scope("reviewer"):
        reviewed = Scripts(store).comment(p, script["id"], 1, "reviewer", "checked", verdict="approved")
        assert reviewed["verdict"] == "approved"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            Scripts(store)._write(p, "script", {"title": "unauthorized"}, script["id"], expected=1)
        approved = Scripts(store)._write(p, "production", {"status": "approved"}, task["id"], expected=1)
        assert approved["status"] == "approved"
    admin.project_member(p, "creator", "creator", grants=["review"])
    with scope("creator"), pytest.raises(psycopg.errors.InsufficientPrivilege):
        Scripts(store).comment(p, script["id"], 1, "creator", "self approval", verdict="approved")


def test_http_production_approval_requires_reviewer_not_creator(team, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from creative_agent.scripts import Scripts
    from creative_agent.team_admin import TeamAdmin
    from creative_agent.team_runtime import create_team_app

    store, scope, p, _, _ = team
    admin = TeamAdmin(ADMIN)
    for subject, role in [("creator", "creator"), ("reviewer", "reviewer")]:
        admin.member("a", subject)
        admin.project_member(p, subject, role)
    with scope("creator"):
        script = Scripts(store)._write(p, "script", {"title": "s", "rows": [], "confirmed": True})
        asset = store.put(p, "asset", {"source_kind": "finished"})
        task = Scripts(store)._write(
            p,
            "production",
            {
                "script_id": script["id"],
                "script_version": 1,
                "script_snapshot": store.get(p, "script", script["id"]),
                "status": "in_review",
                "finished_asset_id": asset["id"],
            },
        )
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)

    class Verifier:
        def verify(self, token):
            return token

    app = create_team_app(
        tmp_path / "data",
        store=store,
        verifier=Verifier(),
        public_origin="https://creative.example",
        master_key_file=key,
    )
    with TestClient(app, base_url="https://creative.example", raise_server_exceptions=False) as client:
        url = f"/api/projects/{p}/production/{task['id']}"
        body = {"expected_version": 1, "status": "approved"}
        assert client.put(url, headers={"Authorization": "Bearer creator"}, json=body).status_code == 403
        assert (
            client.put(
                url, headers={"Authorization": "Bearer reviewer"}, json={**body, "status": "in_progress"}
            ).status_code
            == 403
        )
        result = client.put(url, headers={"Authorization": "Bearer reviewer"}, json=body)
        assert result.status_code == 200 and result.json()["status"] == "approved", result.text


def test_cancel_is_persisted_then_leased_worker_requests_vendor_cancel_once(team, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from creative_agent.contracts import CostQuote
    from creative_agent.team_runtime import create_team_app
    from creative_agent.team_worker import TeamWorker, external_guard
    from creative_agent.workflows import Workflows

    store, scope, p, _, _ = team
    with scope():
        job = store.create_job(p, "generation", {}, CostQuote(amount="2", basis="test"), "cancel")
        store.transition(p, job["id"], "running")
        store.transition(p, job["id"], "submitting")
        store.accept_submission(p, job["id"], "vendor-id")
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)

    class Verifier:
        def verify(self, token):
            return "alice"

    app = create_team_app(
        tmp_path / "data",
        store=store,
        verifier=Verifier(),
        public_origin="https://creative.example",
        master_key_file=key,
    )
    with TestClient(app, base_url="https://creative.example") as client:
        response = client.post(
            f"/api/projects/{p}/jobs/{job['id']}/cancel", headers={"Authorization": "Bearer valid"}
        )
        assert response.status_code == 200 and response.json()["state"] == "cancelling"
    calls = []

    class Vendor:
        def cancel_video(self, request_id):
            external_guard()
            calls.append(("cancel", request_id))
            return {"state": "unsupported"}

        def poll_video(self, request_id):
            external_guard()
            calls.append(("poll", request_id))
            return {"state": "running"}

    workflow = Workflows(store, None)

    def execute(project, jid):
        workflow.resume_generation(project, jid, Vendor())

    worker = TeamWorker(store, execute)
    assert worker.run_once()
    import psycopg

    with psycopg.connect(ADMIN) as db:
        db.execute("UPDATE team_queue SET lease_until=now()-interval '1 second'")
    assert worker.run_once()
    assert calls == [("cancel", "vendor-id"), ("poll", "vendor-id"), ("poll", "vendor-id")]
    with scope():
        assert store.job(p, job["id"])["actual_cost"] is None
        assert len([e for e in store.events(p, job["id"]) if e["type"] == "cancel_attempt"]) == 1


def test_settlement_requires_budget_grant_and_preserves_unknown_until_confirmed(team):
    from creative_agent.contracts import CostQuote
    from creative_agent.team_admin import TeamAdmin

    store, scope, p, _, _ = team
    with scope():
        job = store.create_job(p, "analysis", {}, CostQuote(amount="2", basis="test"), "settle")
        store.transition(p, job["id"], "running")
        store.finish(p, job["id"], result={})
    admin = TeamAdmin(ADMIN)
    admin.member("a", "creator")
    admin.project_member(p, "creator", "creator")
    with scope("creator"), pytest.raises(PermissionError):
        store.settle(p, job["id"], "0")
    admin.project_member(p, "creator", "viewer", grants=["budget"])
    with scope("creator"):
        assert store.settle(p, job["id"], "1.5")["actual_cost"] == "1.5"


def test_local_media_bundle_import_into_scoped_postgres_preserves_history_and_never_enqueues(team, tmp_path):
    import hashlib
    from pathlib import Path

    from PIL import Image

    from creative_agent.contracts import CostQuote
    from creative_agent.media import MediaService
    from creative_agent.portability import export_bundle, import_bundle
    from creative_agent.scripts import Scripts
    from creative_agent.store import Conflict, Store

    store, scope, target, _, _ = team
    local = Store(tmp_path / "source.sqlite")
    source = local.create_project("local", "10")["id"]
    source_media = MediaService(local, tmp_path / "local-media")
    image = tmp_path / "product.png"
    Image.new("RGB", (16, 10), "purple").save(image)
    asset = source_media.ingest(source, image, source_kind="own")
    script = Scripts(local).create(
        source,
        {
            "title": "source",
            "objective": "test",
            "confirmed": True,
            "rows": [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "dialogue": "confirmed copy",
                    "reshoot": "按参考构图补拍产品包装，图片仅用作构图依据",
                    "references": [{"asset_id": asset["id"]}],
                }
            ],
        },
    )
    production = Scripts(local).produce(source, script["id"], 1, "production", "editor")
    finished = source_media.ingest(source, image, source_kind="finished")
    Scripts(local).update_task(source, production["id"], 1, "in_progress")
    Scripts(local).update_task(source, production["id"], 2, "in_review", finished_asset_id=finished["id"])
    production = Scripts(local).update_task(source, production["id"], 3, "approved")
    charged = local.create_job(
        source,
        "analysis",
        {"asset_ids": [asset["id"]]},
        CostQuote(amount="2", basis="confirmed"),
        "historical",
    )
    local.transition(source, charged["id"], "running")
    local.transition(source, charged["id"], "submitting")
    archive = tmp_path / "source.zip"
    manifest = export_bundle(local, source_media, source, archive)
    target_media = MediaService(store, tmp_path / "team-media")
    with scope():
        receipt = import_bundle(
            store, target_media, archive, target_project_id=target, expected_sha256=manifest["sha256"]
        )
        saved = store.get(target, "asset", asset["id"])
        assert saved["project_id"] == target
        assert Path(saved["path"]).is_relative_to(target_media.root / target)
        assert (
            hashlib.sha256(target_media.file(target, asset["id"]).read_bytes()).hexdigest() == asset["sha256"]
        )
        assert store.get(target, "script", script["id"])["confirmed"] is True
        assert store.get(target, "production", production["id"])["script_snapshot"]["confirmed"] is True
        assert store.get(target, "production", production["id"])["status"] == "approved"
        history = store.get(target, "migration_job", charged["id"])
        assert history["state"] == "archived" and history["original_state"] == "submitting"
        assert history["actual_cost"] is None and history["quote"]["amount"] == "2"
        assert history["replay_allowed"] is False and history["requires_reconciliation"] is True
        assert store.jobs(target) == [] and receipt["replay_allowed"] is False
        with store.connection() as db:
            assert db.execute("SELECT * FROM team_queue WHERE project_id=?", (target,)).fetchall() == []
        with pytest.raises(Conflict):
            import_bundle(store, target_media, archive, target_project_id=target)


def test_http_shared_credential_requires_explicit_consent_and_never_echoes_secret(team, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from creative_agent.team_runtime import create_team_app

    store, scope, p, _, _ = team
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)

    class Verifier:
        def verify(self, token):
            return "alice"

    app = create_team_app(
        tmp_path / "data",
        store=store,
        verifier=Verifier(),
        public_origin="https://creative.example",
        master_key_file=key,
    )
    body = {
        "provider": "deepseek",
        "label": "shared model",
        "model_id": "deepseek-flash",
        "role": "strategy",
        "api_key": "fixture-only-not-live-secret",
    }
    with TestClient(app, base_url="https://creative.example") as client:
        headers = {"Authorization": "Bearer verified"}
        denied = client.post(f"/api/projects/{p}/models", headers=headers, json=body)
        assert denied.status_code == 403 and "共享" in denied.json()["detail"]
        with scope():
            assert store.list(p, "model") == []
        saved = client.post(
            f"/api/projects/{p}/models", headers={**headers, "X-Confirm-Shared-Credential": "true"}, json=body
        )
        assert saved.status_code == 200 and saved.json()["has_key"] is True
        assert body["api_key"] not in saved.text and "secret_ref" not in saved.text
