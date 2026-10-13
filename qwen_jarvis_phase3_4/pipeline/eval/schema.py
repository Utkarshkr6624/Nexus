"""Minimal JSON-Schema subset validator for tool arguments (no third-party dependency).
Supported: type (object/array/string/integer/number/boolean/null, or a list of types), properties, required, additionalProperties (bool),
enum, minimum, maximum, minLength, maxLength, pattern, items, minItems, maxItems.  Anything else in a schema is ignored (documented)."""
import re

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(value, schema, path="$"):
    """Returns a list of human-readable error strings (empty list = valid)."""
    errs = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda v: False)(value) for x in ts):
            return [f"{path}: expected {'/'.join(ts)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value:
                errs.append(f"{path}: missing required property {r!r}")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties", True) is False:
                errs.append(f"{path}: unexpected property {k!r}")
    return errs


def validate_tool_call(call: dict, tools: list):
    """Validate one parsed call {'name','arguments'} against a list of tool definitions
    ([{'type':'function','function':{'name','description','parameters':{...}}}]). Returns a list of errors."""
    by_name = {t["function"]["name"]: t["function"] for t in tools}
    name = call.get("name")
    if name not in by_name:
        return [f"unknown tool {name!r}; available: {sorted(by_name)}"]
    params = dict(by_name[name].get("parameters") or {"type": "object"})
    params.setdefault("type", "object")
    params.setdefault("additionalProperties", False)    # strict by default: invented argument names are errors
    return validate(call.get("arguments"), params, "arguments")
