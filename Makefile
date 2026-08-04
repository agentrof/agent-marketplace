.PHONY: validate release-validate counts counts-check dist-check test eval check scaffold smoke-plugin-installs smoke-plugin-installs-public integration-hosts integration-hosts-public release-check public-release-check

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
	$(PY) -m unittest tools.tests.test_scenario_report tools.tests.test_runtime_scripts tools.tests.test_pmo_cli tools.tests.test_pmo_hooks tools.tests.test_pmo_dashboard tools.tests.test_ba_compile -v
	@echo "eval: deterministic behavior assertions green"

check: validate release-validate counts-check dist-check test
	@echo "check: all gates green"

smoke-plugin-installs:
	$(PY) tools/smoke_plugin_installs.py --channel checkout

smoke-plugin-installs-public:
	$(PY) tools/smoke_plugin_installs.py --channel public

integration-hosts: smoke-plugin-installs

integration-hosts-public: smoke-plugin-installs-public

release-check: check integration-hosts
	@echo "release-check: deterministic and real-host gates green"

public-release-check: check integration-hosts-public
	@echo "public-release-check: stable channel gates green"

scaffold:
	@echo "usage:"
	@echo "  $(PY) tools/scaffold.py new-plugin --name <kebab>"
	@echo "  $(PY) tools/scaffold.py new-agent  --plugin <plugin> --name <role>"
	@echo "  $(PY) tools/scaffold.py new-skill  --plugin <plugin> --name <skill> --kind entry|hidden"
