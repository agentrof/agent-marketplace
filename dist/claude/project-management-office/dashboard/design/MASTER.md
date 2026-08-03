# Control Tower - Design System Master

Status: persisted. System: Observatory (top brand bar + wide horizontal tab bar, layered-shadow elevation, airy density, hero KPI). Source: owner-picked candidate 3 of 3 in the DS gate preview (`.obs-*` token prefix).

## 1. Logic header

- Product: Control Tower, a read-only, localhost operations view over the multi-agent team's work orders, project board, quality ledger, and audit trail. No write actions live in this surface.
- Emotional goal: calm authority. The dashboard reads as a periodic check-in surface, not a control room; the owner should be oriented in under a second.
- Interaction density: low and airy, a deliberate counter-density move relative to the data-dense alternatives that were on the table. Whitespace floor starts at 8px, not 4px, trading rows-per-screen for scanability.
- System thesis: one hero KPI number, framed by generous shadow-layered panels, tells the owner the one thing that matters before anything else on the screen competes for attention.

## 2. Global rules - semantic palette

Every role is a CSS custom property on `.obs-root`, themed via `:root[data-theme="light"] .obs-root` / `:root[data-theme="dark"] .obs-root`. With no `data-theme` attribute present (no-JS case), `.obs-root`'s own base values apply and are the dark set, so dark is the brand-native, no-JS default.

| Token | Light | Dark |
|---|---|---|
| `--obs-bg` | `#ffffff` | `#0c0d10` |
| `--obs-bg-subtle` | `#fafafa` | `#101114` |
| `--obs-surface` | `#fcfcfd` | `#16171b` |
| `--obs-surface-raised` | `#ffffff` | `#1c1e23` |
| `--obs-border` | `#e6e6e9` | `#2a2c32` |
| `--obs-border-strong` | `#d1d1d6` | `#3c3f47` |
| `--obs-fg` | `#131316` | `#f7f7f8` |
| `--obs-fg-muted` | `#55565f` | `#a3a4ad` |
| `--obs-fg-subtle` | `#6f7079` | `#84858e` |
| `--obs-accent-solid` | `#1237e8` | `#3de3ff` |
| `--obs-accent-on` (text/icon on accent-solid) | `#ffffff` | `#04050a` |
| `--obs-accent-text` | `#1237e8` | `#1cb8ff` |
| `--obs-accent-surface` | `#eceffd` | `#020f14` |
| `--obs-accent-border` | `#c2cdfa` | `#0f3a44` |
| `--obs-good` | `#067647` | `#34d399` |
| `--obs-warning` | `#8a5a00` | `#fbbf24` |
| `--obs-danger` | `#b42318` | `#f87171` |

Shadow tokens (see section 3 for the dark ring-fold rule):

| Token | Light | Dark |
|---|---|---|
| `--obs-shadow-sm` | `0 1px 2px rgba(19,19,22,.05), 0 1px 1px rgba(19,19,22,.04)` | `0 1px 2px rgba(0,0,0,.4), 0 0 0 1px var(--obs-border)` |
| `--obs-shadow-md` | `0 4px 10px rgba(19,19,22,.06), 0 8px 24px rgba(19,19,22,.06)` | `0 8px 20px rgba(0,0,0,.45), 0 0 0 1px var(--obs-border)` |
| `--obs-shadow-lg` | `0 8px 24px rgba(19,19,22,.08), 0 20px 48px rgba(19,19,22,.10)` | `0 20px 48px rgba(0,0,0,.55), 0 0 0 1px var(--obs-border-strong)` |

Pulse accent ramp (5 stops, theme-invariant): `--obs-ramp-1: #3DE3FF`, stop 2 `#1CB8FF`, stop 3 `#1578FF`, stop 4 `#2453FF`, `--obs-ramp-5: #1237E8`.

Where the ramp may appear, as a hard rule: never as a solid fill on functional UI (buttons, links, chips, status pills all resolve to one solid accent stop per theme). The only two places the full 5-stop ramp is allowed are:
- a soft 14%-opacity blurred decorative disc behind the hero KPI number (`.obs-hero::before`), never carrying text.
- the brand tile background, and there only under the amended deep-stop constraint in section 4, not the full 5-stop run.

Every functional color traces to one of the tokens above; the ramp never has to clear an AA obligation because it never sits under required-legible text or icons.

### Computed AA contrast (corrected values, both themes)

