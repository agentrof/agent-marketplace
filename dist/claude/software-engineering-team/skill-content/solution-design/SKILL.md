---
name: solution-design
description: Solution landscape knowledge for the team's solution roles. Loaded by software-engineering-team flows; not user-facing.
exposure: internal
---

# Solution Design

The project keeps one solution landscape under
`workspace/docs/solution-design/`: components, engagements and decisions.
Every accepted technology, boundary, integration, method and sustainability
verdict is a linked Markdown decision; nothing decided stays only in
conversation. The `obsidian-vault` skill governs every file of the tree.

## When to Use

- A task adds or changes a component, integration, technology or method
  decision.
- A build task's pre-check compares a change with the landscape.

## Rules

An update to these documents is a knowledge task of the `deliver` entry and
runs the fast lane: branch, change, pull request, review, merge.

- `solution-architect` writes. Allocate every active business process to a
  topology component, or record why it needs none.
- A `build` component names its `app_kind` and its `workspace/apps/<component-id>`
  path; external, managed and self-hosted dependencies stay components without
  a source tree.
- Record framing, constraints, options, rejected alternatives and the verdict
  of each decision, with links to the analysis it serves. Component stacks may
  differ; no global stack is assumed.
- `solution-reviewer` reviews read-only and returns findings; the architect
  applies accepted ones to the owning documents.
