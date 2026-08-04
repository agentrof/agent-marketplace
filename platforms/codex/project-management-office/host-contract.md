# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode, stop before mutation and ask the user to switch modes.
- Treat `AGENT_MARKETPLACE_HOOKS_ACTIVE` in session context as the hook trust sentinel. If it is absent, stop and ask the user to review and trust this plugin through `/hooks`, then start a new task.
- When the canonical workflow reaches a choice gate, end the current turn with one concise question and the same options. Resume from PMO state after the user's next message.
- Use the Codex-native skill selector (`$` or `/skills`) and preserve every PMO state transition and approval gate.
- For the upgrade entry, end the turn with one concise option-preserving question for status, plan approval, apply approval, or recovery approval. A blocked status lists the ordered clearing actions and stops without mutation. A completed status requires a new task/session.
- Successful completion means the same durable state and artifacts as every supported host; host-specific UI is not part of the contract.
