"""Carry approved epic reviews across a backlog revision that only rebinds its sources.

Process switch ``source_rebind`` at ``receipt_when_unchanged``: a backlog
revision whose root differs from its approved predecessor only in its input
bindings, Requirement and lifecycle fields reuses each epic's approved review
when no note of that epic is impacted by the changed sources. The receipt this
module derives names every impacted note and its reason, and owner approval
names the receipt's exact hash. Nothing in it is trusted: every check replays
it from Git and the vault.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
import re
import subprocess

import atomic_file
import backlog_compile as backlog
import backlog_migration
import requirement_compile

SWITCH = "source_rebind"
VALUE = "receipt_when_unchanged"
KIND = "backlog-source-rebind-v1"
RECEIPTS = "backlog/artifacts/source-rebinds"
SECTION = "Source Rebind"
SECTION_LINE = re.compile(
    r"(?m)^Compiler \[Source Rebind\]: receipt (sha256:[0-9a-f]{64}) from ([0-9a-f]{40}|[0-9a-f]{64})\s*$")
HASH = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
STANDARD = "take the standard reviewed revision (stub-epic --new-review for every epic)"
# Root keys a source rebind may change: lifecycle stamps, the revision counter,
# the policy pin and the sources the backlog binds.
ROOT_REBIND_KEYS = frozenset({
    "status", "tags", "revision", "approved_at_utc", "source_hash", "package_hash",
    "input_bindings", "requirement_ref", "input_contract", "absent_input_stages",
    "process_policy_path", "process_policy_revision", "process_policy_source_hash",
})
LIFECYCLE_KEYS = frozenset({
    "status", "approved_at_utc", "source_hash", "package_hash",
    "process_policy_path", "process_policy_revision", "process_policy_source_hash",
})
UPSTREAM_LIFECYCLE_KEYS = frozenset({
    "status", "approved_at", "approved_at_utc", "package_hash", "package_status",
    "package_approved_at_utc", "package_contract_version", "baseline_hash", "source_hash",
})
SOURCE_ROW_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
WIKILINK = re.compile(r"\[\[([^\[\]\n]+)\]\]")
EPIC_NOTE = re.compile(r"backlog/epics/([^/]+)/epic\.md")
STORY_NOTE = re.compile(r"backlog/epics/([^/]+)/stories/([^/]+)/story\.md")
PLAN_NOTE = re.compile(r"backlog/epics/([^/]+)/stories/([^/]+)/test-plan\.md")
EPIC_REVIEW = re.compile(r"backlog/epics/([^/]+)/reviews/round-([0-9]+)-epic-review\.md")
ROOT_REVIEW = re.compile(r"backlog/reviews/round-([0-9]+)-backlog-review\.md")
ROOT = "backlog/backlog.md"


class RebindRefused(ValueError):
    """The revision cannot take the source-rebind path."""


def byte_hash(data: bytes) -> str:
    return backlog_migration.byte_hash(data)


def approval_hash(receipt: dict) -> str:
    """The owner approves this hash: the receipt without its approval and sealed postimage."""
    unsealed = {key: value for key, value in receipt.items()
                if key not in {"owner_approval", "after_package_hash"}}
    return byte_hash(backlog_migration.encoded(unsealed))


def lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def without_navigation(body: str) -> str:
    return body.split(backlog.NAV_MARKER, 1)[0]


def content_form(text: str) -> str:
    """A backlog note without its lifecycle stamps, navigation and generated relations."""
    props, body = backlog.parse_front_matter_text(lf(text))
    stable = {key: value for key, value in props.items() if key not in LIFECYCLE_KEYS}
    if isinstance(stable.get("tags"), list):
        stable["tags"] = [tag for tag in stable["tags"] if not str(tag).startswith("status/")]
    body = without_navigation(backlog.without_generated_relations(body))
    return json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")) \
        + "\n" + body.strip() + "\n"


def content_hash(text: str) -> str:
    return byte_hash(content_form(text).encode("utf-8"))


def upstream_form(text: str) -> str:
    """An upstream document without what its approval writes, in comparable form."""
    text = lf(text)
    lines = text.split("\n")
    if lines and lines[0] == "---" and "---" in lines[1:]:
        end = lines.index("---", 1)
        kept = [line for line in lines[1:end]
                if line.partition(":")[0].strip() not in UPSTREAM_LIFECYCLE_KEYS
                and not re.match(r"^\s*-\s*[\"']?status/[a-z0-9-]+[\"']?\s*$", line)]
        lines = ["---", *kept, *lines[end:]]
    return backlog.without_generated_relations("\n".join(lines).rstrip() + "\n")


def source_rows(text: str) -> dict[str, str]:
    """Every table row a source document mints, keyed by its id."""
    rows = {}
    for line in lf(text).split("\n"):
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and SOURCE_ROW_ID.match(cells[0]):
            rows[cells[0]] = " ".join(" | ".join(cells).split())
    return rows


def relation_targets(text: str) -> set[str]:
    """The wikilink targets of a note's front matter: its typed outgoing edges."""
    text = lf(text)
    if not text.startswith("---\n"):
        return set()
    end = text.find("\n---", 4)
    header = text[4:end] if end > 0 else ""
    targets = set()
    for match in WIKILINK.finditer(header):
        parsed = backlog.split_wikilink(match.group(0))
        if parsed is not None and parsed[0]:
            targets.add(parsed[0].removesuffix(".md"))
    return targets


def require_opt_in(docs: Path) -> None:
    import process_policy
    values, _snapshot = process_policy.effective_values(docs)
    if values[SWITCH]["value"] != VALUE:
        raise RebindRefused(f"{SWITCH} must select {VALUE} in the approved Process Policy; {STANDARD}")


def opted_in(docs: Path) -> bool:
    import process_policy
    try:
        values, _snapshot = process_policy.effective_values(docs)
    except ValueError:
        return False
    return values.get(SWITCH, {}).get("value") == VALUE


