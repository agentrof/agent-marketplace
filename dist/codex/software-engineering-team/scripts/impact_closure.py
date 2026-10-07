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

The module is read-only: it indexes the vault and tells a role where to look,
and never writes a vault file.

The closure never caps a read: a role that reads beyond it records the read
with ``record_beyond``. Relations are read in tiers (see "Edges, read in tiers"); every edge
records the tiers that produced it, and a disagreement between tiers is a
graph gap. Every gap carries its evidence and a ``suggested_fix``; a role
reports it as a finding and the flow's writer applies the fix through the
owning compiler during its normal revision.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import vault_check
from ba_compile import parse_frontmatter, split_wikilink

# Relation keys outside the vault relation contract that still draw
# dependency edges: their owning compilers validate them.
EXTRA_RELATION_KEYS = ("depends_on", "governs", "applies_to")
# Keys whose targets constrain the source: a changed note pulls them in.
CONSTRAINT_KEYS = ("constrained_by", "uses_design", "satisfies")
# Relations that point up to what a note answers to: a change re-reads their
# targets, one hop, since a changed note may no longer meet them.
UPWARD_KEYS = ("implements", "derives_from", "verifies")
# Note types whose change widens the closure to every citing note.
SHARED_CONTRACT_EXTRA_TYPES = ("verification-contract", "environment-contract",
                               "delivery-governance", "architecture-standard",
                               "definition-of-done")
POLICY_TYPES = ("process-policy",)
POLICY_PATHS = ("delivery/process-policy.md",)
# Keys on which a relation must not close a cycle.
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
        "upward_keys": list(UPWARD_KEYS),
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


def changed_paths(docs: Path, changed: list[str], notes) -> list[str]:
    """Each changed path as docs-relative, whether given relative to the docs,
    the project (``workspace/docs/...``, ``./workspace/docs/...``) or absolute.

    A path outside the vault, or naming neither a note nor a file in it, is
    refused rather than dropped from the closure.
    """
    root = Path(docs).resolve()
    result = []
    for raw in changed:
        text = str(raw).replace("\\", "/")
        if Path(text).is_absolute():
            try:
                text = Path(text).resolve().relative_to(root).as_posix()
            except ValueError:
                raise ValueError(f"changed path {raw!r} is outside the vault") from None
        while text.startswith("./"):
            text = text[2:]
        rel = normalize(text)
        if not rel or ".." in rel.split("/") or rel.startswith("/"):
            raise ValueError(f"changed path {raw!r} is outside the vault")
        if rel not in notes and not (root / rel).is_file():
            raise ValueError(f"changed path {raw!r} names no note or file in the vault")
        result.append(rel)
    return result


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
        machine_record = (target in self.vault.index and target.endswith(".json")
                          and bool({"_generated", "_ledger"} & set(Path(target).parts)))
        if (source in self.vault.notes and (target in self.vault.notes or machine_record)
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


# Front-matter keys that name the note itself or carry no reference.
SELF_KEYS = frozenset({"id", "aliases", "title", "type", "status", "source_hash",
                       "package_hash", "approved_at_utc"})
REF_KEY = re.compile(r"(_ref|_refs)$")
ID_VALUE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]+(?:@r[0-9]+)?$")


