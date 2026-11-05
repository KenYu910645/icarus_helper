"""Present unresolved game text without inventing its runtime arguments."""
from __future__ import annotations

import re


PLACEHOLDER = re.compile(r"\{(?:\d+|[A-Za-z_]\w*)\}")


def display_mission_text(value: object) -> str:
    """Unknown arguments can be counts, health, timers or selected item names.

    Fixed, source-verified targets are filled by the catalog builder first.
    This final display fallback deliberately does not assign numeric meanings.
    """
    text = str(value)
    if not PLACEHOLDER.search(text):
        return text
    return PLACEHOLDER.sub("?", text).strip() + " [Unconfirmed values; check the current in-game objective.]"
