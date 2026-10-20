"""The query engine.

Implements MongoDB's filter semantics: dotted paths, implicit equality,
array "multikey" matching, the full set of comparison operators and the
logical combinators.

Two rules drive most of the code:

1. **A missing field is not the same as ``null``** -- except for equality,
   where MongoDB treats them the same (``{"a": null}`` matches documents
   with no ``a`` field).
2. **Values are compared across types** using the BSON type order, so
   ``{"a": {"$gt": 5}}`` matches ``a="abc"`` because strings sort after
   numbers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .comparison import (
    compare,
    is_array,
    is_number,
    type_name,
    type_rank,
    values_equal,
)
from .errors import WriteError

__all__ = ["match_document", "get_path", "split_path", "path_exists", "LOGICAL_OPERATORS"]

LOGICAL_OPERATORS = ("$and", "$or", "$nor")

# Operators that take a list of values.
_LIST_OPERATORS = ("$in", "$nin", "$all")

# MongoDB numeric type codes used by ``$type``.
_TYPE_CODES = {
    "double": 1,
    "string": 2,
    "object": 3,
    "array": 4,
    "binData": 5,
    "undefined": 6,
    "objectId": 7,
    "bool": 8,
    "date": 9,
    "null": 10,
    "regex": 11,
    "int": 16,
    "timestamp": 17,
    "long": 18,
    "decimal": 19,
}


def split_path(path: str) -> list[str]:
    """Split a dotted field path into its components."""
    if not isinstance(path, str):
        raise WriteError(f"field path must be a string, got {type(path).__name__}")
    if path == "":
        raise WriteError("field path may not be empty")
    return path.split(".")


def get_path(document: Any, path: str) -> tuple[list[Any], bool]:
    """Resolve a dotted path inside a document.

    Returns ``(values, found)``. ``values`` holds every value the path
    resolves to -- more than one when the path crosses an array, e.g.
    ``"a.b"`` against ``{"a": [{"b": 1}, {"b": 2}]}`` yields ``[1, 2]``.
    ``found`` reports whether the path exists at all, which is what
    ``$exists`` needs and what distinguishes a missing field from a field
    that is explicitly ``null``.
    """
    parts = split_path(path)
    current: list[Any] = [document]
    found = False

    for part in parts:
        next_values: list[Any] = []
        for container in current:
            if isinstance(container, dict):
                if part in container:
                    found = True
                    next_values.append(container[part])
            elif isinstance(container, (list, tuple)):
                if part.isdigit():
                    index = int(part)
                    if index < len(container):
                        found = True
                        next_values.append(container[index])
                else:
                    # Implicit traversal: MongoDB reaches into documents
                    # nested inside arrays using the same dotted path.
                    for item in container:
                        if isinstance(item, dict) and part in item:
                            found = True
                            next_values.append(item[part])
        current = next_values
        if not current:
            break

    return current, found


def path_exists(document: Any, path: str) -> bool:
    """True when a dotted path resolves to at least one value."""
    _, found = get_path(document, path)
    return found


def _is_operator_expression(condition: Any) -> bool:
    """True when a condition dict is made up purely of operators."""
    return (
        isinstance(condition, dict)
        and len(condition) > 0
        and all(isinstance(key, str) and key.startswith("$") for key in condition)
    )


def _has_operator_key(condition: Any) -> bool:
    return isinstance(condition, dict) and any(
        isinstance(key, str) and key.startswith("$") for key in condition
    )


def _equality_match(actual: Any, expected: Any) -> bool:
    """Equality between one stored value and one query value.

    Handles the array rules:
    * a stored array matches a scalar when it *contains* that scalar
    * a stored array matches an array when it contains *all* its elements
    """
    if values_equal(actual, expected):
        return True

    if is_array(actual):
        items = list(actual)
        if is_array(expected):
            wanted = list(expected)
            return all(any(values_equal(item, w) for item in items) for w in wanted)
        return any(values_equal(item, expected) for item in items)

    return False


def _match_equality(values: list[Any], expected: Any, found: bool) -> bool:
    """Apply MongoDB's implicit equality to the values at a path."""
    if not values:
        # Missing field: only ``null`` matches (MongoDB equates them).
        return expected is None
    return any(_equality_match(value, expected) for value in values)


def _iter_candidates(value: Any) -> Iterable[Any]:
    """Flatten one level of array so array members can be compared."""
    if is_array(value):
        return list(value)
    return [value]


