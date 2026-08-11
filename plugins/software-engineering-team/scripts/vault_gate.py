#!/usr/bin/env python3
"""Install or run the repository-portable single-vault quality gate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


COMPILER_SCRIPTS = (
    "vault_check.py", "ba_compile.py", "landscape_check.py",
    "experience_compile.py", "experience_artifact_check.py",
)
DATA_PATHS = (
    "skill-content/obsidian-vault/data",
    "skill-content/business-analysis/data",
    "skill-content/experience-modeling/data",
    "templates/vault",
)


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run(command: list[str], name: str) -> dict:
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=300)
    return {
        "name": name, "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    }


def workspace_for(project_root: Path) -> Path:
    candidates = [project_root / "workspace"]
    candidates.extend(sorted(path.parent for path in project_root.glob(
        "*/config.json")))
    for workspace in candidates:
        try:
            config = json.loads(
                (workspace / "config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        contract = config.get("agent_marketplace", {})
        owner = contract.get("team_id", "") if isinstance(contract, dict) else ""
        if (owner or config.get("team_id")) == "software-engineering-team":
            return workspace
    return project_root / "workspace"


def gate(project_root: Path, root: Path) -> dict:
    docs = workspace_for(project_root) / "docs"
    scripts = root / "scripts"
    results = [run([
        sys.executable, str(scripts / "vault_check.py"), "check",
        "--vault", str(docs), "--json",
    ], "closed-vault-schema-and-relations")]
    ba_root = docs / "business-analysis"
    if ba_root.is_dir():
        for space in sorted(ba_root.iterdir()):
            if (space / "space.md").is_file():
                results.append(run([
                    sys.executable, str(scripts / "ba_compile.py"), "check",
                    "--space", str(space), "--vault-root", str(docs),
                    "--gate", "approval", "--json",
                ], f"business-analysis:{space.name}"))
    solution = docs / "solution-design"
    if (solution / "landscape.md").is_file():
        results.append(run([
            sys.executable, str(scripts / "landscape_check.py"),
            "--tree", str(solution),
        ], "solution-design"))
    experience = docs / "experience-design"
    programs = experience / "programs"
    if programs.is_dir():
        for program in sorted(programs.glob("prg-*")):
            if (program / "program.md").is_file():
                results.append(run([
                    sys.executable, str(scripts / "experience_compile.py"),
                    "check", "--root", str(experience), "--program",
                    program.name.upper(), "--gate", "--json",
                ], f"experience-design:{program.name}"))
    return {
        "ok": all(item["ok"] for item in results),
        "project_root": str(project_root), "vault": str(docs),
        "results": results,
    }


def cmd_check(args) -> int:
    if Path(sys.argv[0]).suffix == ".pyz":
        with tempfile.TemporaryDirectory(prefix="vault-gate-") as temporary:
            with zipfile.ZipFile(sys.argv[0]) as archive:
                archive.extractall(temporary)
            result = gate(args.project_root.resolve(), Path(temporary))
    else:
        result = gate(args.project_root.resolve(), package_root())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["results"]:
            print(f"{'PASS' if item['ok'] else 'FAIL'} {item['name']}")
            if not item["ok"]:
                sys.stdout.write(item["stdout"])
                sys.stderr.write(item["stderr"])
    return 0 if result["ok"] else 1


def cmd_install(args) -> int:
    root = package_root()
    destination = (
        args.project_root.resolve() / ".github" / "agentrof" / "vault-gate.pyz"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vault-gate-build-") as temporary:
        staging = Path(temporary)
        shutil.copyfile(__file__, staging / "__main__.py")
        (staging / "scripts").mkdir()
        for name in COMPILER_SCRIPTS:
            shutil.copyfile(root / "scripts" / name, staging / "scripts" / name)
        for relative in DATA_PATHS:
            source = root / relative
            if source.is_dir():
                shutil.copytree(source, staging / relative)
        temporary_zip = destination.with_suffix(".tmp")
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(path.relative_to(staging).as_posix())
                    info.date_time = (1980, 1, 1, 0, 0, 0)
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, path.read_bytes(),
                                     compress_type=zipfile.ZIP_DEFLATED)
        temporary_zip.replace(destination)
    destination.chmod(0o755)
    print(destination)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--project-root", type=Path, required=True)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)
    install = sub.add_parser("install")
    install.add_argument("--project-root", type=Path, required=True)
    install.set_defaults(func=cmd_install)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