def project_of(docs: Path) -> Path:
    project = backlog.history_project(docs)
    if project is None:
        raise RebindRefused(f"a source rebind needs the backlog's Git history; {STANDARD}")
    return project


def docs_prefix(project: Path, docs: Path) -> str:
    return docs.resolve().relative_to(project.resolve()).as_posix()


def git(project: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs", *argv],
                          cwd=project, capture_output=True, check=False)


def tree_texts(project: Path, commit: str, prefix: str) -> dict[str, str]:
    """Every Markdown file under one repository prefix at one commit, by repository path."""
    return {path: lf(data.decode("utf-8")) for path, data in
            backlog.committed_tree_sources(project, commit, prefix).items() if path.endswith(".md")}


def receipt_commit(project: Path, anchor: str, digest: str) -> str | None:
    """The newest commit that introduced ``digest`` into ``anchor``: its approval."""
    listing = git(project, "log", "--format=%H", "-S", digest, "HEAD", "--", anchor)
    if listing.returncode:
        return None
    for commit in listing.stdout.decode("ascii").split():
        blob = backlog.history_blob(project, commit, anchor)
        if blob is not None and digest in blob.decode("utf-8", "replace"):
            return commit
    return None


def source_location(prefix: str, stage: str, reference: str) -> tuple[str, str]:
    """The package folder and the receipt anchor of one bound source, by repository path."""
    root = f"{prefix}/" if prefix else ""
    if stage == "business-analysis":
        match = re.fullmatch(r"business-analysis/([a-z0-9]+(?:-[a-z0-9]+)*)/space", reference)
        if match is None:
            raise RebindRefused(f"unsupported business-analysis reference {reference}")
        folder = f"{root}business-analysis/{match.group(1)}"
        return folder, f"{folder}/space.md"
    if stage == "solution-design" and reference == "solution-design/landscape":
        return f"{root}solution-design", f"{root}solution-design/landscape.md"
    if stage == "design-system" and reference == "design-system/MASTER":
        return f"{root}design-system", f"{root}design-system/MASTER.md"
    if stage == "experience-design":
        import experience_application_check
        ledger = f"{root}experience-design/" + experience_application_check.LEDGER_RELATIVE.as_posix()
        name = reference.split("@", 1)[0]
        folder = f"{root}experience-design" if name == "application" \
            else f"{root}experience-design/experiences/{name}"
        return folder, ledger
    raise RebindRefused(f"unsupported {stage} reference {reference}")


def binding_rows(props: dict) -> dict[tuple[str, str], tuple[str, str]]:
    """Bindings keyed by stage and package name; an Experience ref carries its revision."""
    rows = {}
    for binding in backlog.values(props, "input_bindings"):
        stage, _sep, remainder = binding.partition("|")
        reference, _sep2, digest = remainder.partition("|")
        key = (stage, reference.split("@", 1)[0])
        if key in rows or not HASH.fullmatch(digest):
            raise RebindRefused(f"backlog input binding is malformed or repeated: {binding}")
        rows[key] = (reference, digest)
    return rows


def package_diff(project: Path, prefix: str, folder: str, before: str, after: str
                 ) -> tuple[list[str], dict[str, tuple[str | None, str | None]]]:
    """Changed authored documents of one package between two commits, by docs path."""
    old, new = tree_texts(project, before, folder), tree_texts(project, after, folder)
    strip = len(prefix) + 1 if prefix else 0
    changed, texts = [], {}
    for path in sorted(set(old) | set(new)):
        parts = path.split("/")
        if "_generated" in parts or "_ledger" in parts or "artifacts" in parts:
            continue
        a, b = old.get(path), new.get(path)
        if (a is None) != (b is None) or (a is not None and upstream_form(a) != upstream_form(b)):
            changed.append(path[strip:])
            texts[path[strip:]] = (a, b)
    return changed, texts


def requirement_text(read, ref: str) -> tuple[str | None, str | None]:
    """The docs path and text of the Requirement with ``ref``, through ``read``."""
    if not ref:
        return None, None
    for path, text in read.listing("requirements"):
        if not re.fullmatch(r"requirements/req-[^/]+\.md", path):
            continue
        props, _line, error = requirement_compile.parse_frontmatter(text)
        if not error and str(props.get("id", "")) == ref:
            return path, text
    return None, None


def requirement_hash(text: str | None) -> str | None:
    if text is None:
        return None
    props, body_line, error = requirement_compile.parse_frontmatter(lf(text))
    if error:
        raise RebindRefused("the bound Requirement cannot be parsed")
    body = "\n".join(lf(text).split("\n")[body_line - 1:]).lstrip("\n")
    return requirement_compile.semantic_hash(props, body)


class WorkingTree:
    """Reads docs paths from the working tree."""

    def __init__(self, docs: Path):
        self.docs = docs

    def listing(self, folder: str) -> list[tuple[str, str]]:
        base = self.docs / folder
        return [(path.relative_to(self.docs).as_posix(), lf(path.read_text(encoding="utf-8")))
                for path in sorted(base.rglob("*.md")) if path.is_file()] if base.is_dir() else []


class Commit:
    """Reads docs paths from one commit."""

    def __init__(self, project: Path, prefix: str, commit: str):
        self.project, self.prefix, self.commit = project, prefix, commit

    def listing(self, folder: str) -> list[tuple[str, str]]:
        root = f"{self.prefix}/{folder}" if self.prefix else folder
        strip = len(self.prefix) + 1 if self.prefix else 0
        return sorted((path[strip:], text) for path, text in
                      tree_texts(self.project, self.commit, root).items())


