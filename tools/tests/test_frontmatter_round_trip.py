"""Every compiler's frontmatter writer and reader round-trip a value exactly."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import architecture_compile  # noqa: E402
import ba_compile  # noqa: E402
import backlog_compile  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import design_system_compile  # noqa: E402
import experience_compile  # noqa: E402
import operation_compile  # noqa: E402
import requirement_compile  # noqa: E402


VALUES = (
    'Fix the "login": flow',
    'say "hi"',
    '"quoted" start',
    "it's",
    "'single'",
    "C:\\temp\\new",
    'a \\" b',
    "ends with a backslash \\",
    "key: value",
    "ends with a colon:",
    "a #hash",
    "#hash",
    " padded ",
    "[[target|alias]]",
    "[draft] plan",
    "- dash lead",
    "line\nbreak",
    "separator\u2028inside",
    "~",
    "null",
    "plain",
)


class CompilerRoundTripTests(unittest.TestCase):
    """Write, read and write again: the value survives and the bytes hold."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.note = Path(temporary.name) / "note.md"

    def cycle(self, write, read):
        for value in VALUES:
            with self.subTest(value=value):
                first = write({"goal": value, "items": [value], "tail": "kept"})
                self.note.write_text(first, encoding="utf-8")
                props = read(self.note)
                self.assertEqual(props["goal"], value)
                self.assertEqual(props["items"], [value])
                self.assertEqual(props["tail"], "kept")
                self.assertEqual(write(props), first)

    def test_delivery_notes(self):
        self.cycle(lambda props: delivery_compile.frontmatter(props, "# Note"),
                   lambda path: delivery_compile.split_note(path)[0])

    def test_architecture_records(self):
        def read(path):
            architecture_compile.rewrite(path, {})
            return ba_compile.parse_frontmatter(path.read_text(encoding="utf-8"))[0]
        self.cycle(lambda props: architecture_compile.frontmatter(props, "# Note"), read)

    def test_requirement_notes(self):
        self.cycle(lambda props: requirement_compile.render_note(props, "# Note"),
                   lambda path: requirement_compile.split_note(path)[0])

    def test_operation_contracts(self):
        self.cycle(lambda props: operation_compile.render(props, "# Note"),
                   lambda path: operation_compile.parse(path)[0])

    def test_delivery_governance(self):
        self.cycle(lambda props: delivery_governance.render(props, "# Note"),
                   lambda path: delivery_governance.read(path)[0])

    def test_backlog_notes(self):
        self.cycle(lambda props: backlog_compile.front_matter(props, "# Note"),
                   lambda path: backlog_compile.parse_front_matter(path)[0])

    def test_experience_notes(self):
        def read(path):
            data, body = experience_compile.fm(path)
            experience_compile.rewrite(path, data, body)
            return experience_compile.fm(path)[0]
        self.cycle(lambda props: experience_compile.render_fm(props, "# Note"), read)

    def test_business_analysis_stub_title(self):
        schema = {"doc_types": {"glossary": {
            "extra_frontmatter": {"required": []}, "required_sections": [], "mints": [],
        }}, "row_schemas": {}}
        for value in VALUES:
            with self.subTest(value=value):
                text = "\n".join(ba_compile.stub_lines(schema, "glossary", value))
                self.assertEqual(ba_compile.parse_frontmatter(text)[0]["title"], value)

    def test_design_system_reader_decodes_a_quoted_title(self):
        for value in VALUES:
            with self.subTest(value=value):
                self.note.write_text(
                    f"---\ntitle: {ba_compile.frontmatter_scalar(value)}\nrevision: 2\n---\n",
                    encoding="utf-8")
                fields, _lines, _end = design_system_compile.parse_frontmatter(self.note)
                self.assertEqual(fields, {"title": value, "revision": 2})


