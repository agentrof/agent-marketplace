"""Build one small but fully approved backlog package for Delivery tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import contextlib
import io
import json
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backlog_compile


CRITERION = (
    "[[business-analysis/delivery/domains/identity/acceptance/"
    "delivery-acceptance|delivery:AC-DEL-001]]"
)
EXPERIENCE = (
    "[[experience-design/programs/prg-001/releases/rel-001/release|REL-001]]"
)
DESIGN = "[[design-system/MASTER|Design Master]]"
CONSTRAINT = "[[solution-design/landscape|Solution Landscape]]"


def _write_note(path: Path, props: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")


def _complete_review_body(title: str, sections: list[str]) -> str:
    lines = [f"# {title}", ""]
    for section in sections:
        lines.extend([f"## {section}", ""])
        if section == "Deferred Criteria":
            lines.extend([
                "| criterion_ref | owner_role | reason | revisit_trigger |",
                "|---|---|---|---|",
                "",
            ])
        else:
            lines.extend([
                f"Evidence [{section}]: [[backlog/backlog|Backlog]] records the exact inputs evaluated for {section}.",
                f"Conclusion [{section}]: {section} is supported by the cited inputs and their exact relation coverage.",
                "",
            ])
    return "\n".join(lines)


def _write_upstreams(docs: Path) -> None:
    _write_note(
        docs / "business-analysis/delivery/domains/identity/acceptance/delivery-acceptance.md",
        {"type": "acceptance_set", "title": "Delivery acceptance", "status": "approved",
         "owner_role": "business_analyst", "tags": ["doc/acceptance-set", "status/approved"],
         "aliases": ["AC-DEL-001"]},
        "# Delivery acceptance\n\n| id | criterion |\n|---|---|\n| AC-DEL-001 | An account can be registered. |\n",
    )
    registry = docs / "business-analysis/delivery/_generated/registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"ids": {"AC-DEL-001": {
        "doc": "domains/identity/acceptance/delivery-acceptance.md",
        "doc_status": "approved",
    }}}), encoding="utf-8")
    _write_note(
        docs / "experience-design/programs/prg-001/releases/rel-001/release.md",
        {"type": "release", "title": "Release 1", "status": "approved",
         "owner_role": "ux_designer", "tags": ["doc/release", "status/approved"],
         "aliases": ["REL-001"]},
        "# Release 1\n\nApproved experience boundary.\n",
    )
    _write_note(
        docs / "design-system/MASTER.md",
        {"type": "design-master", "title": "Design Master", "status": "approved",
         "owner_role": "design_system_architect", "tags": ["doc/design-master", "status/approved"]},
        "# Design Master\n\nApproved design boundary.\n",
    )
    _write_note(
        docs / "solution-design/landscape.md",
        {"type": "landscape", "title": "Solution Landscape", "status": "approved",
         "owner_role": "solution_architect", "tags": ["doc/landscape", "status/approved"]},
        "# Solution Landscape\n\nApproved solution boundary.\n",
    )


def _author_story(story: Path, test_plan: Path, story_id: str) -> None:
    props, body = backlog_compile.parse_front_matter(story)
    props["scope"] = f"Deliver the observable outcome defined by {story_id}."
    props["priority_reason"] = f"{story_id} is required to achieve the approved epic outcome."
    replacements = {
        "Describe the observable user or business value.": f"Users receive the approved business outcome for {story_id}.",
        "Describe the smallest valuable behavior.": f"Deliver the observable outcome defined by {story_id}.",
        "List behavior deliberately excluded from this story.": "Administrative bulk operations remain outside this slice.",
        "- backend_developer: Own implementation and integration.": "- backend_developer: Implement the validated account boundary and API integration.",
        "- [ ] Map every cited criterion to an observable result.": "- [ ] Every cited criterion has an observable passing result.",
        "Record delivery constraints without execution state.": "Preserve the approved API boundary and avoid delivery-state metadata.",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    story.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")

    text = test_plan.read_text(encoding="utf-8")
    for coverage in backlog_compile.SCENARIO_COVERAGE_CLASSES:
        replacement = (
            f"| {coverage} | covered | {story_id}-TS-001 | |"
            if coverage == "empty" else
            f"| {coverage} | not_applicable | - | The reviewed story declares no {coverage} behavior. |"
        )
        text = text.replace(
            f"| {coverage} | not_applicable | - | TODO: assess this coverage class. |",
            replacement,
        )
    text = text.replace("the preconditions are satisfied", "an eligible customer supplies valid account details")
    text = text.replace("the user performs the story action", "the customer submits the account request")
    text = text.replace("the expected outcome is observable", "the account result and identifier are returned")
    test_plan.write_text(text, encoding="utf-8")


def make_approved_backlog(docs: Path, story_id: str = "AUTH-01") -> None:
    """Materialize one valid approved package in ``docs`` for Delivery tests."""
    _write_upstreams(docs)
    epic = "delivery-fixture"
    slug = story_id.lower()
    with contextlib.redirect_stdout(io.StringIO()):
        backlog_compile.init(SimpleNamespace(docs=str(docs)))
        backlog_compile.stub_epic(SimpleNamespace(
            docs=str(docs), slug=epic, id="EP-001", title=None,
            goal="Enable customers to access approved account capabilities.",
        ))
        backlog_compile.stub_story(SimpleNamespace(
            docs=str(docs), epic=epic, slug=slug, id=story_id, title=None,
            scope=f"Deliver the observable outcome defined by {story_id}.",
            work_kind="feature", criterion_ref=[CRITERION], experience_ref=[EXPERIENCE],
            evidence_ref=[], uses_design=[DESIGN], constrained_by=[CONSTRAINT],
        ))
    base = docs / "backlog/epics" / epic / "stories" / slug
    _author_story(base / "story.md", base / "test-plan.md", story_id)

    root_review = docs / "backlog/reviews/round-1-backlog-review.md"
    root_props, _ = backlog_compile.parse_front_matter(root_review)
    root_props.update({
        "verdict": "approved",
        "related_to": [f"[[backlog/epics/{epic}/epic|EP-001]]"],
        "dependency_refs": [],
    })
    root_review.write_text(backlog_compile.front_matter(
        root_props,
        _complete_review_body(
            str(root_props["title"]),
            backlog_compile.backlog_contract()["required_backlog_review_sections"],
        ),
    ), encoding="utf-8")
    epic_review = docs / "backlog/epics" / epic / "reviews/round-1-epic-review.md"
    epic_props, _ = backlog_compile.parse_front_matter(epic_review)
    epic_props.update({
        "verdict": "approved",
        "verifies": [
            f"[[backlog/epics/{epic}/stories/{slug}/story|{story_id}]]",
            f"[[backlog/epics/{epic}/stories/{slug}/test-plan|{story_id}-TP]]",
        ],
        "scenario_refs": [f"{story_id}-TS-001"],
        "dependency_refs": [],
    })
    epic_review.write_text(backlog_compile.front_matter(
        epic_props,
        _complete_review_body(
            str(epic_props["title"]),
            backlog_compile.backlog_contract()["required_epic_review_sections"],
        ),
    ), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        result = backlog_compile.approve(SimpleNamespace(docs=str(docs)))
    if result != 0:
        raise AssertionError("test backlog approval failed")
