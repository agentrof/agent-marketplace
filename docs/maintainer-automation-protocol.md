# Maintainer automation protocol

This protocol turns two recurring maintainer intentions into explicit state
machines:

- an issue may advance automatically to a tested, reviewable pull request, but
  never through merge;
- an explicit release instruction may merge the already-selected eligible
  change, prepare and publish the stable release, and restore a clean local and
  remote Git state.

The protocol is repository maintenance infrastructure. It is separate from the
shipped Requirement and Delivery flows used inside consuming projects.

## Authority model

An issue is problem input, not authority. An issue author cannot grant GitHub
write access, weaken a gate, select a release, or authorize a merge. The
following transitions are the only authority-bearing events:

| Transition | Required authority |
| --- | --- |
| Trusted bug or improvement enters isolated solving | Issue author is `OWNER` or `MEMBER`, and the event actor passes the action's live write-access check |
| External report enters isolated solving | A write-authorized maintainer applies `automation:solve` |
| Candidate PR merges to `main` | The user explicitly approves that PR, or explicitly starts a release whose selected set contains it |
| Stable release starts and completes | The user explicitly instructs the maintainer agent to start the release |

If more than one unmerged PR could match a release instruction and the user did
not name the set, the maintainer agent stops and asks for the exact PRs. It
never infers an arbitrary batch from recency, labels, milestones, or open
changesets.

## Flow A: issue to approval-ready PR

```text
RECEIVED
  -> ELIGIBILITY
  -> ISOLATED_SOLVE
  -> SECRETS_FREE_VERIFY
  -> PR_PUBLISH
  -> HOST_AND_OS_GATES
  -> AWAIT_MERGE_APPROVAL
  -> MERGED_TO_MAIN
  -> ISSUE_CLOSED
```

### 1. Eligibility

Bug and improvement issue forms apply the existing `bug` and `enhancement`
labels. On `opened` or `reopened`, a report from an owner or organization member
may start immediately. A collaborator or external report receives an
approval-boundary comment and waits for a write-authorized maintainer to apply
`automation:solve`. The pinned Codex action independently verifies that the
event actor currently has repository write access.

The workflow refuses issues labeled `security` or `automation:blocked`.
Suspected vulnerabilities belong in private vulnerability reporting. The
branch name is deterministic, `codex/issue-<number>`, and any existing branch
or open PR stops a duplicate run.

### 2. Isolated solve

The Codex job receives the OpenAI key but only `contents: read`; checkout
credentials are not persisted and the action receives no GitHub write token.
The issue title and body are bounded, control bytes and HTML comments are
removed, and the remaining JSON is explicitly marked as untrusted problem
data. Trigger authorization is checked twice: by repository policy and by the
Codex action's default write-collaborator restriction.

The solver must:

1. establish the root cause from repository evidence;
2. analyze shared, host-specific, Linux, macOS, Windows, ConPTY, and real WSL2
   impact where the changed surface reaches those gates;
3. challenge the obvious solution for regression, security, compatibility,
   upgrade, rollback, and release risk;
4. implement the smallest complete fix and regression tests;
5. generate every distribution from canonical sources;
6. add one new changeset and run `make check`;
7. return a bounded structured result and UTF-8 patch, without committing,
   pushing, opening a PR, merging, closing the issue, or releasing.

The autonomous path rejects binary patches and modifications to its own
authority, workflow, packaging, release, validation, memory, and security
control plane. Those changes use the normal maintainer-led path.

### 3. Secrets-free verification

A second job has no OpenAI key and no repository write permission. It decodes
the closed result, validates every patch path and size, applies the exact patch,
regenerates distributions, rejects generated drift and undeclared files,
checks the PR changeset, and runs `make check`.

A blocked solver result publishes no branch. A failed verifier publishes no
branch. Logs remain available for diagnosis.

### 4. PR publication and gates

