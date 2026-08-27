"""Reusable fixtures for the canonical Experience application contract."""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import experience_application_check
import experience_compile


STATE_CLASSES = (
    "ordinary",
    "loading",
    "empty",
    "validation",
    "permission",
    "stale",
    "conflict",
    "failure",
    "retry",
    "recovery",
)


APPLICATION_TOKEN_CSS = """:root {
  --catalog-background: #fff;
  --catalog-surface: #f4f4f4;
  --catalog-foreground: #171717;
  --catalog-muted: #525252;
  --catalog-border: #737373;
  --catalog-focus: #171717;
  --catalog-accent: #2563eb;
  --catalog-success: #166534;
  --catalog-warning: #92400e;
  --catalog-error: #b91c1c;
  --catalog-font-heading: ui-sans-serif, system-ui, sans-serif;
  --catalog-font-body: ui-sans-serif, system-ui, sans-serif;
  --catalog-line-height: 1.5;
  --catalog-focus-width: 2px;
  --catalog-focus-offset: 3px;
  --catalog-border-width: 1px;
  --catalog-header-layer: 10;
  --catalog-content-width: 72rem;
  --catalog-gutter: 1.5rem;
  --catalog-scroll-offset: 5rem;
  --catalog-card-min-width: 16rem;
  --catalog-touch-target: 2.75rem;
  --catalog-swatch-height: 5rem;
  --catalog-type-display-size: 3rem;
  --catalog-type-display-weight: 700;
  --catalog-space-xs: 0.25rem;
  --catalog-space-sm: 0.5rem;
  --catalog-space-md: 1rem;
  --catalog-space-lg: 1.5rem;
  --catalog-space-xl: 2rem;
  --catalog-space-2xl: 3rem;
  --catalog-space-3xl: 4rem;
  --catalog-radius-sm: 4px;
  --catalog-radius-md: 8px;
  --catalog-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --catalog-motion-fast: 150ms;
  --catalog-motion-easing: ease-out;
}
[data-catalog-theme="dark"] {
  --catalog-background: #171717;
  --catalog-surface: #262626;
  --catalog-foreground: #f4f4f4;
  --catalog-muted: #d4d4d4;
  --catalog-border: #a3a3a3;
  --catalog-focus: #f4f4f4;
  --catalog-accent: #93c5fd;
  --catalog-success: #86efac;
  --catalog-warning: #fde68a;
  --catalog-error: #fca5a5;
}
@media (max-width: 768px) {
  :root {
    --catalog-gutter: 1rem;
    --catalog-type-display-size: 2.25rem;
  }
}
"""


def application_token_markdown(background: str = "#fff") -> str:
    """Return a complete Design System token fence for app-bound fixtures."""
    tokens = APPLICATION_TOKEN_CSS.replace(
        "--catalog-background: #fff;",
        f"--catalog-background: {background};",
        1,
    )
    return (
        "<!-- catalog:tokens:start -->\n```css\n"
        + tokens
        + "```\n<!-- catalog:tokens:end -->\n"
    )


def exact_record_refs(package: Path) -> list[str]:
    """Return every active record as its package-qualified immutable ref."""
    findings: list[str] = []
    rows = experience_compile.records(package, findings)
    if findings:
        raise AssertionError("; ".join(findings))
    return sorted(
        f"{package.name}:{row['id']}@r{row['revision']}"
        for row in rows
        if row.get("record_state") == "active"
    )


