.PHONY: install test run-collector run-agent

install:
	python -m pip install -r requirements-dev.txt

test:
	python -m compileall -q collector agent tests
	python -m pytest -q

run-collector:
	uvicorn collector.main:app --host 0.0.0.0 --port 8000 --reload

run-agent:
	python -m agent.main
