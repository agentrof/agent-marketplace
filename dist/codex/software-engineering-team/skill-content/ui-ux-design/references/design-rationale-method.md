# Design Rationale Method

How to move a design from template to premium: extract principles from proven
exemplars, map them to the product's context, and document a rationale for
every decision. Study exemplars to understand WHY they work, never to copy them.

## Core Aesthetic Principles

Every design must embody all five:

- **Simple** - no visual clutter; every element earns its place on screen
- **Minimal** - restraint in color, shadows, decorative elements
- **Clean** - generous whitespace, clear boundaries, consistent spacing rhythm
- **Understandable** - obvious hierarchy, scannable content, intuitive navigation
- **Aesthetic** - refined details, polished transitions, premium feel

## Exemplar Principles

Vendor names below are citations of studied products; extract the principle,
not the pixels.

| Exemplar | What It Teaches | Why It Works |
|----------|-----------------|--------------|
| Tremor | Data-first hierarchy: muted palettes, barely visible shadows, subtle 1px borders, fast 100ms transitions | Colors never compete with data; whitespace between data sections prevents cognitive overload |
| Stripe | Premium restraint: massive whitespace, one accent color, bold typography, solid-fill CTAs | Extreme restraint creates elegance; hierarchy comes from type weight, not decoration |
| Ant Design | Systematic consistency: 4px spacing unit, semantic colors, 6px default radius, neutral text hierarchy via opacity | Mathematically related values build trust; semantic colors carry meaning without explanation |
| shadcn/ui | Borders over shadows, HSL tokens as custom properties, dark mode via data attributes | Borders scale predictably; HSL enables programmatic theming; refined but never decorative |
| Linear | Keyboard-first UX, cohesive visual language, subtle gradients, polished micro-interactions | Speed is the priority; visual noise slows comprehension; feedback without interruption |
| Vercel | Stark black/white contrast, minimal color, bold typography, clean iconography | Maximum contrast creates confidence; a rare accent color carries real weight |

## Shared Premium Traits

Every exemplar shows all of these; a design that misses one reads as a template:

- **Premium feel** - looks like a funded production product, not a prototype
- **Generous whitespace** - content breathes; sections have real vertical rhythm
- **Content legibility first** - typography does the heavy lifting; hierarchy is instant
- **Subtle depth** - shadows barely visible, borders thin and light; depth suggested, not shouted
- **Restraint in color** - one accent dominates; semantic colors appear only where they carry meaning
- **Consistent spacing rhythm** - a base unit system harmonizes every component
- **Smooth micro-interactions** - hovers shift backgrounds subtly; transitions 100-200ms; nothing jumps
- **Cards as primary containers** - data grouped in bordered, padded, rounded cards
- **Professional tone** - trustworthy for daily business use

## Avoid List

- Heavy gradients, glossy effects, decorative flourishes
- Bright saturated colors spread across the interface; color must be meaningful
- Thick borders or heavy drop shadows
- Cramped layouts; if it feels tight, add whitespace
- Generic default-framework template look
- Playful tone where the product needs professional trust
- Visual clutter; every element must earn its place

## Rationale Per Decision

First analyze the product: purpose (core job-to-be-done), user context
(professional or casual, desktop or mobile, daily or occasional), dominant
content (tables, charts, media, text, forms), emotional goal (trust, energy,
calm, focus), interaction pattern (scanning, keyboard, input, navigation,
search). Then decide each dimension and write down WHY:

| Dimension | Decide | Document |
|-----------|--------|----------|
| Color strategy | Emotional response, restraint level, semantic meanings | Which exemplar principle applies and why it fits this product |
| Typography | Data-dense vs content-rich, hierarchy strength, tabular numerics | How type serves the dominant content |
| Spacing | Generous breathing room vs compact efficiency, vertical rhythm | Density matched to the use case |
| Shadow and depth | Border-led vs shadow-led, layering strength | Why depth is suggested at this level |
| Component style | Rounded/friendly vs sharp/precise, animation character | How states and radius express the product's tone |
| Icon set | One set matching typography weight and component style | Why this weight and style |

Example rationale statement: "This financial advisory platform serves
professional users scanning complex price data. Trust and data clarity are
paramount, so it follows a data-first hierarchy (subtle borders, muted colors,
generous data spacing) and premium restraint (one accent color, bold type
hierarchy). The palette emphasizes trust (blue-gray family) with semantic
accents for price movements; typography uses tabular numerics for data
columns; shadows stay minimal to keep focus on content."

## Divergent Candidates First

Never open with a single converged proposal. Produce candidates whose axis of
difference is structural, not cosmetic:

- restraint vs expressive color
- border-led vs shadow-led depth
- data-first vs marketing-first layout

Seed each candidate from a different style emphasis (the product row's
secondary styles and the reasoning rule's decision branches are ready-made
seeds), present the axis of difference with the rationale, pick one, refine.

## Self-Critique Gate

Run before delivery. Default assumption: the goal has NOT been achieved until
visual evidence proves otherwise. Judge the render, not the code. "Looks
different" is not "looks correct."

| Check | Question |
|-------|----------|
| Context alignment | Does the palette match the product's emotional goal? |
| Data fit | Does typography support the dominant content type? |
| Spacing logic | Does density match the use case (dashboard structured, marketing generous)? |
| Visual consistency | Same radius, shadow depth, and color usage across all components? |
| Exemplar benchmark | Would this feel at home among the exemplars above? |
| Contrast evidence | Measured 4.5:1 body text in BOTH light and dark palettes? |
| Focus and keyboard | Focus indicators visible; tab order matches visual order? |
| Token compliance | Every color and spacing value traces to a token, no ad-hoc hex? |
| Icon alignment | One declared set, consistent stroke, matching type weight? |
| Rationale complete | A documented WHY for every major decision? |

If any check fails, revise before presenting. State conclusions from observed
evidence ("from the visual evidence, I observe...") and never declare success
without it.
