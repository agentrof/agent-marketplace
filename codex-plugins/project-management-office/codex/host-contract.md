# Codex Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode, stop before mutation and ask the user to switch modes.
- Treat `AGENTROF_HOOKS_ACTIVE` in session context as the hook trust sentinel. If it is absent, stop and ask the user to review and trust this plugin through `/hooks`, then start a new task.
- When the canonical workflow asks through `AskUserQuestion`, end the current turn with one concise question and the same options. Resume from PMO state after the user's next message.
- Use the Codex-native skill selector (`$` or `/skills`) and preserve every PMO state transition and approval gate.
- Successful completion means the same durable state and artifacts as Claude Code; host-specific UI is not part of the contract.
