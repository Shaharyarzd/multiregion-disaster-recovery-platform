.PHONY: install test lint typecheck local-demo terraform-fmt terraform-validate verify

install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	python3 -m ruff check .
	python3 -m ruff format --check .

typecheck:
	python3 -m mypy src

local-demo:
	./scripts/local-dr-drill.sh

terraform-fmt:
	terraform fmt -check -recursive terraform

terraform-validate:
	./scripts/terraform-validate.sh

verify: lint typecheck test terraform-fmt terraform-validate

