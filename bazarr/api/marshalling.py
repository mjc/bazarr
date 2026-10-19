# coding=utf-8

from collections.abc import Iterable

from flask_restx import marshal
from flask_restx.model import Model, OrderedModel

from api.response_types import MarshalledRow, MediaMetadata


def marshal_row(row: MediaMetadata | MarshalledRow, model: Model | OrderedModel) -> MarshalledRow:
    result = marshal(row, model)
    if not isinstance(result, dict):
        raise TypeError('Expected a marshalled response object')
    return result


def marshal_rows(rows: Iterable[MediaMetadata | MarshalledRow], model: Model | OrderedModel) -> list[MarshalledRow]:
    # Consume formatted rows one at a time instead of retaining an intermediate list.
    return [marshal_row(row, model) for row in rows]
