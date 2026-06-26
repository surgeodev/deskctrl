# deskctrl — Makefile for development and packaging

VERSION := $(shell python3 -c "from deskctrl import __version__; print(__version__)" 2>/dev/null || echo "0.2.1")
REPO := surgeodev/deskctrl

.PHONY: all install test clean deb release

all: deb

# ── Install in development mode ─────────────────────────────────────────
install:
	pip install -e ".[all]"

# ── Run tests ────────────────────────────────────────────────────────────
test:
	python3 -m pytest tests/ -v --tb=short 2>/dev/null || \
	python3 -c "
import sys
sys.path.insert(0, '.')
from deskctrl.server import DeskctrlServer
from deskctrl.client import DeskctrlClient, DISPLAY_NONE
import threading, time
s = DeskctrlServer(host='127.0.0.1', port=15833)
s.start()
c = DeskctrlClient(host='127.0.0.1', port=15833, display_mode=DISPLAY_NONE)
assert c.connect(), 'Client should connect'
assert c.state.screen_width > 0
c.disconnect()
s.stop()
print('Integration test PASSED')
"

# ── Build .deb package ──────────────────────────────────────────────────
deb:
	@echo "==> Building deskctrl v$(VERSION) .deb"
	bash scripts/pkg-build.sh

# ── Install from .deb ───────────────────────────────────────────────────
install-deb: deb
	sudo dpkg -i dist/deskctrl_$(VERSION)-1_amd64.deb
	sudo apt-get install -f -y

# ── Create GitHub release ────────────────────────────────────────────────
release: deb
	@echo "==> Creating GitHub release v$(VERSION)"
	gh release create "v$(VERSION)" \
		--title "deskctrl v$(VERSION)" \
		--notes "See CHANGELOG.md for details" \
		dist/deskctrl_$(VERSION)-1_amd64.deb \
		scripts/install.sh \
		scripts/install.ps1

# ── Clean build artifacts ────────────────────────────────────────────────
clean:
	rm -rf dist/ build/ *.egg-info __pycache__/
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete

# ── Create source tarball for curl-based install ────────────────────────
src-tarball:
	@echo "==> Creating source tarball"
	rm -f dist/deskctrl-src.tar.gz
	git archive --format=tar.gz \
		--prefix=deskctrl-$(VERSION)/ \
		-o dist/deskctrl-src.tar.gz \
		HEAD 2>/dev/null || \
	tar czf dist/deskctrl-src.tar.gz \
		--exclude='.git' \
		--exclude='__pycache__' \
		--exclude='*.pyc' \
		--exclude='_*_venv' \
		--exclude='venv' \
		--exclude='.env' \
		-C .. deskctrl

# ── Full release: tag + push + release ──────────────────────────────────
tag:
	@if git diff --quiet HEAD; then \
		echo "==> Tagging v$(VERSION)"; \
		git tag "v$(VERSION)"; \
		git push origin "v$(VERSION)"; \
	else \
		echo "Uncommitted changes. Commit first:"; \
		git status; \
		exit 1; \
	fi

# ── Help ─────────────────────────────────────────────────────────────────
help:
	@echo "deskctrl Makefile"
	@echo ""
	@echo "  make install      pip install in dev mode"
	@echo "  make test         run integration tests"
	@echo "  make deb          build .deb package"
	@echo "  make release      create GitHub release"
	@echo "  make tag          tag current version and push"
	@echo "  make src-tarball  create source archive"
	@echo "  make clean        remove build artifacts"
