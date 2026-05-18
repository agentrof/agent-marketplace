# list-skills

Primitive marketplace skill. Enumerates `.claude/skills/` and outputs both JSON and Markdown listings.

## When to use

User asks to list, browse, show, or inspect the marketplace skill catalog.

## What it does

Walks every subdirectory of `.claude/skills/`, parses each `manifest.yaml`, writes `listing.json` and `listing.md` to the run workspace, and renders the markdown in chat.

## Key files

- `scripts/list.py` - enumerator.