class LegacyNoteCompatibilityTests(unittest.TestCase):
    """Notes written before quoted values were decoded parse by one rule."""

    def test_only_escaped_or_apostrophe_edged_quoted_tokens_change(self):
        # Every double-quoted token without a backslash or an inner apostrophe
        # edge parses as the historical quote strip did, so its hashes hold.
        for token in ('"[[business-analysis/sales/space|Sales]]"',
                      '"Scenario: Land and Expand Process"',
                      '"business-analysis|business-analysis/sales/space|sha256:1a43"',
                      '"ST-019: verification=sha256:20e3, environment=sha256:e5dc"',
                      '"[[a\\|b]]"', '"Use "fast": lane"', "'single'", 'plain "end"',
                      '""', '"true"', '"12"'):
            with self.subTest(token=token):
                if "\\" not in token:
                    self.assertEqual(ba_compile.frontmatter_value(token), token.strip("\"'"))

        # The two token shapes whose reading changes.
        self.assertEqual(ba_compile.frontmatter_value('"say \\"hi\\""'), 'say "hi"')
        self.assertEqual(ba_compile.frontmatter_value('"\'aside\'"'), "'aside'")
        # Tokens that are not one JSON string keep the historical strip.
        self.assertEqual(ba_compile.frontmatter_value('"[[a\\|b]]"'), "[[a\\|b]]")
        self.assertEqual(ba_compile.frontmatter_value('"Use "fast": lane"'), 'Use "fast": lane')
        self.assertEqual(ba_compile.frontmatter_value("'single'"), "single")
        self.assertEqual(ba_compile.frontmatter_value('"\\ud800"'), "\\ud800")

    def test_typed_tokens_keep_their_types(self):
        props, _line, error = ba_compile.parse_frontmatter(
            '---\nflag: "true"\ncount: "12"\nrelation: ""\n---\n')
        self.assertIsNone(error)
        self.assertEqual(props, {"flag": True, "count": 12, "relation": []})

    def test_escaped_value_from_the_old_writer_decodes_once_and_stops_growing(self):
        with tempfile.TemporaryDirectory() as temporary:
            note = Path(temporary) / "delivery.md"
            # First write of the old writer, then the bytes after one old rewrite.
            for text, value in (
                ('---\ngoal: "Fix the \\"login\\": flow"\n---\n\n# D\n', 'Fix the "login": flow'),
                ('---\ngoal: "Fix the \\\\\\"login\\\\\\": flow"\n---\n\n# D\n',
                 'Fix the \\"login\\": flow'),
            ):
                with self.subTest(text=text):
                    note.write_text(text, encoding="utf-8")
                    props, body = delivery_compile.split_note(note)
                    self.assertEqual(props["goal"], value)
                    rewritten = delivery_compile.frontmatter(props, body)
                    self.assertEqual(rewritten, text)
                    note.write_text(rewritten, encoding="utf-8")
                    self.assertEqual(delivery_compile.frontmatter(*delivery_compile.split_note(note)),
                                     rewritten)


class ExperienceDigestCompatibilityTests(unittest.TestCase):
    """The source digest keeps the historical rendering existing approvals hash."""

    def test_quoting_a_title_leaves_the_source_digest_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "checkout"
            package.mkdir()
            note = package / "experience.md"
            legacy = ("---\ntype: experience\nrevision: 1\ntitle: Scenario: Land and expand\n"
                      "tags:\n  - \"doc/experience\"\n---\n\n# Scenario: Land and expand\n")
            note.write_text(legacy, encoding="utf-8")
            stable = legacy.replace('tags:\n  - "doc/experience"\n', "")
            historical = "sha256:" + hashlib.sha256(
                b"experience.md\0" + stable.encode() + b"\0").hexdigest()
            self.assertEqual(experience_compile.source_digest(package), historical)

            data, body = experience_compile.fm(note)
            experience_compile.rewrite(note, data, body)
            self.assertIn('title: "Scenario: Land and expand"\n', note.read_text(encoding="utf-8"))
            self.assertEqual(experience_compile.source_digest(package), historical)


if __name__ == "__main__":
    unittest.main()
