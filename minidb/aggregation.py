"""The aggregation pipeline.

Supports the stages most applications actually use::

    $match  $project  $addFields/$set  $unset  $group  $sort  $limit
    $skip   $count    $unwind          $replaceRoot  $sortByCount

and a useful set of expressions (``$add``, ``$cond``, ``$year``, ``$concat``,
comparison operators, ...) plus the ``$group`` accumulators.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Callable, Iterable

from .comparison import (
    SortKey,
    compare,
    is_array,
    is_document,
    is_number,
    values_equal,
)
from .errors import WriteError
from .paths import get_path, set_path
from .query import match_document

__all__ = ["run_pipeline", "evaluate_expression", "expression_path"]


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------


def expression_path(document: Any, path: str) -> Any:
    """Resolve a ``$field`` reference the way the aggregation framework does.

    When the path crosses an array the result is an array of the matching
    values, matching MongoDB.
    """
    parts = path.split(".")
    current: list[Any] = [document]
    crossed_array = False

    for part in parts:
        next_values: list[Any] = []
        for container in current:
            if isinstance(container, dict):
                if part in container:
                    next_values.append(container[part])
            elif isinstance(container, (list, tuple)):
                crossed_array = True
                if part.isdigit():
                    index = int(part)
                    if index < len(container):
                        next_values.append(container[index])
                else:
                    for item in container:
                        if isinstance(item, dict) and part in item:
                            next_values.append(item[part])
        current = next_values
        if not current:
            return None

    if crossed_array and len(current) > 1:
        return current
    return current[0] if current else None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if is_array(value):
        return list(value)
    return [value]


class _Missing:
    """Marks a value MongoDB would leave out of the result entirely."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


def _is_expression(value: Any) -> bool:
    """Is this value an expression (rather than a plain literal)?

    MongoDB only treats a document as a *document expression* when each of
    its values is an expression. A document holding plain literals is read
    as a (sub-)projection spec, which MongoDB then rejects -- the field is
    simply left out of the result. ``$literal`` is the way to keep a
    literal document.
    """
    if isinstance(value, str):
        return value.startswith("$")
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return True
    return False


def evaluate_expression(
    expression: Any, document: Any, variables: dict[str, Any] | None = None
) -> Any:
    """Evaluate an aggregation expression against a document."""
    if isinstance(expression, str):
        if expression.startswith("$$"):
            name = expression[2:]
            if name in ("ROOT", "CURRENT"):
                return document
            if variables and name in variables:
                return variables[name]
            raise WriteError(f"unknown variable {expression!r}")
        if expression.startswith("$"):
            return expression_path(document, expression[1:])
        return expression

    if isinstance(expression, dict):
        if len(expression) == 1:
            operator = next(iter(expression))
            if isinstance(operator, str) and operator.startswith("$"):
                return _apply_expression_operator(
                    operator, expression[operator], document, variables
                )
        # A document expression: every value has to be an expression.
        values: dict[str, Any] = {}
        for key, value in expression.items():
            if not _is_expression(value):
                return MISSING
            computed = evaluate_expression(value, document, variables)
            if computed is MISSING:
                return MISSING
            values[key] = computed
        return values

    if isinstance(expression, list):
        return [evaluate_expression(item, document, variables) for item in expression]

    return expression


def _numeric_operands(values: list[Any]) -> list[float]:
    numbers = []
    for value in values:
        if not is_number(value):
            raise WriteError(f"expected a number, got {value!r}")
        numbers.append(value)
    return numbers


