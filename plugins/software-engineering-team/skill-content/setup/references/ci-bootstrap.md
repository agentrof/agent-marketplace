# CI Bootstrap

This is the deferred Delivery activation contract. Project setup does not
materialize CI. `merge-pr` merges a Delivery PR only on green provider checks,
so `delivery_compile.py approve-execution` refuses, on every approval and
re-approval, until a committed workflow will run on that PR or the approved
Verification Contract declares that its checks come from outside the
repository's workflows. When neither holds, offer the packaged
`templates/ci-tests.yml` before execution approval; the project then commits it
and pushes it to the target branch.

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

The Verification Contract's `pull_request_check_source` names where the
Delivery PR checks come from. `repository_workflow`, the default for a contract
without the field, requires the workflow above. A project whose checks come
from an external CI reporting through the Checks API or commit statuses, or
from an organization-level required workflow or ruleset, declares `external`
and names that source in `pull_request_check_provider`. The operation compiler
refuses any other source, an external source without a provider, and a
provider for `repository_workflow`. Approval then requires no workflow. It
reports the declared provider because `merge-pr` still merges the Delivery PR
only on green checks, so that provider must report them on the Delivery PR and,
like any CI, run the portable vault gate below. The source is contract content,
so changing it takes a contract revision and approval, whose new hash blocks
Item start, resume, reopen and takeover until execution is approved again.

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

