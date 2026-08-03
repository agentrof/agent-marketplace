# Host Contract

- Present every canonical choice gate through `AskUserQuestion`, preserving the options, recommendation, and tradeoffs.
- When the canonical workflow says `spawn`, use the `software-engineering-team:<agent-id>` identity. Open the PMO task before spawning, wait for every required agent, then close the task after validating its output.
- Dispatch independent read-only agents together and wait for all of them. Do not dispatch overlapping writers concurrently.
- During setup, materialize `templates/CLAUDE.md` as the project `CLAUDE.md`, substituting the chosen workspace name, and preserve existing user content.
- For delivery-lane handoffs, open a new Claude Code session in the selected worktree.
- Successful completion means the same durable state, gates, artifacts, and PR outcome as every supported host; host-specific UI is not part of the contract.
