# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode, stop before mutation and ask the user to switch modes.
- Treat `AGENT_MARKETPLACE_HOOKS_ACTIVE` in session context as the hook trust sentinel. If it is absent, stop and ask the user to review and trust this plugin through `/hooks`, then start a new task.
- Present every canonical choice gate through `request_user_input`, preserving
  the options, recommendation and tradeoffs. Use it only at those gate sites.
- Use the Codex-native skill selector (`$` or `/skills`) and preserve every PMO state transition and approval gate.
- For the upgrade entry, present the canonical prerequisite, status, plan,
  apply and recovery gates through one `request_user_input` popup at a time.
  Preserve the host-neutral prerequisite copy exactly; do not inject the host
  name. A branch-only blocked status runs the PMO `prepare-branch` command;
  every other blocked status lists the ordered clearing actions and stops. A
  completed status requires a new task/session.
- Successful completion means the same durable state and artifacts as every supported host; host-specific UI is not part of the contract.
