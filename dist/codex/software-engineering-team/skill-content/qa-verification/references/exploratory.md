# Exploratory Pass

[conditional] Read only when a human explicitly requests an exploratory pass, outside the deterministic gate. Non-gate clause: nothing found in exploration ever touches the verdict; the deterministic gates (coverage matrix, suite, runtime protocol) stand unchanged. Every observation routes to the analyst as a candidate business rule and enters the deterministic chain from there: rule id first, then test, then audit.

## Session Contract

- One charter per session, written before exploring: "Explore <target area> using <resource or lens> to discover <class of information>."
- Time-box the session; when the box ends, the session ends. Finding quality beats coverage claims.
- Keep session notes as you go: path taken, observations, open questions. The notes are the deliverable; an undocumented session did not happen.
- DON'T mix exploration and gate execution in one session; the gate's records must stay deterministic and reproducible.

## Charter Repertoire

Pick the charter by what the human wants probed:

| Concern raised | Charter target |
|---|---|
| New feature feels underspecified | The feature's implicit assumptions: behavior the brief does not state |
| Cross-feature interference | Data and state shared between the increment and adjacent features |
| Input robustness | Fields and uploads under hostile, huge, or oddly encoded input |
| Workflow realism | One realistic end-to-end task attempted the way a first-time user would |

## Tour Repertoire

Structured walks over the running application, each a different lens:

- Data-entry tour: visit every input; try boundary, duplicate, and mutually contradictory values across screens.
- Interruption tour: cancel, refresh, back-navigate, double-submit, and let the session expire mid-flow; watch what state survives.
- Configuration tour: change every setting the increment reads, then re-walk the affected surfaces.
- Error tour: force each failure the UI claims to handle and judge the message against what actually happened.
- Landmark tour: walk the main surfaces in the sequence a demo would use; note anything that needs a verbal explanation to make sense.

## Routing Findings

- Every observation becomes a candidate business rule for the analyst: observed behavior, expected behavior in the explorer's judgment, and why the gap matters. The analyst decides whether it becomes a BR-### or is rejected.
- DON'T file exploration observations as defects, don't attach severity, and don't add them to the verification record's findings; they have no gate standing until an analyst-issued rule id exists.
- Once the rule id exists, coverage follows the normal chain: the developer writes the tagged test, the audit maps it, the gate enforces it.
