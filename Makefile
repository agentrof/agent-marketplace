.PHONY: validate counts counts-check test check scaffold

PY := python3

validate:
	$(PY) tools/validate.py

counts:
	$(PY) tools/counts.py

counts-check:
	$(PY) tools/counts.py --check

test:
	$(PY) -m unittest discover -s tools/tests -p 'test_*.py' -v

check: validate counts-check test
	@echo "check: all gates green"

scaffold:
	@echo "usage:"
	@echo "  $(PY) tools/scaffold.py new-plugin --name <kebab>"
	@echo "  $(PY) tools/scaffold.py new-agent  --plugin <plugin> --name <role>"
	@echo "  $(PY) tools/scaffold.py new-skill  --plugin <plugin> --name <skill> --kind entry|hidden"
