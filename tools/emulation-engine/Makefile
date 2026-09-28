# Core Emulation Engine — common tasks. `make` alone prints this list.
# macOS and Linux; on Windows use install.ps1 / run.ps1 directly (no make needed),
# and run the tests with  .\.venv\Scripts\python -m unittest discover -s engine/tests
.PHONY: help install run test bundle

# Prefer the venv interpreter once install.sh has created it; the tests use the
# fake backend and the simulator has no third-party imports, so the system
# python3 is fine before that.
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
DATE   := $(shell date +%Y%m%d)
# The folder this Makefile lives in, whatever it was renamed to; `bundle` zips it
# from the parent directory so the archive unpacks into a folder of the same name.
TOOL   := $(notdir $(CURDIR))
BUNDLE := emulation-engine-$(DATE).zip

help:
	@echo "make install   create .venv and install the input library (Quartz on macOS, pynput on Linux)"
	@echo "make run       start the engine and open the console (Ctrl-C stops it); Windows: .\\run.ps1"
	@echo "make test      run the engine tests on the fake backend and the offline cadence simulator (no input)"
	@echo "make bundle    zip this folder, minus .venv, as $(BUNDLE) for sharing"

install:
	./install.sh

# Windows has no run.sh: use run.ps1 from PowerShell instead.
run:
	./run.sh

test:
	$(PYTHON) -m unittest discover -s engine/tests -v
	$(PYTHON) engine/test_metrics.py

bundle:
	rm -f "$(BUNDLE)"
	cd .. && zip -r "$(TOOL)/$(BUNDLE)" "$(TOOL)" \
	  -x "$(TOOL)/.venv/*" "$(TOOL)/*.zip" '*/__pycache__/*' '*.pyc' '*/.DS_Store'
	@echo "Wrote $(BUNDLE) — send it to anyone without repo access; they unzip, then ./install.sh and ./run.sh (Windows: install.ps1 and run.ps1)"
