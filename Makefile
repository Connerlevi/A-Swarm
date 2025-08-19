PY=python
PIP=pip

.PHONY: setup test clean compile

setup:
	$(PY) -m venv .venv && . .venv/bin/activate && $(PIP) install -r requirements.txt

test:
	. .venv/bin/activate && pytest -q

compile:
	. .venv/bin/activate && $(PY) -m policy_compiler.compiler compile

clean:
	rm -rf .venv dist build __pycache__ */__pycache__
