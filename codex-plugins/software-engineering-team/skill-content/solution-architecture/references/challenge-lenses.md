# Challenge Lenses

The adversarial round for solution engagements. Each lens is one fresh-context, read-only spawn of the challenger agent; the spawn prompt names the lens, its questions and the files. Challengers get FILES ONLY (the engagement doc, the landscape, the decision log, cited analysis docs), never conversation history.

## Spawn Shape

Per lens, one spawn of software-engineering-team-analysis-challenger:

1. Identity: "You are software-engineering-team-analysis-challenger, challenging solution engagement <slug> through the <lens> lens."
2. The constitution body, read from the file printed by
   "$RUN" path "$TEAM" constitution.md (dispatcher per the develop
   flow's state contract)
   and pasted verbatim: {{constitution}}
3. The lens block below, pasted verbatim.
4. Inputs: read-fully the engagement doc and the decision notes it minted (decisions/); summary-only landscape.md and cited analysis docs.
5. Output: a findings table (finding, evidence, severity blocking/minor), nothing else; read-only by constitution and by capability.

Named practitioner questions (a real-world operating question the matrix cannot settle) go instead to software-engineering-team-domain-expert with an explicit expert profile and the specific questions, assembled with the same constitution paste; write the profile per the challenge-review skill's expert-casting reference. Its answers return as proposals to confirm, never facts.

## The Four Lenses

**technology-fit-and-traceability.** Does every verdict trace to a cited requirement or budget? Are there requirements the chosen option demonstrably misses? Are ASSUMED/UNVERIFIED markers resolved or carried as named risks? Does every verified claim name its in-session source, and would that source actually support the cell? Is any matrix cell a bare preference dressed as a judgment?

**sustainability-and-operability.** Who operates each chosen component and is that written? What is the year-two story: upgrades, failure modes, observability, on-call load? Does the team-capability cell reflect the configured stacks or wishful thinking? Is any component quietly assumed to run itself?

**cost-and-lock-in.** Is the cost judged at the stated scale and over the run-rate trajectory, not the free tier? Is the exit path concrete (data out, consumers ported, contract ended) or a slogan? Which verdict would flip if the vendor doubled the price, and is that risk named?

**security-and-compliance.** Which trust boundaries does each option move and does the record say so? Where does data rest and transit, under whose obligations? Do integration constructs create implicit trust between components the analysis never granted? Is anything security-relevant deferred without an owner ruling?

## Disposition Discipline

Every finding is triaged in conversation by the persona, the single writer: fix (the doc or record updates, named), reject (one-line reason in the round record), defer (carried to the gate by name). A blocking finding left unfixed forces another round; the cap is 3 rounds, and residue at the cap goes to the owner at the gate, never silently dropped.
