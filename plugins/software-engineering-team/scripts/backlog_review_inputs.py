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
from ba_compile import GENERATED_BODY_BLOCKS, INLINE_CODE_RE, WIKILINK_RE, without_code


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
    # Code is syntax, not a link, exactly as the vault gate reads it.
    body = without_code(body)
    for start, end in GENERATED_BODY_BLOCKS:
        if start in body:
            if end not in body:
                raise InputError("unterminated generated knowledge block")
            body = re.sub(re.escape(start) + r".*?" + re.escape(end), "", body, flags=re.S)
    # Navigation describes traversal, not evidence.
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


def backlog_authored(path: str) -> bool:
    """Backlog rules bind backlog notes; each upstream stage gates its own notes."""
    return path.startswith("backlog/")


def links(value: str, source: str, unparsed: set[str]) -> list[tuple[bool, str]]:
    found = [(bool(match.group("embed")), match.group(0).lstrip("!"))
             for match in WIKILINK_RE.finditer(value)]
    remainder = WIKILINK_RE.sub("", value)
    if "[[" in remainder or "]]" in remainder:
        # Only the owning stage can repair an approved upstream note, so the
        # reviewer is told where a relation may have been missed.
        if backlog_authored(source):
            raise InputError(f"malformed reviewer source wikilink in {source}")
        unparsed.add(source)
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
             backlog.POLICY_PATH, scripts / "process_policy.py",
             scripts.parent / "skill-content/configure/data/process-switches.json",
             backlog.STORY_SIZE_MEASURES_PATH]
    return digest([[path.name, file_hash(path)] for path in files])


LINK_RELATIONS = {"derives_from", "verifies", "related_to"}


def link_target(value: str) -> str:
    parsed = backlog.split_wikilink(value)
    return parsed[0] if parsed else value


def source_scenarios(story: dict) -> dict[str, list[str]]:
    """Map each declared planning source to the scenarios that cite it."""
    declared = list(story["criteria"])
    if story["work_kind"] != "feature":
        declared += backlog.values(story["props"], "related_to")
    labels = {}
    for value in declared:
        parsed = backlog.split_wikilink(value)
        if parsed is not None:
            labels[parsed] = parsed[2] or parsed[0]
    cited: dict[str, set[str]] = {label: set() for label in labels.values()}
    for scenario_id, block in backlog.scenario_blocks(story["test_body"]):
        fields, _duplicates = backlog.scenario_fields(block)
        values, _clean = backlog.source_ref_values(fields.get("source_refs", ""))
        for value in values:
            parsed = backlog.split_wikilink(value)
            if parsed in labels:
                cited[labels[parsed]].add(scenario_id)
    return {label: sorted(ids) for label, ids in sorted(cited.items())}


def compiler_check(docs: Path, record: dict, scope_epics: list[dict], review: dict,
                   relations: dict[str, list[str]], root: bool,
                   budget: dict | None = None) -> dict:
    """Report the compiler facts a reader would otherwise re-derive.

    Source errors already failed the manifest, so they are empty here. The
    current review note gets the findings the final gate will report for it.
    Under story_size_budget at propose_split, the story size measures of the
    stories in scope are given facts as well.
    """
    contract = backlog.backlog_contract()
    sections = contract["required_backlog_review_sections" if root
                        else "required_epic_review_sections"]
    pending = backlog.review_section_findings(review["body"], sections, review["path"], docs)
    pending += backlog.accepted_minor_findings(docs, review["body"], review["path"], contract)
    if budget is not None and not root:
        pending += backlog.size_exception_rows(docs, scope_epics[0], review)[1]
    # The coverage check reads every current review; hand it only this one.
    if root:
        scoped = dict(record, epics=[dict(item, reviews=[]) for item in record["epics"]])
    else:
        scoped = dict(record, backlog_reviews=[], epics=scope_epics)
    pending += backlog.review_coverage_findings(scoped, docs)
    # Expected sets live in review.expected_relations; the audit states only
    # how the note's declaration compares with them.
    audit = {}
    for key, expected in sorted(relations.items()):
        declared = [link_target(value) if key in LINK_RELATIONS else value
                    for value in backlog.values(review["props"], key)]
        duplicates = sorted({value for value in declared if declared.count(value) > 1})
        missing = sorted(set(expected) - set(declared))
        extra = sorted(set(declared) - set(expected))
        if not declared and expected:
            audit[key] = {"state": "pending"}
        elif missing or extra or duplicates:
            audit[key] = {"state": "differs", "missing": missing, "extra": extra,
                          "duplicates": duplicates}
        else:
            audit[key] = {"state": "exact"}
    stories = [story for item in scope_epics for story in item["stories"]]
    facts = {story["id"]: {"epic": story["epic_id"],
                           "scenarios": len(story["scenario_ids"]),
                           "source_scenarios": source_scenarios(story)}
             for story in stories}
    counts = {"epics": len(scope_epics), "stories": len(stories), "test_plans": len(stories),
              "scenarios": sum(len(story["scenario_ids"]) for story in stories),
              "planning_sources": sum(len(facts[story["id"]]["source_scenarios"]) for story in stories),
              "dependency_refs": len(relations["dependency_refs"])}
    if root:
        deferred, _findings = backlog.deferred_criteria(docs, review["body"], review["path"])
        counts["deferred_criteria"] = len(deferred)
    result = {"source_errors": [], "review_note": {"path": review["path"],
                                                   "pending_findings": sorted(set(pending))},
              "relation_audit": audit, "counts": counts, "stories": facts}
    if budget is not None:
        result["story_size"] = backlog.story_size_report(record, docs, budget, set(facts))
    return result


