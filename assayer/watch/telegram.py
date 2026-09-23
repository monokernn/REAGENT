"""Where a watch report lands.

One message per coin per change, never a digest of everything that stayed the
same. The shape is fixed so a holder learns to read it at a glance: what moved,
what it was and what it is now, which of their ``[alerts]`` it crossed, and the
function and block it was read from. Nothing is said that the reading cannot
point at.

The bot token and chat come from the environment, never from config.toml,
which is committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

API = "https://api.telegram.org"


@dataclass
class Report:
    asset: str
    chain: str
    address: str
    block: int | None
    changes: list[dict] = field(default_factory=list)   # diff.material(diff_snapshots(...))
    crossed: list[dict] = field(default_factory=list)   # snapshot["alerts"] where crossed
    sheet_url: str | None = None

    @property
    def quiet(self) -> bool:
        """Nothing moved and no threshold is crossed: there is no message to send."""
        return not self.changes and not self.crossed


class TelegramReporter:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("REAGENT_TELEGRAM_TOKEN")
        self.chat_id = chat_id or os.environ.get("REAGENT_TELEGRAM_CHAT")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def render(self, report: Report) -> str:
        """The message text, Telegram HTML."""
        raise NotImplementedError("watch: the report layout is not in this build yet")

    def send(self, report: Report) -> None:
        if report.quiet:
            return
        if not self.configured:
            raise RuntimeError("set REAGENT_TELEGRAM_TOKEN and REAGENT_TELEGRAM_CHAT")
        raise NotImplementedError("watch: sending is not in this build yet")
