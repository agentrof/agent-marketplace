# Mutation Gate

Why it exists: every other gate proves "a tagged test passed", never that
its assertions bite. When the code and its tests share an author, weak or
tautological assertions are the dominant escape. Mutation testing is the
deterministic backstop: the runner plants small behavior changes
(mutants) and a test suite that stays green over a mutant proved it
asserts nothing about that behavior.

## Running the gate

- The command comes from the approved Verification Contract's `mutation_command`; the
  project's setup configured a runner per stack and verified it runs.
  Never hardcode a tool.
- Scope: the code-owned files this story changed, never the whole tree.
  Environment-owned paths (the workspace/environment/ prefix) are
  excluded; the live protocol verifies them, not mutants. The command
  carries a `{{changed_files}}` placeholder QA substitutes with the
  space-joined list (git diff --name-only against the main line, the
  environment prefix filtered out); a runner that scopes only through
  its config file gets the scope
  written there for the run, and the record states the effective scope
  either way. A whole-tree run on a mature project is a budget
  violation, not extra rigor.
- The gate signal is the RUN invocation's own exit code. Never chain a
  report command after the run inside mutation_command (the chain's
  exit code masks the gate); collect the report with a second
  invocation and record both.
- Record in the verification record: the exact composed command, the
  scope, the mutant totals (generated, killed, survived, timed out) and
  every survivor with its file, line and mutation description.

## Judging survivors

File scope governs: a surviving mutant anywhere in a changed FILE is in
scope for judgment, whether or not its exact line changed.

- A surviving mutant in a changed file is a finding:
  - MAJOR when the mutated line sits on a path a BR-### or AC-### test
    is supposed to prove (the tag map from the coverage audit tells you);
    the requirement's test does not actually check the behavior.
  - MINOR when the line is incidental (logging, formatting, defensive
    branches the contract does not name).
- Route the finding to the owning developer with the survivor's
  description; the fix is a stronger assertion or a missing case, never
  deleting the mutant's target.
- Equivalent mutants (behavior genuinely unchanged) are recorded as
  ACCEPTED with one line of reasoning; more than a handful of "equivalent"
  calls in one story is itself a smell that the tests assert too little.

## Gate semantics

- Pass: zero unaccepted survivors in the changed files.
- A missing `mutation_command` on a story that changed code is a
  blocking finding routed to the owner: configure the runner via the
  configure entry. Documentation-only, asset-only or environment-only
  stories are exempt; say so in the record.
- The gate reads the runner's exit code and report; QA never overrides a
  survivor by judgment alone.

## Environment pitfalls

- Fork-based mutation runners abort their workers on macOS: the OS kills
  forked children of processes that touched system frameworks, and every
  mutant then reports as a crash instead of a kill/survive verdict. On
  macOS configure a subprocess-based runner, or delegate the mutation
  gate to a Linux CI job; either way the runner must be verified by one
  real invocation before the config records it.
- A runner that cannot produce kill/survive verdicts in this environment
  is a blocking finding with the waiver path, never a silent skip: the
  record names the constraint, the owner waives with a reason, and the
  CI lane carries the gate.
