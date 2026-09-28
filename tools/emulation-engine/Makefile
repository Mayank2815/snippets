# Core Emulation Engine — common tasks. `make` alone prints this list.
.PHONY: help install run test bundle

# Prefer the venv interpreter once install.sh has created it; the simulator has no
# third-party imports, so the system python3 is fine before that.
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
DATE   := $(shell date +%Y%m%d)
BUNDLE := emulation-engine-$(DATE).zip

help:
	@echo "make install   create .venv and install the Quartz bindings (macOS only)"
	@echo "make run       start the engine and open the console (Ctrl-C stops it)"
	@echo "make test      run the offline cadence simulator (generates no input)"
	@echo "make bundle    zip this folder, minus .venv, as $(BUNDLE) for sharing"

install:
	./install.sh

run:
	./run.sh

test:
	$(PYTHON) engine/test_metrics.py

bundle:
	rm -f "$(BUNDLE)"
	cd .. && zip -r "emulation-engine/$(BUNDLE)" emulation-engine \
	  -x 'emulation-engine/.venv/*' 'emulation-engine/*.zip' '*/__pycache__/*' '*.pyc' '*/.DS_Store'
	@echo "Wrote $(BUNDLE) — send it to anyone without repo access; they unzip, then ./install.sh and ./run.sh"
