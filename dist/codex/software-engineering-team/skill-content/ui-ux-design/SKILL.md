---
name: ui-ux-design
description: Professional UI rules for web interfaces. Applies design-token layering, accessibility, motion and responsive rules through checklists and forbidden patterns.
exposure: internal
---

# UI/UX Design

UI rules for design-and-experience work: token layering, accessibility, motion
and responsive behavior, checked before a change ships.

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

1. **Analyze product context.** Purpose, users, dominant content (data/text/media/forms), emotional goal (trust/energy/calm), interaction pattern (scan/keyboard/input/navigate). Method: the design-rationale-method reference below.
2. **Follow the project's design tokens.** Theme through the three token layers in the token-architecture reference; never hardcode per-screen values.
3. **Review against the rules.** Match a UX symptom (contrast, motion, forms, navigation) to its section in the ux-quick-reference file, then run the checklist below.

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
- [token-architecture](references/token-architecture.md): three-layer token system (primitive, semantic, component) with dark-mode overrides and naming convention. Read when implementing design tokens in code.
- [design-rationale-method](references/design-rationale-method.md): exemplar-derived principles, rationale-per-decision, divergence protocol, self-critique gate. Read when analyzing product context or running the self-critique gate.
- [recipes](references/recipes.md): fluid type via clamp(), runnable contrast-ratio checker, dark-palette derivation rules. Read when verifying contrast or implementing fluid type and dark palettes.
