"""Builder registry for the vault checker (plugins/software-engineering-team/
scripts/vault_check.py), the doctrine test_ba_compile.py applies to ba_compile.

VAULT_BUILDERS holds one builder per id in vault_check.CHECK_IDS; each builder
mutates a copy of one valid project vault so that its check fires and no other;
a meta-test keeps the registry in lockstep with CHECK_IDS; and the untouched
vault stays silent. A check that cannot fire alone names the checks it always
fires with in COMPANIONS, and its builder is held to exactly that set."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_check  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402

MAP = "maps/solution-design.md"
LANDSCAPE = "solution-design/landscape.md"
DECISION = "solution-design/decisions/queue-decision.md"
PROSE = "Orders flow through one queue."

# One Solution Design subtree on top of a set-up project vault: its map, the
# landscape hub and one decision, which the rendered decision index lists.
SUBTREE = {
    MAP: """---
type: moc
title: Solution Design
tags:
  - doc/moc
---

# Solution Design

- [[solution-design/landscape|Solution landscape]]
- [[solution-design/decisions/queue-decision|Queue choice]]
""",
    LANDSCAPE: f"""---
type: landscape
title: Solution landscape
status: draft
tags:
  - doc/landscape
  - status/draft
---

# Solution landscape

{PROSE}

## Navigation <!-- sec: nav -->

[[maps/solution-design|Solution Design]]
""",
    DECISION: """---
type: decision
title: Queue choice
status: proposed
tags:
  - doc/decision
  - status/proposed
aliases:
  - SD-001
---

# Queue choice

Use one durable queue.

## Navigation <!-- sec: nav -->