| Pair | Light | Dark |
|---|---|---|
| fg on bg | 18.5:1 | 18.2:1 |
| fg-muted on bg | 7.3:1 | 7.8:1 |
| fg-subtle on bg-subtle (light) / on bg (dark) | 4.7:1 | 5.3:1 |
| accent-text on bg | 7.67:1 | 8.65:1 |
| accent-text on surface | 7.48:1 | 7.97:1 |
| accent-text on accent-surface (badge) | 6.70:1 | 8.65:1 |
| on-accent text on accent-solid (button) | 7.67:1 | 13.22:1 |
| good on surface (light) / on bg (dark) | 5.55:1 | 10.04:1 |
| warning on bg | 5.93:1 | 11.72:1 |
| danger on surface (light) / on bg (dark) | 6.41:1 | 7.07:1 |

`--obs-fg-subtle` is `#6f7079` in light (this is the corrected post-fix value, 4.7:1 on bg-subtle) and `#84858e` in dark (5.3:1 on bg).

## 3. Typography, spacing, radius, elevation, motion, breakpoints, icon set

### Typography

Single typeface: Inter, weights 400/500/600/700 only, no mono substitution. Numeric columns (work-order ids, timestamps, USD costs, the hero number) use `font-variant-numeric: tabular-nums` on Inter rather than swapping family.

| Role | Size / Leading / Tracking / Weight |
|---|---|
| Display (hero KPI) | 56px / 1.05 / -1px / 700 |
| H1 | 32px / 1.1 / -0.6px / 700 |
| H2 | 24px / 1.15 / -0.3px / 600 |
| H3 | 18px / 1.25 / 0 / 600 |
| Body large | 17px / 1.6 / 0 / 500 |
| Body | 15px / 1.6 / 0 / 400 |
| Small | 13px / 1.5 / 0 / 400 |
| Caption | 11px / 1.4 / +1.6px uppercase / 500 |

### Spacing scale

Base unit 8px, deliberately not 4px: `--obs-space-1: 8px`, `--obs-space-2: 12px`, `--obs-space-3: 16px`, `--obs-space-4: 24px`, `--obs-space-5: 32px`, `--obs-space-6: 48px`, `--obs-space-7: 72px`. Raising the floor from 4px to 8px is the explicit counter-density move: nothing in the system can accidentally look cramped, at the cost of fewer rows fitting above the fold. Correct for a periodic check-in reading pattern, not a continuous-monitor one.

### Radius scale

`--obs-radius-sm: 10px` (chips), `--obs-radius-md: 14px` (cards, buttons), `--obs-radius-lg: 20px` (panels, hero), `--obs-radius-xl: 28px` (modals).

### Shadow / elevation tiers

Classic four-tier layered elevation (`--obs-shadow-sm` / `-md` / `-lg`), used in both themes. Dark-theme ring-fold rule: every dark shadow token folds a 1px border into the shadow definition (`0 0 0 1px var(--obs-border)` or `var(--obs-border-strong)` for the lg tier) so the layer still reads against near-black, since shadow alone is too faint on true dark backgrounds. This is a stated, deliberate divergence from a border-only-in-dark default: Observatory's thesis is generous, legible layering, so shadows are kept everywhere rather than dropped in dark mode. Never drop the ring-fold border in dark; that fallback is load-bearing, not decorative.

### Motion tokens

`--obs-dur-fast: 150ms`, `--obs-dur-base: 220ms`, `--obs-dur-slow: 300ms`, `--obs-ease: cubic-bezier(.3,.7,.4,1)` (calm ease-in-out, no spring). Card hover lifts 2px with a shadow-tier increase; the running-status icon spins. Nothing else animates. Reduced-motion clamp (global, hard rule, applies to every token above): `@media (prefers-reduced-motion: reduce){ *,*::before,*::after{ animation-duration:.001ms !important; animation-iteration-count:1 !important; transition-duration:.001ms !important; scroll-behavior:auto !important } }`. Under this clamp the card hover lift and the running-status spin both collapse to their end state with no visible animation.

### Breakpoints

375 / 640 / 768 / 1024 / 1280 / 1536px, the design-system standard ladder. Below 768px the tab bar becomes horizontally scrollable; it never wraps to two lines and the top brand row never collapses into a hamburger (there are only six top-level views). No page-level horizontal scroll is permitted at any width.

### Icon set

