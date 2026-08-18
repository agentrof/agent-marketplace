# Engagement Session

Read this reference before starting or resuming a solution-design engagement.
The solution tree is a living, project-level Markdown landscape. An engagement
studies one topic; accepted decisions are written back to the landscape.

## Preflight

1. Confirm that `workspace/config.json` is present and owned by
   `software-engineering-team`. `output_language` governs authored prose;
   filenames, IDs, keys and status values remain English.
2. Ground every cited analysis domain with
   `ba_compile.py check --space <space> --gate approval`. Route failures to
   business analysis.
3. Use one stable kebab-case slug per engagement topic. A later revisit updates
   the same canonical engagement and records changed decisions in their normal
   structures; Git history preserves the prior prose.
4. Run `landscape_check.py --tree workspace/docs/solution-design` and
   `vault_check.py check --vault workspace/docs --scope solution-design`.

## Engagement work

1. Maintain `engagements/<slug>.md` with framing, touched components,
   requirements, constraints, options and a verdict.
2. Link the landscape, analysis criteria and decisions in front matter. Every
   decision records its accepted alternative and the reason for rejection.
3. Ask fresh read-only challengers for structured findings. Resolve accepted
   findings in the engagement or decision documents and keep the reviewer
   replies transient.
4. Run the artifact, landscape and vault checks after each milestone.

## Solution gate

Before asking the owner to approve, run all mechanical checks, ensure every
challenge finding is dispositioned, render the decision index and update the
solution-design map. On approval, stamp the engagement with the supplied
checker command and commit the complete solution tree together.

The tracked Markdown tree and its compiler results are the complete engagement
state.
