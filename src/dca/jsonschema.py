"""A small, exact JSON Schema checker for the contract documents (tasks.md T044).

Standard library only, because the runtime is stdlib-only and pulling a validator in would add a
dependency to the one component that must stay auditable. It is NOT a general JSON Schema
implementation: it supports exactly the keywords the repository's own contracts use, and it raises
`UnsupportedKeyword` for anything else rather than ignoring it.

That last decision is the important one. A validator that silently skips a keyword it does not know
reports PASS on a document it never actually checked, which is worse than having no validator at
all - the contract would look enforced while the constraint that mattered went unread. So an
unknown keyword is a loud failure of the validator, not a quiet pass for the document.

Supported: $ref (local `#/$defs/...`), $defs, type, const, enum, pattern, minLength, maxLength,
minimum, maximum, required, properties, additionalProperties, items, contains, minItems, maxItems,
patternProperties, propertyNames, uniqueItems, allOf, anyOf, oneOf, not, if/then/else.
Annotation-only keywords ($schema, $id, title,
description, examples, default, deprecated, $comment) are ignored by design.
"""

import re

IGNORED = frozenset({
    "$schema", "$id", "title", "description", "examples", "default", "deprecated", "$comment",
    "$defs", "definitions",
})

SUPPORTED = frozenset({
    "$ref", "type", "const", "enum", "pattern", "minLength", "maxLength", "required",
    "properties", "additionalProperties", "items", "contains", "minItems", "maxItems",
    "minimum", "maximum", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
    "patternProperties", "propertyNames", "uniqueItems",
})

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


class UnsupportedKeyword(Exception):
    """The schema uses a keyword this checker does not implement. Never a document failure."""


def _is_type(value, name):
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = TYPES.get(name)
    if expected is None:
        raise UnsupportedKeyword(f"unknown type {name!r}")
    return isinstance(value, expected)


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise UnsupportedKeyword(f"only local refs are supported, got {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _errors(value, schema, root, path):
    """Yield every constraint `value` violates, each with the JSON path where it happened."""
    if schema is True or schema == {}:
        return
    if schema is False:
        yield f"{path}: nothing is valid here"
        return
    for keyword in schema:
        if keyword not in SUPPORTED and keyword not in IGNORED:
            raise UnsupportedKeyword(f"{path}: schema keyword {keyword!r} is not implemented")

    if "$ref" in schema:
        yield from _errors(value, _resolve(schema["$ref"], root), root, path)

    if "type" in schema:
        names = schema["type"]
        names = [names] if isinstance(names, str) else names
        if not any(_is_type(value, name) for name in names):
            yield f"{path}: expected type {'|'.join(names)}, got {type(value).__name__}"
            return

    if "const" in schema and value != schema["const"]:
        yield f"{path}: expected {schema['const']!r}, got {value!r}"
    if "enum" in schema and not any(value == option for option in schema["enum"]):
        yield f"{path}: {value!r} is not one of {schema['enum']!r}"
    if "pattern" in schema and isinstance(value, str):
        if re.search(schema["pattern"], value) is None:
            yield f"{path}: {value!r} does not match {schema['pattern']}"
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        yield f"{path}: shorter than {schema['minLength']}"
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        yield f"{path}: longer than {schema['maxLength']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            yield f"{path}: {value} is below the minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            yield f"{path}: {value} is above the maximum {schema['maximum']}"

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                yield f"{path}: missing required property {name!r}"
        properties = schema.get("properties", {})
        for name, subschema in properties.items():
            if name in value:
                yield from _errors(value[name], subschema, root, f"{path}.{name}")
        pattern_properties = schema.get("patternProperties", {})
        for expression, subschema in pattern_properties.items():
            for name, item in value.items():
                if re.search(expression, name):
                    yield from _errors(item, subschema, root, f"{path}.{name}")
        if "propertyNames" in schema:
            for name in value:
                for problem in _errors(name, schema["propertyNames"], root, f"{path}.{name}"):
                    yield f"{problem} (property name)"
        if schema.get("additionalProperties") is False:
            for name in value:
                if name in properties:
                    continue
                if any(re.search(expression, name) for expression in pattern_properties):
                    continue
                yield f"{path}: property {name!r} is not allowed"
        elif isinstance(schema.get("additionalProperties"), dict):
            for name, item in value.items():
                if name in properties:
                    continue
                if any(re.search(expression, name) for expression in pattern_properties):
                    continue
                yield from _errors(item, schema["additionalProperties"], root, f"{path}.{name}")

    if isinstance(value, list):
        if "items" in schema:
            for index, item in enumerate(value):
                yield from _errors(item, schema["items"], root, f"{path}[{index}]")
        if "minItems" in schema and len(value) < schema["minItems"]:
            yield f"{path}: needs at least {schema['minItems']} items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            yield f"{path}: allows at most {schema['maxItems']} items"
        if schema.get("uniqueItems") is True:
            seen = []
            for item in value:
                if item in seen:
                    yield f"{path}: items must be unique, {item!r} repeats"
                    break
                seen.append(item)
        if "contains" in schema:
            if not any(not list(_errors(item, schema["contains"], root, path)) for item in value):
                yield f"{path}: no item satisfies the required 'contains' constraint"

    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        results = [list(_errors(value, sub, root, path)) for sub in schema[keyword]]
        passing = sum(1 for problems in results if not problems)
        if keyword == "allOf":
            for problems in results:
                yield from problems
        elif keyword == "anyOf" and passing == 0:
            yield f"{path}: matches none of the anyOf branches"
        elif keyword == "oneOf" and passing != 1:
            yield f"{path}: matches {passing} oneOf branches, expected exactly 1"

    if "not" in schema and not list(_errors(value, schema["not"], root, path)):
        yield f"{path}: must NOT match the 'not' schema"

    if "if" in schema:
        matched = not list(_errors(value, schema["if"], root, path))
        branch = schema.get("then") if matched else schema.get("else")
        if branch is not None:
            yield from _errors(value, branch, root, path)


def validate(document, schema):
    """Every violation, as readable strings. An empty list means the document conforms."""
    return list(_errors(document, schema, schema, "$"))


def check(document, schema):
    """Raise ValueError naming every violation, or return the document unchanged."""
    problems = validate(document, schema)
    if problems:
        raise ValueError("document does not conform:\n  " + "\n  ".join(problems))
    return document
