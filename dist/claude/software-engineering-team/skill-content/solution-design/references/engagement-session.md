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
3. Use a new kebab-case topic slug for each engagement. Reopened topics append
   `-2`, `-3`, and so on; never reuse a closed slug.
4. Run `landscape_check.py --tree workspace/docs/solution-design` and
   `vault_check.py check --vault workspace/docs --scope solution-design`.

## Engagement work

1. Maintain `engagements/<slug>.md` with framing, touched components,
   requirements, constraints, options and a verdict.
2. Link the landscape, analysis criteria and decisions in front matter. Every
   decision records its accepted alternative and the reason for rejection.
3. Debate in explicit rounds. Record each round under `reviews/` and resolve
   every finding as fix, reject (with a reason), or defer (with a revisit note).
4. Run the artifact, landscape and vault checks after each milestone.

## Solution gate

Before asking the owner to approve, run all mechanical checks, ensure every
challenge finding is dispositioned, render the decision index and update the
solution-design map. On approval, stamp the engagement with the supplied
checker command and commit the complete solution tree together.

The tracked Markdown tree and its compiler results are the complete engagement
state.
