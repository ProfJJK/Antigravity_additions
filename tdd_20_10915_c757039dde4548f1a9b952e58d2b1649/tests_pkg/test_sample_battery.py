"""Battery for the sample module."""


def test_sorting_orders_ascending() -> None:
    assert sorted([3, 1, 2]) == [1, 2, 3]


def test_string_reversal() -> None:
    assert "abc"[::-1] == "cba"


def test_dict_lookup() -> None:
    table = {"a": 1, "b": 2}
    assert table["b"] - table["a"] == 1