The `vault_gate` and `tests` jobs require complete Git history. The gate can
resolve previously accepted sources from earlier committed blobs, and Item
verification can read pinned approved receipts and Test Plans after later
revisions replace them. Use `fetch-depth: 0` with `actions/checkout`; its
[default checkout fetches one commit](https://github.com/actions/checkout/blob/v4/README.md).
Custom workflows and external CI that run these checks need equivalent
history. Existing committed workflows need the same checkout setting;
changing the packaged template does not edit them.

## Delivery closure check

The packaged `templates/delivery-closure.yml` is an opt-in workflow that
`operation_compile.py render-closure-ci --output
.github/workflows/delivery-closure.yml` writes as it ships; the project then
commits it and pushes it to the target branch. The workflow runs the base
branch's tracked `.github/agentrof/vault-gate.pyz`, so `render-closure-ci`
refuses while that archive lacks the `delivery-closure` subcommand: reinstall
it with `vault_gate.py install` and commit it in the same commit as the
workflow, or every pull request fails. Setup and refresh never write or edit a
committed workflow or archive, and execution approval never requires this one.
It runs on `pull_request_target` for `opened`, `synchronize`, `reopened` and
`edited`, so retargeting a pull request to another base runs it again; every
edit runs it, because a job skipped by its `if:` condition reports success to
a required check. It has read-only `contents` permission, checks out the
event's `github.sha`, the base branch tip that holds the workflow, with full
history, fetches `refs/pull/<number>/head`, verifies that it is the event's
head commit, and runs the base branch's `vault-gate.pyz delivery-closure`
with the repository's default branch as the Delivery target, as the detached
checkout has no remote HEAD and no current branch to name it.
Event values reach the steps only through `env:`, and the pull request's own
files are read as Git data and never run. The check passes a pull request no
open Delivery manages. A pull request is managed when its head ref is an
`agentrof/` ref, when its head holds commits that only an open Delivery's
Integration or Item refs reach and its base lacks, when it changes a path under
a path claim of a not cancelled Item of an open Delivery, or under a live
provisional claim of an open Delivery, among the paths it
changes against its own base, compared from its merge base with that
Delivery's own target branch, or when it writes the exact product bytes
of such an Item that its base lacks at paths it changes itself; labels and other branch names never decide
it, and a promotion between branches that holds only target commits is not
managed. A managed pull request passes only as the recorded PR head of its
Delivery with every closure precondition met: the PR record, its intent and
its published Review carry exactly what their verbs write, and each commit on
the reviewed Integration line is a control record whose product change is its
Item's, its target merge's or its cancellation's, and every Item integration
on that line merges a Story of the reviewed package and holds in its own tree
code review and verification notes, approved and passed, bound to the product
tip its seal names. Each failure names the step
that owns its recovery; a merged Delivery's claims hold until `verify-merge`
drops its refs, and the check names that step.

The check is red on `opened`, because the head then is the PR intent, and turns
green on the `synchronize` that the PR record's push runs. A recovery step that
changes no PR head, such as the release of a leftover Slot, does not run it
again: re-run the failed check from the pull request's checks page after that
step. A pull request that changes
`.github/workflows/delivery-closure.yml` or `.github/agentrof/vault-gate.pyz`
still runs the base branch's copies, but gets a `DELIVERY_CLOSURE_GATE_CHANGED`
warning, as the changed copies decide every later pull request; protect both
paths with CODEOWNERS and required code owner review, or a ruleset that
restricts them, so the project owner reviews each such change.

A green check prevents nothing by itself. Only an owner-installed ruleset on
the target branch that requires the `delivery-closure` check from the GitHub
Actions app (`integration_id` 15368), or requires
`.github/workflows/delivery-closure.yml` of this repository with a workflows
rule, with no bypass actors, can stop a direct provider merge, an admin bypass
or an owner push; no command of this package can. A check required by name only
can be met by a commit status or by a same-name job, so `protection-status`
reports it `not_configured`. Pinning the check to the Actions app still
accepts a same-name job from a workflow a pull request adds, which the path
protection above or a workflows rule closes. `delivery_git.py
protection-status` reads the provider's rules read-only and reports whether
the check is required, whether a pull request is required and whether the
ruleset has no bypass actors, each `configured`, `not_configured` or `unknown`,
with the reason for a check required by name only. A property reads
`not_configured` only when both the branch rules and the classic branch
protection are readable, or GitHub answers that the branch is not protected.
Classic protection hidden from a token without admin rights, branch rules or
a ruleset's bypass actors the token cannot read, and a repository id a
workflows rule must name that cannot be read all read `unknown`. The report
never refuses anything.

The closure check proves that the records have the coordinator's shape and
bind each other, the reviewed package and the product; it does not prove who
wrote a Review, a code review or a verification. Anyone who can push the
`agentrof/` refs can write consistent notes that pass, so protecting them is
part of the required setup: the owner installs a ruleset that targets
`refs/heads/agentrof/**` and restricts creations, restricts updates, restricts
deletions and blocks non-fast-forward pushes, with only the accounts that run
the coordinator as bypass actors. A writer limited by deletion and force-push
rules alone can still create these refs or fast-forward an Integration onto
consistent forged notes. The coordinator needs the bypass because it creates
every `agentrof/` ref, `merge-pr` and `verify-merge` delete the Integration and
Item refs of a proven merge, `integrate-item`, `pause-item` and
`cancel-delivery` delete Slot refs, as do `start-item`, `reopen-item` and
`takeover-item` when the target moves during activation, `refresh-target`
re-issues Item claims on the refreshed Integration as non-fast-forward
updates, and every atomic push carries `--force-with-lease`. A PR
record's paths other than the notes `open-pr` authors, such as the vault
projections it re-renders, are only required to be non-product paths under
`workspace/docs/`.

The Delivery activation materializer, `operation_compile.py render-ci`, reads
the approved Verification and, when required, Environment Contracts. It
substitutes the test command and renders optional dependency-audit and
environment-smoke job blocks in `ci-tests.yml`; setup never reads command
fields from config.
Project setup substitutes `{{project_local_ignores}}` in the packaged
gitignore template from the product's declared project-local roots. No token
is written literally to a consuming repository.

- Refuse materialization while any placeholder source is absent.
- Refuse materialization for a contract that declares an `external`
  `pull_request_check_source`; that project runs no repository workflow for
  its checks.
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