Only after verification does a fresh job mint a short-lived installation token
from a dedicated GitHub App. Before that token exists, the job re-reads the
issue and destination with its read-only `GITHUB_TOKEN`. A closed issue, a new
blocking label, withdrawn external approval, or an existing branch or PR stops
publication. The publisher then re-materializes the same result, compares the
patch hash, applies the patch, and never executes candidate code. It pushes
the deterministic branch and opens a PR whose body contains root cause,
challenged solution, impact, reported tests, and `Closes #<number>`.

The dedicated App token is important. Enabling the repository-wide “Allow
GitHub Actions to create and approve pull requests” switch would couple PR
creation to approval authority. A GitHub App can instead be installed only on
this repository and mint a token narrowed to Contents, Issues, and Pull
requests write. The resulting PR emits the ordinary `pull_request` event, so
the exact candidate receives the existing `validate.yml`, `codeql.yml`, and
path-sensitive `release-hosts.yml` gates without synthetic dispatches or a
long-lived personal token. Those gates include native Linux, macOS, Windows,
ConPTY, and real WSL2 evidence plus Claude, Codex, and OpenCode coverage when
the changed surface reaches the host release contract.

The workflow contains no merge command. The PR remains at
`AWAIT_MERGE_APPROVAL` until a maintainer reviews the diff and every required
check is green. Merging the PR closes the issue through the PR relationship;
release publication is not the issue-resolution boundary.

## Flow B: explicit release to clean main

```text
RELEASE_REQUESTED
  -> SELECTED_PR_SET
  -> FEATURE_MERGE
  -> MAIN_GREEN
  -> PREPARE_RELEASE
  -> MAINTAINER_RELEASE_PR
  -> RELEASE_PR_GREEN
  -> RELEASE_MERGE
  -> PUBLISH_GREEN
  -> REF_AUDIT
  -> BRANCH_CLEANUP
  -> CLEAN_MAIN
```

An explicit instruction such as “start the release” authorizes this complete
flow for the PR set already selected in the active task. It does not authorize
unrelated PRs, failed checks, force pushes, tag replacement, or arbitrary
branch deletion.

The maintainer agent performs these steps without asking for a second approval
unless scope becomes ambiguous or a gate fails:

1. Resolve the selected PR set. Require same-repository heads, `main` bases,
   non-draft status, the intended issue linkage, review approval when branch
   policy requires it, and all required checks green.
2. Merge each selected feature PR with branch deletion. Wait for the exact
   resulting `main` commit to pass its required validation. Confirm its linked
   issue is closed.
3. Dispatch `Prepare stable release` on `main`. That workflow binds the exact
   SHA, runs every release host gate, consumes pending release-impact
   changesets, generates distributions, runs `make release-check`, and pushes
   `release/stable`.
4. Open the release PR with the maintainer's GitHub identity using the compare
   URL emitted by the prepare workflow. This human-identity step is
   intentional: it guarantees the normal `pull_request` validation event.
5. Wait for every release PR check. Merge only when green. The explicit release
   request authorizes this release PR merge after the gates pass.
6. Wait for `Publish stable release`. It verifies the exact merge commit,
   stable-base ancestry, tag and release collision absence, host behavior, and
   package release contract before atomically updating `stable` and the tag,
   publishing the GitHub Release, and deleting remote `release/stable`.
7. Verify the published tag, `origin/main`, and `origin/stable` resolve to the
   same commit; the GitHub Release is neither draft nor prerelease; selected
   issues are closed; and remote release/feature branches are absent.
8. Delete only the explicitly selected, merged local `codex/<kebab-name>`
   feature branches and any
   merged local `release/stable`. Fast-forward local `main`, align local
   `stable` to `origin/stable`, prune remote tracking refs, switch to `main`,
   and require an empty `git status --short`. Use the fail-closed finalizer with
   every selected branch named explicitly:

   ```console
   python3 tools/release.py finalize-local --version X.Y.Z \
     --branch codex/issue-123 --branch release/stable --apply
   ```

   The command refuses a dirty worktree, mismatched main/stable/tag refs, stale
   remote `release/stable`, unsupported branch names, duplicate branch inputs,
   or any branch not proven ancestral to `origin/main`.

