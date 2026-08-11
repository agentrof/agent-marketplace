# Agent Marketplace Project

Read `{{workspace}}/memory/agent-marketplace.md`,
`{{workspace}}/memory/me.md`, and `{{workspace}}/memory/profile.md` before
managed team work.

## Project contract

- One delivery team owns this project. Stop before mutation if the configured
  team and the active team differ.
- Start managed work through the team's entry skills. Do not make free-form
  changes to machine-owned project state.
- Treat `{{workspace}}/config.json`, generated views, and files carrying an
  Agent Marketplace generated header as machine-managed.
- Project configuration changes go through the configure entry. Marketplace
  component and managed-surface changes go through Agent Marketplace Upgrade.
- Durable delivery state lives in PMO. Repository files remain the reviewable
  source for code and authored project knowledge.

## Language and choices

- `output_language` governs Markdown body prose. `terminology_language`
  governs names, technical terms, code and comments, commits, and pull request
  bodies. Machine-layer paths, keys, identifiers, and command output stay
  English ASCII.
- Present owner decisions through the host's native choice gate. Preserve the
  declared options, recommendation, and tradeoffs.
- Declare variation points in configuration or schema. Do not hard-code
  project-specific enums, thresholds, formats, taxonomies, or policy values.
