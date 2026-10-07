"""Disposable checkout-bound SQLite navigation, using the canonical parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from collections import ChainMap
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import tempfile

import atomic_file
import context_catalog
import file_lock
import impact_closure
import vault_check

SCHEMA_VERSION = 1
POLICY = Path(__file__).resolve().parents[1] / "templates/vault-index-policy.json"
RUNTIME = Path(".agentrof/agent-marketplace/.runtime/vault-index")


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def policy():
    value = json.loads(POLICY.read_text(encoding="utf-8"))
    if value["schema_version"] != 1 or not value["extensions"] \
            or any(not isinstance(ext, str) or not ext.startswith(".") for ext in value["extensions"]):
        raise ValueError("invalid vault index scope policy")
    return value


def capabilities():
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE capability USING fts5(text)")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError("Python requires SQLite with FTS5 support; use a supported Python distribution") from exc
    return {"sqlite_version": sqlite3.sqlite_version, "fts5": True}


def project_docs(project):
    project = Path(project).resolve()
    for relative in ("workspace", "workspace/docs"):
        path = project / relative
        if path.resolve() != path:
            raise ValueError("workspace/docs must stay inside the selected project without directory links")
    return project / "workspace/docs"


def cache_path(docs):
    docs = Path(docs).absolute()
    if docs.name != "docs" or docs.parent.name != "workspace":
        raise ValueError(f"--docs must be a project's workspace/docs directory, not {docs}")
    docs = project_docs(docs.parents[1])
    folder = docs.parents[1] / RUNTIME
    if folder.resolve() != folder:
        raise ValueError(f"the vault index folder resolves outside the runtime scratch: {folder}")
    return folder / "index.db"


def scan_files(docs, cached=None, *, verify=True):
    """Only eligible paths; excluded artifact trees are never traversed."""
    cached = cached or {}
    settings = policy()
    vault_policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    artifact = vault_policy[settings["artifact_directory_policy_key"]]
    result = {}
    def failed(exc):
        raise exc
    for directory, dirs, names in os.walk(docs, followlinks=False, onerror=failed):
        base = Path(directory)
        dirs[:] = [name for name in dirs if name != artifact
                   and not (base == Path(docs) and name in settings["excluded_roots"])]
        for name in dirs:
            if (base / name).is_symlink():
                raise ValueError("indexed directories must not use symbolic links")
        for name in names:
            path = base / name
            if path.suffix.lower() not in settings["extensions"]:
                continue
            if path.is_symlink():
                raise ValueError(f"{path.relative_to(docs).as_posix()} must not use a symbolic link")
            if not path.is_file():
                continue
            info = path.stat()
            relative = path.relative_to(docs).as_posix()
            prior = cached.get(relative)
            if not verify and prior and prior["size"] == info.st_size and prior["mtime_ns"] == info.st_mtime_ns:
                result[relative] = prior
            else:
                result[relative] = {"sha": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    return result


def note_value(note):
    return {"fm": note.fm, "fm_end": note.fm_end, "fm_error": note.fm_error,
            "lines": note.lines, "generated": note.generated, "subtree": note.subtree,
            "wikilinks": note.wikilinks, "mdlinks": note.mdlinks, "fm_targets": note.fm_targets,
            "headings": note.headings, "block_ids": sorted(note.block_ids)}


def restore_note(root, relative, value):
    return vault_check.Note(rel=relative, path=root / relative, fm=value["fm"],
        fm_end=value["fm_end"], fm_error=value["fm_error"], lines=value["lines"],
        generated=value["generated"], subtree=value["subtree"],
        wikilinks=[tuple(row) for row in value["wikilinks"]],
        mdlinks=[tuple(row) for row in value["mdlinks"]],
        fm_targets=[tuple(row) for row in value["fm_targets"]],
        headings=[tuple(row) for row in value["headings"]], block_ids=set(value["block_ids"]))


class ReaderSession:
    def __init__(self, connection, docs):
        self.connection, self.docs = connection, docs
    def __del__(self):
        try:
            self.connection.close()
        except sqlite3.Error:
            pass


class Rows(Mapping):
    def __init__(self, store, table, key):
        self.store, self.table, self.key = getattr(store, "session", store), table, key
    def __getitem__(self, key):
        row = self.store.connection.execute(
            f"SELECT payload FROM {self.table} WHERE {self.key}=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])
    def __iter__(self):
        return (row[0] for row in self.store.connection.execute(f"SELECT {self.key} FROM {self.table} ORDER BY {self.key}"))
    def __len__(self):
        return self.store.connection.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0]
    def __contains__(self, key):
        return self.store.connection.execute(f"SELECT 1 FROM {self.table} WHERE {self.key}=?", (key,)).fetchone() is not None


class Aliases(Mapping):
    def __init__(self, store):
        self.store = getattr(store, "session", store)
    def __getitem__(self, key):
        rows = self.store.connection.execute(
            "SELECT a.uid,u.priority FROM aliases a JOIN units u ON a.uid=u.uid WHERE a.name=? ORDER BY a.uid", (key,)).fetchall()
        if not rows:
            raise KeyError(key)
        priority = max(row[1] for row in rows)
        return [uid for uid, rank in rows if rank == priority]
    def __iter__(self):
        return (row[0] for row in self.store.connection.execute("SELECT DISTINCT name FROM aliases ORDER BY name"))
    def __len__(self):
        return self.store.connection.execute("SELECT count(DISTINCT name) FROM aliases").fetchone()[0]
    def __contains__(self, key):
        return self.store.connection.execute("SELECT 1 FROM aliases WHERE name=? LIMIT 1", (key,)).fetchone() is not None


class OwnerLookup(Mapping):
    def __init__(self, store):
        self.store = getattr(store, "session", store)
    def __getitem__(self, key):
        units = Rows(self.store, "units", "uid")
        if key in units:
            return units[key]["path"]
        aliases = Aliases(self.store)
        if key in aliases:
            ids = aliases[key]
            return units[ids[0]]["path"] if len(ids) == 1 else None
        row = self.store.connection.execute("SELECT path FROM owners WHERE name=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]
    def __iter__(self):
        return iter(set(Aliases(self.store)) | set(Rows(self.store, "units", "uid")) |
                    {r[0] for r in self.store.connection.execute("SELECT name FROM owners")})
    def __len__(self):
        return sum(1 for _key in self)


class Catalog(dict):
    def __init__(self, store):
        super().__init__(documents=Rows(store, "documents", "path"),
                         units=Rows(store, "units", "uid"), aliases=Aliases(store))
        self.owner_lookup = OwnerLookup(store)


class NoteMap(Rows):
    def __init__(self, store, selected=None):
        super().__init__(store, "parsed_notes", "path")
        self.selected = selected
    def __getitem__(self, path):
        return restore_note(self.store.docs, path, super().__getitem__(path))
    def __iter__(self):
        return super().__iter__() if self.selected is None else iter(sorted(self.selected))
    def __len__(self):
        return super().__len__() if self.selected is None else len(self.selected)


class FileSet(Set):
    """Navigation can identify an excluded physical target without indexing it."""
    def __init__(self, store):
        self.store = store
    def __contains__(self, path):
        if not isinstance(path, str):
            return False
        if path in self.store.files:
            return True
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path:
            return False
        candidate = self.store.docs / relative
        return not candidate.is_symlink() and candidate.resolve().is_relative_to(self.store.docs) and candidate.is_file()
    def __iter__(self):
        return iter(self.store.files)
    def __len__(self):
        return len(self.store.files)


class EdgeRows(Sequence):
    def __init__(self, store):
        self.store = getattr(store, "session", store)
    def __iter__(self):
        for s, t, k in self.store.connection.execute("SELECT DISTINCT source,target,key FROM edge_facts ORDER BY source,target,key"):
            tiers = {r[0] for r in self.store.connection.execute(
                "SELECT tier FROM edge_facts WHERE source=? AND target=? AND key=?", (s, t, k))}
            yield [s, t, k, [tier for tier in impact_closure.TIERS if tier in tiers]]
    def __len__(self):
        return self.store.connection.execute("SELECT count(*) FROM (SELECT DISTINCT source,target,key FROM edge_facts)").fetchone()[0]
    def __getitem__(self, key):
        return list(self)[key]
    def __eq__(self, other):
        return list(self) == list(other)


class OwnedEdges(impact_closure.Edges):
    def __init__(self, vault, owner):
        super().__init__(vault)
        self.owner = owner


class Store:
    def __init__(self, docs, connection):
        self.docs, self.connection = Path(docs), connection
        self.session = ReaderSession(connection, self.docs)
        self.catalog = Catalog(self)
        self.files = Rows(self, "files", "path")
    def close(self):
        self.connection.close()
    def meta(self, key, default=None):
        row = self.connection.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set_meta(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, encoded(value)))
    def initialize(self):
        tokenizer = policy()["fts_tokenizer"]
        if tokenizer != "unicode61":
            raise ValueError("unsupported vault index tokenizer")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS parsed_notes(path TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(path TEXT PRIMARY KEY,payload TEXT NOT NULL,id_norm TEXT,title_norm TEXT);
        CREATE INDEX IF NOT EXISTS notes_id ON notes(id_norm);
        CREATE INDEX IF NOT EXISTS notes_title ON notes(title_norm);
        CREATE TABLE IF NOT EXISTS note_names(name TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(name,path));
        CREATE TABLE IF NOT EXISTS documents(path TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS units(uid TEXT PRIMARY KEY,path TEXT NOT NULL,payload TEXT NOT NULL,priority INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS units_path ON units(path);
        CREATE TABLE IF NOT EXISTS aliases(name TEXT NOT NULL,uid TEXT NOT NULL,PRIMARY KEY(name,uid));
        CREATE INDEX IF NOT EXISTS aliases_uid ON aliases(uid);
        CREATE TABLE IF NOT EXISTS owners(name TEXT PRIMARY KEY,path TEXT);
        CREATE TABLE IF NOT EXISTS citations(owner TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(owner,target));
        CREATE INDEX IF NOT EXISTS citations_target ON citations(target,owner);
        CREATE TABLE IF NOT EXISTS owner_flags(path TEXT PRIMARY KEY,body_ready INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS raw_refs(owner TEXT NOT NULL,ref TEXT NOT NULL,PRIMARY KEY(owner,ref));
        CREATE INDEX IF NOT EXISTS raw_refs_ref ON raw_refs(ref);
        CREATE TABLE IF NOT EXISTS edge_facts(owner TEXT NOT NULL,source TEXT NOT NULL,target TEXT NOT NULL,key TEXT NOT NULL,tier TEXT NOT NULL,PRIMARY KEY(owner,source,target,key,tier));
        CREATE INDEX IF NOT EXISTS edges_source ON edge_facts(source,key,target);
        CREATE INDEX IF NOT EXISTS edges_target ON edge_facts(target,key,source);
        CREATE TABLE IF NOT EXISTS gaps(owner TEXT NOT NULL,path TEXT,source TEXT,target TEXT,reason TEXT,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS gaps_path ON gaps(path);
        CREATE INDEX IF NOT EXISTS gaps_source ON gaps(source);
        CREATE INDEX IF NOT EXISTS gaps_target ON gaps(target);
        CREATE TABLE IF NOT EXISTS search_units(uid TEXT PRIMARY KEY,rowid INTEGER NOT NULL UNIQUE);
        """)
        # Modern SQLite can discard duplicate source text while retaining its
        # token index and normal rowid deletion. Older FTS5 builds remain usable.
        contentless = sqlite3.sqlite_version_info >= (3, 43, 0)
        suffix = ",content='',contentless_delete=1" if contentless else ""
        self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search_text USING fts5(title,body,tokenize='unicode61'" + suffix + ")")
    def vault(self, selected=None):
        return vault_check.Vault(root=self.docs,
            policy=vault_check.load_policy(vault_check.DEFAULT_POLICY), index=FileSet(self),
            notes=NoteMap(self, selected))
    def delete_source(self, path):
        ids = [r[0] for r in self.connection.execute("SELECT uid FROM units WHERE path=?", (path,))]
        for uid in ids:
            row = self.connection.execute("SELECT rowid FROM search_units WHERE uid=?", (uid,)).fetchone()
            if row:
                self.connection.execute("DELETE FROM search_text WHERE rowid=?", row)
                self.connection.execute("DELETE FROM search_units WHERE uid=?", (uid,))
            self.connection.execute("DELETE FROM aliases WHERE uid=?", (uid,))
        self.connection.execute("DELETE FROM units WHERE path=?", (path,))
        for table in ("documents", "parsed_notes", "notes", "files"):
            self.connection.execute(f"DELETE FROM {table} WHERE path=?", (path,))
        self.connection.execute("DELETE FROM raw_refs WHERE owner=?", (path,))
        self.connection.execute("DELETE FROM note_names WHERE path=?", (path,))
        self.connection.execute("DELETE FROM citations WHERE owner=?", (path,))
        self.connection.execute("DELETE FROM owner_flags WHERE path=?", (path,))
        self.connection.execute("DELETE FROM edge_facts WHERE owner=?", (path,))
        self.connection.execute("DELETE FROM gaps WHERE owner=?", (path,))
    def catalog_rows(self, records):
        for path, doc in records["documents"].items():
            self.connection.execute("INSERT OR REPLACE INTO documents VALUES(?,?)", (path, encoded(doc)))
        source_bytes = {}
        for uid, unit in records["units"].items():
            priority = 2 if unit.get("historical") else 0 if unit["kind"] == "receipt" else 1
            self.connection.execute("INSERT OR REPLACE INTO units VALUES(?,?,?,?)", (uid, unit["path"], encoded(unit), priority))
            if unit["path"] not in source_bytes:
                source_bytes[unit["path"]] = (self.docs / unit["path"]).read_bytes()
            content = context_catalog.unit_content(self.docs, unit, raw=source_bytes[unit["path"]]).decode("utf-8")
            row = self.connection.execute("INSERT INTO search_text(title,body) VALUES(?,?)", (unit["label"], content))
            self.connection.execute("INSERT OR REPLACE INTO search_units VALUES(?,?)", (uid, row.lastrowid))
        self.connection.executemany("INSERT OR IGNORE INTO aliases VALUES(?,?)",
            ((name, uid) for name, ids in records["aliases"].items() for uid in ids))
    def refs(self, note):
        refs = set()
        for key, value in note.fm.items():
            if key in impact_closure.SELF_KEYS:
                continue
            for text in impact_closure.frontmatter_values(value):
                refs.add(text)
                if text.startswith("[[") and text.endswith("]]"):
                    target, _anchor, label = vault_check.split_wikilink(text[2:-2])
                    refs.update((target, target + ".md", label))
                for endpoint in text.split(" -> "):
                    refs.add(endpoint)
        for _line, _embed, target, _anchor, label, _raw in note.wikilinks:
            refs.update((target, target + ".md", label))
        return {r for r in refs if r}
    def update_note(self, path):
        settings = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        note = vault_check.scan_note(self.docs, self.docs / path, settings["generated_marker_prefix"])
        self.connection.execute("INSERT INTO parsed_notes VALUES(?,?)", (path, encoded(note_value(note))))
        aliases = [a for a in note.fm.get("aliases", []) if isinstance(a, str)]
        ident = note.fm.get("id") or (aliases[0] if aliases else "")
        title = note.fm.get("title", "")
        value = {"id": ident, "title": title, "aliases": aliases,
                 "type": impact_closure.note_type(note), "authored": not note.generated}
        self.connection.execute("INSERT INTO notes VALUES(?,?,?,?)", (path, encoded(value), str(ident).lower(), str(title).lower()))
        self.connection.executemany("INSERT OR IGNORE INTO note_names VALUES(?,?)",
            ((name.lower(), path) for name in [str(ident), str(title), *aliases] if name))
        self.connection.executemany("INSERT OR IGNORE INTO raw_refs VALUES(?,?)", ((path, ref) for ref in self.refs(note)))
        if not note.generated:
            targets = {target + ".md" for _line, _embed, target, _anchor, _label, _raw in note.wikilinks if target}
            targets.update(target + ".md" for _key, target in note.fm_targets if target)
            self.connection.executemany("INSERT OR IGNORE INTO citations VALUES(?,?)",
                ((path, target) for target in targets if target != path))
        return note
    def update_json(self, path, canonical=False):
        records = {"documents": {}, "units": {}, "aliases": {}}
        if canonical:
            context_catalog.add_receipts(self.docs, records, paths=[self.docs / path])
        if not records["documents"]:
            raw = (self.docs / path).read_bytes()
            value = json.loads(raw)
            content = json.dumps(value, sort_keys=True, ensure_ascii=False)
            uid = path + "::json:root"
            records = {"documents": {path: {"path": path, "type": "json-source", "title": path,
                "source_hash": context_catalog.digest(raw), "units": [uid], "references": {}}},
                "units": {uid: {"unit_id": uid, "path": path, "kind": "json", "label": path,
                "json_pointer": [], "source_hash": context_catalog.digest(raw),
                "content_hash": context_catalog.digest(content.encode()), "bytes": len(content.encode())}},
                "aliases": {path: [uid]}}
        self.catalog_rows(records)
        self.connection.execute("INSERT INTO notes VALUES(?,?,?,?)", (path,
            encoded({"id": "", "title": path, "aliases": [], "type": "compiler-record", "authored": True}), "", path.lower()))
    def graph_owner(self, path):
        self.connection.execute("DELETE FROM edge_facts WHERE owner=? AND tier!='text'", (path,))
        self.connection.execute("DELETE FROM gaps WHERE owner=? AND reason='unresolved_relation'", (path,))
        if path not in NoteMap(self):
            return
        vault = self.vault({path})
        edges = OwnedEdges(vault, path)
        keys = set(impact_closure.closure_policy(vault.policy)["relation_keys"])
        unresolved = impact_closure.frontmatter_tier(vault, edges, keys, self.catalog)
        if path == impact_closure.MATRIX:
            impact_closure.index_tier(vault, edges)
        body_ready = impact_closure.body_tier(vault, edges)
        self.connection.execute("INSERT OR REPLACE INTO owner_flags VALUES(?,?)", (path, int(body_ready)))
        impact_closure.navigation_tier(vault, edges)
        self.connection.executemany("INSERT OR IGNORE INTO edge_facts VALUES(?,?,?,?,?)",
            ((path, s, t, key, tier) for (s, t, key), tiers in edges.tiers.items() for tier in tiers))
        self.add_gaps(path, unresolved)
    def add_gaps(self, owner, gaps):
        for gap in gaps:
            gap = dict(gap)
            gap.setdefault("suggested_fix", impact_closure.suggested_fix(gap))
            self.connection.execute("INSERT INTO gaps VALUES(?,?,?,?,?,?)", (owner,
                gap.get("path"), gap.get("source"), gap.get("target"), gap["reason"], encoded(gap)))
    def diagnostics(self):
        self.connection.execute("DELETE FROM edge_facts WHERE tier='text'")
        self.connection.execute("DELETE FROM gaps WHERE reason!='unresolved_relation'")
        vault = self.vault()
        specs = set(vault_check.relation_specs(vault.policy))
        typed = {(s, t, k) for s, t, k in self.connection.execute("SELECT source,target,key FROM edge_facts WHERE tier='frontmatter'") if k in specs}
        matrix = vault.notes.get(impact_closure.MATRIX)
        body = self.connection.execute("SELECT 1 FROM owner_flags WHERE body_ready=1 LIMIT 1").fetchone() is not None
        tiers = {"index": matrix is not None, "frontmatter": True, "body": body,
                 "navigation": True, "text": False}
        for tier, ready in (("index", tiers["index"]), ("body", body)):
            if not ready:
                continue
            other = {(s, t, k) for s, t, k in self.connection.execute("SELECT source,target,key FROM edge_facts WHERE tier=?", (tier,)) if k in specs}
            for s, t, k in sorted(typed - other):
                self.add_gaps(s, [{"path": s, "reason": "tier_disagreement", "key": k, "target": t,
                    "tiers": ["frontmatter", tier], "detail": f"missing from {tier}"}])
            for s, t, k in sorted(other - typed):
                self.add_gaps(s, [{"path": s, "reason": "tier_disagreement", "key": k, "target": t,
                    "tiers": [tier, "frontmatter"], "detail": "missing from frontmatter"}])
        related = {p for s, t in self.connection.execute("SELECT source,target FROM edge_facts WHERE key NOT IN (?,?)",
            (impact_closure.LINK_KEY, impact_closure.LIST_KEY)) for p in (s, t)}
        isolated = []
        for path, payload in self.connection.execute("SELECT path,payload FROM notes ORDER BY path"):
            value = json.loads(payload)
            if path in NoteMap(self) and value["authored"] and path not in related:
                note = vault.notes[path]
                if not impact_closure.is_navigation(vault, note):
                    isolated.append(path)
                    self.add_gaps(path, [{"path": path, "reason": "no_typed_relations"}])
        if isolated:
            edges = impact_closure.Edges(vault)
            impact_closure.text_tier(vault, edges, isolated)
            self.connection.executemany("INSERT OR IGNORE INTO edge_facts VALUES(?,?,?,?,?)",
                ((s, s, t, k, tier) for (s, t, k), values in edges.tiers.items() for tier in values))
            for s, t, _key in sorted(edges.of("text")):
                self.add_gaps(t, [{"path": t, "reason": "text_only_relation", "source": s, "tiers": ["text"]}])
        tiers["text"] = bool(isolated)
        self.set_meta("tiers", tiers)
    def fresh_proofs(self):
        return impact_closure.proofs_for(self.docs, vault_check.authored(self.vault()))
    def find_notes(self, name):
        paths = [row[0] for row in self.connection.execute("SELECT path FROM note_names WHERE name=? ORDER BY path", (name,))]
        if not paths:
            paths = [path for path in Rows(self, "notes", "path") if name in path.lower()]
        return [{"path": path, **{key: Rows(self, "notes", "path")[path][key]
                                 for key in ("id", "title", "type", "aliases")}} for path in paths]
    def edges_for(self, path, field="source"):
        if field not in {"source", "target"}:
            raise ValueError("unknown edge direction")
        grouped = {}
        for s, t, key, tier in self.connection.execute(
                f"SELECT source,target,key,tier FROM edge_facts WHERE {field}=? ORDER BY key,source,target", (path,)):
            grouped.setdefault((s, t, key), set()).add(tier)
        return [[s, t, key, [tier for tier in impact_closure.TIERS if tier in values]]
                for (s, t, key), values in grouped.items()]
    def gaps_for(self, paths=None, reason=None):
        where, args = [], []
        if paths is not None:
            if not paths:
                return []
            marks = ",".join("?" for _ in paths)
            where.append(f"(path IN ({marks}) OR source IN ({marks}) OR target IN ({marks}))")
            args.extend(list(paths) * 3)
        if reason:
            where.append("reason=?")
            args.append(reason)
        sql = "SELECT DISTINCT payload FROM gaps" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY payload"
        return [json.loads(row[0]) for row in self.connection.execute(sql, args)]
    def data(self):
        return IndexData(self)


