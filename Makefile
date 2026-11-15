.PHONY: run test install help

help:
	@echo "make run      start both Emulation Engine (:4320) and Workbench Dashboard (:4300)"
	@echo "make test     run test suites across tools"

run:
	./run.sh

test:
	cd tools/emulation-engine && make test
	cd tools/workbench && make test

