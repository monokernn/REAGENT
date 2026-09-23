"""Watch: a coin read again and again, and told to Telegram when it moves.

A reading is a moment. Somebody holding a coin for a month wants the moment
after it too: the owner that was renounced coming back, a trading switch
appearing in an upgrade, the LP that was parked being pulled.

Nothing new is read and nothing new is judged. The coin reader already reads
the contract, ``diff_snapshots`` already says what moved between two readings,
and ``compute_alerts`` already measures a reading against the ``[alerts]`` the
holder wrote into the asset file. Watch adds a schedule, a memory of the last
reading, and somewhere to send the result.

Not wired in yet: what is here is the shape the rest is being built on.
"""

from .telegram import Report, TelegramReporter  # noqa: F401
from .loop import watch  # noqa: F401