def structure(sources: dict[str, str]) -> dict:
    """Epics, stories and the latest epic review of one canonical note set."""
    epics: dict[str, dict] = {}
    stories: dict[str, dict] = {}
    for path in sorted(sources):
        if (match := EPIC_NOTE.fullmatch(path)):
            props, _body = backlog.parse_front_matter_text(sources[path])
            epics.setdefault(match.group(1), {"reviews": {}, "stories": []}).update(
                id=backlog.note_id(props, match.group(1)), path=path)
    for path in sorted(sources):
        if (match := STORY_NOTE.fullmatch(path)):
            props, _body = backlog.parse_front_matter_text(sources[path])
            depends = set()
            for value in backlog.values(props, "depends_on"):
                parsed = backlog.split_wikilink(value)
                if parsed is not None:
                    depends.add(parsed[0].removesuffix(".md") + ".md")
            epic = epics.setdefault(match.group(1), {"reviews": {}, "stories": [], "id": match.group(1),
                                                     "path": f"backlog/epics/{match.group(1)}/epic.md"})
            plan = path[:-len("story.md")] + "test-plan.md"
            stories[path] = {"id": backlog.note_id(props, match.group(2)), "path": path,
                             "test_plan": plan, "epic": match.group(1), "depends": depends,
                             "implements": backlog.values(props, "implements")}
            epic["stories"].append(path)
        elif (match := EPIC_REVIEW.fullmatch(path)):
            epics.setdefault(match.group(1), {"reviews": {}, "stories": [], "id": match.group(1),
                                              "path": f"backlog/epics/{match.group(1)}/epic.md"}
                             )["reviews"][int(match.group(2))] = path
    for epic in epics.values():
        epic["latest_review"] = epic["reviews"][max(epic["reviews"])] if epic["reviews"] else None
    roots = sorted((int(match.group(1)), path) for path in sources
                   if (match := ROOT_REVIEW.fullmatch(path)))
    return {"epics": epics, "stories": stories, "root_review": roots[-1][1] if roots else None}


def current_sources(docs: Path) -> dict[str, bytes]:
    """The canonical backlog notes of the working tree, through safe paths only."""
    found = {}
    for path in sorted((docs / "backlog").rglob("*.md")):
        relative = path.relative_to(docs).as_posix()
        if backlog_migration.NOTE.fullmatch(relative):
            found[relative] = backlog_migration.safe_path(docs, relative).read_bytes()
    return found


def cites(text: str, links: list[str], ids: list[str]) -> list[str]:
    """The changed documents and row ids that a note's text names."""
    hits = [link for link in links
            if re.search(re.escape(link) + r"(?:\.md)?(?=[|#\]\\])", text)]
    hits += [identifier for identifier in ids
             if re.search(rf"(?<![A-Za-z0-9-]){re.escape(identifier)}(?![A-Za-z0-9-])", text)]
    return sorted(set(hits))


def closure_scope(docs: Path, present: list[str], deleted: dict[str, str], links: list[str],
                  ids: list[str]) -> tuple[list[str], list[str]]:
    """The relation closure of the changed sources, and every graph gap that could hide an edge
    between them and the backlog.

    A gap counts when a backlog note's unresolved value names a changed source
    document or row, when a changed source's unresolved value names a backlog
    note, or when a closure member without typed relations is a backlog note.
    """
    import task_inputs
    api = task_inputs.closure_api()
    try:
        raw = task_inputs.closure_raw(api, docs, present, deleted)
        rows = {key: raw[key] for key in ("closure", "changed", "graph_gaps")}
        closure = sorted({task_inputs.docs_relative(task_inputs.row_path(row)) for row in rows["closure"]})
    except (KeyError, TypeError, ValueError) as exc:
        raise RebindRefused(f"the source impact closure cannot be read: {exc}; {STANDARD}") from exc
    members = set(closure) | {task_inputs.docs_relative(task_inputs.row_path(row)) for row in rows["changed"]}
    gaps = set()
    for row in rows["graph_gaps"]:
        if not isinstance(row, dict):
            path = task_inputs.docs_relative(row)
            if path.startswith("backlog/") and path in members:
                gaps.add(path)
            continue
        path = task_inputs.docs_relative(row.get("path") or row.get("source") or "")
        value = " ".join(str(row.get(key, "")) for key in ("value", "target"))
        if row.get("reason") in {"no_typed_relations", "text_only_relation"}:
            touches = path.startswith("backlog/") and path in members
        elif path.startswith("backlog/"):
            touches = bool(cites(value + "]", links, ids))
        else:
            touches = path in {link + ".md" for link in links} and "backlog/" in value
        if touches:
            gaps.add(path)
    return closure, sorted(gaps)


def scan_text(text: str) -> str:
    props_text, body = text, ""
    if text.startswith("---\n") and (end := text.find("\n---", 4)) > 0:
        props_text, body = text[:end], text[end:]
    return props_text + without_navigation(backlog.without_generated_relations(body))


