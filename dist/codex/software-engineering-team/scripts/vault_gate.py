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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath


COMPILER_SCRIPTS = (
    "vault_check.py", "ba_compile.py", "landscape_check.py",
    "experience_compile.py", "experience_application_check.py",
    "architecture_compile.py",
    "design_system_compile.py", "backlog_compile.py",
    "requirement_compile.py", "requirement_route.py", "stage_package.py",
    "operation_compile.py", "delivery_governance.py", "marketplace_paths.py",
    "delivery_closure.py", "process_policy.py",
)
DATA_PATHS = (
    "skill-content/obsidian-vault/data",
    "skill-content/business-analysis/data",
    "skill-content/experience-modeling/data",
    "skill-content/design-system/data",
    "templates/vault",
)
DATA_ROOTS = ("skill-content", "templates")
SWITCH_REGISTRY = "skill-content/configure/data/process-switches.json"


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def joined_literals(division: ast.BinOp) -> list[str]:
    """Return each run of string literals one ``/`` chain joins, as a path."""
    operands = []
    node = division
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        operands.append(node.right)
        node = node.left
    operands.append(node)
    joined, run = [], []
    for operand in [*reversed(operands), None]:
        if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            run.append(operand.value)
        elif run:
            joined.append("/".join(run))
            run = []
    return joined


