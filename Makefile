# tuliprox-cf-edge — canonical commands.
#
# The suite lives in build/run_tests.py so CI and a local run execute the SAME checks and can
# never drift. See docs/OPERATIONS.md for day-to-day (browser-only) operations.

PY ?= python3

.PHONY: test test-live test-all help

help:
	@echo "make test       offline suite (no network, no credentials) — this is what gates CI"
	@echo "make test-live  offline suite + probe the deployed production edge"
	@echo "make test-all   alias for test-live"

test:
	$(PY) build/run_tests.py

test-live:
	$(PY) build/run_tests.py --live

test-all: test-live
