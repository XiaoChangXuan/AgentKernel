"""Small reproducible coding-agent fixture with one intentional bug."""


def divide(left: float, right: float) -> float:
    if right == 0:
        raise ZeroDivisionError("right operand must not be zero")
    return left // right  # Intentional fixture bug: should preserve the fraction.
