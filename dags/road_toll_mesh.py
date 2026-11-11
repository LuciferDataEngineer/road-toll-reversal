"""Airflow 3 asset-aware mesh.

Each domain publishes its table. The targets domain waits until both the death
table and the population table have been updated, which is the data-aware
schedule (a dag run is created when those assets are produced).
"""

from __future__ import annotations

from datetime import datetime

from airflow.sdk import Asset, dag, task

CRASHES = Asset(name="crashes.fatalities", uri="file:///work/data/gold/crashes")
POPULATION = Asset(name="population.erp", uri="file:///work/data/gold/population")
TARGETS = Asset(name="targets.path", uri="file:///work/data/gold/targets")


@dag(
    dag_id="crashes_domain",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["road-toll", "crashes"],
)
def crashes_domain():
    @task(outlets=[CRASHES])
    def publish_crashes() -> str:
        from road_toll.pipeline import build_crashes

        build_crashes()
        return "crashes"

    publish_crashes()


@dag(
    dag_id="population_domain",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["road-toll", "population"],
)
def population_domain():
    @task(outlets=[POPULATION])
    def publish_population() -> str:
        from road_toll.pipeline import build_population

        build_population()
        return "population"

    publish_population()


@dag(
    dag_id="targets_domain",
    schedule=[CRASHES, POPULATION],
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["road-toll", "targets"],
)
def targets_domain():
    @task(inlets=[CRASHES, POPULATION], outlets=[TARGETS])
    def publish_targets() -> str:
        from road_toll.pipeline import build_targets_and_serving

        facts = build_targets_and_serving()
        return str(facts["australia"]["deaths"])

    publish_targets()


crashes_domain()
population_domain()
targets_domain()
