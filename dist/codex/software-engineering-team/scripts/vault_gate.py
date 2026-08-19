#!/usr/bin/env python3
"""Install or run the repository-portable single-vault quality gate."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


COMPILER_SCRIPTS = (
    "vault_check.py", "ba_compile.py", "landscape_check.py",
    "experience_compile.py", "experience_artifact_check.py", "architecture_compile.py",
    "design_system_compile.py", "backlog_compile.py",
    "requirement_compile.py", "requirement_route.py", "stage_package.py",
    "operation_compile.py", "delivery_governance.py", "marketplace_paths.py",
)
DATA_PATHS = (
    "skill-content/obsidian-vault/data",
    "skill-content/business-analysis/data",
    "skill-content/experience-modeling/data",
    "templates/vault",
)


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def packaged_scripts(root: Path) -> tuple[str, ...]:
    """Close the portable archive over every shipped sibling import."""
    scripts = root / "scripts"
    pending = list(COMPILER_SCRIPTS)
    included: set[str] = set()
    while pending:
        name = pending.pop()
        if name in included:
            continue
        source = scripts / name
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        included.add(name)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".", 1)[0])
        for module in sorted(modules):
            dependency = f"{module}.py"
            if (scripts / dependency).is_file() and dependency not in included:
                pending.append(dependency)
    return tuple(sorted(included))


def run(command: list[str], name: str) -> dict:
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=300)
    return {
        "name": name, "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    }


def team_resolver(root: Path):
    helper = root / "scripts" / "marketplace_paths.py"
    spec = importlib.util.spec_from_file_location(
        "portable_gate_marketplace_paths", helper
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"portable gate path resolver cannot load: {helper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.team_from_config


def workspace_for(project_root: Path, root: Path) -> tuple[Path, list[str]]:
    resolve_team = team_resolver(root)
    workspace = project_root / "workspace"
    findings = []
    try:
        config = json.loads(
            (workspace / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    if resolve_team(config) != "software-engineering-team":
        findings.append("canonical workspace/config.json is missing or not owned by the team")
    for candidate in sorted(project_root.glob("*/config.json")):
        if candidate.parent == workspace:
            continue
        try:
            config = json.loads(
                candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if resolve_team(config) == "software-engineering-team":
            findings.append(
                "non-canonical managed workspace: "
                + candidate.parent.relative_to(project_root).as_posix()
            )
    return workspace, findings


def frontmatter_status(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("status:"):
            return line.partition(":")[2].strip().strip("\"'")
    return ""


def gate(project_root: Path, root: Path) -> dict:
    workspace, workspace_findings = workspace_for(project_root, root)
    docs = workspace / "docs"
    scripts = root / "scripts"
    results = [{
        "name": "workspace-contract", "ok": not workspace_findings,
        "returncode": 1 if workspace_findings else 0,
        "stdout": "\n".join(workspace_findings), "stderr": "",
    }, run([
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
    design_system = docs / "design-system"
    if (design_system / "MASTER.md").is_file():
        results.append(run([
            sys.executable, str(scripts / "design_system_compile.py"),
            "check", "--root", str(design_system),
        ], "design-system"))
    operation = docs / "operation"
    for kind, filename in (("verification", "verification-contract.md"),
                           ("environment", "environment-contract.md")):
        if (operation / filename).is_file():
            results.append(run([
                sys.executable, str(scripts / "operation_compile.py"), "check",
                "--kind", kind, "--docs", str(docs), "--json",
            ], f"operation:{kind}"))
    governance = docs / "delivery" / "governance" / "governance.md"
    if governance.is_file():
        results.append(run([
            sys.executable, str(scripts / "delivery_governance.py"), "check",
            "--docs", str(docs), "--json",
        ], "delivery-governance"))
    experience = docs / "experience-design"
    legacy_experience = experience / "baselines"
    if legacy_experience.exists():
        results.append({"name": "legacy-experience-hard-cut", "ok": False, "returncode": 1,
                        "stdout": "legacy experience-design/baselines tree is forbidden", "stderr": ""})
    packages = experience / "experiences"
    if packages.is_dir():
        for package in sorted(path for path in packages.iterdir() if path.is_dir()):
            if package.name.startswith("exp-"):
                results.append({"name": f"retired-experience-prefix:{package.name}", "ok": False,
                                "returncode": 1,
                                "stdout": "Experience process slugs must not use the retired exp- prefix", "stderr": ""})
            if (package / "experience.md").is_file():
                results.append(run([
                    sys.executable, str(scripts / "experience_compile.py"),
                    "check", "--experience-root", str(package), "--gate", "--json",
                ], f"experience:{package.name}"))
    requirements = docs / "requirements"
    if requirements.is_dir() and any(requirements.glob("req-*.md")):
        results.append(run([
            sys.executable, str(scripts / "requirement_compile.py"), "check",
            "--docs", str(docs), "--json",
        ], "requirements"))
    architecture = docs / "system-architecture"
    legacy_architecture = [name for name in ("api-contract.md", "data-model.md", "threat-model.md", "environment.md") if (architecture / name).exists()]
    if legacy_architecture:
        results.append({"name": "legacy-system-architecture-hard-cut", "ok": False, "returncode": 1,
                        "stdout": "legacy root architecture records are forbidden: " + ", ".join(legacy_architecture), "stderr": ""})
    if (architecture / "architecture.md").is_file():
        results.append(run([
            sys.executable, str(scripts / "architecture_compile.py"), "check",
            "--docs", str(docs), "--json",
        ], "system-architecture"))
    backlog = docs / "backlog" / "backlog.md"
    if backlog.is_file():
        command = [
            sys.executable, str(scripts / "backlog_compile.py"), "check",
            "--docs", str(docs), "--json",
        ]
        approved = frontmatter_status(backlog) == "approved"
        if approved:
            command.insert(-1, "--approved")
        results.append(run(
            command, "backlog:approved" if approved else "backlog:draft"
        ))
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
        for name in packaged_scripts(root):
            shutil.copyfile(root / "scripts" / name, staging / "scripts" / name)
        for relative in DATA_PATHS:
            source = root / relative
            if source.is_dir():
                shutil.copytree(source, staging / relative)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".vault-gate.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary_zip = Path(raw_temporary)
        try:
            with zipfile.ZipFile(
                temporary_zip, "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        info = zipfile.ZipInfo(path.relative_to(staging).as_posix())
                        info.date_time = (1980, 1, 1, 0, 0, 0)
                        info.external_attr = 0o644 << 16
                        archive.writestr(
                            info, path.read_bytes(),
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
            with temporary_zip.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary_zip, destination)
        finally:
            temporary_zip.unlink(missing_ok=True)
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
