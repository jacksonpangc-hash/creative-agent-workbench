"""Offline organization administration; database-owner credentials never enter API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import decimal
from .team_auth import ALL_PERMISSIONS, ROLE_PERMISSIONS


class TeamAdmin:
    def __init__(self, dsn):
        self.dsn = dsn

    def organization(self, organization_id, name, budget, currency="CNY"):
        import psycopg

        if not organization_id or not name.strip() or currency not in {"CNY", "USD"} or decimal(budget) < 0:
            raise ValueError("组织、预算或币种无效")
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "INSERT INTO team_organizations(id,name,budget,currency) VALUES(%s,%s,%s,%s)",
                (organization_id, name.strip(), decimal(budget), currency),
            )

    def member(self, organization_id, subject, role="member"):
        import psycopg

        if not subject or role not in {"member", "admin"}:
            raise ValueError("成员身份或角色无效")
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "INSERT INTO team_members(organization_id,subject,role) VALUES(%s,%s,%s) ON CONFLICT(organization_id,subject) DO UPDATE SET role=excluded.role,active=true",
                (organization_id, subject, role),
            )

    def project_member(self, project_id, subject, role, *, grants=()):
        import psycopg

        if role not in ROLE_PERMISSIONS or not set(grants) <= ALL_PERMISSIONS:
            raise ValueError("角色或附加权限无效")
        with psycopg.connect(self.dsn) as db:
            member = db.execute(
                "SELECT 1 FROM team_members m JOIN team_projects p ON p.organization_id=m.organization_id WHERE p.project_id=%s AND m.subject=%s AND m.active",
                (project_id, subject),
            ).fetchone()
            if not member:
                raise ValueError("项目和成员必须属于同一有效组织")
            db.execute(
                "INSERT INTO team_project_members(project_id,subject,role,grants) VALUES(%s,%s,%s,%s) ON CONFLICT(project_id,subject) DO UPDATE SET role=excluded.role,grants=excluded.grants,active=true",
                (project_id, subject, role, list(grants)),
            )

    def revoke(self, organization_id, subject):
        import psycopg

        with psycopg.connect(self.dsn) as db:
            db.execute(
                "UPDATE team_members SET active=false WHERE organization_id=%s AND subject=%s",
                (organization_id, subject),
            )


def secret_file(path):
    path = Path(path)
    import os

    # Docker secrets are read-only mounts with 0444 mode by default. Local files
    # must be private; read-only Docker secret mounts are the explicit alternative.
    mounted = (
        path.is_relative_to(Path("/run/secrets")) and bool(os.statvfs(path).f_flag & os.ST_RDONLY)
        if path.exists()
        else False
    )
    if not path.is_file() or (path.stat().st_mode & 0o077 and not mounted):
        raise ValueError("凭证文件必须为私有文件或只读 Docker secret 挂载")
    return path.read_text().strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="团队数据库离线迁移和组织成员管理")
    parser.add_argument("--admin-dsn-file", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate")
    migration.add_argument("--runtime-role", default="creative_runtime")
    org = commands.add_parser("organization")
    org.add_argument("--id", required=True)
    org.add_argument("--name", required=True)
    org.add_argument("--budget", required=True)
    org.add_argument("--currency", choices=["CNY", "USD"], default="CNY")
    member = commands.add_parser("member")
    member.add_argument("--organization", required=True)
    member.add_argument("--subject", required=True)
    member.add_argument("--role", choices=["admin", "member"], default="member")
    grant = commands.add_parser("project-member")
    grant.add_argument("--project", required=True)
    grant.add_argument("--subject", required=True)
    grant.add_argument("--role", choices=list(ROLE_PERMISSIONS), required=True)
    grant.add_argument("--grants", nargs="*", choices=sorted(ALL_PERMISSIONS), default=[])
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--organization", required=True)
    revoke.add_argument("--subject", required=True)
    args = parser.parse_args(argv)
    dsn = secret_file(args.admin_dsn_file)
    if args.command == "migrate":
        from .postgres_store import migrate

        migrate(dsn, args.runtime_role)
    else:
        service = TeamAdmin(dsn)
        if args.command == "organization":
            service.organization(args.id, args.name, args.budget, args.currency)
        if args.command == "member":
            service.member(args.organization, args.subject, args.role)
        if args.command == "project-member":
            service.project_member(args.project, args.subject, args.role, grants=args.grants)
        if args.command == "revoke":
            service.revoke(args.organization, args.subject)
    print(json.dumps({"operation": args.command, "ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