def frontmatter_values(value):
    """Every string inside a front-matter value, through lists and mappings."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from frontmatter_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from frontmatter_values(item)


def reference_owners(vault, records: dict | None = None) -> dict:
    """Resolve note and source-unit identities without selecting an ambiguous owner."""
    if records is not None and hasattr(records, "owner_lookup"):
        return records.owner_lookup
    owners = dict(vault_check.relation_identity_owners(vault))
    for note in vault_check.authored(vault):
        ident = note.fm.get("id")
        if isinstance(ident, str) and ident.strip():
            owners.setdefault(ident.strip(), note.rel)
        component = note.fm.get("component_id")
        if isinstance(component, str) and component:
            owners.setdefault(component, note.rel)
        record = note.fm.get("record_id")
        revision = note.fm.get("revision")
        if record and revision:
            exact = (f"ARC:{record}@r{revision}" if str(record).startswith("CON-") else
                     f"ARC:{note.fm.get('component_ref') or 'ROOT'}:{record}@r{revision}")
            owners.setdefault(exact, note.rel)
    import context_catalog
    records = context_catalog.catalog(vault) if records is None else records
    for reference, identities in records["aliases"].items():
        owners[reference] = (records["units"][identities[0]]["path"] if len(identities) == 1 else None)
    for identity, unit in records["units"].items():
        owners[identity] = unit["path"]
    return owners


def resolve_reference(vault, value: str, owners: dict) -> str | None:
    """The note a front-matter value names, by wikilink, path, id or alias."""
    text = value.strip()
    binding = text.split("|")
    if len(binding) == 3 and binding[2].startswith("sha256:") and not text.startswith("[["):
        return resolve_reference(vault, binding[1], owners)
    if text.startswith("[[") and text.endswith("]]"):
        return vault_check.resolve_wikilink(vault, split_wikilink(text[2:-2])[0], False)
    if text in owners:
        return owners[text]
    if not text or "\n" in text or len(text) > 300:
        return None
    rel = normalize(text.lstrip("./"))
    for candidate in (rel, f"{rel}.md"):
        if candidate in vault.notes:
            return candidate
    return owners.get(text)


def looks_like_reference(key: str, value: str, relation: bool) -> bool:
    text = value.strip()
    binding = text.split("|")
    return (relation or bool(REF_KEY.search(key)) or text.startswith("[[")
            or bool(ID_VALUE.match(text)) or (len(binding) == 3 and binding[2].startswith("sha256:")))


def frontmatter_tier(vault, edges: Edges, keys, records: dict | None = None) -> list:
    """Edges for every front-matter value that names a note, typed relation or
    not (``requirement_ref``, ``verification_contract_ref``, any wikilink, id,
    alias or path); returns the reference-like values no note resolves."""
    owners = reference_owners(vault, records)
    for edge in vault_check.relation_edges(vault):
        if edge.key in keys and not (edge.alias in owners and owners[edge.alias] is None):
            edges.add(edge.source, edge.target, edge.key, "frontmatter")
    unresolved = []
    for note in vault_check.authored(vault):
        for key in sorted(note.fm):
            if key in SELF_KEYS:
                continue
            for value in frontmatter_values(note.fm[key]):
                if key == "dependency_refs":
                    import backlog_compile
                    names = value.split(" -> ")
                    detail = None
                    if len(names) != 2 or any(not backlog_compile.ID_RE.fullmatch(name) for name in names):
                        detail = "malformed directed dependency edge"
                        targets = []
                    else:
                        targets = [resolve_reference(vault, name, owners) for name in names]
                        if any(target not in vault.notes or note_type(vault.notes[target]) != "story"
                               for target in targets):
                            detail = "dependency endpoint is missing, ambiguous or has the wrong type"
                        else:
                            declared = {resolve_reference(vault, link, owners) for link in
                                        frontmatter_values(vault.notes[targets[0]].fm.get("depends_on", []))}
                            if targets[1] not in declared:
                                detail = "directed dependency is not declared by its source"
                    if detail:
                        unresolved.append({"path": note.rel, "reason": "unresolved_relation",
                                           "key": key, "value": value, "detail": detail})
                    else:
                        for target in targets:
                            edges.add(note.rel, target, key, "frontmatter")
                    continue
                target = resolve_reference(vault, value, owners)
                if target in vault.index and target != note.rel:
                    edges.add(note.rel, target, key, "frontmatter")
                elif target is None and looks_like_reference(key, value, key in keys):
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


def graph(vault, policy: dict, records: dict | None = None) -> tuple:
    """(Edges, gaps, tiers present) over every relation form the vault uses."""
    edges = Edges(vault)
    keys = set(policy["relation_keys"])
    present = {"index": index_tier(vault, edges)}
    gaps = frontmatter_tier(vault, edges, keys, records)
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
    for gap in gaps:
        gap["suggested_fix"] = suggested_fix(gap)
    return edges, gaps, present


def suggested_fix(gap: dict) -> str:
    """What the flow's writer changes, through the owning compiler, to close ``gap``."""
    reason, path = gap["reason"], gap["path"]
    if reason == "tier_disagreement" and gap.get("detail") == "missing from frontmatter":
        return (f"declare `{gap['key']}` to {gap['target']} in the front matter of {path},"
                " or remove the stale entry from the generated view by re-rendering it")
    if reason == "tier_disagreement":
        return (f"re-render the generated relation views so they carry `{gap['key']}`"
                f" from {path} to {gap['target']}")
    if reason == "no_typed_relations":
        return f"declare the typed relations of {path} in its front matter"
    if reason == "text_only_relation":
        return (f"declare the typed relation {gap['source']} names to {path} by identifier"
                " in the front matter of the citing note")
    if reason == "unresolved_relation":
        if gap.get("key") == "dependency_refs":
            return (f"correct `dependency_refs` {gap.get('value')} in {path} against the owning "
                    f"dependency declarations: {gap.get('detail', 'its endpoints do not resolve')}")
        return (f"correct or remove `{gap.get('key')}` {gap.get('value')} in {path}:"
                " its target does not resolve")
    return f"correct the relations of {path}"


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


