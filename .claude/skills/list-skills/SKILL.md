---
name: list-skills
description: Enumerate all skills under .claude/skills/ with id, version, description, tags, and depends_on. Use when the user asks to list, browse, show, or inspect the marketplace skill catalog.
---

# list-skills

## Purpose

Walk `.claude/skills/`, read each `manifest.yaml`, and produce both a human-readable table and a machine-readable JSON listing.

## Inputs

None. Query operation, no parameters.

## Flow

1. **Open run workspace**. Generate UUID4. Create `.run/<uuid>/META.md` (`status: pending`, `component: list-skills`, `kind: skill`) and `.run/<uuid>/artifacts/`. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.
2. **Run script**. Invoke `scripts/list.py --output-dir .run/<uuid>/artifacts/`. The script writes `listing.json` and `listing.md` into that folder.
3. **Render to chat**. Read `.run/<uuid>/artifacts/listing.md` and show it in chat.
4. **Close run workspace**. Update `META.md`: `status: done`, `ended` set, `## Outputs` notes the skill count, `## Artifacts` lists `listing.json` and `listing.md`. Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Rules

- Pure query. Never writes to `.claude/`.
- If a manifest is malformed, include the skill in the listing with `error: <message>` instead of skipping silently.
