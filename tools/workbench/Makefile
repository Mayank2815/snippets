.PHONY: run test

run:
	./run.sh

test:
	python3 -m py_compile server.py
	@python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4300/healthz', timeout=1)" 2>/dev/null && echo "Workbench live healthz: OK" || echo "Workbench syntax valid (server not running)."