def write_application(
    root: Path,
    *,
    routes: dict[str, str] | None = None,
    transitions: dict[str, dict] | None = None,
) -> Path:
    """Author a deterministic, fully covered application over active packages."""
    application = root / "artifacts/application.html"
    text = application.read_text(encoding="utf-8")
    packages = sorted(
        (
            package
            for package in (root / "experiences").iterdir()
            if package.is_dir()
            and (package / "experience.md").is_file()
            and experience_compile.fields(package).get("status")
            not in {"retired", "retirement_pending"}
        ),
        key=lambda package: package.name,
    )
    if not packages:
        raise AssertionError("an application fixture needs an active Experience package")

    route_by_experience = routes or {
        package.name: f"#/{package.name}" for package in packages
    }
    if set(route_by_experience) != {package.name for package in packages}:
        raise AssertionError("routes must name every active Experience exactly once")

    records_by_experience: dict[str, list[dict]] = {}
    for package in packages:
        record_findings: list[str] = []
        rows = experience_compile.records(package, record_findings)
        if record_findings:
            raise AssertionError("; ".join(record_findings))
        records_by_experience[package.name] = [
            row for row in rows if row.get("record_state") == "active"
        ]
    refs_by_experience = {
        experience_id: sorted(
            f"{experience_id}:{row['id']}@r{row['revision']}"
            for row in rows
        )
        for experience_id, rows in records_by_experience.items()
    }
    transition_config = transitions or {}
    expected_transitions = {
        reference
        for references in refs_by_experience.values()
        for reference in references
        if ":TRN-" in reference
    }
    if set(transition_config) != expected_transitions:
        raise AssertionError(
            "transition fixtures must configure every active transition exactly once"
        )

    route_rows: dict[str, dict] = {}
    record_entries: dict[str, list[dict]] = {}
    for package in packages:
        route = route_by_experience[package.name]
        route_rows[route] = {
            "route": route,
            "state_class": "ordinary",
            "experience_id": package.name,
            "label": package.name.replace("-", " ").title(),
            "record_refs": [],
            "transitions": [],
        }

    first_package = packages[0]
    first_route = route_by_experience[first_package.name]
    for state_class in STATE_CLASSES:
        if state_class == "ordinary":
            continue
        state_route = f"{first_route}/state-{state_class}"
        route_rows[state_route] = {
            "route": state_route,
            "state_class": state_class,
            "experience_id": first_package.name,
            "label": f"{first_package.name.replace('-', ' ').title()} {state_class}",
            "record_refs": [],
            "transitions": [],
        }

    for package in packages:
        base_route = route_by_experience[package.name]
        for row in records_by_experience[package.name]:
            reference = f"{package.name}:{row['id']}@r{row['revision']}"
            state_class = (
                str(row.get("state_class", ""))
                if str(row.get("id", "")).startswith("STA-")
                else "ordinary"
            )
            record_route = (
                base_route
                if state_class == "ordinary"
                else f"{base_route}/state-{state_class}"
            )
            if record_route not in route_rows:
                route_rows[record_route] = {
                    "route": record_route,
                    "state_class": state_class,
                    "experience_id": package.name,
                    "label": f"{package.name.replace('-', ' ').title()} {state_class}",
                    "record_refs": [],
                    "transitions": [],
                }
            route_rows[record_route]["record_refs"].append(reference)
            record_entries[reference] = [
                {"route": record_route, "state_class": state_class}
            ]

    controls_by_route: dict[str, list[str]] = {
        route: [] for route in route_rows
    }
    context_by_route: dict[str, set[str]] = {
        route: set() for route in route_rows
    }
    return_targets: set[str] = set()
    for package in packages:
        source = route_by_experience[package.name]
        for reference in sorted(
            ref for ref in refs_by_experience[package.name] if ":TRN-" in ref
        ):
            config = transition_config[reference]
            target = str(config["target"])
            if target not in route_rows:
                raise AssertionError(f"transition target is not a fixture route: {target}")
            preserve_context = list(config.get("preserve_context", []))
            return_route = str(config.get("return_route", ""))
            edge = {
                "transition_ref": reference,
                "target": target,
                "outcome": route_rows[target]["state_class"],
                "preserve_context": preserve_context,
                "return_route": return_route,
            }
            route_rows[source]["transitions"].append(edge)
            context_by_route[source].update(preserve_context)
            context_by_route[target].update(preserve_context)
            if return_route:
                return_targets.add(target)
            controls_by_route[source].append(
                "<button type=\"button\" "
                f"data-route-target=\"{html.escape(target, quote=True)}\" "
                f"data-transition-ref=\"{html.escape(reference, quote=True)}\" "
                "data-preserve-context=\""
                f"{html.escape(','.join(preserve_context), quote=True)}\" "
                "data-return-route=\""
                f"{html.escape(return_route, quote=True)}\">Continue</button>"
            )

    entry_route = first_route
    simulations: list[dict] = []
    for index, target in enumerate(sorted(set(route_rows) - {entry_route}), start=1):
        target_row = route_rows[target]
        simulation_id = (
            f"simulate-{target_row['experience_id']}-"
            f"{target_row['state_class']}-{index}"
        )
        simulation = {
            "simulation_id": simulation_id,
            "source": entry_route,
            "outcome": target_row["state_class"],
            "target": target,
            "return_route": entry_route,
        }
        simulations.append(simulation)
        return_targets.add(target)
        if index == 1:
            controls_by_route[entry_route].append(
                "<form "
                f"data-route-target=\"{html.escape(target, quote=True)}\" "
                f"data-simulation-id=\"{html.escape(simulation_id, quote=True)}\">"
                '<label>Simulation note <input required name="simulation-note" '
                'aria-label="Simulation note"></label>'
                '<button type="submit">Submit simulation</button></form>'
            )
        else:
            controls_by_route[entry_route].append(
                "<button type=\"button\" "
                f"data-route-target=\"{html.escape(target, quote=True)}\" "
                f"data-simulation-id=\"{html.escape(simulation_id, quote=True)}\">"
                f"Simulate {html.escape(target_row['state_class'])}</button>"
            )
    for target in return_targets:
        controls_by_route[target].append(
            '<button type="button" data-application-action="return-route">'
            "Return</button>"
        )

    controls_by_route[entry_route].append(
        '<div aria-label="Review collection">'
        '<label>Search review items <input type="search" '
        'data-application-search></label>'
        '<label>Filter review items <select '
        'data-application-filter>'
        '<option value="">All</option><option value="open">Open</option>'
        '<option value="closed">Closed</option></select></label>'
        '<div data-search-item data-filter-item data-filter-value="open">Open item</div>'
        '<div data-search-item data-filter-item data-filter-value="closed">Closed item</div>'
        '</div>'
        '<p id="fixture-priority-label">Review priority</p>'
        '<div role="listbox" aria-labelledby="fixture-priority-label">'
        '<button type="button" role="option" aria-selected="true" '
        'data-value="normal" data-application-action="select-option">Normal</button>'
        '<button type="button" role="option" aria-selected="false" '
        'data-value="urgent" data-application-action="select-option">Urgent</button>'
        '</div>'
        '<button type="button" data-application-action="toggle-menu" '
        'aria-controls="fixture-menu" aria-expanded="false">Menu</button>'
        '<nav id="fixture-menu" hidden aria-label="Application menu">'
        '<button type="button" data-application-action="toggle-pressed" '
        'aria-pressed="false">Pin menu</button></nav>'
        '<button type="button" data-application-action="toggle-drawer" '
        'aria-controls="fixture-settings" aria-expanded="false">Settings</button>'
        '<aside id="fixture-settings" hidden data-application-settings '
        'aria-label="Application settings">'
        '<button type="button" data-application-action="toggle-pressed" '
        'aria-pressed="false">Compact settings</button></aside>'
        '<button type="button" data-application-action="open-modal" '
        'aria-haspopup="dialog" aria-controls="fixture-onboarding">Onboarding</button>'
        '<dialog id="fixture-onboarding" data-application-onboarding '
        'aria-labelledby="fixture-onboarding-title">'
        '<h2 id="fixture-onboarding-title">Application onboarding</h2>'
        '<button type="button" data-application-action="close-modal">Close onboarding</button>'
        '</dialog>'
    )

    sections: list[str] = []
    ordered_routes = [entry_route] + sorted(set(route_rows) - {entry_route})
    for index, route in enumerate(ordered_routes, start=1):
        row = route_rows[route]
        title_id = f"fixture-route-{index}-title"
        records = "\n".join(
            f"      <p data-experience-ref=\"{html.escape(reference, quote=True)}\">"
            f"{html.escape(reference)}</p>"
            for reference in sorted(row["record_refs"])
        )
        context = "\n".join(
            f"      <label>{html.escape(key)} <input "
            f"data-context-key=\"{html.escape(key, quote=True)}\" value=\"\"></label>"
            for key in sorted(context_by_route[route])
        )
        sections.append(
            f"    <section data-application-route=\"{html.escape(route, quote=True)}\" "
            f"data-application-state=\"{row['state_class']}\" "
            f"aria-labelledby=\"{title_id}\">\n"
            f"      <h1 id=\"{title_id}\">{html.escape(row['label'])}</h1>\n"
            "      <p data-private>This content is privacy-sensitive.</p>\n"
            f"{context}\n{records}\n"
            f"      {' '.join(controls_by_route[route])}\n"
            "    </section>"
        )

    for package in packages:
        mapping = {
            "schema_version": 2,
            "application_path": "experience-design/artifacts/application.html",
            "experience_id": package.name,
            "bindings": [
                {
                    "record_ref": reference,
                    "entries": record_entries[reference],
                }
                for reference in refs_by_experience[package.name]
            ],
        }
        (package / "artifacts/application-map.json").write_bytes(
            experience_application_check.canonical(mapping)
        )

    contract = {
        "schema_version": 2,
        "entry_route": entry_route,
        "state_classes": list(STATE_CLASSES),
        "routes": [route_rows[route] for route in ordered_routes],
        "simulations": simulations,
    }
    main = (
        '<main id="application-main" class="application-shell" tabindex="-1">\n'
        + "\n".join(sections)
        + "\n  </main>"
    )
    text, main_count = re.subn(
        r'<main id="application-main".*?</main>',
        lambda _match: main,
        text,
        count=1,
        flags=re.S,
    )
    contract_body = json.dumps(contract, indent=2, sort_keys=True)
    text, contract_count = re.subn(
        r'(<script type="application/json" id="experience-application-contract">)'
        r'.*?(</script>)',
        lambda match: f"{match.group(1)}\n{contract_body}\n  {match.group(2)}",
        text,
        count=1,
        flags=re.S,
    )
    if (main_count, contract_count) != (1, 1):
        raise AssertionError("application template no longer exposes fixture boundaries")
    application.write_text(text, encoding="utf-8")
    return application


