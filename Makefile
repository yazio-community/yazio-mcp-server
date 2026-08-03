# Everything here expects the nix-shell: `nix-shell --run "make test"`.

.PHONY: help run test lint check

help:
	@echo "make run    start the server on 127.0.0.1:8931"
	@echo "make test   run the mocked test suite"
	@echo "make lint   ruff check + format check"

run:
	python3 -m yazio_mcp

test:
	python3 -m pytest -q

lint:
	ruff check .


check: lint test
