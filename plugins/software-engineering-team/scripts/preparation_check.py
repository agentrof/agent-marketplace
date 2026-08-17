#!/usr/bin/env python3
"""Route preparation from approved files and scoped Git handoffs only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import marketplace_paths

SCRIPT_DIR = Path(__file__).resolve().parent
TEAM = "software-engineering-team"
WORKSPACE = "workspace"
STAGE_PATHS = {
    "business_analysis": (
        "workspace/docs/business-analysis",
        "workspace/docs/maps/business-analysis.md",
    ),
    "solution_design": (
        "workspace/docs/solution-design",
        "workspace/docs/maps/solution-design.md",
    ),
    "design_system": (
        "workspace/docs/design-system",
        "workspace/docs/maps/design-system.md",
    ),
    "experience_design": (
        "workspace/docs/experience-design",
        "workspace/docs/maps/experience-design.md",
    ),
    "backlog": (
        "workspace/docs/backlog",
        "workspace/docs/maps/backlog.md",
    ),
}
WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
GENERATED_RELATION_RE = re.compile(
    r"(?ms)^## Related knowledge "
    r"<!-- sec: relations:generated:start -->.*?"
    r"<!-- sec: relations:generated:end -->\s*"
)
FENCED_BLOCK_RE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^\s*(?P=fence)\s*$"
)
TRACKED_VAULT_PAYLOAD = (
    "workspace/docs/.obsidian/app.json",
    "workspace/docs/.obsidian/appearance.json",
    "workspace/docs/.obsidian/core-plugins.json",
    "workspace/docs/.obsidian/graph.json",
    "workspace/docs/.obsidian/types.json",
    "workspace/docs/.obsidian/snippets",
    ".github/agentrof/vault-gate.pyz",
)


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


def wikilink_targets(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    text = GENERATED_RELATION_RE.sub("", text)
    text = FENCED_BLOCK_RE.sub("", text)
    targets: list[str] = []
    for match in WIKILINK_RE.finditer(text):
        inner = match.group(1)
        for separator in ("\\|", "|"):
            if separator in inner:
                inner = inner.split(separator, 1)[0]
                break
        target = inner.split("#", 1)[0].strip()
        target_path = PurePosixPath(target)
        if (target and not target_path.is_absolute()
                and all(part not in {"", ".", ".."}
                        for part in target_path.parts)):
            targets.append(target)
    return targets


def source_package_roots(docs: Path, path: Path) -> list[Path]:
    """Map a linked owner note to the smallest compiler-bound package."""
    try:
        parts = path.relative_to(docs).parts
    except ValueError:
        return []
    roots: list[Path] = []
    if len(parts) >= 2 and parts[0] == "business-analysis":
        roots.extend((docs / "business-analysis" / parts[1],
                      docs / "maps/business-analysis.md"))
    elif parts and parts[0] == "solution-design":
        roots.extend((docs / "solution-design",
                      docs / "maps/solution-design.md"))
    elif parts and parts[0] == "design-system":
        roots.extend((docs / "design-system",
                      docs / "maps/design-system.md"))
    elif len(parts) >= 4 and parts[:2] == ("experience-design", "programs"):
        roots.extend((docs / "experience-design/programs" / parts[2],
                      docs / "experience-design/experience.md",
                      docs / "maps/experience-design.md"))
    elif parts and parts[0] == "system-architecture":
        roots.extend((docs / "system-architecture",
                      docs / "maps/system-architecture.md"))
    elif parts and parts[0] == "issues":
        roots.append(docs / "maps/issues.md")
    return roots


def backlog_linked_paths(root: Path) -> list[str]:
    """Close a backlog over linked evidence and compiler-owned packages."""
    docs = root / "workspace" / "docs"
    backlog = docs / "backlog"
    if not backlog.is_dir():
        return []
    required: set[Path] = set()
    queued = list(sorted(backlog.rglob("*.md")))
    scanned: set[Path] = set()

    def add(candidate: Path) -> None:
        if not (candidate.exists() or candidate.is_symlink()):
            return
        if candidate != backlog and backlog not in candidate.parents:
            required.add(candidate)
        if candidate.is_dir() and not candidate.is_symlink():
            queued.extend(sorted(candidate.rglob("*.md")))
        elif (candidate.suffix == ".md"
              and docs / "maps" not in candidate.parents):
            queued.append(candidate)

    while queued:
        note = queued.pop(0)
        if note in scanned:
            continue
        scanned.add(note)
        for target in wikilink_targets(note):
            if target == "backlog" or target.startswith("backlog/"):
                continue
            candidate = docs / f"{target}.md"
            add(candidate)
            for package in source_package_roots(docs, candidate):
                add(package)
    return sorted(path.relative_to(root).as_posix() for path in required)


def handoff_paths(root: Path, stages: list[str]) -> list[str]:
    paths = ["workspace/config.json", "workspace/docs/home.md",
             *TRACKED_VAULT_PAYLOAD]
    for stage in stages:
        paths.extend(STAGE_PATHS[stage])
    if "backlog" in stages:
        paths.extend(backlog_linked_paths(root))
    return list(dict.fromkeys(paths))


def git_handoff(root: Path, stages: list[str]) -> dict:
    """Check only completed-stage inputs, never unrelated active work."""
    paths = handoff_paths(root, stages)
    files: list[str] = []
    missing_paths: list[str] = []
    symlink_paths: list[str] = []
    for relative in paths:
        target = root / relative
        if target.is_symlink():
            symlink_paths.append(relative)
            continue
        if target.is_file():
            files.append(relative)
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                relative_path = path.relative_to(root).as_posix()
                if path.is_symlink():
                    symlink_paths.append(relative_path)
                elif path.is_file():
                    files.append(relative_path)
        else:
            missing_paths.append(relative)
    tracked_result = subprocess.run(
        ["git", "ls-files", "--", *paths], cwd=root,
        capture_output=True, text=True, check=False,
    )
    tracked = set(tracked_result.stdout.splitlines()) \
        if tracked_result.returncode == 0 else set()
    untracked = sorted(path for path in files if path not in tracked)
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *paths],
        cwd=root, capture_output=True, text=True, check=False,
    )
    changes = status_result.stdout.splitlines() \
        if status_result.returncode == 0 else ["Git status failed"]
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
        capture_output=True, text=True, check=False,
    )
    blockers = []
    if tracked_result.returncode != 0:
        blockers.append("Git tracked-file inspection failed")
    if head.returncode != 0:
        blockers.append("the repository has no commit yet")
    if missing_paths:
        blockers.append(
            "required handoff paths are missing: " + ", ".join(missing_paths)
        )
    if symlink_paths:
        blockers.append(
            "required handoff paths are symlinked: " + ", ".join(symlink_paths)
        )
    if untracked:
        blockers.append("required files are not tracked: " + ", ".join(untracked))
    if changes:
        blockers.append(
            "required files have uncommitted changes: " + "; ".join(changes)
        )
    return {
        "ok": not blockers,
        "stages": stages,
        "required_paths": paths,
        "missing_paths": missing_paths,
        "symlink_paths": symlink_paths,
        "untracked_files": untracked,
        "changes": changes,
        "blockers": blockers,
    }


def blocked_handoff(intent: str, entry: str, stage: str, checks: dict,
                    handoff: dict) -> dict:
    checks["git_handoff"] = handoff
    return {
        "ok": False,
        "intent": intent,
        "next_entry": entry,
        "reason": (
            f"{stage} is approved but its Git handoff is incomplete: "
            + "; ".join(handoff["blockers"])
        ),
        "checks": checks,
    }


def inspect(root: Path, intent: str) -> dict:
    if intent == "requirement":
        try:
            import requirement_route
            return requirement_route.route(root / WORKSPACE / "docs")
        except (ImportError, OSError, ValueError) as exc:
            return {
                "ok": False,
                "intent": intent,
                "next_entry": "requirement",
                "reason": f"Requirement routing failed: {exc}",
                "checks": {},
            }
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
    docs = work / "docs"
    checks = {"business_analysis": any_approved_ba(docs), "solution_design": approved_solution(docs), "design_system": design_master(docs), "experience_design": approved_experience(docs)}
    backlog_approved, backlog_present = backlog_state(work)
    checks.update({"backlog_approved": backlog_approved, "backlog_present": backlog_present})
    # Requirement Flow owns applicability and ordering. Once the approved
    # backlog has completed its tracked handoff, the next explicit boundary is
    # Delivery Flow; otherwise the unified Requirement entry remains the only
    # route. No project-wide origin is inferred.
    if backlog_approved:
        handoff = git_handoff(root, ["backlog"])
        if not handoff["ok"]:
            return blocked_handoff(intent, "deliver", "backlog", checks, handoff)
        checks["git_handoff"] = handoff
        return {
            "ok": True,
            "intent": intent,
            "next_entry": "deliver",
            "alternatives": ["delivery-plan"],
            "reason": "approved backlog is committed; Delivery Flow remains explicit",
            "checks": checks,
        }
    return {
        "ok": False, "intent": intent, "next_entry": "requirement",
        "reason": "Requirement Flow determines applicable stages and backlog eligibility",
        "checks": checks,
    }


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
