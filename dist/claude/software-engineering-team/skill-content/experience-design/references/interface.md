# Interface design and proof

## Product direction

Inspect the existing product, content and design system. Choose hierarchy,
density, typography, color and interaction from the user's job and the actual
data. For an unsettled design direction, show structurally different candidates
and the reason each fits. A palette swap alone is weak divergence. Use examples
to understand principles without copying a brand or forcing every product into
the same card layout.

## States and recovery

For each affected action and data view, consider loading, empty, success,
partial failure, denied access, stale data, pending work and cancellation.
Preserve entered data on recoverable errors. Explain errors near the affected
control and provide an actionable next step. Show whether a mutation is saved
or still pending. Handle duplicate actions and stale/conflicting writes with
the API's actual semantics.

## Accessibility and layout

Use semantic controls and meaningful accessible names. Verify keyboard reach,
logical focus order, visible focus, dialogs and focus restoration. Associate
labels and error text with fields. Announce important asynchronous changes
without overwhelming assistive technology. Avoid color-only distinctions and
respect reduced-motion preferences.

Use the project's adopted accessibility standard and verify contrast and target
size against it. Inspect each supported theme independently. Test layouts at
the actual breakpoints, zoom and content extremes that can break them, including
long translations, dense data and narrow viewports. Avoid forced breakpoint or
motion values unrelated to the product's requirements.

## Design system

Reuse semantic tokens for meaning and shared component styles for consistency.
Where themes or component overrides need them, organize primitive, semantic
and component tokens so a change has one owner. Keep one icon language at a
given hierarchy level. Add a token only when it represents a reusable design
decision. Do not create a new token subsystem for a single local fix.

## Observed evidence

Render the changed surface with representative content, inspect screenshots,
then exercise its meaningful interactions. Check focus, keyboard, error and
pending behavior directly. Compare the result with the requested outcome and
accepted direction. Record material limitations. Update the owning design
record when behavior or a reusable rule changes.
