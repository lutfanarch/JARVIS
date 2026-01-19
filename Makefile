# Default name of the handoff archive for Ticket 16.0.  This ensures
# that `make handoff` creates a deterministic ZIP with the current
# ticket identifier.  Update this value whenever a new ticket is
# completed so that the generated artifact reflects the correct version.
HANDOFF_ZIP ?= jarvis_repo_ticket18_25.zip

.PHONY: help init-db tests lint format clean handoff handoff-verify

help:
	@echo "Available targets: init-db, tests, lint, format, clean"

# Run database migrations
init-db:
	@alembic upgrade head

# Run the test suite
tests:
	@PYTHONPATH=src pytest -q

# Lint the codebase
lint:
	@flake8 src tests

# Format the codebase
format:
	@black src tests

# Remove build artifacts and caches
clean:
	@echo "Cleaning repository..."
	@find . -name '__pycache__' -type d -exec rm -rf {} + || true
	@find . -name '*.pyc' -exec rm -f {} + || true
	@rm -rf .pytest_cache || true
	@find . -maxdepth 2 -name '*.egg-info' -type d -exec rm -rf {} + || true
	@rm -rf build dist || true

# Produce a deterministic handoff ZIP archive containing only tracked
# files under the jarvis/ prefix.  This target first runs clean to
# remove caches and compiled artifacts, then uses git archive to
# create the zip.  The resulting file is named with the current
# ticket version (update as needed).
handoff: clean
	@echo "Creating handoff ZIP $(HANDOFF_ZIP)..."
	@# Exclude any pre‑existing zip artifacts from the archive to avoid nested ZIPs
	@git archive --format=zip --prefix=jarvis/ \
		--exclude=*.zip \
		-o $(HANDOFF_ZIP) HEAD
	@echo "Handoff ZIP created: $(HANDOFF_ZIP)"
	# Run verification to ensure the archive contains the correct prefix and no caches
	@$(MAKE) handoff-verify

# Verify that the generated handoff ZIP adheres to the required structure.
# This target checks that every path in the ZIP starts with the jarvis/ prefix
# and that no cached bytecode or egg-info directories are present.  It fails
# loudly if either condition is violated.
handoff-verify:
	@echo "Verifying handoff ZIP $(HANDOFF_ZIP)..."
	@if unzip -Z1 $(HANDOFF_ZIP) | grep -qv '^jarvis/'; then \
	    echo "ERROR: missing jarvis/ prefix in handoff ZIP"; \
	    exit 1; \
	fi
	@if unzip -Z1 $(HANDOFF_ZIP) | egrep -q '(__pycache__|\\.pyc$$|\\.pytest_cache|\\.egg-info)'; then \
	    echo "ERROR: cache or build artifacts found in handoff ZIP"; \
	    exit 1; \
	fi
	# Fail if any artifacts directory is present
	@if unzip -Z1 $(HANDOFF_ZIP) | grep -q '^jarvis/artifacts/'; then \
	    echo "ERROR: artifacts directory found in handoff ZIP"; \
	    exit 1; \
	fi
	# Fail if any environment files (.env or .env.*) are present
	@if unzip -Z1 $(HANDOFF_ZIP) | egrep -q '^jarvis/.*\\.env(\\.|$$)'; then \
	    echo "ERROR: environment file found in handoff ZIP"; \
	    exit 1; \
	fi
	# Fail if any database files (.db) are present
	@if unzip -Z1 $(HANDOFF_ZIP) | egrep -q '\\.db$$'; then \
	    echo "ERROR: database file found in handoff ZIP"; \
	    exit 1; \
	fi
	# Fail if any .venv directory is present
	@if unzip -Z1 $(HANDOFF_ZIP) | grep -q '^jarvis/.venv/'; then \
	    echo "ERROR: .venv directory found in handoff ZIP"; \
	    exit 1; \
	fi
	# Ensure no nested ZIP artifacts remain inside the handoff.  Any .zip entry
	# indicates a potentially committed artifact (e.g., prior handoff archives)
	@if unzip -Z1 $(HANDOFF_ZIP) | egrep -q '\\.zip$$'; then \
	    echo "ERROR: nested zip artifact in handoff"; \
	    exit 1; \
	fi
	@echo "Handoff ZIP verification passed"
