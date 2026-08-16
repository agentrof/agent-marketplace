#!/usr/bin/env python3
"""Route greenfield preparation from project-local documents only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import marketplace_paths

SCRIPT_DIR = Path(__file__).resolve().parent
TEAM = "software-engineering-team"
WORKSPACE = "workspace"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def workspace(root: Path) -> tuple[Path | None, dict, list[str]]:
    path = root / WORKSPACE / "config.json"
    config = read_json(path)
    conflicts = []
    for candidate in sorted(root.glob("*/config.json")):
        if candidate == path:
            continue
        value = read_json(candidate)
        if marketplace_paths.team_from_config(value) == TEAM:
            conflicts.append(candidate.parent.relative_to(root).as_posix())
    if marketplace_paths.team_from_config(config) == TEAM:
        return path.parent, config, conflicts
    return None, {}, conflicts


def fm_status(path: Path) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("status:"):
            return line.partition(":")[2].strip().strip("\"'")
    return ""


def command_clean(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def any_approved_ba(docs: Path) -> bool:
    spaces = sorted((docs / "business-analysis").glob("*/space.md"))
    if not spaces:
        return False
    return all(fm_status(path) == "approved" and command_clean([
        sys.executable, str(SCRIPT_DIR / "ba_compile.py"), "check",
        "--space", str(path.parent), "--vault-root", str(docs), "--gate", "approval", "--json",
    ]) for path in spaces)


def approved_solution(docs: Path) -> bool:
    tree = docs / "solution-design"
    landscape = tree / "landscape.md"
    engagements = sorted((tree / "engagements").glob("*.md"))
    return landscape.is_file() and bool(engagements) and all(
        "Status: approved " in path.read_text(encoding="utf-8") for path in engagements
    ) and command_clean([sys.executable, str(SCRIPT_DIR / "landscape_check.py"), "--tree", str(tree)])


def design_master(docs: Path) -> bool:
    master = docs / "design-system" / "MASTER.md"
    return master.is_file() and fm_status(master) == "approved" and command_clean([
        sys.executable, str(SCRIPT_DIR / "design_system_compile.py"), "check", "--root", str(master.parent),
    ]) and command_clean([
        sys.executable, str(SCRIPT_DIR / "vault_check.py"), "check", "--vault", str(docs), "--scope", "design-system",
    ])


def approved_experience(docs: Path) -> bool:
    root = docs / "experience-design"
    programs = sorted((root / "programs").glob("prg-*"))
    return bool(programs) and all(
        fm_status(program / "program.md") == "approved" and command_clean([
            sys.executable, str(SCRIPT_DIR / "experience_compile.py"), "check", "--root", str(root), "--program", program.name.upper(), "--gate", "--json",
        ]) for program in programs
    )


def backlog_state(work: Path) -> tuple[bool, bool]:
    script = SCRIPT_DIR / "backlog_compile.py"
    docs = work / "docs"
    approved = command_clean([sys.executable, str(script), "check", "--docs", str(docs), "--approved", "--json"])
    present = command_clean([sys.executable, str(script), "check", "--docs", str(docs), "--json"])
    return approved, present


def inspect(root: Path, intent: str) -> dict:
    work, config, conflicts = workspace(root)
    if conflicts:
        return {
            "ok": False, "intent": intent, "next_entry": "configure",
            "reason": "non-canonical or duplicate managed workspace: "
                      + ", ".join(conflicts),
            "checks": {"workspace_conflicts": conflicts},
        }
    if work is None:
        return {"ok": False, "intent": intent, "next_entry": "setup", "reason": "managed workspace config is missing", "checks": {}}
    origin = str(config.get("project_origin", ""))
    checks = {"project_origin": origin}
    if origin not in {"greenfield", "existing"}:
        return {"ok": False, "intent": intent, "next_entry": "configure", "reason": "project_origin must be classified", "checks": checks}
    docs = work / "docs"
    checks.update({"business_analysis": any_approved_ba(docs), "solution_design": approved_solution(docs), "design_system": design_master(docs), "experience_design": approved_experience(docs)})
    backlog_approved, backlog_present = backlog_state(work)
    checks.update({"backlog_approved": backlog_approved, "backlog_present": backlog_present})
    if origin == "existing":
        return {"ok": True, "intent": intent, "next_entry": "deliver", "reason": "existing project uses scoped delivery preparation", "checks": checks}
    route = (("business_analysis", "business-analysis"), ("solution_design", "solution-design"), ("design_system", "design-system"), ("experience_design", "experience-design"), ("backlog_present", "backlog-plan"))
    for key, entry in route:
        if not checks[key]:
            return {"ok": False, "intent": intent, "next_entry": entry, "reason": f"greenfield preparation stage {key} is incomplete", "checks": checks}
    if not backlog_approved:
        return {"ok": False, "intent": intent, "next_entry": "backlog-plan", "reason": "backlog documents exist but cross-epic and epic approvals are incomplete", "checks": checks}
    return {"ok": True, "intent": intent, "next_entry": "deliver", "alternatives": ["delivery-lanes"], "reason": "greenfield preparation is complete; delivery activation remains explicit", "checks": checks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "route"):
        p = sub.add_parser(name); p.add_argument("--project-root", required=True); p.add_argument("--intent", default="deliver"); p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = inspect(Path(args.project_root).resolve(), args.intent)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["next_entry"]); print(result["reason"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