def _match_comparison(values: list[Any], operand: Any, operator: str) -> bool:
    """Apply ``$gt``/``$gte``/``$lt``/``$lte``.

    MongoDB *type brackets* these operators: a value only takes part in the
    comparison when it has the same type as the operand. So ``{"a": {"$lt":
    5}}`` skips a field holding ``null`` or a document, but does compare
    numbers against numbers.

    For an array field the comparison succeeds if *any* element satisfies it,
    which is MongoDB's multikey behaviour.
    """
    operand_rank = type_rank(operand)
    for value in values:
        for candidate in _iter_candidates(value):
            if type_rank(candidate) != operand_rank:
                continue
            if operator == "$gt" and compare(candidate, operand) > 0:
                return True
            if operator == "$gte" and compare(candidate, operand) >= 0:
                return True
            if operator == "$lt" and compare(candidate, operand) < 0:
                return True
            if operator == "$lte" and compare(candidate, operand) <= 0:
                return True
    return False


def _match_in(values: list[Any], operand: Any, found: bool) -> bool:
    """Apply ``$in`` / ``$nin``."""
    if not isinstance(operand, (list, tuple)):
        raise WriteError("$in and $nin need an array of values")
    for candidate in operand:
        if is_array(candidate):
            # An array inside $in must match exactly.
            if any(values_equal(value, candidate) for value in values):
                return True
        elif _match_equality(values, candidate, found):
            return True
    return False


def _match_regex(values: list[Any], operand: Any, options: str) -> bool:
    if isinstance(operand, re.Pattern):
        pattern = operand
    elif isinstance(operand, str):
        flags = re.IGNORECASE if "i" in options else 0
        flags |= re.MULTILINE if "m" in options else 0
        flags |= re.DOTALL if "s" in options else 0
        flags |= re.VERBOSE if "x" in options else 0
        try:
            pattern = re.compile(operand, flags)
        except re.error as exc:
            raise WriteError(f"invalid regular expression: {exc}") from exc
    else:
        raise WriteError("$regex needs a string or a compiled pattern")

    for value in values:
        for candidate in _iter_candidates(value):
            if isinstance(candidate, str) and pattern.search(candidate):
                return True
    return False


def _match_all(values: list[Any], operand: Any) -> bool:
    """Apply ``$all``: the stored array must contain every listed value."""
    if not isinstance(operand, (list, tuple)):
        raise WriteError("$all needs an array of values")
    wanted = list(operand)
    for value in values:
        if not is_array(value):
            continue
        items = list(value)
        if all(any(values_equal(item, w) for item in items) for w in wanted):
            return True
    return False


def _match_size(values: list[Any], operand: Any) -> bool:
    if not isinstance(operand, int) or isinstance(operand, bool):
        raise WriteError("$size needs an integer")
    for value in values:
        if is_array(value) and len(list(value)) == operand:
            return True
    return False


def _match_elem_match(values: list[Any], operand: Any) -> bool:
    """Apply ``$elemMatch``: at least one array member matches."""
    if isinstance(operand, dict) and _has_operator_key(operand):
        # ``{$elemMatch: {$gt: 5}}`` -- operators applied to the member itself.
        for value in values:
            if not is_array(value):
                continue
            for item in value:
                if _apply_operators_to_value(item, operand):
                    return True
        return False

    for value in values:
        if not is_array(value):
            continue
        for item in value:
            if isinstance(operand, dict):
                if match_document(item, operand):
                    return True
            elif values_equal(item, operand):
                return True
    return False


def _apply_operators_to_value(value: Any, expression: dict) -> bool:
    """Apply an operator expression directly to a single value.

    Used by ``$elemMatch`` where the operators describe the member value
    rather than a field inside a member document. The comparison is type
    bracketed, exactly as it is for ``$elemMatch`` in MongoDB.
    """
    options = expression.get("$options", "")
    for operator, operand in expression.items():
        if operator == "$options":
            continue
        if operator == "$eq" and not _equality_match(value, operand):
            return False
        if operator == "$ne" and _equality_match(value, operand):
            return False
        if operator in ("$gt", "$gte", "$lt", "$lte"):
            if not _match_comparison([value], operand, operator):
                return False
        if operator == "$regex":
            if not _match_regex([value], operand, options):
                return False
        if operator == "$type":
            if type_name(value) != operand:
                return False
    return True


