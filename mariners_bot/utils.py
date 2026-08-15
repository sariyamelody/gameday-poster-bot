"""Small shared helpers with no natural home elsewhere."""


def format_ordinal(n: int) -> str:
    """Format an integer as an ordinal string, e.g. 9 -> '9th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
