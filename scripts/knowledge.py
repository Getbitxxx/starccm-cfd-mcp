"""Search a curated index; titles and URLs are never treated as validated code."""
from __future__ import annotations

import json
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "knowledge" / "index.json"


def search_knowledge(query: str, star_version: str = "2502", limit: int = 8) -> list[dict]:
    if not query.strip() or not 1 <= limit <= 20:
        raise ValueError("query must be nonempty and limit must be 1-20")
    words = [word.casefold() for word in query.split()]
    entries = json.loads(INDEX.read_text(encoding="utf-8"))["entries"]
    ranked = []
    for entry in entries:
        body = " ".join(str(entry.get(key, "")) for key in ("title", "tags", "description")).casefold()
        score = sum(2 if word in str(entry["title"]).casefold() else 1 for word in words if word in body)
        if score and (entry.get("star_version") in (None, star_version)):
            ranked.append((score, entry))
    return [entry for _, entry in sorted(ranked, key=lambda item: (-item[0], item[1]["title"]))[:limit]]
