#!/usr/bin/env python3
"""Impact closure over the docs vault's relation graph.

A review under ``review_scope: impact_closure`` reads in full only the notes a
change can affect. This module computes that set from what the vault already
keeps: the typed front-matter relations (validated by vault_check), the
renderer-owned inverse relation blocks and catalogs, the generated
cross-subtree matrix, and the approval stamps that prove an unchanged note is
byte-identical to what was approved.

Verbs:
  closure  --docs D --changed P...   print the closure as JSON
  views    --docs D                  print the relation views to consult first
  heal     --docs D --source S --target T --kind K --evidence E --role R
           write one missing typed relation through vault_check's relation
           contract and re-render the generated relation views
  render   --docs D --role R
           re-render the relation views a tier disagreement found stale

The closure never caps a read: a role that reads beyond it records the read
with ``record_beyond``. Relations are read in tiers (see "Edges, read in tiers"); every edge
records the tiers that produced it, and a disagreement between tiers is a
graph gap. A read-only role never heals; it reports the missing relation as
a finding and the flow's writer calls ``heal`` or ``render``.

Stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import re
import sys
from pathlib import Path

import atomic_file
import vault_check
from ba_compile import frontmatter_item, parse_frontmatter, split_wikilink

# Relation keys outside the vault relation contract that still draw
# dependency edges: their owning compilers validate them.
EXTRA_RELATION_KEYS = ("depends_on", "governs", "applies_to")
# Keys whose targets constrain the source: a changed note pulls them in.
CONSTRAINT_KEYS = ("constrained_by", "uses_design", "satisfies")
# Note types whose change widens the closure to every citing note.
SHARED_CONTRACT_EXTRA_TYPES = ("verification-contract", "environment-contract",
                               "delivery-governance", "architecture-standard",
                               "definition-of-done")
POLICY_TYPES = ("process-policy",)
POLICY_PATHS = ("delivery/process-policy.md",)
# Keys on which a healed relation must not close a cycle.
ACYCLIC_KEYS = ("derives_from",)

# The relation views a role consults first. Each entry is a glob relative to
# the docs root and the question the view answers.
VIEW_SPECS = (
    ("home.md", "vault entry point and map hubs"),
    ("maps/*.md", "the flow's map: every note of a subtree, by role"),
    ("maps/_generated/relation-status.md", "typed relation and target counts"),
    ("maps/_generated/cross-subtree-matrix.md",
     "every typed relation: source, key, target, canonical ref"),
    ("maps/_generated/stale-relations.md", "typed relations that no longer hold"),
    ("maps/_generated/uncovered-analysis.md",
     "qualified analysis ids no note relates to"),
    ("maps/_relations/*/relations-*.md",
     "inverse relation catalogs of heavily cited notes"),
    ("business-analysis/*/_generated/registry.json",
     "analysis criterion and rule ids to their notes"),
    ("business-analysis/*/_generated/status.md", "analysis space status"),
    ("business-analysis/*/_generated/open-questions.md", "open analysis questions"),
    ("solution-design/decision-log.md", "solution decisions and their status"),
    ("solution-design/_generated/*.json",
     "capability registry, component catalog and topology"),
    ("system-architecture/decision-log.md", "architecture decisions"),
    ("system-architecture/_generated/*", "architecture catalog"),
    ("design-system/MASTER.md", "design master; its uses_design back-links"),
    ("experience-design/_generated/application-registry.json",
     "current application receipt and process packages"),
    ("delivery/process-policy.md", "process switches in force"),
)

READ_ONLY_MODES = {"review", "consume"}


class HealRefused(ValueError):
    """A relation heal the relation contract or the role context refuses."""


# ---------------------------------------------------------------------------
# Policy and vault
# ---------------------------------------------------------------------------


def closure_policy(vault_policy: dict, overrides: dict | None = None) -> dict:
    """Closure variation points, derived from the vault relation contract."""
    specs = vault_check.relation_specs(vault_policy)
    shared = set(SHARED_CONTRACT_EXTRA_TYPES)
    for key in ("constrained_by", "uses_design"):
        shared.update(t for t in (specs.get(key, {}) or {}).get("targets", [])
                      if t != "*")
    policy = {
        "relation_keys": sorted(set(specs) | set(EXTRA_RELATION_KEYS)),
        "constraint_keys": list(CONSTRAINT_KEYS),
        "shared_contract_types": sorted(shared),
        "policy_types": list(POLICY_TYPES),
        "policy_paths": list(POLICY_PATHS),
        "acyclic_keys": list(ACYCLIC_KEYS),
    }
    policy.update(overrides or {})
    return policy


def load_vault(docs: Path) -> vault_check.Vault:
    return load_vault_reusing(docs, None)


def load_vault_reusing(docs: Path, reuse: dict | None) -> vault_check.Vault:
    """The vault, reusing hash-proven cached notes (see vault_query)."""
    vault_policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    return vault_check.build_vault(
        Path(docs), vault_check.effective_policy(vault_policy, Path(docs)), reuse=reuse)


def normalize(rel: str) -> str:
    rel = rel.replace("\\", "/")
    for prefix in ("workspace/docs/", "docs/"):
        if rel.startswith(prefix):
            return rel[len(prefix):]
    return rel


def note_type(note) -> str:
    return vault_check.kebab(str(note.fm.get("type", "")))


# ---------------------------------------------------------------------------
# Edges, read in tiers
# ---------------------------------------------------------------------------
#
# The vault carries each relation in several forms. Every edge is keyed
# (source, target, key) and remembers each tier that produced it:
#   index        compiler-generated machine views (cross-subtree matrix)
#   frontmatter  typed front-matter properties, ids resolved through the
#                compiler registries
#   body         compiler relation blocks and catalogs, and body wikilinks
#                (aliases and embeds) resolved through vault link resolution
#   navigation   generated maps, home and nav sections (membership only)
#   text         targeted identifier search, run only for graph gaps
# Tiers are cross-checked where cheap; a disagreement is a graph gap.

TIERS = ("index", "frontmatter", "body", "navigation", "text")
LINK_KEY, LIST_KEY, MENTION_KEY = "links_to", "lists", "mentions"
UNTYPED_KEYS = (LINK_KEY, MENTION_KEY)
MATRIX = "maps/_generated/cross-subtree-matrix.md"


class Edges:
    def __init__(self, vault) -> None:
        self.vault = vault
        self.tiers: dict = {}

    def add(self, source: str, target: str, key: str, tier: str) -> None:
        if (source in self.vault.notes and target in self.vault.notes
                and source != target):
            self.tiers.setdefault((source, target, key), set()).add(tier)

    def of(self, tier: str, keys=None) -> set:
        return {edge for edge, tiers in self.tiers.items() if tier in tiers
                and (keys is None or edge[2] in keys)}


def is_navigation(vault, note) -> bool:
    maps_dir = vault.policy.get("maps_dir", "maps")
    return (note_type(note) in vault_check.NAVIGATION_TYPES or note.subtree == maps_dir
            or note.rel == vault.policy.get("home_file", "home.md"))


def index_tier(vault, edges: Edges) -> bool:
    matrix = vault.notes.get(MATRIX)
    if matrix is None:
        return False
    cell = re.compile(r"^\| \[\[([^\]|\\#]+)[^|]*\|[^|]*\| `([a-z_]+)` \| \[\[([^\]|\\#]+)")
    for line in matrix.lines:
        match = cell.match(line)
        if match:
            edges.add(f"{match.group(1)}.md", f"{match.group(3)}.md", match.group(2), "index")
    return True


def frontmatter_tier(vault, edges: Edges, keys) -> list:
    """Typed front-matter edges; returns the values no note resolves."""
    for edge in vault_check.relation_edges(vault):
        if edge.key in keys:
            edges.add(edge.source, edge.target, edge.key, "frontmatter")
    unresolved = []
    owners = None
    for note in vault_check.authored(vault):
        for key in sorted(keys):
            raw = note.fm.get(key)
            if raw in (None, "", []):
                continue
            for value in raw if isinstance(raw, list) else [raw]:
                if not isinstance(value, str):
                    continue
                if value.startswith("[[") and value.endswith("]]"):
                    target = vault_check.resolve_wikilink(
                        vault, split_wikilink(value[2:-2])[0], False)
                else:
                    if owners is None:
                        owners = vault_check.relation_identity_owners(vault)
                    target = owners.get(value.strip())
                if target in vault.notes and target != note.rel:
                    edges.add(note.rel, target, key, "frontmatter")
                else:
                    unresolved.append({"path": note.rel, "reason": "unresolved_relation",
                                       "key": key, "value": value})
    return unresolved


def body_tier(vault, edges: Edges) -> bool:
    """Relation blocks, relation catalogs and body wikilinks; True when any
    compiler relation block exists."""
    specs = vault_check.relation_specs(vault.policy)
    by_label = {str(spec.get("inverse_label")): key for key, spec in specs.items()
                if isinstance(spec, dict)}
    row = re.compile(r"^- (?P<label>[^:]+): \[\[(?P<source>[^\]|\\#]+)")
    blocks = False
    for note in vault.notes.values():
        text = "\n".join(note.lines)
        if note.generated and "/_relations/" in f"/{note.rel}":
            target = re.search(r"^Target: \[\[([^\]|\\#]+)", text, re.M)
            block, target_rel = (text, f"{target.group(1)}.md") if target else ("", "")
        else:
            block, target_rel = vault_check.relation_block(text), note.rel
        blocks = blocks or bool(block)
        for line in block.splitlines():
            match = row.match(line)
            if match and match.group("label") in by_label:
                edges.add(f"{match.group('source')}.md", target_rel,
                          by_label[match.group("label")], "body")
        if note.generated or is_navigation(vault, note):
            continue
        skip = set()
        inside = False
        for number, line in enumerate(note.lines, start=1):
            if vault_check.RELATION_START in line:
                inside = True
            if inside or vault_check.NAV_MARKER in line:
                skip.add(number)
            if vault_check.RELATION_END in line:
                inside = False
        nav = next((n for n, line in enumerate(note.lines, start=1)
                    if vault_check.NAV_MARKER in line), None)
        for lineno, embed, target, _anchor, _alias, _inner in note.wikilinks:
            if not target or lineno in skip or (nav is not None and lineno >= nav):
                continue
            resolved = vault_check.resolve_wikilink(vault, target, embed)
            if resolved:
                edges.add(note.rel, resolved, LINK_KEY, "body")
    return blocks


def navigation_tier(vault, edges: Edges) -> None:
    """Membership: a map, home or nav section lists the notes it links."""
    for note in vault.notes.values():
        if note.lines and note.lines[0] == vault_check.RELATION_CATALOG_MARKER:
            continue  # a relation view, read by the index and body tiers
        if is_navigation(vault, note):
            links = note.wikilinks
        else:
            nav = next((n for n, line in enumerate(note.lines, start=1)
                        if vault_check.NAV_MARKER in line), None)
            if nav is None:
                continue
            links = [link for link in note.wikilinks if link[0] >= nav]
        for _lineno, embed, target, _anchor, _alias, _inner in links:
            resolved = vault_check.resolve_wikilink(vault, target, embed) if target else None
            if not resolved:
                continue
            edges.add(note.rel, resolved, LIST_KEY, "navigation")


def identifiers(note) -> list:
    found = []
    for value in [note.fm.get("id"), *(note.fm.get("aliases") or [])]:
        if isinstance(value, str) and len(value.strip()) >= 3 and re.search(r"[0-9:-]", value):
            found.append(value.strip())
    return found


def text_tier(vault, edges: Edges, targets) -> None:
    """Targeted identifier search: only for notes the other tiers leave as gaps."""
    wanted = {}
    for rel in targets:
        for ident in identifiers(vault.notes[rel]):
            wanted.setdefault(ident, rel)
    if not wanted:
        return
    pattern = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(
        re.escape(i) for i in sorted(wanted, key=len, reverse=True)) + r")(?![A-Za-z0-9_-])")
    for note in vault_check.authored(vault):
        if is_navigation(vault, note):
            continue
        for match in pattern.finditer("\n".join(note.lines)):
            edges.add(note.rel, wanted[match.group(1)], MENTION_KEY, "text")


def graph(vault, policy: dict) -> tuple:
    """(Edges, gaps, tiers present) over every relation form the vault uses."""
    edges = Edges(vault)
    keys = set(policy["relation_keys"])
    present = {"index": index_tier(vault, edges)}
    gaps = frontmatter_tier(vault, edges, keys)
    present["frontmatter"] = True
    present["body"] = body_tier(vault, edges)
    navigation_tier(vault, edges)
    present["navigation"] = True

    contract = set(vault_check.relation_specs(vault.policy))
    typed = edges.of("frontmatter", contract)
    for tier, ready in (("index", present["index"]), ("body", present["body"])):
        if not ready:
            continue
        other = edges.of(tier, contract)
        for source, target, key in sorted(typed - other):
            gaps.append({"path": source, "reason": "tier_disagreement", "key": key,
                         "target": target, "tiers": ["frontmatter", tier],
                         "detail": f"missing from {tier}"})
        for source, target, key in sorted(other - typed):
            gaps.append({"path": source, "reason": "tier_disagreement", "key": key,
                         "target": target, "tiers": [tier, "frontmatter"],
                         "detail": "missing from frontmatter"})

    related = {rel for (s, t, k) in edges.tiers if k not in (LINK_KEY, LIST_KEY)
               for rel in (s, t)}
    isolated = sorted(rel for rel, note in vault.notes.items()
                      if not note.generated and not is_navigation(vault, note)
                      and rel not in related)
    gaps.extend({"path": rel, "reason": "no_typed_relations"} for rel in isolated)
    present["text"] = bool(isolated)
    text_tier(vault, edges, isolated)
    for source, target, _key in sorted(edges.of("text")):
        gaps.append({"path": target, "reason": "text_only_relation", "source": source,
                     "tiers": ["text"]})
    return edges, gaps, present


# ---------------------------------------------------------------------------
# Approval proof
# ---------------------------------------------------------------------------


def _split(text: str) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise ValueError(error)
    return props, "\n".join(text.splitlines()[body_line - 1:]).lstrip("\n")


def _backlog(path: Path, text: str) -> str:
    import backlog_compile
    return backlog_compile.digest_text(text)


def _delivery(path: Path, text: str) -> str:
    import delivery_compile
    return delivery_compile.content_hash(*_split(text))


def _delivery_review(path: Path, text: str) -> str:
    import delivery_compile
    return delivery_compile.content_hash(
        *_split(text), exclude=delivery_compile.MUTABLE - {"pull_request_url"})


def _requirement(path: Path, text: str) -> str:
    import requirement_compile
    return requirement_compile.semantic_hash(*_split(text))


def _experience(path: Path, text: str) -> str:
    import experience_compile
    if path.name != "experience.md":
        raise ValueError("an experience stamp covers its package root only")
    return experience_compile.source_digest(path.parent)


def _operation(path: Path, text: str) -> str:
    import operation_compile
    return operation_compile.receipt_hash(*_split(text))


def _governance(path: Path, text: str) -> str:
    import delivery_governance
    return delivery_governance.governance_hash(*_split(text))


def _architecture(path: Path, text: str) -> str:
    import architecture_compile
    return architecture_compile.source_hash(path)


# Each owning compiler's own approval digest; a stamp is proven when the
# digest of the scheme that wrote it matches the note's current bytes.
APPROVAL_SCHEMES = {
    "backlog": _backlog,
    "delivery": _delivery,
    "delivery_review": _delivery_review,
    "requirement": _requirement,
    "experience": _experience,
    "operation": _operation,
    "governance": _governance,
    "architecture": _architecture,
}


def approval_state(note) -> dict | None:
    """None for an unstamped note, else the stamp and whether bytes still match."""
    stamp = note.fm.get("source_hash")
    if not isinstance(stamp, str) or not stamp:
        return None
    text = note.path.read_text(encoding="utf-8")
    for name, scheme in APPROVAL_SCHEMES.items():
        try:
            if scheme(note.path, text) == stamp:
                return {"approval_hash": stamp, "scheme": name, "proven": True}
        except Exception:  # noqa: BLE001 - a scheme that cannot parse is no proof
            continue
    return {"approval_hash": stamp, "scheme": None, "proven": False}


def ba_package_proofs(docs: Path) -> dict:
    """Notes of approved analysis spaces whose package hash still matches."""
    import ba_compile
    proofs: dict = {}
    root = docs / "business-analysis"
    if not root.is_dir():
        return proofs
    for space in sorted(p for p in root.iterdir() if p.is_dir()):
        overview = space / "space.md"
        if not overview.is_file():
            continue
        props = parse_frontmatter(overview.read_text(encoding="utf-8"))[0]
        stamp = props.get("package_hash")
        if props.get("package_status") != "approved" or not stamp:
            continue
        proven = ba_compile.package_hash(space) == stamp
        for path in space.rglob("*.md"):
            proofs[path.relative_to(docs).as_posix()] = {
                "approval_hash": stamp, "scheme": "analysis_package", "proven": proven}
    return proofs


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------


def proofs_for(docs: Path, notes, only=None) -> dict:
    """Approval proof per stamped note (``only`` limits the notes checked)."""
    proofs = {rel: state for rel, state in ba_package_proofs(docs).items()
              if only is None or rel in only}
    for note in notes:
        if only is not None and note.rel not in only:
            continue
        state = approval_state(note)
        if state is not None:
            proofs[note.rel] = state
    return proofs


def snapshot(vault, policy: dict | None = None, proofs: dict | None = None) -> dict:
    """Everything a closure needs, as plain data a cache can keep."""
    policy = closure_policy(vault.policy, policy)
    edges, gaps, present = graph(vault, policy)
    authored = vault_check.authored(vault)
    return {
        "notes": {note.rel: note_type(note) for note in authored},
        "citers": {rel: sorted(source for source in sources
                               if source in vault.notes and not vault.notes[source].generated
                               and source != rel)
                   for rel, sources in vault.inbound.items()},
        "edges": edges.tiers,
        "gaps": gaps,
        "tiers": present,
        "proofs": proofs if proofs is not None else proofs_for(vault.root, authored),
    }


def closure(docs: Path, changed: list[str], *, policy: dict | None = None) -> dict:
    """The impact closure of ``changed`` (docs-relative note paths).

    Every approved note whose stamp no longer matches its bytes joins the
    change set: a changed approved note is never proven. A changed shared
    contract or process policy widens the closure to every citing note.
    """
    vault = load_vault(Path(docs).absolute())
    return closure_from(snapshot(vault, policy), changed,
                        closure_policy(vault.policy, policy))


def closure_from(snap: dict, changed: list[str], policy: dict) -> dict:
    """The closure over a ``snapshot`` (see ``closure``)."""
    notes, proofs = snap["notes"], snap["proofs"]
    dependents: dict = {}
    constraints: dict = {}
    listings: dict = {}
    citations: dict = {}
    for source, target, key in snap["edges"]:
        if key == LIST_KEY:
            listings.setdefault(target, set()).add(source)
            continue
        if key in UNTYPED_KEYS:
            citations.setdefault(target, set()).add(source)
            continue
        dependents.setdefault(target, set()).add(source)
        if key in policy["constraint_keys"]:
            constraints.setdefault(source, set()).add(target)

    stale = sorted(rel for rel, state in proofs.items()
                   if rel in notes and not state["proven"])
    change_set = sorted({normalize(rel) for rel in changed} | set(stale))

    widened_by = []
    frontier = [rel for rel in change_set if rel in notes]
    for rel in change_set:
        kind = None
        if rel in policy["policy_paths"] or notes.get(rel) in policy["policy_types"]:
            kind = "process_policy"
        elif notes.get(rel) in policy["shared_contract_types"]:
            kind = "shared_contract"
        if kind:
            citers = list(snap["citers"].get(rel, []))
            widened_by.append({"path": rel, "reason": kind, "citers": citers})
            frontier.extend(citers)
    # An untyped body link or identifier mention says a note cites the
    # change, not what it depends on: it joins one hop, never transitively.
    for rel in list(frontier):
        frontier.extend(citations.get(rel, ()))
    members = set()
    while frontier:
        rel = frontier.pop()
        if rel in members:
            continue
        members.add(rel)
        frontier.extend(dependents.get(rel, ()))
    pending = [rel for rel in change_set if rel in notes]
    seen = set()
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for target in constraints.get(rel, ()):
            members.add(target)
            pending.append(target)

    for rel in change_set:
        members.update(listings.get(rel, ()))

    outside = sorted(set(notes) - members)
    return {
        "changed": change_set,
        "closure": sorted(members),
        "proven_unchanged": [{"path": rel, "approval_hash": proofs[rel]["approval_hash"],
                              "scheme": proofs[rel]["scheme"]}
                             for rel in outside if rel in proofs and proofs[rel]["proven"]],
        "unstamped": [rel for rel in outside if rel not in proofs],
        "stale_approved": stale,
        "widened_by": widened_by,
        "graph_gaps": snap["gaps"],
        "tiers": snap["tiers"],
        "edges": [{"source": source, "target": target, "key": key,
                   "tiers": [tier for tier in TIERS if tier in tiers]}
                  for (source, target, key), tiers in sorted(snap["edges"].items())
                  if source in members or target in members],
    }


def vault_views(docs: Path) -> dict:
    """The relation views a role consults first, with what each answers."""
    docs = Path(docs)
    views = []
    for pattern, answers in VIEW_SPECS:
        paths = sorted(p.relative_to(docs).as_posix() for p in docs.glob(pattern)
                       if p.is_file())
        views.append({"pattern": pattern, "answers": answers, "paths": paths})
    return {"docs": str(docs), "views": views}


def record_beyond(manifest: dict, path: str, reason: str) -> dict:
    """Record one read beyond the closure on ``manifest`` (in place) and return it."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("a beyond-closure read names the note or file it read")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a beyond-closure read records why the closure was insufficient")
    entry = {"path": normalize(path.strip()), "reason": reason.strip()}
    reads = manifest.setdefault("beyond_closure", [])
    if entry not in reads:
        reads.append(entry)
    return manifest


