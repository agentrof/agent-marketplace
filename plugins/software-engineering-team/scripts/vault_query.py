#!/usr/bin/env python3
"""Query the docs vault through a disposable cached index.

The index is one human-readable JSON file under the project runtime scratch
(``.agentrof/agent-marketplace/.runtime/vault-index/index.json``). It holds
every relation tier impact_closure reads, the approval proofs and the scanned
notes. Every query hashes the vault's files first: unchanged files are reused,
changed files are rescanned, and the relation graph is recomputed from the
cached notes. Deleting the file loses nothing; the next query rebuilds it.

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

Common options: --docs D (required, the project's ``workspace/docs``),
--verify (hash every file instead of trusting an unchanged size and mtime).
The cache location is fixed and never configurable: the tool writes only
inside that ``vault-index`` folder and never a vault or other project file.
No embeddings, no database; stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import atomic_file
import impact_closure
import vault_check

SCHEMA_VERSION = 1
RUNTIME = Path(".agentrof") / "agent-marketplace" / ".runtime" / "vault-index"
BUILDER_FILES = ("impact_closure.py", "vault_check.py", "vault_query.py", "ba_compile.py")


SHARD_NAME = re.compile(r"^[0-9a-f]{64}\.json$")


def default_cache(docs: Path) -> Path:
    """The one cache file: ``<project>/.agentrof/.../vault-index/index.json``.

    ``docs`` must be a project's ``workspace/docs`` directory, so the cache
    folder sits outside the vault; a folder that resolves elsewhere, through a
    link or otherwise, is refused.
    """
    docs = Path(docs).resolve()
    if docs.name != "docs" or docs.parent.name != "workspace":
        raise ValueError(f"--docs must be a project's workspace/docs directory, not {docs}")
    project = docs.parents[1]
    folder = project / RUNTIME
    resolved = folder.resolve()
    if resolved != folder or resolved.is_relative_to(docs) or not resolved.is_relative_to(
            (project / ".agentrof").resolve()):
        raise ValueError(f"the vault index folder resolves outside the runtime scratch: {resolved}")
    return folder / "index.json"


def builder_hash() -> str:
    """Code identity: a changed reader or policy invalidates the whole cache."""
    digest = hashlib.sha256(str(SCHEMA_VERSION).encode())
    here = Path(__file__).resolve().parent
    for name in BUILDER_FILES:
        digest.update((here / name).read_bytes())
    digest.update(vault_check.DEFAULT_POLICY.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Note (de)serialization as plain JSON data
# ---------------------------------------------------------------------------


def note_to_json(note) -> dict:
    return {
        "fm": note.fm, "fm_end": note.fm_end, "fm_error": note.fm_error,
        "lines": note.lines, "generated": note.generated, "subtree": note.subtree,
        "wikilinks": note.wikilinks, "mdlinks": note.mdlinks,
        "fm_targets": note.fm_targets, "headings": note.headings,
        "block_ids": sorted(note.block_ids)}


def note_from_json(root: Path, rel: str, data: dict):
    return vault_check.Note(
        rel=rel, path=root / rel, fm=data["fm"], fm_end=data["fm_end"],
        fm_error=data["fm_error"], lines=data["lines"], generated=data["generated"],
        subtree=data["subtree"], wikilinks=[tuple(i) for i in data["wikilinks"]],
        mdlinks=[tuple(i) for i in data["mdlinks"]],
        fm_targets=[tuple(i) for i in data["fm_targets"]],
        headings=[tuple(i) for i in data["headings"]], block_ids=set(data["block_ids"]))


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def scan_files(docs: Path, cached: dict, verify: bool = False) -> dict:
    """rel -> {sha, size, mtime_ns}. A file whose size and mtime match the
    cache keeps its cached hash; any other file (or every file, ``verify``)
    is hashed, and the hash alone decides whether it changed."""
    files = {}
    for folder, dirs, names in os.walk(docs):
        base = Path(folder)
        rel_dir = base.relative_to(docs).as_posix()
        if rel_dir == ".":
            dirs[:] = [d for d in dirs if d not in {".trash", ".obsidian"}]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(docs).as_posix()
            info = path.stat()
            entry = cached.get(rel)
            if (not verify and entry and entry.get("size") == info.st_size
                    and entry.get("mtime_ns") == info.st_mtime_ns):
                files[rel] = entry
            else:
                files[rel] = {"sha": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    return files


def shard_dir(cache: Path) -> Path:
    return cache.with_name(cache.stem + "-notes")


def note_identity(note) -> tuple:
    aliases = [a for a in (note.fm.get("aliases") or []) if isinstance(a, str)]
    ident = note.fm.get("id")
    ident = ident if isinstance(ident, str) and ident else (aliases[0] if aliases else "")
    title = note.fm.get("title")
    return ident, title if isinstance(title, str) else "", aliases


def load_cache(cache: Path) -> dict:
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refresh(docs: Path, cache: Path, verify: bool = False) -> tuple[dict, dict]:
    """Bring the cache up to the vault's current bytes: (index, what was redone).

    ``index.json`` holds the graph, proofs and file hashes; each scanned note
    sits beside it in ``index-notes/<sha>.json``, read only to rebuild.
    """
    started = time.perf_counter()
    docs = docs.absolute()
    builder = builder_hash()
    data = load_cache(cache)
    full = data.get("builder") != builder or data.get("docs") != str(docs)
    if full:
        data = {}
    cached = data.get("files", {})
    current = scan_files(docs, cached, verify)
    changed = sorted(rel for rel, entry in current.items()
                     if rel not in cached or cached[rel]["sha"] != entry["sha"])
    removed = sorted(set(cached) - set(current))
    status = {"path": str(cache), "full": full, "changed": changed, "removed": removed}
    if not changed and not removed and not full:
        if current != cached:  # touched, same bytes: keep the stat fast path warm
            data["files"] = dict(sorted(current.items()))
            atomic_file.replace_text(cache, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        status["ms"] = round((time.perf_counter() - started) * 1000, 1)
        return data, status

    shards = shard_dir(cache)
    reuse = {}
    for rel, entry in current.items():
        shard = shards / f"{entry['sha']}.json"
        if rel.endswith(".md") and rel not in changed and shard.is_file():
            try:
                reuse[rel] = note_from_json(docs, rel, json.loads(shard.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                pass  # a damaged shard is rescanned
    vault = impact_closure.load_vault_reusing(docs, reuse)
    authored = vault_check.authored(vault)
    # A proof depends on the note and, for package stamps, on files below its
    # directory: recheck every note whose directory holds a change, and every
    # analysis note when any analysis file changed (one package hash).
    dirty = set(changed) | set(removed)
    dirs = {Path(rel).parent.as_posix() for rel in dirty}
    def touched(rel: str) -> bool:
        base = Path(rel).parent.as_posix()
        return rel in dirty or any(d == base or d.startswith(base + "/") for d in dirs) or (
            rel.startswith("business-analysis/")
            and any(r.startswith("business-analysis/") for r in dirty))
    recheck = None if full else {note.rel for note in authored if touched(note.rel)}
    proofs = {} if full else {rel: proof for rel, proof in data.get("proofs", {}).items()
                              if rel not in recheck and rel not in dirty}
    proofs.update(impact_closure.proofs_for(docs, authored, recheck))
    snap = impact_closure.snapshot(vault, proofs={})
    authored_set = set(snap["notes"])

    shards.mkdir(parents=True, exist_ok=True)
    for rel, note in vault.notes.items():
        shard = shards / f"{current[rel]['sha']}.json"
        if rel not in reuse and not shard.is_file():
            atomic_file.replace_text(shard, json.dumps(note_to_json(note), ensure_ascii=False))
    live = {f"{entry['sha']}.json" for rel, entry in current.items() if rel in vault.notes}
    for shard in shards.iterdir():
        # Only the index's own shards are ever removed.
        if SHARD_NAME.match(shard.name) and shard.is_file() and not shard.is_symlink() \
                and shard.name not in live:
            shard.unlink()
    data = {
        "schema_version": SCHEMA_VERSION,
        "builder": builder,
        "docs": str(docs),
        "tiers": snap["tiers"],
        "notes": {rel: {"id": note_identity(n)[0], "title": note_identity(n)[1],
                        "type": impact_closure.note_type(n), "aliases": note_identity(n)[2],
                        "authored": rel in authored_set}
                  for rel, n in sorted(vault.notes.items())},
        "proofs": dict(sorted(proofs.items())),
        "edges": [[s, t, k, [tier for tier in impact_closure.TIERS if tier in tiers]]
                  for (s, t, k), tiers in sorted(snap["edges"].items())],
        "citers": snap["citers"],
        "gaps": snap["gaps"],
        "files": dict(sorted(current.items())),
    }
    atomic_file.replace_text(cache, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    status["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return data, status


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class Index:
    def __init__(self, data: dict) -> None:
        self.data = data

    def resolve(self, ref: str) -> str:
        rel = impact_closure.normalize(ref)
        for candidate in (rel, f"{rel}.md"):
            if candidate in self.data["notes"]:
                return candidate
        hits = self.find(ref)
        if len(hits) == 1:
            return hits[0]["path"]
        raise LookupError(f"'{ref}' names {len(hits)} notes; use a path"
                          + (": " + ", ".join(h["path"] for h in hits[:10]) if hits else ""))

    def find(self, ref: str) -> list:
        wanted = ref.strip().lower()
        notes = self.data["notes"]
        exact = [rel for rel, n in notes.items()
                 if wanted in {n["id"].lower(), n["title"].lower(),
                               *(a.lower() for a in n["aliases"])}]
        hits = exact or [rel for rel in notes if wanted in rel.lower()]
        return [{"path": rel, **{k: notes[rel][k] for k in ("id", "title", "type", "aliases")}}
                for rel in sorted(hits)]

    def edges(self, field: int, rel: str) -> list:
        return [{"source": s, "target": t, "key": k, "tiers": tiers}
                for s, t, k, tiers in sorted(self.data["edges"], key=lambda e: (e[2], e[0], e[1]))
                if (s, t)[field] == rel]

    def snapshot(self) -> dict:
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
    for s, t, k, tiers in index.data["edges"]:
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
    proof = index.data["proofs"].get(rel)
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
    return impact_closure.closure_from(index.snapshot(), args.changed, policy)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, required=True)
    parser.add_argument("--verify", action="store_true",
                        help="hash every file, ignoring the size and mtime fast path")
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
    args = parser.parse_args(argv)
    if not args.docs.is_dir():
        print(f"vault_query: docs directory not found: {args.docs}", file=sys.stderr)
        return 2
    try:
        cache = default_cache(args.docs)
    except ValueError as exc:
        print(f"vault_query: {exc}", file=sys.stderr)
        return 2
    data, status = refresh(args.docs, cache, args.verify)
    try:
        result = args.func(Index(data), args)
    except (LookupError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"vault_query: {exc}", file=sys.stderr)
        return 1
    result["cache"] = status
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
