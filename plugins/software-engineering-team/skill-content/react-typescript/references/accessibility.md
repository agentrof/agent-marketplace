# Accessibility Implementation

WCAG AA as the floor. Load when building interactive components or auditing a frontend. Test assertions live in [qa-checklist](qa-checklist.md).

## Semantic Structure First

- Prefer native elements over ARIA retrofits: `<button>` for actions, `<a>` for navigation, `<nav>`, `<main>`, `<header>`, `<section>` for landmarks. A `div` with a click handler recreates, badly, what `<button>` gives for free (focus, keyboard activation, role).
- One `<h1>` per page and a heading hierarchy that never skips levels; screen-reader users navigate by it.
- Add ARIA only where no native semantic exists, and then completely: role, states (`aria-expanded`, `aria-busy`), and relationships (`aria-describedby`, `aria-controls`).
- Every image has `alt` text; decorative images use `alt=""` so they are skipped, not announced as noise.
- Associate every form control with a visible label (`htmlFor` matching the input id, or `aria-labelledby`). Placeholder text is not a label: it vanishes on input and fails contrast.
- Tie validation messages to their field with `aria-describedby` and mark invalid fields with `aria-invalid`.

## Keyboard First

- Every interaction works without a mouse: tab reaches it, Enter/Space activates it, Escape dismisses it, arrow keys move within composite widgets (menus, tabs, listboxes).
- Tab order follows visual order; avoid positive `tabindex`, fix the DOM order instead.
- Provide a skip link to bypass repeated navigation.
- No keyboard traps: focus can always leave a component. The one deliberate trap, a modal, must be complete: focus moves in on open, cycles inside, and returns to the trigger on close.

## Focus Visibility

- Never remove focus outlines; style them. Use `:focus-visible` to show a clear ring for keyboard focus without flashing on mouse clicks.
- Focus indicators meet contrast requirements against the surface they sit on, in every theme.
- After a route change or content swap, move focus to the new content's heading or container so keyboard and screen-reader users are not stranded.

## Live Regions

- Announce dynamic updates that do not move focus: `aria-live="polite"` for status (saved, loaded, N results), `aria-live="assertive"` only for urgent errors.
- Render the live region container up front and change its text content; regions injected at announcement time are unreliable.
- Loading state changes, toast notifications, and async validation results all need an announcement path.

## Zoom and Reflow

- Content works at 200% zoom: no horizontal scrolling for text content, no clipped or overlapping controls.
- Use relative units for type and spacing so user font-size preferences apply.
- Touch targets are comfortably sized and separated; hover-only affordances have focus and touch equivalents.

## Color and Motion

- Text contrast at least 4.5:1 (3:1 for large text); non-text UI indicators at least 3:1. Verify in every theme.
- Never encode meaning in color alone; pair it with text or an icon.
- Respect `prefers-reduced-motion` (see [tokens](tokens.md)); no auto-playing motion or media without user initiation.

## Testing Method

- **Automated:** an axe audit on every component in its default and interactive states; zero violations is the bar. Automated checks catch roughly a third of issues; they are the floor, not the audit.
- **Manual:** a full keyboard pass over every flow, and a screen-reader pass (at least one desktop reader) over critical journeys: forms, dialogs, dynamic updates.
- **Common findings to sweep for:** missing labels, div-buttons, focus traps, silent updates, skipped headings, hover-only menus, removed outlines, index-order tab traps.