def derive(docs: Path, project: Path, predecessor: str, before_bytes: dict[str, bytes],
           after_bytes: dict[str, bytes], after_reader, *, live: bool,
           reader_epics: list[str], package_version: str) -> dict:
    """The receipt of one source rebind, from its predecessor and its candidate."""
    prefix = docs_prefix(project, docs)
    before = {path: lf(data.decode("utf-8")) for path, data in before_bytes.items()}
    after = {path: lf(data.decode("utf-8")) for path, data in after_bytes.items()}
    if ROOT not in before or ROOT not in after:
        raise RebindRefused(f"the backlog root is missing; {STANDARD}")
    before_props, before_body = backlog.parse_front_matter_text(before[ROOT])
    after_props, after_body = backlog.parse_front_matter_text(after[ROOT])
    kept = sorted(key for key in set(before_props) | set(after_props) if key not in ROOT_REBIND_KEYS
                  and before_props.get(key) != after_props.get(key))
    if kept or without_navigation(backlog.without_generated_relations(before_body)).strip() \
            != without_navigation(backlog.without_generated_relations(after_body)).strip():
        raise RebindRefused("the backlog root changed beyond its bindings and Requirement"
                            + (f" ({', '.join(kept)})" if kept else " (its body)") + f"; {STANDARD}")
    before_rows, after_rows = binding_rows(before_props), binding_rows(after_props)
    if set(before_rows) != set(after_rows):
        raise RebindRefused(f"the revision adds or removes a bound source family; {STANDARD}")
    old_requirement = str(before_props.get("requirement_ref", "") or "")
    new_requirement = str(after_props.get("requirement_ref", "") or "")
    before_read = Commit(project, prefix, predecessor)
    old_path, old_text = requirement_text(before_read, old_requirement)
    new_path, new_text = requirement_text(after_reader, new_requirement)
    requirement = {"before": {"ref": old_requirement, "semantic_hash": requirement_hash(old_text)},
                   "after": {"ref": new_requirement, "semantic_hash": requirement_hash(new_text)}}
    requirement_changed = requirement["before"] != requirement["after"]

    changes, documents, ids, upstream_texts = [], set(), set(), {}
    for key in sorted(before_rows):
        (old_ref, old_hash), (new_ref, new_hash) = before_rows[key], after_rows[key]
        if (old_ref, old_hash) == (new_ref, new_hash):
            continue
        stage = key[0]
        folder, anchor = source_location(prefix, stage, new_ref)
        before_commit = receipt_commit(project, anchor, old_hash)
        after_commit = receipt_commit(project, anchor, new_hash)
        if before_commit is None or after_commit is None:
            raise RebindRefused(f"no committed approval of {stage} {new_ref} holds"
                                f" {old_hash if before_commit is None else new_hash}; {STANDARD}")
        if live:
            import stage_package
            _receipt, errors = stage_package.verify(docs, stage, new_ref, new_hash,
                                                    require_committed=True, require_strict_current=True)
            if errors:
                raise RebindRefused("; ".join(errors) + f"; {STANDARD}")
        changed, texts = package_diff(project, prefix, folder, before_commit, after_commit)
        upstream_texts.update(texts)
        changed_ids = set()
        for path in changed:
            old_rows, new_rows = source_rows(texts[path][0] or ""), source_rows(texts[path][1] or "")
            changed_ids |= {row for row in set(old_rows) | set(new_rows)
                            if old_rows.get(row) != new_rows.get(row)}
        change = {"stage": stage, "ref": new_ref, "before_hash": old_hash, "after_hash": new_hash,
                  "before_commit": before_commit, "after_commit": after_commit,
                  "changed_documents": changed, "changed_ids": sorted(changed_ids)}
        if old_ref != new_ref:
            change["before_ref"] = old_ref
        changes.append(change)
        documents.update(changed)
        ids |= changed_ids
    if not changes and not requirement_changed:
        raise RebindRefused(f"the revision rebinds no source; {STANDARD}")
    if requirement_changed:
        for path, text in ((old_path, old_text), (new_path, new_text)):
            if path is not None:
                documents.add(path)
        if old_path == new_path and old_path is not None:
            upstream_texts[old_path] = (old_text, new_text)
        else:
            if old_path is not None:
                upstream_texts[old_path] = (old_text, None)
            if new_path is not None:
                upstream_texts[new_path] = (None, new_text)
    links = sorted(path[:-3] for path in documents)
    changed_ids = sorted(ids)

    old, new = structure(before), structure(after)
    impacted: dict[str, set[str]] = {}

    def impact(path: str, reason: str) -> None:
        impacted.setdefault(path, set()).add(reason)

    files = []
    for path in sorted(path for path in set(before) | set(after)
                       if STORY_NOTE.fullmatch(path) or PLAN_NOTE.fullmatch(path)
                       or EPIC_NOTE.fullmatch(path)):
        a = content_hash(before[path]) if path in before else None
        b = content_hash(after[path]) if path in after else None
        files.append({"path": path, "before_content_hash": a, "after_content_hash": b})
        if a != b:
            impact(path, "deleted" if b is None else "new" if a is None else "changed")
    old_edges = {(story["path"], target) for story in old["stories"].values() for target in story["depends"]}
    new_edges = {(story["path"], target) for story in new["stories"].values() for target in story["depends"]}
    for source, target in old_edges ^ new_edges:
        impact(source, "dependency edge")
        impact(target, "dependency edge")
    for path, story in new["stories"].items():
        previous = old["stories"].get(path)
        if previous is not None and previous["id"] != story["id"]:
            impact(path, "identity")
    for path, text in sorted(after.items()):
        if STORY_NOTE.fullmatch(path) or PLAN_NOTE.fullmatch(path) or EPIC_NOTE.fullmatch(path):
            for hit in cites(scan_text(text), links, changed_ids):
                impact(path, "cites " + hit)
    if requirement_changed:
        refs = {old_requirement, new_requirement} - {""}
        for path, story in new["stories"].items():
            for ref in sorted(refs & set(story["implements"])):
                impact(path, "implements " + ref)
    for path, (old_text, new_text) in sorted(upstream_texts.items()):
        moved = relation_targets(old_text or "") ^ relation_targets(new_text or "")
        for target in sorted(moved):
            if target.startswith("backlog/") and target + ".md" in after:
                impact(target + ".md", "inbound " + path[:-3])
    reviews = {}
    for slug, epic in old["epics"].items():
        review = epic["latest_review"]
        if review is not None:
            reviews[slug] = review
            for hit in cites(scan_text(before[review]), links, changed_ids):
                impact(review, "cites " + hit)
    if live:
        present = [path for path in sorted(documents) if (docs / path).is_file()]
        deleted = {path: texts[0] for path, texts in upstream_texts.items()
                   if texts[1] is None and texts[0] is not None}
        closure, gaps = closure_scope(docs, present, deleted, links, changed_ids)
        if gaps:
            raise RebindRefused("the relation graph has gaps between the backlog and the changed sources: "
                                + ", ".join(gaps) + f"; {STANDARD}")
        for path in closure:
            if path in after and path != ROOT and not ROOT_REVIEW.fullmatch(path):
                impact(path, "closure")
    for epic_id in reader_epics:
        matches = [slug for slug, epic in new["epics"].items() if epic["id"] == epic_id]
        if len(matches) != 1:
            raise RebindRefused(f"--review-epic {epic_id} names no epic of the revision")
        impact(new["epics"][matches[0]]["path"], "root reader finding")

    root_review = new["root_review"]
    if root_review is None or root_review == old["root_review"]:
        raise RebindRefused("open the revision's root round with begin-revision first")
    epics, cited = [], set()
    for slug in sorted(new["epics"], key=lambda item: new["epics"][item]["id"]):
        epic = new["epics"][slug]
        notes = {epic["path"]}
        for story in set(epic["stories"]) | {path for path, item in old["stories"].items()
                                             if item["epic"] == slug}:
            notes |= {story, story[:-len("story.md")] + "test-plan.md"}
        review = reviews.get(slug)
        if review is not None:
            notes.add(review)
        reasons = sorted({f"{path}: {reason}" for path in notes for reason in impacted.get(path, ())})
        if slug not in old["epics"]:
            reasons = sorted({*reasons, f"{epic['path']}: new epic"})
        elif review is None:
            reasons = sorted({*reasons, f"{epic['path']}: no approved review"})
        for path in epic["stories"]:
            story = new["stories"][path]
            if impacted.get(path) or impacted.get(story["test_plan"]):
                cited.add(story["id"])
        row = {"id": epic["id"], "disposition": "reviewed" if reasons else "reused",
               "impacted_by": reasons}
        if not reasons:
            row["reused_review"] = {"path": review, "source_hash": backlog.parse_front_matter_text(
                before[review])[0].get("source_hash")}
        epics.append(row)
    receipt = {
        "schema_version": 1, "kind": KIND, "predecessor_commit": predecessor,
        "before_package_hash": before_props.get("package_hash"),
        "binding_changes": changes, "requirement": requirement, "files": files,
        "epics": epics, "reader_epics": sorted(reader_epics),
        "root_review": {"path": root_review},
        "root_scope": {"source_diff_paths": sorted(documents), "cited_stories": sorted(cited)},
        "package_version": package_version,
    }
    receipt["owner_approval"] = approval_hash(receipt)
    return receipt


