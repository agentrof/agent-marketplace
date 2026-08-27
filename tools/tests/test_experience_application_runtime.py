import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
sys.path.insert(0, str(SCRIPTS))

import experience_application_check
import experience_compile
from tools.tests.experience_fixture import APPLICATION_TOKEN_CSS


class ExperienceApplicationRuntimeTests(unittest.TestCase):
    @staticmethod
    def rendered_template() -> str:
        return (
            experience_application_check.template_text()
            .replace(
                "RUNTIME_SHA256",
                experience_application_check.runtime_sha256(),
            )
            .replace(
                "RUNTIME_CSP_SHA256",
                experience_application_check.runtime_csp_sha256(),
            )
        )

    @staticmethod
    def scan(text: str) -> experience_application_check.ApplicationScanner:
        scanner = experience_application_check.ApplicationScanner()
        scanner.feed(text)
        scanner.close()
        return scanner

    @staticmethod
    def registry(
        revision: int, previous_application_hash=None,
    ) -> dict:
        digest = "sha256:" + "1" * 64
        packages: list[dict] = []
        value = {
            "schema_version": 2,
            "application_revision": revision,
            "source_hash": digest,
            "package_set_hash": experience_application_check.sha(
                experience_application_check.canonical(packages)
            ),
            "coverage_hash": digest,
            "design_system": {
                "package_hash": digest,
                "revision": 1,
                "master_source_hash": digest,
            },
            "runtime_sha256": digest,
            "previous_application_hash": (
                previous_application_hash
                or experience_application_check.GENESIS_APPLICATION_HASH
            ),
            "packages": packages,
            "coverage": {
                "entry_route": "#/empty",
                "routes": [{
                    "route": "#/empty",
                    "state_class": "empty",
                    "experience_id": "application",
                }],
                "transitions": [],
                "simulations": [],
                "record_refs": [],
                "state_classes": ["empty"],
            },
        }
        value["application_hash"] = experience_application_check.sha(
            experience_application_check.canonical(value)
        )
        return value

    def test_shipped_contract_and_runtime_self_check(self):
        self.assertEqual(experience_application_check.self_check(), [])

    def test_source_hash_ignores_only_actual_direct_head_machine_metadata(self):
        marker = "/* application:author-styles:start */"
        fake_a = (
            marker + "\n[data-application-route]::before { content: \"<meta "
            "name='experience-application-source-hash' content='VISIBLE-A'>\"; }"
        )
        fake_b = fake_a.replace("VISIBLE-A", "VISIBLE-B")
        source_a = self.rendered_template().replace(marker, fake_a, 1)
        source_b = self.rendered_template().replace(marker, fake_b, 1)
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                '[data-application-route]::before { content: "<meta " "fake>"; }',
                set(),
            ),
            {"content"},
        )
        self.assertNotEqual(
            experience_application_check.source_hash(source_a),
            experience_application_check.source_hash(source_b),
        )
        self.assertEqual(
            experience_application_check.metadata(source_a)[
                "experience-application-source-hash"
            ],
            "SOURCE_HASH",
        )

    def test_scanner_normalizes_css_escapes_and_captures_network_attributes(self):
        scanner = experience_application_check.ApplicationScanner()
        scanner.feed(
            '<body background="https://example.invalid/background.png">'
            '<a href="#/safe" ping="https://example.invalid/collect">Safe</a>'
            r"<style>.leak { background: u\72l(https://example.invalid/x) }</style>"
            "</body>"
        )
        self.assertIn(
            ("background", "https://example.invalid/background.png"),
            scanner.targets,
        )
        self.assertIn(
            ("ping", "https://example.invalid/collect"),
            scanner.targets,
        )
        self.assertIn(
            "url(https://example.invalid/x)",
            experience_application_check.normalized_css(scanner.styles[0]),
        )

    def test_smil_and_escaped_presentation_urls_are_forbidden(self):
        scanner = self.scan(
            r'<svg><animate attributeName="href" to="https://example.invalid/x"></animate>'
            r'<path filter="u\72l(https://example.invalid/filter)"></path></svg>'
        )
        self.assertEqual(scanner.smil_mutations, ["animate"])
        self.assertEqual(
            scanner.presentation_urls,
            [("filter", r"u\72l(https://example.invalid/filter)")],
        )
        findings = experience_application_check.dynamic_svg_findings(scanner)
        self.assertTrue(any("SMIL mutation" in item for item in findings))
        self.assertTrue(any("dynamic URL references" in item for item in findings))

        animated_svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5">'
            b'<animate attributeName="cx" values="5;95;5" dur=".2s" '
            b'repeatCount="indefinite"/></circle></svg>'
        )
        svg_url = "data:image/svg+xml;base64," + base64.b64encode(
            animated_svg
        ).decode("ascii")
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", svg_url,
            )
        )
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", "data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==",
            )
        )

        def png_chunk(kind: bytes, data: bytes) -> bytes:
            checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
            return (
                len(data).to_bytes(4, "big") + kind + data
                + checksum.to_bytes(4, "big")
            )

        png_header = b"\x89PNG\r\n\x1a\n"
        ihdr = png_chunk(
            b"IHDR",
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00",
        )
        idat = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        iend = png_chunk(b"IEND", b"")
        static_png = "data:image/png;base64," + base64.b64encode(
            png_header + ihdr + idat + iend
        ).decode("ascii")
        missing_pixels = "data:image/png;base64," + base64.b64encode(
            png_header + ihdr + iend
        ).decode("ascii")
        missing_iend = "data:image/png;base64," + base64.b64encode(
            png_header + ihdr + idat
        ).decode("ascii")
        animated_png = "data:image/png;base64," + base64.b64encode(
            png_header + ihdr
            + png_chunk(b"acTL", b"\x00\x00\x00\x01\x00\x00\x00\x00")
            + idat
            + iend
        ).decode("ascii")
        self.assertTrue(
            experience_application_check.allowed_application_target(
                "src", static_png,
            )
        )
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", animated_png,
            )
        )
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", missing_pixels,
            )
        )
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", missing_iend,
            )
        )
        self.assertFalse(
            experience_application_check.allowed_application_target(
                "src", "data:image/jpeg;base64,/9j/2Q==",
            )
        )

    def test_csp_must_be_exact_and_second_in_head(self):
        text = self.rendered_template()
        csp = next(line for line in text.splitlines() if "Content-Security-Policy" in line)
        text = text.replace(csp + "\n", "", 1).replace(
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            + csp,
            1,
        )
        findings = experience_application_check.document_structure_findings(
            self.scan(text)
        )
        self.assertTrue(any("head must begin" in item for item in findings))
        for mutated_head in (
            self.rendered_template().replace(
                '  <meta name="viewport" content="width=device-width, initial-scale=1">',
                '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
                '  <meta name="theme-color" content="#ff00ff">',
                1,
            ),
            self.rendered_template().replace(
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                '<meta id="extra" name="viewport" '
                'content="width=device-width, initial-scale=1">',
                1,
            ),
        ):
            self.assertTrue(any(
                "closed canonical child order and exact attribute surface" in item
                for item in experience_application_check.document_structure_findings(
                    self.scan(mutated_head)
                )
            ))

    def test_scripts_must_be_final_direct_children_after_dom(self):
        text = self.rendered_template()
        runtime = re.search(
            r'  <script id="experience-application-runtime".*?</script>\n',
            text,
            re.S,
        )
        self.assertIsNotNone(runtime)
        runtime_text = runtime.group(0)
        text = text.replace(runtime_text, "", 1).replace(
            '  <main id="application-main"', runtime_text + '  <main id="application-main"', 1
        )
        findings = experience_application_check.document_structure_findings(
            self.scan(text)
        )
        self.assertTrue(any("loaded after the application DOM" in item for item in findings))
        self.assertTrue(any("final direct body children" in item for item in findings))

        trailing = self.rendered_template().replace(
            "</body>", "Visible DOM after the runtime\n</body>", 1
        )
        trailing_findings = experience_application_check.document_structure_findings(
            self.scan(trailing)
        )
        self.assertTrue(
            any("cannot contain direct document" in item for item in trailing_findings)
        )

    def test_route_views_cannot_be_nested_below_application_main(self):
        text = self.rendered_template().replace(
            '    <section data-application-route="#/author-route"',
            '    <div>\n    <section data-application-route="#/author-route"',
            1,
        ).replace("    </section>\n  </main>", "    </section>\n    </div>\n  </main>", 1)
        findings = experience_application_check.document_structure_findings(
            self.scan(text)
        )
        self.assertTrue(any("direct child of #application-main" in item for item in findings))

        for mutation in (
            self.rendered_template().replace(
                "  </main>", "    <p>Persistent unmapped UI</p>\n  </main>", 1,
            ),
            self.rendered_template().replace(
                '  <div id="application-announcer"',
                "  <p>Persistent unmapped UI</p>\n  <div id=\"application-announcer\"",
                1,
            ),
            self.rendered_template().replace(
                '<header class="application-shell application-toolbar">',
                '<header class="application-shell application-toolbar">'
                '<p>Persistent unmapped UI</p>',
                1,
            ),
            self.rendered_template().replace(
                ">Theme</button>", ">Delete account</button>", 1,
            ),
            self.rendered_template().replace(
                "<strong>Application acceptance prototype</strong>",
                "<strong> </strong>",
                1,
            ),
        ):
            topology_findings = experience_application_check.document_structure_findings(
                self.scan(mutation)
            )
            self.assertTrue(any(
                "exact closed" in item
                or "exact fixed brand" in item
                or "exact visible labels" in item
                or "direct main route roots" in item
                for item in topology_findings
            ), topology_findings)

    def test_route_roots_and_table_content_are_browser_stable(self):
        for tag in ("p", "table"):
            text = self.rendered_template().replace(
                "<section data-application-route=",
                f"<{tag} data-application-route=",
                1,
            ).replace("</section>", f"</{tag}>", 1)
            scanner = self.scan(text)
            findings = experience_application_check.document_structure_findings(
                scanner
            )
            contract = experience_application_check.parse_contract(
                scanner, findings
            )
            experience_application_check.validate_contract(
                contract, {}, {"author-experience"}, scanner, [], findings,
                authoring=True,
            )
            self.assertTrue(any(
                "browser-stable" in item for item in findings
            ), (tag, findings))

        valid_table = self.scan(
            "<table><caption>Summary</caption><tbody><tr><td>Value</td>"
            "</tr></tbody></table>"
        )
        self.assertEqual(valid_table.parser_reparenting_risks, [])
        unstable_table = self.scan("<table><button>Moved</button></table>")
        self.assertIn("table>button", unstable_table.parser_reparenting_risks)
        for markup, risk in (
            ("<li>Orphan</li>", "document>li"),
            ("<legend>Orphan</legend>", "document>legend"),
            ("<figcaption>Orphan</figcaption>", "document>figcaption"),
            ("<dt>Orphan</dt>", "document>dt"),
            ("<option>Orphan</option>", "document>option"),
            ("<ul><button>Moved</button></ul>", "ul>button"),
            ("<button><div>Block</div></button>", "button>div"),
            ("<span><section>Block</section></span>", "span>section"),
            ("<label><label>Nested</label></label>", "label>label"),
            ("<address><h2>Heading</h2></address>", "address>h2"),
            ("<header><footer>Nested</footer></header>", "header/footer>footer"),
            ("<footer><header>Nested</header></footer>", "header/footer>header"),
            (
                "<figure><p>Body</p><figcaption>Caption</figcaption>"
                "<p>After caption</p></figure>",
                "figure>figcaption-order",
            ),
            (
                "<fieldset><p>Body</p><legend>Late</legend></fieldset>",
                "fieldset>legend-order",
            ),
        ):
            with self.subTest(markup=markup):
                self.assertIn(risk, self.scan(markup).parser_reparenting_risks)
        stable_lists = self.scan(
            "<ul><li>One</li></ul><dl><dt>Term</dt><dd>Meaning</dd></dl>"
            "<fieldset><legend>Group</legend><p>Body</p></fieldset>"
            "<figure><figcaption>Caption</figcaption><p>Body</p></figure>"
        )
        self.assertEqual(stable_lists.parser_reparenting_risks, [])
        foreign_button = self.scan(
            '<svg><button type="button" data-application-action="toggle-pressed">'
            'Not an HTML button</button></svg>'
        )
        self.assertTrue(any(
            item.startswith("foreign-content>button")
            for item in foreign_button.parser_reparenting_risks
        ))

        nested_heading = self.rendered_template().replace(
            "Replace this route with the approved Experience set",
            "<h2>Visible route label</h2>",
            1,
        )
        nested_scanner = self.scan(nested_heading)
        self.assertIn(
            "heading>heading", nested_scanner.parser_reparenting_risks
        )
        self.assertTrue(any(
            "browser-stable content models" in item
            for item in experience_application_check.document_structure_findings(
                nested_scanner
            )
        ))

        paragraph_autoclose = self.rendered_template().replace(
            "<strong>Application acceptance prototype</strong>",
            '<p id="theme-name"><div>Theme name</div></p>',
            1,
        ).replace(
            'aria-label="Toggle color theme"',
            'aria-labelledby="theme-name"',
            1,
        )
        paragraph_scanner = self.scan(paragraph_autoclose)
        self.assertIn("p>div", paragraph_scanner.parser_reparenting_risks)
        self.assertTrue(any(
            "browser-stable content models" in item
            for item in experience_application_check.document_structure_findings(
                paragraph_scanner
            )
        ))

        second_main = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<main><p data-private>This file is the only visual Experience implementation.</p></main>",
            1,
        )
        self.assertTrue(any(
            "exact fixed main#application-main" in item
            for item in experience_application_check.document_structure_findings(
                self.scan(second_main)
            )
        ))

    def test_contract_rejects_wrong_primitive_types_without_coercion(self):
        scanner = self.scan(self.rendered_template())
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        contract["schema_version"] = True
        contract["entry_route"] = 7
        contract["routes"][0]["label"] = 8
        contract["routes"][0]["transitions"] = [{
            "transition_ref": "author-experience:TRN-001@r1",
            "target": 9,
            "outcome": "ordinary",
            "preserve_context": [],
            "return_route": "",
        }]
        contract["simulations"] = [{
            "simulation_id": 10,
            "source": "#/author-route",
            "outcome": "ordinary",
            "target": "#/author-route",
            "return_route": "#/author-route",
        }]
        experience_application_check.validate_contract(
            contract,
            {},
            {"author-experience"},
            scanner,
            [],
            findings,
            authoring=True,
        )
        for suffix, primitive in (
            ("schema_version", "integer"),
            ("entry_route", "string"),
            ("routes[0].label", "string"),
            ("routes[0].transitions[0].target", "string"),
            ("simulations[0].simulation_id", "string"),
        ):
            self.assertTrue(
                any(f"{suffix} must be an exact JSON {primitive}" in item for item in findings),
                (suffix, findings),
            )

    def test_contract_route_announcement_labels_are_globally_unique(self):
        scanner = self.scan(self.rendered_template())
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        duplicate = json.loads(json.dumps(contract["routes"][0]))
        duplicate["route"] = "#/duplicate-label"
        duplicate["label"] = "  VISIBLE   ROUTE   LABEL  "
        contract["routes"][0]["label"] = "Visible route label"
        duplicate["transitions"] = []
        contract["routes"].append(duplicate)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings,
            authoring=True,
        )
        self.assertTrue(any(
            "duplicates normalized route label" in item for item in findings
        ), findings)

        spoof_findings: list[str] = []
        spoof_contract = experience_application_check.parse_contract(
            scanner, spoof_findings,
        )
        spoof_contract["routes"][0]["label"] = "Settings"
        spoof = json.loads(json.dumps(spoof_contract["routes"][0]))
        spoof["route"] = "#/spoofed-label"
        spoof["label"] = "Set\u200btings"
        spoof["transitions"] = []
        spoof_contract["routes"].append(spoof)
        experience_application_check.validate_contract(
            spoof_contract, {}, {"author-experience"}, scanner, [],
            spoof_findings, authoring=True,
        )
        self.assertTrue(any(
            "without invisible or control code points" in item
            for item in spoof_findings
        ), spoof_findings)

        for invisible in (
            "\u115f", "\u1160", "\u17b4", "\u17b5", "\u2800",
            "\u3164", "\uffa0", "\U000e0100",
            "\u00a0", "\u2003", "\u3000",
        ):
            self.assertTrue(
                experience_application_check.forbidden_label_codepoint(
                    invisible
                ),
                f"U+{ord(invisible):04X}",
            )
            self.assertFalse(
                experience_application_check.has_visible_content(invisible),
                f"U+{ord(invisible):04X}",
            )
            invisible_text = self.rendered_template().replace(
                "Replace this route with the approved Experience set",
                invisible,
            )
            invisible_scanner = self.scan(invisible_text)
            invisible_findings: list[str] = []
            invisible_contract = experience_application_check.parse_contract(
                invisible_scanner, invisible_findings,
            )
            experience_application_check.validate_contract(
                invisible_contract, {}, {"author-experience"},
                invisible_scanner, [], invisible_findings, authoring=True,
            )
            self.assertTrue(any(
                "without invisible or control code points" in item
                or "visible heading whose exact text matches" in item
                for item in invisible_findings
            ), (f"U+{ord(invisible):04X}", invisible_findings))

    def test_application_map_rejects_wrong_primitive_types_without_coercion(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "checkout"
            target = package / "artifacts/application-map.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({
                "schema_version": True,
                "application_path": 3,
                "experience_id": [],
                "bindings": [{
                    "record_ref": 4,
                    "entries": [{"route": 5, "state_class": 6}],
                }],
            }), encoding="utf-8")
            _normalized, findings = experience_application_check.load_application_map(package)
            for suffix, primitive in (
                ("schema_version", "integer"),
                ("application_path", "string"),
                ("experience_id", "string"),
                ("bindings[0].record_ref", "string"),
                ("bindings[0].entries[0].route", "string"),
                ("bindings[0].entries[0].state_class", "string"),
            ):
                self.assertTrue(
                    any(f"{suffix} must be an exact JSON {primitive}" in item for item in findings),
                    (suffix, findings),
                )

    def test_external_json_rejects_non_scalar_unicode_before_hashing(self):
        with self.assertRaisesRegex(ValueError, "Unicode scalar"):
            experience_application_check.canonical({"route": "\ud800"})
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "demo"
            target = package / "artifacts/application-map.json"
            target.parent.mkdir(parents=True)
            target.write_text(
                '{"schema_version":2,'
                '"application_path":"experience-design/artifacts/application.html",'
                '"experience_id":"demo","bindings":[{"record_ref":"demo:SCR-001@r1",'
                '"entries":[{"route":"\\ud800","state_class":"ordinary"}]}]}',
                encoding="utf-8",
            )
            value, findings = experience_application_check.load_application_map(
                package
            )
            self.assertEqual(value, {})
            self.assertTrue(any("Unicode scalar" in item for item in findings))

            root = Path(raw) / "experience-design"
            ledger = root / "_ledger/application-revisions.json"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(
                '{"schema_version":2,"revisions":["\\ud800"]}',
                encoding="utf-8",
            )
            rows, ledger_findings = (
                experience_application_check.verified_application_ledger(root)
            )
            self.assertEqual(rows, [])
            self.assertTrue(any(
                "Unicode scalar" in item for item in ledger_findings
            ))

        invalid_contract = self.rendered_template().replace(
            '"label": "Replace this route with the approved Experience set"',
            '"label": "\\ud800"',
            1,
        )
        scanner = self.scan(invalid_contract)
        contract_findings: list[str] = []
        contract = experience_application_check.parse_contract(
            scanner, contract_findings,
        )
        self.assertEqual(contract, {})
        self.assertTrue(any(
            "Unicode scalar" in item for item in contract_findings
        ))
        deeply_nested = "[" * 500 + "0" + "]" * 500
        with self.assertRaisesRegex(ValueError, "nesting depth"):
            experience_application_check.strict_json_loads(deeply_nested)
        with self.assertRaisesRegex(ValueError, "nesting depth"):
            experience_compile.strict_json_loads(deeply_nested)
        with self.assertRaisesRegex(ValueError, "Unicode scalar"):
            experience_compile.strict_json_loads('{"x":"\\ud800"}')
        with self.assertRaisesRegex(ValueError, "Unicode scalar"):
            experience_compile.canonical({"x": "\ud800"})
        nested_value: object = "leaf"
        for _index in range(70):
            nested_value = [nested_value]
        with self.assertRaisesRegex(ValueError, "depth or node limit"):
            experience_application_check.canonical(nested_value)

    def test_keyboard_focus_does_not_make_a_div_an_action_control(self):
        text = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<div tabindex="0" data-application-action="toggle-theme">Fake</div>',
            1,
        )
        scanner = self.scan(text)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract,
            {},
            {"author-experience"},
            scanner,
            [],
            findings,
            authoring=True,
        )
        self.assertIn(
            "application controls must use native actionable HTML elements",
            findings,
        )

    def test_token_reference_cannot_hide_a_mixed_hard_coded_css_value(self):
        tokens = {"--catalog-border", "--catalog-content-width"}
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { border: 1px solid var(--catalog-border); }", tokens
            ),
            {"border"},
        )
        for reset_shorthand in (
            ".card { border: var(--catalog-border); }",
            ".card { column-rule: var(--catalog-border); }",
        ):
            property_name = reset_shorthand.split("{")[1].split(":")[0].strip()
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    reset_shorthand, tokens,
                ),
                {property_name},
                reset_shorthand,
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { padding: calc(var(--catalog-border) + var(--catalog-border)); }",
                tokens,
            ),
            {"padding"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { width: calc(var(--catalog-border) - var(--catalog-border)); }",
                tokens,
            ),
            {"width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { width: var(--catalog-content-width); }", tokens,
            ),
            {"width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".application-shell { width: var(--catalog-border); }", tokens,
            ),
            {"width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "body { position: fixed; touch-action: none; }", tokens,
            ),
            {"position", "touch-action"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "main { zoom: 0; }", tokens,
            ),
            {"zoom"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { background-image: linear-gradient(#f00, #00f); "
                "border-top-color: #0f0; width: 347px; }",
                tokens,
            ),
            {"background-image", "border-top-color", "width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { display: flex; flex-wrap: wrap; position: relative; "
                "width: var(--catalog-border); }",
                tokens,
            ),
            {"position", "width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { filter: none; user-select: initial; "
                "color-scheme: initial; }",
                tokens,
            ),
            {"color-scheme", "filter", "user-select"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { display: contents; width: var(--catalog-border) !important; }",
                tokens,
            ),
            {"display", "width"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "button, [data-application-route] { display: none; "
                "visibility: hidden; pointer-events: none; }",
                tokens,
            ),
            {"display", "pointer-events", "visibility"},
        )
        for selector, declaration, property_name in (
            ("body", "width: var(--catalog-content-width)", "width"),
            ("[data-application-route]", "min-width: var(--catalog-content-width)", "min-width"),
            ("img", "width: var(--catalog-content-width)", "width"),
            ("body", "min-inline-size: var(--catalog-content-width)", "min-inline-size"),
            ("button", "display: inline", "display"),
            ("body", "white-space: nowrap", "white-space"),
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f"{selector} {{ {declaration}; }}", tokens,
                ),
                {property_name},
                (selector, declaration),
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                r":root { --catalog\2d background: transparent; }",
                {"--catalog-background"},
            ),
            {"--catalog-background"},
        )
        color_tokens = {
            "--catalog-background", "--catalog-surface",
            "--catalog-foreground", "--catalog-border", "--catalog-accent",
        }
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "body { color: var(--catalog-background); }", color_tokens,
            ),
            {"color"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { background: var(--catalog-foreground); }", color_tokens,
            ),
            {"background"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".card { color: var(--catalog-foreground); "
                "background: var(--catalog-surface); "
                "border-color: var(--catalog-border); }",
                color_tokens,
            ),
            set(),
        )
        for shared_token in ("accent", "success", "warning", "error"):
            token_name = f"--catalog-{shared_token}"
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    ".card { color: var(" + token_name + "); "
                    "background: var(" + token_name + "); }",
                    {token_name},
                ),
                {"background", "color"},
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".icon { fill: var(--catalog-accent); "
                "background-color: var(--catalog-accent); }",
                {"--catalog-accent"},
            ),
            {"background-color", "fill"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "[data-application-route] { background: var(--catalog-accent); "
                "-webkit-text-fill-color: var(--catalog-accent); }",
                {"--catalog-accent"},
            ),
            {"background", "-webkit-text-fill-color"},
        )
        for split_rules in (
            "[data-application-route] { color: var(--catalog-accent); } "
            "[data-application-route] { background: var(--catalog-accent); }",
            ".a, [data-application-route] { color: var(--catalog-accent); } "
            "[data-application-route], .b { background: var(--catalog-accent); }",
            "@media (min-width: 0px) { [data-application-route] { "
            "color: var(--catalog-accent); } } "
            "@media (min-width: 0px) { [data-application-route] { "
            "background: var(--catalog-accent); } }",
            ".x { background: var(--catalog-accent); } "
            ".x.x { color: var(--catalog-accent); }",
            ".x { background: var(--catalog-accent); } "
            ":where(.x) { color: var(--catalog-accent); }",
            ".x { background: var(--catalog-accent); } "
            ".x:hover { color: var(--catalog-accent); }",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    split_rules, {"--catalog-accent"},
                ),
                {"background", "color"},
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "button { background: var(--catalog-accent); } "
                "a { color: var(--catalog-accent); }",
                {"--catalog-accent"},
            ),
            {"background"},
        )
        for nested in (
            "[data-application-route] { color: var(--catalog-accent); "
            "@media (min-width: 0px) { background: var(--catalog-accent); } }",
            ".parent { & { color: var(--catalog-accent); } }",
            ".parent { @supports (display: block) { color: "
            "var(--catalog-accent); } }",
        ):
            self.assertIn(
                "invalid-css",
                experience_application_check.hard_coded_author_properties(
                    nested, {"--catalog-accent"},
                ),
            )
        for property_name in ("accent-color", "caret-color", "fill", "stroke"):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".cue {{ {property_name}: transparent; }}", set(),
                ),
                {property_name},
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                ".surface { background: transparent; }", set(),
            ),
            {"background"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "body { cursor: none; }", set(),
            ),
            {"cursor"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "button { outline-color: var(--catalog-accent); }", color_tokens,
            ),
            {"outline-color"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                'input[type="checkbox"] { appearance: none; '
                'background: transparent; border: none; }',
                color_tokens,
            ),
            {"appearance", "background", "border"},
        )
        for property_name, value in (
            ("accent-color", "initial"),
            ("background", "none"),
            ("background", "transparent"),
            ("background-color", "initial"),
            ("background-color", "transparent"),
            ("background-image", "none"),
            ("border", "initial"),
            ("color", "inherit"),
            ("color", "initial"),
            ("color", "revert"),
            ("color", "unset"),
            ("fill", "initial"),
            ("stroke", "revert-layer"),
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".visual {{ {property_name}: {value}; }}", color_tokens,
                ),
                {property_name},
                (property_name, value),
            )
        for hidden_display in (
            "contents", "none", "ruby", "table-column", "table-column-group",
            "inline", "list-item", "table", "inherit", "initial", "revert",
            "revert-layer", "unset",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f"[data-application-route] {{ display: {hidden_display}; }}",
                    color_tokens,
                ),
                {"display"},
            )
        for visible_display in ("block", "flow-root", "inline-block"):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".layout {{ display: {visible_display}; }}", color_tokens,
                ),
                set(),
            )
        for unsafe_layout in (
            ".layout { display: flex; }",
            ".layout { display: inline-flex; }",
            ".layout { display: grid; grid-auto-flow: column; }",
            ".layout { display: inline-grid; }",
        ):
            self.assertIn(
                "display",
                experience_application_check.hard_coded_author_properties(
                    unsafe_layout, color_tokens,
                ),
                unsafe_layout,
            )
        for wrapped_layout in (
            ".layout { display: flex; flex-wrap: wrap; }",
            ".layout { display: inline-flex; flex-wrap: wrap; }",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    wrapped_layout, color_tokens,
                ),
                set(),
                wrapped_layout,
            )
        for overflow_value in (
            "hidden", "clip", "hidden auto", "auto hidden", "clip auto",
            "auto clip", "visible hidden", "auto overlay",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".heading {{ overflow: {overflow_value}; }}", color_tokens,
                ),
                {"overflow"},
                overflow_value,
            )
        for overflow_declaration in (
            "overflow: auto", "overflow: visible auto", "overflow: scroll",
            "overflow-x: auto", "overflow-y: visible",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".collection {{ {overflow_declaration}; }}", color_tokens,
                ),
                set(),
                overflow_declaration,
            )
        for property_name, value in (
            ("flex-direction", "row-reverse"),
            ("flex-direction", "definitely-invalid"),
            ("flex-wrap", "nowrap"),
            ("flex-wrap", "initial"),
            ("flex-wrap", "wrap-reverse"),
            ("grid-auto-flow", "dense"),
            ("grid-auto-flow", "row dense"),
            ("grid-area", "footer"),
            ("grid-column", "2"),
            ("grid-row-start", "3"),
            ("grid-template-areas", '"a b"'),
            ("text-transform", "capitalize"),
            ("text-transform", "full-width"),
            ("text-transform", "lowercase"),
            ("text-transform", "uppercase"),
            ("white-space", "break-spaces"),
            ("white-space", "pre"),
            ("white-space", "pre-line"),
            ("white-space", "pre-wrap"),
            ("white-space", "nowrap"),
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".layout {{ {property_name}: {value}; }}", color_tokens,
                ),
                {property_name},
                (property_name, value),
            )
        for declaration in (
            "flex-direction: row", "flex-direction: column",
            "flex-wrap: wrap",
            "align-items: center", "justify-content: space-between",
            "text-transform: none",
            "white-space: normal",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".layout {{ {declaration}; }}", color_tokens,
                ),
                set(),
                declaration,
            )
        comment_string = (
            '[data-application-route] { content: "/*"; display: none; } /* */'
        )
        self.assertIn(
            'content: "/*"; display: none',
            experience_application_check.normalized_css(comment_string),
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                comment_string, color_tokens,
            ),
            {"content", "display"},
        )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                '.safe { content: "escaped \\\"/*"; } /* display: none; */',
                color_tokens,
            ),
            {"content"},
        )

        design_tokens = set(
            experience_application_check.REQUIRED_APPLICATION_ROOT_TOKENS
        )
        for styles, expected in (
            (
                "img { box-sizing: content-box; padding-inline: "
                "var(--catalog-space-3xl); }",
                {"box-sizing"},
            ),
            (
                ".application-shell { margin-inline: "
                "var(--catalog-space-3xl); }",
                {"margin-inline"},
            ),
            (
                "main { margin-left: var(--catalog-space-3xl); }",
                {"margin-left"},
            ),
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    styles, design_tokens,
                ),
                expected,
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                '.broken { content: "unterminated; }', color_tokens,
            ),
            {"invalid-css"},
        )
        deeply_nested_css = (
            "@media (min-width:0px){" * 32
            + "p{display:block}"
            + "}" * 32
        )
        self.assertIn(
            "invalid-css",
            experience_application_check.hard_coded_author_properties(
                deeply_nested_css, color_tokens,
            ),
        )
        for generated_content in (
            'content: "Spoof"', 'quotes: "[" "]"',
            "counter-increment: route", "counter-reset: route",
            "counter-set: route 1", "forced-color-adjust: none",
        ):
            property_name = generated_content.split(":", 1)[0]
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f"#author-route-title::before {{ {generated_content}; }}",
                    color_tokens,
                ),
                {property_name},
                generated_content,
            )
        self.assertEqual(
            experience_application_check.hard_coded_author_properties(
                "h1 { direction: rtl; unicode-bidi: bidi-override; }",
                color_tokens,
            ),
            {"direction", "unicode-bidi"},
        )
        semantic_tokens = {
            "--catalog-border-width", "--catalog-content-width",
            "--catalog-font-body", "--catalog-motion-easing",
            "--catalog-motion-fast", "--catalog-radius-md",
            "--catalog-scroll-offset", "--catalog-type-display-weight",
        }
        for property_name in (
            "animation-duration", "border-radius", "font-weight",
            "letter-spacing", "text-decoration-thickness", "text-indent",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".wrong {{ {property_name}: var(--catalog-content-width); }}",
                    semantic_tokens,
                ),
                {property_name},
            )
        for declaration in (
            "animation-duration: var(--catalog-motion-fast)",
            "animation-timing-function: var(--catalog-motion-easing)",
            "border-radius: var(--catalog-radius-md)",
            "border-width: var(--catalog-border-width)",
            "font-family: var(--catalog-font-body)",
            "font-weight: var(--catalog-type-display-weight)",
            "scroll-margin-top: var(--catalog-scroll-offset)",
        ):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".right {{ {declaration}; }}", semantic_tokens,
                ),
                set(),
                declaration,
            )

    def test_application_tokens_require_complete_light_and_dark_contracts(self):
        template = experience_application_check.template_text()
        self.assertIn(
            "min-block-size: var(--catalog-touch-target, 2.75rem) !important",
            template,
        )
        self.assertIn("max-inline-size: 100% !important", template)
        findings = experience_application_check.application_design_token_findings(
            ":root { --catalog-background: #fff; }"
        )
        self.assertTrue(any(
            "root token contract is incomplete" in item for item in findings
        ))
        self.assertTrue(any(
            "dark-theme token contract is incomplete" in item for item in findings
        ))
        self.assertTrue(any(
            "missing responsive override" in item for item in findings
        ))
        self.assertEqual(
            experience_application_check.application_design_token_findings(
                APPLICATION_TOKEN_CSS
            ),
            [],
        )
        missing_responsive = APPLICATION_TOKEN_CSS.split(
            "@media (max-width: 768px)", 1
        )[0]
        self.assertTrue(any(
            "missing responsive override" in item
            for item in experience_application_check.application_design_token_findings(
                missing_responsive
            )
        ))
        missing_responsive_declaration = APPLICATION_TOKEN_CSS.replace(
            "    --catalog-type-display-size: 2.25rem;\n", "", 1,
        )
        self.assertTrue(any(
            "responsive override contract" in item
            for item in experience_application_check.application_design_token_findings(
                missing_responsive_declaration
            )
        ))
        root_block, after_root = APPLICATION_TOKEN_CSS.split(
            '[data-catalog-theme="dark"]', 1,
        )
        dark_body, responsive_body = after_root.split(
            "@media (max-width: 768px)", 1,
        )
        dark_block = '[data-catalog-theme="dark"]' + dark_body
        responsive_block = "@media (max-width: 768px)" + responsive_body
        for reordered in (
            dark_block + root_block + responsive_block,
            responsive_block + root_block + dark_block,
            root_block + ":root {}\n" + dark_block + responsive_block,
            root_block + dark_block
            + '[data-catalog-theme="dark"] {}\n' + responsive_block,
        ):
            self.assertTrue(any(
                "canonical scope order/count must be root, dark, responsive"
                in item
                for item in experience_application_check.application_design_token_findings(
                    reordered
                )
            ), reordered)
        for injected_css in (
            "body { display: none; }",
            "[data-application-route] { display: none !important; }",
            "button { pointer-events: none; }",
            'html[data-privacy="masked"] [data-private] { filter: none !important; }',
            "@media (max-width: 768px) { body { display: none; } }",
            '@import url("https://example.invalid/tokens.css");',
            ":root { color: transparent; }",
        ):
            self.assertTrue(
                experience_application_check.application_design_token_findings(
                    APPLICATION_TOKEN_CSS + "\n" + injected_css
                ),
                injected_css,
            )
        empty_values = re.sub(
            r"(--[a-zA-Z0-9_-]+)\s*:[^;{}]*;",
            r"\1: ;",
            APPLICATION_TOKEN_CSS,
        )
        empty_findings = (
            experience_application_check.application_design_token_findings(
                empty_values
            )
        )
        self.assertTrue(any(
            "root tokens need one concrete effective value" in item
            for item in empty_findings
        ))
        self.assertTrue(any(
            "dark-theme tokens need one concrete effective value" in item
            for item in empty_findings
        ))
        unresolved = APPLICATION_TOKEN_CSS.replace(
            "--catalog-background: #fff;",
            "--catalog-background: var(--missing);",
            1,
        )
        self.assertTrue(any(
            "root tokens need one concrete effective value" in item
            for item in experience_application_check.application_design_token_findings(
                unresolved
            )
        ))
        collapsed = (
            APPLICATION_TOKEN_CSS
            .replace("--catalog-content-width: 72rem;", "--catalog-content-width: 0px;")
            .replace("--catalog-type-display-size: 3rem;", "--catalog-type-display-size: 0px;")
            .replace("--catalog-line-height: 1.5;", "--catalog-line-height: 0;")
            .replace("--catalog-focus-width: 2px;", "--catalog-focus-width: 0px;")
        )
        collapsed_findings = (
            experience_application_check.application_design_token_findings(
                collapsed
            )
        )
        self.assertTrue(any(
            "root token values violate canonical semantic constraints" in item
            and "--catalog-content-width" in item
            and "--catalog-focus-width" in item
            and "--catalog-line-height" in item
            and "--catalog-type-display-size" in item
            for item in collapsed_findings
        ))
        for token_name, original, oversized in (
            ("gutter", "1.5rem", "999999rem"),
            ("focus-width", "2px", "999999rem"),
            ("focus-offset", "3px", "999999rem"),
            ("type-display-size", "3rem", "999999rem"),
            ("motion-fast", "150ms", "999999s"),
            ("shadow-sm", "0 1px 2px rgba(0, 0, 0, 0.05)", "0 999999rem 999999rem #000"),
        ):
            unbounded = APPLICATION_TOKEN_CSS.replace(
                f"--catalog-{token_name}: {original};",
                f"--catalog-{token_name}: {oversized};",
                1,
            )
            self.assertTrue(any(
                "root token values violate canonical semantic constraints" in item
                and f"--catalog-{token_name}" in item
                for item in experience_application_check.application_design_token_findings(
                    unbounded
                )
            ), token_name)
        no_contrast = APPLICATION_TOKEN_CSS.replace(
            "--catalog-foreground: #171717;",
            "--catalog-foreground: #fff;",
            1,
        )
        self.assertTrue(any(
            "root token contrast is below 4.5" in item
            for item in experience_application_check.application_design_token_findings(
                no_contrast
            )
        ))
        for token_name, original_value in (
            ("accent", "#2563eb"),
            ("success", "#166534"),
            ("warning", "#92400e"),
            ("error", "#b91c1c"),
        ):
            invisible_state_text = APPLICATION_TOKEN_CSS.replace(
                f"--catalog-{token_name}: {original_value};",
                f"--catalog-{token_name}: #fff;",
                1,
            )
            self.assertTrue(any(
                "root token contrast is below 4.5" in item
                and f"--catalog-{token_name}" in item
                for item in experience_application_check.application_design_token_findings(
                    invisible_state_text
                )
            ), token_name)
        for state_token in ("accent", "success", "warning", "error"):
            self.assertEqual(
                experience_application_check.hard_coded_author_properties(
                    f".state {{ background: var(--catalog-{state_token}); }}",
                    {f"--catalog-{state_token}"},
                ),
                {"background"},
                state_token,
            )
        invisible_surface_focus = (
            APPLICATION_TOKEN_CSS
            .replace("--catalog-surface: #f4f4f4;", "--catalog-surface: #757575;", 1)
            .replace("--catalog-focus: #171717;", "--catalog-focus: #757575;", 1)
            .replace("--catalog-foreground: #171717;", "--catalog-foreground: #000;", 1)
        )
        self.assertTrue(any(
            "root token contrast is below 3" in item
            and "--catalog-focus / --catalog-surface" in item
            for item in experience_application_check.application_design_token_findings(
                invisible_surface_focus
            )
        ))
        invisible_boundaries = (
            APPLICATION_TOKEN_CSS
            .replace("--catalog-border: #737373;", "--catalog-border: #fff;", 1)
            .replace("--catalog-border: #a3a3a3;", "--catalog-border: #171717;", 1)
        )
        boundary_findings = (
            experience_application_check.application_design_token_findings(
                invisible_boundaries
            )
        )
        for scope in ("root", "dark-theme"):
            for surface in ("--catalog-background", "--catalog-surface"):
                self.assertTrue(any(
                    f"{scope} token contrast is below 3" in item
                    and f"--catalog-border / {surface}" in item
                    for item in boundary_findings
                ), (scope, surface, boundary_findings))
        transparent = APPLICATION_TOKEN_CSS.replace(
            "--catalog-background: #fff;",
            "--catalog-background: transparent;",
            1,
        )
        self.assertTrue(any(
            "root token values violate canonical semantic constraints" in item
            and "--catalog-background" in item
            for item in experience_application_check.application_design_token_findings(
                transparent
            )
        ))
        for original, invalid in (
            (
                "ui-sans-serif, system-ui, sans-serif",
                "url(data:text/css,x)",
            ),
            (
                "ui-sans-serif, system-ui, sans-serif",
                '"</style><img src=https://example.invalid/x>"',
            ),
            ("--catalog-motion-easing: ease-out;", "--catalog-motion-easing: potato;"),
            (
                "--catalog-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);",
                "--catalog-shadow-sm: potato;",
            ),
        ):
            invalid_property_token = APPLICATION_TOKEN_CSS.replace(
                original, invalid, 1,
            )
            self.assertTrue(any(
                "token values violate canonical semantic constraints" in item
                or "tokens need one concrete effective value" in item
                for item in experience_application_check.application_design_token_findings(
                    invalid_property_token
                )
            ), invalid)
        for valid_easing in (
            "linear", "cubic-bezier(0.25, 0.1, 0.25, 1)",
            "steps(2, jump-none)",
        ):
            self.assertFalse(any(
                "--catalog-motion-easing" in item
                for item in experience_application_check.application_design_token_findings(
                    APPLICATION_TOKEN_CSS.replace(
                        "--catalog-motion-easing: ease-out;",
                        f"--catalog-motion-easing: {valid_easing};",
                        1,
                    )
                )
            ), valid_easing)
        conditional = "@media (max-width: 0px) {\n" + APPLICATION_TOKEN_CSS + "}\n"
        conditional_findings = (
            experience_application_check.application_design_token_findings(
                conditional
            )
        )
        self.assertTrue(any(
            "only exact root, dark-theme and canonical responsive" in item
            for item in conditional_findings
        ))
        self.assertTrue(any(
            "root token contract is incomplete" in item
            for item in conditional_findings
        ))
        self.assertTrue(any(
            "dark-theme token contract is incomplete" in item
            for item in conditional_findings
        ))

    def test_root_state_and_route_label_remain_accessibly_bound(self):
        changed = self.rendered_template().replace(
            '<html lang="en" data-theme="light" data-catalog-theme="light" data-privacy="visible">',
            '<html lang="en" data-theme="dark" data-catalog-theme="dark" data-privacy="masked">',
            1,
        )
        scanner = self.scan(changed)
        required = experience_application_check.required_experience_findings(
            changed, scanner,
        )
        self.assertIn("application needs accessible theme control", required)
        self.assertIn("application needs privacy masking control", required)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings,
            authoring=True,
        )
        self.assertTrue(any("initial state" in item for item in findings))

        for old, new in (
            ('<html lang="en"', '<html lang="en-1"'),
            (
                '<h1 id="author-route-title">',
                '<h1 id="author-route-title" lang="!" dir="banana">',
            ),
            (
                '<h1 id="author-route-title">',
                '<h1 id="author-route-title" xml:lang="en">',
            ),
        ):
            language_findings = experience_application_check.document_structure_findings(
                self.scan(self.rendered_template().replace(old, new, 1))
            )
            self.assertTrue(any(
                "closed BCP47 and exact direction grammar" in item
                for item in language_findings
            ), (old, new, language_findings))
        valid_locale = self.rendered_template().replace(
            '<h1 id="author-route-title">',
            '<h1 id="author-route-title" lang="zh-Hant-TW" dir="ltr">',
            1,
        )
        self.assertFalse(any(
            "closed BCP47 and exact direction grammar" in item
            for item in experience_application_check.document_structure_findings(
                self.scan(valid_locale)
            )
        ))

        invalid_root = self.scan(self.rendered_template().replace(
            'data-theme="light"', 'data-theme="banana"', 1,
        ))
        self.assertTrue(any(
            "html root needs" in item
            for item in experience_application_check.document_structure_findings(
                invalid_root
            )
        ))

        mismatched_catalog_theme = self.scan(
            self.rendered_template().replace(
                'data-catalog-theme="light"', 'data-catalog-theme="dark"', 1
            )
        )
        self.assertTrue(any(
            "synchronized exact application/catalog theme" in item
            for item in experience_application_check.document_structure_findings(
                mismatched_catalog_theme
            )
        ))

        invalid_aria_disabled = self.scan(
            self.rendered_template().replace(
                "<p data-private>", '<p data-private aria-disabled="maybe">', 1
            )
        )
        self.assertTrue(any(
            "aria-disabled must use the exact true/false tokens" in item
            for item in experience_application_check.document_structure_findings(
                invalid_aria_disabled
            )
        ))

        hidden_announcer = self.scan(
            self.rendered_template().replace(
                'id="application-announcer" role="status"',
                'id="application-announcer" role="status" aria-hidden="true"',
                1,
            )
        )
        self.assertTrue(any(
            "assistive-technology-reachable" in item
            for item in experience_application_check.document_structure_findings(
                hidden_announcer
            )
        ))

        nested_announcer = self.scan(
            self.rendered_template().replace(
                '<div id="application-announcer" role="status" aria-live="polite" class="application-shell"></div>',
                '<div id="application-announcer" role="status" aria-live="polite" '
                'class="application-shell"><button type="button">Lost</button></div>',
                1,
            )
        )
        self.assertTrue(any(
            "element-free fixed-runtime text sink" in item
            for item in experience_application_check.document_structure_findings(
                nested_announcer
            )
        ))

        empty_label = self.rendered_template().replace(
            '<h1 id="author-route-title">Replace this route with the approved Experience set</h1>',
            '<h1 id="author-route-title"></h1>',
            1,
        )
        empty_scanner = self.scan(empty_label)
        label_findings: list[str] = []
        empty_contract = experience_application_check.parse_contract(
            empty_scanner, label_findings,
        )
        experience_application_check.validate_contract(
            empty_contract, {}, {"author-experience"}, empty_scanner, [],
            label_findings, authoring=True,
        )
        self.assertTrue(any(
            "visible heading whose exact text matches" in item
            for item in label_findings
        ))

        drifted_label = self.rendered_template().replace(
            "Replace this route with the approved Experience set</h1>",
            "Destructive account action</h1>",
            1,
        )
        drifted_scanner = self.scan(drifted_label)
        drifted_findings: list[str] = []
        drifted_contract = experience_application_check.parse_contract(
            drifted_scanner, drifted_findings
        )
        experience_application_check.validate_contract(
            drifted_contract, {}, {"author-experience"}, drifted_scanner, [],
            drifted_findings, authoring=True,
        )
        self.assertTrue(any(
            "visible heading whose exact text matches" in item
            for item in drifted_findings
        ))
        for aria_label in ("&#x200b;", "Wrong route"):
            overridden_label = self.rendered_template().replace(
                '<h1 id="author-route-title">',
                f'<h1 id="author-route-title" aria-label="{aria_label}">',
                1,
            )
            overridden_scanner = self.scan(overridden_label)
            overridden_findings: list[str] = []
            overridden_contract = experience_application_check.parse_contract(
                overridden_scanner, overridden_findings,
            )
            experience_application_check.validate_contract(
                overridden_contract, {}, {"author-experience"},
                overridden_scanner, [], overridden_findings, authoring=True,
            )
            self.assertTrue(any(
                "visible heading whose exact text matches" in item
                for item in overridden_findings
            ), aria_label)
        route_root_override = self.rendered_template().replace(
            'data-application-state="ordinary" aria-labelledby="author-route-title"',
            'data-application-state="ordinary" aria-labelledby="author-route-title" '
            'aria-label="&#x200b;"',
            1,
        )
        root_override_scanner = self.scan(route_root_override)
        root_override_findings: list[str] = []
        root_override_contract = experience_application_check.parse_contract(
            root_override_scanner, root_override_findings,
        )
        experience_application_check.validate_contract(
            root_override_contract, {}, {"author-experience"},
            root_override_scanner, [], root_override_findings, authoring=True,
        )
        self.assertTrue(any(
            "browser-stable section/article/div root" in item
            for item in root_override_findings
        ))

    def test_theme_toggle_requires_explicit_accessible_state(self):
        text = self.rendered_template().replace(
            'data-application-action="toggle-theme" aria-label="Toggle color theme" '
            'aria-pressed="false"',
            'data-application-action="toggle-theme" aria-label="Toggle color theme"',
            1,
        )
        scanner = self.scan(text)
        self.assertIn(
            "application needs accessible theme control",
            experience_application_check.required_experience_findings(
                text, scanner,
            ),
        )
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract,
            {},
            {"author-experience"},
            scanner,
            [],
            findings,
            authoring=True,
        )
        self.assertIn("toggle-theme needs an explicit aria-pressed state", findings)

        unnamed = self.rendered_template().replace(
            '<button type="button" data-application-action="toggle-theme" '
            'aria-label="Toggle color theme" aria-pressed="false">Theme</button>',
            '<button type="button" data-application-action="toggle-theme" '
            'aria-pressed="false"></button>',
            1,
        )
        unnamed_scanner = self.scan(unnamed)
        self.assertIn(
            "application needs accessible theme control",
            experience_application_check.required_experience_findings(
                unnamed, unnamed_scanner,
            ),
        )
        unnamed_findings: list[str] = []
        unnamed_contract = experience_application_check.parse_contract(
            unnamed_scanner, unnamed_findings,
        )
        experience_application_check.validate_contract(
            unnamed_contract, {}, {"author-experience"}, unnamed_scanner, [],
            unnamed_findings, authoring=True,
        )
        self.assertIn(
            "application controls must expose a non-empty accessible name",
            unnamed_findings,
        )
        for unsafe_label in (
            '<span id="bad-label" aria-label="&#x200b;">Toggle choice</span>',
            '<span id="bad-label"><span aria-label="&#x200b;">'
            "Toggle choice</span></span>",
        ):
            clobbered_name = self.rendered_template().replace(
                '<button type="button" data-application-action="toggle-theme" '
                'aria-label="Toggle color theme" aria-pressed="false">Theme</button>',
                unsafe_label
                + '<button type="button" data-application-action="toggle-theme" '
                'aria-labelledby="bad-label" aria-pressed="false"></button>',
                1,
            )
            clobbered_scanner = self.scan(clobbered_name)
            clobbered_findings: list[str] = []
            clobbered_contract = experience_application_check.parse_contract(
                clobbered_scanner, clobbered_findings,
            )
            experience_application_check.validate_contract(
                clobbered_contract, {}, {"author-experience"},
                clobbered_scanner, [], clobbered_findings, authoring=True,
            )
            self.assertIn(
                "application controls must expose a non-empty accessible name",
                clobbered_findings,
            )
        precedence_override = self.rendered_template().replace(
            '<button type="button" data-application-action="toggle-theme" '
            'aria-label="Toggle color theme" aria-pressed="false">Theme</button>',
            '<span id="bad-label">&#x200b;</span>'
            '<button type="button" data-application-action="toggle-theme" '
            'aria-label="Toggle color theme" aria-labelledby="bad-label" '
            'aria-pressed="false"></button>',
            1,
        )
        precedence_scanner = self.scan(precedence_override)
        precedence_findings: list[str] = []
        precedence_contract = experience_application_check.parse_contract(
            precedence_scanner, precedence_findings,
        )
        experience_application_check.validate_contract(
            precedence_contract, {}, {"author-experience"},
            precedence_scanner, [], precedence_findings, authoring=True,
        )
        self.assertIn(
            "application controls must expose a non-empty accessible name",
            precedence_findings,
        )

    def test_hover_pressed_and_selected_visual_markers_are_mandatory(self):
        text = self.rendered_template()
        self.assertEqual(
            experience_application_check.required_experience_findings(text), []
        )
        for marker, replacement, label in (
            (":hover", ":focus-within", "visible hover behavior"),
            (":active", ":focus-within", "visible pressed behavior"),
            ('[aria-pressed="true"]', '[aria-current="page"]', "visible toggled-pressed behavior"),
            ('[aria-selected="true"]', '[aria-current="page"]', "visible listbox selection behavior"),
        ):
            findings = experience_application_check.required_experience_findings(
                text.replace(marker, replacement, 1)
            )
            self.assertIn(f"application needs {label}", findings)

    def test_listbox_selection_requires_a_keyboard_activatable_button(self):
        base = self.rendered_template()
        control = (
            '<a href="#/author-route" data-application-action="select-option" '
            'role="option" aria-selected="false" data-value="one">One</a>'
        )
        scanner = self.scan(base.replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            + control,
            1,
        ))
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract,
            {},
            {"author-experience"},
            scanner,
            [],
            findings,
            authoring=True,
        )
        self.assertIn(
            "select-option needs a button with role=option, aria-selected and data-value",
            findings,
        )
        button_scanner = self.scan(base.replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<div role="listbox">'
            '<button type="button" data-application-action="select-option" '
            'role="option" aria-selected="false" data-value="one">One</button>'
            '</div>',
            1,
        ))
        button_findings: list[str] = []
        button_contract = experience_application_check.parse_contract(
            button_scanner, button_findings
        )
        experience_application_check.validate_contract(
            button_contract,
            {},
            {"author-experience"},
            button_scanner,
            [],
            button_findings,
            authoring=True,
        )
        self.assertNotIn(
            "select-option needs a button with role=option, aria-selected and data-value",
            button_findings,
        )
        self.assertIn(
            'addEventListener(document, "click"',
            experience_application_check.template_runtime(),
        )
        self.assertIn(
            'action === "select-option"',
            experience_application_check.template_runtime(),
        )

    def test_comments_cannot_satisfy_required_controls_or_privacy_target(self):
        text = self.rendered_template()
        text = re.sub(
            r'\s*<button type="button" data-application-action="toggle-(?:theme|privacy)".*?</button>',
            "",
            text,
            count=2,
        ).replace("data-private", "data-not-private")
        text = text.replace(
            "</body>",
            '<!-- data-application-action="toggle-theme" '
            'data-application-action="toggle-privacy" data-private -->\n</body>',
            1,
        )
        findings = experience_application_check.required_experience_findings(text)
        self.assertIn("application needs accessible theme control", findings)
        self.assertIn("application needs privacy masking control", findings)
        self.assertIn("application needs privacy-masked content", findings)

        no_hover = self.rendered_template().replace(":hover", ":focus-within", 1)
        no_hover = no_hover.replace(
            "</body>", "<!-- <style>:hover { opacity: 1; }</style> -->\n</body>", 1
        )
        self.assertIn(
            "application needs visible hover behavior",
            experience_application_check.required_experience_findings(no_hover),
        )
        parser_differential = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            "<!--><style>body{display:none}</style><!-- -->",
            1,
        )
        self.assertTrue(any(
            "HTML comments and bogus declarations" in item
            for item in experience_application_check.required_experience_findings(
                parser_differential
            )
        ))

    def test_html_end_tag_scanners_follow_browser_token_boundaries(self):
        for tag, pattern in (
            ("script", experience_application_check.SCRIPT_PATTERN),
            ("style", experience_application_check.STYLE_PATTERN),
        ):
            source = f"<{tag}>body</{tag}\t\n data-marker>"
            matches = list(pattern.finditer(source))
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].group("body"), "body")
            self.assertEqual(
                experience_application_check.browser_stable_html_source_findings(
                    f"<html>--!></html>"
                ),
                [
                    "application HTML comments and bogus declarations are forbidden "
                    "for browser-stable parsing"
                ],
            )

    def test_modal_disclosure_search_and_form_topology_fails_closed(self):
        def validate(fragment: str) -> list[str]:
            text = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + fragment,
                1,
            )
            scanner = self.scan(text)
            findings = experience_application_check.document_structure_findings(
                scanner
            )
            contract = experience_application_check.parse_contract(scanner, findings)
            experience_application_check.validate_contract(
                contract,
                {},
                {"author-experience"},
                scanner,
                [],
                findings,
                authoring=True,
            )
            return findings

        fake_modal = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="fake-modal">Open</button>'
            '<div id="fake-modal"></div>'
        )
        self.assertTrue(any("initially closed, visible dialog" in item for item in fake_modal))
        open_dialog = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="open-modal">Open</button>'
            '<dialog id="open-modal" open></dialog>'
        )
        self.assertTrue(any("initially closed, visible dialog" in item for item in open_dialog))
        missing_popup_semantics = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-controls="popup-dialog">Open</button>'
            '<dialog id="popup-dialog" aria-label="Dialog">'
            '<button type="button" data-application-action="close-modal">Close</button>'
            '</dialog>'
        )
        self.assertIn(
            "open-modal needs aria-haspopup=dialog", missing_popup_semantics
        )
        false_modal_semantics = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="false-modal">Open</button>'
            '<dialog id="false-modal" aria-label="Dialog" aria-modal="false">'
            '<button type="button" data-application-action="close-modal">Close</button>'
            '</dialog>'
        )
        self.assertIn(
            "dialogs allow passive naming/description ARIA and optional aria-modal=true only",
            false_modal_semantics,
        )
        same_dialog_description = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="help-modal">Open help</button>'
            '<dialog id="help-modal" aria-labelledby="help-title">'
            '<h2 id="help-title">Help</h2>'
            '<p id="field-help">Use your project name.</p>'
            '<label>Project name <input data-context-key="project" '
            'aria-describedby="field-help"></label>'
            '<button type="button" data-application-action="close-modal">'
            'Close</button></dialog>'
        )
        self.assertFalse(any(
            "passive ARIA descriptions" in item
            for item in same_dialog_description
        ), same_dialog_description)
        dialog_description = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="described-modal">Open</button>'
            '<dialog id="described-modal" aria-labelledby="described-title" '
            'aria-describedby="modal-description">'
            '<h2 id="described-title">Help</h2>'
            '<p id="modal-description">Describe this modal.</p>'
            '<button type="button" data-application-action="close-modal">'
            'Close</button></dialog>'
        )
        self.assertFalse(any(
            "passive ARIA descriptions" in item
            for item in dialog_description
        ), dialog_description)
        unmanaged_close_policy = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="close-policy">Open</button>'
            '<dialog id="close-policy" aria-label="Dialog" closedby="none">'
            '<button type="button" data-application-action="close-modal">Close</button>'
            '</dialog>'
        )
        self.assertTrue(any(
            "unmanaged native invocation" in item
            and "dialog[closedby]" in item
            for item in unmanaged_close_policy
        ))
        outside_close = validate(
            '<button type="button" data-application-action="close-modal">Close</button>'
        )
        self.assertTrue(any("inside one reachable dialog" in item for item in outside_close))
        standalone_option = validate(
            '<button type="button" data-application-action="select-option" '
            'role="option" aria-selected="false" data-value="one">One</button>'
        )
        self.assertTrue(any("inside one reachable listbox" in item for item in standalone_option))
        broken_search = validate(
            '<div data-application-search></div><p data-search-item>One</p>'
        )
        self.assertTrue(any("input[type=search]" in item for item in broken_search))
        aria_only_search = validate(
            '<input type="search" data-application-search aria-label="Search">'
            '<div data-search-item>Valid item</div>'
        )
        self.assertTrue(any(
            "input[type=search]" in item for item in aria_only_search
        ))
        hidden_labelledby_search = validate(
            '<input type="search" data-application-search '
            'aria-labelledby="hidden-search-label">'
            '<div data-search-item>Valid item</div>'
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="label-modal">Open</button>'
            '<dialog id="label-modal" aria-labelledby="modal-label">'
            '<h2 id="modal-label">Labels</h2>'
            '<span id="hidden-search-label">Search</span>'
            '<button type="button" data-application-action="close-modal">'
            'Close</button></dialog>'
        )
        self.assertTrue(any(
            "input[type=search]" in item
            for item in hidden_labelledby_search
        ))
        aria_only_filter = validate(
            '<select data-application-filter aria-label="Filter">'
            '<option value="">All</option><option value="open">Open</option>'
            '</select><div data-filter-item data-filter-value="open">Open</div>'
        )
        self.assertTrue(any(
            "application filters need" in item for item in aria_only_filter
        ))
        aria_only_listbox = validate(
            '<div role="listbox" aria-label="Choice">'
            '<button type="button" role="option" '
            'data-application-action="select-option" aria-selected="false" '
            'data-value="a">A</button></div>'
        )
        self.assertIn(
            "listboxes must bind their accessible purpose to one matching visible label",
            aria_only_listbox,
        )
        readonly_search = validate(
            '<input type="search" readonly aria-label="Search" '
            'data-application-search><p data-search-item>One</p>'
        )
        self.assertTrue(any(
            "input[type=search]" in item for item in readonly_search
        ))
        clobbered_collection_item = validate(
            '<input type="search" aria-label="Search" data-application-search>'
            '<form data-search-item>Visible record'
            '<input name="innerText" aria-label="Field">'
            '<button type="submit">Submit</button></form>'
        )
        self.assertIn(
            "application collection items must use a non-form browser-stable semantic container",
            clobbered_collection_item,
        )
        orphan_list_item = validate(
            '<input type="search" data-application-search aria-label="Search">'
            '<li data-search-item>Orphan list item</li>'
        )
        self.assertIn(
            "application collection items must use a non-form browser-stable semantic container",
            orphan_list_item,
        )
        nested_collection_items = validate(
            '<select data-application-filter aria-label="Filter">'
            '<option value="">All</option><option value="a">A</option>'
            '<option value="b">B</option></select>'
            '<div data-filter-item data-filter-value="a">Parent'
            '<div data-filter-item data-filter-value="b">Child</div></div>'
        )
        self.assertIn(
            "same-route application collection items cannot contain one another",
            nested_collection_items,
        )
        orphan_collection_items = validate(
            '<div data-search-item>Orphan search</div>'
            '<div data-filter-item data-filter-value="orphan">Orphan filter</div>'
        )
        self.assertIn(
            "each route with search items must own exactly one application search control",
            orphan_collection_items,
        )
        self.assertIn(
            "each route with filter items must own exactly one application filter control",
            orphan_collection_items,
        )
        disclosure_collection_item = validate(
            '<input type="search" data-application-search aria-label="Search">'
            '<button type="button" data-application-action="toggle-menu" '
            'aria-controls="owned-menu" aria-expanded="false">Menu</button>'
            '<div id="owned-menu" hidden data-search-item>Menu contents</div>'
        )
        self.assertIn(
            "application collection items cannot also be fixed-runtime disclosure targets",
            disclosure_collection_item,
        )
        stateful_search = validate(
            '<input type="search" aria-label="Search" aria-required="true" '
            'data-application-search><p data-search-item>One</p>'
        )
        self.assertTrue(any(
            "input[type=search]" in item for item in stateful_search
        ))
        self_hiding_search = validate(
            '<div data-search-item>Collection <input type="search" '
            'aria-label="Search" data-application-search></div>'
        )
        self.assertTrue(any(
            "outside every collection item" in item
            for item in self_hiding_search
        ))
        hidden_navigation = validate(
            '<label>Search <input type="search" value="nomatch" '
            'data-application-search></label>'
            '<p data-search-item><button type="button" '
            'data-application-action="toggle-pressed" aria-pressed="false">'
            'Open target</button></p>'
        )
        self.assertIn(
            "application routing and action controls must remain outside every collection item the runtime can hide",
            hidden_navigation,
        )
        hidden_only_search_item = validate(
            '<input type="search" aria-label="Search" data-application-search>'
            '<p data-search-item><span hidden>Invisible item</span></p>'
        )
        self.assertTrue(any(
            "same-route search items" in item
            for item in hidden_only_search_item
        ))
        runtime_exposed_inaccessible_search_item = validate(
            '<input type="search" data-application-search aria-label="Search">'
            '<div data-search-item>Valid item</div>'
            '<div data-search-item hidden><span aria-hidden="true">'
            'Runtime-exposed inaccessible item</span></div>'
        )
        self.assertTrue(any(
            "same-route search items" in item
            for item in runtime_exposed_inaccessible_search_item
        ))
        role_search = validate(
            '<input type="search" role="link" aria-label="Search" '
            'data-application-search><p data-search-item>One</p>'
        )
        self.assertTrue(any(
            "input[type=search]" in item for item in role_search
        ))
        duplicate_search = validate(
            '<input type="search" aria-label="Search one" data-application-search>'
            '<input type="search" aria-label="Search two" data-application-search>'
            '<p data-search-item>One</p>'
        )
        self.assertTrue(any(
            "exactly one search control" in item for item in duplicate_search
        ))
        duplicate_filter = validate(
            '<select aria-label="Filter one" data-application-filter>'
            '<option value="">All</option><option value="open">Open</option></select>'
            '<select aria-label="Filter two" data-application-filter>'
            '<option value="">All</option><option value="open">Open</option></select>'
            '<p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "exactly one filter control" in item for item in duplicate_filter
        ))
        multiple_filter = validate(
            '<select multiple aria-label="Filter" data-application-filter>'
            '<option value="">All</option><option value="open">Open</option>'
            '</select><p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item for item in multiple_filter
        ))
        stateful_filter = validate(
            '<select aria-label="Filter" aria-readonly="true" '
            'data-application-filter><option value="">All</option>'
            '<option value="open">Open</option></select>'
            '<p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item for item in stateful_filter
        ))
        self_hiding_filter = validate(
            '<div data-filter-item data-filter-value="open">Collection '
            '<select aria-label="Filter" data-application-filter>'
            '<option value="">All</option><option value="open">Open</option>'
            '</select></div>'
        )
        self.assertTrue(any(
            "outside every collection item" in item
            for item in self_hiding_filter
        ))
        disabled_filter = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value="">All</option><option disabled value="open">Open</option>'
            '</select><p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item for item in disabled_filter
        ))
        hidden_optgroup_filter = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value="">All</option><optgroup hidden label="Unavailable">'
            '<option value="open">Open</option></optgroup></select>'
            '<p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item
            for item in hidden_optgroup_filter
        ))
        unnamed_filter_options = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value=""></option><option value="open"></option>'
            '</select><p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item
            for item in unnamed_filter_options
        ))
        invisible_explicit_filter_label = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value="" label="&#x200b;">All</option>'
            '<option value="open">Open</option></select>'
            '<p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item
            for item in invisible_explicit_filter_label
        ))
        duplicate_filter_labels = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value="">Same</option><option value="open">Same</option>'
            '</select><p data-filter-item data-filter-value="open">One</p>'
        )
        self.assertTrue(any(
            "application filters need" in item
            for item in duplicate_filter_labels
        ))
        runtime_exposed_inaccessible_filter_item = validate(
            '<select aria-label="Filter" data-application-filter>'
            '<option value="">All</option><option value="open">Open</option>'
            '</select><p data-filter-item data-filter-value="open">One</p>'
            '<p hidden data-filter-item data-filter-value="open">'
            '<span aria-hidden="true">Hidden</span></p>'
        )
        self.assertTrue(any(
            "application filters need" in item
            for item in runtime_exposed_inaccessible_filter_item
        ))
        empty_listbox = validate('<div role="listbox" aria-label="Empty"></div>')
        self.assertIn(
            "every listbox must contain at least one reachable option",
            empty_listbox,
        )
        duplicate_listbox_labels = validate(
            '<div role="listbox" aria-label="Choice">'
            '<button type="button" role="option" '
            'data-application-action="select-option" aria-selected="false" '
            'data-value="a">Same</button>'
            '<button type="button" role="option" '
            'data-application-action="select-option" aria-selected="false" '
            'data-value="b">Same</button></div>'
        )
        self.assertIn(
            "listbox option accessible names must be non-empty and unique",
            duplicate_listbox_labels,
        )
        divergent_listbox_labels = validate(
            '<div role="listbox" aria-label="Choice">'
            '<button type="button" role="option" aria-label="First choice" '
            'data-application-action="select-option" aria-selected="false" '
            'data-value="a">Same</button>'
            '<button type="button" role="option" aria-label="Second choice" '
            'data-application-action="select-option" aria-selected="false" '
            'data-value="b">Same</button></div>'
        )
        self.assertIn(
            "listbox option visible labels must be unique and contained in their accessible names",
            divergent_listbox_labels,
        )
        divergent_action_label = validate(
            '<button type="button" aria-label="Save" '
            'data-application-action="toggle-pressed" '
            'aria-pressed="false">Delete account</button>'
        )
        self.assertIn(
            "application control accessible names must contain their visible labels",
            divergent_action_label,
        )
        ordinary_fragment = validate(
            '<a href="#details">Details</a><div id="details">More</div>'
        )
        self.assertTrue(any(
            "fragment anchors" in item for item in ordinary_fragment
        ))
        role_override = validate(
            '<button type="button" role="link" '
            'data-application-action="toggle-pressed" '
            'aria-pressed="false">Toggle</button>'
        )
        self.assertTrue(any(
            "cannot override their implicit accessibility role" in item
            for item in role_override
        ))
        hidden_visual_label = validate(
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-label="Pin" aria-pressed="false"></button>'
        )
        self.assertIn(
            "application controls must expose a non-empty visible label",
            hidden_visual_label,
        )
        empty_private = validate('<span data-private></span>')
        self.assertTrue(any(
            "privacy-masked content must be non-empty" in item
            for item in empty_private
        ))
        nested_outcome = validate(
            '<div data-application-outcome><button type="button" '
            'data-application-action="toggle-pressed" aria-pressed="false">'
            'Pin</button></div>'
        )
        self.assertTrue(any(
            "text-only leaf" in item for item in nested_outcome
        ))
        empty_form = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route"></form>'
        )
        self.assertTrue(any("descendant submit control" in item for item in empty_form))
        unnamed_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<button type="submit"></button></form>'
        )
        self.assertTrue(any(
            "descendant submit control" in item for item in unnamed_submit
        ))
        default_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route"><input type="submit"></form>'
        )
        self.assertFalse(any(
            "descendant submit control" in item for item in default_submit
        ), default_submit)
        image_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route"><input type="image" '
            'alt="Submit" src="data:image/png;base64,invalid"></form>'
        )
        self.assertTrue(any(
            "descendant submit control" in item for item in image_submit
        ), image_submit)
        for invisible_submit_markup in (
            '<button type="submit" aria-label="Submit"></button>',
            '<input type="submit" aria-label="Submit" value="">',
        ):
            invisible_affordance = validate(
                '<form data-transition-ref="author-experience:TRN-001@r1" '
                'data-route-target="#/author-route">'
                + invisible_submit_markup + '</form>'
            )
            self.assertTrue(any(
                "descendant submit control" in item
                for item in invisible_affordance
            ), invisible_affordance)
        invisible_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route"><input type="submit" '
            'value="\u200b"></form>'
        )
        self.assertTrue(any(
            "descendant submit control" in item for item in invisible_submit
        ), invisible_submit)
        embedded_invisible_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route"><input type="submit" '
            'value="R&#x200b;un"></form>'
        )
        self.assertTrue(any(
            "descendant submit control" in item
            for item in embedded_invisible_submit
        ), embedded_invisible_submit)
        bad_extra_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<button type="submit">Good submit</button>'
            '<button type="submit" tabindex="-1" aria-label="&#x200b;" '
            'aria-pressed="true"></button></form>'
        )
        self.assertTrue(any(
            "descendant submit controls in sequential keyboard navigation with passive ARIA"
            in item for item in bad_extra_submit
        ))

        composed_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<input type="submit" value="Submit" '
            'data-application-action="toggle-pressed" aria-pressed="false">'
            '</form>'
        )
        self.assertTrue(any(
            "cannot declare independent application actions or routing identities"
            in item for item in composed_submit
        ))
        self.assertTrue(any(
            "input application actions must declare type=button" in item
            for item in composed_submit
        ))
        child_route_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<button type="submit" data-simulation-id="child-route" '
            'data-route-target="#/author-route">Submit</button></form>'
        )
        self.assertTrue(any(
            "cannot declare independent application actions or routing identities"
            in item for item in child_route_submit
        ))
        form_owner_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<button type="submit" form="missing">Submit</button></form>'
        )
        self.assertTrue(any(
            "cannot declare independent application actions or routing identities"
            in item for item in form_owner_submit
        ))
        form_encoding_submit = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route">'
            '<button type="submit" formenctype="text/plain">Submit</button>'
            '</form>'
        )
        self.assertTrue(any(
            "cannot declare independent application actions or routing identities"
            in item for item in form_encoding_submit
        ))
        native_form_override = validate(
            '<form novalidate method="get" '
            'data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><input required name="value">'
            '<button type="submit">Submit</button></form>'
        )
        self.assertTrue(any(
            "native navigation or validation overrides" in item
            for item in native_form_override
        ))
        focusable_form_owner = validate(
            '<form tabindex="0" data-simulation-id="focusable-form" '
            'data-route-target="#/author-route">'
            '<button type="submit">Run</button></form>'
        )
        self.assertIn(
            "routed form owners cannot declare tabindex; only validated descendant fields and submit controls own focus",
            focusable_form_owner,
        )
        for impossible_field in (
            '<input type="number" required min="10" max="1" '
            'aria-label="Impossible number">',
            '<textarea required maxlength="0" '
            'aria-label="Impossible text"></textarea>',
            '<select required aria-label="Impossible choice">'
            '<option value="">Only empty choice</option></select>',
            '<input type="number" step="0" aria-label="Zero step">',
            '<input required minlength="3" maxlength="2" '
            'aria-label="Impossible length">',
            '<input required pattern="(?!)" aria-label="Opaque pattern">',
            '<input type="number" min="1e999999" max="2e999999" '
            'step="1e999999" aria-label="Overflow number">',
            '<input type="number" min="1e-999999" max="2e-999999" '
            'step="1e-999999" aria-label="Underflow number">',
            '<select aria-label="Empty choice"></select>',
            '<select aria-label="Disabled choice">'
            '<option disabled value="x">X</option></select>',
        ):
            impossible_form = validate(
                '<form data-simulation-id="impossible-form" '
                'data-route-target="#/author-route">'
                + impossible_field
                + '<button type="submit">Run</button></form>'
            )
            self.assertIn(
                "routed form fields need a type-appropriate, mechanically satisfiable native constraint domain",
                impossible_form,
                impossible_field,
            )
        satisfiable_form = validate(
            '<form data-simulation-id="satisfiable-form" '
            'data-route-target="#/author-route">'
            '<input type="number" required min="1" max="10" step="0.5" '
            'aria-label="Valid number">'
            '<textarea required minlength="1" maxlength="10" '
            'aria-label="Valid text"></textarea>'
            '<select required aria-label="Valid choice">'
            '<option value="">Choose</option><option value="one">One</option>'
            '</select><button type="submit">Run</button></form>'
        )
        self.assertNotIn(
            "routed form fields need a type-appropriate, mechanically satisfiable native constraint domain",
            satisfiable_form,
        )
        for mixed_topology in (
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><input required aria-label="Visible field">'
            '<button type="button" data-application-action="toggle-drawer" '
            'aria-controls="submit-drawer" aria-expanded="false">Review</button>'
            '<div id="submit-drawer" hidden><button type="submit">Submit</button>'
            '</div></form>',
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><button type="button" '
            'data-application-action="toggle-drawer" aria-controls="field-drawer" '
            'aria-expanded="false">Edit</button><div id="field-drawer" hidden>'
            '<input required aria-label="Hidden field"></div>'
            '<button type="submit">Submit</button></form>',
        ):
            self.assertIn(
                "routed form owners, fields and submit controls must share one exact disclosure and dialog topology",
                validate(mixed_topology),
            )
        field_form_override = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><input required aria-label="Secret" '
            'name="secret" form="missing"><button type="submit">Run</button>'
            '</form>'
        )
        self.assertIn(
            "routed form fields cannot override their exact owning form",
            field_form_override,
        )
        external_form_field = validate(
            '<form id="routed" data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><button type="submit">Run</button></form>'
            '<input required aria-label="External" data-context-key="external" '
            'form="routed">'
        )
        self.assertTrue(any(
            "cannot override native form ownership" in item
            for item in external_form_field
        ))
        unnamed_form_field = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><input required name="secret">'
            '<button type="submit">Run</button></form>'
        )
        self.assertTrue(any(
            "routed form fields must be named" in item
            for item in unnamed_form_field
        ))
        aria_only_form_field = validate(
            '<form data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route=""><input required name="secret" '
            'aria-label="Secret"><button type="submit">Run</button></form>'
        )
        self.assertTrue(any(
            "routed form fields must be named" in item
            for item in aria_only_form_field
        ))
        orphan_route = validate(
            '<button type="button" data-route-target="#/author-route">Orphan</button>'
        )
        self.assertTrue(any(
            "require exactly one owning declarative identity" in item
            for item in orphan_route
        ))
        stale_simulation_attrs = validate(
            '<button type="button" data-simulation-id="simulate-missing" '
            'data-route-target="#/author-route" data-return-route="#/author-route">'
            'Simulate</button>'
        )
        self.assertTrue(any(
            "simulation controls may declare only" in item
            for item in stale_simulation_attrs
        ))
        action_routing_attrs = validate(
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false" data-route-target="">Toggle</button>'
        )
        self.assertTrue(any(
            "actions cannot own transition routing attributes" in item
            for item in action_routing_attrs
        ))
        orphan_return = validate(
            '<button type="button" data-application-action="return-route">'
            'Back nowhere</button>'
        )
        self.assertIn(
            "return-route controls must exist exactly on routes targeted by a declared non-empty return route",
            orphan_return,
        )
        mismatched_anchor = validate(
            '<a href="#/wrong" data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/author-route" data-preserve-context="" '
            'data-return-route="">Continue</a>'
        )
        self.assertTrue(any(
            "anchor href must exactly match" in item
            for item in mismatched_anchor
        ))

        valid = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="valid-modal">Open</button>'
            '<dialog id="valid-modal" aria-modal="true">'
            '<button type="button" data-application-action="close-modal">Close</button>'
            '</dialog>'
            '<p id="valid-choice-label">Choice</p>'
            '<div role="listbox" aria-labelledby="valid-choice-label"><button type="button" '
            'data-application-action="select-option" role="option" '
            'aria-selected="false" data-value="one">One</button></div>'
            '<label>Search <input type="search" data-application-search></label>'
            '<p data-search-item>One</p>'
        )
        self.assertFalse(any("initially closed, visible dialog" in item for item in valid))
        self.assertFalse(any("inside one reachable dialog" in item for item in valid))
        self.assertFalse(any("inside one reachable listbox" in item for item in valid))
        self.assertFalse(any("input[type=search]" in item for item in valid))

        hidden_drawer = validate(
            '<button type="button" data-application-action="toggle-drawer" '
            'aria-controls="valid-drawer" aria-expanded="false">Open</button>'
            '<aside id="valid-drawer" hidden>'
            '<p id="drawer-choice-label">Drawer choice</p>'
            '<div role="listbox" aria-labelledby="drawer-choice-label"><button type="button" '
            'data-application-action="select-option" role="option" '
            'aria-selected="false" data-value="one">One</button></div>'
            '<label>Search <input type="search" data-application-search></label>'
            '<p data-search-item data-private>One</p>'
            '</aside>'
        )
        for message in (
            "application controls must be enabled and reachable",
            "inside one reachable listbox",
            "application search needs one enabled input[type=search]",
            "privacy-masked content must be reachable",
        ):
            self.assertFalse(any(message in item for item in hidden_drawer), hidden_drawer)

        unnamed_search = validate(
            '<input type="search" tabindex="-1" data-application-search>'
            '<p data-search-item>One</p>'
        )
        self.assertTrue(any(
            "input[type=search]" in item for item in unnamed_search
        ))
        unnamed_dialog = validate(
            '<button type="button" data-application-action="open-modal" '
            'aria-haspopup="dialog" aria-controls="unnamed-dialog">Open</button>'
            '<dialog id="unnamed-dialog">'
            '<button type="button" data-application-action="close-modal">Close</button>'
            '</dialog>'
        )
        self.assertTrue(any(
            "non-empty accessible name" in item for item in unnamed_dialog
        ))

        orphaned_dialog = validate(
            '<dialog id="orphaned-dialog">'
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false">Toggle</button>'
            '<input type="search" data-application-search>'
            '<p data-search-item data-private>One</p>'
            '</dialog>'
        )
        self.assertTrue(any(
            "application controls must be enabled and reachable" in item
            for item in orphaned_dialog
        ))
        self.assertTrue(any(
            "application search needs one enabled input[type=search]" in item
            for item in orphaned_dialog
        ))
        self.assertTrue(any(
            "privacy-masked content must be non-empty" in item
            for item in orphaned_dialog
        ))

        self_target = validate(
            '<button id="self-drawer" type="button" '
            'data-application-action="toggle-drawer" '
            'aria-controls="self-drawer" aria-expanded="true">Drawer</button>'
        )
        self.assertTrue(any(
            "must not be the control itself" in item for item in self_target
        ))

        orphan_option = validate(
            '<button type="button" role="option" aria-selected="false">'
            'Orphan</button>'
        )
        self.assertTrue(any(
            "must belong to exactly one canonical listbox" in item
            for item in orphan_option
        ))
        incomplete_listbox = validate(
            '<div role="listbox" aria-label="Incomplete">'
            '<button type="button" role="option" aria-selected="false" '
            'data-value="one">One</button></div>'
        )
        self.assertTrue(any(
            "every listbox option must be" in item
            for item in incomplete_listbox
        ))
        unsafe_option_name = validate(
            '<div role="listbox" aria-label="Choices">'
            '<button type="button" role="option" aria-selected="false" '
            'data-value="one" data-application-action="select-option">'
            'O&#xfe0f;ne</button></div>'
        )
        self.assertTrue(any(
            "every listbox option must be" in item
            for item in unsafe_option_name
        ))
        for noncanonical_content in (
            '<div role="listbox" aria-label="Choices">'
            '<button type="button" role="option" aria-selected="false" '
            'data-value="one" data-application-action="select-option">One</button>'
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false">Unrelated toggle</button></div>',
            '<div role="listbox" aria-label="Choices">'
            '<button type="button" role="option" aria-selected="false" '
            'data-value="one" data-application-action="select-option">'
            '<span>Nested option label</span></button></div>',
        ):
            self.assertIn(
                "listboxes may contain only direct, text-only canonical option controls",
                validate(noncanonical_content),
            )
        duplicate_listbox = validate(
            '<div role="listbox" aria-label="Duplicate">'
            '<button type="button" role="option" aria-selected="true" '
            'data-value="same" data-application-action="select-option">One</button>'
            '<button type="button" role="option" aria-selected="true" '
            'data-value="same" data-application-action="select-option">Two</button>'
            '</div>'
        )
        self.assertTrue(any(
            "data-value values must be unique" in item
            for item in duplicate_listbox
        ))
        self.assertTrue(any(
            "cannot begin with multiple selected options" in item
            for item in duplicate_listbox
        ))
        multiselect = validate(
            '<div role="listbox" aria-label="Multiple" '
            'aria-multiselectable="true"></div>'
        )
        self.assertTrue(any(
            "supports only single-select listboxes" in item
            for item in multiselect
        ))
        for attrs in (
            'tabindex="0"', 'tabindex="-1"',
            'aria-activedescendant="option-one"',
            'aria-orientation="horizontal"',
            'aria-readonly="true"', 'aria-required="true"',
        ):
            conflicting_focus = validate(
                f'<div role="listbox" aria-label="Choices" {attrs}>'
                '<button id="option-one" type="button" role="option" '
                'data-application-action="select-option" data-value="one" '
                'aria-selected="false">One</button></div>'
            )
            self.assertTrue(any(
                "independent focus or horizontal keyboard model" in item
                for item in conflicting_focus
            ), attrs)

        duplicate_drawer = validate(
            '<button type="button" data-application-action="toggle-drawer" '
            'aria-controls="shared-drawer" aria-expanded="false">One</button>'
            '<button type="button" data-application-action="toggle-drawer" '
            'aria-controls="shared-drawer" aria-expanded="false">Two</button>'
            '<aside id="shared-drawer" hidden>Drawer</aside>'
        )
        self.assertTrue(any(
            "exactly one state owner" in item for item in duplicate_drawer
        ))
        duplicate_theme = validate(
            '<button type="button" data-application-action="toggle-theme" '
            'aria-pressed="false">Extra theme</button>'
        )
        self.assertTrue(any(
            "exactly one application-wide control" in item
            for item in duplicate_theme
        ))
        for fragment in (
            '<button type="button" data-application-action="toggle-theme" '
            'aria-pressed="false" aria-expanded="true">Extra</button>',
            '<button type="button" data-application-action="return-route" '
            'aria-pressed="true">Return</button>',
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false" aria-selected="true">Toggle</button>',
        ):
            self.assertTrue(any(
                "ARIA state must belong to its exact runtime action" in item
                for item in validate(fragment)
            ), fragment)

    def test_browser_inert_markup_and_plain_deep_links_fail_closed(self):
        plain_link = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<a href="#/author-route">Bypass</a>',
            1,
        )
        scanner = self.scan(plain_link)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings,
            authoring=True,
        )
        self.assertTrue(any(
            "deep-route anchors" in item for item in findings
        ))

        noscript = self.scan(self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<noscript><button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false">Inert</button></noscript>',
            1,
        ))
        self.assertIn("noscript", noscript.forbidden_elements)

        textarea = self.scan(self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<textarea><button type="button">Not DOM</button></textarea>',
            1,
        ))
        self.assertTrue(any(
            "raw-text or RCDATA" in item
            for item in experience_application_check.document_structure_findings(
                textarea
            )
        ))

    def test_html5_native_inert_and_document_accessibility_fail_closed(self):
        def all_findings(text: str) -> list[str]:
            scanner = self.scan(text)
            findings = experience_application_check.document_structure_findings(
                scanner
            )
            contract = experience_application_check.parse_contract(
                scanner, findings
            )
            experience_application_check.validate_contract(
                contract, {}, {"author-experience"}, scanner, [], findings,
                authoring=True,
            )
            findings.extend(
                experience_application_check.required_experience_findings(
                    text, scanner
                )
            )
            return findings

        for mutation, message in (
            (
                self.rendered_template().replace(
                    "<p data-private>", '<p style="" data-private>', 1,
                ),
                "inline style attributes are forbidden",
            ),
            (
                self.rendered_template().replace(
                    '<p data-private>This file is the only visual Experience implementation.</p>',
                    '<p data-private>This file is the only visual Experience implementation.</p>'
                    '<button type="button" tabindex="" '
                    'data-application-action="toggle-pressed" '
                    'aria-pressed="false">Extra toggle</button>',
                    1,
                ),
                "tabindex attributes must be absent",
            ),
        ):
            self.assertTrue(any(
                message in item for item in all_findings(mutation)
            ), message)

        self_closing = self.rendered_template().replace(
            '<header class="application-shell application-toolbar">',
            '<dialog id="trap" />\n'
            '<header class="application-shell application-toolbar">',
            1,
        )
        self.assertTrue(any(
            "self-close non-void" in item
            for item in all_findings(self_closing)
        ))

        void_end_tag = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation."
            "</br>Browser-only break</p>",
            1,
        )
        self.assertTrue(any(
            "browser-stable content models" in item
            for item in all_findings(void_end_tag)
        ))

        for raw_role in (
            " button ", "foo button", "button link", " listbox ",
            "foo menuitemcheckbox", "application", "dialog", "alertdialog",
            "presentation", "none",
        ):
            role_markup = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                f'<div role="{raw_role}" aria-label="Fake">Content</div>',
                1,
            )
            self.assertTrue(any(
                "explicit ARIA roles" in item for item in all_findings(role_markup)
            ), raw_role)

        whitespace_heading_id = self.rendered_template().replace(
            'aria-labelledby="author-route-title"',
            'aria-labelledby="author route title"',
            1,
        ).replace(
            'id="author-route-title"', 'id="author route title"', 1,
        )
        self.assertTrue(any(
            "single-token HTML/ARIA identifiers" in item
            for item in all_findings(whitespace_heading_id)
        ))
        whitespace_controls_id = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<button type="button" data-application-action="toggle-menu" '
            'aria-expanded="false" aria-controls="menu panel">Menu</button>'
            '<div id="menu panel" hidden><button type="button" '
            'data-application-action="toggle-pressed" aria-pressed="false">'
            'Item</button></div>',
            1,
        )
        self.assertTrue(any(
            "single-token HTML/ARIA identifiers" in item
            for item in all_findings(whitespace_controls_id)
        ))

        select_trap = self.rendered_template().replace(
            '<header class="application-shell application-toolbar">',
            '<select aria-label="Trap">'
            '<header class="application-shell application-toolbar">',
            1,
        ).replace("</header>", "</header></select>", 1)
        self.assertTrue(any(
            "cannot nest forms or native interactive controls" in item
            for item in all_findings(select_trap)
        ))

        theme = (
            '<button type="button" data-application-action="toggle-theme" '
            'aria-label="Toggle color theme" aria-pressed="false">Theme</button>'
        )
        for replacement in (
            theme.replace(" type=", " inert type=", 1),
            theme.replace(" type=", ' aria-hidden="true" type=', 1),
            theme.replace(" type=", ' aria-disabled="true" type=', 1),
            theme.replace(" type=", ' tabindex="-1" type=', 1),
            theme.replace(" type=", ' tabindex=" -1 " type=', 1),
            theme.replace(" type=", ' tabindex="-01" type=', 1),
            '<fieldset disabled>' + theme + '</fieldset>',
            theme.replace(">Theme</button>", "></button>", 1),
            theme.replace(' aria-label="Toggle color theme"', '').replace(
                ">Theme<", '><span aria-hidden="true">Theme</span><'
            ),
        ):
            with self.subTest(replacement=replacement):
                findings = all_findings(
                    self.rendered_template().replace(theme, replacement, 1)
                )
                self.assertIn(
                    "application needs accessible theme control", findings
                )

        for unsafe_name in (
            theme.replace(
                'aria-label="Toggle color theme"', 'aria-label=""', 1,
            ),
            theme.replace(
                'aria-label="Toggle color theme"',
                'aria-label="Toggle&#x200b; color theme"',
                1,
            ),
            '<span id="unsafe-theme-label">Toggle&#xfe0f; color theme</span>'
            + theme.replace(
                'aria-label="Toggle color theme"',
                'aria-labelledby="unsafe-theme-label"',
                1,
            ),
            theme.replace(' aria-label="Toggle color theme"', '', 1).replace(
                '>Theme</button>', '>The&#x34f;me</button>', 1,
            ),
        ):
            findings = all_findings(
                self.rendered_template().replace(theme, unsafe_name, 1)
            )
            self.assertIn(
                "application needs accessible theme control", findings
            )

        explicit_label_owner = self.rendered_template().replace(
            theme,
            '<label for="theme-control">Bypass theme</label>'
            + theme.replace("<button ", '<button id="theme-control" ', 1),
            1,
        )
        self.assertTrue(any(
            "unmanaged native invocation behavior" in item
            for item in all_findings(explicit_label_owner)
        ))
        implicit_label_owner = self.rendered_template().replace(
            theme, "<label>Bypass theme" + theme + "</label>", 1,
        )
        self.assertTrue(any(
            "implicit label owner" in item
            for item in all_findings(implicit_label_owner)
        ))
        for semantic_escape, message in (
            ('<div aria-disabled="true">Static content</div>', "exact canonical accessibility owner"),
            ('<div title="Wrong tooltip">Static content</div>', "unmanaged native invocation behavior"),
            ('<input type="search" aria-label="Search" placeholder="Wrong hint" data-application-search>', "unmanaged native invocation behavior"),
        ):
            escaped = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + semantic_escape,
                1,
            )
            self.assertTrue(any(
                message in item for item in all_findings(escaped)
            ), semantic_escape)

        multiple_implicit_controls = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<label>Search and filter <input type="search" aria-label="Search" '
            'data-application-search><select aria-label="Filter" '
            'data-application-filter><option value="">All</option>'
            '<option value="open">Open</option></select></label>'
            '<p data-private data-search-item data-filter-item '
            'data-filter-value="open">Visible item</p>',
            1,
        )
        self.assertTrue(any(
            "exactly one labelable descendant" in item
            for item in all_findings(multiple_implicit_controls)
        ))
        for bad_optgroup in (
            '<optgroup><option value="open">Open</option></optgroup>',
            '<optgroup label=""><option value="open">Open</option></optgroup>',
            '<optgroup label="&#x200b;"><option value="open">Open</option></optgroup>',
            '<optgroup label="Open"></optgroup>',
        ):
            invalid_group = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                '<label>Filter <select aria-label="Filter" data-application-filter>'
                '<option value="">All</option>' + bad_optgroup
                + '</select></label><p data-private data-filter-item '
                'data-filter-value="open">Visible item</p>',
                1,
            )
            self.assertTrue(any(
                "every optgroup" in item for item in all_findings(invalid_group)
            ), bad_optgroup)

        unmanaged_table = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<p data-private>Private</p><table><tbody><tr><th scope="banana">'
            'Name</th><td headers="missing">Value</td></tr></tbody></table>',
            1,
        )
        self.assertTrue(any(
            "forbidden dependency elements" in item
            for item in all_findings(unmanaged_table)
        ))
        for closed_fragment in (
            "<dl><dt>Orphan term</dt></dl>",
            "<dl><dd>Orphan definition</dd></dl>",
            '<meta name="theme-color" content="#000000">',
            '<meta charset="windows-1252">',
            '<data value="x">Semantic value</data>',
            '<dfn>Defined term</dfn>',
            '<time datetime="2026-08-27">Someday</time>',
        ):
            closed_surface = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + closed_fragment,
                1,
            )
            self.assertTrue(any(
                "forbidden dependency elements" in item
                or "browser-stable content models" in item
                for item in all_findings(closed_surface)
            ), closed_fragment)
        for input_type, value in (
            ("date", "not-a-date"),
            ("number", "not-a-number"),
            ("time", "25:99"),
            ("month", "2026-99"),
        ):
            sanitized_private = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                f'<input type="{input_type}" aria-label="Private value" '
                f'data-private value="{value}">',
                1,
            )
            self.assertIn(
                "application needs privacy-masked content",
                all_findings(sanitized_private),
                input_type,
            )
        password_private = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<input type="password" data-private data-context-key="secret" '
            'aria-label="Secret" value="secret">',
            1,
        )
        self.assertIn(
            "application needs privacy-masked content",
            all_findings(password_private),
        )
        self.assertEqual(
            experience_application_check.authored_visible_value(
                "input", {"type": "password", "value": "secret"},
            ),
            "",
        )
        reserved_class_escape = self.rendered_template().replace(
            '<p data-private>This file is the only visual Experience implementation.</p>',
            '<p data-private class="application-skip">'
            'This file is the only visual Experience implementation.</p>',
            1,
        )
        self.assertIn(
            "fixed application scaffold classes must have only their exact canonical owners",
            all_findings(reserved_class_escape),
        )

        multiple_name_sources = self.rendered_template().replace(
            theme,
            '<span id="theme-label-a">Toggle</span>'
            '<span id="theme-label-b">theme</span>'
            + theme.replace(
                'aria-label="Toggle color theme"',
                'aria-labelledby="theme-label-a theme-label-b"',
                1,
            ),
            1,
        )
        self.assertIn(
            "application needs accessible theme control",
            all_findings(multiple_name_sources),
        )

        fallback_scanner = self.scan(
            '<body><div role="listbox" aria-label="">Visible listbox</div>'
            '<form data-simulation-id="simulation" data-route-target="#/next">'
            '<button type="submit" aria-label="">Visible submit</button>'
            '</form></body>'
        )
        self.assertEqual(
            "",
            experience_application_check.control_accessible_name(
                fallback_scanner.listboxes[0], fallback_scanner,
            ),
        )
        self.assertEqual(
            "",
            experience_application_check.control_accessible_name(
                fallback_scanner.routed_forms[0]["submit_affordances"][0],
                fallback_scanner,
            ),
        )

        for description_attribute in (
            'aria-describedby="missing-description"',
            'aria-describedby="duplicate duplicate"',
            'aria-describedby=""',
            'aria-description="&#x200b;"',
        ):
            described_theme = theme.replace(
                " type=", f" {description_attribute} type=", 1,
            )
            findings = all_findings(
                self.rendered_template().replace(theme, described_theme, 1)
            )
            self.assertTrue(any(
                "passive ARIA descriptions need visible scalar text" in item
                for item in findings
            ), description_attribute)
        valid_description = self.rendered_template().replace(
            theme,
            '<p id="theme-description">Changes the visual color theme.</p>'
            + theme.replace(
                " type=", ' aria-describedby="theme-description" type=', 1,
            ),
            1,
        )
        self.assertFalse(any(
            "passive ARIA descriptions need visible scalar text" in item
            for item in all_findings(valid_description)
        ))

        for unsupported_aria in (
            'aria-owns="application-main"',
            'aria-flowto="application-main"',
            'aria-controls="application-main"',
            'aria-activedescendant="application-main"',
            'aria-modal="true"',
            'aria-readonly="true"',
        ):
            mutation = self.rendered_template().replace(
                '<header class="application-shell application-toolbar">',
                '<header class="application-shell application-toolbar" '
                + unsupported_aria + '>',
                1,
            )
            self.assertTrue(any(
                "ARIA attributes must belong to one exact canonical accessibility owner"
                in item for item in all_findings(mutation)
            ), unsupported_aria)
        invalid_aria_hidden = self.rendered_template().replace(
            theme, theme.replace(" type=", ' aria-hidden="TRUE" type=', 1), 1,
        )
        self.assertTrue(any(
            "aria-hidden must use the exact true/false tokens" in item
            for item in all_findings(invalid_aria_hidden)
        ))

        for mutation, message in (
            (
                self.rendered_template().replace(
                    '  <meta name="viewport" content="width=device-width, initial-scale=1">\n',
                    "", 1,
                ),
                "responsive viewport",
            ),
            (
                self.rendered_template().replace(
                    "  <title>Application acceptance prototype</title>\n", "", 1
                ),
                "document title",
            ),
            (
                self.rendered_template().replace(
                    "Application acceptance prototype</title>",
                    "Application&#x202e; acceptance prototype</title>",
                    1,
                ),
                "document title",
            ),
            (
                self.rendered_template().replace(
                    "<title>Application acceptance prototype</title>",
                    "<title>Delete all records</title>",
                    1,
                ),
                "document title",
            ),
            (
                self.rendered_template().replace(
                    '<a class="application-skip" href="#application-main">Skip to application</a>',
                    '<a class="application-skip" href="#application-main" tabindex="-1">Skip to application</a>',
                    1,
                ),
                "skip link",
            ),
            (
                self.rendered_template().replace(
                    '<a class="application-skip" href="#application-main">',
                    '<a class="application-skip" href="#application-main" role="button">',
                    1,
                ),
                "skip link",
            ),
            (
                self.rendered_template().replace(
                    '<main id="application-main" class="application-shell" tabindex="-1">',
                    '<main id="application-main" class="application-shell" tabindex="-1" role="application">',
                    1,
                ),
                "exact fixed main",
            ),
            (
                self.rendered_template().replace(
                    '<section data-application-route="#/author-route"',
                    '<section role="application" data-application-route="#/author-route"',
                    1,
                ),
                "browser-stable section/article/div root",
            ),
            (
                self.rendered_template().replace(
                    '<h1 id="author-route-title">',
                    '<h1 id="author-route-title" role="presentation">',
                    1,
                ),
                "visible heading",
            ),
            (
                self.rendered_template().replace(
                    'id="application-announcer" role="status" aria-live="polite"',
                    'id="application-announcer" role="status" aria-live="polite" aria-busy="true"',
                    1,
                ),
                "fixed-runtime text sink",
            ),
        ):
            self.assertTrue(any(
                message in item for item in all_findings(mutation)
            ))

        unmanaged = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<div popover>Hidden</div><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==" '
            'attributionsrc="https://example.invalid/report">',
            1,
        )
        self.assertTrue(any(
            "unmanaged native invocation" in item
            for item in all_findings(unmanaged)
        ))
        for live_region in (
            '<div role="status" aria-live="assertive">Unmanaged status</div>',
            '<div aria-live="assertive">Unmanaged live region</div>',
            '<div aria-atomic="true">Unmanaged atomic region</div>',
            '<output>Unmanaged native status</output>',
        ):
            mutation = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + live_region,
                1,
            )
            self.assertTrue(any(
                "outside the fixed application-announcer" in item
                for item in all_findings(mutation)
            ), live_region)
        for native_widget in (
            '<progress value="0.5" max="1"></progress>',
            '<meter value="5" min="0" max="10"></meter>',
        ):
            mutation = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + native_widget,
                1,
            )
            self.assertTrue(any(
                "native widgets outside the fixed control model" in item
                for item in all_findings(mutation)
            ), native_widget)
        for emerging_capability in (
            '<geolocation autolocate></geolocation>',
            '<permission type="geolocation"></permission>',
            '<usermedia></usermedia>',
            '<install></install>',
            '<model></model>',
            '<x-capability></x-capability>',
        ):
            mutation = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + emerging_capability,
                1,
            )
            self.assertTrue(any(
                "forbidden dependency elements" in item
                for item in all_findings(mutation)
            ), emerging_capability)
        for attribute in ("accesskey", "autofocus", "contenteditable", "draggable"):
            unmanaged_attribute = self.rendered_template().replace(
                "<p data-private>",
                f"<p data-private {attribute}>",
                1,
            )
            self.assertTrue(any(
                "unmanaged native invocation" in item
                for item in all_findings(unmanaged_attribute)
            ), attribute)
        until_found = self.rendered_template().replace(
            "<p data-private>", '<p hidden="until-found" data-private>', 1,
        )
        self.assertTrue(any(
            "hidden attributes must use only the canonical empty or hidden boolean value"
            in item for item in all_findings(until_found)
        ))
        closed_details = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<details><summary hidden>Reveal</summary>"
            "<p data-private>This file is the only visual Experience implementation.</p>"
            "</details>",
            1,
        )
        self.assertTrue(any(
            "forbidden dependency elements" in item
            for item in all_findings(closed_details)
        ))
        for unstable in (
            "area", "audio", "canvas", "datalist", "frame", "frameset",
            "image", "listing", "map", "math", "nobr", "ruby", "rt",
            "rp", "svg", "video",
        ):
            mutation = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                f"<{unstable}>Parser-unstable text</{unstable}>"
                "<p data-private>This file is the only visual Experience implementation.</p>",
                1,
            )
            self.assertTrue(any(
                "forbidden dependency elements" in item
                for item in all_findings(mutation)
            ), unstable)

        dead_button = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<button type="button">Unmanaged</button>',
            1,
        )
        self.assertTrue(any(
            "every interactive element must have one validated" in item
            for item in all_findings(dead_button)
        ))
        for role in (
            "grid", "menuitemcheckbox", "menuitemradio", "scrollbar",
            "treeitem",
        ):
            dead_widget = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                f'<div role="{role}">Unmanaged widget</div>',
                1,
            )
            self.assertTrue(any(
                "every interactive element must have one validated" in item
                for item in all_findings(dead_widget)
            ), role)
        for tabindex in ("+0", "00", "-0", " 0 ", "01", "1", "-1"):
            dead_focus = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                f'<div tabindex="{tabindex}">Unmanaged focus</div>',
                1,
            )
            self.assertTrue(any(
                "every interactive element must have one validated" in item
                for item in all_findings(dead_focus)
            ), tabindex)
        missing_alt = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==">',
            1,
        )
        self.assertTrue(any(
            "img must declare an alt" in item
            for item in all_findings(missing_alt)
        ))
        missing_src = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<img alt="Product diagram">',
            1,
        )
        self.assertTrue(any(
            "valid static PNG src" in item
            for item in all_findings(missing_src)
        ))
        zero_sized_record = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<img data-private data-experience-ref="author-experience:SCR-001@r1" '
            'alt="Checkout" width="0" height="0" '
            'src="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==">',
            1,
        )
        zero_sized_findings = all_findings(zero_sized_record)
        self.assertTrue(any(
            "HTML presentational attributes" in item
            for item in zero_sized_findings
        ))
        self.assertTrue(any(
            "rendered Experience record" in item
            for item in zero_sized_findings
        ))
        self.assertIn(
            "application needs privacy-masked content", zero_sized_findings,
        )
        for clobber in ("getElementById", "querySelectorAll", "addEventListener"):
            clobbered_document = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                f'<form name="{clobber}"><p data-private>'
                "This file is the only visual Experience implementation.</p></form>",
                1,
            )
            self.assertTrue(any(
                "shadow fixed runtime DOM properties" in item
                for item in all_findings(clobbered_document)
            ), clobber)
        intrinsic_private = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<input type="hidden" data-private value="Invisible secret">',
            1,
        )
        intrinsic_findings = all_findings(intrinsic_private)
        self.assertTrue(any(
            "privacy-masked content must be non-empty and reachable" in item
            for item in intrinsic_findings
        ))
        self.assertIn(
            "application needs privacy-masked content", intrinsic_findings
        )
        invisible_theme_name = self.rendered_template().replace(
            'aria-label="Toggle color theme"', 'aria-label="&#x200b;"', 1,
        )
        self.assertIn(
            "application needs accessible theme control",
            all_findings(invisible_theme_name),
        )
        invisible_title = self.rendered_template().replace(
            "<title>Application acceptance prototype</title>",
            "<title>&#x200b;</title>",
            1,
        )
        self.assertTrue(any(
            "document title" in item for item in all_findings(invisible_title)
        ))
        invisible_private = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>&#x200b;</p>",
            1,
        )
        self.assertIn(
            "application needs privacy-masked content",
            all_findings(invisible_private),
        )
        valueless_private = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<br data-private value="Invisible secret">',
            1,
        )
        valueless_findings = all_findings(valueless_private)
        self.assertIn(
            "application needs privacy-masked content", valueless_findings
        )
        self.assertTrue(any(
            "privacy-masked content must be non-empty" in item
            for item in valueless_findings
        ))
        datalist_search = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<input type="search" aria-label="Search" data-application-search>'
            '<datalist data-search-item><option>Invisible result</option></datalist>',
            1,
        )
        self.assertTrue(any(
            "same-route search items" in item
            for item in all_findings(datalist_search)
        ))
        for unsafe_css in (
            ".x{overflow:visible\u0085auto}",
            ".x{align-items:first\u0085baseline}",
            ".x{white-space:normal\u0085}",
            ".x{display:block\u0085}",
            r".x{display:block\85 }",
        ):
            self.assertIn(
                "invalid-css",
                experience_application_check.hard_coded_author_properties(
                    unsafe_css,
                    experience_application_check.REQUIRED_APPLICATION_ROOT_TOKENS,
                ),
                unsafe_css,
            )
        invalid_class_separator = self.rendered_template().replace(
            '<p data-private>', '<p class="one\u0085two" data-private>', 1,
        )
        self.assertTrue(any(
            "class token lists may use only ASCII whitespace" in item
            for item in all_findings(invalid_class_separator)
        ))
        for fragment in (
            '<span id="search-purpose" data-private>Search catalogue</span>'
            '<input type="search" data-application-search '
            'aria-labelledby="search-purpose"><p data-search-item>Result</p>',
            '<input type="search" data-application-search '
            'aria-labelledby="search-purpose"><p data-search-item>'
            '<span id="search-purpose">Search items</span></p>'
            '<p data-search-item>Another result</p>',
            '<label>Choice <select data-context-key="choice">'
            '<option value="shown">Shown</option>'
            '<option id="hidden-purpose" value="other">Invisible purpose</option>'
            '</select></label><input data-context-key="field" '
            'aria-labelledby="hidden-purpose">',
            '<label>Editable label <textarea id="dynamic-label" '
            'data-context-key="label">Initial purpose</textarea></label>'
            '<input data-context-key="field" aria-labelledby="dynamic-label">',
            '<span id="search-purpose">Search</span>'
            '<input type="search" data-application-search '
            'aria-labelledby="\u0085search-purpose">'
            '<p data-search-item>Result</p>',
            '<label>Search\u0085<input type="search" '
            'data-application-search></label><p data-search-item>Result</p>',
            '<label><span hidden>Search</span><input type="search" '
            'data-application-search></label><p data-search-item>Result</p>',
            '<label><span inert>Search</span><input type="search" '
            'data-application-search></label><p data-search-item>Result</p>',
            '<label><span aria-hidden="true">Search</span>'
            '<input type="search" data-application-search></label>'
            '<p data-search-item>Result</p>',
        ):
            fragment_findings = all_findings(
                self.rendered_template().replace(
                    '<p data-private>This file is the only visual Experience implementation.</p>',
                    '<p data-private>This file is the only visual Experience implementation.</p>'
                    + fragment,
                    1,
                )
            )
            self.assertTrue(any(
                "input[type=search]" in item
                or "data-context-key requires" in item
                for item in fragment_findings
            ), (fragment, fragment_findings))
        filter_separator = self.rendered_template().replace(
            '<p data-private>This file is the only visual Experience implementation.</p>',
            '<p data-private>This file is the only visual Experience implementation.</p>'
            '<label>Filter <select data-application-filter>'
            '<option value="">All</option><option value="open">Open</option>'
            '<option value="closed">Closed</option></select></label>'
            '<p data-filter-item data-filter-value="open\u0085closed">Both states</p>',
            1,
        )
        self.assertTrue(any(
            "exact same-route filter values" in item
            for item in all_findings(filter_separator)
        ))
        for raw_refs in ("\u0085description", "description\u00a0peer"):
            invalid_idrefs = self.rendered_template().replace(
                '<button type="button" data-application-action="toggle-privacy"',
                '<p id="description">Description</p>'
                '<button type="button" aria-describedby="' + raw_refs + '" '
                'data-application-action="toggle-privacy"',
                1,
            )
            self.assertTrue(any(
                "passive ARIA descriptions" in item
                for item in all_findings(invalid_idrefs)
            ), raw_refs)
        overridden_description = self.rendered_template().replace(
            '<p data-private>This file is the only visual Experience implementation.</p>',
            '<p data-private>This file is the only visual Experience implementation.</p>'
            '<label>Search <input type="search" data-application-search '
            'aria-describedby="description-target"></label>'
            '<p id="description-target" aria-label="Different help">'
            'Visible help</p><p data-search-item>Result</p>',
            1,
        )
        self.assertTrue(any(
            "passive ARIA descriptions" in item
            for item in all_findings(overridden_description)
        ))
        private_heading = self.rendered_template().replace(
            '<h1 id="author-route-title">',
            '<h1 id="author-route-title" data-private>',
            1,
        )
        private_heading_findings = all_findings(private_heading)
        self.assertTrue(any(
            "visible heading" in item for item in private_heading_findings
        ))
        self.assertIn(
            "data-private must identify a text-only passive leaf outside application identity and controls",
            private_heading_findings,
        )
        forbidden_route_text = self.rendered_template().replace(
            "Replace this route with the approved Experience set</h1>",
            "Replace this route with the approved Experience set\u0085</h1>",
            1,
        )
        self.assertTrue(any(
            "visible heading" in item
            for item in all_findings(forbidden_route_text)
        ))
        for injected_control in (
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false" aria-label="Save\u0085">Save</button>',
            '<button type="button" data-application-action="toggle-pressed" '
            'aria-pressed="false">Save\u0085</button>',
        ):
            invalid_control = self.rendered_template().replace(
                '<p data-private>This file is the only visual Experience implementation.</p>',
                '<p data-private>This file is the only visual Experience implementation.</p>'
                + injected_control,
                1,
            )
            self.assertTrue(any(
                "non-empty accessible name" in item
                or "non-empty visible label" in item
                for item in all_findings(invalid_control)
            ), injected_control)
        empty_href = self.scan(self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<a href="">Reload</a>',
            1,
        ))
        self.assertIn(("href", ""), empty_href.targets)

    def test_context_groups_are_leaf_typed_and_shape_compatible(self):
        def validate(fragment: str) -> list[str]:
            text = self.rendered_template().replace(
                "<p data-private>This file is the only visual Experience implementation.</p>",
                "<p data-private>This file is the only visual Experience implementation.</p>"
                + fragment,
                1,
            )
            scanner = self.scan(text)
            findings: list[str] = []
            contract = experience_application_check.parse_contract(scanner, findings)
            experience_application_check.validate_contract(
                contract, {}, {"author-experience"}, scanner, [], findings,
                authoring=True,
            )
            return findings

        empty_context = validate(
            '<input type="search" data-application-search '
            'data-context-key="" aria-label="Search">'
            '<select data-application-filter data-context-key="" '
            'aria-label="Filter"><option value="all">All</option></select>'
            '<p data-search-item data-filter-item data-filter-value="all">Item</p>'
        )
        self.assertTrue(any(
            "data-context-key must use a normalized lowercase identifier"
            in item for item in empty_context
        ), empty_context)

        generic = validate('<div data-context-key="unsafe"><button>Lost</button></div>')
        self.assertTrue(any("supported leaf control" in item for item in generic))
        for impossible_context in (
            '<label>Quantity <input type="number" min="10" max="1" '
            'data-context-key="qty"></label>',
            '<label>Quantity <input type="number" step="0" '
            'data-context-key="qty"></label>',
            '<label>Reference <input minlength="3" maxlength="2" '
            'data-context-key="reference"></label>',
            '<label>Choice <select required data-context-key="choice">'
            '<option value="">Only empty</option></select></label>',
        ):
            self.assertIn(
                "data-context-key native controls need type-appropriate mechanically satisfiable constraints",
                validate(impossible_context),
                impossible_context,
            )
        checkboxes = validate(
            '<input type="checkbox" data-context-key="choice" value="same">'
            '<input type="checkbox" data-context-key="choice" value="same">'
        )
        self.assertTrue(any("checkbox context groups" in item for item in checkboxes))
        radios = validate(
            '<input type="radio" name="one" checked data-context-key="choice" value="a">'
            '<input type="radio" name="two" checked data-context-key="choice" value="b">'
        )
        self.assertTrue(any("radio context groups" in item for item in radios))
        duplicate_radio_names = validate(
            '<input type="radio" name="choice" aria-label="Same" '
            'data-context-key="choice" value="a">'
            '<input type="radio" name="choice" aria-label="Same" '
            'data-context-key="choice" value="b">'
        )
        self.assertTrue(any(
            "radio context groups" in item for item in duplicate_radio_names
        ))
        duplicate_checkbox_names = validate(
            '<input type="checkbox" aria-label="Same" '
            'data-context-key="choice" value="a">'
            '<input type="checkbox" aria-label="Same" '
            'data-context-key="choice" value="b">'
        )
        self.assertIn(
            "checkbox context groups need distinct accessible choice names",
            duplicate_checkbox_names,
        )
        split_form_radios = validate(
            '<form><input type="radio" name="choice" aria-label="Choice A" '
            'data-context-key="choice" value="a"></form>'
            '<form><input type="radio" name="choice" aria-label="Choice B" '
            'data-context-key="choice" value="b"></form>'
        )
        self.assertIn(
            "radio context groups must share one native form owner",
            split_form_radios,
        )
        colliding_radios = validate(
            '<input type="radio" name="choice" data-context-key="first" value="a">'
            '<input type="radio" name="choice" data-context-key="second" value="b">'
        )
        self.assertTrue(any(
            "must belong to exactly one route/context identity" in item
            for item in colliding_radios
        ))
        implicit_select = validate(
            '<select data-context-key="choice"><option>Implicit</option></select>'
        )
        self.assertTrue(any(
            "explicit unique values" in item for item in implicit_select
        ))
        duplicate_select = validate(
            '<select data-context-key="choice"><option value="same">One</option>'
            '<option value="same">Two</option></select>'
        )
        self.assertTrue(any(
            "explicit unique values" in item for item in duplicate_select
        ))
        duplicate_select_labels = validate(
            '<select aria-label="Choice" data-context-key="choice">'
            '<option value="a">Same</option><option value="b">Same</option>'
            '</select>'
        )
        self.assertTrue(any(
            "visible, enabled, named options" in item
            for item in duplicate_select_labels
        ))
        for options in (
            '<optgroup hidden label="Hidden"><option value="x">X</option></optgroup>',
            '<optgroup disabled label="Disabled"><option value="x">X</option></optgroup>',
            '<option hidden value="x">X</option>',
            '<option disabled selected value="x">X</option>',
            '<option value="x"></option>',
        ):
            invalid_options = validate(
                '<select aria-label="Choice" data-context-key="choice">'
                + options + '</select>'
            )
            self.assertTrue(any(
                "visible, enabled, named options" in item
                for item in invalid_options
            ), options)
        invisible_explicit_context_label = validate(
            '<select aria-label="Choice" data-context-key="choice">'
            '<option value="x" label="&#x200b;">Visible fallback text</option>'
            '</select>'
        )
        self.assertTrue(any(
            "visible, enabled, named options" in item
            for item in invisible_explicit_context_label
        ))
        unnamed_context = validate(
            '<input data-context-key="customer_id" value="123">'
        )
        self.assertTrue(any(
            "one named, reachable supported leaf control" in item
            for item in unnamed_context
        ))
        aria_only_context = validate(
            '<input data-context-key="customer_id" value="123" '
            'aria-label="Customer">'
        )
        self.assertTrue(any(
            "one named, reachable supported leaf control" in item
            for item in aria_only_context
        ))
        for unreachable_context in (
            '<input aria-label="Name" data-context-key="name" tabindex="-1">',
            '<textarea aria-label="Notes" data-context-key="notes" '
            'tabindex="-1"></textarea>',
            '<select aria-label="Choice" data-context-key="choice" '
            'tabindex="-1"><option value="x">X</option></select>',
        ):
            self.assertTrue(any(
                "one named, reachable supported leaf control" in item
                for item in validate(unreachable_context)
            ), unreachable_context)
        contradictory_checked = validate(
            '<input type="checkbox" aria-label="Choice" aria-checked="true" '
            'data-context-key="choice" value="yes">'
        )
        self.assertTrue(any(
            "one named, reachable supported leaf control" in item
            for item in contradictory_checked
        ))

        constrained = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<p data-private>This file is the only visual Experience implementation.</p>'
            '<input type="range" min="0" max="10" value="5" '
            'aria-label="Quantity" data-context-key="qty">'
            '<button type="button" data-transition-ref="author-experience:TRN-001@r1" '
            'data-route-target="#/target" data-preserve-context="qty" '
            'data-return-route="#/author-route">Continue</button>',
            1,
        ).replace(
            "  </main>",
            '    <section data-application-route="#/target" '
            'data-application-state="ordinary" aria-labelledby="target-title">'
            '<h1 id="target-title">Target</h1><p data-private>Target content</p>'
            '<input type="range" min="100" max="200" value="150" '
            'aria-label="Quantity" data-context-key="qty">'
            '<button type="button" data-application-action="return-route">Return</button>'
            '</section>\n  </main>',
            1,
        )
        constrained_scanner = self.scan(constrained)
        constrained_findings: list[str] = []
        constrained_contract = experience_application_check.parse_contract(
            constrained_scanner, constrained_findings
        )
        constrained_contract["routes"][0]["transitions"] = [{
            "transition_ref": "author-experience:TRN-001@r1",
            "target": "#/target",
            "outcome": "ordinary",
            "preserve_context": ["qty"],
            "return_route": "#/author-route",
        }]
        constrained_contract["routes"].append({
            "route": "#/target",
            "state_class": "ordinary",
            "experience_id": "author-experience",
            "label": "Target",
            "record_refs": [],
            "transitions": [],
        })
        experience_application_check.validate_contract(
            constrained_contract, {}, {"author-experience"},
            constrained_scanner, [], constrained_findings, authoring=True,
        )
        self.assertTrue(any(
            "context key qty has incompatible source and target control shapes"
            in item for item in constrained_findings
        ), constrained_findings)

    def test_state_records_must_render_only_their_declared_state(self):
        reference = "author-experience:STA-001@r1"
        text = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            '<p data-private data-experience-ref="' + reference + '">State</p>',
            1,
        )
        scanner = self.scan(text)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        contract["routes"][0]["record_refs"] = [reference]
        maps = [{
            "experience_id": "author-experience",
            "bindings": [{
                "record_ref": reference,
                "entries": [
                    {"route": "#/author-route", "state_class": "ordinary"},
                    {"route": "#/missing-failure", "state_class": "failure"},
                ],
            }],
        }]
        experience_application_check.validate_contract(
            contract,
            {reference: "failure"},
            {"author-experience"},
            scanner,
            maps,
            findings,
            authoring=False,
        )
        self.assertIn(
            f"state record {reference} is not rendered as failure", findings
        )

        empty_text = text.replace(">State</p>", "></p>", 1)
        empty_scanner = self.scan(empty_text)
        empty_findings: list[str] = []
        empty_contract = experience_application_check.parse_contract(
            empty_scanner, empty_findings
        )
        empty_contract["routes"][0]["record_refs"] = [reference]
        experience_application_check.validate_contract(
            empty_contract, {}, {"author-experience"}, empty_scanner, [],
            empty_findings, authoring=True,
        )
        self.assertTrue(any(
            "non-empty accessible content" in item for item in empty_findings
        ))

    def test_nonempty_contract_mechanically_requires_working_feature_families(self):
        scanner = self.scan(self.rendered_template())
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings,
            authoring=False,
        )
        feature = next(
            item for item in findings
            if "working interaction coverage is missing" in item
        )
        for label in (
            "forms", "filters", "search", "custom menus", "custom listboxes",
            "drawers", "modal/overlay", "onboarding", "settings",
        ):
            self.assertIn(label, feature)

    def test_disclosures_are_same_route_reachable_and_state_consistent(self):
        text = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<nav id="route-drawer"></nav>',
            1,
        ).replace(
            "<strong>Application acceptance prototype</strong>",
            '<strong>Application acceptance prototype</strong>'
            '<button type="button" data-application-action="toggle-drawer" '
            'aria-controls="route-drawer" aria-expanded="false">Drawer</button>',
            1,
        )
        scanner = self.scan(text)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings, authoring=True
        )
        self.assertTrue(any("same route" in item for item in findings))
        self.assertTrue(any("hidden state must match" in item for item in findings))

    def test_nonempty_application_simulations_cover_each_nonordinary_state(self):
        scanner = self.scan(self.rendered_template())
        parse_findings: list[str] = []
        base = experience_application_check.parse_contract(scanner, parse_findings)
        failure = {
            "route": "#/author-route/failure",
            "state_class": "failure",
            "experience_id": "author-experience",
            "label": "Failure",
            "record_refs": [],
            "transitions": [],
        }
        retry = dict(
            failure,
            route="#/author-route/retry",
            state_class="retry",
            label="Retry",
        )
        base["routes"].extend([failure, retry])
        findings: list[str] = []
        experience_application_check.validate_contract(
            base, {}, {"author-experience"}, scanner, [], findings, authoring=True
        )
        simulation_message = next(
            item for item in findings if "deterministic simulations" in item
        )
        self.assertIn("failure", simulation_message)
        self.assertIn("retry", simulation_message)

        base["simulations"] = [{
            "simulation_id": "simulate-failure",
            "source": "#/author-route",
            "outcome": "failure",
            "target": "#/author-route/failure",
            "return_route": "#/author-route",
        }]
        incomplete: list[str] = []
        experience_application_check.validate_contract(
            base, {}, {"author-experience"}, scanner, [], incomplete, authoring=True
        )
        incomplete_message = next(
            item for item in incomplete if "deterministic simulations" in item
        )
        self.assertNotIn("failure", incomplete_message)
        self.assertIn("retry", incomplete_message)

    def test_runtime_scopes_search_and_reads_context_via_shared_adapter(self):
        runtime = experience_application_check.template_runtime()
        self.assertIn(
            'elementClosest(search, "[data-application-route]") || main', runtime
        )
        self.assertIn(
            "routeContext(route)[key] = readContextGroup(contextElements(route, key));",
            runtime,
        )
        self.assertIn(
            "Object.assign(routeContext(target), captureContext(source, keys));",
            runtime,
        )
        self.assertIn("if (scope) applyCollectionFilters(scope);", runtime)
        self.assertIn(
            'addEventListener(window, "popstate", () => reconcileHistory(true));',
            runtime,
        )
        self.assertIn(
            ')].filter((candidate) => !elementClosest(candidate, "[hidden]"));',
            runtime,
        )

    def test_file_context_is_rejected_before_runtime_assignment(self):
        text = self.rendered_template().replace(
            "<p data-private>This file is the only visual Experience implementation.</p>",
            "<p data-private>This file is the only visual Experience implementation.</p>"
            '<input type="file" data-context-key="attachment">',
            1,
        )
        scanner = self.scan(text)
        findings: list[str] = []
        contract = experience_application_check.parse_contract(scanner, findings)
        experience_application_check.validate_contract(
            contract, {}, {"author-experience"}, scanner, [], findings,
            authoring=True,
        )
        self.assertIn(
            "file inputs cannot preserve context through the fixed local runtime",
            findings,
        )

    def test_application_map_loader_consumes_v2_route_state_schema(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "checkout"
            target = package / "artifacts/application-map.json"
            target.parent.mkdir(parents=True)
            valid = {
                "schema_version": 2,
                "application_path": "experience-design/artifacts/application.html",
                "experience_id": "checkout",
                "bindings": [{
                    "record_ref": "checkout:STA-001@r1",
                    "entries": [{
                        "route": "#/checkout/failure",
                        "state_class": "failure",
                    }],
                }],
            }
            target.write_bytes(experience_application_check.canonical(valid))
            normalized, findings = (
                experience_application_check.load_application_map(package)
            )
            self.assertEqual(findings, [])
            self.assertEqual(normalized, valid)

            legacy = dict(valid)
            legacy["schema_version"] = 1
            legacy["bindings"] = [{
                "record_ref": "checkout:STA-001@r1",
                "routes": ["#/checkout/failure"],
            }]
            target.write_bytes(experience_application_check.canonical(legacy))
            _normalized, findings = (
                experience_application_check.load_application_map(package)
            )
            self.assertTrue(any("unsupported schema" in row for row in findings))
            self.assertTrue(any("must contain exactly" in row for row in findings))

    def test_verified_ledger_is_exact_contiguous_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            root.mkdir()
            first = self.registry(1)
            second = self.registry(2, first["application_hash"])
            experience_application_check.write_registry_and_ledger(root, first)
            rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertEqual(findings, [])
            self.assertEqual(rows, [first])

            with self.assertRaisesRegex(ValueError, "replay and gaps"):
                experience_application_check.write_registry_and_ledger(root, first)
            with self.assertRaisesRegex(ValueError, "replay and gaps"):
                experience_application_check.write_registry_and_ledger(
                    root, self.registry(3)
                )

            generated = root / "_generated/application-registry.json"
            ledger = root / "_ledger/application-revisions.json"
            generated_before = generated.read_bytes()
            ledger_before = ledger.read_bytes()
            with mock.patch.object(
                experience_application_check.os,
                "replace",
                side_effect=OSError("injected replace failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected replace failure"):
                    experience_application_check.write_registry_and_ledger(root, second)
            self.assertEqual(generated.read_bytes(), generated_before)
            self.assertEqual(ledger.read_bytes(), ledger_before)
            self.assertEqual(list(root.rglob("*.tmp")), [])

            experience_application_check.write_registry_and_ledger(root, second)
            rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertEqual(findings, [])
            self.assertEqual(rows, [first, second])
            original = ledger.read_bytes()

            forged = json.loads(json.dumps({
                "schema_version": 2,
                "revisions": rows,
            }))
            forged_first = forged["revisions"][0]
            forged_first["source_hash"] = "sha256:" + "2" * 64
            forged_first["application_hash"] = experience_application_check.sha(
                experience_application_check.canonical({
                    key: value for key, value in forged_first.items()
                    if key != "application_hash"
                })
            )
            ledger.write_bytes(experience_application_check.canonical(forged))
            _rows, findings = experience_application_check.verified_application_ledger(
                root
            )
            self.assertTrue(any("hash chain" in item for item in findings))
            ledger.write_bytes(original)

            malformed = {"schema_version": 2, "revisions": [first, self.registry(3)]}
            ledger.write_bytes(experience_application_check.canonical(malformed))
            _rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertTrue(any("ordered, contiguous" in item for item in findings))

            wrong_type = self.registry(2)
            wrong_type["application_revision"] = True
            unsigned = {
                key: value for key, value in wrong_type.items()
                if key != "application_hash"
            }
            wrong_type["application_hash"] = experience_application_check.sha(
                experience_application_check.canonical(unsigned)
            )
            ledger.write_bytes(experience_application_check.canonical({
                "schema_version": 2,
                "revisions": [first, wrong_type],
            }))
            _rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertTrue(any("positive integer" in item for item in findings))

            tampered = self.registry(2)
            tampered["application_hash"] = "sha256:" + "0" * 64
            ledger.write_bytes(experience_application_check.canonical({
                "schema_version": 2,
                "revisions": [first, tampered],
            }))
            _rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertTrue(any("does not match its receipt" in item for item in findings))

            ledger.write_bytes(experience_application_check.canonical({
                "schema_version": 2,
                "revisions": [first],
                "unexpected": True,
            }))
            _rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertTrue(any("exact root fields" in item for item in findings))

            extra_row = dict(first, unexpected=True)
            ledger.write_bytes(experience_application_check.canonical({
                "schema_version": 2,
                "revisions": [extra_row],
            }))
            _rows, findings = experience_application_check.verified_application_ledger(root)
            self.assertTrue(any("exact registry fields" in item for item in findings))
            ledger.write_bytes(original)

    def test_checker_rejects_every_hard_link_under_experience_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            root.mkdir()
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("shared\n", encoding="utf-8")
            try:
                os.link(first, second)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable: {exc}")
            _registry, findings = experience_application_check.compile_application(root)
            self.assertTrue(any("hard-linked files are forbidden" in item for item in findings))

    def test_root_identity_is_checked_before_symlinks_are_resolved(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            real = state / "real/experience-design"
            real.mkdir(parents=True)
            linked = state / "workspace/docs/experience-design"
            linked.parent.mkdir(parents=True)
            try:
                linked.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                experience_application_check.root_for(linked)

            project = state / "project"
            (project / "workspace").mkdir(parents=True)
            docs = state / "real-docs"
            (docs / "experience-design").mkdir(parents=True)
            (project / "workspace/docs").symlink_to(docs, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                experience_application_check.root_for(project)

            wrong_case = state / "Experience-Design"
            wrong_case.mkdir()
            with self.assertRaisesRegex(ValueError, "exact NFC spelling and case"):
                experience_application_check.root_for(wrong_case)

    def test_gate_closes_root_and_package_artifact_surfaces(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            root_artifacts = root / "artifacts"
            package = root / "experiences/retired-process"
            package_artifacts = package / "artifacts"
            nested = package_artifacts / "nested"
            nested.mkdir(parents=True)
            root_artifacts.mkdir(parents=True)
            (root_artifacts / "sidecar.json").write_text("{}\n", encoding="utf-8")
            (package / "experience.md").write_text(
                "---\nstatus: retired\n---\n# Retired\n", encoding="utf-8"
            )
            generated = package / "_generated"
            generated.mkdir()
            for name in ("registry.json", "coverage.json"):
                (generated / name).write_text("{}\n", encoding="utf-8")
            (package_artifacts / "sidecar.png").write_bytes(b"not-an-image")
            (nested / "application-map.json").write_text("{}\n", encoding="utf-8")
            for name in ("preview.svg", "preview.png", "prototype.pdf"):
                (package / name).write_bytes(b"not-a-preview")
            alias_package = root / "experiences/alias-process"
            alias_artifacts = alias_package / "ARTIFACTS"
            alias_artifacts.mkdir(parents=True)
            (alias_artifacts / "application-map.json").write_text("{}\n", encoding="utf-8")

            _registry, findings = experience_application_check.compile_application(
                root, gate=True
            )
            messages = "\n".join(findings)
            self.assertIn(
                "Experience root/artifacts/sidecar.json: closed artifact surface permits only application.html",
                messages,
            )
            self.assertIn(
                "experiences/retired-process/artifacts/sidecar.png: closed artifact surface permits only application-map.json",
                messages,
            )
            self.assertIn(
                "experiences/retired-process/artifacts/nested/application-map.json: closed artifact surface permits only application-map.json",
                messages,
            )
            self.assertIn(
                "reserved artifacts directory must use exact NFC spelling and case",
                messages,
            )
            for name in ("preview.svg", "preview.png", "prototype.pdf"):
                self.assertIn(
                    f"experiences/retired-process/{name}: Experience Design closed file surface does not permit this path",
                    messages,
                )
            self.assertNotIn(
                "experiences/retired-process/_generated/coverage.json: "
                "Experience Design closed file surface does not permit this path",
                messages,
            )

    def test_fixed_runtime_executes_context_outcomes_retry_recovery_and_return(self):
        contract = {
            "schema_version": 2,
            "entry_route": "#/checkout",
            "state_classes": [
                "ordinary", "loading", "empty", "validation", "permission",
                "stale", "conflict", "failure", "retry", "recovery",
            ],
            "routes": [
                {
                    "route": "#/checkout",
                    "state_class": "ordinary",
                    "experience_id": "checkout",
                    "label": "Checkout",
                    "record_refs": [],
                    "transitions": [{
                        "transition_ref": "checkout:TRN-001@r1",
                        "target": "#/returns/loading",
                        "outcome": "loading",
                        "preserve_context": [
                            "cart_id", "notifications", "payment", "query",
                        ],
                        "return_route": "#/checkout",
                    }],
                },
                {
                    "route": "#/returns/loading",
                    "state_class": "loading",
                    "experience_id": "returns",
                    "label": "Loading",
                    "record_refs": [],
                    "transitions": [{
                        "transition_ref": "returns:TRN-001@r1",
                        "target": "#/returns/failure",
                        "outcome": "failure",
                        "preserve_context": [],
                        "return_route": "#/returns/loading",
                    }],
                },
                {
                    "route": "#/returns/failure",
                    "state_class": "failure",
                    "experience_id": "returns",
                    "label": "Failure",
                    "record_refs": [],
                    "transitions": [{
                        "transition_ref": "returns:TRN-002@r1",
                        "target": "#/returns/retry",
                        "outcome": "retry",
                        "preserve_context": [],
                        "return_route": "#/returns/loading",
                    }],
                },
                {
                    "route": "#/returns/retry",
                    "state_class": "retry",
                    "experience_id": "returns",
                    "label": "Retry",
                    "record_refs": [],
                    "transitions": [],
                },
                {
                    "route": "#/returns/recovery",
                    "state_class": "recovery",
                    "experience_id": "returns",
                    "label": "Recovery",
                    "record_refs": [],
                    "transitions": [],
                },
            ],
            "simulations": [
                {
                    "simulation_id": "fail-request",
                    "source": "#/returns/loading",
                    "outcome": "failure",
                    "target": "#/returns/failure",
                    "return_route": "#/returns/loading",
                },
                {
                    "simulation_id": "retry-request",
                    "source": "#/returns/loading",
                    "outcome": "retry",
                    "target": "#/returns/retry",
                    "return_route": "#/returns/loading",
                },
                {
                    "simulation_id": "recover-request",
                    "source": "#/returns/loading",
                    "outcome": "recovery",
                    "target": "#/returns/recovery",
                    "return_route": "#/returns/loading",
                },
            ],
        }
        harness = r"""
const assert = require("node:assert/strict");
const listeners = Object.create(null);
class Element {
  constructor(attrs = {}, text = "") {
    this.attrs = {...attrs};
    delete this.attrs.parent;
    this.parentElement = attrs.parent || null;
    this.tagName = (attrs.tag || "div").toUpperCase();
    this.textContent = text;
    this.value = attrs.value || "";
    this.checked = Boolean(attrs.checked);
    this.hidden = false;
    this.dataset = Object.create(null);
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  focus() { this.focused = true; }
  matches(selector) {
    return selector.split(",").some((part) => {
      const item = part.trim();
      if (item === "form[data-transition-ref]") {
        return this.attrs.tag === "form" &&
          Element.prototype.hasAttribute.call(this, "data-transition-ref");
      }
      if (item === "form[data-simulation-id]") {
        return this.attrs.tag === "form" &&
          Element.prototype.hasAttribute.call(this, "data-simulation-id");
      }
      if (item === '.application-skip[href="#application-main"]') {
        return this.attrs.class === "application-skip" &&
          this.attrs.href === "#application-main";
      }
      const valued = item.match(/^\[([^=]+)="([^"]*)"\]$/);
      if (valued) return Element.prototype.getAttribute.call(this, valued[1]) === valued[2];
      const match = item.match(/^\[([^\]]+)\]$/);
      return Boolean(match && Element.prototype.hasAttribute.call(this, match[1]));
    });
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) {
      if (Element.prototype.matches.call(node, selector)) return node;
    }
    return null;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll() { return []; }
}
const root = new Element();
const main = new Element({id: "application-main"});
const announcer = new Element({id: "application-announcer"});
const contractNode = new Element(
  {id: "experience-application-contract"},
  JSON.stringify(CONTRACT)
);
const views = CONTRACT.routes.map((route) => new Element({
  "data-application-route": route.route,
  "data-application-state": route.state_class,
}));
const sourceContext = new Element({"data-context-key": "cart_id", value: "cart-42"});
const targetContext = new Element({"data-context-key": "cart_id", value: ""});
const sourceSecret = new Element({"data-context-key": "secret", value: "source-only"});
const targetSecret = new Element({"data-context-key": "secret", value: ""});
const sourceQuery = new Element({
  tag: "input", type: "search", "data-context-key": "query", value: "needle",
});
const targetSearch = new Element({
  tag: "input", type: "search", "data-context-key": "query",
  "data-application-search": "", value: "",
});
const matchingItem = new Element({"data-search-item": ""}, "Needle result");
const otherItem = new Element({"data-search-item": ""}, "Other result");
const sourceEmail = new Element({
  tag: "input", type: "checkbox", "data-context-key": "notifications",
  value: "email", checked: true,
});
const sourceSms = new Element({
  tag: "input", type: "checkbox", "data-context-key": "notifications",
  value: "sms", checked: false,
});
const targetEmail = new Element({
  tag: "input", type: "checkbox", "data-context-key": "notifications",
  value: "email", checked: false,
});
const targetSms = new Element({
  tag: "input", type: "checkbox", "data-context-key": "notifications",
  value: "sms", checked: false,
});
const sourceCard = new Element({
  tag: "input", type: "radio", "data-context-key": "payment",
  value: "card", checked: false,
});
const sourceBank = new Element({
  tag: "input", type: "radio", "data-context-key": "payment",
  value: "bank", checked: true,
});
const targetCard = new Element({
  tag: "input", type: "radio", "data-context-key": "payment",
  value: "card", checked: false,
});
const targetBank = new Element({
  tag: "input", type: "radio", "data-context-key": "payment",
  value: "bank", checked: false,
});
const outcomeNodes = new Map(
  CONTRACT.routes.map((route) => [route.route, new Element({"data-application-outcome": ""})])
);
const openDialog = {open: false, close() { this.open = false; }};
const contexts = new Map([
  ["#/checkout", [
    sourceContext, sourceEmail, sourceSms, sourceCard, sourceBank,
    sourceSecret, sourceQuery,
  ]],
  ["#/returns/loading", [
    targetContext, targetEmail, targetSms, targetCard, targetBank,
    targetSecret, targetSearch,
  ]],
]);
const loadingView = views.find(
  (view) => view.getAttribute("data-application-route") === "#/returns/loading"
);
loadingView.querySelectorAll = (selector) => {
  if (selector === "[data-application-search]") return [targetSearch];
  if (selector === "[data-application-filter]") return [];
  if (selector === "[data-search-item],[data-filter-item]") {
    return [matchingItem, otherItem];
  }
  return [];
};
const byId = new Map([
  ["application-main", main],
  ["application-announcer", announcer],
  ["experience-application-contract", contractNode],
]);
const mockDocumentPrototype = {
  getElementById: (id) => byId.get(id) || null,
  addEventListener: (type, listener) => { listeners[type] = listener; },
  querySelectorAll: (selector) => {
    if (selector === "[data-application-route]") return views;
    if (selector === "dialog[open]") return openDialog.open ? [openDialog] : [];
    const context = selector.match(/^\[data-application-route="([^"]+)"\] \[data-context-key\]$/);
    if (context) return contexts.get(context[1]) || [];
    const outcome = selector.match(/^\[data-application-route="([^"]+)"\] \[data-application-outcome\]$/);
    if (outcome) return [outcomeNodes.get(outcome[1])];
    if (selector === "[data-search-item]") return [];
    return [];
  },
};
globalThis.document = Object.assign(Object.create(mockDocumentPrototype), {
  documentElement: root,
  getElementById: new Element({tag: "form", name: "getElementById"}),
  querySelectorAll: new Element({tag: "form", name: "querySelectorAll"}),
  addEventListener: new Element({tag: "form", name: "addEventListener"}),
});
globalThis.window = {
  location: {hash: ""},
  addEventListener: (type, listener) => { listeners[type] = listener; },
};
const historyEntries = [];
window.history = {
  state: null,
  pushState(snapshot, _title, route) {
    this.state = JSON.parse(JSON.stringify(snapshot));
    window.location.hash = route;
    historyEntries.push({route, state: this.state});
  },
  replaceState(snapshot, _title, route) {
    this.state = JSON.parse(JSON.stringify(snapshot));
    window.location.hash = route;
    const entry = {route, state: this.state};
    if (historyEntries.length) historyEntries[historyEntries.length - 1] = entry;
    else historyEntries.push(entry);
  },
};
const click = (target) => listeners.click({
  target,
  preventDefault() {},
});
"""
        assertions = r"""
assert.equal(root.dataset.applicationState, "ordinary");
listeners.input({target: sourceSecret});
const themeControl = new Element({
  "data-application-action": "toggle-theme",
  "aria-pressed": "false",
});
click(themeControl);
assert.equal(root.dataset.theme, "dark");
assert.equal(root.dataset.catalogTheme, "dark");
assert.equal(themeControl.getAttribute("aria-pressed"), "true");
click(themeControl);
assert.equal(root.dataset.theme, "light");
assert.equal(root.dataset.catalogTheme, "light");
assert.equal(themeControl.getAttribute("aria-pressed"), "false");
const ariaDisabledTheme = new Element({
  "data-application-action": "toggle-theme",
  "aria-disabled": "true",
  "aria-pressed": "false",
});
click(ariaDisabledTheme);
assert.equal(ariaDisabledTheme.getAttribute("aria-pressed"), "false");
const hiddenOwner = new Element({hidden: ""});
const hiddenTheme = new Element({
  parent: hiddenOwner,
  "data-application-action": "toggle-theme",
  "aria-pressed": "false",
});
click(hiddenTheme);
assert.equal(root.dataset.theme, "light");
assert.equal(hiddenTheme.getAttribute("aria-pressed"), "false");
const transition = new Element({
  "data-transition-ref": "checkout:TRN-001@r1",
  "data-route-target": "#/returns/loading",
});
click(transition);
assert.equal(window.location.hash, "#/returns/loading");
assert.equal(root.dataset.applicationState, "loading");
assert.equal(root.dataset.applicationOutcome, "loading");
assert.equal(targetContext.value, "cart-42");
assert.equal(targetEmail.checked, true);
assert.equal(targetSms.checked, false);
assert.equal(targetCard.checked, false);
assert.equal(targetBank.checked, true);
assert.equal(targetSecret.value, "");
assert.equal(targetSearch.value, "needle");
assert.equal(matchingItem.hidden, false);
assert.equal(otherItem.hidden, true);
window.history.state = {
  experienceApplication: true,
  route: "#/returns/loading",
  returnRoutes: ["#/checkout"],
  outcome: "history-restored",
};
listeners.popstate();
assert.equal(root.dataset.applicationOutcome, "history-restored");
assert.equal(outcomeNodes.get("#/returns/loading").textContent, "history-restored");
window.location.hash = "#details";
listeners.hashchange();
assert.equal(root.dataset.applicationState, "loading");
let skipPrevented = false;
listeners.click({
  target: new Element({class: "application-skip", href: "#application-main"}),
  preventDefault() { skipPrevented = true; },
});
assert.equal(skipPrevented, true);
assert.equal(window.location.hash, "#/returns/loading");
const returnControl = new Element({"data-application-action": "return-route"});
openDialog.open = true;
const nested = new Element({"data-transition-ref": "returns:TRN-001@r1"});
click(nested);
assert.equal(window.location.hash, "#/returns/failure");
assert.equal(openDialog.open, false);
window.location.hash = "#/returns/loading";
listeners.hashchange();
assert.equal(root.dataset.applicationState, "loading");
assert.equal(root.dataset.applicationOutcome, "loading");
assert.equal(outcomeNodes.get("#/returns/loading").textContent, "loading");
click(returnControl);
assert.equal(window.location.hash, "#/checkout");
click(transition);
click(nested);
assert.equal(window.location.hash, "#/returns/failure");
const retryChain = new Element({"data-transition-ref": "returns:TRN-002@r1"});
click(retryChain);
assert.equal(window.location.hash, "#/returns/retry");
click(returnControl);
assert.equal(window.location.hash, "#/returns/loading");
click(returnControl);
assert.equal(window.location.hash, "#/checkout");
click(returnControl);
assert.equal(window.location.hash, "#/checkout");
assert.equal(sourceContext.value, "cart-42");
click(transition);
const routedForm = new Element({
  tag: "form",
  "data-transition-ref": "returns:TRN-001@r1",
});
routedForm.closest = new Element({name: "closest"});
routedForm.matches = new Element({name: "matches"});
routedForm.getAttribute = new Element({name: "getAttribute"});
routedForm.hasAttribute = new Element({name: "hasAttribute"});
let clickPrevented = false;
listeners.click({
  target: {closest: (selector) => selector.includes("application-skip") ? null : routedForm},
  preventDefault() { clickPrevented = true; },
});
assert.equal(clickPrevented, false);
assert.equal(window.location.hash, "#/returns/loading");
const hiddenSubmitOwner = new Element({hidden: ""});
const hiddenSubmitter = new Element({tag: "button", parent: hiddenSubmitOwner});
let hiddenSubmitPrevented = false;
listeners.submit({
  target: routedForm,
  submitter: hiddenSubmitter,
  preventDefault() { hiddenSubmitPrevented = true; },
});
assert.equal(hiddenSubmitPrevented, true);
assert.equal(window.location.hash, "#/returns/loading");
let submitPrevented = false;
listeners.submit({
  target: routedForm,
  preventDefault() { submitPrevented = true; },
});
assert.equal(submitPrevented, true);
assert.equal(window.location.hash, "#/returns/failure");
click(returnControl);
assert.equal(window.location.hash, "#/returns/loading");
for (const [simulationId, expectedState, target] of [
  ["fail-request", "failure", "#/returns/failure"],
  ["retry-request", "retry", "#/returns/retry"],
  ["recover-request", "recovery", "#/returns/recovery"],
]) {
  const simulation = new Element({
    "data-simulation-id": simulationId,
    "data-route-target": target,
  });
  click(simulation);
  assert.equal(root.dataset.applicationState, expectedState);
  assert.equal(root.dataset.applicationOutcome, expectedState);
  click(returnControl);
  assert.equal(root.dataset.applicationState, "loading");
}
assert.equal(announcer.textContent, "Loading");
assert.equal(main.focused, true);
"""
        source = (
            "const CONTRACT = "
            + json.dumps(contract, separators=(",", ":"))
            + ";\n"
            + harness
            + "\n"
            + experience_application_check.template_runtime()
            + "\n"
            + assertions
        )
        result = subprocess.run(
            ["node", "-e", source],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
