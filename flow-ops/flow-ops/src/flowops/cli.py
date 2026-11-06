"""CLI: generate workflow JSON, lint it, and show what was built.

    python -m flowops.cli generate            # write workflows/*.json
    python -m flowops.cli validate            # lint them (CI runs this)
    python -m flowops.cli check               # generate + confirm no drift
    python -m flowops.cli show ticket-triage-agent
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .validate import validate_directory, validate_file
from .workflows import ALL_WORKFLOWS, build_all

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = ROOT / "workflows"


def cmd_generate(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for wf in build_all():
        path = wf.write(out)
        print(f"  wrote {path.relative_to(ROOT)}  ({len(wf.nodes)} nodes)")
    return 0


def cmd_validate(out: Path) -> int:
    report = validate_directory(out)
    for finding in report.findings:
        print(f"  {finding}")
    print(
        f"\n  {len(report.errors)} error(s), {len(report.warnings)} warning(s) "
        f"across {len(list(out.glob('*.json')))} workflow(s)"
    )
    return 0 if report.ok else 1


def cmd_check(out: Path) -> int:
    """Regenerate into memory and compare against what is committed.

    Catches the failure mode this whole approach exists to prevent: someone
    edits a workflow on the n8n canvas, exports it over the generated file, and
    the Python definition silently stops being the source of truth.
    """
    drift = False
    for wf in build_all():
        from .builder import slug

        path = out / f"{slug(wf.name)}.json"
        expected = json.dumps(wf.to_json(), indent=2) + "\n"
        if not path.exists():
            print(f"  MISSING  {path.name} — run `generate`")
            drift = True
        elif path.read_text(encoding="utf-8") != expected:
            print(f"  DRIFT    {path.name} — committed JSON differs from the Python definition")
            drift = True
        else:
            print(f"  ok       {path.name}")
    if drift:
        print("\n  committed workflows are out of date; run `python -m flowops.cli generate`")
        return 1
    print("\n  no drift")
    return 0


def cmd_show(name: str, out: Path) -> int:
    path = out / f"{name}.json"
    if not path.exists():
        print(f"no such workflow: {name}", file=sys.stderr)
        print(f"available: {', '.join(sorted(ALL_WORKFLOWS))}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))

    print(f"\n  {data['name']}")
    print(f"  {data.get('meta', {}).get('description', '')}\n")
    print(f"  {len(data['nodes'])} nodes, {len(data['connections'])} with outgoing connections\n")
    for node in data["nodes"]:
        flags = []
        if node.get("retryOnFail"):
            flags.append("retry")
        if node.get("onError"):
            flags.append("continue-on-fail")
        if node.get("alwaysOutputData"):
            flags.append("always-output")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"    {node['name']:<28} {node['type'].split('.')[-1]}{suffix}")

    print("\n  connections")
    for source, by_type in data["connections"].items():
        for ctype, buckets in by_type.items():
            for i, bucket in enumerate(buckets):
                for link in bucket:
                    arrow = "-->" if ctype == "main" else f"..{ctype}..>"
                    label = f"[{i}]" if len(buckets) > 1 else ""
                    print(f"    {source}{label} {arrow} {link['node']}")

    report = validate_file(path)
    print(f"\n  lint: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    for finding in report.findings:
        print(f"    {finding}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flowops")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="write workflow JSON")
    sub.add_parser("validate", help="lint workflow JSON")
    sub.add_parser("check", help="fail if committed JSON has drifted")
    p_show = sub.add_parser("show", help="describe one workflow")
    p_show.add_argument("name")

    args = ap.parse_args(argv)

    if args.cmd == "generate":
        return cmd_generate(args.out)
    if args.cmd == "validate":
        return cmd_validate(args.out)
    if args.cmd == "check":
        return cmd_check(args.out)
    if args.cmd == "show":
        return cmd_show(args.name, args.out)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