def plan(docs: Path, source_commit: str, reader_epics: list[str] | None = None,
         package_version: str | None = None) -> dict:
    """Derive the receipt of the working tree's revision against its approved predecessor."""
    project = project_of(docs)
    try:
        oid, before = backlog_migration.approved_sources(docs, source_commit)
    except ValueError as exc:
        raise RebindRefused(f"{exc}; {STANDARD}") from exc
    return derive(docs, project, oid, before, current_sources(docs), WorkingTree(docs), live=True,
                  reader_epics=list(reader_epics or []),
                  package_version=package_version or backlog_migration.installed_version())


def source_diff(docs: Path, receipt: dict) -> list[dict]:
    """The unified diff of every changed source document, from its before to its after commit."""
    project = project_of(docs)
    prefix = docs_prefix(project, docs)
    result = []
    for change in receipt["binding_changes"]:
        folder, _anchor = source_location(prefix, change["stage"], change["ref"])
        _changed, texts = package_diff(project, prefix, folder, change["before_commit"], change["after_commit"])
        for path in change["changed_documents"]:
            old, new = texts.get(path, (None, None))
            result.append({"path": path, "diff": "".join(difflib.unified_diff(
                (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                f"{change['before_commit'][:12]}/{path}", f"{change['after_commit'][:12]}/{path}"))})
    return result


def section_receipt(body: str) -> tuple[str, str] | None:
    """The receipt hash and predecessor a root review's Source Rebind section names."""
    if SECTION not in backlog.headings(body):
        return None
    match = SECTION_LINE.search(backlog.section(body, SECTION))
    return (match.group(1), match.group(2)) if match else ("", "")


def receipt_path(docs: Path, digest: str) -> Path:
    if not HASH.fullmatch(digest or ""):
        raise RebindRefused("a source-rebind receipt is named by its sha256 owner approval")
    return backlog_migration.safe_path(docs, f"{RECEIPTS}/{digest[7:]}.json")


def load_receipt(path: Path) -> dict:
    try:
        receipt = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RebindRefused(f"source-rebind receipt is unreadable: {exc}") from exc
    shape_findings(receipt)
    return receipt


def shape_findings(receipt) -> None:
    strings = ("predecessor_commit", "before_package_hash", "package_version", "owner_approval")
    if (not isinstance(receipt, dict) or receipt.get("kind") != KIND
            or type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or any(not isinstance(receipt.get(key), str) for key in strings)
            or not isinstance(receipt.get("reader_epics"), list)
            or any(not isinstance(item, str) for item in receipt["reader_epics"])
            or not isinstance(receipt.get("epics"), list) or not isinstance(receipt.get("files"), list)):
        raise RebindRefused("source-rebind receipt has invalid field types")
    if COMMIT.fullmatch(receipt["predecessor_commit"]) is None:
        raise RebindRefused("source-rebind receipt must name one full predecessor commit object id")
    if not backlog_migration.VERSION.fullmatch(receipt["package_version"]):
        raise RebindRefused("source-rebind receipt package_version must be numeric SemVer")
    for row in receipt["files"]:
        path = row.get("path") if isinstance(row, dict) else None
        if not isinstance(path, str) or "\\" in path or backlog_migration.NOTE.fullmatch(path) is None:
            raise RebindRefused("source-rebind receipt names a path outside the canonical backlog")
    if receipt["owner_approval"] != approval_hash(receipt):
        raise RebindRefused("source-rebind receipt does not match its owner approval hash")
    sealed = receipt.get("after_package_hash")
    if sealed is not None and not (isinstance(sealed, str) and HASH.fullmatch(sealed)):
        raise RebindRefused("source-rebind receipt after_package_hash is malformed")


def latest_root_review(record: dict) -> dict | None:
    return backlog.latest(record["backlog_reviews"])


def approval_findings(record: dict, docs: Path, *, approving: bool = False) -> list[str]:
    """Refuse a source-rebind approval its receipt replay does not prove.

    The approval itself also needs the receipt file that apply-source-rebind
    writes for the owner-approved hash; a pre-approval check replays the
    receipt the root round names without it.
    """
    review = latest_root_review(record)
    named = section_receipt(review["body"]) if review is not None else None
    if named is None:
        return []
    digest, predecessor = named
    path = review["path"]
    try:
        require_opt_in(docs)
        if not digest:
            raise RebindRefused(f"{path} Source Rebind section names no receipt; rerun"
                                " record-source-rebind-root-review")
        file = receipt_path(docs, digest)
        stored = load_receipt(file) if file.is_file() else None
        if stored is None and approving:
            raise RebindRefused("a source-rebind revision is approved only by apply-source-rebind"
                                " --approve-receipt with the owner's receipt hash")
        if stored is not None and "after_package_hash" in stored:
            raise RebindRefused("the source-rebind receipt is already sealed by an earlier approval")
        reader_epics = stored["reader_epics"] if stored else reader_scope_epics(review["body"])
        expected = plan(docs, predecessor, reader_epics,
                        stored["package_version"] if stored else None)
    except (ValueError, OSError) as exc:
        return [f"{path} source rebind: {exc}"]
    if expected["owner_approval"] != digest or (stored is not None and expected != stored):
        return [f"{path} source rebind receipt does not equal its replay from {predecessor};"
                " rerun plan-source-rebind and record-source-rebind-root-review"]
    return epic_round_findings(record, docs, expected, predecessor)


def epic_round_findings(record: dict, docs: Path, receipt: dict, predecessor: str) -> list[str]:
    """Each reviewed epic needs this revision's approved round; each reused one its approved review."""
    findings = []
    by_id = {epic["id"]: epic for epic in record["epics"]}
    project = project_of(docs)
    prefix = docs_prefix(project, docs)
    for row in receipt["epics"]:
        epic = by_id.get(row["id"])
        review = backlog.latest(epic["reviews"]) if epic else None
        if row["disposition"] == "reused":
            reused = row["reused_review"]
            original = backlog.history_blob(project, predecessor, f"{prefix}/{reused['path']}"
                                            if prefix else reused["path"])
            if review is None or review["path"] != reused["path"] or original is None \
                    or (docs / review["path"]).read_bytes() != original:
                findings.append(f"{row['id']} reuses {reused['path']} by the source rebind receipt, but its"
                                f" latest review is not that approved review byte for byte; {STANDARD}")
            continue
        props = review["props"] if review else {}
        if review is None or props.get("source_hash") or props.get("approved_at_utc"):
            findings.append(f"{row['id']} is impacted by the source rebind ("
                            + "; ".join(row["impacted_by"]) + "); open and approve its next review"
                            " round with stub-epic --new-review")
        elif props.get("verdict") != "approved":
            findings.append(f"{review['path']} verdict is not approved")
    return findings


def replay(docs: Path, receipt: dict, *, final: bool, require_committed: bool = True) -> dict:
    """Prove one sealed receipt from Git: its predecessor, its approved postimage and its impact.

    The relation-closure reasons depend on the vault graph the approval read,
    so a later replay requires only the reasons it can rebuild from Git and
    accepts the recorded ones beyond them; the approval itself replayed the
    receipt in full, and its approved root review binds the receipt hash.
    """
    shape_findings(receipt)
    sealed = receipt.get("after_package_hash")
    if sealed is None:
        raise RebindRefused("source-rebind receipt is not sealed by an approval")
    project = project_of(docs)
    prefix = docs_prefix(project, docs)
    oid, before = backlog_migration.approved_sources(docs, receipt["predecessor_commit"])
    if oid != receipt["predecessor_commit"]:
        raise RebindRefused("source-rebind predecessor must be named by its full commit id")
    if backlog.parse_front_matter_text(before[ROOT].decode("utf-8"))[0].get("package_hash") \
            != receipt["before_package_hash"]:
        raise RebindRefused("source-rebind before_package_hash differs from its predecessor approval")
    if final:
        after, reader = current_sources(docs), WorkingTree(docs)
    else:
        found = backlog_migration.approved_package_sources(docs, sealed)
        if found is None:
            raise RebindRefused("no committed approval holds the receipt's after_package_hash")
        commit, after = found
        reader = Commit(project, prefix, commit)
    root_props, _body = backlog.parse_front_matter_text(after[ROOT].decode("utf-8"))
    if root_props.get("status") != "approved" or root_props.get("package_hash") != sealed:
        raise RebindRefused("the receipt's after_package_hash is not the approved backlog postimage")
    if require_committed:
        name = f"{RECEIPTS}/{receipt['owner_approval'][7:]}.json"
        blob = backlog.history_blob(project, "HEAD", f"{prefix}/{name}" if prefix else name)
        try:
            committed = json.loads(blob.decode("utf-8")) if blob is not None else None
        except (UnicodeError, json.JSONDecodeError):
            committed = None
        if committed != receipt:
            raise RebindRefused("source-rebind receipt is not committed in HEAD")
    root_review = structure({path: data.decode("utf-8") for path, data in after.items()})["root_review"]
    named = section_receipt(backlog.parse_front_matter_text(after[root_review].decode("utf-8"))[1]) \
        if root_review else None
    if named != (receipt["owner_approval"], receipt["predecessor_commit"]):
        raise RebindRefused("the approved root review does not name this source-rebind receipt")
    rebuilt = derive(docs, project, oid, before, after, reader, live=False,
                     reader_epics=receipt["reader_epics"], package_version=receipt["package_version"])
    compare(receipt, rebuilt)
    by_id = {row["id"]: row for row in receipt["epics"]}
    latest = structure({path: data.decode("utf-8") for path, data in after.items()})["epics"]
    for epic in latest.values():
        row = by_id[epic["id"]]
        if row["disposition"] == "reused":
            path = row["reused_review"]["path"]
            if epic["latest_review"] != path or after.get(path) != before.get(path):
                raise RebindRefused(f"{epic['id']} reused review is not the approved predecessor's")
    return receipt


def compare(recorded: dict, rebuilt: dict) -> None:
    """Every field rebuilt from Git must match; impact may only exceed the rebuilt reasons."""
    fixed = [key for key in rebuilt if key not in {"epics", "root_scope", "owner_approval"}]
    if set(recorded) - set(rebuilt) - {"after_package_hash"} \
            or any(recorded.get(key) != rebuilt[key] for key in fixed) \
            or recorded["root_scope"]["source_diff_paths"] != rebuilt["root_scope"]["source_diff_paths"] \
            or not set(rebuilt["root_scope"]["cited_stories"]) <= set(recorded["root_scope"]["cited_stories"]):
        raise RebindRefused("source-rebind receipt does not equal its replay")
    recorded_epics = {row["id"]: row for row in recorded["epics"]}
    if set(recorded_epics) != {row["id"] for row in rebuilt["epics"]}:
        raise RebindRefused("source-rebind receipt epic set does not equal its replay")
    for row in rebuilt["epics"]:
        kept = recorded_epics[row["id"]]
        if not set(row["impacted_by"]) <= set(kept["impacted_by"]) \
                or (kept["disposition"] == "reused") != (not kept["impacted_by"]) \
                or (kept["disposition"] == "reused" and kept.get("reused_review") != row.get("reused_review")):
            raise RebindRefused(f"source-rebind receipt understates the impact on {row['id']}")


def impacted_paths(receipt: dict) -> set[str]:
    """Every backlog note a receipt names as impacted."""
    return {entry.split(": ", 1)[0] for row in receipt["epics"] for entry in row["impacted_by"]}


def receipts(docs: Path) -> list[dict]:
    directory = docs / RECEIPTS
    if not directory.exists():
        return []
    found = []
    for path in sorted(directory.glob("*.json")):
        backlog_migration.safe_path(docs, path.relative_to(docs).as_posix())
        receipt = load_receipt(path)
        if path.stem != receipt["owner_approval"][7:]:
            raise RebindRefused(f"source-rebind receipt {path.name} is not named by its owner approval")
        found.append(receipt)
    return found


def root_review_body(record: dict, docs: Path, body: str, receipt: dict) -> str:
    """Write the root review sections the compiler proves, and the Source Rebind section."""
    root = record["backlog"]
    root_link = backlog.wikilink(root["path"], str(root["props"].get("title", backlog.DEFAULT_BACKLOG_TITLE)))
    epic_links = ", ".join(backlog.wikilink(item["path"], item["id"]) for item in record["epics"])
    cross = sorted(backlog.dependency_edges(record["stories"], False, record))
    every = sorted(backlog.dependency_edges(record["stories"], None, record))
    bindings = backlog.values(root["props"], "input_bindings")
    reviewed = [row["id"] for row in receipt["epics"] if row["disposition"] == "reviewed"]
    reused = [row for row in receipt["epics"] if row["disposition"] == "reused"]
    plans = ", ".join(backlog.wikilink(story["test_plan"], f"{story['id']}-TP")
                      for story in record["stories"])
    sections = {
        "Epic Coverage": (
            f"{root_link} holds the epics {epic_links}; the source rebind receipt reuses the approved"
            f" review of {', '.join(row['id'] for row in reused) or 'no epic'} and reviews"
            f" {', '.join(reviewed) or 'no epic'} again.",
            "Every story sits in exactly one epic, and every epic the changed sources impact has a"
            " fresh review round of this revision, as the compiler check reports."),
        "Cross-Epic Dependencies": (
            f"{root_link} declares {len(cross)} cross-epic dependency edges"
            + (": " + ", ".join(cross) if cross else "") + ".",
            "The compiler found no dependency cycle and no unknown target, and dependency_refs lists"
            " every cross-epic edge."),
        "Delivery Sequencing": (
            f"{root_link} declares {len(every)} dependency edges" + (": " + ", ".join(every) if every else "")
            + ".",
            "Delivery order follows the declared dependency edges, which the compiler found acyclic."),
        "Shared Contracts": (
            f"{root_link} binds {len(bindings)} source receipts; the receipt names"
            f" {len(receipt['binding_changes'])} changed bindings and"
            f" {len(receipt['root_scope']['source_diff_paths'])} changed source documents.",
            "The compiler validated every input binding of this revision against its current receipt."),
        "Global Test Coverage": (
            f"{plans or root_link} carry the test plans of the backlog.",
            "Every story maps each planning source to a scenario and classifies every scenario in its"
            " coverage table, as the compiler check reports."),
    }
    main, marker, navigation = body.partition(backlog.NAV_MARKER)
    main = re.sub(r"(?m)^.*this round has not evaluated its current inputs\..*\n+", "", main)
    for title, (evidence, conclusion) in sections.items():
        main = backlog.replace_section(main, title, f"Evidence [{title}]: {evidence}\n"
                                                    f"Conclusion [{title}]: {conclusion}")
    if root.get("planning_mode") == "requirement":
        requirement_ref = str(root["props"].get("requirement_ref", ""))
        implementing = sorted(story["id"] for story in record["stories"]
                              if requirement_compile.implements_requirement(story["implements"],
                                                                            requirement_ref))
        rows, _errors = backlog.structured_table(backlog.section(main, backlog.REQUIREMENT_COVERAGE),
                                                 backlog.REQUIREMENT_COVERAGE_COLUMNS, "",
                                                 backlog.REQUIREMENT_COVERAGE)
        table = ["| " + " | ".join(backlog.REQUIREMENT_COVERAGE_COLUMNS) + " |", "|---|---|---|"]
        table += [f"| {row['requirement']} | {row['story_ids']} | {row['disposition']} |"
                  for row in rows if row["requirement"] != requirement_ref]
        if implementing:
            table.append(f"| {requirement_ref} | {', '.join(implementing)} | covered |")
        main = backlog.replace_section(main, backlog.REQUIREMENT_COVERAGE, "\n".join(table))
    lines = [f"Compiler [Source Rebind]: receipt {receipt['owner_approval']} from"
             f" {receipt['predecessor_commit']}", ""]
    for change in receipt["binding_changes"]:
        lines.append(f"- {change['stage']} {change['ref']}: {change['before_hash']} -> {change['after_hash']},"
                     f" {len(change['changed_documents'])} changed documents")
    for row in receipt["epics"]:
        lines.append(f"- {row['id']}: {row['disposition']}"
                     + (" (" + "; ".join(row["impacted_by"]) + ")" if row["impacted_by"] else ""))
    lines += ["", "backlog_compile.py record-source-rebind-root-review wrote this section and the sections"
              " above it from the receipt and the compiler's structural checks; one scoped root reader"
              " judges the source diff."]
    content = "\n".join(lines)
    if SECTION in backlog.headings(main):
        main = backlog.replace_section(main, SECTION, content)
    else:
        main = main.rstrip() + f"\n\n## {SECTION}\n\n{content}\n\n"
    return main.rstrip() + "\n\n" + marker + navigation if marker else main.rstrip() + "\n"


def record_root_review(args) -> int:
    """Write the current root round of a source-rebind revision from its receipt."""
    docs = backlog.docs_root(args.docs)
    path = original = None
    try:
        require_opt_in(docs)
        receipt = plan(docs, args.source_commit, list(args.review_epic or []))
        with backlog.stage_package.candidate_session(), backlog.experience_validation_session():
            record, _errors = backlog.collect(docs, review_inputs=True)
        review = latest_root_review(record)
        if (record["backlog"]["props"].get("status") != "draft" or review is None
                or review["path"] != receipt["root_review"]["path"]
                or review["props"].get("approved_at_utc") or review["props"].get("source_hash")):
            raise RebindRefused("a source-rebind root review needs a draft revision with an open root round")
        if backlog.LIGHT_PATH_SECTION in backlog.headings(review["body"]):
            raise RebindRefused("the root round is a light root review; open a fresh one with stub-backlog-review")
        path = docs / review["path"]
        original = path.read_bytes()
        props = dict(review["props"])
        props.update(
            related_to=[backlog.wikilink(item["path"], item["props"]["title"]) for item in record["epics"]],
            dependency_refs=sorted(backlog.dependency_edges(record["stories"], False, record)))
        atomic_file.replace_bytes(path, backlog.front_matter(
            props, root_review_body(record, docs, review["body"], receipt)).encode("utf-8"))
    except (OSError, ValueError, RuntimeError) as exc:
        if path is not None and original is not None and path.read_bytes() != original:
            atomic_file.replace_bytes(path, original)
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "review": review["path"], "receipt": receipt},
                     indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def command(args) -> int:
    """plan-source-rebind is read-only; apply-source-rebind approves the exact owner-approved receipt."""
    docs = backlog.docs_root(args.docs)
    try:
        require_opt_in(docs)
        if args.command == "plan-source-rebind":
            receipt = plan(docs, args.source_commit, list(args.review_epic or []))
            print(json.dumps({"ok": True, "receipt": receipt}, indent=2, ensure_ascii=False))
            return 0
        import setup_project
        with setup_project.refresh_guard(project_of(docs)):
            require_opt_in(docs)
            receipt = apply(docs, args.source_commit, list(args.review_epic or []), args.approve_receipt)
        print(json.dumps({"ok": True, "receipt": receipt}, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, ensure_ascii=False))
        return 1


