# CI Bootstrap

This is the deferred Delivery activation contract. Project setup does not
materialize CI. After the Delivery contract is approved, when the repository
has no PR-triggered test workflow in its CI directory (`.github/workflows/` on
GitHub), offer to add the packaged `templates/ci-tests.yml` file.

The template always runs the tracked portable single-vault gate before the
project-specific jobs. Do not replace it with an installed plugin path; every
supported host and CI must execute the same `.pyz` bytes.

The Delivery activation materializer reads the approved Verification and,
when required, Environment Contracts. It substitutes the test command and
renders optional dependency-audit and environment-smoke job blocks in
`ci-tests.yml`; setup never reads command fields from config.
Project setup substitutes `{{project_local_ignores}}` in the packaged
gitignore template from the product's declared project-local roots. No token
is written literally to a consuming repository.

- Refuse materialization while any placeholder source is absent.
- Use `test_command` and its declared `test_workdir` from the approved
  Verification Contract.
- Use the contract's explicit dependency-audit disposition and command. The
  Solution decision determines technologies; CI does not infer audits from a
  global config value.
- Use the approved Environment Contract only for a Delivery that declares a
  live runtime check. Include `environment_smoke` only when its up-then-down
  probe passes now. Otherwise omit that job and leave the Delivery blocked on
  runtime verification rather than guessing a command.
- Route dependency advisories through the Deliver entry as a fix-atomic
  lockfile bump.
