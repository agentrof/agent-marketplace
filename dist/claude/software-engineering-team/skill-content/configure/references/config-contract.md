# Configuration contract

`workspace/config.json` is the closed project bootstrap contract. Accepted
keys are `schema_version`, `team_id`, `output_language`, `terminology_language`,
`tests_root`, `workspace_layout`, `pr_proof`, `local_proof`, `local_proof_map`,
`proof_runner`, `task_triggers` and `class_models`. The current schema is 2.
Setup supplies missing layout defaults and preserves valid project overrides.

Use `project_config.py set --config workspace/config.json --field <field>
--value <JSON-or-string>` for ordinary fields. Preview the requested delta with
`--dry-run` and show its consumers before applying a durable setting change.
Model settings use `set-class`, described in `role-models.md`.

## Field ownership

- Languages control authored prose and terminology. Keys, paths and machine
  output remain English. Changing a language does not rewrite existing notes.
- `tests_root` defaults to `workspace/tests`. `workspace_layout` declares
  permitted top-level workspace and documentation folders.
- `pr_proof` selects `remote_ci` or `local_record`. Local proof commands,
  impact rules and an optional runner follow the delivery entry's
  `references/local-proof.md`. An uncovered path requires full proof.
- `task_triggers` overrides named method, risk and reviewer trigger rules.
  Rule predicates may use `path_globs`, `keywords` and `exclude_globs`.
  Reviewers may additionally use `rigors` and `on_any_risk`. Available reviewer
  identities are `reviewer` and `critical-reviewer`. Discovery triggers still
  require the owner to confirm semantic effects.
- `class_models.<host>.<profile>` overrides model or effort for `light`, `work`,
  `review` or `critical`. Missing keys use package defaults. `session` follows
  the parent model while retaining the profile's effort.

Technology choices, data invariants, capacities and service boundaries belong
in accepted design records. Environment and verification commands belong in
operation documents, with executable proof commands referenced by config.
Keep those records aligned with code when their contract changes.

## Legacy migration

Setup previews compatible model and trigger renames. It removes empty
`role_routing` and the old default `task_criteria_range` of `[3, 8]`.
Custom retired settings, persona overrides and differing rules that would
collapse onto one identifier require an explicit decision. Their original
values remain intact while that decision is pending.

Prepare a complete reviewed replacement JSON and calculate the SHA-256 of the
current config bytes. Preview the exact replacement with
`project_config.py migrate --config workspace/config.json --config-file
<reviewed.json> --expected-sha256 <digest> --dry-run --json`. Show before,
after and dropped keys. Apply the identical reviewed payload without
`--dry-run` under the owner's migration approval. The writer validates the
closed shape, serializes with project maintenance and rejects a changed source
digest. Then run `project_config.py check --config workspace/config.json` and
refresh affected local launch definitions through the host contract.
