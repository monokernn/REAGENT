"""The cycle: snapshot, subtract, report, sleep.

A target is an asset file, the same one ``assayer snapshot`` reads, so its
``pool`` and ``[alerts]`` come along with it. Each pass takes a snapshot the way
the CLI does, subtracts the previous one with ``diff_snapshots``, keeps only
what ``material`` keeps, and hands that and the crossed alerts to the reporter.

The first snapshot of a target is a baseline and sends nothing. A read that
fails is not a change: an RPC that times out leaves the previous snapshot
standing and is retried on the next pass, it never becomes "ownership changed
to unknown".
"""

from __future__ import annotations

from .telegram import TelegramReporter


def watch(config: dict, assets: list[str], reporter: TelegramReporter, *,
          interval_s: int = 900, once: bool = False) -> None:
    """Keep ``assets`` under watch until stopped, or for one pass with ``once``."""
    raise NotImplementedError("watch: the loop is not in this build yet")
