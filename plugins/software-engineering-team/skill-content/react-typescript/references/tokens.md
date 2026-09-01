# Design-Token Consumption and Theming

How the frontend consumes the project design master. The design master defines the tokens; this file defines how components use them. Hardcoded values are review blockers (see [review-checklist](review-checklist.md)).

## Three Layers

| Layer | Contains | Who reads it |
|---|---|---|
| Primitive | raw values: the full color ramp, spacing scale, font stacks, radii, shadows | only the semantic layer |
| Semantic | purpose-named references: `text-primary`, `surface-elevated`, `border-default`, `interactive-primary` | components, via classes or custom properties |
| Component | per-component slots: `button-bg`, `card-border`, `input-focus-ring` | the one component they name |

Rules:

- Components consume semantic and component tokens only. A component reading a primitive (`gray-900`) couples itself to a value instead of a purpose and breaks under theming.
- Name by purpose, not appearance: `text-primary`, never `dark-gray`. A token named after its value cannot survive a theme change.
- Every raw value in component code is a violation: hex/rgb/hsl colors, arbitrary pixel spacing, ad hoc font sizes, arbitrary z-index numbers. If the design master lacks a needed token, the gap is escalated to the design owner, not patched locally.
- Token additions and renames are contract changes: deprecate gradually, never repoint a token to a different purpose.

## CSS Custom Properties

Primitives are declared once at the root; semantic tokens reference primitives; themes remap only the semantic layer.

```css
:root {
  /* primitive layer (defined by the design master) */
  --color-gray-50: #fafafa;
  --color-gray-900: #171717;
  --color-brand-500: #3b82f6;

  /* semantic layer: purpose -> primitive */
  --text-primary: var(--color-gray-900);
  --surface-default: #ffffff;
  --interactive-primary: var(--color-brand-500);
}

:root[data-theme="dark"] {
  /* dark theme remaps semantics; primitives never change */
  --text-primary: var(--color-gray-50);
  --surface-default: var(--color-gray-900);
}
```

Components then style against `var(--text-primary)` (or the utility class generated from it) and are theme-agnostic by construction.

## Color-Scheme Handling

- Default to the system preference via `prefers-color-scheme`; let the user's explicit theme override win by stamping `data-theme` on the root element.
- Persist the explicit choice, and apply it with a synchronous inline script before first paint so the page never flashes the wrong theme (an effect runs too late).
- Set the `color-scheme` property so native controls, scrollbars, and form elements match the active theme.
- Verify contrast in both themes; a token pair that passes in light mode can fail in dark mode.

```html
<script>
  /* runs before paint: explicit choice wins, otherwise system preference */
  var t = localStorage.getItem("theme");
  if (t) document.documentElement.dataset.theme = t;
</script>
```

## Motion and Preference Media

- Respect `prefers-reduced-motion: reduce`: disable decorative animation and replace movement-based transitions with opacity, keeping essential feedback.
- Keep transitions on performant properties (`transform`, `opacity`) and within the duration scale the design master defines; never animate layout properties as a default.
- Honor `prefers-contrast` where the design master defines a high-contrast mapping.

## Failure Modes

- **Token sprawl:** components minting their own custom properties outside the three layers. New tokens go through the design master.
- **Layer skipping:** a semantic token defined as a raw value instead of referencing a primitive, silently forking the palette.
- **Partial theming:** a component that looks right in the default theme because it hardcodes one side. Test every component in every theme combination.
- **Flash of wrong theme:** theme applied after hydration; move resolution to the pre-paint script above.
