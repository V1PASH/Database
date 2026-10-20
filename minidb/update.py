"""Update operators.

Implements MongoDB's update modifiers: ``$set``, ``$unset``, ``$inc``,
``$mul``, ``$min``, ``$max``, ``$push``, ``$addToSet``, ``$pop``, ``$pull``,
``$pullAll``, ``$rename``, ``$setOnInsert`` and ``$currentDate``, plus
``arrayFilters`` so that ``"items.$[i].qty"`` style paths work.

An update document either contains only ``$``-operators (a modifier update)
or contains no operators at all (a *replacement*, which swaps the whole
document out).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable

from .comparison import SortKey, compare, is_array, is_number, values_equal
from .errors import WriteError
from .paths import get_path, set_path, split_path, unset_path
from .query import apply_operator_expression, match_document

__all__ = ["apply_update", "is_modifier_update", "MODIFIER_OPERATORS"]

MODIFIER_OPERATORS = (
    "$set",
    "$unset",
    "$inc",
    "$mul",
    "$min",
    "$max",
    "$push",
    "$addToSet",
    "$pop",
    "$pull",
    "$pullAll",
    "$rename",
    "$setOnInsert",
    "$currentDate",
)


def is_modifier_update(update: Any) -> bool:
    """True when the update document uses operators rather than replacing."""
    if not isinstance(update, dict):
        raise WriteError("update must be a document")
    if not update:
        return False
    return any(isinstance(key, str) and key.startswith("$") for key in update)


# ---------------------------------------------------------------------------
# target resolution (supports "items.$[i].qty")
# ---------------------------------------------------------------------------


def _matches_array_filter(element: Any, identifier: str, condition: Any) -> bool:
    """Does one array member satisfy the filter bound to ``identifier``?

    Array filters are written in one of two ways::

        {"item": {"$gte": 5}}     the condition applies to the member itself
        {"item.qty": {"$gte": 5}} the condition applies to a field of the member

    The identifier prefix is stripped before the filter is applied.
    """
    if not isinstance(condition, dict):
        return False

    rewritten: dict[str, Any] = {}
    on_element: Any = None
    for key, value in condition.items():
        if isinstance(key, str) and key.startswith(identifier + "."):
            rewritten[key[len(identifier) + 1 :]] = value
        elif key == identifier:
            on_element = value
        else:
            rewritten[key] = value

    if on_element is not None:
        # The condition describes the member itself.
        if isinstance(on_element, dict) and any(
            isinstance(key, str) and key.startswith("$") for key in on_element
        ):
            return apply_operator_expression(
                element, "", on_element, [element], True
            )
        return values_equal(element, on_element)

    if isinstance(element, dict):
        return match_document(element, rewritten)
    return False


def _resolve_targets(
    document: Any, path: str, array_filters: dict[str, Any]
) -> list[tuple[Any, str]]:
    """Find every ``(container, key)`` pair a path refers to."""
    parts = split_path(path)
    targets: list[tuple[Any, str]] = []

    def walk(container: Any, remaining: list[str]) -> None:
        if not remaining:
            return
        key = remaining[0]
        rest = remaining[1:]

        if key == "$[]":
            if not isinstance(container, list):
                raise WriteError(f"cannot use '$[]' in the path {path!r}")
            for index, element in enumerate(container):
                if rest:
                    walk(element, rest)
                else:
                    targets.append((container, str(index)))
            return

        if key.startswith("$[") and key.endswith("]"):
            identifier = key[2:-1]
            if identifier not in array_filters:
                raise WriteError(
                    f"no array filter found for identifier {identifier!r} in {path!r}"
                )
            if not isinstance(container, list):
                raise WriteError(f"cannot use {key!r} on a non-array in {path!r}")
            condition = array_filters[identifier]
            for index, element in enumerate(container):
                if not _matches_array_filter(element, identifier, condition):
                    continue
                if rest:
                    walk(element, rest)
                else:
                    # The path ends at the filter: the member itself is the
                    # target.
                    targets.append((container, str(index)))
            return

        if not rest:
            targets.append((container, key))
            return

        child = None
        if isinstance(container, dict):
            child = container.get(key)
        elif isinstance(container, list) and key.isdigit():
            index = int(key)
            if index < len(container):
                child = container[index]
        if isinstance(child, (dict, list)):
            walk(child, rest)

    walk(document, parts)
    return targets


def _read_target(document: Any, path: str, array_filters: dict[str, Any]) -> Any:
    """Read the current value of a path (first target wins)."""
    targets = _resolve_targets(document, path, array_filters)
    if not targets:
        values, _ = get_path(document, path)
        return values[0] if values else None
    container, key = targets[0]
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, list) and key.isdigit():
        index = int(key)
        if index < len(container):
            return container[index]
    return None


def _write_targets(
    document: Any, path: str, value: Any, array_filters: dict[str, Any]
) -> None:
    targets = _resolve_targets(document, path, array_filters)
    if not targets:
        if "$[" in path:
            # An array filter that matched nothing: there is nothing to
            # write, and the path itself is not a real field.
            return
        set_path(document, path, value)
        return
    for container, key in targets:
        if isinstance(container, dict):
            container[key] = value
        elif isinstance(container, list) and key.isdigit():
            index = int(key)
            while len(container) <= index:
                container.append(None)
            container[index] = value


# ---------------------------------------------------------------------------
# individual operators
# ---------------------------------------------------------------------------


def _op_set(document, path, value, ctx) -> None:
    _write_targets(document, path, value, ctx.array_filters)


def _op_unset(document, path, value, ctx) -> None:
    targets = _resolve_targets(document, path, ctx.array_filters)
    if not targets:
        unset_path(document, path)
        return
    for container, key in targets:
        if isinstance(container, dict):
            container.pop(key, None)
        elif isinstance(container, list) and key.isdigit():
            index = int(key)
            if index < len(container):
                container[index] = None


def _op_inc(document, path, value, ctx) -> None:
    if not is_number(value):
        raise WriteError(f"$inc needs a number, got {type(value).__name__}")
    current = _read_target(document, path, ctx.array_filters)
    if current is None:
        new_value = value
    elif is_number(current):
        new_value = current + value
    else:
        raise WriteError(f"cannot apply $inc to the non-numeric field {path!r}")
    _write_targets(document, path, new_value, ctx.array_filters)


def _op_mul(document, path, value, ctx) -> None:
    if not is_number(value):
        raise WriteError(f"$mul needs a number, got {type(value).__name__}")
    current = _read_target(document, path, ctx.array_filters)
    new_value = value if current is None else current * value
    _write_targets(document, path, new_value, ctx.array_filters)


def _op_min(document, path, value, ctx) -> None:
    current = _read_target(document, path, ctx.array_filters)
    if current is None or compare(value, current) < 0:
        _write_targets(document, path, value, ctx.array_filters)


def _op_max(document, path, value, ctx) -> None:
    current = _read_target(document, path, ctx.array_filters)
    if current is None or compare(value, current) > 0:
        _write_targets(document, path, value, ctx.array_filters)


def _sort_values(values: list, spec: Any) -> list:
    if isinstance(spec, int) and not isinstance(spec, bool):
        return sorted(values, key=SortKey, reverse=spec < 0)
    if isinstance(spec, dict):
        for field, direction in reversed(list(spec.items())):
            values = sorted(
                values,
                key=lambda item, f=field: SortKey(_nested(item, f)),
                reverse=direction < 0,
            )
        return values
    raise WriteError("$sort needs 1, -1 or a document of field directions")


def _nested(document: Any, path: str) -> Any:
    values, _ = get_path(document, path)
    return values[0] if values else None


def _op_push(document, path, value, ctx) -> None:
    current = _read_target(document, path, ctx.array_filters)
    if current is None:
        current = []
    elif not is_array(current):
        raise WriteError(f"cannot $push to the non-array field {path!r}")
    else:
        current = list(current)

    if isinstance(value, dict) and "$each" in value:
        each = list(value.get("$each") or [])
        position = value.get("$position")
        if position is None:
            current.extend(each)
        else:
            current[position:position] = each
        if "$sort" in value:
            current = _sort_values(current, value["$sort"])
        if "$slice" in value:
            slice_value = value["$slice"]
            if slice_value < 0:
                current = current[slice_value:]
            else:
                current = current[:slice_value]
    else:
        current.append(value)

    _write_targets(document, path, current, ctx.array_filters)


def _op_add_to_set(document, path, value, ctx) -> None:
    current = _read_target(document, path, ctx.array_filters)
    if current is None:
        current = []
    elif not is_array(current):
        raise WriteError(f"cannot $addToSet to the non-array field {path!r}")
    else:
        current = list(current)

    if isinstance(value, dict) and "$each" in value:
        candidates = list(value.get("$each") or [])
    else:
        candidates = [value]

    for candidate in candidates:
        if not any(values_equal(existing, candidate) for existing in current):
            current.append(candidate)

    _write_targets(document, path, current, ctx.array_filters)


def _op_pop(document, path, value, ctx) -> None:
    if value not in (1, -1):
        raise WriteError("$pop needs 1 (last) or -1 (first)")
    current = _read_target(document, path, ctx.array_filters)
    if current is None or not is_array(current):
        return
    current = list(current)
    if not current:
        return
    if value == 1:
        current.pop()
    else:
        current.pop(0)
    _write_targets(document, path, current, ctx.array_filters)


def _op_pull(document, path, value, ctx) -> None:
    current = _read_target(document, path, ctx.array_filters)
    if current is None or not is_array(current):
        return
    items = list(current)
    if isinstance(value, dict) and any(
        isinstance(key, str) and key.startswith("$") for key in value
    ):
        # $pull with an operator expression removes members matching it.
        kept = [
            item
            for item in items
            if not _matches_pull_condition(item, value)
        ]
    elif isinstance(value, dict):
        kept = [item for item in items if not _pull_document_matches(item, value)]
    else:
        kept = [item for item in items if not values_equal(item, value)]
    _write_targets(document, path, kept, ctx.array_filters)


def _pull_document_matches(item: Any, condition: dict) -> bool:
    if isinstance(item, dict):
        return match_document(item, condition)
    return False


def _matches_pull_condition(item: Any, expression: dict) -> bool:
    """Does an array member match a ``$pull`` operator expression?

    Like MongoDB, the comparison is type bracketed: a member only takes part
    when it has the same type as the operand.
    """
    return apply_operator_expression(item, "", expression, [item], True)


def _op_pull_all(document, path, value, ctx) -> None:
    if not isinstance(value, (list, tuple)):
        raise WriteError("$pullAll needs an array of values")
    current = _read_target(document, path, ctx.array_filters)
    if current is None or not is_array(current):
        return
    items = list(current)
    kept = [
        item
        for item in items
        if not any(values_equal(item, candidate) for candidate in value)
    ]
    _write_targets(document, path, kept, ctx.array_filters)


def _op_rename(document, path, value, ctx) -> None:
    if not isinstance(value, str):
        raise WriteError("$rename needs the new field name as a string")
    current = _read_target(document, path, ctx.array_filters)
    if current is None and not get_path(document, path)[1]:
        return
    targets = _resolve_targets(document, path, ctx.array_filters)
    for container, key in targets:
        if isinstance(container, dict):
            container[value] = container.pop(key)
        else:
            raise WriteError("$rename cannot be used on array members")


def _op_set_on_insert(document, path, value, ctx) -> None:
    if not ctx.is_insert:
        return
    _write_targets(document, path, value, ctx.array_filters)


def _op_current_date(document, path, value, ctx) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    if isinstance(value, dict) and value.get("$type") == "timestamp":
        stored: Any = {"$timestamp": {"t": int(now.timestamp()), "i": 0}}
    else:
        stored = now
    _write_targets(document, path, stored, ctx.array_filters)


_OPERATORS: dict[str, Callable[[Any, str, Any, Any], None]] = {
    "$set": _op_set,
    "$unset": _op_unset,
    "$inc": _op_inc,
    "$mul": _op_mul,
    "$min": _op_min,
    "$max": _op_max,
    "$push": _op_push,
    "$addToSet": _op_add_to_set,
    "$pop": _op_pop,
    "$pull": _op_pull,
    "$pullAll": _op_pull_all,
    "$rename": _op_rename,
    "$setOnInsert": _op_set_on_insert,
    "$currentDate": _op_current_date,
}


class _Context:
    __slots__ = ("array_filters", "is_insert")

    def __init__(self, array_filters: dict[str, Any], is_insert: bool) -> None:
        self.array_filters = array_filters
        self.is_insert = is_insert


def _build_array_filters(array_filters: Any) -> dict[str, Any]:
    if array_filters is None:
        return {}
    if not isinstance(array_filters, (list, tuple)):
        raise WriteError("array_filters must be an array")
    built: dict[str, Any] = {}
    for entry in array_filters:
        if not isinstance(entry, dict):
            raise WriteError("each array filter must be a document")
        for key in entry:
            # The whole entry is kept: the identifier prefix is stripped
            # later, when the filter is matched against an array member.
            built[key.split(".")[0]] = entry
    return built


def apply_update(
    document: dict,
    update: Any,
    *,
    is_insert: bool = False,
    array_filters: Any = None,
) -> dict:
    """Apply ``update`` to ``document`` in place and return it.

    When ``update`` contains no operators the document is *replaced* by it.
    """
    if not isinstance(update, dict):
        raise WriteError("update must be a document")

    if not update:
        raise WriteError("update document is empty")

    modifier = is_modifier_update(update)

    if modifier:
        for key in update:
            if not (isinstance(key, str) and key.startswith("$")):
                raise WriteError(
                    "update document cannot mix operators with plain fields"
                )
        unknown = [key for key in update if key not in _OPERATORS]
        if unknown:
            raise WriteError(f"unsupported update operator: {unknown[0]}")
    else:
        if "_id" in update and "_id" in document and not values_equal(
            update["_id"], document["_id"]
        ):
            raise WriteError("the _id field cannot be changed")
        replacement = dict(update)
        if "_id" in document:
            replacement["_id"] = document["_id"]
        document.clear()
        document.update(replacement)
        return document

    context = _Context(_build_array_filters(array_filters), is_insert)

    for operator, operand in update.items():
        if not isinstance(operand, dict):
            raise WriteError(f"{operator} needs a document of field/value pairs")
        for path, value in operand.items():
            if path.startswith("$"):
                raise WriteError(f"{operator} may not be applied to the field {path!r}")
            if operator == "$set" and path == "_id":
                raise WriteError("the _id field cannot be changed")
            _OPERATORS[operator](document, path, value, context)

    return document
