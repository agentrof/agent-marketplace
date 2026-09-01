.PHONY: validate release-validate counts counts-check dist-check test eval check scaffold release-check public-release-check

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
	$(PY) -m unittest discover -s tools/tests -p 'test_*.py' -v

eval:
	$(PY) -m unittest tools.tests.test_scenario_report tools.tests.test_runtime_scripts tools.tests.test_ba_compile -v
	@echo "eval: deterministic behavior assertions green"

check: validate release-validate counts-check dist-check test
	@echo "check: all gates green"

release-check: check
	@echo "release-check: deterministic gates green"

public-release-check: check
	@echo "public-release-check: stable channel gates green"

scaffold:
	@echo "usage:"
	@echo "  $(PY) tools/scaffold.py new-plugin --name <kebab>"
	@echo "  $(PY) tools/scaffold.py new-agent  --plugin <plugin> --name <role>"
	@echo "  $(PY) tools/scaffold.py new-skill  --plugin <plugin> --name <skill> --kind entry|hidden"
