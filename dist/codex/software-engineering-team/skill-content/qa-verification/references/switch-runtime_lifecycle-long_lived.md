# Reuse one long-lived product environment

These are the instructions of process switch `runtime_lifecycle` at
`long_lived`, selected in the project's Process Policy. At `fresh`, the
default, every runtime candidate keeps the full down/up path of
`runtime-protocol.md`.

## When the long-lived path applies

The owner sets parameter `environment_paths` in the Process Policy: the
comma-separated path globs of the project's environment code, such as the
environment command, image recipes, deployment manifests and seed scenarios.
Without it every environment command is refused.

A long-lived instance is trusted only once a fresh one has proven the same
environment code. Each freeze carries the Item's fresh runtime history: every
earlier candidate whose session recorded a complete, intact fresh
`down`/`up`/`seed`/`logs`/`down` lifecycle, whatever QA concluded about
product behaviour. The runner decides each candidate's lifecycle at the first
environment command of its session:

- `long_lived` (`fresh_evidence_held`) when a candidate in that history holds
  every path matching `environment_paths` byte for byte as this candidate
  does;
- `fresh` (`fresh_evidence_missing`) when the history holds no fresh
  candidate yet, so an Item's first runtime pass is always fresh;
- `fresh` (`environment_code_changed`, with the matched paths) when every
  fresh candidate in the history differs from this one in an environment
  path.

So repair passes that leave environment code alone reuse the instance, a pass
that changes it runs fresh again, and approval, and with it Delivery closure,
never repeats a fresh run already proven on the same environment code. Each
environment event of a long-lived session records `lifecycle: long_lived` in
its evidence hash. Final QA evidence is refused when the recorded lifecycle
differs from the one the candidate requires, with that requirement and its
reason named, so no Item is approved on long-lived evidence without fresh
evidence of its environment code.

## Long-lived sequence

The project's environment command keeps its verbs; the meaning of two is
stricter on this path:

1. `up` is the identity check. Against a running instance it must be
   idempotent: it converges the instance to the candidate in the runner's
   private checkout, whose `HEAD` is the frozen product commit, and fails
   when the running services do not serve that candidate. It never tears the
   instance down. Against no instance it brings one up.
2. `seed <scenario>` is the reset. It must return the instance to the
   scenario's known state, removing state earlier sessions left behind.
3. `logs`, then `url <service>` per published service, as in
   `runtime-protocol.md`.

No `down` precedes `up` and none ends the session: the instance stays up for
the next candidate, and settling QA or freezing a new candidate does not ask
for teardown. An interrupted command still requires `down` before anything
else. `down` remains available at any time.

Final runtime evidence requires a successful `up`, a `seed` after the last
`up` and a `logs` after that `seed`, all from one recorded environment
identity with exact event hashes. At `qa_preflight: lifecycle_first` the
live groups require that `up`/`seed`/`logs` sequence first and the final log
check still follows the last live group.

A defect that appears only on a fresh instance is a finding against the
reset or the idempotent `up`; widen `environment_paths` or fix the verb
rather than retrying the candidate.
