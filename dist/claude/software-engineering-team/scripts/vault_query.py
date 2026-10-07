#!/usr/bin/env python3
"""Query the docs vault through a disposable checkout-local SQLite index.

The index lives at ``.agentrof/agent-marketplace/.runtime/vault-index/index.db``.
It contains eligible Markdown and JSON outside all artifact subtrees. Queries
reconcile source changes and use indexed records; source and approval checks
remain authoritative. Deleting the cache loses no project truth.

Verbs (all print JSON):
  closure --changed P...      impact closure of the changed notes
  related <id|path>           outgoing and incoming edges, by key and tier
  who-cites <id|path>         notes that cite the note, by key and tier
  path <a> <b>                shortest relation path between two notes
  find <id|alias|title>       notes matching an id, alias, title or path
  hash <path>                 approval hash, scheme and proven-unchanged check
  changed-since <ref>         notes changed since a Git ref, plus stale stamps
  gaps [--reason R]           graph gaps (missing, unresolved, disagreeing)
  search <terms>              text search over the notes: ids and line anchors
  search-sections <terms>     indexed candidate source units through FTS5
  index ensure|sync|rebuild   source-derived cache maintenance
  index status|check         readonly diagnostics and coverage verification

Common options: --docs D (required, the project's ``workspace/docs``),
--verify (accepted for compatibility; every query already hashes every eligible
source before relying on the index).
The cache location is fixed and never configurable: the tool writes only
inside that ``vault-index`` folder and never a vault or other project file.
Python SQLite/FTS5 only; no server or embeddings.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import subprocess
import sys
from collections import deque
from pathlib import Path

import impact_closure
import vault_check
import context_catalog
import vault_index

SCHEMA_VERSION = vault_index.SCHEMA_VERSION
RUNTIME = Path(".agentrof") / "agent-marketplace" / ".runtime" / "vault-index"
BUILDER_FILES = ("impact_closure.py", "vault_check.py", "vault_query.py", "ba_compile.py", "context_catalog.py", "context_history.py", "project_context.py", "vault_index.py")


READ_ONLY_ERRORS = {errno.EACCES, errno.EPERM, errno.EROFS}


def project_docs(project: Path) -> Path:
    """Bind the vault to the selected root before following any directory link."""
    project = Path(project).resolve()
    for relative in ("workspace", "workspace/docs"):
        path = project / relative
        if path.resolve() != path:
            raise ValueError("workspace/docs must stay inside the selected project without directory links")
    return project / "workspace/docs"


def default_cache(docs: Path) -> Path:
    """The one cache file: ``<project>/.agentrof/.../vault-index/index.db``.

    ``docs`` must be a project's ``workspace/docs`` directory, so the cache
    folder sits outside the vault; a folder that resolves elsewhere, through a
    link or otherwise, is refused.
    """
    docs = Path(docs).absolute()
    if docs.name != "docs" or docs.parent.name != "workspace":
        raise ValueError(f"--docs must be a project's workspace/docs directory, not {docs}")
    docs = project_docs(docs.parents[1])
    project = docs.parents[1]
    folder = project / RUNTIME
    resolved = folder.resolve()
    if resolved != folder or resolved.is_relative_to(docs) or not resolved.is_relative_to(
            (project / ".agentrof").resolve()):
        raise ValueError(f"the vault index folder resolves outside the runtime scratch: {resolved}")
    return folder / "index.db"


def builder_hash() -> str:
    """Code identity: a changed reader or policy invalidates the whole cache."""
    digest = hashlib.sha256(str(SCHEMA_VERSION).encode())
    here = Path(__file__).resolve().parent
    for name in BUILDER_FILES:
        digest.update((here / name).read_bytes())
    digest.update(vault_check.DEFAULT_POLICY.read_bytes())
    digest.update(vault_index.POLICY.read_bytes())
    return digest.hexdigest()


def scan_files(docs: Path) -> dict:
    """Every eligible source with its size, modification time and hash."""
    return vault_index.scan_files(docs)


def locked_refresh(docs: Path, cache: Path, *, rebuild: bool = False,
                   repair: bool = False, failed=None, wait: bool = True):
    return vault_index.locked_refresh(docs, cache, builder_hash(), rebuild=rebuild, repair=repair,
                                      failed=failed, wait=wait)


def refresh(docs: Path, cache: Path, *, persist: bool = True, rebuild: bool = False):
    return vault_index.refresh(docs, cache, builder_hash(), persist=persist, rebuild=rebuild)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class Index:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.store = getattr(data, "store", None)

    def resolve(self, ref: str) -> str:
        """A graph node: a note, or a machine record an edge cites; never a generic JSON source."""
        rel = impact_closure.normalize(ref)
        for candidate in (rel, f"{rel}.md"):
            if candidate in self.data["notes"]:
                return candidate
        hits = self.find(ref, graph=True)
        if len(hits) == 1:
            return hits[0]["path"]
        raise LookupError(f"'{ref}' names {len(hits)} notes; use a path"
                          + (": " + ", ".join(h["path"] for h in hits[:10]) if hits else ""))

    def find(self, ref: str, graph: bool = False) -> list:
        wanted = ref.strip().lower()
        notes = self.data["notes"]
        records = context_catalog.resolve(self.data.get("catalog", {"units": {}, "aliases": {}}), ref)
        if graph:
            records = [row for row in records if row["kind"] != "json"]
        if records:
            return [{"path": row["path"], "id": ref, "title": row["label"],
                     "type": self.data["catalog"]["documents"][row["path"]]["type"],
                     "unit_kind": row["kind"], "aliases": [ref], "unit_id": row["unit_id"]}
                    for row in records]
        if self.store is not None:
            return self.store.find_notes(wanted)
        exact = [rel for rel, n in notes.items()
                 if wanted in {n["id"].lower(), n["title"].lower(),
                               *(a.lower() for a in n["aliases"])}]
        hits = exact or [rel for rel in notes if wanted in rel.lower()]
        return [{"path": rel, **{k: notes[rel][k] for k in ("id", "title", "type", "aliases")}}
                for rel in sorted(hits)]

    def edges(self, field: int, rel: str) -> list:
        if self.store is not None:
            return [{"source": s, "target": t, "key": k, "tiers": tiers}
                    for s, t, k, tiers in self.store.edges_for(rel, "source" if field == 0 else "target")]
        return [{"source": s, "target": t, "key": k, "tiers": tiers}
                for s, t, k, tiers in sorted(self.data["edges"], key=lambda e: (e[2], e[0], e[1]))
                if (s, t)[field] == rel]

    def snapshot(self) -> dict:
        if self.store is not None:
            return self.store.closure_snapshot()
        return {
            "notes": {rel: n["type"] for rel, n in self.data["notes"].items() if n["authored"]},
            "citers": self.data["citers"],
            "edges": {(s, t, k): set(tiers) for s, t, k, tiers in self.data["edges"]},
            "gaps": self.data["gaps"],
            "tiers": self.data["tiers"],
            "proofs": self.data["proofs"],
        }


def by_key(edges: list, field: str) -> dict:
    grouped: dict = {}
    for edge in edges:
        grouped.setdefault(edge["key"], []).append({"note": edge[field], "tiers": edge["tiers"]})
    return grouped


def q_related(index: Index, args) -> dict:
    rel = index.resolve(args.ref)
    return {"note": rel, "outgoing": by_key(index.edges(0, rel), "target"),
            "incoming": by_key(index.edges(1, rel), "source")}


def q_who_cites(index: Index, args) -> dict:
    rel = index.resolve(args.ref)
    incoming = [e for e in index.edges(1, rel) if e["key"] != impact_closure.LIST_KEY]
    return {"note": rel, "cited_by": by_key(incoming, "source"),
            "citers": sorted({e["source"] for e in incoming})}


def q_path(index: Index, args) -> dict:
    start, goal = index.resolve(args.a), index.resolve(args.b)
    links: dict = {}
    for s, t, k, tiers in ([] if index.store else index.data["edges"]):
        if k == impact_closure.LIST_KEY:
            continue  # maps connect everything; membership is not a relation path
        hop = {"key": k, "tiers": tiers}
        links.setdefault(s, []).append((t, {**hop, "direction": "out"}))
        links.setdefault(t, []).append((s, {**hop, "direction": "in"}))
    previous = {start: None}
    queue = deque([start])
    while queue:
        rel = queue.popleft()
        if rel == goal:
            break
        if index.store is not None:
            # The same hop order as the full edge list: (source, target, key).
            links[rel] = []
            for s, t, k, tiers in index.store.edges_touching(rel):
                if k == impact_closure.LIST_KEY:
                    continue
                hop = {"key": k, "tiers": tiers}
                if s == rel:
                    links[rel].append((t, {**hop, "direction": "out"}))
                if t == rel:
                    links[rel].append((s, {**hop, "direction": "in"}))
        for nxt, hop in sorted(links.get(rel, []), key=lambda item: item[0]):
            if nxt not in previous:
                previous[nxt] = (rel, hop)
                queue.append(nxt)
    if goal not in previous:
        return {"from": start, "to": goal, "path": None}
    hops = []
    rel = goal
    while previous[rel] is not None:
        prior, hop = previous[rel]
        hops.append({"from": prior, "to": rel, **hop})
        rel = prior
    return {"from": start, "to": goal, "path": hops[::-1]}


def q_find(index: Index, args) -> dict:
    return {"query": args.ref, "notes": index.find(args.ref)}


def q_hash(index: Index, args) -> dict:
    rel = index.resolve(args.ref)
    entry = index.data["files"].get(rel, {})
    proofs = index.store.fresh_proofs(only={rel}) if index.store is not None else index.data["proofs"]
    proof = proofs.get(rel)
    return {"note": rel, "file_sha256": entry.get("sha"),
            "approval_hash": proof["approval_hash"] if proof else None,
            "scheme": proof["scheme"] if proof else None,
            "proven_unchanged": bool(proof and proof["proven"]),
            "stamped": proof is not None}


def q_changed_since(index: Index, args) -> dict:
    docs = args.docs.absolute()
    def git(*argv: str) -> list:
        out = subprocess.run(["git", "-C", str(docs), *argv], capture_output=True,
                             text=True, check=True).stdout
        return [line for line in out.splitlines() if line]
    if args.ref.startswith("-"):
        raise ValueError(f"changed-since takes a commit, not a Git option: {args.ref!r}")
    try:
        commit = git("rev-parse", "--verify", "--end-of-options", args.ref + "^{commit}")[0]
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"changed-since: {args.ref!r} is no commit") from exc
    paths = set(git("diff", "--name-only", "--relative", commit, "--", "."))
    paths |= set(git("ls-files", "--others", "--exclude-standard", "--", "."))
    stale = sorted(rel for rel, p in index.data["proofs"].items() if not p["proven"])
    return {"ref": args.ref, "changed": sorted(paths), "stale_approved": stale}


def q_gaps(index: Index, args) -> dict:
    if index.store is not None:
        return {"gaps": index.store.gaps_for(reason=args.reason)}
    return {"gaps": [g for g in index.data["gaps"]
                     if args.reason is None or g["reason"] == args.reason]}


def q_search(index: Index, args) -> dict:
    """Every line of an indexed note holding all terms (case-insensitive)."""
    terms = [term.lower() for term in args.query.split()]
    docs = args.docs.absolute()
    hits = []
    for rel, note in sorted(index.data["notes"].items()):
        if not terms:
            break
        lines = (docs / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, start=1):
            lowered = line.lower()
            if all(term in lowered for term in terms):
                hits.append({"path": rel, "id": note["id"] or None, "line": number,
                             "anchor": f"{rel}:{number}", "text": line.strip()})
                if len(hits) >= args.limit:
                    return {"query": args.query, "hits": hits, "truncated": True}
    return {"query": args.query, "hits": hits, "truncated": False}


def q_closure(index: Index, args) -> dict:
    policy = impact_closure.closure_policy(
        vault_check.load_policy(vault_check.DEFAULT_POLICY))
    snap = index.snapshot()
    return impact_closure.closure_from(
        snap, impact_closure.changed_paths(args.docs, args.changed, snap["notes"]), policy)


def q_search_sections(index: Index, args) -> dict:
    # A term without letters or digits tokenizes to nothing and would match no unit.
    terms = [term for term in args.query.split() if any(char.isalnum() for char in term)]
    if not terms:
        return {"query": args.query, "hits": [], "truncated": False}
    expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
    store = index.store
    if store is None:
        raise ValueError("indexed section search requires the SQLite source store")
    rows = store.connection.execute(
        "SELECT m.uid,bm25(search_text) FROM search_text JOIN search_units m ON m.rowid=search_text.rowid "
        "WHERE search_text MATCH ? ORDER BY bm25(search_text),m.uid LIMIT ?",
        (expression, args.limit + 1)).fetchall()
    hits = []
    for uid, score in rows[:args.limit]:
        unit = store.catalog["units"][uid]
        hits.append({"unit_id": uid, "path": unit["path"], "label": unit["label"],
                     "kind": unit["kind"], "source_hash": unit["source_hash"],
                     "bytes": unit["bytes"], "score": score,
                     "historical": bool(unit.get("historical")), "approval_authority": False})
    return {"query": args.query, "hits": hits, "truncated": len(rows) > args.limit}


def query_data(args, cache, *, rebuild=False, repair=False, failed=None):
    try:
        return locked_refresh(args.docs, cache, rebuild=rebuild, repair=repair, failed=failed)
    except OSError as exc:
        if exc.errno not in READ_ONLY_ERRORS:
            raise
        return refresh(args.docs, cache, persist=False, rebuild=rebuild or repair)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, required=True)
    parser.add_argument("--verify", action="store_true",
                        help="accepted for compatibility; every query hashes every eligible source")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("closure")
    p.add_argument("--changed", nargs="*", default=[])
    p.set_defaults(func=q_closure)
    for name, func in (("related", q_related), ("who-cites", q_who_cites),
                       ("find", q_find), ("hash", q_hash)):
        p = sub.add_parser(name)
        p.add_argument("ref")
        p.set_defaults(func=func)
    p = sub.add_parser("path")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=q_path)
    p = sub.add_parser("changed-since")
    p.add_argument("ref")
    p.set_defaults(func=q_changed_since)
    p = sub.add_parser("gaps")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=q_gaps)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=q_search)
    p = sub.add_parser("search-sections")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=q_search_sections)
    p = sub.add_parser("index")
    p.add_argument("action", choices=("ensure", "sync", "rebuild", "status", "check"))
    args = parser.parse_args(argv)
    if not args.docs.is_dir():
        if args.command == "index":
            print(json.dumps({"status": "needs_setup", "sources": 0}))
            return 1
        print(f"vault_query: docs directory not found: {args.docs}", file=sys.stderr)
        return 2
    try:
        cache = default_cache(args.docs)
    except ValueError as exc:
        print(f"vault_query: {exc}", file=sys.stderr)
        return 2
    if args.command == "index" and args.action in {"status", "check"}:
        try:
            result = vault_index.inspect_index(args.docs, cache, builder_hash(), check=args.action == "check")
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "invalid", "reason": str(exc)}))
            return 1
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result["status"] in {"ready", "ready_empty"} else 1
    if hasattr(args, "limit") and args.limit <= 0:
        parser.error("limit must be positive")
    try:
        data, status = query_data(args, cache, rebuild=args.command == "index" and args.action == "rebuild")
    except (OSError, ValueError) as exc:
        print(f"vault_query: {exc}", file=sys.stderr)
        return 2
    try:
        for attempt in range(2):
            try:
                result = ({"status": status["state"], "generation": status["generation"],
                           "documents": len(data["catalog"]["documents"])}
                          if args.command == "index" else args.func(Index(data), args))
                break
            except vault_index.DATABASE_ERRORS as exc:
                failure = vault_index.sqlite_failure(exc)
                if attempt or not isinstance(failure, vault_index.CacheCorruptError):
                    raise failure from exc
                failed = data.store.session.identity
                data.store.close()
                data, status = query_data(args, cache, repair=True, failed=failed)
                status["recovered"] = True
        result["cache"] = status
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (LookupError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"vault_query: {exc}", file=sys.stderr)
        return 1
    finally:
        data.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
