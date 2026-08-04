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
The release tool combines pending changesets and applies the highest requested
impact for each component.
