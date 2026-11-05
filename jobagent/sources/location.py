"""Decide whether a job's location fits `search.location_mode`.

"india_remote": keep jobs located in India, and remote jobs that are open to India
(e.g. "Remote - India", "Remote - APAC", "Anywhere"). Remote jobs tied to another country
("US - Remote", "Remote, Canada") are dropped.
"""
from __future__ import annotations

import re

INDIA = [
    "india", "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune", "mumbai", "navi mumbai", "thane", "delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida", "greater noida", "faridabad",
    "ghaziabad", "kolkata", "ahmedabad", "gandhinagar", "jaipur", "chandigarh", "mohali", "kochi", "cochin",
    "thiruvananthapuram", "trivandrum", "coimbatore", "indore", "bhopal", "nagpur", "nashik", "vadodara",
    "surat", "lucknow", "bhubaneswar", "visakhapatnam", "vizag", "vijayawada", "mysore", "mysuru",
    "mangalore", "mangaluru", "goa", "dehradun", "patna", "ranchi", "raipur", "guwahati", "kanpur",
    "karnataka", "telangana", "maharashtra", "tamil nadu", "haryana", "uttar pradesh", "gujarat", "kerala",
    "west bengal", "andhra pradesh", "rajasthan", "madhya pradesh", "punjab", "odisha",
]
# Remote regions that include India.
OPEN_TO_INDIA = ["worldwide", "global", "anywhere", "apac", "asia", "asia pacific", "international"]
REMOTE_WORDS = ["remote", "work from home", "wfh", "fully remote", "remote first", "remote-first"]


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


def is_india_or_remote_india(location: str | None, source: str) -> bool:
    loc = (location or "").lower().strip()
    if not loc:
        # Naukri and our India-targeted LinkedIn searches are India-only; career sites must say where.
        return source in ("naukri", "linkedin")
    if _has(loc, INDIA):
        return True
    if _has(loc, REMOTE_WORDS):
        # Split multi-location strings ("Remote, Canada; Remote, UK") and look at what each remote part is tied to.
        for part in re.split(r"[;/|]", loc):
            rest = part
            for w in REMOTE_WORDS + ["hybrid", "-", ",", "(", ")", "only", "based"]:
                rest = rest.replace(w, " ")
            rest = rest.strip()
            if _has(part, REMOTE_WORDS) and (not rest or _has(rest, OPEN_TO_INDIA)):
                return True
        return False
    return False


def keep_location(location: str | None, source: str, mode: str) -> bool:
    if mode == "india_remote":
        return is_india_or_remote_india(location, source)
    return True
