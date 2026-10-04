# Rebind Review Scope

These are the instructions of process switch `rebind_review_scope` at
`source_delta`. A task binds this file only when the project's Process
Policy selects that value; at the default, `full`, every final snapshot
review reads the whole package and prototype tree.

Rule 9 stays: the final attestation binds the exact inputs after the last
authored change. A source-only rebind has no authored change, so the
compiler proves the unchanged bytes and the reviewer judges the source
delta.

## When the scope applies

Run `experience_compile.py source-impact --root
workspace/docs/experience-design --source-ref <the rebound source>` after
the package and the application enter review. The scoped review applies to
a package only when its row reports `package_change` `source_rebind_only`
and `review_scope` `source_delta`: every note equals HEAD but for the
root's lifecycle, revision and bindings, no package or application artifact
byte changed, and no note cites a changed source row or document. Any other
row, a second source in the same revision, or a changed input after the
review takes the full review.

## The scoped review

Give the fresh `experience-reviewer` the `source-impact` output, the source
diff from its `source_base_commit` to the working tree for the changed
source documents, the package's notes, and the application registry fields
the attestation binds. Do not give it the prototype tree. The reviewer:

- confirms that no note, flow, state or transition claim depends on a
  changed source row, rule or criterion, whether or not it cites it, and
  reports each one that does as a finding, which ends the scoped review and
  starts the full one;
- writes the same schema-v4 attestation as a full review, bound to the
  proposal hash, artifact-tree hash, package-set hash, application hash and
  revision the compiler reports, and names `source_delta` in its
  advisories.

Never use the scoped review for a package with an authored change, and
never reuse an attestation for different bytes.

## Measurement

Record per rebind review its scope, wall time, input tokens and findings,
and the source-caused findings any later full review of the same package
raised. The registry's promotion rule judges these.
