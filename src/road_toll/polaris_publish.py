"""Publish one gold table to Iceberg through Apache Polaris on MinIO.

Domain principals are separate. The crashes principal can see the crashes
namespace and is refused the population namespace. DuckDB then reads the
table Polaris committed.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request

import duckdb

from road_toll.paths import DB_PATH

BASE = os.environ.get("POLARIS_URL", "http://localhost:8181").rstrip("/")
REALM = os.environ.get("POLARIS_REALM", "POLARIS")
ROOT_ID = os.environ.get("POLARIS_CLIENT_ID", "root")
ROOT_SECRET = os.environ.get("POLARIS_CLIENT_SECRET", "s3cr3t")
CATALOG = "road_toll"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
S3_KEY = os.environ.get("S3_ACCESS_KEY", "minio_root")
S3_SECRET = os.environ.get("S3_SECRET_KEY", "m1n1opwd")
BUCKET = "road-toll"


def _json(method: str, path: str, token: str | None, payload: dict | None = None, form: dict | None = None):
    data = None
    headers = {"Accept": "application/json", "Polaris-Realm": REALM}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        import json

        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        status = error.code
    parsed = None
    if raw:
        import json

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
    return status, parsed


def _token(client_id: str, secret: str) -> str:
    status, body = _json(
        "POST",
        "/api/catalog/v1/oauth/tokens",
        None,
        form={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise RuntimeError(f"Token request failed ({status}): {body}")
    return body["access_token"]


def _wait() -> None:
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:8182/q/health", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Polaris health check on port 8182 did not succeed.")


def _ok(status: int, what: str, body) -> None:
    if status not in {200, 201, 204, 409}:
        raise RuntimeError(f"{what} failed ({status}): {body}")


def publish() -> None:
    _wait()
    root = _token(ROOT_ID, ROOT_SECRET)
    status, body = _json(
        "POST",
        "/api/management/v1/catalogs",
        root,
        {
            "catalog": {
                "name": CATALOG,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {"default-base-location": f"s3://{BUCKET}/gold"},
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": [f"s3://{BUCKET}"],
                    "endpoint": S3_ENDPOINT,
                    "endpointInternal": "http://minio:9000",
                    "pathStyleAccess": True,
                    "region": "us-west-2",
                },
            }
        },
    )
    _ok(status, "create catalog", body)

    principals = {}
    for name, namespaces in {
        "crashes_owner": ["crashes"],
        "population_owner": ["population"],
        "targets_owner": ["targets"],
    }.items():
        principals[name] = _domain_principal(root, name, namespaces)

    crashes = _token(principals["crashes_owner"]["clientId"], principals["crashes_owner"]["clientSecret"])
    allowed, _ = _json("GET", f"/api/catalog/v1/{CATALOG}/namespaces/crashes", crashes)
    refused, refused_body = _json("GET", f"/api/catalog/v1/{CATALOG}/namespaces/population", crashes)
    if allowed != 200 or refused not in {403, 401, 404}:
        raise RuntimeError(
            f"Domain separation failed. crashes={allowed}, population={refused} {refused_body}"
        )
    print(f"Crashes principal can read crashes ({allowed}) and is refused population ({refused}).")
    _write_and_read(principals["crashes_owner"])


def _domain_principal(root: str, name: str, namespaces: list[str]) -> dict:
    status, body = _json(
        "POST",
        "/api/management/v1/principals",
        root,
        {"principal": {"name": name, "properties": {}}},
    )
    _ok(status, f"principal {name}", body)
    if status == 409:
        raise RuntimeError(f"Principal {name} already exists. Start from a fresh Polaris volume.")
    credentials = body["credentials"]
    role = f"{name}_role"
    catalog_role = f"{name}_catalog"
    status, response = _json(
        "POST", "/api/management/v1/principal-roles", root, {"principalRole": {"name": role}}
    )
    _ok(status, "principal role", response)
    status, response = _json(
        "POST",
        f"/api/management/v1/catalogs/{CATALOG}/catalog-roles",
        root,
        {"catalogRole": {"name": catalog_role}},
    )
    _ok(status, "catalog role", response)
    status, response = _json(
        "PUT",
        f"/api/management/v1/principals/{name}/principal-roles",
        root,
        {"principalRole": {"name": role}},
    )
    _ok(status, "assign principal role", response)
    status, response = _json(
        "PUT",
        f"/api/management/v1/principal-roles/{role}/catalog-roles/{CATALOG}",
        root,
        {"catalogRole": {"name": catalog_role}},
    )
    _ok(status, "assign catalog role", response)
    for privilege in ("CATALOG_READ_PROPERTIES",):
        _grant(root, catalog_role, {"type": "catalog", "privilege": privilege})
    for namespace in namespaces:
        status, response = _json(
            "POST",
            f"/api/catalog/v1/{CATALOG}/namespaces",
            root,
            {"namespace": [namespace], "properties": {}},
        )
        _ok(status, f"namespace {namespace}", response)
        for privilege in (
            "NAMESPACE_READ_PROPERTIES",
            "TABLE_LIST",
            "TABLE_CREATE",
            "TABLE_READ_DATA",
            "TABLE_WRITE_DATA",
            "TABLE_DROP",
        ):
            _grant(
                root,
                catalog_role,
                {"type": "namespace", "namespace": [namespace], "privilege": privilege},
            )
    return credentials


def _grant(root: str, catalog_role: str, grant: dict) -> None:
    status, body = _json(
        "PUT",
        f"/api/management/v1/catalogs/{CATALOG}/catalog-roles/{catalog_role}/grants",
        root,
        grant,
    )
    _ok(status, f"grant {grant}", body)


def _write_and_read(credentials: dict) -> None:
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog(
        "polaris",
        **{
            "type": "rest",
            "uri": f"{BASE}/api/catalog",
            "warehouse": CATALOG,
            "credential": f"{credentials['clientId']}:{credentials['clientSecret']}",
            "scope": "PRINCIPAL_ROLE:ALL",
            "oauth2-server-uri": f"{BASE}/api/catalog/v1/oauth/tokens",
            "header.Polaris-Realm": REALM,
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_KEY,
            "s3.secret-access-key": S3_SECRET,
            "s3.path-style-access": "true",
            "s3.region": "us-west-2",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        },
    )
    import pyarrow as pa

    con = duckdb.connect(str(DB_PATH), read_only=True)
    frame = con.execute(
        """
        SELECT state, year, month, rolling_deaths, target_deaths, excess_deaths
        FROM state_month_path
        WHERE year = 2026 AND month = 9
        """
    ).fetchdf()
    con.close()
    arrow_in = pa.Table.from_pandas(frame)
    identifier = ("crashes", "september_gap")
    if catalog.table_exists(identifier):
        catalog.drop_table(identifier)
    table = catalog.create_table(identifier, schema=arrow_in.schema)
    table.append(arrow_in)
    arrow = table.scan().to_arrow()
    reader = duckdb.connect()
    reader.register("gap", arrow)
    count = reader.execute("SELECT count(*), sum(rolling_deaths) FROM gap WHERE state = 'AUS'").fetchone()
    if count[0] < 1 or int(count[1]) != 1334:
        raise RuntimeError(f"DuckDB read of the Polaris table did not match the gold toll: {count}")
    print(f"DuckDB read the Iceberg gold table: Australia September rolling deaths = {int(count[1])}.")


if __name__ == "__main__":
    publish()
