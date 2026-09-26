#!/usr/bin/env python3
"""Lifecycle compiler for the single Delivery Governance document."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ba_compile import (
    frontmatter_item, frontmatter_scalar, parse_frontmatter, without_generated_relations,
)


RELATIVE = Path("delivery/governance/governance.md")


def docs_root(value: str | None) -> Path:
    root = Path(value or "workspace/docs").resolve()
    if root.name == "docs":
        return root
    if (root / "workspace" / "docs").is_dir():
        return root / "workspace" / "docs"
    if (root / "docs").is_dir():
        return root / "docs"
    return root


def path_for(docs: Path) -> Path:
    return docs / RELATIVE


def parse_text(text: str, path: Path) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise ValueError(f"{path}: {error}")
    return props, "\n".join(text.splitlines()[body_line - 1:]).strip()


def read(path: Path) -> tuple[dict, str]:
    return parse_text(path.read_text(encoding="utf-8"), path)


def render(props: dict, body: str) -> str:
    lines = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {frontmatter_item(item)}" for item in value)
        else:
            lines.append(f"{key}: {frontmatter_scalar(value)}")
    return "\n".join(lines + ["---", "", body.strip(), ""])


def governance_hash(props: dict, body: str) -> str:
    projection = {key: value for key, value in props.items()
                  if key not in {"governance_hash", "source_hash", "approved_at_utc"}}
    return "sha256:" + hashlib.sha256(json.dumps(
        {"frontmatter": projection, "body": without_generated_relations(body)}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def status(docs: Path, text: str | None = None) -> tuple[dict, list[str]]:
    """Check the governance file, or ``text`` as its content before it is written."""
    path = path_for(docs)
    if text is None and not path.is_file():
        return {}, [f"missing delivery governance: {path}"]
    try:
        props, body = read(path) if text is None else parse_text(text, path)
    except (OSError, ValueError) as exc:
        return {}, [str(exc)]
    errors: list[str] = []
    if props.get("type") != "delivery-governance":
        errors.append("type must be delivery-governance")
    if props.get("status") not in {"draft", "approved"}:
        errors.append("status must be draft or approved")
    if not isinstance(props.get("revision"), int) or props["revision"] < 1:
        errors.append("revision must be a positive integer")
    if not isinstance(props.get("max_parallel"), int) or isinstance(props.get("max_parallel"), bool) or props.get("max_parallel", 0) < 1:
        errors.append("max_parallel must be a positive integer")
    digest = governance_hash(props, body)
    if props.get("status") == "approved":
        if props.get("governance_hash") != digest:
            errors.append("approved governance_hash is stale")
        if props.get("source_hash") != digest:
            errors.append("approved source_hash is stale")
        if not isinstance(props.get("approved_at_utc"), str):
            errors.append("approved governance needs compiler-owned approved_at_utc")
    return {"path": str(path), "status": props.get("status"),
            "revision": props.get("revision"), "max_parallel": props.get("max_parallel"),
            "governance_hash": digest, "current": not errors and props.get("status") == "approved"}, errors


def init(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    value = args.max_parallel
    if not isinstance(value, int) or value < 1:
        raise ValueError("max_parallel must be a positive integer")
    props = {"type": "delivery-governance", "title": "Delivery Governance",
             "status": "draft", "revision": 1, "max_parallel": value,
             "tags": ["doc/delivery-governance", "status/draft"]}
    body = "# Delivery Governance\n\n## Coordination\n\nThis document owns the hard maximum number of active Delivery slots.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/delivery|Delivery]]"
    path.parent.mkdir(parents=True, exist_ok=True)
    map_path = docs / "maps" / "delivery.md"
    if not map_path.exists():
        template = Path(__file__).resolve().parents[1] / "templates" / "vault" / "maps" / "delivery.md"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(render(props, body), encoding="utf-8")
    print(json.dumps({"path": str(path), "status": "draft"}, sort_keys=True))
    return 0


def begin_revision(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    props, body = read(path)
    if props.get("status") != "approved":
        raise ValueError("begin-revision requires approved governance")
    props["status"] = "draft"
    props["revision"] = int(props.get("revision", 0)) + 1
    props.pop("governance_hash", None)
    props.pop("source_hash", None)
    props.pop("approved_at_utc", None)
    props["tags"] = ["doc/delivery-governance", "status/draft"]
    path.write_text(render(props, body), encoding="utf-8")
    print(json.dumps({"path": str(path), "status": "draft", "revision": props["revision"]}, sort_keys=True))
    return 0


def approve(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    props, body = read(path)
    if props.get("status") != "draft":
        raise ValueError("approve requires draft governance")
    props["status"] = "approved"
    props["tags"] = ["doc/delivery-governance", "status/approved"]
    props["approved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    digest = governance_hash(props, body)
    props["governance_hash"] = digest
    props["source_hash"] = digest
    text = render(props, body)
    # Check the text the file will hold before writing it, so a refusal leaves the draft as it was.
    value, errors = status(docs, text)
    if errors:
        raise ValueError("approval check failed: " + "; ".join(errors))
    path.write_text(text, encoding="utf-8")
    print(json.dumps(value, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "begin-revision", "approve", "status"):
        entry = sub.add_parser(name)
        entry.add_argument("--docs")
        entry.add_argument("--json", action="store_true")
        if name == "init":
            entry.add_argument("--max-parallel", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return init(args)
        if args.command == "begin-revision":
            return begin_revision(args)
        if args.command == "approve":
            return approve(args)
        value, errors = status(docs_root(args.docs))
        print(json.dumps({"ok": not errors, "receipt": value, "errors": errors}, sort_keys=True))
        return 1 if errors else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
