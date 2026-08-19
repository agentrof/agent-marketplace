#!/usr/bin/env python3
"""Lifecycle compiler for the project Operation contracts.

Operation truth lives in ``workspace/docs/operation``. Verification and
environment contracts are independent, approved revisioned documents. They
are deliberately outside product-stage package hashes: a command change is a
delivery concern, although its cited accepted Solution decision is rechecked
at every approval and consumption boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ba_compile import parse_frontmatter
import stage_package


KINDS = {"verification", "environment"}
TYPE_FOR = {
    "verification": "verification-contract",
    "environment": "environment-contract",
}
FILE_FOR = {
    "verification": "verification-contract.md",
    "environment": "environment-contract.md",
}
COMMAND_FIELDS = {
    "verification": (
        "test_command", "mutation_command", "dependency_audit_command",
    ),
    "environment": ("env_command",),
}
WORKDIR_FIELDS = {
    "verification": (
        "test_workdir", "mutation_workdir", "dependency_audit_workdir",
    ),
    "environment": ("env_workdir",),
}
DISPOSITIONS = {"required", "not_applicable"}
TOKEN_RE = re.compile(r"(?:\{\{[^{}]+\}\}|\$\{[^{}]+\})")
CREDENTIAL_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|password|secret)\s*=\s*[^\s]+"
)


def docs_root(value: str | None) -> Path:
    root = Path(value or "workspace/docs").resolve()
    if root.name == "docs":
        return root
    if (root / "workspace" / "docs").is_dir():
        return root / "workspace" / "docs"
    if (root / "docs").is_dir():
        return root / "docs"
    return root


def contract_path(docs: Path, kind: str) -> Path:
    return docs / "operation" / FILE_FOR[kind]


def parse(path: Path) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
    if error:
        raise ValueError(f"{path}: {error}")
    return props, "\n".join(path.read_text(encoding="utf-8").splitlines()[body_line - 1:]).strip()


def scalar(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(f"  - {item}" for item in value)
    return str(value)


def render(props: dict, body: str) -> str:
    lines = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", "", body.strip(), ""])
    return "\n".join(lines)


def source_hash(props: dict, body: str) -> str:
    excluded = {"source_hash", "approved_at_utc"}
    view = {key: value for key, value in props.items() if key not in excluded}
    return "sha256:" + hashlib.sha256(
        json.dumps({"frontmatter": view, "body": body}, ensure_ascii=False,
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def valid_workdir(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(
        item not in {"", ".", ".."} for item in path.parts
    )


def accepted_solution_ref(docs: Path, value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    ref = value.removeprefix("[[").split("|", 1)[0].removesuffix("]] ").removesuffix("]]")
    ref = ref.removesuffix(".md")
    if not ref.startswith("solution-design/decisions/"):
        return False
    candidate = docs / (ref + ".md")
    if not candidate.is_file():
        return False
    try:
        props, _body = parse(candidate)
    except (OSError, ValueError):
        return False
    _receipt, package_errors = stage_package.verify(
        docs, "solution-design", "solution-design/landscape"
    )
    return props.get("status") == "accepted" and not package_errors


def check_contract(docs: Path, kind: str) -> tuple[dict, list[str]]:
    path = contract_path(docs, kind)
    if not path.is_file():
        return {}, [f"missing {kind} contract: {path}"]
    try:
        props, body = parse(path)
    except (OSError, ValueError) as exc:
        return {}, [str(exc)]
    errors: list[str] = []
    if props.get("type") != TYPE_FOR[kind]:
        errors.append(f"type must be {TYPE_FOR[kind]}")
    if props.get("status") not in {"draft", "approved"}:
        errors.append("status must be draft or approved")
    if not isinstance(props.get("revision"), int) or props["revision"] < 1:
        errors.append("revision must be a positive integer")
    refs = props.get("constrained_by")
    if refs is not None and (not isinstance(refs, list)
                             or any(not accepted_solution_ref(docs, ref) for ref in refs)):
        errors.append("constrained_by contains a missing or non-accepted Solution decision")
    for field in COMMAND_FIELDS[kind]:
        value = props.get(field, "")
        if value and (not isinstance(value, str) or TOKEN_RE.search(value)
                      or CREDENTIAL_RE.search(value)):
            errors.append(f"{field} is empty, contains a credential literal, or has an unresolved token")
    for field in WORKDIR_FIELDS[kind]:
        if not valid_workdir(props.get(field, "")):
            errors.append(f"{field} must be a normalized repository-relative path")
    if kind == "verification":
        if props.get("status") == "approved" and (not isinstance(refs, list) or not refs):
            errors.append("approved contract must cite at least one accepted Solution decision")
        if props.get("status") == "approved" and (not isinstance(props.get("test_command"), str) or not props["test_command"].strip()):
            errors.append("test_command is required")
        for prefix in ("mutation", "dependency_audit"):
            disposition = props.get(f"{prefix}_disposition")
            if disposition not in DISPOSITIONS:
                errors.append(f"{prefix}_disposition must be required or not_applicable")
            command = props.get(f"{prefix}_command", "")
            rationale = props.get(f"{prefix}_rationale", "")
            if disposition == "required" and (not isinstance(command, str) or not command.strip()):
                errors.append(f"{prefix}_command is required when disposition is required")
            if disposition == "not_applicable" and (not isinstance(rationale, str) or not rationale.strip()):
                errors.append(f"{prefix}_rationale is required when disposition is not_applicable")
    else:
        if props.get("status") == "approved" and (not isinstance(refs, list) or not refs):
            errors.append("approved contract must cite at least one accepted Solution decision")
        if props.get("status") == "approved" and (not isinstance(props.get("env_command"), str) or not props["env_command"].strip()):
            errors.append("env_command is required")
        scenarios = props.get("scenarios")
        if props.get("status") == "approved" and (not isinstance(scenarios, list) or not scenarios):
            errors.append("scenarios must be a non-empty list")
        for name in ("tolerated_warnings", "service_catalog"):
            if not isinstance(props.get(name), list):
                errors.append(f"{name} must be a list")
    digest = source_hash(props, body)
    if props.get("status") == "approved":
        if props.get("source_hash") != digest:
            errors.append("approved contract source_hash is stale")
        if not isinstance(props.get("approved_at_utc"), str):
            errors.append("approved contract needs compiler-owned approved_at_utc")
    return {"path": str(path), "kind": kind, "status": props.get("status"),
            "revision": props.get("revision"), "source_hash": digest,
            "current": not errors and props.get("status") == "approved"}, errors


def initial_props(kind: str, refs: list[str]) -> dict:
    common = {
        "type": TYPE_FOR[kind], "title": TYPE_FOR[kind].replace("-", " ").title(),
        "status": "draft", "revision": 1, "constrained_by": refs,
        "source_hash": "", "tags": [f"doc/{TYPE_FOR[kind]}", "status/draft"],
    }
    if kind == "verification":
        return common | {
            "test_command": "", "test_workdir": ".",
            "mutation_disposition": "not_applicable", "mutation_command": "",
            "mutation_workdir": ".", "mutation_rationale": "Describe why mutation testing is not applicable.",
            "dependency_audit_disposition": "not_applicable", "dependency_audit_command": "",
            "dependency_audit_workdir": ".", "dependency_audit_rationale": "Describe why dependency auditing is not applicable.",
        }
    return common | {
        "env_command": "", "env_workdir": ".", "scenarios": ["default"],
        "tolerated_warnings": [], "service_catalog": [],
    }


def init(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, args.kind)
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    refs = args.constrained_by or []
    props = initial_props(args.kind, refs)
    body = "# " + props["title"] + "\n\n## Contract\n\nFill the declared command contract, then approve it through this compiler.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/operation|Operation]]"
    path.parent.mkdir(parents=True, exist_ok=True)
    map_path = docs / "maps" / "operation.md"
    if not map_path.exists():
        template = Path(__file__).resolve().parents[1] / "templates" / "vault" / "maps" / "operation.md"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(render(props, body), encoding="utf-8")
    print(json.dumps({"kind": args.kind, "path": str(path), "status": "draft"}, sort_keys=True))
    return 0


def revise(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, args.kind)
    props, body = parse(path)
    if props.get("status") != "approved":
        raise ValueError("begin-revision requires an approved contract")
    props["status"] = "draft"
    props["revision"] = int(props.get("revision", 0)) + 1
    props.pop("approved_at_utc", None)
    props["source_hash"] = ""
    props["tags"] = [f"doc/{TYPE_FOR[args.kind]}", "status/draft"]
    path.write_text(render(props, body), encoding="utf-8")
    print(json.dumps({"kind": args.kind, "path": str(path), "status": "draft", "revision": props["revision"]}, sort_keys=True))
    return 0


def approve(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, args.kind)
    props, body = parse(path)
    if props.get("status") != "draft":
        raise ValueError("approve requires a draft contract")
    props["status"] = "approved"
    props["tags"] = [f"doc/{TYPE_FOR[args.kind]}", "status/approved"]
    props["approved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    props["source_hash"] = source_hash(props, body)
    path.write_text(render(props, body), encoding="utf-8")
    value, errors = check_contract(docs, args.kind)
    if errors:
        raise ValueError("approval check failed: " + "; ".join(errors))
    print(json.dumps(value, sort_keys=True))
    return 0


def command_in_workdir(command: str, workdir: str) -> str:
    """Render a declared command with its normalized repository workdir."""
    return command if workdir == "." else f"cd {shlex.quote(workdir)} && {command}"


def ci_audit_job(props: dict) -> str:
    if props.get("dependency_audit_disposition") != "required":
        return ""
    command = command_in_workdir(
        str(props["dependency_audit_command"]),
        str(props["dependency_audit_workdir"]),
    )
    return "\n".join((
        "", "  dependency_audit:", "    runs-on: ubuntu-latest", "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Audit locked dependencies for known advisories",
        "        run: " + command,
    ))


def ci_environment_job(props: dict) -> str:
    command = command_in_workdir(str(props["env_command"]), str(props["env_workdir"]))
    return "\n".join((
        "", "  environment_smoke:", "    runs-on: ubuntu-latest",
        "    timeout-minutes: 15", "    steps:", "      - uses: actions/checkout@v4",
        "      - name: Stand the environment up from scratch",
        "        run: " + command + " up",
        "      - name: Dump service logs for diagnosis", "        if: failure()",
        "        run: " + command + " logs", "      - name: Tear the environment down",
        "        if: always()", "        run: " + command + " down",
    ))


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def render_ci(args) -> int:
    """Materialize CI solely from approved Operation Contract receipts."""
    docs = docs_root(args.docs)
    verification, errors = check_contract(docs, "verification")
    if errors or not verification.get("current"):
        raise ValueError("approved current Verification Contract is required: " + "; ".join(errors))
    verification_props, _body = parse(contract_path(docs, "verification"))
    environment_props = None
    if args.include_environment:
        environment, env_errors = check_contract(docs, "environment")
        if env_errors or not environment.get("current"):
            raise ValueError("approved current Environment Contract is required: " + "; ".join(env_errors))
        environment_props, _body = parse(contract_path(docs, "environment"))
    template_path = Path(args.template).resolve() if args.template else (
        Path(__file__).resolve().parents[1] / "templates" / "ci-tests.yml"
    )
    template = template_path.read_text(encoding="utf-8")
    substitutions = {
        "{{test_command}}": command_in_workdir(
            str(verification_props["test_command"]),
            str(verification_props["test_workdir"]),
        ),
        "{{dependency_audit_job}}": ci_audit_job(verification_props),
        "{{environment_smoke_job}}": ci_environment_job(environment_props)
        if environment_props is not None else "",
    }
    for token, value in substitutions.items():
        if token not in template:
            raise ValueError(f"CI template is missing required token {token}")
        template = template.replace(token, value)
    if TOKEN_RE.search(template):
        raise ValueError("CI materialization left an unresolved template token")
    output = Path(args.output).resolve()
    atomic_text(output, template)
    print(json.dumps({
        "output": str(output), "verification_hash": verification["source_hash"],
        "environment_hash": None if environment_props is None else check_contract(docs, "environment")[0]["source_hash"],
    }, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "begin-revision", "approve", "status"):
        entry = sub.add_parser(name)
        entry.add_argument("--kind", choices=sorted(KINDS), required=True)
        entry.add_argument("--docs")
        entry.add_argument("--json", action="store_true")
        if name == "init":
            entry.add_argument("--constrained-by", action="append")
    render_ci_parser = sub.add_parser("render-ci")
    render_ci_parser.add_argument("--docs")
    render_ci_parser.add_argument("--output", required=True)
    render_ci_parser.add_argument("--template")
    render_ci_parser.add_argument("--include-environment", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return init(args)
        if args.command == "begin-revision":
            return revise(args)
        if args.command == "approve":
            return approve(args)
        if args.command == "render-ci":
            return render_ci(args)
        value, errors = check_contract(docs_root(args.docs), args.kind)
        print(json.dumps({"ok": not errors, "receipt": value, "errors": errors},
                         ensure_ascii=False, sort_keys=True))
        return 1 if errors else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
