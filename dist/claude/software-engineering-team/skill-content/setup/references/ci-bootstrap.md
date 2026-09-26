# CI Bootstrap

This is the deferred Delivery activation contract. Project setup does not
materialize CI. `delivery_compile.py approve-execution` refuses, on every
approval and re-approval, until a committed workflow will run on the Delivery
PR, because `merge-pr` merges that PR only on green provider checks. When none
exists, offer the packaged `templates/ci-tests.yml` before execution approval;
the project then commits it and pushes it to the target branch.

A workflow counts when it is a `.yml` or `.yaml` file directly in
`.github/workflows/`, its top-level `on` names `pull_request` or
`pull_request_target`, and that event lists no `types` or includes
`synchronize`. After the PR opens, `open-pr` pushes the Integration head that
records its URL, and `merge-pr` reads that head's checks. Only `synchronize`
runs for that push; `opened` and `reopened` alone check earlier heads. The
test job is optional, so a workflow that runs only the portable vault gate
counts. Counting does not guarantee a mergeable check: `merge-pr` still
refuses a job skipped by its `if:` condition and a green commit status, which
reports no conclusion
([agentrof/agent-marketplace#229](https://github.com/agentrof/agent-marketplace/issues/229)).

The check reads committed trees with local Git and never fetches. GitHub runs a
`pull_request` workflow from the PR merge commit, so it counts in the target
branch's remote-tracking ref or in the Delivery Integration's. GitHub runs a
`pull_request_target` workflow from the default branch, so it counts only in
the target's, which is the default branch whenever
`refs/remotes/<remote>/HEAD` names it. Without a remote-tracking target, `HEAD`
stands in; outside a Git checkout, the working tree does. A written but
uncommitted workflow, or one not yet pushed and fetched, does not count.

A project whose pull request checks come from an external CI provider or an
organization-level required workflow still needs a minimal `pull_request`
workflow here, and one that runs only the portable vault gate suffices. A
declared exemption is tracked in
[agentrof/agent-marketplace#230](https://github.com/agentrof/agent-marketplace/issues/230).

The check parses lines, not YAML. It reads a top-level `on` whose value is one
event or a one-line list, or whose direct children are event keys or list
items, and an event's `types` as a scalar or one-line list on the key's line, a
block list below it, or a one-line flow mapping. It resolves no other form. It
evaluates no `branches` or `paths` filter and cannot see a workflow disabled on
GitHub or Actions turned off, so any of these can still leave the Delivery PR
without a check.

The template always runs the tracked portable single-vault gate before the
project-specific jobs. Do not replace it with an installed plugin path; every
supported host and CI must execute the same `.pyz` bytes.

The Delivery activation materializer, `operation_compile.py render-ci`, reads
the approved Verification and, when required, Environment Contracts. It
substitutes the test command and renders optional dependency-audit and
environment-smoke job blocks in `ci-tests.yml`; setup never reads command
fields from config.
Project setup substitutes `{{project_local_ignores}}` in the packaged
gitignore template from the product's declared project-local roots. No token
is written literally to a consuming repository.

- Refuse materialization while any placeholder source is absent.
- Use `test_command` and its declared `test_workdir` from the approved
  Verification Contract.
- Use the contract's explicit dependency-audit disposition and command. The
  Solution decision determines technologies; CI does not infer audits from a
  global config value.
- Use the approved Environment Contract only for a Delivery that declares a
  live runtime check. Include `environment_smoke` only when its up-then-down
  probe passes now. Otherwise omit that job and leave the Delivery blocked on
  runtime verification rather than guessing a command.
- Route dependency advisories through the Deliver entry as a fix-atomic
  lockfile bump.
