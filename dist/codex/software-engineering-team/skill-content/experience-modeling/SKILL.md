---
name: experience-modeling
description: Internal knowledge for program and release Experience Design trees, stable journey/flow/screen/state/transition identities, BA space/domain projection, inheritance, coverage, artifacts and deterministic gates. Load for experience authoring, review and backlog UX references.
exposure: internal
---

# Experience Modeling

Treat experience as a versioned graph projected from approved analysis scopes.

## When to Use

- Loaded by experience authors and reviewers.
- Loaded by backlog planning when stories cite exact experience revisions.

## Core Rules

- Use `programs/<program>/releases/<release>/`; do not use `sketches/` for approved experience.
- Place a record at the lowest common ancestor of its `analysis_scopes`: one domain under that domain, multiple domains in one space under the space, multiple spaces under the release.
- Model one user goal per journey, bounded variations per flow-set and one screen with its states and transitions per screen note.
- Use `JRN`, `FLW`, `SCR`, `STA` and `TRN` stable IDs. Preserve IDs across releases and increment revision when behavior changes. Cite exact records as `PRG-001:SCR-001@r2`.
- Inherit the preceding effective registry. Author only the release delta. Retire records instead of deleting them and never reference a future release revision.
- Qualify BA criteria as `<space>:<criterion-id>` and retain the approved analysis revision and hash in scope projection notes.
- Generate files only through `experience_compile.py`; never edit `_generated/`.
- Keep work candidates in `workspace/experience-design-work/`. Promote only approved, network-free HTML packages with declared IDs and a registry hash.
- Enforce schema and limits from `data/experience-schema.json`. Overrides require the configure gate.
- Mechanical findings cannot be waived. Semantic findings need fix or reasoned rejection within three review rounds.
