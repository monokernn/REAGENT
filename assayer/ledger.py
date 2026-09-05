"""Spend accounting with atomic writes.

Only one phase in this program costs money, so the ledger has one job: make it
impossible to spend more than the config allows, and impossible to lose a
recorded spend to a concurrent writer. Read-modify-write happens under a lock
file, and every write lands via ``os.replace``.

Two limits, both from ``[budget]``:

``max_usd_per_day``
    refuses the next extraction once the day's total reaches it.

``max_usd_per_extraction``
    a runaway call cannot be interrupted after the fact, so its cost is recorded
    and the *next* extraction is refused until a human looks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import util


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class Budget:
    day: str
    spent_today: float
    total: float
    calls_today: int
    last_extraction_usd: float


def _empty() -> dict:
    return {"days": {}, "total_usd": 0.0, "last_extraction_usd": 0.0,
            "entries": 0}


def _read(p: util.Paths) -> dict:
    if not p.budget.exists():
        return _empty()
    try:
        data = util.read_json(p.budget)
    except (ValueError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("days", {})
    data.setdefault("total_usd", 0.0)
    data.setdefault("last_extraction_usd", 0.0)
    data.setdefault("entries", 0)
    return data


def state(p: util.Paths, now: datetime | None = None) -> Budget:
    now = now or util.utc_now()
    day = now.strftime("%Y-%m-%d")
    data = _read(p)
    today = (data.get("days") or {}).get(day) or {}
    return Budget(day=day, spent_today=float(today.get("usd", 0.0)),
                  total=float(data.get("total_usd", 0.0)),
                  calls_today=int(today.get("calls", 0)),
                  last_extraction_usd=float(data.get("last_extraction_usd", 0.0)))


def check(p: util.Paths, config: dict, now: datetime | None = None) -> Budget:
    """Refuse the next paid call if either limit is already reached."""
    budget_config = config.get("budget") or {}
    per_day = float(budget_config.get("max_usd_per_day", 0.0) or 0.0)
    per_call = float(budget_config.get("max_usd_per_extraction", 0.0) or 0.0)
    current = state(p, now)
    if per_day and current.spent_today >= per_day:
        raise BudgetExceeded(
            "daily_limit_reached",
            f"spent ${current.spent_today:.4f} today, limit ${per_day:.2f}")
    if per_call and current.last_extraction_usd > per_call:
        raise BudgetExceeded(
            "previous_extraction_over_limit",
            f"the last extraction cost ${current.last_extraction_usd:.4f}, "
            f"limit ${per_call:.2f} — raise the limit or clear "
            f"last_extraction_usd in state/budget.json")
    return current


def record(p: util.Paths, usd: float, *, asset: str, kind: str = "extraction",
           detail: str | None = None, now: datetime | None = None) -> Budget:
    """Add a spend. Safe against concurrent writers."""
    now = now or util.utc_now()
    day = now.strftime("%Y-%m-%d")
    usd = float(usd)
    p.state.mkdir(parents=True, exist_ok=True)
    with util.FileLock(p.state / "budget.json.lock"):
        data = _read(p)
        days = data.setdefault("days", {})
        today = days.setdefault(day, {"usd": 0.0, "calls": 0})
        today["usd"] = round(float(today.get("usd", 0.0)) + usd, 8)
        today["calls"] = int(today.get("calls", 0)) + 1
        data["total_usd"] = round(float(data.get("total_usd", 0.0)) + usd, 8)
        data["entries"] = int(data.get("entries", 0)) + 1
        if kind == "extraction":
            data["last_extraction_usd"] = usd
        data["updated_at"] = util.iso(now)
        util.write_json(p.budget, data)
    util.log_event(p, "spend", asset=asset, kind=kind, usd=usd, detail=detail)
    return state(p, now)


def clear_last_extraction(p: util.Paths) -> None:
    """Acknowledge an over-limit call so the next one may proceed."""
    with util.FileLock(p.state / "budget.json.lock"):
        data = _read(p)
        data["last_extraction_usd"] = 0.0
        util.write_json(p.budget, data)