def _apply_expression_operator(
    operator: str, operand: Any, document: Any, variables: dict[str, Any] | None
) -> Any:
    def evaluate(value: Any) -> Any:
        return evaluate_expression(value, document, variables)

    if operator == "$literal":
        return operand
    if operator == "$add":
        parts = operand if is_array(operand) else [operand]
        values = [evaluate(part) for part in parts]
        if all(is_number(value) for value in values):
            return sum(values)
        if all(isinstance(value, str) for value in values):
            return "".join(values)
        if any(is_array(value) for value in values):
            merged: list = []
            for value in values:
                merged.extend(_as_list(value))
            return merged
        raise WriteError("$add needs numbers, strings or arrays")
    if operator == "$subtract":
        left, right = _numeric_operands([evaluate(part) for part in operand])
        return left - right
    if operator == "$multiply":
        values = _numeric_operands([evaluate(part) for part in operand])
        result = 1
        for value in values:
            result *= value
        return result
    if operator == "$divide":
        left, right = _numeric_operands([evaluate(part) for part in operand])
        if right == 0:
            raise WriteError("division by zero")
        return left / right
    if operator == "$mod":
        left, right = _numeric_operands([evaluate(part) for part in operand])
        if right == 0:
            raise WriteError("division by zero")
        return left % right
    if operator == "$abs":
        return abs(evaluate(operand))
    if operator == "$ceil":
        return math.ceil(evaluate(operand))
    if operator == "$floor":
        return math.floor(evaluate(operand))
    if operator == "$round":
        value = evaluate(operand[0])
        places = int(evaluate(operand[1])) if len(operand) > 1 else 0
        return round(value, places)
    if operator == "$sqrt":
        return math.sqrt(evaluate(operand))
    if operator == "$pow":
        base, exponent = _numeric_operands([evaluate(part) for part in operand])
        return base**exponent
    if operator == "$concat":
        return "".join(str(evaluate(part)) for part in operand)
    if operator == "$toLower":
        return str(evaluate(operand)).lower()
    if operator == "$toUpper":
        return str(evaluate(operand)).upper()
    if operator == "$trim":
        return str(evaluate(operand)).strip()
    if operator == "$strLenCP":
        return len(str(evaluate(operand)))
    if operator == "$split":
        source = str(evaluate(operand[0]))
        separator = str(evaluate(operand[1]))
        return source.split(separator)
    if operator == "$size":
        value = evaluate(operand)
        if not is_array(value):
            raise WriteError("$size needs an array")
        return len(value)
    if operator == "$arrayElemAt":
        array = _as_list(evaluate(operand[0]))
        index = int(evaluate(operand[1]))
        if index < 0:
            index += len(array)
        if 0 <= index < len(array):
            return array[index]
        return None
    if operator == "$type":
        from .comparison import type_name

        return type_name(evaluate(operand))
    if operator == "$year":
        return _as_datetime(evaluate(operand)).year
    if operator == "$month":
        return _as_datetime(evaluate(operand)).month
    if operator == "$dayOfMonth":
        return _as_datetime(evaluate(operand)).day
    if operator == "$hour":
        return _as_datetime(evaluate(operand)).hour
    if operator == "$minute":
        return _as_datetime(evaluate(operand)).minute
    if operator == "$second":
        return _as_datetime(evaluate(operand)).second
    if operator == "$ifNull":
        value = evaluate(operand[0])
        if value is None:
            return evaluate(operand[1])
        return value
    if operator == "$cond":
        if is_array(operand):
            condition, when_true, when_false = operand
        else:
            condition = operand["if"]
            when_true = operand["then"]
            when_false = operand["else"]
        return evaluate(when_true) if evaluate(condition) else evaluate(when_false)
    if operator in ("$eq", "$ne", "$gt", "$gte", "$lt", "$lte"):
        left = evaluate(operand[0])
        right = evaluate(operand[1])
        if operator == "$eq":
            return values_equal(left, right)
        if operator == "$ne":
            return not values_equal(left, right)
        result = compare(left, right)
        return {
            "$gt": result > 0,
            "$gte": result >= 0,
            "$lt": result < 0,
            "$lte": result <= 0,
        }[operator]
    if operator == "$and":
        return all(bool(evaluate(part)) for part in operand)
    if operator == "$or":
        return any(bool(evaluate(part)) for part in operand)
    if operator == "$not":
        return not bool(evaluate(operand))
    if operator == "$in":
        value = evaluate(operand[0])
        return any(values_equal(value, evaluate(candidate)) for candidate in operand[1:])
    if operator == "$min":
        values = [evaluate(part) for part in operand]
        return min(values, key=SortKey)
    if operator == "$max":
        values = [evaluate(part) for part in operand]
        return max(values, key=SortKey)
    if operator == "$mergeObjects":
        merged: dict = {}
        for part in operand:
            value = evaluate(part)
            if is_document(value):
                merged.update(value)
        return merged

    raise WriteError(f"unsupported aggregation expression: {operator}")