One icon set only: Fluent UI System Icons. Regular weight is the default everywhere; Filled is reserved for the active tab and for completed/verified states. 16px throughout chrome and list rows, 20-24px in the hero mini-KPIs. One stroke weight, never mixed with another icon family.

## 4. Lockup rule (hard rule, owner amendment folded in)

The Agentrof mark renders WHITE in both themes, always, with no per-theme variant. It sits on a fixed 36px gradient tile (`--obs-radius-sm` corner radius) that does not change between light and dark.

Owner amendment, binding: the tile gradient uses the DEEP ramp stops only, not the candidate's original full 5-stop run. Amended token:

```
--obs-chrome-tile: linear-gradient(135deg, #1578FF, #1237E8);
```

This replaces the candidate's original `--obs-chrome-tile` definition, which ran the full ramp from `--obs-ramp-1` (`#3DE3FF`) through `#1CB8FF`, `#1578FF`, `#2453FF` to `--obs-ramp-5` (`#1237E8`). The bright stops `#3DE3FF` and `#1CB8FF` never sit under the white mark, at any point on the tile.

Reason, stated as a hard rule with ratios: white on `#1578FF` is about 4.2:1, white on `#1237E8` is about 7.7:1, so the white mark clears 3:1 (the icon-level AA floor) at every point along the deep-stop gradient. White against the bright cyan stops does not clear that floor with the same margin, which is why they are excluded from the tile entirely. The full 5-stop ramp remains valid everywhere else it is already specified as decorative (the hero-KPI wash disc), where it never carries text or an icon and therefore has no contrast obligation.

## 5. Component specs

| Component | Spec |
|---|---|
| App shell | Top brand row (gradient-tile logo + product name/subtitle + search + icon actions) over a wide horizontal tab bar; active tab = accent-solid underline + fg text + filled icon; inactive tabs are fg-subtle, medium weight. No sidebar. |
| KPI tile | One hero pattern: a 56px tabular-nums number with the ramp-wash disc behind it, plus a 2x2 grid of smaller supporting mini-KPI cards (own shadow-sm, uppercase label with leading icon, 22px tabular-nums value). Not four equal tiles. |
| Work-order row | Id (tabular-nums, fg-subtle) + truncating title + status pill (`running` / `waiting_gate` / `blocked` / `escalated` / `complete`, each tinted from its status token at 14% on surface), then a fully labeled horizontal 6-step ladder: Plan, Build, Review, QA, Gate, Done. Each step is a dot (26px, done = filled accent-solid, now = accent-bordered ring with `box-shadow: 0 0 0 3px var(--obs-accent-surface)`, upcoming = outline only) plus its label, joined by 36px connecting segments that fill accent-solid once done. `waiting_gate` state: status pill and step ring share the warning-family accent-surface tint at the current step, meta row states "Human gate pending." `dangling` state: a separate danger-bordered pill (`--obs-dangling`) reading "dangling," shown alongside the status pill, not instead of it, plus a meta line naming the elapsed no-attempt time and "flagged for owner attention." |
| Board card | Raised card (`--obs-surface-raised`, `--obs-shadow-sm`, 16-24px padding), lift + shadow-md on hover (`translateY(-2px)`). Anatomy: 13.5px medium-weight title, priority chip, optional one-line reason text, optional dependency line with a link icon in accent-text, optional DoD checklist block. |
| Priority chip | Pill, tinted background at 14% of its status color on surface-raised, AA-passing status-token text, uppercase, 10.5px, 700 weight. `critical` = danger, `high` = warning, `medium` = accent, `low` = fg-subtle on bg-subtle. |
| DoD checklist item | Icon-led row, icon carries the state so color is never the only signal: `verified` = filled checkmark-circle in good; `pending` = outline checkmark-circle in fg-subtle; `failed` = dismiss-circle in danger, with the row's own text also recolored to danger. |
| Findings row | Severity pill (e.g. `high` tinted warning) + title + source, generous 14px row height. Fixed/waived rows fade to 70% opacity and their pill re-tints to good. |
| Attempt row | Bot icon + role + attempt index + right-aligned tabular-nums USD cost (`--obs-cost`, 600 weight, fg color), 14px row height. |
| Audit feed | Flat list inside a shadow-sm panel, one leading icon per event type, generous line-height, relative timestamp right-aligned in fg-subtle tabular-nums, newest event first. Append-only, no edit affordance anywhere in this read-only surface. |
| Table | Header row in fg-subtle, uppercase; wrapped in a shadow-sm panel rather than a hard border; numeric columns tabular-nums; roomier row padding than a dense-grid system would use. |
| Empty state | Icon + one full sentence (not a fragment) + one solid-accent-fill CTA button, centered, generous padding. |
| Loading state | Same shadow-sm shell as the loaded panel, skeleton blocks at shadow-sm, no shimmer; the airy language does not need motion to read as alive. |
| Error state | Same shadow-sm panel shell as empty/loading, danger-token icon and message, one retry CTA where a retry is meaningful. |
| Theme toggle | Button toggles `data-theme` between `light` and `dark` on the root element via JS. With JS disabled, no `data-theme` attribute is ever set, so `.obs-root`'s own base tokens apply, and those base tokens are the dark set: dark is the no-JS default, not an accident of media-query fallback. When JS is present, initial theme still respects `prefers-color-scheme`, and the toggle is fully keyboard operable with a visible focus ring. |

