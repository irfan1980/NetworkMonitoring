"""Standalone CLI entry point (PRD §2.6: "the same toolkit must serve BOTH manual
verification and the automated suite"). See `relay/fixtures_cli.py` for the thin
launcher script; this module holds the actual `argparse` logic.

Usage (from `relay/`):
    python fixtures_cli.py list
    python fixtures_cli.py seed FIX01
    python fixtures_cli.py teardown FIX01 --facts-file .fixture-facts/FIX01.json
    python fixtures_cli.py rowcounts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import elevation, scenarios

FACTS_DIR = Path(__file__).resolve().parents[3] / ".fixture-facts"


def _facts_path(code: str) -> Path:
    FACTS_DIR.mkdir(exist_ok=True)
    return FACTS_DIR / f"{code}.json"


def cmd_list(_args: argparse.Namespace) -> int:
    for code in scenarios.all_codes():
        scn = scenarios.get(code)
        flag = " [ELEVATED]" if scn.elevated else ""
        print(f"{code}: {scn.title}{flag}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    scn = scenarios.get(args.code)
    if scn.elevated and not elevation.is_elevated():
        print(
            f"refusing to seed {scn.code}: requires an elevated process "
            "(Run as Administrator) -- see relay/README.md 'Elevated fixture subset'.",
            file=sys.stderr,
        )
        return 2
    facts = scn.seed()
    _facts_path(scn.code).write_text(json.dumps(facts, default=str, indent=2), encoding="utf-8")
    print(f"seeded {scn.code}: {json.dumps(facts, default=str)}")
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    scn = scenarios.get(args.code)
    facts_path = _facts_path(scn.code)
    if not facts_path.exists():
        print(f"no saved facts for {scn.code} at {facts_path} -- pass --facts-file or re-seed first", file=sys.stderr)
        return 2
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    scn.teardown(facts)
    facts_path.unlink(missing_ok=True)
    print(f"tore down {scn.code}")
    return 0


def cmd_rowcounts(_args: argparse.Namespace) -> int:
    from . import connections, rows

    with connections.mysql() as conn:
        mysql_out = rows.count_all(conn, rows.MYSQL, "outbox")
        mysql_in = rows.count_all(conn, rows.MYSQL, "inbox")
    with connections.postgresql() as conn:
        pg_out = rows.count_all(conn, rows.POSTGRESQL, "outbox")
        pg_in = rows.count_all(conn, rows.POSTGRESQL, "inbox")
    with connections.mssql() as conn:
        ms_out = rows.count_all(conn, rows.MSSQL, "outbox")
        ms_in = rows.count_all(conn, rows.MSSQL, "inbox")
    print(json.dumps(
        {
            "mysql": {"outbox": mysql_out, "inbox": mysql_in},
            "postgresql": {"outbox": pg_out, "inbox": pg_in},
            "mssql": {"outbox": ms_out, "inbox": ms_in},
        },
        indent=2,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List every scenario code and title.").set_defaults(func=cmd_list)

    p_seed = sub.add_parser("seed", help="Seed one scenario, save its facts for later teardown.")
    p_seed.add_argument("code", choices=scenarios.all_codes())
    p_seed.set_defaults(func=cmd_seed)

    p_teardown = sub.add_parser("teardown", help="Tear down one previously-seeded scenario.")
    p_teardown.add_argument("code", choices=scenarios.all_codes())
    p_teardown.set_defaults(func=cmd_teardown)

    sub.add_parser("rowcounts", help="Print current outbox/inbox row counts on all three platforms.").set_defaults(
        func=cmd_rowcounts
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
