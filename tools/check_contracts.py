"""Read-only verification of local schemas and synthetic fixtures. No network."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    schemas = {}
    for path in sorted((ROOT / "contracts/schemas").glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        expected_id = f"https://eva.local/schemas/{path.name}"
        if schema["$id"] != expected_id or expected_id in schemas:
            raise ValueError(f"Invalid or duplicate schema id: {path.name}")
        schemas[expected_id] = schema
    registry = Registry().with_resources(
        (uri, Resource.from_contents(schema)) for uri, schema in schemas.items()
    )
    count = 0
    for path in sorted((ROOT / "contracts/examples").glob("*.json")):
        uri = f"https://eva.local/schemas/{path.stem}.schema.json"
        Draft202012Validator(schemas[uri], registry=registry).validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        count += 1
    if count != len(schemas) or not count:
        raise ValueError("Every schema must have a corresponding fixture")
    print(f"OK: {len(schemas)} schemas, {count} synthetic fixtures; no remote retrieval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
