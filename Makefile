PY ?= python
export PYTHONPATH := src

.PHONY: all pipeline model dashboard test contracts bench iceberg

all: pipeline model dashboard

pipeline:
	$(PY) -m road_toll.cli pipeline

model:
	$(PY) -m road_toll.cli model

dashboard:
	$(PY) -m road_toll.cli dashboard

contracts:
	$(PY) -m road_toll.cli contracts

test:
	$(PY) -m ruff check src tests dags scripts
	$(PY) -m pytest

bench:
	$(PY) -m road_toll.cli benchmark

iceberg:
	$(PY) -m road_toll.polaris_publish
