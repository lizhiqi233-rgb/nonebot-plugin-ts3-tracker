from __future__ import annotations


def parse_delimited_list(raw: str) -> list[str]:
    normalized = raw.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return [item.strip() for item in normalized.split("\n") if item.strip()]
