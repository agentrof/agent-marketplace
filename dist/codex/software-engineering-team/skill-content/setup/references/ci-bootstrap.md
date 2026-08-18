# CI Bootstrap

This is the deferred Delivery activation contract. Project setup does not
materialize CI. After the Delivery contract is approved, when the repository
has no PR-triggered test workflow in its CI directory (`.github/workflows/` on
GitHub), offer to add the packaged `templates/ci-tests.yml` file.

The template always runs the tracked portable single-vault gate before the
project-specific jobs. Do not replace it with an installed plugin path; every
supported host and CI must execute the same `.pyz` bytes.

The deferred materializer substitutes `{{test_command}}`,
`{{audit_command}}` and `{{env_command}}` in `ci-tests.yml`. Project setup
substitutes `{{project_local_ignores}}` in the packaged gitignore template
from the product's declared project-local roots. No token is written
literally to a consuming repository.

- Refuse materialization while any placeholder source is absent.
- Use the configured test command for the test placeholder.
- Build one dependency-audit command per configured stack, anchored at its
  lockfile. Python FastAPI uses `pip-audit`; React TypeScript runs `npm audit
  --audit-level=high` in the frontend directory. Chain two stacks with `&&`.
- Use the configured environment command. Include `environment_smoke` only
  when an up-then-down probe passes now. Otherwise omit the job, note that a
  later setup run will append it, and treat its later absence as a gap once
  the probe passes.
- Keep dependency audit. Route advisories through the Deliver entry as a
  fix-atomic lockfile bump.
