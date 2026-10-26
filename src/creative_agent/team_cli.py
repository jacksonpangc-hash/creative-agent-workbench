"""Team server and worker use only explicitly supplied secret/configuration files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立团队入口：OIDC + PostgreSQL + leased worker")
    parser.add_argument("command", choices=["serve", "worker"])
    parser.add_argument(
        "--dsn-file", type=Path, default=Path(os.environ.get("TEAM_DSN_FILE", "/run/secrets/runtime_dsn"))
    )
    parser.add_argument(
        "--master-key-file",
        type=Path,
        default=Path(os.environ.get("TEAM_MASTER_KEY_FILE", "/run/secrets/vault_master_key")),
    )
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TEAM_DATA_DIR", "/data")))
    parser.add_argument("--public-origin", default=os.environ.get("TEAM_PUBLIC_ORIGIN"))
    parser.add_argument("--issuer", default=os.environ.get("TEAM_OIDC_ISSUER"))
    parser.add_argument("--audience", default=os.environ.get("TEAM_OIDC_AUDIENCE"))
    parser.add_argument("--jwks-url", default=os.environ.get("TEAM_OIDC_JWKS_URL"))
    parser.add_argument("--ui-dir", type=Path, default=Path(os.environ.get("TEAM_UI_DIR", "/app/ui/dist")))
    parser.add_argument("--oidc-client-id", default=os.environ.get("TEAM_OIDC_CLIENT_ID"))
    parser.add_argument(
        "--authorization-endpoint", default=os.environ.get("TEAM_OIDC_AUTHORIZATION_ENDPOINT")
    )
    parser.add_argument("--token-endpoint", default=os.environ.get("TEAM_OIDC_TOKEN_ENDPOINT"))
    args = parser.parse_args(argv)
    if not args.public_origin or not args.issuer or not args.audience:
        parser.error("必须明确配置 HTTPS origin、OIDC issuer 和 audience")
    from .postgres_store import PostgresStore
    from .team_admin import secret_file
    from .team_auth import OIDCVerifier
    from .team_runtime import create_team_app

    store = PostgresStore(secret_file(args.dsn_file))
    verifier = OIDCVerifier(args.issuer, args.audience, jwks_url=args.jwks_url)
    app = create_team_app(
        args.data_dir,
        store=store,
        verifier=verifier,
        public_origin=args.public_origin,
        master_key_file=args.master_key_file,
        static_dir=args.ui_dir,
        oidc_client_id=args.oidc_client_id,
        authorization_endpoint=args.authorization_endpoint,
        token_endpoint=args.token_endpoint,
    )
    if args.command == "worker":
        from .team_worker import TeamWorker

        TeamWorker(store, app.state.execute).run()
    else:
        import uvicorn

        # Bind only inside the deployment's isolated Docker network. Only Caddy
        # publishes a host port; trusted proxy headers have an explicit peer IP.
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("TEAM_PROXY_IP", "172.30.0.2"),
            access_log=False,
            log_level="warning",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