def architecture_proofs(docs: Path, notes) -> dict:
    """A sealed architecture record is proven by its immutable ledger snapshot:
    the record carries no stamp, the snapshot keeps the sealed source hash."""
    import architecture_compile
    root = Path(docs) / "system-architecture"
    proofs: dict = {}
    for note in notes:
        if not note.rel.startswith("system-architecture/") \
                or note.fm.get("revision_state") != "sealed":
            continue
        record_id = architecture_compile.stable_id(note.fm)
        snapshot = root / "_ledger" / "records" / record_id / f"r{note.fm.get('revision')}.json"
        try:
            saved = json.loads(snapshot.read_text(encoding="utf-8")).get("source_hash")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(saved, str) and saved:
            proofs[note.rel] = {"approval_hash": saved, "scheme": "architecture",
                                "proven": architecture_compile.source_hash(note.path) == saved}
    return proofs


def proofs_for(docs: Path, notes, only=None) -> dict:
    """Approval proof per stamped note (``only`` limits the notes checked)."""
    notes = list(notes)
    proofs = {rel: state for rel, state in ba_package_proofs(docs).items()
              if only is None or rel in only}
    proofs.update(architecture_proofs(docs, [note for note in notes
                                             if only is None or note.rel in only]))
    for note in notes:
        if only is not None and note.rel not in only:
            continue
        state = approval_state(note)
        if state is not None:
            proofs[note.rel] = state
    return proofs


def citers(vault, edges: Edges) -> dict:
    """Every authored note that cites a note: by body link or by any front-matter
    reference, so widening on a shared contract reaches each Item that names it."""
    found: dict = {}
    for rel, sources in vault.inbound.items():
        found.setdefault(rel, set()).update(sources)
    for source, target, key in edges.tiers:
        if key != LIST_KEY:
            found.setdefault(target, set()).add(source)
    return {rel: sorted(source for source in sources
                        if source in vault.notes and not vault.notes[source].generated
                        and source != rel)
            for rel, sources in sorted(found.items())}


def snapshot(vault, policy: dict | None = None, proofs: dict | None = None,
             records: dict | None = None) -> dict:
    """Everything a closure needs, as plain data a cache can keep."""
    policy = closure_policy(vault.policy, policy)
    edges, gaps, present = graph(vault, policy, records)
    authored = vault_check.authored(vault)
    nodes = {note.rel: note_type(note) for note in authored}
    for _source, target, _key in edges.tiers:
        if target not in vault.notes and target.endswith(".json"):
            nodes[target] = "compiler-record"
    return {
        "notes": nodes,
        "citers": citers(vault, edges),
        "edges": edges.tiers,
        "gaps": gaps,
        "tiers": present,
        "proofs": proofs if proofs is not None else proofs_for(vault.root, authored),
    }


def earlier_relations(docs: Path, texts: dict) -> dict:
    """Notes each deleted note named in its front matter, resolved in the
    current vault: ``texts`` maps the deleted note to its earlier bytes."""
    vault = load_vault(Path(docs).absolute())
    owners = reference_owners(vault)
    result = {}
    for rel, text in texts.items():
        props = parse_frontmatter(text)[0] or {}
        targets = {resolve_reference(vault, value, owners)
                   for key, raw in props.items() if key not in SELF_KEYS
                   for value in frontmatter_values(raw)}
        result[rel] = sorted(target for target in targets if target in vault.notes)
    return result


def closure(docs: Path, changed: list[str], *, policy: dict | None = None,
            deleted: dict | None = None) -> dict:
    """The impact closure of ``changed`` (docs-relative note paths).

    Every approved note whose stamp no longer matches its bytes joins the
    change set: a changed approved note is never proven. A changed shared
    contract or process policy widens the closure to every citing note.
    """
    vault = load_vault(Path(docs).absolute())
    snap = snapshot(vault, policy)
    result = closure_from(snap, changed_paths(docs, changed, snap["notes"]),
                          closure_policy(vault.policy, policy))
    if deleted:
        # A deleted note's earlier relations name what it answered to and what
        # it constrained: those notes, and their closure, are re-read.
        seeds = sorted({target for targets in earlier_relations(docs, deleted).values()
                        for target in targets})
        if seeds:
            more = closure_from(snap, sorted({*result["changed"], *seeds}),
                                closure_policy(vault.policy, policy))
            more["changed"] = result["changed"]
            result = more
        result["deleted"] = sorted(deleted)
    return result


def closure_from(snap: dict, changed: list[str], policy: dict) -> dict:
    """The closure over a ``snapshot`` (see ``closure``)."""
    notes, proofs = snap["notes"], snap["proofs"]
    dependents: dict = {}
    constraints: dict = {}
    upward: dict = {}
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
        if key in policy.get("upward_keys", ()):
            upward.setdefault(source, set()).add(target)

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
        members.update(upward.get(rel, ()))

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
    args = parser.parse_args(argv)
    if not args.docs.is_dir():
        print(f"impact_closure: docs directory not found: {args.docs}", file=sys.stderr)
        return 2
    try:
        if args.command == "closure":
            result = closure(args.docs, args.changed)
        else:
            result = vault_views(args.docs)
    except ValueError as exc:
        print(f"impact_closure: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