class IndexData(dict):
    def __init__(self, store):
        self.store = store
        super().__init__(schema_version=SCHEMA_VERSION, builder=store.meta("builder"),
            docs=str(store.docs), catalog=store.catalog, notes=Rows(store, "notes", "path"),
            files=store.files, edges=EdgeRows(store), tiers=store.meta("tiers", {}))
    def __getitem__(self, key):
        if key == "gaps":
            return self.store.gaps_for()
        if key == "proofs":
            return self.store.fresh_proofs()
        if key == "citers":
            result = {}
            for owner, target in self.store.connection.execute("SELECT owner,target FROM citations ORDER BY target,owner"):
                result.setdefault(target, set()).add(owner)
            for s, t, key, _tiers in self["edges"]:
                if key != impact_closure.LIST_KEY:
                    result.setdefault(t, set()).add(s)
            return {p: sorted(values) for p, values in sorted(result.items())}
        return super().__getitem__(key)
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def check_cache_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("vault database, sidecars and lock must be regular unaliased files")


def check_database_files(cache):
    for path in (cache, *(Path(str(cache) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        check_cache_file(path)


def refresh(docs, cache, builder, *, verify=True, persist=True, rebuild=False):
    started = time.perf_counter()
    docs, cache = Path(docs).absolute(), Path(cache)
    if not docs.is_dir():
        raise ValueError("needs_setup: workspace/docs does not exist")
    capabilities()
    if persist:
        check_database_files(cache)
    connection = sqlite3.connect(str(cache) if persist else ":memory:", timeout=policy()["busy_timeout_ms"] / 1000)
    store = Store(docs, connection)
    try:
        if persist:
            connection.execute("PRAGMA journal_mode=WAL")
        store.initialize()
        existing_root = store.meta("docs")
        if existing_root is not None and existing_root != str(docs):
            raise ValueError("vault index belongs to another checkout; rebuild the selected checkout")
        prior_generation = store.meta("generation", 0)
        full = rebuild or store.meta("builder") != builder or store.meta("schema_version") != SCHEMA_VERSION
        cached = {} if full else store.files
        current = scan_files(docs, cached, verify=verify)
        changed = sorted(p for p, entry in current.items() if p not in cached or entry["sha"] != cached[p]["sha"])
        removed = sorted(set(cached) - set(current))
        status = {"path": str(cache), "full": full, "changed": changed, "removed": removed, "persisted": persist}
        if changed or removed or full:
            with connection:
                if full:
                    for table in ("aliases", "units", "documents", "files", "notes", "note_names", "parsed_notes", "owners", "citations", "owner_flags", "raw_refs", "edge_facts", "gaps", "search_units", "search_text", "meta"):
                        connection.execute(f"DELETE FROM {table}")
                affected = set(changed) | set(removed)
                identities = set(affected)
                before_aliases = {}
                for path in affected:
                    identities.add(path.removesuffix(".md"))
                    ids = {r[0] for r in connection.execute("SELECT uid FROM units WHERE path=?", (path,))}
                    identities.update(ids)
                    for uid in ids:
                        identities.update(r[0] for r in connection.execute("SELECT name FROM aliases WHERE uid=?", (uid,)))
                    before_aliases[path] = {(name, uid) for uid in ids for name, in
                        connection.execute("SELECT name FROM aliases WHERE uid=?", (uid,))}
                old_owners = set()
                for ref in identities:
                    old_owners.update(r[0] for r in connection.execute("SELECT owner FROM raw_refs WHERE ref=?", (ref,)))
                for path in affected:
                    store.delete_source(path)
                markdown = set()
                receipts = context_catalog.receipt_paths(docs,
                    candidates=[docs / p for p in changed if p.endswith(".json")])
                for path in changed:
                    connection.execute("INSERT INTO files VALUES(?,?)", (path, encoded(current[path])))
                    if path.endswith(".md"):
                        store.update_note(path)
                        markdown.add(path)
                    elif path.endswith(".json"):
                        store.update_json(path, docs / path in receipts)
                if markdown:
                    store.catalog_rows(context_catalog.catalog(store.vault(markdown), include_receipts=False))
                for path in changed:
                    for uid, in connection.execute("SELECT uid FROM units WHERE path=?", (path,)):
                        identities.add(uid)
                        identities.update(r[0] for r in connection.execute("SELECT name FROM aliases WHERE uid=?", (uid,)))
                namespace_changed = bool(removed)
                for path in changed:
                    now = {(name, uid) for uid, in connection.execute("SELECT uid FROM units WHERE path=?", (path,))
                           for name, in connection.execute("SELECT name FROM aliases WHERE uid=?", (uid,))}
                    namespace_changed = namespace_changed or now != before_aliases.get(path, set())
                # Registry identities are compiler-owned. Reuse parsed notes;
                # only derivation and identity changes require this global namespace.
                if full or any(p.endswith(".json") for p in affected) or namespace_changed:
                    registries = [docs / p for p in current if len(Path(p).parts) == 4
                        and Path(p).parts[0] == "business-analysis"
                        and Path(p).parts[-2:] == ("_generated", "registry.json")]
                    owners = vault_check.relation_identity_owners(store.vault(), registry_paths=registries)
                    connection.execute("DELETE FROM owners")
                    connection.executemany("INSERT INTO owners VALUES(?,?)", owners.items())
                owners_to_update = markdown | old_owners
                for ref in identities:
                    owners_to_update.update(r[0] for r in connection.execute("SELECT owner FROM raw_refs WHERE ref=?", (ref,)))
                if full or any(p.endswith(".json") for p in affected):
                    owners_to_update = set(NoteMap(store))
                for owner in sorted(owners_to_update):
                    store.graph_owner(owner)
                store.diagnostics()
                checked = scan_files(docs, {}, verify=True)
                if {p: v["sha"] for p, v in checked.items()} != {p: v["sha"] for p, v in current.items()}:
                    raise ValueError("source changed during indexing; retry synchronization")
                for path, entry in checked.items():
                    connection.execute("UPDATE files SET payload=? WHERE path=?", (encoded(entry), path))
                store.set_meta("builder", builder)
                store.set_meta("schema_version", SCHEMA_VERSION)
                store.set_meta("docs", str(docs))
                store.set_meta("generation", prior_generation + 1)
                hashes = {p: entry["sha"] for p, entry in checked.items()}
                sources = {p: "sha256:" + hashes[p] for p in Rows(store, "documents", "path")}
                store.set_meta("snapshot_inputs", {"builder": builder, "files": hashes, "sources": sources})
        elif current:
            with connection:
                for path, entry in current.items():
                    if entry != cached[path]:
                        connection.execute("UPDATE files SET payload=? WHERE path=?", (encoded(entry), path))
        connection.execute("BEGIN")
        status.update(ms=round((time.perf_counter() - started) * 1000, 1),
                      generation=store.meta("generation", 0), state="ready" if len(store.catalog["documents"]) else "ready_empty")
        return store.data(), status
    except sqlite3.Error as exc:
        connection.close()
        raise ValueError(f"vault SQLite index is unavailable or corrupt: {exc}") from exc
    except BaseException:
        connection.close()
        raise


def locked_refresh(docs, cache, builder, *, verify=True, rebuild=False):
    project = Path(docs).resolve().parents[1]
    folder = atomic_file.real_directory(project, Path(cache).parent.relative_to(project))
    path = folder / ".lock"
    check_cache_file(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o666)
    try:
        file_lock.lock(descriptor)
        try:
            result = refresh(docs, cache, builder, verify=verify, rebuild=rebuild)
            cleanup_legacy(Path(cache), Path(docs))
            return result
        finally:
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def cleanup_legacy(cache, docs):
    """Remove only an owned previous projection after SQLite publication."""
    legacy = cache.with_name("index.json")
    if not legacy.is_file() or legacy.is_symlink():
        return
    try:
        value = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(value, dict) or value.get("docs") != str(docs.absolute()) or value.get("schema_version") != 2:
        return
    shards = cache.with_name("index-notes")
    if shards.is_symlink():
        raise ValueError("legacy vault shard directory must not be a link")
    if shards.is_dir():
        for path in shards.iterdir():
            if re.fullmatch(r"[a-f0-9]{64}\.json", path.name) and path.is_file() and not path.is_symlink():
                path.unlink()
        if not any(shards.iterdir()):
            shards.rmdir()
    legacy.unlink()


def inspect_index(docs, cache, builder, *, check=False):
    """Copy a consistent closed/checkpointed or WAL snapshot without source writes."""
    docs, cache = Path(docs).absolute(), Path(cache)
    check_database_files(cache)
    if not cache.exists():
        return {"status": "absent", "documents": 0}
    # A shared writer lock prevents publication while copying the cache.
    lock = cache.with_name(".lock")
    descriptor = None
    if lock.exists():
        check_cache_file(lock)
        descriptor = os.open(lock, os.O_RDONLY)
        file_lock.lock(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix="vault-index-check-") as temporary:
            clone = Path(temporary) / "index.db"
            clone.write_bytes(cache.read_bytes())
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(cache) + suffix)
                check_cache_file(sidecar)
                if sidecar.is_file():
                    Path(str(clone) + suffix).write_bytes(sidecar.read_bytes())
            connection = sqlite3.connect(clone)
            try:
                store = Store(docs, connection)
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    return {"status": "corrupt"}
                if store.meta("docs") != str(docs) or store.meta("builder") != builder \
                        or store.meta("schema_version") != SCHEMA_VERSION:
                    return {"status": "incompatible"}
                result = {"status": "ready" if len(store.catalog["documents"]) else "ready_empty",
                          "generation": store.meta("generation"), "documents": len(store.catalog["documents"]),
                          "units": len(store.catalog["units"]), "files": len(store.files), "verified": False}
                if check:
                    actual = scan_files(docs, {}, verify=True)
                    if {p: v["sha"] for p, v in actual.items()} != {p: v["sha"] for p, v in store.files.items()}:
                        result["status"] = "stale"
                    expected = {r[0] for r in connection.execute("SELECT uid FROM units")}
                    found = {r[0] for r in connection.execute("SELECT uid FROM search_units")}
                    search_ids = {r[0] for r in connection.execute("SELECT rowid FROM search_text")}
                    mapped = {r[0] for r in connection.execute("SELECT rowid FROM search_units")}
                    if expected != found or search_ids != mapped:
                        result["status"] = "incomplete"
                    for unit in store.catalog["units"].values():
                        context_catalog.unit_content(docs, unit)
                    result["verified"] = True
                return result
            except sqlite3.Error as exc:
                raise ValueError(f"invalid vault database: {exc}") from exc
            finally:
                connection.close()
    finally:
        if descriptor is not None:
            file_lock.unlock(descriptor)
            os.close(descriptor)