def write_empty_application(root: Path) -> Path:
    """Author the canonical empty-state application after the last retirement."""
    application = root / "artifacts/application.html"
    text = application.read_text(encoding="utf-8")
    main = """<main id="application-main" class="application-shell" tabindex="-1">
    <section data-application-route="#/empty" data-application-state="empty" aria-labelledby="application-empty-title">
      <h1 id="application-empty-title">No active experiences</h1>
      <p data-private>There are no active process experiences.</p>
    </section>
  </main>"""
    contract = {
        "schema_version": 2,
        "entry_route": "#/empty",
        "state_classes": list(STATE_CLASSES),
        "routes": [{
            "route": "#/empty",
            "state_class": "empty",
            "experience_id": "application",
            "label": "No active experiences",
            "record_refs": [],
            "transitions": [],
        }],
        "simulations": [],
    }
    text, main_count = re.subn(
        r'<main id="application-main".*?</main>',
        lambda _match: main,
        text,
        count=1,
        flags=re.S,
    )
    contract_body = json.dumps(contract, indent=2, sort_keys=True)
    text, contract_count = re.subn(
        r'(<script type="application/json" id="experience-application-contract">)'
        r'.*?(</script>)',
        lambda match: f"{match.group(1)}\n{contract_body}\n  {match.group(2)}",
        text,
        count=1,
        flags=re.S,
    )
    if (main_count, contract_count) != (1, 1):
        raise AssertionError("application template no longer exposes empty-state boundaries")
    application.write_text(text, encoding="utf-8")
    return application


def tree_snapshot(root: Path) -> dict[str, bytes]:
    """Capture a byte-exact regular-file snapshot for rollback assertions."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
