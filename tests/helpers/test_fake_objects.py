"""Tests for fake SQLAlchemy-like helper objects used in path tests."""
from types import SimpleNamespace

import pytest

from tests.test_helpers import (
    _Condition,
    _Column,
    _Query,
    _Result,
)


class TestCondition:
    def test_and_returns_self(self):
        cond = _Condition()
        result = cond & _Condition()
        assert result is cond

    def test_and_accepts_any_right_operand(self):
        cond = _Condition()
        assert (cond & "anything") is cond
        assert (cond & 42) is cond
        assert (cond & None) is cond


class TestColumn:
    def test_hash_is_based_on_name(self):
        c1 = _Column("foo")
        c2 = _Column("foo")
        c3 = _Column("bar")
        assert hash(c1) == hash(c2)
        assert hash(c1) != hash(c3)

    def test_eq_returns_condition(self):
        col = _Column("x")
        result = col == "value"
        assert isinstance(result, _Condition)

    def test_ne_returns_condition(self):
        col = _Column("x")
        result = col != "value"
        assert isinstance(result, _Condition)

    def test_in_returns_condition(self):
        col = _Column("x")
        result = col.in_([1, 2, 3])
        assert isinstance(result, _Condition)

    def test_is_returns_condition(self):
        col = _Column("x")
        result = col.is_(None)
        assert isinstance(result, _Condition)

    def test_is_not_returns_condition(self):
        col = _Column("x")
        result = col.is_not(None)
        assert isinstance(result, _Condition)

    def test_label_returns_self(self):
        col = _Column("x")
        assert col.label("alias") is col

    def test_lower_returns_self(self):
        col = _Column("x")
        assert col.lower() is col

    def test_columns_usable_as_dict_keys(self):
        c1 = _Column("a")
        c2 = _Column("b")
        mapping = {c1: 1, c2: 2}
        assert mapping[c1] == 1
        assert mapping[c2] == 2

    def test_equal_columns_share_dict_slot(self):
        c1 = _Column("same")
        c2 = _Column("same")
        mapping = {c1: 10}
        assert mapping[c2] == 10


class TestQuery:
    def test_where_returns_self(self):
        q = _Query()
        assert q.where(_Condition()) is q

    def test_join_returns_self(self):
        q = _Query()
        assert q.join(object()) is q

    def test_select_from_returns_self(self):
        q = _Query()
        assert q.select_from(object()) is q

    def test_exists_returns_self(self):
        q = _Query()
        assert q.exists() is q

    def test_limit_returns_self(self):
        q = _Query()
        assert q.limit(10) is q

    def test_label_returns_self(self):
        q = _Query()
        assert q.label("alias") is q

    def test_with_only_columns_returns_self(self):
        q = _Query()
        assert q.with_only_columns(["col"]) is q

    def test_order_by_returns_self(self):
        q = _Query()
        assert q.order_by("col") is q

    def test_values_returns_self(self):
        q = _Query()
        assert q.values(a=1) is q

    def test_update_returns_self(self):
        q = _Query()
        assert q.update() is q

    def test_method_chaining(self):
        q = _Query()
        result = q.where(_Condition()).join(object()).order_by("col").limit(5)
        assert result is q


class TestResult:
    def test_first_returns_provided_value(self):
        r = _Result(first_value="hello")
        assert r.first() == "hello"

    def test_first_returns_none_by_default(self):
        r = _Result()
        assert r.first() is None

    def test_all_returns_provided_list(self):
        r = _Result(all_value=[1, 2, 3])
        assert r.all() == [1, 2, 3]

    def test_all_returns_empty_list_by_default(self):
        r = _Result()
        assert r.all() == []

    def test_scalar_returns_explicit_scalar_value(self):
        r = _Result(first_value="first", scalar_value="scalar")
        assert r.scalar() == "scalar"

    def test_scalar_falls_back_to_first_value_when_scalar_not_set(self):
        r = _Result(first_value="only_first")
        assert r.scalar() == "only_first"

    def test_scalar_returns_none_when_nothing_set(self):
        r = _Result()
        assert r.scalar() is None

    def test_scalar_returns_falsy_scalar_when_explicitly_set(self):
        r = _Result(first_value="first", scalar_value=0)
        assert r.scalar() == 0

    def test_iter_yields_all_values(self):
        r = _Result(all_value=["a", "b", "c"])
        assert list(r) == ["a", "b", "c"]

    def test_iter_empty_by_default(self):
        r = _Result()
        assert list(r) == []

    def test_first_and_all_independent(self):
        r = _Result(first_value="one", all_value=["two", "three"])
        assert r.first() == "one"
        assert r.all() == ["two", "three"]

    def test_result_stores_any_type_as_first(self):
        obj = SimpleNamespace(x=1)
        r = _Result(first_value=obj)
        assert r.first() is obj
