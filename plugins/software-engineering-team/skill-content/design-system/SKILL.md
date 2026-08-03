---
name: design-system
description: Creates and updates the project's design system, the single source of visual truth. Interactive; candidates are generated from curated design data, the user picks, MASTER.md is written. Touches only the design-system folder.
disable-model-invocation: true
---

# Design System

The only place the design master is born or changed.

## When to Use
- No design system exists yet and design work is about to start.
- The existing system needs a change: palette, type, tokens, a page
  override.

## Procedure

1. Pre-flight and state detection:
   - Read workspace/config.json when present: MASTER body prose follows
     output_language; token, component and spec names follow
     terminology_language (default English).
   - Load the obsidian-vault knowledge skill: MASTER and every page
     override are vault notes (frontmatter, tags mirror, nav section);
     their titles carry the type designation (Title Law), emitted
     born-compliant by the persist script.
   - MASTER exists at workspace/docs/design-system/MASTER.md: UPDATE mode.
   - No MASTER but the codebase carries themes or tokens: EXTRACTION mode;
     derive MASTER from what the code already declares, never invent over
     it.
   - Neither: CREATION mode.
2. Gather inputs interactively: industry and product type, brand
   material the user owns, taste preferences and reference likes, target
   audience; preference questions go through the AskUserQuestion popup,
   open taste input stays free-form. The user's own files win over
   generated suggestions.
3. CREATION mode: spawn ux-designer (spawn template from
   the flow printed by "$RUN" path "$TEAM" flows/design.md, the
   dispatcher per its preconditions) to generate the requested
   number of CANDIDATE systems (default three) using the bound design
   knowledge skill: each candidate is a coherent system dressed onto a
   sample screen, produced with a deliberately different search emphasis,
   presented in one self-contained preview written to
   workspace/docs/design-system/candidates.html with the axis of
   difference stated per candidate. The preview is a working artifact:
   delete it after MASTER is written.
4. DS GATE: the user picks a candidate through the AskUserQuestion
   popup, one option per candidate with its emphasis in the description
   (or requests different emphases; one re-run). The pick is written as
   workspace/docs/design-system/MASTER.md: vault frontmatter and nav
   section (the persist script emits them), logic header, global rules
   (semantic palette with light and dark pairs, typography, spacing,
   radius derived from style, shadows, motion tokens, breakpoints, one
   declared icon set), component specs, style guidelines, plain-text
   anti-patterns, pre-delivery checklist.
5. UPDATE mode: interpret the requested change, show its impact, apply
   it to MASTER.md or as a page override at
   workspace/docs/design-system/pages/<page>.md (deviations only; no
   deviation, no file). Silent deviation is a violation. Override
   consolidation is part of UPDATE: when the same deviation recurs
   across pages, fold it into MASTER and delete the overrides it
   absorbs; an override that contradicts MASTER is a finding, not a
   preference. Run the consolidation sweep when asked (the develop
   flow's periodic reconciliation requests it).
6. Close: update maps/design-system.md to match (one wikilink per
   override with its deviation summary), ensure home.md links this
   tree's map (dynamic home: the map seed materializes with the tree's
   first content), then run
   "$RUN" run "$TEAM" scripts/vault_check.py check --vault
   workspace/docs --scope design-system; repair every finding before
   closing (obsidian-vault skill; migrate covers the deterministic
   classes). A leftover candidates.html is a finding, never an
   exemption.
7. HARD SCOPE LIMIT: this flow writes only under
   workspace/docs/design-system/, plus home and its own map note
   repair, and vault payload materialization (per-file, only where
   missing). Requests to design product pages or write code are
   refused and routed to sketch or deliver.
