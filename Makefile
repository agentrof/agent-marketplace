.PHONY: validate release-validate counts counts-check dist-check test eval check scaffold host-check-opencode release-check public-release-check

PY := python3
OPENCODE_BIN ?= $(shell command -v opencode 2>/dev/null)
OPENCODE_REAL_PROBE ?= $(CURDIR)/platforms/opencode/real_host_probe.py
OPENCODE_TUI ?= 0
OPENCODE_TUI_ARG := $(if $(filter 1 true yes,$(OPENCODE_TUI)),--tui,)

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

host-check-opencode:
	@test -n "$(OPENCODE_BIN)" || { echo "host-check-opencode: OPENCODE_BIN is required" >&2; exit 4; }
	@test -f "$(OPENCODE_REAL_PROBE)" || { echo "host-check-opencode: OPENCODE_REAL_PROBE is required" >&2; exit 4; }
	$(PY) platforms/opencode/host_check.py --opencode "$(OPENCODE_BIN)" --probe "$(OPENCODE_REAL_PROBE)" $(OPENCODE_TUI_ARG)

release-check: check host-check-opencode
	@echo "release-check: deterministic and real-host gates green"

public-release-check: check
	@echo "public-release-check: stable channel gates green"

scaffold:
	@echo "usage:"
	@echo "  $(PY) tools/scaffold.py new-plugin --name <kebab>"
	@echo "  $(PY) tools/scaffold.py new-agent  --plugin <plugin> --name <role>"
	@echo "  $(PY) tools/scaffold.py new-skill  --plugin <plugin> --name <skill> --kind entry|hidden"
