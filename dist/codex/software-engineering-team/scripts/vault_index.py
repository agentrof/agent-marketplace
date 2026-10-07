"""Disposable checkout-bound SQLite navigation, using the canonical parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
import errno
import hashlib
from itertools import count, zip_longest
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
import tempfile

try:
    import sqlite3
except ImportError:  # a Python built without its optional SQLite extension
    sqlite3 = None

import atomic_file
import context_catalog
import file_lock
import impact_closure
import vault_check

SCHEMA_VERSION = 2
POLICY = Path(__file__).resolve().parents[1] / "templates/vault-index-policy.json"
TOKENIZERS = ("unicode61", "porter unicode61")
DATABASE_ERRORS = (sqlite3.Error,) if sqlite3 else ()
# Three bound lists of this size stay below the oldest SQLite default of 999 variables.
CHUNK = 300
SCHEMA = (
    "CREATE TABLE meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE files(path TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE parsed_notes(path TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE notes(path TEXT PRIMARY KEY,payload TEXT NOT NULL,id_norm TEXT,title_norm TEXT)",
    "CREATE INDEX notes_id ON notes(id_norm)",
    "CREATE INDEX notes_title ON notes(title_norm)",
    "CREATE TABLE note_names(name TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(name,path))",
    "CREATE TABLE documents(path TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE units(uid TEXT PRIMARY KEY,path TEXT NOT NULL,payload TEXT NOT NULL,priority INTEGER NOT NULL,kind TEXT NOT NULL)",
    "CREATE INDEX units_path ON units(path)",
    "CREATE TABLE aliases(name TEXT NOT NULL,uid TEXT NOT NULL,PRIMARY KEY(name,uid))",
    "CREATE INDEX aliases_uid ON aliases(uid)",
    "CREATE TABLE owner_claims(name TEXT NOT NULL,path TEXT NOT NULL,tier INTEGER NOT NULL,source TEXT NOT NULL)",
    "CREATE INDEX owner_claims_name ON owner_claims(name,tier,source)",
    "CREATE INDEX owner_claims_source ON owner_claims(source)",
    "CREATE INDEX owner_claims_path ON owner_claims(path)",
    "CREATE TABLE citations(owner TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(owner,target))",
    "CREATE INDEX citations_target ON citations(target,owner)",
    "CREATE TABLE owner_flags(path TEXT PRIMARY KEY,body_ready INTEGER NOT NULL)",
    "CREATE TABLE raw_refs(owner TEXT NOT NULL,ref TEXT NOT NULL,PRIMARY KEY(owner,ref))",
    "CREATE INDEX raw_refs_ref ON raw_refs(ref)",
    "CREATE TABLE edge_facts(owner TEXT NOT NULL,source TEXT NOT NULL,target TEXT NOT NULL,key TEXT NOT NULL,tier TEXT NOT NULL,PRIMARY KEY(owner,source,target,key,tier))",
    "CREATE INDEX edges_source ON edge_facts(source,key,target)",
    "CREATE INDEX edges_target ON edge_facts(target,key,source)",
    "CREATE TABLE gaps(owner TEXT NOT NULL,path TEXT,source TEXT,target TEXT,reason TEXT,payload TEXT NOT NULL,rank INTEGER NOT NULL,k1 TEXT NOT NULL,k2 INTEGER NOT NULL)",
    "CREATE INDEX gaps_path ON gaps(path)",
    "CREATE INDEX gaps_source ON gaps(source)",
    "CREATE INDEX gaps_target ON gaps(target)",
    "CREATE INDEX gaps_owner ON gaps(owner)",
    "CREATE TABLE probes(owner TEXT NOT NULL,path TEXT NOT NULL,present INTEGER NOT NULL,PRIMARY KEY(owner,path))",
    "CREATE INDEX probes_path ON probes(path)",
    "CREATE TABLE source_errors(path TEXT PRIMARY KEY,reason TEXT NOT NULL)",
    "CREATE TABLE search_units(uid TEXT PRIMARY KEY,rowid INTEGER NOT NULL UNIQUE)",
    "CREATE TABLE lookups(owner TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(owner,name))",
    "CREATE INDEX lookups_name ON lookups(name)",
    "CREATE TABLE stamped(path TEXT PRIMARY KEY)",
)
SEARCH_TABLE = "search_text"
# Probes and lookups are invalidation bookkeeping that may include lookups made through
# another note's content, so they are compared through the edges and gaps they produce.
PROJECTION = ("parsed_notes", "notes", "note_names", "documents", "units", "aliases", "owner_claims",
              "citations", "owner_flags", "raw_refs", "edge_facts", "gaps", "source_errors", "stamped")
TABLES = frozenset(statement.split()[2].split("(")[0] for statement in SCHEMA
                   if statement.startswith("CREATE TABLE")) | {SEARCH_TABLE}
COPY_PREFIX = ".index-copy-"
RECOVERY_PREFIX = ".index-recovery-"
SourceChangedError = context_catalog.SourceChangedError


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def chunks(values):
    values = list(values)
    return [values[start:start + CHUNK] for start in range(0, len(values), CHUNK)]


def policy():
    value = json.loads(POLICY.read_text(encoding="utf-8"))
    if value["schema_version"] != 1 or not value["extensions"] or value.get("fts_tokenizer") not in TOKENIZERS \
            or any(not isinstance(ext, str) or not ext.startswith(".") for ext in value["extensions"]):
        raise ValueError("invalid vault index scope policy")
    return value


def capabilities():
    if sqlite3 is None:
        raise ValueError("this Python lacks the sqlite3 module; use a Python build with SQLite 3 and FTS5")
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE capability USING fts5(text)")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError(f"Python's SQLite {sqlite3.sqlite_version} lacks FTS5; "
                         "use a Python build with SQLite 3 and FTS5") from exc
    return {"sqlite_version": sqlite3.sqlite_version, "fts5": True}


def scope():
    settings = policy()
    artifact = vault_check.load_policy(vault_check.DEFAULT_POLICY)[settings["artifact_directory_policy_key"]]
    return settings, artifact


def eligible(relative, settings=None, artifact=None):
    """The indexed scope: eligible extensions outside excluded roots and every artifact subtree."""
    if settings is None:
        settings, artifact = scope()
    path = PurePosixPath(relative)
    return (bool(path.parts) and path.parts[0] not in settings["excluded_roots"]
            and artifact not in path.parts[:-1] and path.suffix.lower() in settings["extensions"])


contained = context_catalog.contained


def scan_files(docs, *, digest=True):
    """Eligible paths only; excluded trees and directory links are never traversed.

    A link inside the vault to an excluded location stays inventoried and is
    reported unparsed, never silently dropped.
    """
    docs = Path(docs)
    settings, artifact = scope()
    root = docs.resolve()
    info = os.stat(root)
    root_identity = info.st_dev, info.st_ino
    result = {}
    def failed(exc):
        raise exc
    for directory, dirs, names in os.walk(docs, onerror=failed):
        base = Path(directory)
        dirs[:] = [name for name in dirs if name != artifact
                   and not (base == docs and name in settings["excluded_roots"])]
        for name in names:
            path = base / name
            if path.suffix.lower() not in settings["extensions"]:
                continue
            relative = path.relative_to(docs).as_posix()
            excluded = None
            if path.is_symlink():
                # vault_check reads a linked note like any other; the index does
                # the same, but only for a link that stays in its indexed scope.
                target = contained(path.resolve(), root, root_identity)
                if target is None:
                    raise ValueError(f"{relative} links outside the vault")
                if not eligible(target, settings, artifact):
                    excluded = target
            if not path.is_file():
                continue
            info = path.stat()
            entry = {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
            if excluded is not None:
                entry["excluded_link"] = excluded
            if digest:
                entry["sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = entry
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


def source_key(entry):
    return entry["sha"], entry.get("excluded_link")


def excluded_link_target(docs, relative):
    """The excluded location an in-vault link names, as impact_closure needs it, or None."""
    path = Path(docs) / relative
    if not path.is_symlink():
        return None
    root = Path(docs).resolve()
    info = os.stat(root)
    target = contained(path.resolve(), root, (info.st_dev, info.st_ino))
    return target if target is not None and not eligible(target) else None


def stamped(note):
    """Whether impact_closure.proofs_for can prove this authored note from its own stamp."""
    stamp = note.fm.get("source_hash")
    return (isinstance(stamp, str) and bool(stamp)) or (
        note.rel.startswith("system-architecture/") and note.fm.get("revision_state") == "sealed")


def note_identity(note):
    aliases = note.fm.get("aliases")
    aliases = [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else []
    ident = note.fm.get("id")
    ident = ident if isinstance(ident, str) and ident else (aliases[0] if aliases else "")
    title = note.fm.get("title")
    return ident, title if isinstance(title, str) else "", aliases


def ba_registry(relative):
    parts = PurePosixPath(relative).parts
    return len(parts) == 4 and parts[0] == "business-analysis" and parts[2:] == ("_generated", "registry.json")


class CacheCorruptError(ValueError):
    pass


class CacheUnavailableError(ValueError):
    pass


class CacheBusyError(ValueError):
    """Another process holds a lease this operation waited for until its timeout."""


def database_identity(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_dev, info.st_ino


class DatabaseLease:
    """Keep a database inode alive until every library reader has closed it.

    ``.readers`` is shared by readers and taken exclusively by recovery;
    ``.snapshot`` is shared by snapshot copies and taken exclusively by publication.
    """
    BUSY = {(".readers", True): "vault database has active readers; close them and retry synchronization",
            (".snapshot", False): "vault index publication in progress; retry",
            (".snapshot", True): "vault index snapshot copy in progress; retry synchronization"}

    def __init__(self, cache, *, exclusive=False, timeout=None, name=".readers", read_only=False):
        path = Path(cache).with_name(name)
        self.shared = not exclusive
        self.descriptor = None
        timeout = policy()["busy_timeout_ms"] / 1000 if timeout is None else timeout
        deadline = time.monotonic() + timeout
        flags = (os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT) | getattr(os, "O_NOFOLLOW", 0)
        while True:
            check_cache_file(path)
            descriptor = os.open(path, flags, 0o666)
            try:
                info = os.fstat(descriptor)
                if info.st_nlink and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                    raise ValueError("vault reader lock must be a regular unaliased file")
            except BaseException:
                os.close(descriptor)
                raise
            if info.st_nlink:
                break
            # The name was unlinked after it was opened; a lock on that inode guards nothing.
            os.close(descriptor)
            if time.monotonic() >= deadline:
                raise CacheBusyError("vault reader lock was replaced while opening it; retry")
            time.sleep(file_lock.POLL_SECONDS)
        try:
            if not file_lock.acquire(descriptor, shared=self.shared, timeout=max(0, deadline - time.monotonic())):
                raise CacheBusyError(self.BUSY.get((name, exclusive), "vault index lease is busy; retry"))
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor

    def close(self):
        if self.descriptor is None:
            return
        descriptor, self.descriptor = self.descriptor, None
        try:
            file_lock.unlock(descriptor, shared=self.shared)
        finally:
            os.close(descriptor)


class ReaderSession:
    def __init__(self, connection, docs, lease=None, cache=None):
        self.connection, self.docs, self.lease, self.cache = connection, docs, lease, cache
        self.identity = database_identity(cache) if cache is not None else None
        self.closed = False
        # Names, paths and notes one owner's graph consulted, while it is being graphed.
        self.recorded = None
    def record(self, key):
        if self.recorded is not None:
            self.recorded.add(key)
    def close(self):
        if self.closed:
            return
        try:
            self.connection.close()
        finally:
            self.closed = True
            lease, self.lease = self.lease, None
            if lease is not None:
                lease.close()
    def __del__(self):
        try:
            self.close()
        except Exception:
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
        for row in self.store.connection.execute(f"SELECT {self.key} FROM {self.table} ORDER BY {self.key}"):
            yield row[0]
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
        for row in self.store.connection.execute("SELECT DISTINCT name FROM aliases ORDER BY name"):
            yield row[0]
    def __len__(self):
        return self.store.connection.execute("SELECT count(DISTINCT name) FROM aliases").fetchone()[0]
    def __contains__(self, key):
        return self.store.connection.execute("SELECT 1 FROM aliases WHERE name=? LIMIT 1", (key,)).fetchone() is not None


class OwnerLookup(Mapping):
    """impact_closure.reference_owners over verified rows: catalog units and aliases, then claims.

    JSON sources stay addressable for reading but never become graph identities.
    """
    def __init__(self, store, tiers=(0, 1, 2), catalog=True):
        self.store, self.tiers, self.catalog = getattr(store, "session", store), tiers, catalog
    def __getitem__(self, key):
        self.store.record(key)
        connection = self.store.connection
        if self.catalog:
            row = connection.execute("SELECT path FROM units WHERE uid=? AND kind!='json'", (key,)).fetchone()
            if row is not None:
                return row[0]
            rows = connection.execute("SELECT u.priority,u.path FROM aliases a JOIN units u ON a.uid=u.uid "
                                      "WHERE a.name=? AND u.kind!='json'", (key,)).fetchall()
            if rows:
                priority = max(rank for rank, _path in rows)
                paths = [path for rank, path in rows if rank == priority]
                return paths[0] if len(paths) == 1 else None
        marks = ",".join("?" for _ in self.tiers)
        row = connection.execute(f"SELECT path FROM owner_claims WHERE name=? AND tier IN ({marks}) "
                                 "ORDER BY tier,source LIMIT 1", (key, *self.tiers)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]
    def __iter__(self):
        connection = self.store.connection
        marks = ",".join("?" for _ in self.tiers)
        names = {row[0] for row in connection.execute(f"SELECT name FROM owner_claims WHERE tier IN ({marks})", self.tiers)}
        if self.catalog:
            names.update(row[0] for row in connection.execute("SELECT uid FROM units WHERE kind!='json'"))
            names.update(row[0] for row in connection.execute(
                "SELECT a.name FROM aliases a JOIN units u ON a.uid=u.uid WHERE u.kind!='json'"))
        return iter(sorted(names))
    def __len__(self):
        return sum(1 for _key in self)


class Catalog(dict):
    def __init__(self, store):
        super().__init__(documents=Rows(store, "documents", "path"),
                         units=Rows(store, "units", "uid"), aliases=Aliases(store))
        self.owner_lookup = OwnerLookup(store)
        self.relation_owners = OwnerLookup(store, tiers=(0, 1), catalog=False)


class NoteMap(Rows):
    def __init__(self, store, selected=None):
        super().__init__(store, "parsed_notes", "path")
        self.selected = selected
    def __getitem__(self, path):
        self.store.record(path)
        return restore_note(self.store.docs, path, super().__getitem__(path))
    def __contains__(self, path):
        self.store.record(path)
        return super().__contains__(path)
    def __iter__(self):
        return super().__iter__() if self.selected is None else iter(sorted(self.selected))
    def __len__(self):
        return super().__len__() if self.selected is None else len(self.selected)


class NoteRows(Rows):
    """Markdown notes plus the machine records edges cite, as impact_closure.snapshot lists nodes."""
    UNION = "SELECT path FROM notes UNION SELECT target FROM edge_facts WHERE target GLOB '*.json'"
    def __init__(self, store):
        super().__init__(store, "notes", "path")
    def record(self, path):
        return self.store.connection.execute(
            "SELECT 1 FROM edge_facts WHERE target=? AND target GLOB '*.json' LIMIT 1", (path,)).fetchone() is not None
    def __getitem__(self, path):
        try:
            return super().__getitem__(path)
        except KeyError:
            if not self.record(path):
                raise
            return {"id": "", "title": path, "type": "compiler-record", "aliases": [], "authored": True}
    def __iter__(self):
        for row in self.store.connection.execute(self.UNION + " ORDER BY 1"):
            yield row[0]
    def __len__(self):
        return self.store.connection.execute(f"SELECT count(*) FROM ({self.UNION})").fetchone()[0]
    def __contains__(self, path):
        return super().__contains__(path) or self.record(path)


class FileSet(Set):
    """vault.index membership: inventoried sources, or an exact-case physical file outside the scope."""
    def __init__(self, store):
        self.store, self.probes, self.listings = store, None, {}
    def __contains__(self, path):
        if not isinstance(path, str):
            return False
        self.store.session.record(path)
        if path in self.store.files:
            return True
        present = self.present(path)
        if self.probes is not None:
            self.probes[path] = present
        return present
    def present(self, path):
        parts = path.split("/")
        if "\\" in path or parts[0] == ".trash" or any(part in {"", ".", ".."} for part in parts):
            return False
        candidate = self.store.docs
        for part in parts:
            if part not in self.listing(candidate):
                return False
            candidate = candidate / part
            if candidate.is_symlink():
                return False
        return candidate.is_file()
    def listing(self, directory):
        if directory not in self.listings:
            try:
                self.listings[directory] = set(os.listdir(directory))
            except OSError:
                self.listings[directory] = set()
        return self.listings[directory]
    def __iter__(self):
        return iter(self.store.files)
    def __len__(self):
        return len(self.store.files)


def grouped_edges(rows):
    grouped = {}
    for source, target, key, tier in rows:
        grouped.setdefault((source, target, key), set()).add(tier)
    return [[s, t, k, [tier for tier in impact_closure.TIERS if tier in tiers]]
            for (s, t, k), tiers in grouped.items()]


class EdgeRows(Sequence):
    def __init__(self, store):
        self.store = getattr(store, "session", store)
    def rows(self):
        return grouped_edges(self.store.connection.execute(
            "SELECT source,target,key,tier FROM edge_facts ORDER BY source,target,key"))
    def __iter__(self):
        return iter(self.rows())
    def __len__(self):
        return self.store.connection.execute("SELECT count(*) FROM (SELECT DISTINCT source,target,key FROM edge_facts)").fetchone()[0]
    def __getitem__(self, key):
        return self.rows()[key]
    def __eq__(self, other):
        return list(self) == list(other)


class OwnedEdges(impact_closure.Edges):
    def __init__(self, vault, owner):
        super().__init__(vault)
        self.owner = owner


class Store:
    def __init__(self, docs, connection, lease=None, cache=None):
        self.docs, self.connection = Path(docs), connection
        self.session = ReaderSession(connection, self.docs, lease, cache)
        self.catalog = Catalog(self)
        self.files = Rows(self, "files", "path")
        self.loaded_policy = None
    def close(self):
        self.session.close()
    @property
    def vault_policy(self):
        if self.loaded_policy is None:
            self.loaded_policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        return self.loaded_policy
    def meta(self, key, default=None):
        row = self.connection.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set_meta(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, encoded(value)))
    def binding(self):
        """The stored checkout, derivation and schema, or None for a database this code cannot read."""
        try:
            rows = self.connection.execute(
                "SELECT key,payload FROM meta WHERE key IN ('docs','builder','schema_version')").fetchall()
        except sqlite3.Error as exc:
            failure = sqlite_failure(exc)
            if isinstance(failure, (CacheCorruptError, PermissionError)):
                raise failure from exc
            return None
        return {key: json.loads(payload) for key, payload in rows}
    def generation(self):
        try:
            return self.meta("generation", 0)
        except sqlite3.Error:
            return 0
    def layout_supported(self):
        """Whether every derived table exists and this SQLite can open the stored full-text table."""
        try:
            names = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not TABLES <= names:
                return False
            self.connection.execute(f"SELECT rowid FROM {SEARCH_TABLE} LIMIT 0").fetchall()
        except sqlite3.Error:
            return False
        return True
    def initialize(self):
        tokenizer = policy()["fts_tokenizer"]
        for statement in SCHEMA:
            self.connection.execute(statement)
        # A table that keeps its text deletes each row's exact token statistics,
        # so ranking after any edit history equals a fresh compilation.
        self.connection.execute(f"CREATE VIRTUAL TABLE {SEARCH_TABLE} USING fts5(title,body,tokenize='{tokenizer}')")
    def reset(self):
        """Replace every derived table in the open transaction, whatever schema the file had."""
        try:
            tables = self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
            for virtual in (True, False):
                for name, sql in tables:
                    if name.startswith("sqlite_") or (sql or "").upper().startswith("CREATE VIRTUAL") != virtual:
                        continue
                    self.connection.execute('DROP TABLE IF EXISTS "' + name.replace('"', '""') + '"')
            self.initialize()
        except sqlite3.Error as exc:
            failure = sqlite_failure(exc)
            if isinstance(failure, PermissionError):
                raise failure from exc
            raise CacheCorruptError(f"vault SQLite index needs a replacement file: {exc}") from exc
    def vault(self, selected=None):
        return vault_check.Vault(root=self.docs, policy=self.vault_policy, index=FileSet(self),
                                 notes=NoteMap(self, selected))
    def file_entries(self):
        return {path: json.loads(payload) for path, payload in self.connection.execute("SELECT path,payload FROM files")}
    def verified(self, path):
        raw = (self.docs / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.files[path]["sha"]:
            raise SourceChangedError(f"source changed while indexing: {path}")
        return raw
    def overlay(self, path, raw):
        """A file view that serves the verified bytes; a linked note binds them to its target."""
        view = vault_check.VaultFileView(self.docs)
        source = self.docs / path
        if source.is_symlink():
            # Another spelling of the vault root binds the target by file identity; the
            # catalog's source hash check still refuses bytes read past this view.
            info = os.stat(view.resolved_root)
            target = contained(source.resolve(), view.resolved_root, (info.st_dev, info.st_ino))
            source = view.root / target if target is not None else source.resolve()
        view.put(source, raw)
        return view
    def identities(self, path):
        """Every name whose resolution a change to this source can alter."""
        names = {path, path.removesuffix(".md")}
        uids = [row[0] for row in self.connection.execute("SELECT uid FROM units WHERE path=?", (path,))]
        names.update(uids)
        for chunk in chunks(uids):
            marks = ",".join("?" for _ in chunk)
            names.update(row[0] for row in self.connection.execute(f"SELECT name FROM aliases WHERE uid IN ({marks})", chunk))
        names.update(row[0] for row in self.connection.execute(
            "SELECT name FROM owner_claims WHERE source=? OR path=?", (path, path)))
        return names
    def citing(self, names):
        """Owners whose own references, or whose recorded graph lookups, name any of ``names``."""
        owners = set()
        for chunk in chunks(sorted(names)):
            marks = ",".join("?" for _ in chunk)
            owners.update(row[0] for row in self.connection.execute(
                f"SELECT owner FROM raw_refs WHERE ref IN ({marks}) "
                f"UNION SELECT owner FROM lookups WHERE name IN ({marks})", chunk * 2))
        return owners
    def flipped_probes(self):
        """Owners whose excluded link targets appeared or disappeared since they were graphed."""
        files = FileSet(self)
        owners = set()
        for path, present in self.connection.execute("SELECT DISTINCT path,present FROM probes").fetchall():
            if files.present(path) != bool(present):
                owners.update(row[0] for row in self.connection.execute("SELECT owner FROM probes WHERE path=?", (path,)))
        return owners
    def delete_source(self, path):
        ids = [r[0] for r in self.connection.execute("SELECT uid FROM units WHERE path=?", (path,))]
        for uid in ids:
            row = self.connection.execute("SELECT rowid FROM search_units WHERE uid=?", (uid,)).fetchone()
            if row:
                self.connection.execute(f"DELETE FROM {SEARCH_TABLE} WHERE rowid=?", row)
                self.connection.execute("DELETE FROM search_units WHERE uid=?", (uid,))
            self.connection.execute("DELETE FROM aliases WHERE uid=?", (uid,))
        self.connection.execute("DELETE FROM units WHERE path=?", (path,))
        for table in ("documents", "parsed_notes", "notes", "files", "note_names", "owner_flags", "source_errors",
                      "stamped"):
            self.connection.execute(f"DELETE FROM {table} WHERE path=?", (path,))
        for table in ("raw_refs", "citations", "edge_facts", "gaps", "probes", "lookups"):
            self.connection.execute(f"DELETE FROM {table} WHERE owner=?", (path,))
        self.connection.execute("DELETE FROM owner_claims WHERE source=?", (path,))
    def catalog_rows(self, records):
        for path, doc in records["documents"].items():
            if doc["source_hash"] != "sha256:" + self.files[path]["sha"]:
                raise SourceChangedError(f"source changed while indexing: {path}")
            self.connection.execute("INSERT OR REPLACE INTO documents VALUES(?,?)", (path, encoded(doc)))
        sources = {}
        for uid, unit in records["units"].items():
            priority = 2 if unit.get("historical") else 0 if unit["kind"] == "receipt" else 1
            self.connection.execute("INSERT OR REPLACE INTO units VALUES(?,?,?,?,?)",
                                    (uid, unit["path"], encoded(unit), priority, unit["kind"]))
            content = context_catalog.unit_content(self.docs, unit, sources=sources).decode("utf-8")
            row = self.connection.execute(f"INSERT INTO {SEARCH_TABLE}(title,body) VALUES(?,?)", (unit["label"], content))
            self.connection.execute("INSERT OR REPLACE INTO search_units VALUES(?,?)", (uid, row.lastrowid))
        self.connection.executemany("INSERT OR IGNORE INTO aliases VALUES(?,?)",
            ((name, uid) for name, ids in records["aliases"].items() for uid in ids))
    def unparsed(self, path, raw, reason):
        """An eligible source without addresses stays inventoried and reported, never silently empty."""
        document = {"path": path, "type": "source", "title": path, "source_hash": context_catalog.digest(raw),
                    "units": [], "references": {}, "unparsed": reason}
        self.catalog_rows({"documents": {path: document}, "units": {}, "aliases": {}})
        self.connection.execute("INSERT INTO source_errors VALUES(?,?)", (path, reason))
    def unparsed_paths(self):
        return [{"path": path, "reason": reason}
                for path, reason in self.connection.execute("SELECT path,reason FROM source_errors ORDER BY path")]
    def refs(self, note):
        refs = set()
        for key, value in note.fm.items():
            if key in impact_closure.SELF_KEYS:
                continue
            for text in impact_closure.frontmatter_values(value):
                text = text.strip()
                refs.add(text)
                binding = text.split("|")
                if len(binding) == 3 and binding[2].startswith("sha256:") and not text.startswith("[["):
                    text = binding[1].strip()
                    refs.add(text)
                if text.startswith("[[") and text.endswith("]]"):
                    target, _anchor, label = vault_check.split_wikilink(text[2:-2])
                    refs.update((target, target + ".md", label))
                for endpoint in text.split(" -> "):
                    normalized = impact_closure.normalize(endpoint.strip().lstrip("./"))
                    refs.update((endpoint.strip(), normalized, normalized + ".md"))
        for _line, _embed, target, _anchor, label, _raw in note.wikilinks:
            refs.update((target, target + ".md", label))
        return {r for r in refs if r}
    def update_note(self, path):
        raw = self.verified(path)
        view = self.overlay(path, raw)
        note = vault_check.scan_note(self.docs, self.docs / path, self.vault_policy["generated_marker_prefix"], files=view)
        self.connection.execute("INSERT INTO parsed_notes VALUES(?,?)", (path, encoded(note_value(note))))
        ident, title, aliases = note_identity(note)
        value = {"id": ident, "title": title, "aliases": aliases,
                 "type": impact_closure.note_type(note), "authored": not note.generated}
        self.connection.execute("INSERT INTO notes VALUES(?,?,?,?)", (path, encoded(value), ident.lower(), title.lower()))
        self.connection.executemany("INSERT OR IGNORE INTO note_names VALUES(?,?)",
            ((name.lower(), path) for name in (ident, title, *aliases) if name))
        self.connection.executemany("INSERT OR IGNORE INTO raw_refs VALUES(?,?)", ((path, ref) for ref in self.refs(note)))
        # Like vault.inbound, every note's links name citable targets; readers keep authored citers.
        targets = {target + ".md" for _line, _embed, target, _anchor, _label, _raw in note.wikilinks if target}
        targets.update(target + ".md" for _key, target in note.fm_targets if target)
        self.connection.executemany("INSERT OR IGNORE INTO citations VALUES(?,?)", ((path, target) for target in targets))
        if not note.generated and stamped(note):
            self.connection.execute("INSERT INTO stamped VALUES(?)", (path,))
        if not note.generated:
            # Relation identities, then reference_owners' fallbacks; the first claim wins in rel order.
            single = vault_check.Vault(root=self.docs, policy=self.vault_policy, notes={path: note})
            claims = [(name, owner, 0) for name, owner in
                      vault_check.relation_identity_owners(single, registry_paths=()).items()]
            claims += [(name, path, 2) for name in impact_closure.note_reference_names(note)]
            self.connection.executemany("INSERT INTO owner_claims VALUES(?,?,?,?)",
                ((name, owner, tier, path) for name, owner, tier in claims))
        return note
    def update_json(self, path, canonical=False):
        raw = self.verified(path)
        records = {"documents": {}, "units": {}, "aliases": {}}
        if canonical:
            try:
                context_catalog.add_receipts(self.docs, records, paths=[self.docs / path])
            except ValueError as exc:
                raise ValueError(f"cannot index receipt {path}: {exc}") from exc
        if not records["documents"]:
            try:
                content = json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                self.unparsed(path, raw, f"invalid JSON: {str(exc)[:200]}")
                return
            uid = path + "::json:root"
            records = {"documents": {path: {"path": path, "type": "json-source", "title": path,
                "source_hash": context_catalog.digest(raw), "units": [uid], "references": {}}},
                "units": {uid: {"unit_id": uid, "path": path, "kind": "json", "label": path,
                "json_pointer": [], "source_hash": context_catalog.digest(raw),
                "content_hash": context_catalog.digest(content), "bytes": len(content)}},
                "aliases": {path: [uid]}}
        self.catalog_rows(records)
        if ba_registry(path):
            registry = vault_check.Vault(root=self.docs, policy=self.vault_policy, files=self.overlay(path, raw))
            try:
                owners = vault_check.relation_identity_owners(registry, registry_paths=[self.docs / path])
            except UnicodeError:
                owners = {}
            self.connection.executemany("INSERT INTO owner_claims VALUES(?,?,?,?)",
                ((name, owner, 1, path) for name, owner in owners.items()))
    def graph_owner(self, path):
        self.connection.execute("DELETE FROM edge_facts WHERE owner=? AND tier!='text'", (path,))
        self.connection.execute("DELETE FROM gaps WHERE owner=? AND reason='unresolved_relation'", (path,))
        self.connection.execute("DELETE FROM probes WHERE owner=?", (path,))
        self.connection.execute("DELETE FROM lookups WHERE owner=?", (path,))
        if path not in NoteMap(self):
            return
        vault = self.vault({path})
        vault.index.probes = {}
        edges = OwnedEdges(vault, path)
        keys = set(impact_closure.closure_policy(vault.policy)["relation_keys"])
        # Every name, path and note this graph consults, including through another
        # note's content (a dependency endpoint's depends_on), re-graphs it on change.
        self.session.recorded = set()
        try:
            unresolved = impact_closure.frontmatter_tier(vault, edges, keys, self.catalog)
            if path == impact_closure.MATRIX:
                impact_closure.index_tier(vault, edges)
            body_ready = impact_closure.body_tier(vault, edges)
            impact_closure.navigation_tier(vault, edges)
            recorded = self.session.recorded - {path}
        finally:
            self.session.recorded = None
        self.connection.execute("INSERT OR REPLACE INTO owner_flags VALUES(?,?)", (path, int(body_ready)))
        self.connection.executemany("INSERT OR IGNORE INTO lookups VALUES(?,?)", ((path, name) for name in recorded))
        self.connection.executemany("INSERT OR IGNORE INTO edge_facts VALUES(?,?,?,?,?)",
            ((path, s, t, key, tier) for (s, t, key), tiers in edges.tiers.items() for tier in tiers))
        self.add_gaps(path, unresolved, 0, path)
        self.connection.executemany("INSERT INTO probes VALUES(?,?,?)",
            ((path, probe, int(present)) for probe, present in vault.index.probes.items()))
    def add_gaps(self, owner, gaps, rank, k1="", sequence=None):
        """Rows keep impact_closure.graph's emission order: reason group, then source order."""
        for position, gap in enumerate(gaps):
            gap = dict(gap)
            gap.setdefault("suggested_fix", impact_closure.suggested_fix(gap))
            self.connection.execute("INSERT INTO gaps VALUES(?,?,?,?,?,?,?,?,?)", (owner,
                gap.get("path"), gap.get("source"), gap.get("target"), gap["reason"], encoded(gap),
                rank, k1, position if sequence is None else next(sequence)))
    def diagnostics(self, touched=None):
        """Cross-tier gaps; text mentions are rescanned only for changed sources or identifier maps."""
        self.connection.execute("DELETE FROM gaps WHERE reason!='unresolved_relation'")
        vault = self.vault()
        specs = set(vault_check.relation_specs(vault.policy))
        typed = {(s, t, k) for s, t, k in self.connection.execute(
            "SELECT DISTINCT source,target,key FROM edge_facts WHERE tier='frontmatter'") if k in specs}
        matrix = impact_closure.MATRIX in vault.notes
        body = self.connection.execute("SELECT 1 FROM owner_flags WHERE body_ready=1 LIMIT 1").fetchone() is not None
        tiers = {"index": matrix, "frontmatter": True, "body": body, "navigation": True, "text": False}
        sequence = count()
        for tier, ready in (("index", matrix), ("body", body)):
            if not ready:
                continue
            other = {(s, t, k) for s, t, k in self.connection.execute(
                "SELECT DISTINCT source,target,key FROM edge_facts WHERE tier=?", (tier,)) if k in specs}
            for s, t, k in sorted(typed - other):
                self.add_gaps(s, [{"path": s, "reason": "tier_disagreement", "key": k, "target": t,
                    "tiers": ["frontmatter", tier], "detail": f"missing from {tier}"}], 1, sequence=sequence)
            for s, t, k in sorted(other - typed):
                self.add_gaps(s, [{"path": s, "reason": "tier_disagreement", "key": k, "target": t,
                    "tiers": [tier, "frontmatter"], "detail": "missing from frontmatter"}], 1, sequence=sequence)
        related = {p for s, t in self.connection.execute(
            "SELECT source,target FROM edge_facts WHERE tier!='text' AND key NOT IN (?,?)",
            (impact_closure.LINK_KEY, impact_closure.LIST_KEY)) for p in (s, t)}
        isolated = []
        for path, payload in self.connection.execute("SELECT path,payload FROM notes ORDER BY path").fetchall():
            if path not in related and json.loads(payload)["authored"] \
                    and not impact_closure.is_navigation(vault, vault.notes[path]):
                isolated.append(path)
        wanted = {}
        for path in isolated:
            for ident in impact_closure.identifiers(vault.notes[path]):
                wanted.setdefault(ident, path)
        # Unchanged sources keep their mentions while the identifier map is unchanged;
        # a changed map can expose or hide overlapping matches in any source.
        sources = None
        if touched is not None and wanted == self.meta("text_targets"):
            sources = {path for path in touched if path in vault.notes}
        else:
            self.connection.execute("DELETE FROM edge_facts WHERE tier='text'")
        if isolated and (sources is None or sources):
            edges = impact_closure.Edges(vault)
            impact_closure.text_tier(vault if sources is None else self.vault(sources), edges, isolated)
            self.connection.executemany("INSERT OR IGNORE INTO edge_facts VALUES(?,?,?,?,?)",
                ((s, s, t, k, tier) for (s, t, k), values in edges.tiers.items() for tier in values))
        self.set_meta("text_targets", wanted)
        for path in isolated:
            self.add_gaps(path, [{"path": path, "reason": "no_typed_relations"}], 2, path)
        sequence = count()
        for s, t, _key in self.connection.execute(
                "SELECT DISTINCT source,target,key FROM edge_facts WHERE tier='text' ORDER BY source,target,key").fetchall():
            self.add_gaps(t, [{"path": t, "reason": "text_only_relation", "source": s, "tiers": ["text"]}], 3,
                          sequence=sequence)
        tiers["text"] = bool(isolated)
        self.set_meta("tiers", tiers)
    def fresh_proofs(self, only=None):
        """impact_closure.proofs_for over the stamped authored notes only; ``only`` limits the paths."""
        paths = [row[0] for row in self.connection.execute("SELECT path FROM stamped ORDER BY path")]
        if only is not None:
            paths = [path for path in paths if path in only]
        notes = NoteMap(self, set(paths))
        return impact_closure.proofs_for(self.docs, [notes[path] for path in paths], only=only)
    def closure_snapshot(self):
        """vault_query.Index.snapshot from one grouped edge read; proofs only for stamped notes."""
        edges = grouped_edges(self.connection.execute(
            "SELECT source,target,key,tier FROM edge_facts ORDER BY source,target,key"))
        authored, nodes = set(), {}
        for path, payload in self.connection.execute("SELECT path,payload FROM notes ORDER BY path"):
            value = json.loads(payload)
            if value["authored"]:
                authored.add(path)
                nodes[path] = value["type"]
        notes = {row[0] for row in self.connection.execute("SELECT path FROM notes")}
        for _source, target, _key, _tiers in edges:
            if target.endswith(".json") and target not in notes:
                nodes[target] = "compiler-record"
        found = {}
        for owner, target in self.connection.execute("SELECT owner,target FROM citations"):
            found.setdefault(target, set()).add(owner)
        for source, target, key, _tiers in edges:
            if key != impact_closure.LIST_KEY:
                found.setdefault(target, set()).add(source)
        citers = {path: sorted(source for source in values if source in authored and source != path)
                  for path, values in sorted(found.items())}
        return {"notes": dict(sorted(nodes.items())), "citers": citers,
                "edges": {(s, t, k): set(tiers) for s, t, k, tiers in edges},
                "gaps": self.gaps_for(), "tiers": self.meta("tiers", {}), "proofs": self.fresh_proofs()}
    def find_notes(self, name):
        notes = NoteRows(self)
        paths = [row[0] for row in self.connection.execute("SELECT path FROM note_names WHERE name=? ORDER BY path", (name,))]
        if not paths:
            paths = [path for path in notes if name in path.lower()]
        return [{"path": path, **{key: notes[path][key] for key in ("id", "title", "type", "aliases")}}
                for path in paths]
    def edges_for(self, path, field="source"):
        if field not in {"source", "target"}:
            raise ValueError("unknown edge direction")
        return grouped_edges(self.connection.execute(
            f"SELECT source,target,key,tier FROM edge_facts WHERE {field}=? ORDER BY key,source,target", (path,)))
    def edges_touching(self, path):
        return grouped_edges(self.connection.execute(
            "SELECT source,target,key,tier FROM edge_facts WHERE source=? OR target=? ORDER BY source,target,key",
            (path, path)))
    def gaps_for(self, paths=None, reason=None):
        where, args = [], []
        if reason:
            where.append("reason=?")
            args.append(reason)
        if paths is None:
            groups = [None]
        else:
            groups = chunks(sorted(paths))
            if not groups:
                return []
        rows = {}
        for group in groups:
            clauses, values = list(where), list(args)
            if group is not None:
                marks = ",".join("?" for _ in group)
                clauses.append(f"(path IN ({marks}) OR source IN ({marks}) OR target IN ({marks}))")
                values.extend(group * 3)
            sql = "SELECT rowid,rank,k1,k2,payload FROM gaps" + (" WHERE " + " AND ".join(clauses) if clauses else "")
            for rowid, rank, k1, k2, payload in self.connection.execute(sql, values):
                rows[rowid] = (rank, k1, k2, rowid, payload)
        return [json.loads(row[4]) for row in sorted(rows.values())]
    def data(self):
        return IndexData(self)