## 6. Anti-patterns and pre-delivery checklist

Anti-patterns:

- Never use the ramp gradient on functional UI (buttons, links, chips): it is a decorative wash confined to the hero disc and the logo tile, and on the logo tile only the amended deep-stop run from section 4.
- Never compress the hero KPI's padding to fit more tiles: a screen that needs more than one hero metric needs a second view, not a cramped hero.
- Never drop the dark-mode shadow ring-fold border: shadow alone is too faint on near-black without the 1px border folded in.
- Never let the tab bar wrap to two lines: it scrolls horizontally instead.
- Never let the bright ramp stops (`#3DE3FF`, `#1CB8FF`) sit under the white brand mark: the deep-stop constraint in section 4 is a hard rule, not a style preference.

Pre-delivery checklist:

- Body text at or above 4.5:1, verified independently in light and dark (contrast table in section 2).
- Focus visible on every interactive element, no exceptions.
- Transitions 150-300ms, transform/opacity/box-shadow only; reduced-motion removes the card lift and the status spin (section 3).
- Responsive at all six standard breakpoints; tab bar scrolls horizontally on narrow widths; no page-level horizontal scroll.
- One icon set (Fluent), Filled reserved for the active tab and completed/verified states only.
- Every color traces to an `--obs-*` token; the hero wash disc and the amended logo tile gradient are the only intentional non-solid color uses in the whole system.

## 7. Inherited brand values

Five values across the design-system corpus carry an explicit "Inherited" mark from the brand. Status for this system, Observatory:

1. Inter weight ladder (400/500/600/700) and tabular-nums for numeric columns, inherited from the brand's type discipline. Applies directly: Observatory's typography scale (section 3) uses the same four-weight ladder and the same tabular-nums rule for ids, timestamps, and costs.
2. The 4/8 spacing rhythm as the brand's base unit. Observatory explicitly diverges: its floor is 8px, not 4px, argued as the correct trade for a periodic check-in surface rather than a continuous-monitor one. The value is not inherited as stated; only the "8" survives, as the higher rung of that same rhythm.
3. The brand's dark-mode rule that shadows are near-invisible on true black and should be replaced by border-only depth. Observatory explicitly diverges: it keeps the four-tier shadow scale in dark mode too, folding a 1px border ring into each shadow token rather than dropping shadow for borders (section 3, shadow ring-fold rule). Stated as a deliberate counter to the inherited default, not an application of it.
4. The brand's focus-ring formula (1px solid ring plus a soft color-mix glow). Not carried by Observatory: its focus-visible treatment is a plain 2px solid outline in accent-solid with a 2px offset, no glow layer. This inherited value belongs to a sibling system in the corpus, not to Observatory's own component CSS.
5. Light theme as true paper white (`#ffffff`, not a soft gray) and the dark-derivation instinct "swap lightness, never invert, near-black never pure black." Applies directly and is the one inherited mark that sits inside Observatory's own spec block (typography section): `--obs-bg` is `#ffffff` in light, and dark tokens are lightness-shifted derivations of the same palette, never inverted, with `--obs-bg` at `#0c0d10` rather than pure black.

## Section list

1. Logic header
2. Global rules - semantic palette (including computed AA contrast, corrected values)
3. Typography, spacing, radius, elevation, motion, breakpoints, icon set
4. Lockup rule (hard rule, owner amendment folded in)
5. Component specs
6. Anti-patterns and pre-delivery checklist
7. Inherited brand values