If any invariant fails, stop at the current recoverable state and report the
exact failed gate. Never repair a release by moving an existing tag, force
pushing `main` or `stable`, deleting an unmerged branch, or bypassing CI.

## One-time activation

The repository keeps issue solving fail-closed until its external dependency is
configured. A maintainer must perform this setup in order:

1. Register a dedicated GitHub App, install it only on this repository, and
   grant only repository Metadata read plus Contents, Issues, and Pull requests
   write. Store its App ID as Actions variable `ISSUE_AUTOMATION_APP_ID` and its
   private key as secret `ISSUE_AUTOMATION_PRIVATE_KEY`. Do not grant Actions,
   Administration, Workflows, Secrets, or organization permissions.
2. Add repository secret `OPENAI_API_KEY`. The official Codex GitHub Action
   requires an OpenAI API key; a ChatGPT login alone is not runner
   authentication.
3. In the repository's selected-actions policy, allow only the immutable
   `openai/codex-action@86365089eb2b84e0a8fb0717b304f8bdcb13b20e` reference.
   Keep SHA pinning required. `actions/create-github-app-token` is GitHub-owned
   and is independently SHA-pinned in the workflow.
4. Create label `automation:solve` with write access restricted by GitHub's
   normal issue-label permissions. Optionally create `automation:blocked` for
   an explicit deny state.
5. Keep the repository-wide Actions create/approve-PR switch disabled. Confirm
   `main` branch rules require the repository's validation and security checks,
   reject force pushes and deletion, and do not grant the dedicated App a
   bypass.
6. Set repository Actions variable `CODEX_ISSUE_AUTOMATION_ENABLED` to `true`.
7. Run the mechanical external-state audit. It reads secret names, never secret
   values:

   ```console
   python3 tools/maintainer_automation.py doctor \
     --repository agentrof/agent-marketplace
   ```

8. Open a harmless maintainer-authored canary bug. Confirm the isolated job has
   no write token, the verifier has no secrets, the publisher does not execute
   the patch, ordinary PR checks attach to the candidate SHA, and the PR remains
   unmerged.

Disable new starts immediately by setting the variable to `false`. Existing
branches and PRs remain ordinary GitHub objects and can be inspected or closed
without the agent.

## Impact and residual risk

| Surface | Effect | Residual risk and control |
| --- | --- | --- |
| GitHub Actions | Adds API usage and isolated solve, verify, and publish jobs | Disabled by default; concurrency is per issue; time and output sizes are bounded |
| Secrets | Codex job receives one OpenAI key; publisher receives one App private key through a pinned token minter | Secrets never share a job; no model job has repository writes; installation token is narrowed and revoked after the job |
| Untrusted issue text | Can influence a model-generated patch | Trusted trigger, bounded/sanitized context, protected paths, secrets-free verification, no automatic merge |
| CI | Bot-authored PRs must emit ordinary events | Dedicated App token triggers normal PR workflows; no synthetic status or dispatch substitutes for PR evidence |
| Hosts and operating systems | Shared source may affect every distribution; relevant PRs now run the real WSL2 job too | Canonical build plus Linux, macOS, Windows, ConPTY, WSL2, Claude, Codex, and OpenCode gates; path filters and cancellation bound the added CI cost |
| Releases | Explicit request can perform several mutations | Selected-set rule, green-gate requirement, exact-SHA provenance, atomic stable/tag push, no force repair |
| Branch cleanup | Deletes merged refs | Only selected branches proven merged are eligible; ambiguous or unmerged refs stop cleanup |

The final human PR review remains a deliberate control. Model output, local
tests, and cross-host CI reduce risk but do not redefine approval.
