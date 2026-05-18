# list-agents

Primitive marketplace skill. Enumerates `.claude/agents/` and outputs both JSON and Markdown listings.

## When to use

User asks to list, browse, show, or inspect the marketplace agent catalog.

## What it does

Walks every `.md` file under `.claude/agents/`, parses each file's YAML frontmatter, writes `listing.json` and `listing.md` to the run workspace, and renders the markdown in chat.

## Key files

- `scripts/list.py` - enumerator.
