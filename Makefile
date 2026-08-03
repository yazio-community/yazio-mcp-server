# Everything here expects the nix-shell: `nix-shell --run "make test"`.

.PHONY: help run test lint smoke smoke-write check

help:
	@echo "make run    start the server on 127.0.0.1:8931"
	@echo "make test   run the mocked test suite"
	@echo "make lint   ruff check + format check"
	@echo "make smoke  drive the whole stack against a live account (read-only)"

run:
	python3 -m yazio_mcp

test:
	python3 -m pytest -q

lint:
	ruff check .


check: lint test

# Needs YAZIO_USERNAME and YAZIO_PASSWORD in the environment or in .env. This is
# the only check that covers Basic auth, the token exchange, the SDK and the
# response shaping at once — the pytest suite mocks all of it.
smoke:
	python3 scripts/smoke_live.py

smoke-write:
	python3 scripts/smoke_live.py --write
