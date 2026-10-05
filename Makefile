.PHONY: validate release-validate counts counts-check dist-check test eval static-check check check-local verify-local check-pr scaffold release-check public-release-check public-release-smoke

PY := python3

validate:
	$(PY) tools/validate.py

release-validate:
	$(PY) tools/release.py validate

counts:
	$(PY) tools/counts.py

counts-check:
	$(PY) tools/counts.py --check

dist-check:
	$(PY) tools/build_distributions.py --check

test:
	PYTHONDONTWRITEBYTECODE=1 $(PY) -m unittest discover -s tools/tests -p 'test_*.py' -v

eval:
	PYTHONDONTWRITEBYTECODE=1 $(PY) -m unittest tools.tests.test_scenario_report tools.tests.test_runtime_scripts tools.tests.test_ba_compile -v
	@echo "eval: deterministic behavior assertions green"

static-check: validate release-validate counts-check dist-check

check-local:
	$(PY) tools/ci_local.py check --staged --target origin/main

verify-local:
	$(PY) tools/ci_local.py verify --staged --target origin/main

check-pr:
	$(PY) tools/release.py check-pr --base origin/main

check: static-check test
	@echo "check: all gates green"

release-check: check
	@echo "release-check: deterministic gates green"

public-release-check: release-check public-release-smoke

public-release-smoke:
	@test -n "$(EXPECTED_RELEASE_SHA)" || (echo "EXPECTED_RELEASE_SHA is required" >&2; exit 1)
	$(PY) tools/smoke_plugin_installs.py --channel public --expected-sha "$(EXPECTED_RELEASE_SHA)"
	@echo "public-release-check: stable channel gates green"

scaffold:
	@echo "usage:"
	@echo "  $(PY) tools/scaffold.py new-plugin --name <kebab>"
	@echo "  $(PY) tools/scaffold.py new-agent  --plugin <plugin> --name <role>"
	@echo "  $(PY) tools/scaffold.py new-skill  --plugin <plugin> --name <skill> --kind entry|hidden"
