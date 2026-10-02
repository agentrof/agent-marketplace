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
`python3 tools/release.py bump`, consumes every pending changeset: it applies
the highest requested impact to each component, writes the summaries into the
release's `CHANGELOG.md` section and deletes the files.
