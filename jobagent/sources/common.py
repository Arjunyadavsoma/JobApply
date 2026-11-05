from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from rich.logging import RichHandler

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler(show_path=False)])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("jobagent")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
)


def html_to_text(markup: str) -> str:
    if not markup:
        return ""
    soup = BeautifulSoup(markup, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
