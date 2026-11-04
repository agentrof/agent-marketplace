# Compute profiles

`/configure models` changes only the requested project compute profiles.
`templates/model-classes.json` declares their host-specific defaults and five
technical launch identities.

| Profile | Launch identity | Access |
| --- | --- | --- |
| `light` | `light-worker` | Write |
| `work` | `worker` | Write |
| `review` | `reviewer` | Read |
| `critical` | `critical-worker` or `critical-reviewer` | Write or read |

Task size does not automatically escalate a model. The owner selects the
profile from uncertainty and consequence, and selects access independently.
Actual permission enforcement follows the host contract.

## Procedure

1. Read `project_config.py classes --config workspace/config.json --host
   <host> --model-list --json`. It reports package defaults, project overrides,
   available choices and supported efforts. Host availability and requested
   settings remain separate from observed runtime settings.
2. Discuss only the profiles the request changes. Show the model/effort delta,
   affected launches and the tradeoff to test. Preserve other profiles.
   Ask an extra confirmation only when the package marks the selected effort
   as requiring it. Use that report's evidence and sources.
3. After approval, run `project_config.py set-class --config
   workspace/config.json --host <host> --class <profile> --model <id>
   --effort <level>`. Omit unchanged values. Use `--default` to remove an
   override and `--confirmed` only for an effort the owner confirmed.
4. Run `project_config.py check --config workspace/config.json`. Apply and
   check the local launch projection through the host contract. Report the
   config diff, resolved launch settings and any reload requirement.
5. Confirm the loaded launch identity before using its settings. Record
   host-observed model and effort when exposed. Otherwise leave them unverified.

A value equal to the package default records no override. A model with no
reasoning-effort setting omits it. An explicit `session` model override retains
the selected effort. An unavailable model keeps its requested pin. Recovery
selects an authorized fallback profile for the affected attempt and preserves
other workers' settings.

`role_classes`, the persona-setting command and automatic escalation slots
are retired. Resolve legacy conflicts through the reviewed replacement in
`config-contract.md`. Host launch files are generated from the package and
project overrides. Global personal settings remain user-owned.
