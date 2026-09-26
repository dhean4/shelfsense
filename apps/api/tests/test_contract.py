"""The hand-written contract (openapi.yaml) must be honoured by the generated document.

The implementation may add things (FastAPI's automatic 422s, extra properties); it may
not drop an operation, a status code, a schema, a required field or a property that the
contract promises.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from shelfsense_api.main import create_app

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(CONTRACT_PATH.read_text())
    return loaded


@pytest.fixture(scope="module")
def generated() -> dict[str, Any]:
    return create_app().openapi()


def _operations(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): op
        for path, item in document["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    }


def test_every_contract_operation_is_implemented(
    contract: dict[str, Any], generated: dict[str, Any]
) -> None:
    missing = set(_operations(contract)) - set(_operations(generated))
    assert not missing, f"operations in contract but not implemented: {sorted(missing)}"


def test_every_contract_status_code_is_declared(
    contract: dict[str, Any], generated: dict[str, Any]
) -> None:
    implemented = _operations(generated)
    problems: list[str] = []
    for key, op in _operations(contract).items():
        promised = set(op["responses"])
        declared = set(implemented[key]["responses"])
        for code in sorted(promised - declared):
            problems.append(f"{key[1].upper()} {key[0]} does not declare {code}")
    assert not problems, "\n".join(problems)


def test_every_contract_schema_exists_with_its_fields(
    contract: dict[str, Any], generated: dict[str, Any]
) -> None:
    promised_schemas = contract["components"]["schemas"]
    actual_schemas = generated["components"]["schemas"]
    problems: list[str] = []
    for name, promised in promised_schemas.items():
        actual = actual_schemas.get(name)
        if actual is None:
            problems.append(f"schema {name} missing")
            continue
        promised_props = set(promised.get("properties", {}))
        actual_props = set(actual.get("properties", {}))
        if promised_props - actual_props:
            problems.append(
                f"schema {name} lacks properties {sorted(promised_props - actual_props)}"
            )
        promised_required = set(promised.get("required", []))
        actual_required = set(actual.get("required", []))
        if promised_required - actual_required:
            problems.append(
                f"schema {name} no longer requires {sorted(promised_required - actual_required)}"
            )
    assert not problems, "\n".join(problems)


def test_contract_tags_match(contract: dict[str, Any], generated: dict[str, Any]) -> None:
    promised = {tag["name"] for tag in contract["tags"]}
    actual = {tag["name"] for tag in generated["tags"]}
    assert promised <= actual