def epic_structure(record: dict, read: set[str]) -> dict:
    """Return the backlog structure an epic manifest derives its read set from.

    Every note the manifest reads is bound by its own hash. A note outside the
    read set can change the read set only through a story identity or a
    dependency edge that reaches a story inside it, and those are bound here.
    """
    stories = [story for story in record["stories"] if story["path"] in read]
    inside = {story["id"] for story in stories}
    return {"epics": sorted([item["id"], item["path"]] for item in record["epics"]
                            if item["path"] in read),
            "stories": sorted([story["id"], story["epic_id"], story["path"], story["test_plan"]]
                              for story in stories),
            "dependencies": sorted(edge for edge in backlog.dependency_edges(
                record["stories"], None, record) if set(edge.split(" -> ")) & inside)}


def manifest(docs: Path, *, epic: str | None = None, expected_hash: str | None = None,
             writer: bool = False) -> dict:
    """Bound one review or writer task; a reader never reads an untouched stub.

    Filling the placeholders the stub verbs write is the writer's task, so a
    writer manifest carries them as ``check.scaffold_findings``. An epic
    reader carries there only the stubs in notes outside its paths, so an
    unfinished epic holds back only the reviews that read it. Every other
    source finding still fails, and the root reader needs complete sources.

    ``source_hash`` binds what the task reads. The root manifest binds every
    backlog note. An epic manifest binds the notes it names and the story
    identities and dependency edges that reach them, not the bytes of notes
    it never reads, so another epic's writer finishing its own notes leaves
    the epic's review fresh; the stubs it lists from outside its paths are
    information, not an input.
    """
    docs = docs.resolve()
    if not docs.is_dir():
        raise InputError("review docs directory is missing")
    before = snapshot(docs)
    contract = contract_hash()
    with stage_package.candidate_session(), backlog.experience_validation_session():
        record, errors = backlog.collect(docs, review_inputs=True)
        stubs = record["scaffold_findings"]
        carried = stubs if writer else []
        errors = sorted(set(errors) - set(carried))
        # Which stubs an epic reader reads is known once its closure is.
        deferred = epic is not None and not writer and set(errors) <= set(stubs)
        if errors and not deferred:
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
        unparsed: set[str] = set()

        def include(relative: str, reason: str) -> None:
            # A path is validated and hashed once per run; the closing
            # freshness check re-validates every included path.
            if relative not in hashes:
                hashes[relative] = file_hash(regular_file(docs, relative))
                pending.append(relative)
            reasons[relative].add(reason)

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

        def reference(value: str, source: str, embed: bool = False) -> None:
            if embed:
                # The vault resolves an embed by the file path it names.
                target = backlog.split_wikilink(value)[0]
                try:
                    include(target, f"embed from {source}")
                except InputError as exc:
                    raise InputError(f"{source} embeds a missing or invalid file: {value}") from exc
            else:
                errors = []
                # Upstream compilers write alias-free semantic links that
                # their own gates accept.
                parsed = backlog.read_link(docs, value, source, errors,
                                           require_alias=backlog_authored(source))
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
                for embed, link in links(INLINE_CODE_RE.sub("", value), relative, unparsed):
                    reference(link, relative, embed)
            for embed, link in links(semantic_body(body), relative, unparsed):
                reference(link, relative, embed)
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

        if deferred and errors:
            notes = {item["path"] for item in epics} | {
                path for story in record["stories"] for path in (story["path"], story["test_plan"])}
            # Every stub finding starts with its note's path; one that names no
            # backlog note outside the read set still fails.
            carried = [finding for finding in errors
                       if finding.split(" ", 1)[0] in notes - set(hashes)]
            blocking = sorted(set(errors) - set(carried))
            if blocking:
                raise InputError("backlog structure is invalid: " + "; ".join(blocking))

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
        try:
            budget = backlog.story_size_budget(docs)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        check = compiler_check(docs, record, owning_epics, current_review, relations, epic is None,
                               budget)
        if writer or carried:
            check["scaffold_findings"] = carried

    after = snapshot(docs)
    if before != after or contract != contract_hash() or any(
            file_hash(regular_file(docs, path)) != value for path, value in hashes.items()):
        raise InputError("review sources changed during manifest generation; retry from current sources")
    if epic is None:
        # The root review reads the complete package: every backlog byte binds it.
        structure_hash = digest({path: value for path, value in before.items()
                                 if path.startswith("backlog/")})
    else:
        structure_hash = digest(epic_structure(record, set(hashes)))
    result = {"ok": True, "schema_version": 1, "scope": scope,
              "primary_paths": sorted(primary), "context_paths": sorted(set(hashes) - primary),
              "paths": sorted(hashes), "files": [{"path": path, "sha256": hashes[path],
                  "reasons": sorted(reasons[path])} for path in sorted(hashes)],
              "structure_hash": structure_hash, "contract_hash": contract,
              "review": {"path": current_review["path"], "expected_relations": relations},
              "check": check}
    if unparsed:
        result["unparsed_link_sources"] = sorted(unparsed)
    bound = result
    if epic is not None and "scaffold_findings" in check:
        inside = [finding for finding in check["scaffold_findings"]
                  if finding.split(" ", 1)[0] in hashes]
        bound_check = {key: value for key, value in check.items() if key != "scaffold_findings"}
        if inside or writer:
            bound_check["scaffold_findings"] = inside
        bound = dict(result, check=bound_check)
    result["source_hash"] = digest(bound)
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
