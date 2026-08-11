# Host Contract

- Present every canonical choice gate through `AskUserQuestion`, preserving the options, recommendation, and tradeoffs.
- Preserve every PMO state transition and approval gate.
- For the upgrade entry, present the canonical prerequisite, status, plan, apply and recovery gates through one `AskUserQuestion` popup at a time. Preserve the host-neutral prerequisite copy exactly; do not inject the host name. A branch-only blocked status runs the PMO `prepare-branch` command; every other blocked status lists the ordered clearing actions and stops. A completed status requires a new session.
- Successful completion means the same durable state and artifacts as every supported host; host-specific UI is not part of the contract.
