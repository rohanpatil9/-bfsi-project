from __future__ import annotations

import re
from typing import Optional


METRIC_ALIASES = {
    "revenue": [
        "revenues",
        "revenue",
        "total revenues",
        "total revenue",
    ],
    "research_and_development": [
        "research and development",
        "research & development",
        "r&d",
    ],
    "operating_income": [
        "income from operations",
        "operating income",
    ],
    "net_income": [
        "net income",
        "net earnings",
    ],
}


def clean_number(value: str) -> Optional[float]:
    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    negative = value.startswith("(") and value.endswith(")")

    value = value.replace("$", "")
    value = value.replace(",", "")
    value = value.replace("(", "")
    value = value.replace(")", "")

    try:
        number = float(value)
    except ValueError:
        return None

    return -number if negative else number


def find_metric_line(content: str, metric: str) -> Optional[str]:
    aliases = METRIC_ALIASES.get(metric, [])

    for line in content.splitlines():
        normalized = re.sub(r"\s+", " ", line.lower()).strip()

        for alias in aliases:
            if normalized.startswith(alias.lower()):
                return line

    return None


def extract_numbers_from_line(line: str) -> list[float]:
    if not line:
        return []

    # Remove percentages such as 12.5%
    line = re.sub(r"\d+(?:\.\d+)?\s*%", "", line)

    matches = re.findall(
        r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\s*\)?",
        line,
    )

    numbers = []

    for match in matches:
        number = clean_number(match)

        if number is not None:
            numbers.append(number)

    return numbers


def extract_metric_values(
    content: str,
    metric: str,
) -> Optional[tuple[float, float]]:

    line = find_metric_line(content, metric)

    if not line:
        return None

    numbers = extract_numbers_from_line(line)

    if len(numbers) < 2:
        return None

    return numbers[-2], numbers[-1]


def yoy_growth(previous: float, current: float) -> float:
    if previous == 0:
        raise ValueError("Cannot calculate growth from zero.")

    return round(
        ((current - previous) / previous) * 100,
        2,
    )


def percentage_point_difference(
    first: float,
    second: float,
) -> float:

    return round(first - second, 2)