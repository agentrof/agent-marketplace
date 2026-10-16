# Process profile: fast lane

At Process Policy `process_profile` `fast_lane`, every change reaches the
target branch through one story card and its own pull request,
`flows/fast-lane.md`.

## Entries

- `/deliver`: one card, the owner's approval on the card, then branch, code
  and tests, review and merge.
- `/requirement` stays for a large, ambiguous or multi-part request. It
  clarifies the scope with the owner, runs the business-analysis,
  solution-design, design-system or experience-design stage only when the
  request needs it, then splits the work into story cards, each started with
  `story_lane.py start` and run as an independent `/deliver`, side by side. Each
  card names the documents it rests on as wikilinks in a `## Sources`
  section. No backlog revision and no plan approval follow.
- The stage entries keep their full method. Their reviews run one reader in
  a single pass and only a critical finding blocks; every other finding
  becomes an issue. Their approved documents reach the target branch by a
  plain pull request, with no Fence or source handoff.
- `/backlog-plan`, `/delivery-plan`, `/execution-plan` and the governed
  Delivery entry `deliver-governed`, all hidden from the user's menu, start
  no new work: tell the user the fast lane is on and to use `/deliver` or
  `/requirement`, and stop. A Delivery opened before the switch finishes
  under the values it pinned; no Delivery flow owns this switch, so changing
  it never forces a Delivery's plan revision.
- Vault, setup, configure, retrospective and autopilot entries are unchanged.

## Story path

- A card lives under `workspace/docs/backlog/cards/`, type `story-card`,
  checked by `scripts/story_lane.py lint` and the vault law only. The
  backlog compiler, epic and root reviews and backlog revisions never read it.
- Delivery is one branch and one pull request to the target branch. Nothing
  reserves a Slot, starts an Item, publishes or approves an execution plan,
  claims paths, pins or pairs Operation contracts, or reopens an Item.
  `story_lane.py overlap` warns about open pull requests that touch the same
  files and never refuses.
- Verification is the project's CI on the pull request head plus one
  `code-reviewer` reading the diff. Only a critical finding blocks; every
  other finding is filed as an issue in the project's repository. Auto-merge
  lands the pull request once CI is green and the reviewer passed.
- The card's `rigor` is `normal` by default. At `high`, proposed for
  authentication, credentials, secrets, data deletion and irreversible
  actions and confirmed by the owner on the card, a second reviewer with the
  security and data-loss lens joins, every affected live group runs and the
  owner merges.
- At rigor `normal`, live tests run only when the diff touches environment
  code or live test files and the change can alter what a live group
  proves; otherwise they run on demand.
- The main session runs every role in the foreground; no delivery
  coordinator is spawned.
- An architecture record revision names the card id as its item reference:
  `architecture_compile.py` accepts an active or delivered card as the owner
  at `fast_lane` and stamps no Item receipt.
- Times: the card holds `requested_at` and `approved_at`; the pull request,
  review comment, CI and merge times are read from GitHub by
  `/retrospective`. Nothing is written after the merge.
- The CI workflow that runs the project's tests on pull requests is
  rendered once with `operation_compile.py render-ci --output
  .github/workflows/tests.yml` from the approved Verification Contract's
  test command, committed by a story, and made a required check.
