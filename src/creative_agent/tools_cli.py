"""Offline, explicit backup/migration/evaluation commands; no supplier transport."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _storage(args):
    from .media import MediaService

    if args.data_dir is None:
        raise ValueError("请明确指定数据/媒体目录")
    if args.postgres_dsn_file:
        if not args.organization or not args.subject or args.postgres_dsn_file.is_symlink():
            raise ValueError("团队操作需要管理员组织、身份与非符号链接凭证文件")
        from .postgres_store import PostgresStore
        from .team_admin import secret_file
        from .team_auth import Actor, current_actor

        token = current_actor.set(Actor(subject=args.subject, organization_id=args.organization))
        try:
            store = PostgresStore(secret_file(args.postgres_dsn_file))
            store.require_org_admin()
            project = args.target_project if args.command == "import" else args.project
            store.require(project, "read", "export", *(["write"] if args.command == "import" else []))
            yield store, MediaService(store, args.data_dir / "media")
        finally:
            current_actor.reset(token)
    else:
        from .store import Store

        database = args.data_dir / "creative.sqlite3"
        if not database.is_file():
            raise ValueError("本地数据库不存在；请先建立明确的源或空目标项目")
        store = Store(database)
        yield store, MediaService(store, args.data_dir / "media")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="项目媒体备份、一次性恢复、盲评与真实来源成本统计；不调用供应商"
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--postgres-dsn-file", type=Path)
    parser.add_argument("--organization")
    parser.add_argument("--subject")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--project", required=True)
    export.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("import")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--target-project", required=True)
    restore.add_argument("--sha256", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--samples", type=Path, required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    evaluate.add_argument("--costs", type=Path)
    evaluate.add_argument("--output", type=Path)
    costs = commands.add_parser("cost-ledger")
    costs.add_argument("--project", required=True)
    costs.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            from .evaluation import evaluate_csv

            result = evaluate_csv(args.labels, args.samples, costs_csv=args.costs)
            if args.output:
                with args.output.open("x", encoding="utf-8") as output:
                    json.dump(result, output, ensure_ascii=False, indent=2)
        else:
            with _storage(args) as (store, media):
                if args.command == "export":
                    from .portability import export_bundle

                    result = export_bundle(store, media, args.project, args.output)
                elif args.command == "import":
                    from .portability import import_bundle

                    result = import_bundle(
                        store,
                        media,
                        args.archive,
                        target_project_id=args.target_project,
                        expected_sha256=args.sha256,
                    )
                else:
                    from .evaluation import write_source_cost_csv

                    write_source_cost_csv(store, args.project, args.output)
                    result = {
                        "path": str(args.output),
                        "evidence_level": "recorded_source_calls",
                        "live_validation": False,
                    }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:  # noqa: BLE001 - DSNs and DB/backend exceptions must never echo into CLI output
        print(
            "操作失败，未自动重试。请核对输入、授权、校验和与目标状态；连接凭证不会写入输出。",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
