"""Bot-wide constants.

Competition limits from https://2026-eth.optibook.net/challenge (2026-11-06).
"""

# --- Competition limits (verified against the challenge page above) -----------
# Absolute position limit per instrument, in lots. Trades that would breach it are rejected.
POSITION_LIMIT = 100
# Maximum total resting order volume per instrument (bids + asks), in lots.
MAX_OUTSTANDING_VOLUME = 200
# Exchange hard limit: insert/amend/delete requests per second across all instruments.
# Exceeding it disconnects the team.
EXCHANGE_REQUEST_LIMIT_PER_S = 25

# --- Fixed order ranges (organiser-supplied workspace guide) ------------------
MIN_ORDER_PRICE = 0.0
MAX_ORDER_PRICE = 1_000_000.0
MIN_ORDER_VOLUME = 1
MAX_ORDER_VOLUME = 100_000

# --- Our own operating parameters ----------------------------------------------
# Request budget we allow ourselves; safety margin below the exchange limit.
REQUEST_BUDGET_PER_S = 20
# Target duration of one loop iteration, in seconds. The workspace guide says to
# sleep for at least 0.2 s per loop iteration.
LOOP_INTERVAL_S = 0.2
# A book whose timestamp has not changed for this many seconds is treated as stale,
# and all our quotes on that instrument are pulled. This is a placeholder until we
# measure the real update frequency (the status line prints the book age).
BOOK_STALE_AFTER_S = 5.0
# How often to print the per-instrument status block, in seconds.
STATUS_INTERVAL_S = 1.0
# Stop the bot when the account PnL from exchange.get_pnl() falls below this (price
# units). Checked once per status interval; quotes are cancelled, positions stay.
# Our own risk parameter, not a competition limit.
MAX_LOSS = -3000.0
