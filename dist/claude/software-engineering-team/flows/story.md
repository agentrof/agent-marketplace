# Story Flow

Spawn template: paste `{{constitution}}` into every role prompt. Load the
`obsidian-vault` skill before writing the card; its vault law is
authoritative.

Switch `process_profile`: at `standard`, this flow does not run; tell the
user the fast lane is off, that `/configure` sets `process_profile` to
`fast_lane`, and route the request to `/requirement`. At `fast_lane`, follow
`skill-content/story/references/switch-process_profile-fast_lane.md` and
the steps below. Read the value with
`scripts/process_policy.py value --switch process_profile`.

One request becomes one story card, one branch and one pull request to the
target branch. Git, the pull request and the project's CI are the protocol:
no Delivery, Fence, Slot, Item, Integration branch, execution plan, path
claim, contract pin or architecture stamp is created or read.

The main session orchestrates. It spawns every role in the foreground, one
call that returns the role's result, and never spawns a delivery
coordinator or a background agent that would report to it.

Each step names its `scripts/story_lane.py` verb. The card holds the
request and approval times; GitHub holds the pull request, review, CI and
merge times. Nothing is recorded after the merge.

The card's `rigor` sets the verification:

| Rigor | Reviewers | Live tests | Merge |
|---|---|---|---|
| `normal` (default) | one `code-reviewer` | only when the change really affects a live group | auto-merge on green CI |
| `high` | the `code-reviewer` and a second one with the security and data-loss lens | every affected live group, always | the owner merges |

1. Request. Run `start --title "<short title>" --request-file <file>` from
   the project checkout, with the user's request verbatim in the file, and
   `--delivery DLV-###` when the user groups stories under a Delivery label.
   It creates and pushes `story/<id>-<slug>` from `origin/main`, writes the
   card skeleton under `workspace/docs/backlog/cards/` with `requested_at`,
   and proposes `rigor: high` when the request names authentication,
   credentials, secrets, data deletion or another irreversible action.
2. Card. Fill the card in one pass: the request, 3 to 8 numbered
   acceptance criteria, each one verifiable property, a `## Tests` list
   naming the test file or case that proves each criterion, and `size` s, m
   or l. Set `rigor: high` yourself when the change touches authentication,
   credentials, secrets, data deletion or an irreversible action even if the
   proposal missed it. `lint --story <id>` until it passes. Show the card to
   the owner with the proposed rigor; the owner confirms or changes rigor on
   the card. On the owner's go, or under a standing delegation, run
   `approve --story <id>`; it writes `approved_at`. No analysis, solution or
   design stage and no review panel reads the card. A request that needs
   more than 8 criteria is two stories: say so and start the second after
   the first.
3. Write. Spawn the owning developer role in the foreground with the card,
   the branch and the files it names. It writes the change and its tests on
   the branch, commits, and runs the project's test command for the suites it
   touched, the command the CI workflow runs on pull requests (`make test`
   unless the project declares another). Architecture records and contracts
   the change needs are edited in the same branch; an architecture record
   revision names the card id as its `--item-ref`.
4. Live tests. At `normal`, run a live group only when the diff touches
   environment code or live test files and the change can really alter what
   that group proves; say which group and why. At `high`, run every live
   group the diff can affect. Otherwise live tests are not part of the story
   and run on demand through the project's live command.
5. Pull request. Run `overlap` and pass its warnings on; they never block.
   Run `open-pr --story <id>`. It marks the card delivered, pushes the
   branch and opens the pull request.
6. Review. Spawn one `code-reviewer` in the foreground with the card and
   the output of `gh pr diff <pr>`. It checks each acceptance criterion
   against the diff and its tests and returns findings with severities. At
   `high`, spawn a second `code-reviewer` in the foreground with the same
   inputs and the lens: authentication and authorization, secret handling,
   data loss, irreversible operations and their rollback. Only a critical
   finding blocks: the developer fixes it on the branch, and the reviewer
   that raised it re-reads only the fix diff, once. Every other finding
   becomes one issue in the project's repository through `gh issue create`,
   linked from a pull request comment.
7. Merge. Run `merge --story <id> --pr <pr>`. It posts the review pass on
   the pull request. At `normal` it turns on auto-merge, so the pull request
   merges when its required CI checks are green; a red check is fixed on the
   branch like a critical finding. At `high` it leaves the merge to the
   owner: report the pull request and stop.
8. Report the card, the pull request and its merge state. `/retrospective`
   reads the times later.

The story is done when its pull request is merged. The card on the target
branch is the durable story record.
