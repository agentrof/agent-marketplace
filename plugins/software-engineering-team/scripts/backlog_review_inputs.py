#!/usr/bin/env python3
"""Derive fresh reviewer inputs from the validated backlog and its references."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import backlog_compile as backlog
import stage_package
from ba_compile import GENERATED_BODY_BLOCKS, WIKILINK_RE


class InputError(ValueError):
    pass


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(docs: Path, relative: str) -> Path:
    if (not relative or relative.startswith("/") or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise InputError(f"invalid reviewer input path: {relative}")
    current = docs
    for part in relative.split("/"):
        aliases = [child.name for child in current.iterdir()
                   if unicodedata.normalize("NFC", child.name).casefold()
                   == unicodedata.normalize("NFC", part).casefold()] if current.is_dir() else []
        if aliases != [part]:
            raise InputError(f"missing or ambiguous reviewer input: {relative}")
        current = current / part
        if current.is_symlink():
            raise InputError(f"reviewer input must not be a symlink: {relative}")
    if not current.is_file():
        raise InputError(f"reviewer input is not a regular file: {relative}")
    return current


def semantic_body(body: str) -> str:
    for start, end in GENERATED_BODY_BLOCKS:
        if start in body:
            if end not in body:
                raise InputError("unterminated generated knowledge block")
            body = re.sub(re.escape(start) + r".*?" + re.escape(end), "", body, flags=re.S)
    # Navigation and fenced examples describe traversal or syntax, not evidence.
    body = re.sub(r"(?m)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", "", body, flags=re.S)
    return re.sub(r"(?m)^(?:##[^\n]*)?<!-- sec: nav -->[^\n]*(?:\n|$).*?(?=^## |\Z)",
                  "", body, flags=re.S)


def strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def links(value: str) -> list[str]:
    found = [match.group(0).lstrip("!") for match in WIKILINK_RE.finditer(value)]
    remainder = WIKILINK_RE.sub("", value)
    if "[[" in remainder or "]]" in remainder:
        raise InputError("malformed reviewer source wikilink")
    return found


def snapshot(docs: Path) -> dict[str, str]:
    result = {}
    for path in sorted(docs.rglob("*.md")):
        relative = path.relative_to(docs).as_posix()
        if "_generated" in path.relative_to(docs).parts or relative.startswith(".obsidian/"):
            continue
        result[relative] = file_hash(regular_file(docs, relative))
    return result


def contract_hash() -> str:
    scripts = Path(__file__).resolve().parent
    files = [Path(__file__), scripts / "backlog_compile.py", scripts / "backlog_input_policy.py", scripts / "stage_package.py",
             scripts / "ba_compile.py", scripts / "requirement_compile.py", scripts / "requirement_route.py",
             backlog.POLICY_PATH]
    return digest([[path.name, file_hash(path)] for path in files])


def manifest(docs: Path, *, epic: str | None = None, expected_hash: str | None = None) -> dict:
    docs = docs.resolve()
    if not docs.is_dir():
        raise InputError("review docs directory is missing")
    before = snapshot(docs)
    contract = contract_hash()
    with stage_package.candidate_session(), backlog.experience_validation_session():
        record, errors = backlog.collect(docs, review_inputs=True)
        if errors:
            raise InputError("backlog structure is invalid: " + "; ".join(errors))
        epics = record["epics"]
        if epic is not None:
            matches = [item for item in epics if epic in {item["id"], item["path"], item["folder"]}]
            if len(matches) != 1:
                raise InputError(f"epic must resolve uniquely: {epic}")
            owning_epics = matches
        else:
            owning_epics = epics
        primary = {record["backlog"]["path"]}
        selected_stories = {story["id"] for item in owning_epics for story in item["stories"]}
        primary.update(item["path"] for item in owning_epics)
        primary.update(path for story in record["stories"] if story["id"] in selected_stories
                       for path in (story["path"], story["test_plan"]))

        by_id = {story["id"]: story for story in record["stories"]}
        by_path = {story["path"]: story for story in record["stories"]}
        by_epic = {item["id"]: item for item in epics}
        adjacency = defaultdict(set)
        for story in record["stories"]:
            for target in story["dependency_targets"]:
                dependency = by_path[target + ".md"]["id"]
                adjacency[story["id"]].add(dependency)
                adjacency[dependency].add(story["id"])
        reasons = defaultdict(set)
        pending = deque()
        hashes = {}

        def include(relative: str, reason: str) -> None:
            path = regular_file(docs, relative)
            reasons[relative].add(reason)
            if relative not in hashes:
                hashes[relative] = file_hash(path)
                pending.append(relative)

        def story_context(story_id: str, reason: str) -> None:
            queue = deque([story_id])
            visited = set()
            while queue:
                identity = queue.popleft()
                if identity in visited:
                    continue
                visited.add(identity)
                story = by_id[identity]
                include(story["path"], reason)
                include(story["test_plan"], reason)
                include(by_epic[story["epic_id"]]["path"], reason)
                queue.extend(sorted(adjacency[identity] - visited))

        def reference(value: str, source: str) -> None:
            errors = []
            parsed = backlog.read_link(docs, value, source, errors)
            if errors or parsed is None:
                raise InputError("; ".join(errors) or f"unresolved source: {value}")
            target = parsed[0] + ".md"
            include(target, f"reference from {source}")
            if target in by_path:
                story_context(by_path[target]["id"], f"Story context from {source}")

        def package_reference(value: str, source: str, stage: str | None = None) -> None:
            if value.startswith("[["):
                reference(value, source)
                return
            if value.startswith(("business-analysis/", "solution-design/", "design-system/")):
                reference(f"[[{value}|{value}]]", source)
                return
            if re.fullmatch(r"REQ-[0-9]{3,}", value):
                matches = [path for path in before if path.startswith("requirements/")
                           and backlog.parse_front_matter(docs / path)[0].get("id") == value]
                if len(matches) != 1:
                    raise InputError(f"Requirement reference must resolve uniquely: {value}")
                include(matches[0], f"Requirement from {source}")
                return
            if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*@r[1-9][0-9]*", value):
                candidates = [item for item in stage_package.candidates(docs, stage or "experience-design")
                              if item["result_ref"] == value]
                if len(candidates) != 1:
                    raise InputError(f"package reference must resolve uniquely: {value}")
                path = Path(candidates[0]["path"])
                try:
                    relative = path.relative_to(docs).as_posix()
                except ValueError as exc:
                    raise InputError("package receipt escapes the docs directory") from exc
                include(relative, f"package receipt from {source}")
                return
            raise InputError(f"unsupported semantic source reference in {source}: {value}")

        for path in sorted(primary):
            include(path, "primary review scope")
        for story_id in sorted(selected_stories):
            story_context(story_id, "incoming/outgoing dependency closure")
        review_notes = [review for item in owning_epics for review in item["reviews"]]
        if epic is None:
            review_notes += record["backlog_reviews"]
        for review in review_notes:
            include(review["path"], "review history and current findings")

        while pending:
            relative = pending.popleft()
            path = docs / relative
            if path.suffix != ".md":
                continue
            props, body = backlog.parse_front_matter(path)
            for value in strings(props):
                for link in links(value):
                    reference(link, relative)
            for link in links(semantic_body(body)):
                reference(link, relative)
            for key in ("requirement_ref", "input_package_refs", "application_ref", "process_refs"):
                for value in backlog.values(props, key):
                    if "[[" not in value:
                        package_reference(value, relative)
            for binding in backlog.values(props, "input_bindings"):
                parts = binding.split("|")
                if len(parts) != 3:
                    raise InputError(f"malformed input binding in {relative}")
                package_reference(parts[1], relative, parts[0])
            for scope in backlog.values(props, "analysis_scopes"):
                parsed = backlog.ANALYSIS_SCOPE_RE.fullmatch(scope)
                if parsed is None:
                    raise InputError(f"invalid analysis scope: {scope}")
                prefix = "business-analysis/" + parsed["space"] + "/"
                if parsed["domain"]:
                    prefix += parsed["domain"] + "/"
                matches = [item for item in before if item.startswith(prefix)]
                if not matches:
                    raise InputError(f"empty analysis scope: {scope}")
                for match in matches:
                    include(match, f"declared analysis scope from {relative}")

    after = snapshot(docs)
    if before != after or contract != contract_hash() or any(
            file_hash(regular_file(docs, path)) != value for path, value in hashes.items()):
        raise InputError("review sources changed during manifest generation; retry from current sources")
    if epic is None:
        current_review = backlog.latest(record["backlog_reviews"])
        relations = {"derives_from": [record["backlog"]["path"][:-3]],
                     "related_to": sorted(item["path"][:-3] for item in epics),
                     "dependency_refs": sorted(backlog.dependency_edges(record["stories"], False, record))}
        scope = "backlog"
    else:
        selected = owning_epics[0]
        current_review = backlog.latest(selected["reviews"])
        relations = {"derives_from": [selected["path"][:-3]],
                     "verifies": sorted(path[:-3] for story in selected["stories"]
                                        for path in (story["path"], story["test_plan"])),
                     "scenario_refs": sorted(scenario for story in selected["stories"] for scenario in story["scenario_ids"]),
                     "dependency_refs": sorted(backlog.dependency_edges(selected["stories"], True, record))}
        scope = selected["path"]
    # An edited outside Story can introduce a new incoming edge. Bind all backlog
    # sources, while keeping unrelated documents out of the reviewer's read set.
    structure_hash = digest({path: value for path, value in before.items() if path.startswith("backlog/")})
    result = {"ok": True, "schema_version": 1, "scope": scope,
              "primary_paths": sorted(primary), "context_paths": sorted(set(hashes) - primary),
              "paths": sorted(hashes), "files": [{"path": path, "sha256": hashes[path],
                  "reasons": sorted(reasons[path])} for path in sorted(hashes)],
              "structure_hash": structure_hash, "contract_hash": contract,
              "review": {"path": current_review["path"], "expected_relations": relations}}
    result["source_hash"] = digest(result)
    if expected_hash is not None and result["source_hash"] != expected_hash:
        raise InputError("review input manifest is stale; regenerate and review the changed sources")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--epic")
    scope.add_argument("--root", action="store_true")
    parser.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    try:
        result = manifest(args.docs, epic=args.epic, expected_hash=args.expected_hash)
    except (InputError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
