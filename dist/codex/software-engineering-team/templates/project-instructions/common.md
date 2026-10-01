# Agent Marketplace project contract

- The project root is the Git checkout and `workspace/` is the only project
  workspace for this team. A second managed vault is invalid.
- Durable truth is kept in tracked project files. Generated indexes are
  reproducible and disposable; no shared database is required.
- Before mutation, inspect `workspace/config.json`, the project-local
  `.agentrof/agent-marketplace/.runtime/` scratch directory if present, and
  `workspace/docs/`. Keep durable state inside the selected Git checkout.
- User decisions are explicit. A workflow pauses at its named gate instead of
  silently changing an approved document.
- Authored Markdown body prose follows `output_language`; project terminology
  follows `terminology_language`. Machine keys, ids, paths and CLI output stay
  English. User-facing conversation may use the user's language.
- Agent Marketplace is public. An issue, comment, commit message or pull
  request sent to it, the files a pull request adds included, never
  identifies this project or its data: no project or code name, repository,
  link or issue reference, commit id, local or home path, story, Delivery,
  scenario or requirement id, measured data presented as this project's,
  domain, client or person. Retell evidence as an anonymous case, such as
  "in one measured project".