def _as_datetime(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    raise WriteError(f"expected a date, got {value!r}")


# ---------------------------------------------------------------------------
# group accumulators
# ---------------------------------------------------------------------------


class _Accumulator:
    def add(self, value: Any) -> None:
        raise NotImplementedError

    def result(self) -> Any:
        raise NotImplementedError


class _Sum(_Accumulator):
    def __init__(self) -> None:
        self.total: Any = 0
        self.seen = False

    def add(self, value: Any) -> None:
        if is_number(value):
            self.total = self.total + value if self.seen else value
            self.seen = True

    def result(self) -> Any:
        return self.total if self.seen else 0


class _Avg(_Accumulator):
    def __init__(self) -> None:
        self.total = 0
        self.count = 0

    def add(self, value: Any) -> None:
        if is_number(value):
            self.total += value
            self.count += 1

    def result(self) -> Any:
        return self.total / self.count if self.count else None


class _Min(_Accumulator):
    """The smallest value; nulls and missing fields are ignored."""

    def __init__(self) -> None:
        self.value: Any = None
        self.seen = False

    def add(self, value: Any) -> None:
        if value is None:
            return
        if not self.seen or compare(value, self.value) < 0:
            self.value = value
        self.seen = True

    def result(self) -> Any:
        return self.value


class _Max(_Accumulator):
    """The largest value; nulls and missing fields are ignored."""

    def __init__(self) -> None:
        self.value: Any = None
        self.seen = False

    def add(self, value: Any) -> None:
        if value is None:
            return
        if not self.seen or compare(value, self.value) > 0:
            self.value = value
        self.seen = True

    def result(self) -> Any:
        return self.value


class _First(_Accumulator):
    def __init__(self) -> None:
        self.value: Any = None
        self.seen = False

    def add(self, value: Any) -> None:
        if not self.seen:
            self.value = value
            self.seen = True

    def result(self) -> Any:
        return self.value


class _Last(_Accumulator):
    def __init__(self) -> None:
        self.value: Any = None

    def add(self, value: Any) -> None:
        self.value = value

    def result(self) -> Any:
        return self.value


class _Push(_Accumulator):
    def __init__(self) -> None:
        self.items: list = []

    def add(self, value: Any) -> None:
        self.items.append(value)

    def result(self) -> Any:
        return self.items


class _AddToSet(_Accumulator):
    def __init__(self) -> None:
        self.items: list = []

    def add(self, value: Any) -> None:
        if not any(values_equal(existing, value) for existing in self.items):
            self.items.append(value)

    def result(self) -> Any:
        return self.items


class _Count(_Accumulator):
    def __init__(self) -> None:
        self.count = 0

    def add(self, value: Any) -> None:
        self.count += 1

    def result(self) -> Any:
        return self.count


_ACCUMULATORS: dict[str, Callable[[], _Accumulator]] = {
    "$sum": _Sum,
    "$avg": _Avg,
    "$min": _Min,
    "$max": _Max,
    "$first": _First,
    "$last": _Last,
    "$push": _Push,
    "$addToSet": _AddToSet,
    "$count": _Count,
}


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _stage_match(documents: list[dict], spec: dict) -> list[dict]:
    return [doc for doc in documents if match_document(doc, spec)]


def _stage_project(documents: list[dict], spec: dict) -> list[dict]:
    """Project fields in or out of each document.

    MongoDB allows an inclusion projection (``{"a": 1}``) or an exclusion
    projection (``{"a": 0}``) but not a mixture -- the single exception is
    ``_id``, which may be excluded from an inclusion projection.
    """
    inclusion: dict[str, Any] = {}
    exclusion: set[str] = set()

    for field, value in spec.items():
        if not isinstance(value, dict) and value in (0, False):
            exclusion.add(field)
        else:
            inclusion[field] = value

    if exclusion - {"_id"} and inclusion:
        raise WriteError(
            "a projection cannot mix included and excluded fields "
            "(only _id may be excluded alongside inclusions)"
        )

    results: list[dict] = []

    if inclusion:
        for document in documents:
            projected: dict = {}
            if "_id" not in exclusion and "_id" in document:
                projected["_id"] = document["_id"]
            for field, value in inclusion.items():
                if field == "_id":
                    continue
                if not isinstance(value, dict) and value in (1, True):
                    found, present = get_path(document, field)
                    if present:
                        set_path(
                            projected,
                            field,
                            found[0] if len(found) == 1 else found,
                        )
                else:
                    computed = evaluate_expression(value, document)
                    if computed is not MISSING:
                        set_path(projected, field, computed)
            results.append(projected)
        return results

    for document in documents:
        projected = dict(document)
        for field in exclusion:
            _remove_field(projected, field)
        results.append(projected)
    return results


def _remove_field(document: dict, path: str) -> None:
    parts = path.split(".")
    container: Any = document
    for part in parts[:-1]:
        if isinstance(container, dict) and part in container:
            container = container[part]
        else:
            return
    if isinstance(container, dict):
        container.pop(parts[-1], None)


def _stage_add_fields(documents: list[dict], spec: dict) -> list[dict]:
    results = []
    for document in documents:
        updated = dict(document)
        for field, expression in spec.items():
            computed = evaluate_expression(expression, document)
            if computed is MISSING:
                updated.pop(field, None)
            else:
                set_path(updated, field, computed)
        results.append(updated)
    return results


def _stage_unset(documents: list[dict], spec: dict) -> list[dict]:
    fields = spec if isinstance(spec, (list, tuple)) else [spec]
    results = []
    for document in documents:
        updated = dict(document)
        for field in fields:
            _remove_field(updated, field)
        results.append(updated)
    return results


def _stage_group(documents: list[dict], spec: dict) -> list[dict]:
    if "_id" not in spec:
        raise WriteError("a $group stage needs an _id")
    group_expression = spec["_id"]
    fields = {key: value for key, value in spec.items() if key != "_id"}

    groups: dict[Any, dict] = {}
    order: list[Any] = []

    for document in documents:
        key = evaluate_expression(group_expression, document)
        if key is MISSING:
            key = None
        hashable = _hashable(key)
        if hashable not in groups:
            groups[hashable] = {"_id": key, "accumulators": {}}
            for name, definition in fields.items():
                operator = next(iter(definition))
                groups[hashable]["accumulators"][name] = (
                    operator,
                    definition[operator],
                    _ACCUMULATORS[operator](),
                )
            order.append(hashable)

        group = groups[hashable]
        for name, definition in fields.items():
            operator, argument, accumulator = group["accumulators"][name]
            if operator == "$count":
                accumulator.add(document)
            else:
                value = evaluate_expression(argument, document)
                accumulator.add(None if value is MISSING else value)

    results = []
    for hashable in order:
        group = groups[hashable]
        result = {"_id": group["_id"]}
        for name, (_, _, accumulator) in group["accumulators"].items():
            result[name] = accumulator.result()
        results.append(result)
    return results


def _hashable(value: Any) -> Any:
    if is_document(value):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if is_array(value):
        return tuple(_hashable(item) for item in value)
    return value


def _stage_sort(documents: list[dict], spec: Any) -> list[dict]:
    if isinstance(spec, dict):
        pairs = list(spec.items())
    else:
        pairs = [tuple(item) for item in spec]

    result = list(documents)
    for field, direction in reversed(pairs):
        result.sort(
            key=lambda doc, f=field: SortKey(expression_path(doc, f)),
            reverse=direction < 0,
        )
    return result


def _stage_limit(documents: list[dict], spec: Any) -> list[dict]:
    count = int(spec)
    return documents[:count] if count >= 0 else documents[count:]


def _stage_skip(documents: list[dict], spec: Any) -> list[dict]:
    return documents[int(spec):]


def _stage_count(documents: list[dict], spec: Any) -> list[dict]:
    field = spec if isinstance(spec, str) else "count"
    return [{field: len(documents)}]


def _stage_unwind(documents: list[dict], spec: Any) -> list[dict]:
    # Both forms are supported: "$field" and {"path": "$field"}.
    if isinstance(spec, str):
        spec = {"path": spec}
    path = spec["path"]
    if not isinstance(path, str) or not path.startswith("$"):
        raise WriteError("$unwind needs a path such as '$items'")
    field = path[1:]
    include_index = spec.get("includeArrayIndex")
    preserve = spec.get("preserveNullAndEmptyArrays", False)

    results: list[dict] = []
    for document in documents:
        value = expression_path(document, field)
        if value is None or (is_array(value) and not value):
            if preserve:
                output = dict(document)
                if include_index:
                    output[include_index] = None
                results.append(output)
            continue
        if not is_array(value):
            output = dict(document)
            if include_index:
                output[include_index] = None
            results.append(output)
            continue
        for index, item in enumerate(value):
            output = dict(document)
            set_path(output, field, item)
            if include_index:
                output[include_index] = index
            results.append(output)
    return results


def _stage_replace_root(documents: list[dict], spec: Any) -> list[dict]:
    expression = spec["newRoot"] if isinstance(spec, dict) else spec
    results = []
    for document in documents:
        new_root = evaluate_expression(expression, document)
        if not is_document(new_root):
            raise WriteError("$replaceRoot needs a document")
        results.append(new_root)
    return results


def _stage_sort_by_count(documents: list[dict], spec: Any) -> list[dict]:
    # Both forms are supported: "$field" and {"field": "$field"}.
    expression = spec["field"] if isinstance(spec, dict) else spec
    grouped = _stage_group(documents, {"_id": expression, "count": {"$sum": 1}})
    return _stage_sort(grouped, {"count": -1})


_STAGES: dict[str, Callable[[list[dict], Any], list[dict]]] = {
    "$match": _stage_match,
    "$project": _stage_project,
    "$addFields": _stage_add_fields,
    "$set": _stage_add_fields,
    "$unset": _stage_unset,
    "$group": _stage_group,
    "$sort": _stage_sort,
    "$limit": _stage_limit,
    "$skip": _stage_skip,
    "$count": _stage_count,
    "$unwind": _stage_unwind,
    "$replaceRoot": _stage_replace_root,
    "$sortByCount": _stage_sort_by_count,
}


def run_pipeline(documents: Iterable[dict], pipeline: Any) -> list[dict]:
    """Run an aggregation pipeline over ``documents``."""
    if not isinstance(pipeline, (list, tuple)):
        raise WriteError("a pipeline must be an array of stages")

    current = list(documents)
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise WriteError(f"each pipeline stage must be a single-key document: {stage!r}")
        name, spec = next(iter(stage.items()))
        handler = _STAGES.get(name)
        if handler is None:
            raise WriteError(f"unsupported aggregation stage: {name}")
        current = handler(current, spec)
    return current
