#!/usr/bin/env python3
"""Determine the next legal preparation or delivery entry deterministically."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import marketplace_paths

SCRIPT_DIR = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def workspace(root: Path) -> tuple[Path | None, dict]:
    candidates = [root / "workspace" / "config.json"]
    candidates.extend(sorted(root.glob("*/config.json")))
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        config = read_json(path)
        if marketplace_paths.team_from_config(config) == "software-engineering-team":
            return path.parent, config
    return None, {}


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
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def any_approved_ba(docs: Path) -> bool:
    spaces = sorted((docs / "business-analysis").glob("*/space.md"))
    if not spaces:
        return False
    return all(
        fm_status(path) == "approved"
        and (path.parent / "_generated" / "registry.json").is_file()
        and command_clean([
            sys.executable, str(SCRIPT_DIR / "ba_compile.py"), "check",
            "--space", str(path.parent), "--vault-root", str(docs),
            "--gate", "approval", "--json",
        ])
        for path in spaces
    )


def approved_solution(docs: Path) -> bool:
    tree = docs / "solution-design"
    if not (tree / "landscape.md").is_file():
        return False
    engagements = sorted((tree / "engagements").glob("*.md"))
    return bool(engagements) \
        and all("Status: approved " in path.read_text(encoding="utf-8") for path in engagements) \
        and command_clean([
            sys.executable, str(SCRIPT_DIR / "landscape_check.py"),
            "--tree", str(tree),
        ])


def design_master(docs: Path) -> bool:
    master = docs / "design-system" / "MASTER.md"
    return master.is_file() and fm_status(master) == "approved" \
        and command_clean([
            sys.executable, str(SCRIPT_DIR / "design_system_compile.py"),
            "check", "--root", str(master.parent),
        ]) and command_clean([
            sys.executable, str(SCRIPT_DIR / "vault_check.py"), "check",
            "--vault", str(docs), "--scope", "design-system",
        ])


def approved_experience(docs: Path) -> bool:
    root = docs / "experience-design"
    programs = sorted((root / "programs").glob("prg-*"))
    if not programs:
        return False
    return all(
        fm_status(program / "program.md") == "approved"
        and command_clean([
            sys.executable, str(SCRIPT_DIR / "experience_compile.py"), "check",
            "--root", str(root), "--program", program.name.upper(),
            "--gate", "--json",
        ])
        for program in programs
    )


def backlog_state(work: Path, config: dict) -> tuple[bool, bool]:
    product = os.environ.get("AGENT_MARKETPLACE_HOME")
    if product:
        home = Path(product)
    else:
        vendor = Path(os.environ.get("AGENTROF_HOME", Path.home() / ".agentrof"))
        home = vendor / "agent-marketplace"
    launcher = home / "bin" / "pmo_cli.py"
    project_key = str(config.get("project_key", ""))
    if launcher.is_file() and project_key:
        completed = subprocess.run(
            [sys.executable, str(launcher), "backlog-plan", "status",
             "--project-key", project_key], capture_output=True, text=True,
            check=False, timeout=30,
        )
        if completed.returncode == 0:
            try:
                plans = json.loads(completed.stdout)
            except json.JSONDecodeError:
                plans = []
            if isinstance(plans, list):
                return (
                    any(item.get("status") in {"verified", "applied"}
                        for item in plans if isinstance(item, dict)),
                    any(item.get("status") == "applied"
                        for item in plans if isinstance(item, dict)),
                )
    # Draft files are only a diagnostic fallback; they never prove apply.
    plans = sorted((work.parent / ".agentrof" / "agent-marketplace"
                    / ".runtime" / "plan").glob("*.json"))
    approved = any(read_json(path).get("approved_hash") for path in plans)
    return approved, False


def inspect(root: Path, intent: str) -> dict:
    work, config = workspace(root)
    if work is None:
        return {"ok": False, "intent": intent, "next_entry": "setup", "reason": "managed workspace config is missing", "checks": {}}
    origin = str(config.get("project_origin", ""))
    state = config.get("agent_marketplace", {})
    vault_state = state.get("vault", {}) if isinstance(state, dict) else {}
    vault_active = (not state or state.get("contract_version", 0) < 3
                    or (isinstance(vault_state, dict)
                        and vault_state.get("status") == "active"))
    checks = {
        "project_origin": origin,
        "project_key": bool(config.get("project_key")),
        "vault_active": vault_active,
    }
    if origin not in {"greenfield", "existing"}:
        return {"ok": False, "intent": intent, "next_entry": "configure", "reason": "project_origin must be classified", "checks": checks}
    if not vault_active and intent in {
            "deliver", "delivery-lanes", "backlog-plan"}:
        return {
            "ok": False, "intent": intent, "next_entry": "upgrade",
            "reason": "vault adoption is pending; approve the exact adoption"
                      " plan and pass the full vault gate",
            "checks": checks,
        }
    docs = work / "docs"
    checks.update({
        "business_analysis": any_approved_ba(docs),
        "solution_design": approved_solution(docs),
        "design_system": design_master(docs),
        "experience_design": approved_experience(docs),
    })
    backlog_approved, backlog_applied = backlog_state(work, config)
    checks.update({"backlog_approved": backlog_approved, "backlog_applied": backlog_applied})
    if origin == "existing":
        return {"ok": True, "intent": intent, "next_entry": "deliver", "reason": "existing project uses scoped delivery preparation", "checks": checks}
    route = (("business_analysis", "business-analysis"),
             ("solution_design", "solution-design"),
             ("design_system", "design-system"),
             ("experience_design", "experience-design"),
             ("backlog_approved", "backlog-plan"))
    for key, entry in route:
        if not checks[key]:
            return {"ok": False, "intent": intent, "next_entry": entry,
                    "reason": f"greenfield preparation stage {key} is incomplete", "checks": checks}
    if not backlog_applied:
        return {"ok": False, "intent": intent, "next_entry": "backlog-plan",
                "reason": "approved backlog has not been atomically applied", "checks": checks}
    return {"ok": True, "intent": intent, "next_entry": "deliver",
            "alternatives": ["delivery-lanes"],
            "reason": "greenfield preparation is complete; delivery activation remains explicit", "checks": checks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "route"):
        p = sub.add_parser(name)
        p.add_argument("--project-root", required=True)
        p.add_argument("--intent", default="deliver")
        p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = inspect(Path(args.project_root).resolve(), args.intent)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["next_entry"])
        print(result["reason"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
