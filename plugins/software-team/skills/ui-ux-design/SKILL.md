---
name: ui-ux-design
description: Search-driven design intelligence for web interfaces. Generates complete design systems (pattern, style, semantic light+dark palette, typography, radius, motion, breakpoints, icon set) from a curated corpus, persists them as a Master file plus page overrides under workspace/docs/design-system/, and enforces professional UI rules through checklists and forbidden patterns.
user-invocable: false
---

# UI/UX Design

Decision surface for design-and-experience work. The intelligence lives in the
CSV corpus under `data/`; the scripts search it, reason over it, and persist a
design system. Do not invent styles or palettes from memory when the corpus
can answer.

## When to Use

- Designing a new page or product surface: landing, dashboard, admin, e-commerce, auth, pricing
- Choosing style, color palette, typography, spacing, or layout systems
- Creating or refactoring UI components: buttons, modals, forms, tables, charts, navigation
- Reviewing UI for experience quality, accessibility, or visual consistency
- Implementing dark mode, animation, or responsive behavior
- Skip for pure backend, API, database, or infrastructure work

Decision rule: if the change alters how a feature looks, feels, moves, or is
interacted with, use this skill.

## Workflow

Run commands from the consuming project's git root: the scripts resolve
their data relative to themselves, and `--persist` writes to the relative
default `workspace/docs/design-system/`, which is only correct from the
project root. Pass `--output-dir` for any other target; never persist
while sitting inside the plugin directory.

1. **Analyze product context.** Purpose, users, dominant content (data/text/media/forms), emotional goal (trust/energy/calm), interaction pattern (scan/keyboard/input/navigate). Method: the design-rationale-method reference below.
2. **Generate the design system** (required first step):
   `python scripts/search.py "<product> <industry> <tone> <density>" --design-system -p "Project Name"`
3. **Persist Master + overrides** once a candidate is chosen:
   `python scripts/search.py "<query>" --design-system --persist -p "Project Name" [--page "dashboard"]`
   Writes `workspace/docs/design-system/MASTER.md` and `pages/<page>.md`. When building a page, read `pages/<page>.md` first; if it exists its rules override MASTER.md, otherwise follow MASTER.md strictly.
4. **Deep-dive domains** as needed:
   `python scripts/search.py "<keywords>" --domain <product|style|color|typography|landing|ux|chart|icons>`
5. **Stack guidance** for implementation fidelity:
   `python scripts/search.py "<keywords>" --stack <react|html-tailwind|shadcn>`

## Query Strategy and Divergence

- Use multi-dimensional keywords: product + industry + tone + density. "fintech dashboard data-dense trust" beats "app".
- Never ship the first candidate. Generate divergent candidates by re-running with `--style-emphasis "<style keywords>"`. Seeds: the product row's Secondary Styles (`--domain product`) and the reasoning output's decision-rule branches.
- Candidates must differ on a structural axis (restraint vs expressive, border-led vs shadow-led depth, data-first vs marketing-first), not palette shuffles. Present the axis and a rationale per decision, then refine the chosen one.
- Stuck? Re-run with different keywords. Dark-mode contrast, animation feel, form UX, or navigation problems: match the symptom to its section in the ux-quick-reference file.

## Scripts Contract

| Script | Purpose |
|--------|---------|
| `scripts/search.py` | CLI: domain search, stack search, `--design-system`, `--persist`, `--style-emphasis`, `--output-dir`, `--force-master` |
| `scripts/core.py` | BM25 engine, CSV and domain config; standard library only |
| `scripts/design_system.py` | Aggregation, reasoning rules, light+dark palette derivation (native mode annotated), success and warning roles, MASTER.md and page-override writer; persisting over an existing MASTER refuses without `--force-master` and routes to UPDATE mode |

Generated MASTER.md always contains: light+dark palette pair per semantic
role, spacing tokens, radius scale derived from the style, shadow scale,
motion tokens (fast/normal/slow plus easing), breakpoints
(375/640/768/1024/1280/1536), one declared icon set, component specs,
anti-patterns, and the pre-delivery checklist. Structure the
implementation as three token layers: the token-architecture reference below.

## Pre-Delivery Checklist

- Contrast: body text 4.5:1 minimum, large text 3:1; verify light AND dark palettes independently
- Focus states visible on every interactive element; tab order matches visual order
- cursor: pointer on all clickable elements; hover and pressed feedback within 150-300ms transitions
- prefers-reduced-motion respected; animate transform and opacity only
- Responsive at 375/640/768/1024/1280/1536; no horizontal scroll on mobile
- Touch targets 44x44px minimum with 8px spacing
- Icons only from the declared set; one stroke width, one fill discipline per hierarchy level
- Semantic tokens everywhere; no raw hex in components
- Self-critique gate passed: assume the goal is NOT met until visual evidence proves it (protocol in the design-rationale-method reference)

## Forbidden Patterns

DO NOT use emoji as icons; use the declared vector icon set.
DO NOT remove focus rings or ship invisible focus states.
DO NOT use instant state changes; transitions run 150-300ms.
DO NOT ship text under 4.5:1 contrast or gray-on-gray hierarchies.
DO NOT hardcode per-screen hex values; theme through semantic tokens.
DO NOT animate width, height, top, or left; transform and opacity only.
DO NOT ignore prefers-reduced-motion.
DO NOT mix icon styles or stroke widths at the same hierarchy level.
DO NOT skip standard breakpoints or allow horizontal scroll on mobile.

## References

- [ux-quick-reference](references/ux-quick-reference.md): ten prioritized rule categories, the named-laws table, information-architecture rules, professional-UI tables. Read when reviewing a surface or matching a UX symptom (contrast, motion, forms, navigation) to its rules.
- [token-architecture](references/token-architecture.md): three-layer token system (primitive, semantic, component) with dark-mode overrides and naming convention. Read when implementing the generated design system in code.
- [design-rationale-method](references/design-rationale-method.md): exemplar-derived principles, rationale-per-decision, divergence protocol, self-critique gate. Read when analyzing product context, generating divergent candidates, or running the self-critique gate.
- [recipes](references/recipes.md): fluid type via clamp(), runnable contrast-ratio checker, dark-palette derivation rules. Read when verifying contrast or implementing fluid type and dark palettes.