# ---------------------------------------------------------------------------
# Heal
# ---------------------------------------------------------------------------


def read_only_context(role: str | None, entry: str | None = None,
                      mode: str | None = None) -> bool:
    import task_inputs
    policy = task_inputs.catalog()
    if mode in READ_ONLY_MODES:
        return True
    if role in policy["read_only_roles"]:
        return True
    entry = (entry or "").replace("_", "-")
    return bool(entry) and role in policy["read_only_entry_roles"].get(entry, [])


def with_relation(text: str, key: str, item: str) -> str:
    """``text`` with ``item`` appended to front-matter block list ``key``."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise HealRefused("source note has no front matter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise HealRefused("source note front matter is not closed")
    line = f"  - {item}"
    for index in range(1, end):
        if re.match(rf"^{re.escape(key)}:\s*$", lines[index]):
            last = index
            while last + 1 < end and re.match(r"^\s+- ", lines[last + 1]):
                last += 1
            lines.insert(last + 1, line)
            return "\n".join(lines)
        if re.match(rf"^{re.escape(key)}:", lines[index]):
            raise HealRefused(f"relation '{key}' is not a block list")
    lines[end:end] = [f"{key}:", line]
    return "\n".join(lines)


def _reaches(edges: set, start: str, goal: str, keys: set) -> bool:
    forward: dict = {}
    for source, target, key in edges:
        if key in keys:
            forward.setdefault(source, set()).add(target)
    stack, seen = [start], set()
    while stack:
        rel = stack.pop()
        if rel == goal:
            return True
        if rel not in seen:
            seen.add(rel)
            stack.extend(forward.get(rel, ()))
    return False


def _snapshot(docs: Path) -> dict:
    return {p.relative_to(docs).as_posix(): p.read_bytes()
            for p in docs.rglob("*.md") if p.is_file()}


def _render(docs: Path) -> tuple[int, str, dict, dict]:
    snapshot = _snapshot(docs)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        status = vault_check.main(["render-relations", "--vault", str(docs)])
    return status, out.getvalue().strip(), snapshot, _snapshot(docs)


def _changed(before: dict, after: dict, skip: str = "") -> list:
    return sorted(rel for rel in set(before) | set(after)
                  if rel != skip and before.get(rel) != after.get(rel))


def _refuse_read_only(role: str, entry: str | None, mode: str | None) -> None:
    if read_only_context(role, entry, mode):
        raise PermissionError(
            f"role '{role}' is read-only here: return the relation as a finding;"
            " the flow's writer applies it")


def heal_views(docs: Path, *, role: str, entry: str | None = None,
               mode: str | None = None) -> dict:
    """Re-render the generated relation views when a tier disagrees with the
    typed front matter; the renderer is vault_check's own."""
    _refuse_read_only(role, entry, mode)
    docs = Path(docs).absolute()
    status, output, before, after = _render(docs)
    if status != 0:
        raise HealRefused(f"relation views did not re-render: {output}")
    return {"status": "rendered", "role": role, "views": _changed(before, after)}