[[solution-design/landscape|Solution landscape]]
""",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append(path: Path, text: str) -> None:
    write(path, path.read_text(encoding="utf-8") + text)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture edit target not found in {path}: {old[:60]}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def map_note(title: str, body: str = "") -> str:
    return f"---\ntype: moc\ntitle: {title}\ntags:\n  - doc/moc\n---\n\n# {title}\n{body}"


def run_vault_check(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = vault_check.main(list(argv))
    return code, out.getvalue()


def findings(docs: Path) -> list[dict]:
    """Every finding one full check of the vault emits."""
    _code, out = run_vault_check("check", "--vault", str(docs), "--json")
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def make_valid_vault(project: Path, *, real_setup: bool = False) -> Path:
    docs = project / "workspace" / "docs"
    if real_setup:
        init_repository(project)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "setup_project.py"), "--project-root", str(project)],
            cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        import setup_project
        payload, _policy_path, policy = setup_project.package_surfaces()
        for source, target in setup_project.payload_sources(policy, payload, docs):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for rel, text in SUBTREE.items():
        write(docs / rel, text)
    append(docs / "home.md", "\n- [[maps/solution-design|Solution Design]]\n")
    code, out = run_vault_check("render-decisions", "--vault", str(docs))
    assert code == 0, out
    code, out = run_vault_check("render-relations", "--vault", str(docs))
    assert code == 0, out
    return docs


def break_vault_layout(docs: Path) -> None:
    write(docs / "scratch.txt", "Working notes.\n")


def break_wikilink_resolution(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [[solution-design/missing-queue|one queue]].")


def break_anchor_resolution(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE,
         "Orders flow through [[solution-design/decisions/queue-decision#^missing|one queue]].")


def break_link_policy(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [one queue](decisions/queue-decision.md).")


def break_table_pipe(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE,
         "| Record |\n|---|\n| [[solution-design/decisions/queue-decision|Queue choice]] |")


def break_table_shape(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "| Record |\n| Queue choice |")


def break_banned_basename(docs: Path) -> None:
    write(docs / "maps/overview.md", map_note("Solution overview"))
    append(docs / MAP, "- [[maps/overview|Solution overview]]\n")


def break_title_shape(docs: Path) -> None:
    edit(docs / LANDSCAPE, "# Solution landscape", "# Solution Landscape")


def break_orphans(docs: Path) -> None:
    write(docs / "maps/orphan-map.md", map_note("Orphan map"))


def break_moc_coverage(docs: Path) -> None:
    # It links itself, so it has an inbound link that home never reaches.
    write(docs / "maps/island-map.md", map_note("Island map", "\n[[maps/island-map|Island map]]\n"))


def break_map_coverage(docs: Path) -> None:
    # The decision's nav footer still links the hub, so only the map misses it.
    edit(docs / MAP, "- [[solution-design/landscape|Solution landscape]]\n", "")


def break_nav_footer(docs: Path) -> None:
    edit(docs / LANDSCAPE, "## Navigation <!-- sec: nav -->", "## Navigation")


def break_frontmatter_props(docs: Path) -> None:
    edit(docs / LANDSCAPE, "status: draft\n", "status: draft\ncolour: blue\n")


def break_tags_mirror(docs: Path) -> None:
    edit(docs / LANDSCAPE, "  - status/draft", "  - status/approved")


def break_alias_ownership(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [[maps/solution-design|SD-001]].")


def break_decision_records(docs: Path) -> None:
    append(docs / "solution-design/decision-log.md", "\nEdited by hand.\n")


def break_generated_views(docs: Path) -> None:
    append(docs / "maps/_generated/relation-status.md", "\nEdited by hand.\n")


def break_home_shape(docs: Path) -> None:
    append(docs / "home.md", "- [[solution-design/landscape|Solution landscape]]\n")


def break_obsidian_payload(docs: Path) -> None:
    (docs / ".obsidian" / "appearance.json").unlink()


VAULT_BUILDERS = {
    "vault_layout": break_vault_layout,
    "wikilink_resolution": break_wikilink_resolution,
    "anchor_resolution": break_anchor_resolution,
    "link_policy": break_link_policy,
    "table_pipe": break_table_pipe,
    "table_shape": break_table_shape,
    "banned_basename": break_banned_basename,
    "title_shape": break_title_shape,
    "orphans": break_orphans,
    "moc_coverage": break_moc_coverage,
    "map_coverage": break_map_coverage,
    "nav_footer": break_nav_footer,
    "frontmatter_props": break_frontmatter_props,
    "tags_mirror": break_tags_mirror,
    "alias_ownership": break_alias_ownership,
    "decision_records": break_decision_records,
    "generated_views": break_generated_views,
    "home_shape": break_home_shape,
    "obsidian_payload": break_obsidian_payload,
}

# A note without an inbound link cannot be reached from home either, so every
# orphans finding comes with a moc_coverage finding for the same note.
COMPANIONS = {"orphans": {"moc_coverage"}}


@integration
class SetupVaultSmokeTests(unittest.TestCase):
    def test_real_setup_produces_a_valid_vault(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(findings(make_valid_vault(Path(raw) / "project", real_setup=True)), [])


class VaultBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.docs = make_valid_vault(Path(cls.temporary.name) / "project")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_valid_vault_is_silent(self):
        self.assertEqual(findings(self.docs), [])

    def test_registry_lockstep_with_check_ids(self):
        self.assertEqual(sorted(VAULT_BUILDERS), sorted(vault_check.CHECK_IDS))

    def test_each_builder_fires_exactly_its_check(self):
        for check, builder in sorted(VAULT_BUILDERS.items()):
            with self.subTest(check=check), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "docs"
                shutil.copytree(self.docs, docs)
                builder(docs)
                found = findings(docs)
                self.assertEqual({finding["check"] for finding in found},
                                 {check, *COMPANIONS.get(check, ())}, found)


class VaultFileViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.docs = make_valid_vault(Path(cls.temporary.name) / "project")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def all_findings(self, vault):
        found = []
        for check in vault_check.CHECKS.values():
            check(vault, found)
        vault_check.check_obsidian_payload(vault, found, vault_check.DEFAULT_PAYLOAD)
        return sorted(found, key=lambda item: (item.path, item.line, item.check, item.message))

    def test_every_check_matches_materialized_postimage(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        before = {path.relative_to(self.docs): path.read_bytes()
                  for path in self.docs.rglob("*") if path.is_file()}
        for name, builder in sorted(VAULT_BUILDERS.items()):
            with self.subTest(check=name), tempfile.TemporaryDirectory() as temporary:
                materialized = Path(temporary) / "docs"
                shutil.copytree(self.docs, materialized)
                builder(materialized)
                after = {path.relative_to(materialized): path.read_bytes()
                         for path in materialized.rglob("*") if path.is_file()}
                view = vault_check.VaultFileView(self.docs)
                for relative in set(before) | set(after):
                    if before.get(relative) != after.get(relative):
                        view.put(self.docs / relative, after.get(relative))
                actual = vault_check.build_vault(self.docs, policy, view)
                expected = vault_check.build_vault(materialized, policy)
                self.assertEqual(self.all_findings(actual), self.all_findings(expected))
        self.assertEqual(before, {path.relative_to(self.docs): path.read_bytes()
                                 for path in self.docs.rglob("*") if path.is_file()})

    def test_registry_artifact_and_deleted_target_are_read_from_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "docs"
            root.mkdir()
            write(root / "solution-design/landscape.md", "[Preview](artifacts/a.bin)\n[[solution-design/old|Old]]\n")
            write(root / "solution-design/old.md", "Old\n")
            view = vault_check.VaultFileView(root)
            view.put(root / "solution-design/artifacts/a.bin", b"\xff\x00opaque")
            view.put(root / "solution-design/old.md", None)
            registry = root / "business-analysis/shop/_generated/registry.json"
            view.put(registry, b'{"ids":{"BR-001":{"doc":"rule.md"}}}')
            vault = vault_check.build_vault(root, vault_check.load_policy(vault_check.DEFAULT_POLICY), view)
            self.assertIn("solution-design/artifacts/a.bin", vault.index)
            self.assertNotIn("solution-design/old.md", vault.index)
            self.assertEqual(vault_check.relation_identity_owners(vault)["shop:BR-001"], "business-analysis/shop/rule.md")
            found = []
            vault_check.check_link_policy(vault, found)
            self.assertEqual(found, [])
            vault_check.check_wikilink_resolution(vault, found)
            self.assertEqual(len(found), 1)
            self.assertIn("old", found[0].message)
            self.assertFalse(registry.exists())

    def test_batch_results_equal_separate_changed_checks(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        vault = vault_check.build_vault(self.docs, policy)
        targets = [DECISION, LANDSCAPE, "missing.md", "solution-design/_generated/decision-index.md"]
        with mock.patch.dict(vault_check.CHECKS, {key: mock.Mock(wraps=value)
                                                for key, value in vault_check.CHECKS.items()}):
            combined = vault_check.changed_findings(vault, targets)
            for name in vault_check.CHANGED_CHECKS:
                self.assertEqual(vault_check.CHECKS[name].call_count, 1)
        for target in targets:
            self.assertEqual(combined[target], vault_check.changed_findings(vault, [target])[target])

    def test_aliases_cannot_be_overridden_or_used_as_patch_parents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(root / "actual.md", "old")
            try:
                (root / "alias.md").symlink_to(root / "actual.md")
                (root / "directory-alias").symlink_to(root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(str(exc))
            view = vault_check.VaultFileView(root)
            view.put(root / "actual.md", b"new")
            self.assertEqual(view.read_bytes(root / "alias.md"), b"new")
            for path in (root / "alias.md", root / "directory-alias/child.md"):
                with self.assertRaisesRegex(ValueError, "alias"):
                    view.put(path, b"unsafe")
            view.put(root / "new-file", b"x")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                view.put(root / "new-file/child", b"x")

    def test_lexical_root_alias_remains_consistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / "real").mkdir()
            write(base / "real/home.md", "Home\n")
            try:
                (base / "alias").symlink_to(base / "real", target_is_directory=True)
            except OSError as exc:
                self.skipTest(str(exc))
            root = base / "alias"
            vault = vault_check.build_vault(root, vault_check.load_policy(vault_check.DEFAULT_POLICY))
            self.assertEqual(vault.root, root)
            self.assertEqual(vault.index, {"home.md"})

    def test_native_reparse_metadata_rejects_patch_aliases_without_junction_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent = root / "junction"
            parent.mkdir()
            leaf = root / "reparse.md"
            write(leaf, "original")
            original_lstat = Path.lstat
            def metadata(path, *args, **kwargs):
                if path in {parent, leaf}:
                    return type("Metadata", (), {"st_mode": stat.S_IFDIR if path == parent else stat.S_IFREG,
                                                  "st_file_attributes": 0x400})()
                return original_lstat(path, *args, **kwargs)
            view = vault_check.VaultFileView(root)
            with mock.patch.object(Path, "lstat", metadata), \
                    mock.patch.object(Path, "is_junction", return_value=False, create=True):
                for path in (parent / "child.md", leaf):
                    with self.subTest(path=path), self.assertRaisesRegex(ValueError, "alias"):
                        view.put(path, b"unsafe")
            self.assertEqual(view.overrides, {})
            self.assertEqual(leaf.read_text(), "original")
            self.assertFalse((parent / "child.md").exists())


if __name__ == "__main__":
    unittest.main()
