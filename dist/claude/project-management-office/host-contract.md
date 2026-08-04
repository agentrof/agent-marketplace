# Host Contract

- Present every canonical choice gate through `AskUserQuestion`, preserving the options, recommendation, and tradeoffs.
- Preserve every PMO state transition and approval gate.
- For the upgrade entry, present status, plan approval, apply approval, and recovery approval through one `AskUserQuestion` popup at a time. A blocked status lists the ordered clearing actions and stops without mutation. A completed status requires a new Claude Code session.
- Successful completion means the same durable state and artifacts as every supported host; host-specific UI is not part of the contract.
