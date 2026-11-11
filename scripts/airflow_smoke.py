"""Confirm the three domain DAGs import, and that targets waits on the other two."""

from __future__ import annotations

from airflow.models.dagbag import DagBag

bag = DagBag(dag_folder="/work/dags", include_examples=False)
if bag.import_errors:
    raise SystemExit(bag.import_errors)

expected = {"crashes_domain", "population_domain", "targets_domain"}
missing = expected - set(bag.dags)
if missing:
    raise SystemExit(f"Missing DAGs: {missing}")

schedule = bag.dags["targets_domain"].schedule
text = repr(schedule)
print("targets schedule:", type(schedule).__name__, text)
if "crashes.fatalities" not in text or "population.erp" not in text:
    raise SystemExit("The targets DAG is not scheduled on the crashes and population assets.")
print("Asset schedule OK")
