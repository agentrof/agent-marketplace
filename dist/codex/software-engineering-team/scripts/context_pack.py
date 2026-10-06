#!/usr/bin/env python3
"""Derive a compact, hash-bound digest of the rules one role applies at one entry.

Process switch ``context_pack`` at ``role_digest`` uses this script; at
``off``, the default, a task reads every file its task-input manifest names,
exactly as before, and ``build`` refuses for a project that has not chosen
``role_digest``.

The pack is derived, never authored. Its sources are the task-input
manifest's required reads for the entry, role and mode (``task_inputs.py``
decides them, switch references of the values in force included), so the
pack never names a file the manifest would not. From each Markdown source it
keeps every rule unit: a list item, table row or paragraph that states an
obligation, a refusal or a switch instruction, or a fenced block that runs a
package script. A rule unit that names another role of the entry and not
this role is listed by id under ``excluded`` and left out of ``rules``. A JSON
source is data: the pack lists it with its hash and the role reads it when
it needs it. The manifest's conditional reads stay links with their
``read_when`` line and hash.

Every source carries its ``sha256``, so the full text stays one read away and
a changed source stales the pack. ``check`` rebuilds the pack from the
current package and refuses (``CONTEXT_PACK_STALE``) a pack whose bytes
differ, naming each changed source. A role that meets a case its pack does
not cover reads the named source and records that read and why.

Output is deterministic: no timestamps, sorted keys, and ``pack_hash`` is the
sha256 of the canonical JSON of the pack without ``pack_hash``.

Commands:
  context_pack.py build --entry E [--role R] [--mode M] [--project-root P] [--out F]
  context_pack.py check --pack F [--project-root P]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_file  # noqa: E402
import task_inputs  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1]
SWITCH = "context_pack"
VALUE = "role_digest"
SCHEMA_VERSION = 1
RUNTIME = Path(".agentrof") / "agent-marketplace" / ".runtime" / "context-packs"
# The words that make a unit a rule. The completeness test scans every source
# line with this same pattern, so a rule line outside every unit fails it.
NORMATIVE = re.compile(
    r"\b(must|never|always|only|refuses?|refused|required?|requires|do not|does not|"
    r"cannot|may not|shall|forbidden|mandatory|at least|at most|exactly)\b",
    re.IGNORECASE)
SWITCH_LINE = re.compile(r"^\s*(?:[-*]\s+)?Switch `[a-z][a-z0-9_]*`")
COMMAND = re.compile(r"\b[a-z][a-z0-9_]*\.py\b")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
FENCE = re.compile(r"^\s*(```|~~~)")


class Refused(Exception):
    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.detail = detail


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def role_forms(role: str) -> re.Pattern:
    """Match a role by its id or its spaced name, in any case."""
    words = role.split("-")
    return re.compile(r"\b" + r"[-\s]".join(map(re.escape, words)) + r"\b", re.IGNORECASE)


def units(text: str) -> list[dict]:
    """Split Markdown into blocks: list items, table rows, paragraphs, fenced blocks.

    Each block keeps its first and last line (1-based) and the heading path
    it sits under. Front matter is left out.
    """
    blocks: list[dict] = []
    headings: list[tuple[int, str]] = []
    current: dict | None = None
    fence: dict | None = None

    def close():
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    lines = text.splitlines()
    # Front matter is metadata the harness reads, not a rule a role applies.
    skip = 0
    if lines and lines[0].strip() == "---":
        skip = next((index + 1 for index, line in enumerate(lines[1:], start=1)
                     if line.strip() == "---"), 0)
    for number, line in enumerate(lines, start=1):
        if number <= skip:
            continue
        if fence is not None:
            fence["lines"].append(line)
            fence["end"] = number
            if FENCE.match(line):
                blocks.append(fence)
                fence = None
            continue
        if FENCE.match(line):
            close()
            fence = {"kind": "block", "start": number, "end": number, "lines": [line],
                     "section": [title for _level, title in headings]}
            continue
        heading = HEADING.match(line)
        if heading:
            close()
            level = len(heading.group(1))
            headings = [item for item in headings if item[0] < level] + [(level, heading.group(2))]
            continue
        if not line.strip():
            close()
            continue
        starts_item = ITEM.match(line)
        row = line.lstrip().startswith("|")
        if current is None or starts_item or row or current["kind"] == "row":
            close()
            current = {"kind": "item" if starts_item else "row" if row else "paragraph",
                       "start": number, "end": number, "lines": [line],
                       "section": [title for _level, title in headings]}
        else:
            current["lines"].append(line)
            current["end"] = number
    close()
    if fence is not None:
        blocks.append(fence)
    return blocks


def is_rule(block: dict) -> bool:
    if block["kind"] == "block":
        return bool(COMMAND.search("\n".join(block["lines"])))
    if block["kind"] == "row" and re.fullmatch(r"\|[\s:|-]*\|?", block["lines"][0].strip()):
        return False
    text = " ".join(block["lines"])
    return bool(NORMATIVE.search(text) or SWITCH_LINE.match(block["lines"][0]))


def compact(block: dict) -> str:
    if block["kind"] == "block":
        return "\n".join(block["lines"][1:-1] if len(block["lines"]) > 1 else block["lines"]).strip()
    return re.sub(r"\s+", " ", " ".join(line.strip() for line in block["lines"])).strip()


def extract(path: str, text: str, role: str | None, others: list[str]) -> tuple[list[dict], list[dict]]:
    """Return the rule units of one source this role keeps, and those it excludes."""
    own = role_forms(role) if role else None
    other_forms = [(other, role_forms(other)) for other in others]
    kept, excluded = [], []
    for block in units(text):
        if not is_rule(block):
            continue
        body = compact(block)
        rule = {"id": f"{path}#L{block['start']}", "source": path,
                "lines": [block["start"], block["end"]], "kind": block["kind"],
                "section": " > ".join(block["section"])}
        named = sorted(other for other, form in other_forms if form.search(body))
        if own is not None and named and not own.search(body):
            excluded.append({**rule, "names": named})
            continue
        kept.append({**rule, "text": body})
    return kept, excluded


def project_value(project: Path) -> str:
    import process_policy
    values, _snapshot = process_policy.effective_values(process_policy.docs_root(project))
    return values.get(SWITCH, {}).get("value", "off")


def build(*, entry: str, role: str | None, mode: str = "review", project: Path | None = None,
          package: Path = PACKAGE) -> dict:
    """Return the pack for one entry, role and mode.

    With a project, the pack follows that project's switch values and
    requires ``context_pack`` at ``role_digest``.
    """
    if project is not None:
        try:
            value = project_value(project)
        except ValueError as exc:
            raise Refused("CONTEXT_PACK_POLICY", f"process policy cannot set {SWITCH}: {exc}") from exc
        if value != VALUE:
            raise Refused("CONTEXT_PACK_OFF", f"{SWITCH} is {value!r} for this project; the task"
                          " reads its full sources")
    policy = task_inputs.catalog(package)
    if entry not in policy["entries"]:
        raise Refused("CONTEXT_PACK_UNKNOWN", f"unknown entry: {entry}")
    route = policy["entries"][entry]
    task = task_inputs.manifest(entry=entry, role=role, mode=mode,
                                project=project if route["project_state"] else None,
                                package=package)
    others = sorted(set(route["roles"]) - {role})
    sources, rules, excluded = [], [], []
    for path in task["required_reads"]:
        data = (package / path).read_bytes()
        record = {"path": path, "sha256": sha256(data)}
        if path.endswith(".md"):
            kept, left = extract(path, data.decode("utf-8"), role, others)
            rules.extend(kept)
            excluded.extend(left)
            record.update(kind="rules", rules=len(kept), excluded=len(left))
        else:
            record.update(kind="data")
        sources.append(record)
    links = [{"path": read["path"], "read_when": read["read_when"],
              "sha256": sha256((package / read["path"]).read_bytes())}
             for read in task["conditional_reads"]]
    pack = {"schema_version": SCHEMA_VERSION, "switch": {SWITCH: VALUE},
            "entry": entry, "role": role, "mode": mode,
            "sources": sources, "conditional_reads": links,
            "rules": rules, "excluded": excluded,
            "fallback": "A case these rules do not cover: read the named source in full and"
                        " record the read and its reason in the output."}
    pack["pack_hash"] = sha256(canonical(pack))
    return pack


def stale_sources(pack: dict, package: Path = PACKAGE) -> list[dict]:
    stale = []
    for record in [*pack.get("sources", []), *pack.get("conditional_reads", [])]:
        path = package / record["path"]
        current = sha256(path.read_bytes()) if path.is_file() else None
        if current != record["sha256"]:
            stale.append({"path": record["path"], "pack": record["sha256"], "current": current})
    return stale


def check(pack: dict, *, project: Path | None = None, package: Path = PACKAGE) -> dict:
    """Refuse a pack whose sources changed or that a fresh build would not reproduce."""
    stale = stale_sources(pack, package)
    if pack.get("pack_hash") != sha256(canonical({k: v for k, v in pack.items() if k != "pack_hash"})):
        raise Refused("CONTEXT_PACK_STALE", "the pack bytes do not match its pack_hash", stale=stale)
    fresh = build(entry=pack["entry"], role=pack["role"], mode=pack["mode"],
                  project=project, package=package)
    if stale or fresh["pack_hash"] != pack["pack_hash"]:
        raise Refused("CONTEXT_PACK_STALE", "the pack no longer matches its sources; rebuild it",
                      stale=stale, current_pack_hash=fresh["pack_hash"])
    return {"ok": True, "pack_hash": pack["pack_hash"], "sources": len(pack["sources"]),
            "rules": len(pack["rules"])}


def default_out(project: Path, pack: dict) -> Path:
    role = pack["role"] or "entry"
    return project / RUNTIME / pack["entry"] / f"{role}-{pack['mode']}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    built = sub.add_parser("build", help="derive the pack; prints it, or writes --out")
    built.add_argument("--entry", required=True)
    built.add_argument("--role")
    built.add_argument("--mode", default="review")
    built.add_argument("--project-root", type=Path)
    built.add_argument("--out", type=Path,
                       help="write the pack here; with --project-root and no --out it lands in"
                            " .agentrof/agent-marketplace/.runtime/context-packs/")
    checked = sub.add_parser("check", help="refuse a stale pack")
    checked.add_argument("--pack", type=Path, required=True)
    checked.add_argument("--project-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            pack = build(entry=args.entry, role=args.role, mode=args.mode,
                         project=args.project_root.resolve() if args.project_root else None)
            out = args.out or (default_out(args.project_root.resolve(), pack)
                               if args.project_root else None)
            if out is None:
                print(json.dumps(pack, indent=2, sort_keys=True))
                return 0
            atomic_file.replace_text(out, json.dumps(pack, indent=2, sort_keys=True) + "\n")
            result = {"ok": True, "path": str(out), "pack_hash": pack["pack_hash"],
                      "rules": len(pack["rules"]), "sources": len(pack["sources"])}
        else:
            try:
                pack = json.loads(args.pack.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise Refused("CONTEXT_PACK_UNREADABLE", f"pack cannot be read: {exc}") from exc
            result = check(pack, project=args.project_root.resolve() if args.project_root else None)
    except Refused as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc), **exc.detail},
                         sort_keys=True))
        return 1
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "code": "CONTEXT_PACK_ERROR", "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
