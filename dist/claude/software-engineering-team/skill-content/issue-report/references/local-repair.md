# Local candidate repair

## Isolate the change

Read the source repository's architecture, authoring and maintainer protocol.
Create a dedicated source branch/worktree and implement the smallest complete
fix in canonical sources. Generate every registered host distribution with
the repository distribution builder. Follow the repository's selected local
gates and exact-candidate review rules. Never hand-edit generated distribution files.

Keep the consumer reproduction and its data outside the public source checkout.
Use synthetic fixtures in the source change. A fix in one host must identify its
impact on every supported host and operating system before publication.

## Load an identified candidate

Use the active host contract's development-package route, either a local plugin
directory or a uniquely named local development marketplace pointing at the
generated package. Use the host's reload or restart requirement. Avoid two enabled copies of the same component in the test
project. Changing installation policy needs its existing owner authority.

Record the candidate's actual loaded path, content digest and host version.
Package files, host projection and active session must all correspond to the
candidate. Editing a source directory alone proves no reload. Test normal entry,
regression behavior and cleanup. Then restore the intended stable selection and
observe its identity. Keep a failed restoration visible.

## Publish once

`file_issue.py` validates the anonymous payload and digest. Its exit 3 means the
external outcome is unknown. Search/reconcile before another create attempt.
The existing approved-payload hash binds content, not permission.

Create a PR from the verified canonical source branch. Include the linked issue,
root cause, affected surfaces, exact validation evidence and remaining limitations.
Use structured text or a body file for multiline CLI payloads. Scan commits and
all added files as well as the PR body. A source change is never auto-merged merely
because it repaired a local installation. Follow the maintainer merge protocol.
