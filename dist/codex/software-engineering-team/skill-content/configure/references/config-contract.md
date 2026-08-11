# Configuration Contract

Read this reference completely before validating, explaining, or applying a
configuration change.

## Supported fields

- `project_origin`: `greenfield` or `existing`. It may change only before
  program, backlog, or delivery state exists. A migrated `unclassified`
  project may be classified exactly once.
- `backend_stack`: `python-fastapi`.
- `frontend_stack`: `react-typescript`.
- `environment_stack`: `docker-compose`.
- `databases`: a non-empty set drawn from `sql` and `nosql`, one or both.
  Values outside these stack enums are refused because the team ships only
  tested stacks; new stacks arrive as maintainer releases.
- `test_command` and `mutation_command`: non-empty command strings.
- `env_command`: one non-empty command entry point implementing `up`, `down`,
  `seed <scenario>`, `logs`, and `url <service>` as defined by the environment
  stack skill.
- `source_dirs`: a non-empty list of repository-relative paths. Refuse
  absolute paths.
- `output_language`: a non-empty language name governing only authored
  Markdown body prose.
- `terminology_language`: a non-empty language name, default English,
  governing names, technical terms, code, comments, commit messages, and PR
  bodies. File names, keys, ids, CLI output, and the machine layer stay English.
- `max_parallel`: an optional positive integer controlling the delivery-lanes
  proposal cap; absent means 3.
- `scale`: optional enum `small`, `medium`, `large`, `x-large`, `xx-large`, or
  `enterprise`; absent means `small`. The business-analysis space schema's
  `scale_profiles` table defines the effective thresholds. Scale multiplies
  volume warnings and raises the domain-nesting ladder; aging, summary caps,
  challenge rounds, and nav peers never scale.
- `model_overrides`: reserved and consumed by nothing. Decline any requested
  value until a maintainer release defines it.

## Limits

`limits` is an optional flat object of positive-integer overrides. Known keys
are exactly:

- `node_direct_docs_warn`, `rule_sets_per_node_warn`,
  `active_br_per_node_warn`, `rules_per_set_warn`, `criteria_per_set_warn`
- `process_doc_lines_warn`, `open_row_age_days_warn`, `space_docs_warn`,
  `space_bytes_warn`
- `nesting_warn_depth`, `nesting_fail_depth`, `challenge_max_rounds`
- `summary_max_lines_space`, `summary_max_lines_default`
- `nav_peer_min`, `nav_peer_max`
- `experience_flows_per_set`, `experience_transitions_per_set`,
  `experience_screens_per_leaf_domain`

Effective precedence is `limits` over `scale` over shipped default. Validate
`nesting_warn_depth < nesting_fail_depth` and
`nav_peer_min <= nav_peer_max` after applying precedence. Refuse unknown keys,
non-integers, and inconsistent pairs while naming the offending key. The
checkers additionally fail soft on invalid hand edits so bad state cannot
brick a space.

## Document type designations

`doc_type_designations` maps each known taxonomy type-kebab to a non-empty
designation in `output_language`. Refuse unknown types and values fold-equal
to another type's current designation. This map and
`doc_type_designation_history` have one writer:
`vault_check.py reconcile-designations`. The write-time hook denies every
other writer.

For a designation change, or an `output_language` change that re-renders the
canonical designation table, obtain the impact plan with:

```text
"$RUN" run "$TEAM" scripts/vault_check.py reconcile-designations \
  --vault workspace/docs --set <type>=<value> ... --dry-run --json
```

Render apply, reject, and adjust-wording options from that plan, including all
before-to-after retitles and alias sweeps. If `locked_skipped` is non-empty,
ask a second choice: audited title/H1 relabel with `--include-locked`
(recommended), or retain the named warnings. Present `manual` and `blocked`
entries as residuals, never approvable options. Apply with `--actor configure`
and the selected `--include-locked`; the verb updates config, titles, H1s,
byte-equal aliases, generated views, and PMO audit events atomically. Close
with a full check and name every residual.

## Impact analysis

Before the choice gate, state:

- Which role-to-skill bindings change. Resolve the static map from
  `"$RUN" path "$TEAM" flows/develop.md`; method skills remain static.
- What future packages do differently and any migration effect on existing
  work. For example, adding a document store makes the architect load both
  database skills and declare a store per entity.
- For `terminology_language`, that only newly authored names change; existing
  names, glossary rows, and merged code are not renamed.
- For `scale` or `limits`, every affected key's effective before-to-after
  value and enforcing checker. The space compiler owns volume, nesting,
  aging, summary, and challenge-round findings; the vault checker owns nav
  peers. Findings must carry exact provenance such as `warn at N: scale
  <level>` or `warn at N: project override`, and generated views remain stale
  until rendered.
- The sharp edge: lowering thresholds may produce split proposals; lowering
  nesting failure depth, summary caps, or challenge rounds may turn approved
  content into a red compile. Run check immediately after the write and show
  every new finding.
