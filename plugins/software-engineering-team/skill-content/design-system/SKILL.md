---
name: design-system
description: Creates and revises the project's directly-authored design system, the single source of visual truth. Interactive; MASTER.md starts as a draft, changes through conversation, and is compiler-approved in place.
exposure: entry
---

# Design System

The only place the design master is born or changed.

## When to Use
- No design system exists yet and design work is about to start.
- The existing system needs a change: palette, type, tokens, a page
  override.

## Procedure

1. Read `flows/design-system.md` completely and resolve mode. Requirement
   mode receives exact BA and Solution receipts from the router. Manual mode
   requires selected strict-current BA and Solution packages; it never
   infers a Requirement from open work.
2. Pre-flight and state detection:
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
   - New or revised MASTER records `contract_version: 2`, exact approved BA
     package `derives_from` links and one exact Solution package
     `constrained_by` link. A page override always
     carries `uses_design: [[design-system/MASTER|Design System]]` and may
     relate to the exact Experience screen or journey it specializes.
3. Gather inputs interactively: industry and product type, brand
   material the user owns, taste preferences and reference likes, target
   audience; preference questions go through a choice gate,
   open taste input stays free-form. The user's own files win over
   generated suggestions.
4. `ux-designer` is the sole writer. CREATION mode: resolve any material style fork through a choice gate,
   then use the bound design knowledge skill's persist command to write one
   `workspace/docs/design-system/MASTER.md` draft directly. The persist
   script emits vault frontmatter, `status: draft` and `revision: 1`; the
   draft itself is the review candidate. The draft contains the
   logic header, global rules
   (semantic palette with light and dark pairs, typography, spacing,
   radius derived from style, shadows, motion tokens, breakpoints, one
   declared icon set), component specs, style guidelines, plain-text
   anti-patterns, pre-delivery checklist.
5. Refine the draft conversationally: interpret each requested change, show
   its impact and apply it to MASTER.md or as a page override at
   `workspace/docs/design-system/pages/<page>.md` (deviations only; no
   deviation, no file).
6. UPDATE mode: for an approved baseline first run the packaged
   `design_system_compile.py begin-revision --root
   workspace/docs/design-system`; never overwrite an approved
   revision. Then apply the requested change to MASTER.md or as a page
   override at
   workspace/docs/design-system/pages/<page>.md (deviations only; no
   deviation, no file). Silent deviation is a violation. Override
   consolidation is part of UPDATE: when the same deviation recurs
   across pages, fold it into MASTER and delete the overrides it
   absorbs; an override that contradicts MASTER is a finding, not a
   preference. Run the consolidation sweep when asked (the develop
   flow's periodic reconciliation requests it).
7. Spawn `design-system-reviewer` read-only with MASTER, overrides and exact
   upstream bindings; resolve all blocking findings.
8. Close: update maps/design-system.md to match (one wikilink per
   override with its deviation summary), ensure home.md links this
   tree's map, then run the packaged `vault_check.py check --vault
   workspace/docs --scope design-system`; run `vault_check.py
   render-relations --vault workspace/docs`; repair every finding. At the
   project decision gate run the packaged `design_system_compile.py approve --root
   workspace/docs/design-system`, then rerun its `check` verb.
9. In Requirement mode bind the compiler receipt; manual mode reports it and
   suggests `/experience-design`. HARD SCOPE LIMIT: this flow writes only under
   workspace/docs/design-system/, plus home and its own map note repair, and
   vault payload materialization (per-file, only where missing). Requests to
   design product pages or write code are
   refused and routed to sketch or deliver.