def apply(docs: Path, source_commit: str, reader_epics: list[str], approved: str) -> dict:
    """Write the receipt, run the ordinary atomic approval and seal the approved package hash."""
    import contextlib
    import io
    from types import SimpleNamespace

    root_props, _body = backlog.parse_front_matter(docs / ROOT)
    path = receipt_path(docs, approved)
    if root_props.get("status") == "approved" and path.is_file():
        sealed = load_receipt(path)
        if sealed.get("after_package_hash") == root_props.get("package_hash"):
            replay(docs, sealed, final=True, require_committed=False)
            return sealed
    if root_props.get("status") == "approved":
        raise RebindRefused("the backlog revision is already approved; a source rebind approves a draft"
                            " revision through this command only")
    receipt = plan(docs, source_commit, reader_epics)
    if approved != receipt["owner_approval"]:
        raise RebindRefused("owner approval must name the exact planned receipt hash")
    if path.exists() and load_receipt(path) != receipt:
        raise RebindRefused("source-rebind receipt was changed")
    snapshot = backlog.snapshot_tree(docs)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.replace_bytes(path, backlog_migration.encoded(receipt))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = backlog.approve(SimpleNamespace(docs=str(docs)))
        if code:
            errors = json.loads(output.getvalue()).get("errors", [])
            raise RebindRefused("approval refused: " + "; ".join(errors))
        props, _body = backlog.parse_front_matter(docs / ROOT)
        sealed = {**receipt, "after_package_hash": props["package_hash"]}
        atomic_file.replace_bytes(path, backlog_migration.encoded(sealed))
        replay(docs, sealed, final=True, require_committed=False)
    except Exception:
        backlog.restore_tree(docs, snapshot)
        raise
    return sealed


def review_scope(docs: Path, record: dict) -> dict | None:
    """The reading scope of a source-rebind revision's readers, or None on every other revision.

    It applies only while the switch selects the value and the open root round
    carries the compiler-written Source Rebind section; its receipt must still
    equal its replay, so a changed source stales every reader at once.
    """
    review = latest_root_review(record)
    if review is None or record["backlog"]["props"].get("status") != "draft":
        return None
    named = section_receipt(review["body"])
    if named is None or not opted_in(docs):
        return None
    digest, predecessor = named
    receipt = plan(docs, predecessor, reader_scope_epics(review["body"]))
    if receipt["owner_approval"] != digest:
        raise RebindRefused("the source-rebind receipt changed since the root round was written;"
                            " rerun record-source-rebind-root-review")
    return {"receipt": receipt, "impacted": impacted_paths(receipt),
            "reused": {row["id"] for row in receipt["epics"] if row["disposition"] == "reused"},
            "source_diff": source_diff(docs, receipt)}


def reader_scope_epics(body: str) -> list[str]:
    """The epics a root reader's finding moved to review, as the Source Rebind section lists them."""
    found = re.findall(r"(?m)^- (\S+): reviewed \(.*root reader finding", backlog.section(body, SECTION))
    return sorted(found)
