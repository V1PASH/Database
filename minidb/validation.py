"""Schema validation.

Every collection can carry a validator, modelled on MongoDB's ``$jsonSchema``
but a little friendlier: it also fills in ``default`` values for missing
fields when a document is inserted.

Supported keywords::

    type, bsonType          the type(s) a value must have
    required                fields that must be present
    properties              per-field rules
    additionalProperties    allow or forbid extra fields
    enum                   the value must be one of these
    minimum, maximum        numeric bounds (with exclusive variants)
    minLength, maxLength    string length
    pattern                 regular expression a string must match
    minItems, maxItems      array length
    uniqueItems             array members must be unique
    items                   rules for array members
    default                 value used when the field is missing
    format                  "email", "uri", "date", "date-time", "uuid"
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from .comparison import is_array, is_document, is_number
from .errors import ValidationError

__all__ = ["Validator", "validate_document", "apply_defaults"]

_TYPE_CHECKS = {
    "object": lambda value: is_document(value),
    "array": lambda value: is_array(value),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "bool": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
    "number": lambda value: is_number(value),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "long": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "double": lambda value: isinstance(value, float),
    "decimal": lambda value: is_number(value),
    "date": lambda value: isinstance(value, (_dt.date, _dt.datetime)),
    "objectId": lambda value: value is not None and type(value).__name__ == "ObjectId",
    "binData": lambda value: isinstance(value, (bytes, bytearray)),
}

_FORMATS = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "uri": re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S+$"),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "date-time": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if is_array(value):
        return "array"
    if is_document(value):
        return "object"
    return type(value).__name__


class Validator:
    """A reusable document schema."""

    def __init__(self, schema: Any) -> None:
        if schema is None:
            raise ValidationError("a validator must be a document")
        if not is_document(schema):
            raise ValidationError("a validator must be a document")
        self.schema = schema

    # -- validation ---------------------------------------------------

    def validate(self, document: Any) -> list[str]:
        """Return a list of human readable problems (empty when valid)."""
        errors: list[str] = []
        self._check(document, self.schema, "", errors)
        return errors

    def _check(self, value: Any, schema: Any, path: str, errors: list[str]) -> None:
        if not is_document(schema):
            return

        where = path or "<document>"

        expected = schema.get("type", schema.get("bsonType"))
        if expected is not None:
            types = expected if isinstance(expected, list) else [expected]
            if not any(_TYPE_CHECKS.get(name, lambda _v: False)(value) for name in types):
                errors.append(
                    f"{where} should be of type {'/'.join(types)}, got {_type_name(value)}"
                )
                return

        if "enum" in schema:
            allowed = schema["enum"]
            if not any(_same(value, option) for option in allowed):
                errors.append(f"{where} must be one of {allowed!r}")

        if is_number(value):
            self._check_number(value, schema, where, errors)
        if isinstance(value, str):
            self._check_string(value, schema, where, errors)
        if is_array(value):
            self._check_array(value, schema, where, errors)
        if is_document(value):
            self._check_object(value, schema, where, errors)

    def _check_number(self, value: Any, schema: dict, where: str, errors: list[str]) -> None:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where} must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{where} must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{where} must be < {schema['exclusiveMaximum']}")
        if "multipleOf" in schema and schema["multipleOf"]:
            remainder = value % schema["multipleOf"]
            if abs(remainder) > 1e-9:
                errors.append(f"{where} must be a multiple of {schema['multipleOf']}")

    def _check_string(self, value: str, schema: dict, where: str, errors: list[str]) -> None:
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where} must be at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where} must be at most {schema['maxLength']} characters")
        if "pattern" in schema:
            try:
                pattern = re.compile(schema["pattern"])
            except re.error as exc:
                raise ValidationError(f"invalid pattern in schema: {exc}") from exc
            if not pattern.search(value):
                errors.append(f"{where} must match {schema['pattern']!r}")
        if "format" in schema and schema["format"] in _FORMATS:
            if not _FORMATS[schema["format"]].match(value):
                errors.append(f"{where} must be a valid {schema['format']}")

    def _check_array(self, value: Any, schema: dict, where: str, errors: list[str]) -> None:
        items = list(value)
        if "minItems" in schema and len(items) < schema["minItems"]:
            errors.append(f"{where} must have at least {schema['minItems']} items")
        if "maxItems" in schema and len(items) > schema["maxItems"]:
            errors.append(f"{where} must have at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            for index, item in enumerate(items):
                if any(
                    _same(item, other) for other in items[index + 1 :]
                ):
                    errors.append(f"{where} must not contain duplicate items")
                    break
        if "items" in schema:
            for index, item in enumerate(items):
                self._check(item, schema["items"], f"{where}[{index}]", errors)

    def _check_object(self, value: dict, schema: dict, where: str, errors: list[str]) -> None:
        properties = schema.get("properties") or {}

        for field in schema.get("required") or []:
            if field not in value:
                errors.append(f"{where} is missing the required field {field!r}")

        for field, rules in properties.items():
            if field in value:
                self._check(value[field], rules, f"{where}.{field}", errors)

        additional = schema.get("additionalProperties", True)
        if additional is False:
            for field in value:
                # _id is added by the database itself, so it is always allowed.
                if field not in properties and field != "_id":
                    errors.append(f"{where} has the unexpected field {field!r}")
        elif is_document(additional):
            for field, field_value in value.items():
                if field not in properties and field != "_id":
                    self._check(field_value, additional, f"{where}.{field}", errors)

    # -- defaults -----------------------------------------------------

    def apply_defaults(self, document: dict) -> dict:
        """Fill in ``default`` values for fields the document is missing."""
        self._apply_defaults(document, self.schema)
        return document

    def _apply_defaults(self, value: Any, schema: Any) -> None:
        if not is_document(schema) or not is_document(value):
            return
        properties = schema.get("properties") or {}
        for field, rules in properties.items():
            if not is_document(rules):
                continue
            if field not in value and "default" in rules:
                value[field] = rules["default"]
            elif field in value:
                child = value[field]
                if is_document(child):
                    self._apply_defaults(child, rules)
                elif is_array(child) and "items" in rules:
                    for item in child:
                        self._apply_defaults(item, rules["items"])


def _same(left: Any, right: Any) -> bool:
    from .comparison import values_equal

    return values_equal(left, right)


def validate_document(document: Any, validator: Any) -> None:
    """Raise :class:`ValidationError` when the document breaks the schema."""
    if validator is None:
        return
    schema = validator.schema if isinstance(validator, Validator) else validator
    errors = Validator(schema).validate(document)
    if errors:
        raise ValidationError(
            f"document failed validation: {'; '.join(errors)}", errors
        )


def apply_defaults(document: dict, validator: Any) -> dict:
    """Apply the schema's defaults to a document."""
    if validator is None:
        return document
    schema = validator.schema if isinstance(validator, Validator) else validator
    return Validator(schema).apply_defaults(document)
