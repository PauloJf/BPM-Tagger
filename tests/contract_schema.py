"""A small JSON-Schema subset checker for the contract files (type, required, properties,
additionalProperties: false, enum, items, minimum, maxLength, and $ref by definition name).
Enough for flat API shapes, with no dependency. The same file lives in both companion repos."""

import hashlib
import json
from pathlib import Path

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path) -> str:
    """Hash of the parsed contract (so whitespace does not count)."""
    return hashlib.sha256(json.dumps(load(path), sort_keys=True).encode()).hexdigest()


def _is(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def errors(value, schema: dict, contract: dict, where: str = "$") -> list[str]:
    """Every way ``value`` breaks ``schema``; empty when it conforms."""
    if "$ref" in schema:
        schema = contract["definitions"][schema["$ref"]]
    out = []
    types = schema.get("type")
    if types:
        names = types if isinstance(types, list) else [types]
        if not any(_is(value, n) for n in names):
            return [f"{where}: expected {'|'.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: {value!r} not one of {schema['enum']}")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{where}: longer than {schema['maxLength']}")
    if _is(value, "number") and "minimum" in schema and value < schema["minimum"]:
        out.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        out += [f"{where}: missing {k}" for k in schema.get("required") or [] if k not in value]
        if schema.get("additionalProperties") is False:
            out += [f"{where}: unexpected {k}" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                out += errors(value[k], sub, contract, f"{where}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += errors(item, schema["items"], contract, f"{where}[{i}]")
    return out


def check(value, name: str, contract: dict) -> None:
    problems = errors(value, {"$ref": name}, contract)
    assert not problems, f"{name}: {problems}"
