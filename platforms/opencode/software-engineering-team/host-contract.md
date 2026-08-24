# Host Contract

- OpenCode support is limited to the project-local terminal adapter. Desktop,
  web, serve, attach, ACP, `--pure`, `--auto`, `--fork`, and experimental code
  mode are outside this contract.
- Resolve package content from `.opencode/agentrof/agent-marketplace/` beneath
  the current project. Never use a global Agent Marketplace package, cache, or
  launcher.
- A canonical entry with `project_scope: external` does not require project
  setup, workspace configuration, or a Git repository.
- Present declared choices in normal conversation text with numbered options,
  recommendation, and tradeoffs. Native OpenCode question UI is optional and
  may not be required for workflow correctness.
- `opencode run` supports only commands marked `choice_free`, with an explicit
  absolute `--dir` target and the generated `software-engineering-team`
  primary agent. When a workflow needs a user decision, stop before mutation
  and continue in the TUI.
- Use the generated `software-engineering-team` primary agent and its
  namespaced subagents. Do not use arbitrary third-party plugins in a supported
  Agent Marketplace workflow.
- The local plugin requires the active package build and exactly one recorded
  runtime binding before protected tool use. `manage.py check` revalidates the
  recorded OpenCode/Python executable identities and OpenCode version. The
  CLI's in-process plugin API does not expose a reliable parent-executable
  identity in `run` mode, so the exact running-host version is additionally a
  real-binary release gate, not an unproven runtime claim. Runtime binding
  also accepts exactly the generated effective OpenCode plugin; a different
  project or global plugin set is unsupported. New or experimental mutating
  tools fail closed.
- Every supported `write`, `edit`, `apply_patch`, and `bash` call invokes
  the hash-pinned canonical package `scripts/vault_hook.py` before execution
  and invokes its matching post phase after success. Session/call identity and
  canonical arguments must match across both phases. Pre-policy failures deny
  the tool; post-policy failures are surfaced immediately, including restoring
  unauthorized Bash changes to `workspace/config.json`.