def _match_type(values: list[Any], operand: Any) -> bool:
    """Apply ``$type``.

    MongoDB's ``"number"`` is an alias covering int, long, double and
    decimal, so it needs special handling.
    """
    for value in values:
        name = type_name(value)
        if isinstance(operand, str):
            if operand == "number":
                if is_number(value):
                    return True
                continue
            if name == operand:
                return True
        elif isinstance(operand, int) and not isinstance(operand, bool):
            if _TYPE_CODES.get(name) == operand:
                return True
    return False


def _match_mod(values: list[Any], operand: Any) -> bool:
    if not isinstance(operand, (list, tuple)) or len(operand) != 2:
        raise WriteError("$mod needs an array of [divisor, remainder]")
    divisor, remainder = operand
    if not isinstance(divisor, int) or isinstance(divisor, bool) or divisor == 0:
        raise WriteError("$mod needs a non-zero integer divisor")
    for value in values:
        if is_number(value) and value % divisor == remainder:
            return True
    return False


def _apply_operator(
    operator: str,
    operand: Any,
    path: str,
    values: list[Any],
    found: bool,
    document: Any,
    options: str,
) -> bool:
    """Evaluate a single query operator against the values at ``path``."""
    if operator == "$eq":
        return _match_equality(values, operand, found)
    if operator == "$ne":
        return not _match_equality(values, operand, found)
    if operator in ("$gt", "$gte", "$lt", "$lte"):
        return _match_comparison(values, operand, operator)
    if operator == "$in":
        return _match_in(values, operand, found)
    if operator == "$nin":
        return not _match_in(values, operand, found)
    if operator == "$exists":
        return found is bool(operand)
    if operator == "$regex":
        return _match_regex(values, operand, options)
    if operator == "$all":
        return _match_all(values, operand)
    if operator == "$size":
        return _match_size(values, operand)
    if operator == "$elemMatch":
        return _match_elem_match(values, operand)
    if operator == "$not":
        if not _is_operator_expression(operand):
            raise WriteError("$not needs an operator expression")
        return not apply_operator_expression(document, path, operand, values, found)
    if operator == "$type":
        return _match_type(values, operand)
    if operator == "$mod":
        return _match_mod(values, operand)
    if operator == "$options":
        return True
    raise WriteError(f"unsupported query operator: {operator}")


def apply_operator_expression(
    document: Any, path: str, expression: dict, values: list[Any], found: bool
) -> bool:
    """Evaluate an operator expression against the values at ``path``."""
    options = expression.get("$options", "") if isinstance(expression, dict) else ""
    for operator, operand in expression.items():
        if not (isinstance(operator, str) and operator.startswith("$")):
            raise WriteError(
                f"unknown operator {operator!r}; an operator expression may only "
                "contain $-prefixed keys"
            )
        if not _apply_operator(operator, operand, path, values, found, document, options):
            return False
    return True


def match_field(document: Any, path: str, condition: Any) -> bool:
    """Match one ``field: condition`` pair of a filter."""
    values, found = get_path(document, path)

    if _is_operator_expression(condition):
        return apply_operator_expression(document, path, condition, values, found)

    if _has_operator_key(condition):
        raise WriteError(
            f"mixed operator and non-operator keys in the condition for {path!r}"
        )

    return _match_equality(values, condition, found)


def match_document(document: Any, query: Any) -> bool:
    """Return True when ``document`` satisfies ``query``."""
    if query is None:
        return True
    if not isinstance(query, dict):
        raise WriteError(f"query filter must be a document, got {type(query).__name__}")

    for key, value in query.items():
        if key == "$and":
            if not isinstance(value, (list, tuple)):
                raise WriteError("$and needs an array of filters")
            if not all(match_document(document, sub) for sub in value):
                return False
        elif key == "$or":
            if not isinstance(value, (list, tuple)):
                raise WriteError("$or needs an array of filters")
            if not value:
                raise WriteError("$or needs at least one filter")
            if not any(match_document(document, sub) for sub in value):
                return False
        elif key == "$nor":
            if not isinstance(value, (list, tuple)):
                raise WriteError("$nor needs an array of filters")
            if not value:
                raise WriteError("$nor needs at least one filter")
            if any(match_document(document, sub) for sub in value):
                return False
        elif isinstance(key, str) and key.startswith("$"):
            raise WriteError(f"unsupported top-level query operator: {key}")
        else:
            if not match_field(document, key, value):
                return False

    return True
