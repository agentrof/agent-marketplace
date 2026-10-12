# Transport recovery

The user may type `on --recover-transport` to arm recovery within that grant's
lifetime. This delegates retrying interrupted assigned work, not approvals,
new tasks, new agents, permission changes or a merge. No extra program, SDK,
API key or service installation is required beyond the package's existing
Python and host requirements.

On a host with native wake hooks, the packaged `asyncRewake` hook watches the main session and
its registered subagents. A host-marked transient API error wakes the same
conversation. A child failure tells the coordinator to message only that
existing agent's exact id. Ordinary assistant text, a user cancellation, an ended grant,
changed workspace and exhausted retries cannot wake it. The retry prompt is
not permission to change the original role, scope or approvals.

On a host with a built-in thread scheduler, `on` emits `native_recovery_enrollment`. Execute the
named built-in `automation_update` tool with those exact arguments as part of
this invocation, without another owner question. Record the returned host
automation id with `native-bind --grant <grant> --automation-id <id>`. Do not
create another heartbeat for an already-bound grant. The thread heartbeat
keeps the coordinator's context, inspects only the registered native threads,
and uses `native-plan` to select failed transport turns. A coordinator action
continues this thread directly; child actions use the existing-thread message
tool with the exact returned id and prompt, never a new thread or agent. A
dispatch hook checks a single-use, short-lived permit against the coordinator,
agent id, unchanged prompt, current grant and workspace before allowing the
retry. It refuses an interrupt. Pause
that heartbeat when its grant expires, is revoked, is replaced or reaches its
goal. If the host lacks the built-in heartbeat tool, report native recovery as
unavailable; do not silently substitute a new CLI conversation or say it is
armed. Native recovery uses the host's existing tools, and does
not install or expose a local app-server listener.

For separately launched CLI sessions, `register-recovery --session-id <uuid>
--transcript <host-jsonl-file> --role <role> --pid <pid>` remains available.
Register only assigned sessions of this project. A still-running CLI process
is not resumed. `heal` performs one check manually; it never selects the host's
latest session implicitly. All paths preserve the native permissions, reject
workspace changes after failure, bound retries and keep a recovery audit log.
