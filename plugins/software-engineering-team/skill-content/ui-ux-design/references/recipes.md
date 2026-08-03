# Recipes

Runnable snippets the generator's output does not ship: fluid type, a contrast
checker, and the dark-palette derivation rules used by the scripts.

## Fluid Typography via clamp()

Scale type with the viewport between fixed bounds; no breakpoint jumps.

```css
h1 {
  font-size: clamp(2rem, 5vw + 1rem, 3.5rem);
  line-height: 1.1;
}

p {
  font-size: clamp(1rem, 2vw + 0.5rem, 1.125rem);
  line-height: 1.6;
  max-width: 65ch; /* optimal reading width */
}
```

Pattern: `clamp(min, preferred, max)` where preferred mixes a viewport unit
with a rem base so zoom still works. Keep body text at 16px minimum on mobile.

## Contrast Ratio Checker

WCAG relative-luminance formula. Body text needs 4.5:1 (AA), large text and
UI components 3:1, enhanced 7:1 (AAA). Verify light and dark palettes
independently.

```js
function getContrastRatio(foreground, background) {
  const getLuminance = (hex) => {
    const h = hex.replace("#", "");
    const rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [r, g, b] = rgb.map((c) => {
      c = c / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const l1 = getLuminance(foreground);
  const l2 = getLuminance(background);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
// getContrastRatio("#1E293B", "#F8FAFC") -> about 15.4 (passes AAA)
```

## Dark Palette Derivation Rules

The generator derives the dark value for each semantic role deterministically
(implemented in `scripts/design_system.py`, `derive_dark_palette`). Apply the
same rules when adjusting palettes by hand:

| Role group | Rule | Clamp |
|------------|------|-------|
| background | Swap lightness (1 - L); near-black, never pure black | L 0.06-0.12 |
| muted | Swap lightness; a step above background | L 0.12-0.20 |
| border | Swap lightness; must stay visible on dark surfaces | L 0.18-0.30 |
| foreground, on-primary | Swap lightness; avoid pure-white glare | L 0.85-0.96 |
| primary, secondary, accent, ring | Keep hue, desaturate about 15%, lift lightness so the accent reads without vibrating | L 0.55-0.72 |
| destructive | Keep the red hue, desaturate about 15%, lift | L 0.55-0.68 |

Principles behind the rules:

- Dark mode uses desaturated, lighter tonal variants, never inverted colors.
- Saturated light-mode accents vibrate against dark backgrounds; desaturation
  restores calm while keeping identity.
- Contrast must be re-verified per theme; light-mode ratios do not carry over.
- Wire the derived values through semantic tokens so the theme switches with
  one override block:

```css
[data-theme="dark"] {
  --color-background: #10192D;
  --color-foreground: #C9D9E8;
  --color-border: #394960;
  /* ...one line per semantic role */
}
```