def heal_relation(docs: Path, source: str, target: str, kind: str, evidence: str, *,
                  role: str, entry: str | None = None, mode: str | None = None,
                  alias: str | None = None) -> dict:
    """Write one missing typed relation and re-render the relation views.

    The postimage must pass vault_check's relation contract with no finding
    the preimage lacked. An approved source note's stamp goes stale, so the
    repair belongs to that package's current or next revision.
    """
    _refuse_read_only(role, entry, mode)
    if not isinstance(evidence, str) or not evidence.strip():
        raise HealRefused("a relation heal records its evidence")
    docs = Path(docs).absolute()
    vault = load_vault(docs)
    specs = vault_check.relation_specs(vault.policy)
    if kind not in specs:
        raise HealRefused(f"relation '{kind}' is outside the vault relation contract;"
                          " its owning compiler writes it")
    source, target = normalize(source), normalize(target)
    for rel in (source, target):
        if rel not in vault.notes or vault.notes[rel].generated:
            raise HealRefused(f"'{rel}' is not an authored vault note")
    if source == target:
        raise HealRefused("a relation never points to its own note")
    policy = closure_policy(vault.policy)
    edges = graph(vault, policy)[0]
    if (source, target, kind) in edges.of("frontmatter"):
        return {"status": "unchanged", "source": source, "target": target,
                "kind": kind, "evidence": evidence, "diff": "", "views": []}
    if kind in policy["acyclic_keys"] and _reaches(set(edges.tiers), target, source, {kind}):
        raise HealRefused(f"relation '{kind}' from '{source}' to '{target}' closes a cycle")
    target_note = vault.notes[target]
    label = alias or vault_check.relation_source_alias(target_note)
    item = frontmatter_item(f"[[{target[:-3]}|{label}]]")
    source_path = docs / source
    before = source_path.read_text(encoding="utf-8")
    after = with_relation(before, kind, item)

    preimage = vault_check.changed_findings(vault, [source])[source]
    files = vault_check.VaultFileView(docs)
    files.put(source_path, after.encode("utf-8"))
    post = vault_check.build_vault(docs, vault.policy, files)
    postimage = vault_check.changed_findings(post, [source])[source]
    known = {(f.check, f.message) for f in preimage}
    new = [f for f in postimage if (f.check, f.message) not in known]
    if new:
        raise HealRefused("relation contract refuses the heal: "
                          + "; ".join(sorted(f.message for f in new)))

    stale = bool(vault.notes[source].fm.get("source_hash"))
    snapshot = _snapshot(docs)
    atomic_file.replace_text(source_path, after)
    status, output, _written, rendered = _render(docs)
    if status != 0:
        atomic_file.replace_text(source_path, before)
        raise HealRefused(f"relation views did not re-render: {output}")
    views = _changed(snapshot, rendered, skip=source)
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{source}", tofile=f"b/{source}"))
    return {"status": "written", "source": source, "target": target, "kind": kind,
            "alias": label, "evidence": evidence.strip(), "role": role,
            "diff": diff, "views": views, "approval_stale": stale}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("closure")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--changed", nargs="*", default=[])
    p = sub.add_parser("views")
    p.add_argument("--docs", type=Path, required=True)
    p = sub.add_parser("heal")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--entry", default=None)
    p.add_argument("--mode", default=None)
    p.add_argument("--alias", default=None)
    p = sub.add_parser("render")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--entry", default=None)
    p.add_argument("--mode", default=None)
    args = parser.parse_args(argv)
    if not args.docs.is_dir():
        print(f"impact_closure: docs directory not found: {args.docs}", file=sys.stderr)
        return 2
    try:
        if args.command == "closure":
            result = closure(args.docs, args.changed)
        elif args.command == "views":
            result = vault_views(args.docs)
        elif args.command == "render":
            result = heal_views(args.docs, role=args.role, entry=args.entry, mode=args.mode)
        else:
            result = heal_relation(args.docs, args.source, args.target, args.kind,
                                   args.evidence, role=args.role, entry=args.entry,
                                   mode=args.mode, alias=args.alias)
    except PermissionError as exc:
        print(f"impact_closure: {exc}", file=sys.stderr)
        return 3
    except HealRefused as exc:
        print(f"impact_closure: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
