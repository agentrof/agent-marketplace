# Windows Long Paths Choice

Git for Windows leaves out of a checkout, which still exits 0, every tracked
file whose path reaches 260 characters or whose directory reaches 248, unless
`core.longpaths` is set. Item and Integration worktrees sit deep below the
project root and share the project repository's local Git config, and the
owner's own Git commands in them must see the files Delivery sees, so the
setting belongs in that config, written only with the owner's consent.

On native Windows, while that local config does not turn `core.longpaths` on,
`setup_project.py inspect` returns the `git.core_longpaths` choice request.
Its preview names the current value, the deepest Item or Integration worktree
root the Delivery code would create for the next Delivery and the backlog's
Stories, and every tracked file Git for Windows would leave out there, each
with its reason: its directory reaches 248 characters, or else its own path
reaches 260. Present it through the choice gate:

- `set`, recommended: runs `git config --local core.longpaths true`, so every
  tracked file checks out in those worktrees.
- `leave`: writes nothing. Apply then warns, without failing, with each of
  those files and its reason, and the next setup asks again.

Pass the answer to apply as `--choice git.core_longpaths=<set|leave>`. Apply
refuses while a returned choice is unanswered and puts the previous value back
when it rolls back. macOS and Linux never ask, and neither does a value Git
already reads as true.
