.PHONY: install install-frontend test test-frontend run-collector run-agent

install:
	python -m pip install -r requirements-dev.txt

install-frontend:
	npm install

test: test-backend test-frontend

test-backend:
	python -m compileall -q collector agent tests
	python -m pytest -q

test-frontend:
	node --check ui/app.js
	node --test ui/tests/*.test.js

run-collector:
	uvicorn collector.main:app --host 0.0.0.0 --port 8000 --reload

run-agent:
	python -m agent.main