class IndexData(dict):
    # Every refresh hashes the eligible inventory before serving it.
    verified_inventory = True

    def __init__(self, store):
        self.store = store
        super().__init__(schema_version=SCHEMA_VERSION, builder=store.meta("builder"),
            docs=str(store.docs), catalog=store.catalog, notes=NoteRows(store),
            files=store.files, edges=EdgeRows(store), tiers=store.meta("tiers", {}))
    def __getitem__(self, key):
        if key == "gaps":
            return self.store.gaps_for()
        if key == "proofs":
            return self.store.fresh_proofs()
        if key == "citers":
            return self.store.closure_snapshot()["citers"]
        return super().__getitem__(key)
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def check_cache_file(path):
    """Refuse a cache file that is not regular or has another name; one being unlinked counts as absent."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if info.st_nlink == 0:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("vault database, sidecars and lock must be regular unaliased files")


def check_database_files(cache):
    for path in (cache, *(Path(str(cache) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        check_cache_file(path)


def sqlite_failure(exc):
    code = getattr(exc, "sqlite_errorcode", 0) & 0xff
    message = str(exc).lower()
    if code in (8, 14) or "readonly database" in message or "unable to open database file" in message:
        return PermissionError(errno.EACCES, f"vault SQLite cache cannot be written: {exc}")
    if code in (11, 26) or "not a database" in message or "database disk image is malformed" in message:
        return CacheCorruptError(f"vault SQLite index is corrupt: {exc}")
    return CacheUnavailableError(f"vault SQLite index is unavailable: {exc}")


def bound(binding, docs, builder):
    """Whether a stored binding names this checkout, derivation and schema.

    Another spelling of the same docs directory, such as a different case on a
    case-insensitive volume, names the same checkout.
    """
    if not binding or binding.get("builder") != builder or binding.get("schema_version") != SCHEMA_VERSION:
        return False
    stored = binding.get("docs")
    if stored == str(docs):
        return True
    try:
        return isinstance(stored, str) and os.path.samefile(stored, docs)
    except OSError:
        return False


def published_uri(cache):
    return Path(cache).absolute().as_uri() + "?mode=ro"


def open_published(cache):
    """``((connection, lease), None)`` reading the published cache in place, or ``(None, reason)``.

    The read-only connection holds one read transaction, so it keeps serving the
    generation it opened while writers publish later ones.
    """
    cache = Path(cache)
    try:
        check_database_files(cache)
        if not cache.is_file():
            return None, None
    except (OSError, ValueError) as exc:
        return None, f"published cache refused: {exc}"
    lease = connection = None
    try:
        if cache.with_name(".readers").exists():
            lease = DatabaseLease(cache, read_only=True)
        connection = sqlite3.connect(published_uri(cache), uri=True, check_same_thread=False,
                                     timeout=policy()["busy_timeout_ms"] / 1000)
        connection.execute("BEGIN")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except (OSError, ValueError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        if lease is not None:
            lease.close()
        return None, f"published cache cannot be read in place: {exc}"
    return (connection, lease), None


def refresh(docs, cache, builder, *, persist=True, rebuild=False, coordinate=True, generation=0, current=None):
    """Reconcile sources into the cache; without writes, read it in place or reconcile it in memory."""
    docs, cache = Path(docs).resolve(), Path(cache)
    if not docs.is_dir():
        raise ValueError("needs_setup: workspace/docs does not exist")
    capabilities()
    fallback = None
    if not persist and not rebuild:
        published, fallback = open_published(cache)
        if published is not None:
            try:
                return compile_index(docs, cache, builder, published=published, persist=False, rebuild=False,
                                     coordinate=False, generation=generation, current=current)
            except (CacheCorruptError, CacheUnavailableError) as exc:
                fallback = f"published cache cannot be reconciled: {exc}"
    data, status = compile_index(docs, cache, builder, published=None, persist=persist, rebuild=rebuild,
                                 coordinate=coordinate, generation=generation, current=current)
    if fallback:
        # The sources answer correctly; the status names why the published cache was not used.
        status["fallback"] = fallback
    return data, status


def publish(connection, cache):
    """Commit, with the checkpoint it may run, while snapshot copies of ``cache`` are paused."""
    guard = DatabaseLease(cache, exclusive=True, name=".snapshot") if cache is not None else None
    try:
        connection.commit()
    finally:
        if guard is not None:
            guard.close()


def compile_index(docs, cache, builder, *, published, persist, rebuild, coordinate, generation,
                  current=None, defer=False):
    """Compile the delta between the sources and the stored projection.

    ``published`` reads the published cache in place: unchanged, it is served
    as is; changed, the delta is applied to an in-memory backup, or with
    ``defer`` the call returns None so a writer can publish it.
    """
    started = time.perf_counter()
    lease = connection = store = None
    served = fallback = None
    try:
        if published is not None:
            connection, lease = published
            served = "published_cache"
        else:
            if persist:
                check_database_files(cache)
                if coordinate:
                    lease = DatabaseLease(cache)
            connection = sqlite3.connect(str(cache) if persist else ":memory:",
                                         timeout=policy()["busy_timeout_ms"] / 1000, check_same_thread=False)
            served = None if persist else "in_memory_compile"
        store = Store(docs, connection, lease, cache if published is not None or (persist and coordinate) else None)
        lease = None
        if persist:
            connection.execute("PRAGMA journal_mode=WAL")
        # A foreign checkout, another derivation or schema, or an FTS layout this SQLite
        # cannot open is recompiled from this checkout's sources, never served.
        layout = store.layout_supported()
        binding = store.binding() if layout else None
        full = rebuild or not bound(binding, docs, builder)
        rebind = not full and binding["docs"] != str(docs)
        prior_generation = max(store.generation(), generation)
        if current is None:
            current = scan_files(docs)
        cached = {} if full else store.file_entries()
        changed = sorted(p for p, entry in current.items()
                         if p not in cached or source_key(entry) != source_key(cached[p]))
        removed = sorted(set(cached) - set(current))
        flipped = set() if full else store.flipped_probes()
        dirty = bool(changed or removed or full or flipped)
        if published is not None:
            if defer and (dirty or rebind or current != cached):
                store.close()
                return None
            if dirty:
                memory = sqlite3.connect(":memory:", check_same_thread=False)
                try:
                    if not full:
                        connection.backup(memory)
                except BaseException:
                    memory.close()
                    raise
                served = "in_memory_compile" if full else "in_memory_copy"
                if full:
                    fallback = ("the published cache has a layout this SQLite cannot read" if not layout else
                                "the published cache is bound to another checkout, derivation or schema")
                store.close()
                connection = memory
                store = Store(docs, connection)
        status = {"path": str(cache), "full": full, "changed": changed, "removed": removed, "persisted": persist}
        if served is not None:
            status["served"] = served
        if fallback:
            status["fallback"] = fallback + "; compiled from sources"
        guard = cache if persist and coordinate else None
        if dirty:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if full:
                    store.reset()
                affected = set(changed) | set(removed)
                identities = set()
                for path in affected:
                    identities |= store.identities(path)
                for path in affected:
                    store.delete_source(path)
                receipts = context_catalog.receipt_paths(docs,
                    candidates=[docs / p for p in changed if Path(p).suffix.lower() == ".json"])
                markdown = set()
                for path in changed:
                    connection.execute("INSERT INTO files VALUES(?,?)", (path, encoded(current[path])))
                    suffix = Path(path).suffix
                    if current[path].get("excluded_link"):
                        store.unparsed(path, store.verified(path),
                                       f"links to an excluded location: {current[path]['excluded_link']}")
                    elif suffix == ".md":
                        store.update_note(path)
                        markdown.add(path)
                    elif suffix.lower() == ".json":
                        store.update_json(path, docs / path in receipts)
                    else:
                        store.unparsed(path, store.verified(path), "not a vault note: notes use the .md extension")
                if markdown:
                    store.catalog_rows(context_catalog.catalog(store.vault(markdown), include_receipts=False))
                for path in affected:
                    identities |= store.identities(path)
                owners = set(NoteMap(store)) if full else markdown | store.citing(identities) | flipped
                for owner in sorted(owners):
                    store.graph_owner(owner)
                store.diagnostics(None if full else affected)
                checked = scan_files(docs)
                if {p: source_key(v) for p, v in checked.items()} != {p: source_key(v) for p, v in current.items()}:
                    raise SourceChangedError("source changed during indexing; retry synchronization")
                for path, entry in checked.items():
                    connection.execute("UPDATE files SET payload=? WHERE path=?", (encoded(entry), path))
                store.set_meta("builder", builder)
                store.set_meta("schema_version", SCHEMA_VERSION)
                store.set_meta("docs", str(docs))
                store.set_meta("generation", prior_generation + 1)
                hashes = {p: entry["sha"] for p, entry in checked.items()}
                sources = {p: "sha256:" + hashes[p] for p in Rows(store, "documents", "path")}
                store.set_meta("snapshot_inputs", {"builder": builder, "files": hashes, "sources": sources})
                publish(connection, guard)
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        elif persist and (current != cached or rebind):
            connection.execute("BEGIN IMMEDIATE")
            try:
                for path, entry in current.items():
                    if entry != cached[path]:
                        connection.execute("UPDATE files SET payload=? WHERE path=?", (encoded(entry), path))
                if rebind:
                    store.set_meta("docs", str(docs))
                publish(connection, guard)
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        if not connection.in_transaction:
            connection.execute("BEGIN")
        status.update(ms=round((time.perf_counter() - started) * 1000, 1),
                      generation=store.meta("generation", 0), state="ready" if len(store.catalog["documents"]) else "ready_empty")
        unparsed = store.unparsed_paths()
        if unparsed:
            status["unparsed"] = unparsed
        return store.data(), status
    except BaseException as exc:
        if store is not None:
            store.close()
        elif connection is not None:
            connection.close()
        if lease is not None:
            lease.close()
        if isinstance(exc, DATABASE_ERRORS):
            raise sqlite_failure(exc) from exc
        raise


def published_generation(cache):
    try:
        connection = sqlite3.connect(published_uri(cache), uri=True)
    except sqlite3.Error:
        return 0
    try:
        row = connection.execute("SELECT payload FROM meta WHERE key='generation'").fetchone()
        value = json.loads(row[0]) if row else 0
        return value if type(value) is int else 0
    except (sqlite3.Error, ValueError):
        return 0
    finally:
        connection.close()


def generation_floor(cache):
    """The last generation this folder published, kept outside the database it numbers."""
    path = Path(cache).with_name(".generation")
    try:
        check_cache_file(path)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return value if type(value) is int and value > 0 else 0


def record_generation(cache, value):
    if type(value) is int and value > generation_floor(cache):
        path = Path(cache).with_name(".generation")
        check_cache_file(path)
        atomic_file.replace_bytes(path, str(value).encode("ascii"))


def copy_alive(directory):
    """Whether the inspection that owns a copy folder may still run.

    A folder younger than the busy timeout counts as live, since its owner
    creates and locks the marker only after creating the folder; an older one
    is live while its owner holds the marker lock.
    """
    try:
        if time.time() - directory.stat().st_mtime < policy()["busy_timeout_ms"] / 1000:
            return True
    except OSError:
        return False
    marker = directory / ".live"
    try:
        check_cache_file(marker)
        descriptor = os.open(marker, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    try:
        if not file_lock.try_lock(descriptor):
            return True
        file_lock.unlock(descriptor)
        return False
    finally:
        os.close(descriptor)


def sweep_runtime(folder):
    """Remove interrupted recovery candidates and ended inspection copies; callers hold ``.lock``."""
    for name in sorted(os.listdir(folder)):
        stale = Path(folder) / name
        if not name.startswith((RECOVERY_PREFIX, COPY_PREFIX)) or stale.is_symlink() or not stale.is_dir():
            continue
        # Only a recovery under this same lock creates a candidate, so none is live here.
        if name.startswith(RECOVERY_PREFIX) or not copy_alive(stale):
            shutil.rmtree(stale, ignore_errors=True)


def recover_database(docs, cache, builder):
    """Compile before publication; never replace a live database or its WAL."""
    exclusive = DatabaseLease(cache, exclusive=True)
    try:
        check_database_files(cache)
        if cache.exists() and not os.access(cache, os.W_OK):
            raise PermissionError(errno.EACCES, "vault SQLite cache is read-only")
        sweep_runtime(cache.parent)
        floor = max(published_generation(cache) if cache.exists() else 0, generation_floor(cache))
        with tempfile.TemporaryDirectory(prefix=RECOVERY_PREFIX, dir=cache.parent) as temporary:
            candidate = Path(temporary) / "index.db"
            data, _status = refresh(docs, candidate, builder, rebuild=True, coordinate=False, generation=floor)
            try:
                if data.store.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("replacement vault database failed integrity verification")
            finally:
                data.store.close()
            with candidate.open("rb+") as handle:
                os.fsync(handle.fileno())
            os.chmod(candidate, atomic_file.replacement_mode(cache))
            moved = []
            try:
                for suffix in ("-wal", "-shm", "-journal"):
                    path = Path(str(cache) + suffix)
                    check_cache_file(path)
                    if path.exists():
                        backup = Path(temporary) / ("old" + suffix)
                        os.replace(path, backup)
                        moved.append((path, backup))
                check_cache_file(cache)
                os.replace(candidate, cache)
            except BaseException:
                for path, backup in reversed(moved):
                    os.replace(backup, path)
                raise
    finally:
        exclusive.close()
    result, status = refresh(docs, cache, builder)
    status.update(full=True, recovered=True)
    return result, status


def serve_unchanged(docs, cache, builder, current):
    """The published cache when it already holds exactly these sources, else None."""
    published, _reason = open_published(cache)
    if published is None:
        return None
    try:
        result = compile_index(docs, cache, builder, published=published, persist=False, rebuild=False,
                               coordinate=False, generation=0, current=current, defer=True)
    except (CacheCorruptError, CacheUnavailableError, PermissionError):
        return None
    if result is not None:
        result[1]["persisted"] = True
    return result


def locked_refresh(docs, cache, builder, *, rebuild=False, repair=False, failed=None, wait=True):
    """Serve an unchanged published cache without the writer lock; serialize writers for a delta.

    The sources are hashed before the lock is taken. With wait=False return
    None instead of queueing behind another writer.
    """
    docs, cache = Path(docs).resolve(), Path(cache)
    if not docs.is_dir():
        raise ValueError("needs_setup: workspace/docs does not exist")
    project = docs.parents[1]
    folder = atomic_file.real_directory(project, cache.parent.relative_to(project))
    path = folder / ".lock"
    current = None
    if not rebuild and not repair:
        capabilities()
        current = scan_files(docs)
        served = serve_unchanged(docs, cache, builder, current)
        if served is not None:
            check_cache_file(path)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o666)
            try:
                if file_lock.try_lock(descriptor):
                    try:
                        sweep_runtime(folder)
                    finally:
                        file_lock.unlock(descriptor)
            finally:
                os.close(descriptor)
            return served
    check_cache_file(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o666)
    try:
        if not file_lock.try_lock(descriptor):
            if not wait:
                return None
            file_lock.lock(descriptor)
            # Another writer ran meanwhile; the earlier hashes may predate its sources.
            current = None
        try:
            sweep_runtime(folder)
            try:
                result = refresh(docs, cache, builder, rebuild=rebuild, current=current,
                                 generation=generation_floor(cache))
                # Another process may already have replaced the database this reader failed on.
                if repair and (failed is None or failed == database_identity(cache)):
                    result[0].store.close()
                    result = recover_database(docs, cache, builder)
            except CacheCorruptError:
                result = recover_database(docs, cache, builder)
            record_generation(cache, result[1]["generation"])
            if result[1]["full"]:
                cleanup_legacy(cache, docs)
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
        owned = (isinstance(value, dict) and isinstance(value.get("docs"), str)
                 and Path(value["docs"]).resolve() == docs and value.get("schema_version") == 2)
    except (OSError, ValueError):
        return
    if not owned:
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


def same_rows(left, right, sql):
    missing = object()
    return all(a == b for a, b in zip_longest(left.execute(sql), right.execute(sql), fillvalue=missing))


def projection_mismatches(store, fresh):
    mismatches = []
    for table in PROJECTION:
        columns = len(store.connection.execute(f"PRAGMA table_info({table})").fetchall())
        sql = f"SELECT * FROM {table} ORDER BY " + ",".join(str(n) for n in range(1, columns + 1))
        if not same_rows(store.connection, fresh.connection, sql):
            mismatches.append(table)
    for key in ("tiers", "snapshot_inputs", "text_targets"):
        if store.meta(key) != fresh.meta(key):
            mismatches.append(key)
    for connection in (store.connection, fresh.connection):
        connection.execute("CREATE VIRTUAL TABLE temp.index_tokens USING fts5vocab(main,search_text,instance)")
    sql = ("SELECT v.term,m.uid,v.col,v.offset FROM temp.index_tokens v "
           "JOIN search_units m ON m.rowid=v.doc ORDER BY v.term,m.uid,v.col,v.offset")
    if not same_rows(store.connection, fresh.connection, sql):
        mismatches.append("search_text")
    return mismatches


def cache_file_signature(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if info.st_nlink == 0:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("vault database, sidecars and lock must be regular unaliased files")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def snapshot_file_hash(path, destination=None):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if info.st_nlink == 0:
            # SQLite is deleting this WAL or the file is being replaced; capture again.
            raise FileNotFoundError(errno.ENOENT, "vault snapshot input is being unlinked", str(path))
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("vault snapshot inputs must be regular unaliased files")
        digest = hashlib.sha256()
        output = destination.open("wb") if destination is not None else None
        try:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
        finally:
            if output is not None:
                output.close()
        return digest.hexdigest()


def copy_cache_snapshot(cache, clone):
    """A closing reader may checkpoint WAL; retry a changed byte snapshot."""
    paths = (cache, Path(str(cache) + "-wal"))
    deadline = time.monotonic() + policy()["busy_timeout_ms"] / 1000
    while True:
        try:
            before = {path: cache_file_signature(path) for path in paths}
            copied = {}
            for path, signature in before.items():
                target = clone if path == cache else Path(str(clone) + "-wal")
                if signature is None:
                    target.unlink(missing_ok=True)
                else:
                    copied[path] = snapshot_file_hash(path, target)
            if before == {path: cache_file_signature(path) for path in paths}:
                checked = {path: snapshot_file_hash(path) for path in copied}
                if checked == copied and before == {path: cache_file_signature(path) for path in paths}:
                    return
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            raise CacheBusyError("vault cache changed while capturing its snapshot; retry inspection")
        time.sleep(file_lock.POLL_SECONDS)


def snapshot_directory(cache):
    """``(folder, marker, fallback)``: a copy folder in the runtime folder that the next writer
    sweeps once this inspection ends, or a system one when the runtime folder is read-only."""
    try:
        temporary = tempfile.TemporaryDirectory(prefix=COPY_PREFIX, dir=cache.parent, ignore_cleanup_errors=True)
    except OSError as exc:
        return (tempfile.TemporaryDirectory(prefix="vault-index-check-", ignore_cleanup_errors=True), None,
                f"the runtime folder is not writable ({exc}); the snapshot was copied to the system temporary folder")
    try:
        marker = os.open(Path(temporary.name) / ".live", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o666)
        file_lock.try_lock(marker)
    except BaseException:
        temporary.cleanup()
        raise
    return temporary, marker, None


def inspect_index(docs, cache, builder, *, check=False):
    """Copy a consistent closed/checkpointed or WAL snapshot without source or published-cache writes."""
    capabilities()
    docs, cache = Path(docs).resolve(), Path(cache)
    check_database_files(cache)
    if not cache.exists():
        return {"status": "absent", "documents": 0}
    try:
        temporary, marker, fallback = snapshot_directory(cache)
        try:
            guard = None
            try:
                # The shared snapshot lease pauses publication only for the copy itself.
                if cache.with_name(".snapshot").exists():
                    guard = DatabaseLease(cache, name=".snapshot", read_only=True)
                clone = Path(temporary.name) / "index.db"
                copy_cache_snapshot(cache, clone)
            finally:
                if guard is not None:
                    guard.close()
            result = inspect_copy(docs, cache, clone, builder, check)
            if fallback:
                result["copy_fallback"] = fallback
            return result
        finally:
            if marker is not None:
                try:
                    file_lock.unlock(marker)
                finally:
                    os.close(marker)
            temporary.cleanup()
    except CacheBusyError as exc:
        return {"status": "busy", "reason": str(exc)}


def inspect_copy(docs, cache, clone, builder, check):
    connection = sqlite3.connect(clone)
    try:
        store = Store(docs, connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return {"status": "corrupt"}
        binding = store.binding()
        if not binding:
            return {"status": "absent", "documents": 0}
        if not bound(binding, docs, builder):
            reasons = [] if bound(dict(binding, builder=builder, schema_version=SCHEMA_VERSION), docs, builder) else ["docs"]
            reasons += [key for key, value in (("builder", builder), ("schema_version", SCHEMA_VERSION))
                        if binding.get(key) != value]
            return {"status": "incompatible", "docs": binding.get("docs"), "reasons": reasons}
        if not store.layout_supported():
            return {"status": "incompatible", "docs": binding.get("docs"), "reasons": ["layout"]}
        stored = store.file_entries()
        result = {"status": "ready" if len(store.catalog["documents"]) else "ready_empty",
                  "generation": store.meta("generation"), "documents": len(store.catalog["documents"]),
                  "units": len(store.catalog["units"]), "files": len(stored), "docs": binding["docs"],
                  "schema_version": SCHEMA_VERSION, "verified": False}
        unparsed = store.unparsed_paths()
        if unparsed:
            result["unparsed"] = unparsed
        if not check:
            observed = scan_files(docs, digest=False)
            stat_only = lambda entries: {path: (entry["size"], entry["mtime_ns"], entry.get("excluded_link"))
                                         for path, entry in entries.items()}
            result["possibly_stale"] = stat_only(observed) != stat_only(stored)
            return result
        hashes = {p: v["sha"] for p, v in stored.items()}
        if {p: source_key(v) for p, v in scan_files(docs).items()} != {p: source_key(v) for p, v in stored.items()}:
            result["status"] = "stale"
            return result
        expected_ids = {r[0] for r in connection.execute("SELECT uid FROM units")}
        found = {r[0] for r in connection.execute("SELECT uid FROM search_units")}
        search_ids = {r[0] for r in connection.execute(f"SELECT rowid FROM {SEARCH_TABLE}")}
        mapped = {r[0] for r in connection.execute("SELECT rowid FROM search_units")}
        if expected_ids != found or search_ids != mapped:
            result["status"] = "incomplete"
        try:
            fresh, _status = refresh(docs, cache, builder, persist=False, rebuild=True)
        except SourceChangedError:
            result["status"] = "stale"
            return result
        try:
            if fresh.store.meta("snapshot_inputs")["files"] != hashes:
                result["status"] = "stale"
                return result
            mismatches = projection_mismatches(store, fresh.store)
        finally:
            fresh.store.close()
        if mismatches:
            result.update(status="incomplete", mismatches=mismatches)
        result["verified"] = result["status"] in {"ready", "ready_empty"}
        return result
    except sqlite3.Error as exc:
        raise ValueError(f"invalid vault database: {exc}") from exc
    finally:
        connection.close()
