# Changesets

Every normal pull request adds one JSON file to this directory. The filename is
a short kebab-case description. A changeset records user-visible stable release
impact without editing `versions.json`.

```json
{
  "summary": "Add a backward-compatible delivery capability.",
  "components": {
    "software-engineering-team": "minor"
  }
}
```

Allowed impacts are `patch`, `minor`, and `major`. Documentation, test, CI, and
other changes with no stable release impact use an empty `components` object.
The release commit, the last commit of a pull request, made by
`python3 tools/release.py bump`, consumes every pending changeset: it needs at
least one that declares an impact, names the release `YYYY.M.N` by the
[calendar](../docs/maintainer-operations-protocol.md#calendar-versions), sets
the marketplace and every plugin to that one version, records the highest
impact of each component in `.release/stable.json`, writes every summary into
the release's `## YYYY.M.N` section of `CHANGELOG.md` and deletes the files.
Impacts never choose the version number.
