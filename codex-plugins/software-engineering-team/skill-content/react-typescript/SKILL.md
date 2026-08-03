---
name: react-typescript
description: React and TypeScript frontend expertise loaded by software-engineering-team agents for client-side work. Use when building components from approved designs, deciding where a piece of state lives, consuming design tokens, enforcing strict typing, meeting performance and accessibility bars, or writing component and hook tests with business-rule traceability.
user-invocable: false
---

# React + TypeScript Frontend

**Given:** approved designs, an API contract, and the project design master (the token source of truth).
**Produces:** a component-driven, strictly typed frontend where every data view handles loading, empty, error, and success, styled entirely through design tokens, with a behavioral test suite.

## When to Use

- Building or extending components, hooks, or pages in a React frontend
- Deciding which category a piece of state belongs to and which tool owns it
- Wiring theming, dark mode, or token consumption from the design master
- Reviewing frontend code (load the review checklist below)
- Planning QA for a frontend deliverable (load the qa checklist below)

## Core Concepts

1. **Component-driven core:** small single-responsibility components composed bottom-up (atoms -> molecules -> organisms -> pages). UI components, business-logic hooks, and the API layer are separate modules; pages orchestrate only. API calls live in one typed client layer, never inline in components.
2. **State separation:** pick the owner by category; never duplicate one value across categories.

   | Category | Holds | Owner |
   |---|---|---|
   | Local | component-scoped UI state | `useState` / `useReducer` |
   | Global client | cross-cutting app state (auth, theme) | one small store, selector-based reads |
   | Server | remote data, caching, refetch | the query/cache library, exclusively |
   | URL | route params, filters, tabs | the router |
   | Form | input values and validation | the form layer, controlled inputs |

   Derive, don't store: anything computable from existing state or props is computed in render.
3. **Design-token consumption:** three layers, primitive -> semantic -> component. Components consume semantic and component tokens only; tokens come from the project design master. Any hardcoded color, spacing, or type value is a violation, not a shortcut. Consumption rules live in the tokens reference.
4. **Type safety:** strict compiler config, no `any`, no unjustified assertions. Every component has a typed props interface, API types match the contract, and view state uses discriminated unions (loading/success/error).

## DO

- Handle loading, empty, error, and success in every data view; derive them from query status, not hand-rolled booleans
- Give list items stable keys from data ids; clean up every effect that subscribes, listens, or starts a timer
- Wrap routes and risky widgets in error boundaries with user-friendly fallbacks
- Split code by route, parallelize independent fetches, preload heavy modules on user intent
- Reach for semantic elements and labeled controls first; add ARIA only where semantics fall short
- Query by role and label in tests; assert behavior the user can observe

## DON'T

- Store derivable values in state, or copy server state into a client store
- Hardcode colors, spacing, or typography; never skip the token layers
- Use array index as key on dynamic lists, or pass a freshly created object as a context value
- Memoize by default; profile first and memoize where it measurably helps
- Remove focus outlines, or attach click handlers to non-interactive elements
- Pin dependency or runtime versions inside this skill's guidance; the project manifest owns versions

## Performance Budget

Hard rules: no sequential awaits for independent data, direct imports over barrel files, dynamic import for heavy or conditional components, virtualize long lists, narrow primitive effect dependencies, functional set-state updates, transitions for non-urgent updates. Severity-tagged detail, with framework-conditional items marked, in the performance reference.

## Accessibility

Keyboard-first interaction, WCAG AA contrast, visible and managed focus, live regions for dynamic updates, content usable at 200% zoom, reduced-motion respected. Implementation checklist in the accessibility reference.

## Testing

Testing-library semantics: prefer role and label queries, build factory fixtures with overrides, test hooks through their rendered behavior, prefix test names with the scenario tag (`[BR-###] description`), and satisfy the behavioral coverage contract instead of a percentage. Full approach in the testing reference.

## Patterns

Severity-ranked do/don't pairs (state, effects, rendering, context, error handling, typing) plus component API design (composition, variants, ref forwarding) live in the patterns reference.

## References

- [patterns](references/patterns.md): severity-ranked do/don't pairs and component API design. Read when implementing or reviewing component code.
- [performance](references/performance.md): severity-tagged performance rules, framework-conditional items marked. Read when writing fetch, list, or code-splitting logic, or chasing a slow page.
- [accessibility](references/accessibility.md): WCAG AA implementation checklist. Read when building interactive components or auditing a frontend.
- [testing](references/testing.md): query priority, factory fixtures, hook testing, scenario tags. Read when writing or reviewing the frontend suite.
- [tokens](references/tokens.md): three-layer token consumption and theming, including first-paint theme resolution. Read when wiring styles, theming, or dark mode.
- [review-checklist](references/review-checklist.md): severity-tagged review assertions, including the token-compliance gate. Read when reviewing frontend code.
- [qa-checklist](references/qa-checklist.md): component, hook, and store test matrices with all-states coverage. Read when planning QA for a frontend deliverable.
- [conditional-capabilities](references/conditional-capabilities.md): implementation discipline for i18n, client telemetry, real-time data, and offline support. Read when the brief names one of these capabilities.

## Troubleshooting

**An effect loops forever or reads stale values.**
The dependency array is wrong. Include every referenced value, or remove the effect entirely: derived data belongs in render, event logic belongs in handlers.

**Everything re-renders when one small thing changes.**
A context provider is passing a freshly created value object, or components read a store without selectors. Memoize the provider value; subscribe to the narrowest slice.

**List rows lose input state or animate incorrectly when reordered.**
Index keys. Switch to stable ids from the data; see the keys row in the patterns reference.

**The page flashes the wrong theme on first paint.**
Theme is resolved in an effect after render. Set the theme class before hydration and let semantic custom properties remap; see the tokens reference.

**Tests cannot find elements without reaching for test ids.**
The markup is not semantic. Render real roles and labels, then query by them; this fixes accessibility and testability together.

**A component throws and the whole app goes white.**
No error boundary above it. Add boundaries at route and widget level with fallback UI.

## Related Skills

Pairs with the python-fastapi skill on the serving backend; schema decisions live in the sql-database-design and nosql-database-design skills; the docker-compose skill packages the built client into the containerized environment.