def packaged_closure(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Close the portable archive over every shipped sibling import and over
    every package data path a shipped script names.

    A data path is named by a string literal, or by string literals joined
    with ``/``, under a DATA_ROOTS directory. DATA_PATHS adds whole data
    directories. A named path the package does not hold is skipped.
    """
    scripts = root / "scripts"
    pending = list(COMPILER_SCRIPTS)
    included: set[str] = set()
    names = set(DATA_PATHS)
    while pending:
        name = pending.pop()
        if name in included:
            continue
        source = scripts / name
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        included.add(name)
        modules = set()
        divisions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                divisions.append(node)
        inner = {id(node.left) for node in divisions}
        for node in divisions:
            if id(node) not in inner:
                names.update(joined_literals(node))
        for module in sorted(modules):
            dependency = f"{module}.py"
            if (scripts / dependency).is_file() and dependency not in included:
                pending.append(dependency)
    # The process switch registry names the data files that declare its
    # parameters, and loading the registry reads every one of them.
    registry = root / SWITCH_REGISTRY
    if SWITCH_REGISTRY in names and registry.is_file():
        try:
            switches = json.loads(registry.read_text(encoding="utf-8")).get("switches", {})
        except (OSError, ValueError, AttributeError):
            switches = {}
        for spec in switches.values() if isinstance(switches, dict) else []:
            declared = spec.get("parameters", {}).get("declared_by", {}) \
                if isinstance(spec, dict) and isinstance(spec.get("parameters"), dict) else {}
            if isinstance(declared, dict) and isinstance(declared.get("path"), str):
                names.add(declared["path"])
    data: list[str] = []
    for parts in sorted({PurePosixPath(name).parts for name in names
                         if name.startswith(DATA_ROOTS)}):
        relative = "/".join(parts)
        if (len(parts) > 1 and parts[0] in DATA_ROOTS and ".." not in parts
                and (root / relative).exists()
                and not any(relative.startswith(f"{kept}/") for kept in data)):
            data.append(relative)
    return tuple(sorted(included)), tuple(data)


def root_folders(root: Path) -> tuple[str, ...]:
    """Return every folder directly under a DATA_ROOTS directory.

    The archive lists each one, so a script that lists a data root sees the
    package's folders, as landscape_check does to resolve the installed
    method skills. A listed folder holds only the files the closure bundles.
    """
    return tuple(sorted(
        f"{name}/{child.name}"
        for name in DATA_ROOTS if (root / name).is_dir()
        for child in (root / name).iterdir() if child.is_dir()
    ))


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


def process_profile(scripts: Path, docs: Path) -> str:
    completed = subprocess.run(
        [sys.executable, str(scripts / "process_policy.py"), "value", "--docs", str(docs),
         "--switch", "process_profile"], capture_output=True, text=True, check=False)
    try:
        return json.loads(completed.stdout).get("value", "governed")
    except (ValueError, AttributeError):
        return "governed"


def changed_docs(project_root: Path, docs: Path, base: str | None) -> set[str] | None:
    """Vault-relative paths changed since *base*; None runs every package check."""
    if not base:
        return None
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(project_root), *args],
                              capture_output=True, text=True, check=False)
    fork = git("merge-base", base, "HEAD")
    # Against the fork point, so uncommitted and untracked edits count too.
    listed = [git("diff", "--name-only", fork.stdout.strip()),
              git("ls-files", "--others", "--exclude-standard")]
    if fork.returncode != 0 or any(item.returncode != 0 for item in listed):
        return None
    prefix = docs.relative_to(project_root).as_posix() + "/"
    return {line[len(prefix):] for item in listed for line in item.stdout.splitlines()
            if line.startswith(prefix)}


def gate(project_root: Path, root: Path, base: str | None = None) -> dict:
    workspace, workspace_findings = workspace_for(project_root, root)
    docs = workspace / "docs"
    scripts = root / "scripts"
    # At fast_lane the file on the target branch is in force: approval stamps are not re-verified.
    fast = process_profile(scripts, docs) == "fast_lane"
    changed = changed_docs(project_root, docs, base)
    jobs: list[tuple[list[str], str]] = []
    results = [{
        "name": "workspace-contract", "ok": not workspace_findings,
        "returncode": 1 if workspace_findings else 0,
        "stdout": "\n".join(workspace_findings), "stderr": "",
    }]

    def queue(command: list[str], name: str, scope: Path) -> None:
        relative = scope.relative_to(docs).as_posix()
        if changed is None or any(path == relative or path.startswith(relative + "/") for path in changed):
            jobs.append((command, name))

    jobs.append(([
        sys.executable, str(scripts / "vault_check.py"), "check",
        "--vault", str(docs), "--json",
    ], "closed-vault-schema-and-relations"))
    ba_root = docs / "business-analysis"
    if ba_root.is_dir():
        for space in sorted(ba_root.iterdir()):
            if (space / "space.md").is_file():
                queue([
                    sys.executable, str(scripts / "ba_compile.py"), "check",
                    "--space", str(space), "--vault-root", str(docs),
                    *([] if fast else ["--gate", "approval"]), "--json",
                ], f"business-analysis:{space.name}", space)
    solution = docs / "solution-design"
    if (solution / "landscape.md").is_file():
        queue([
            sys.executable, str(scripts / "landscape_check.py"),
            "--tree", str(solution),
        ], "solution-design", solution)
    design_system = docs / "design-system"
    if (design_system / "MASTER.md").is_file():
        queue([
            sys.executable, str(scripts / "design_system_compile.py"),
            "check", "--root", str(design_system),
        ], "design-system", design_system)
    operation = docs / "operation"
    for kind, filename in (("verification", "verification-contract.md"),
                           ("environment", "environment-contract.md")):
        if (operation / filename).is_file():
            queue([
                sys.executable, str(scripts / "operation_compile.py"), "check",
                "--kind", kind, "--docs", str(docs), "--json",
            ], f"operation:{kind}", operation)
    governance = docs / "delivery" / "governance" / "governance.md"
    if governance.is_file():
        queue([
            sys.executable, str(scripts / "delivery_governance.py"), "check",
            "--docs", str(docs), "--json",
        ], "delivery-governance", governance.parent)
    experience = docs / "experience-design"
    experience_identity_errors: list[str] = []
    if experience.is_symlink():
        experience_identity_errors.append("experience-design root is symlinked")
    elif experience.is_dir():
        for path in sorted(experience.rglob("*")):
            relative = path.relative_to(docs).as_posix()
            if path.is_symlink():
                experience_identity_errors.append(
                    f"{relative} is symlinked"
                )
            elif path.is_file() and path.stat().st_nlink != 1:
                experience_identity_errors.append(
                    f"{relative} has more than one filesystem link"
                )
    if experience_identity_errors:
        results.append({
            "name": "experience-path-identity",
            "ok": False,
            "returncode": 1,
            "stdout": "; ".join(experience_identity_errors),
            "stderr": "",
        })
    legacy_experience = experience / "baselines"
    if legacy_experience.exists():
        results.append({"name": "legacy-experience-hard-cut", "ok": False, "returncode": 1,
                        "stdout": "legacy experience-design/baselines tree is forbidden", "stderr": ""})
    packages = experience / "experiences"
    experience_packages = sorted(
        (path for path in packages.iterdir()
         if path.is_dir() and not path.is_symlink()
         and (path / "experience.md").is_file()),
        key=lambda path: path.name,
    ) if packages.is_dir() else []
    receipt_state = experience / "_ledger" / "application-revisions.json"
    if (experience_packages or receipt_state.exists()
            or receipt_state.is_symlink()):
        queue([
            sys.executable,
            str(scripts / "experience_application_check.py"),
            "check", "--root", str(experience), *([] if fast else ["--gate"]), "--json",
        ], "experience-application", experience)
    if packages.is_dir():
        for package in sorted(
                path for path in packages.iterdir()
                if path.is_dir() and not path.is_symlink()):
            if package.name.startswith("exp-"):
                results.append({"name": f"retired-experience-prefix:{package.name}", "ok": False,
                                "returncode": 1,
                                "stdout": "Experience process slugs must not use the retired exp- prefix", "stderr": ""})
            if (package / "experience.md").is_file():
                queue([
                    sys.executable, str(scripts / "experience_compile.py"),
                    "check", "--experience-root", str(package),
                    *([] if fast else ["--gate"]), "--json",
                ], f"experience:{package.name}", package)
    requirements = docs / "requirements"
    if requirements.is_dir() and any(requirements.glob("req-*.md")):
        queue([
            sys.executable, str(scripts / "requirement_compile.py"), "check",
            "--docs", str(docs), "--json",
        ], "requirements", requirements)
    architecture = docs / "system-architecture"
    legacy_architecture = [name for name in ("api-contract.md", "data-model.md", "threat-model.md", "environment.md") if (architecture / name).exists()]
    if legacy_architecture:
        results.append({"name": "legacy-system-architecture-hard-cut", "ok": False, "returncode": 1,
                        "stdout": "legacy root architecture records are forbidden: " + ", ".join(legacy_architecture), "stderr": ""})
    if (architecture / "architecture.md").is_file():
        queue([
            sys.executable, str(scripts / "architecture_compile.py"), "check",
            "--docs", str(docs), "--json",
        ], "system-architecture", architecture)
    backlog = docs / "backlog" / "backlog.md"
    if backlog.is_file():
        command = [
            sys.executable, str(scripts / "backlog_compile.py"), "check",
            "--docs", str(docs), "--json",
        ]
        approved = frontmatter_status(backlog) == "approved" and not fast
        if approved:
            command.insert(-1, "--approved")
        if fast:
            command.insert(-1, "--trust-bindings")
        queue(command, "backlog:approved" if approved else "backlog:draft", backlog.parent)
    with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), os.cpu_count() or 1))) as pool:
        results.extend(pool.map(lambda job: run(*job), jobs))
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
            result = gate(args.project_root.resolve(), Path(temporary), args.changed_from)
    else:
        result = gate(args.project_root.resolve(), package_root(), args.changed_from)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["results"]:
            print(f"{'PASS' if item['ok'] else 'FAIL'} {item['name']}")
            if not item["ok"]:
                sys.stdout.write(item["stdout"])
                sys.stderr.write(item["stderr"])
    return 0 if result["ok"] else 1


def closure_check(args, root: Path) -> int:
    """Run the Delivery closure check of one pull request with the scripts under *root*.

    The scripts are the base branch's archive, never the pull request's, and
    every pull request value arrives as an argument, never as shell text.
    """
    command = [
        sys.executable, str(root / "scripts" / "delivery_git.py"), "closure-check",
        "--project-root", str(args.project_root.resolve()), "--pr-url", args.pr_url,
        "--head", args.head, "--head-ref", args.head_ref, "--base", args.base,
        "--target", args.target, "--remote", args.remote,
    ]
    return subprocess.run(command, check=False).returncode


def cmd_delivery_closure(args) -> int:
    if Path(sys.argv[0]).suffix == ".pyz":
        with tempfile.TemporaryDirectory(prefix="vault-gate-") as temporary:
            with zipfile.ZipFile(sys.argv[0]) as archive:
                archive.extractall(temporary)
            return closure_check(args, Path(temporary))
    return closure_check(args, package_root())


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
        scripts, data = packaged_closure(root)
        for name in scripts:
            shutil.copyfile(root / "scripts" / name, staging / "scripts" / name)
        for relative in data:
            source, target = root / relative, staging / relative
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".vault-gate.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary_zip = Path(raw_temporary)
        try:
            with zipfile.ZipFile(
                temporary_zip, "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for folder in root_folders(root):
                    info = zipfile.ZipInfo(f"{folder}/")
                    info.date_time = (1980, 1, 1, 0, 0, 0)
                    # 0x10 is the MS-DOS directory attribute.
                    info.external_attr = (0o40755 << 16) | 0x10
                    archive.writestr(info, b"")
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        info = zipfile.ZipInfo(path.relative_to(staging).as_posix())
                        info.date_time = (1980, 1, 1, 0, 0, 0)
                        info.external_attr = 0o644 << 16
                        archive.writestr(
                            info, path.read_bytes(),
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
            # Windows does not permit fsync on a read-only descriptor.  Open
            # the completed archive read/write so the durability barrier has
            # the same semantics on every supported host.
            with temporary_zip.open("r+b") as handle:
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
    check.add_argument("--changed-from", help="base ref: run only the package checks whose tree changed since it")
    check.set_defaults(func=cmd_check)
    closure = sub.add_parser("delivery-closure")
    closure.add_argument("--project-root", type=Path, required=True)
    closure.add_argument("--pr-url", required=True)
    closure.add_argument("--head", required=True)
    closure.add_argument("--head-ref", default="")
    closure.add_argument("--base", required=True)
    closure.add_argument("--target", default="")
    closure.add_argument("--remote", default="origin")
    closure.set_defaults(func=cmd_delivery_closure)
    install = sub.add_parser("install")
    install.add_argument("--project-root", type=Path, required=True)
    install.set_defaults(func=cmd_install)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
