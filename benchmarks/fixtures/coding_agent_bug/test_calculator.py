from calculator import divide


def test_divide_preserves_fraction() -> None:
    assert divide(5, 2) == 2.5


def test_divide_rejects_zero() -> None:
    try:
        divide(1, 0)
    except ZeroDivisionError:
        pass
    else:
        raise AssertionError("divide must reject zero")
