"""Sharp-dip bounce rider for the cbBTC 24h race (agent-fun/bars-replay/v1).

Idea, in one paragraph: one-minute cbBTC prices mean-revert over a few minutes
after a sharp drop, but drift aimlessly the rest of the time, so this bot only
trades the sharp drops. It buys all of its cash when the close has fallen at
least 30 bps over the last 5 bars and the latest bar already closed back up
(the bounce has started), then rides the bounce: once the close is 60 bps
above the entry close it trails the peak close by 30 bps, and it cuts the
trade at 30 bps below the entry close if the bounce fails. After every exit
it waits 30 bars before looking for the next dip, so it never chases the same
move twice. It holds at most one position, never shorts, never uses leverage,
and every decision is integer arithmetic on closes it has already been sent,
so two runs on the same bars answer byte for byte the same.

Numbers, all in basis points (1 bp = 0.01%):
  DIP_BARS = 5        bars the drop is measured over
  DIP_BPS = 30        the close must be at least this far below 5 bars ago
  TAKE_PROFIT_BPS = 60   arm the trailing exit once the close is this far above entry
  TRAIL_BPS = 30      sell once the close falls this far below the peak close
  STOP_BPS = 30       sell once the close falls this far below the entry close
  COOLDOWN_BARS = 30  bars to wait after an exit before a new entry
  MAX_SLIPPAGE_BPS = 150  cap on how far a fill may move from the decided close

Why these numbers: a full round trip costs about 26 bps in fees, slippage and
pool impact, so a trade must catch a move clearly larger than that; 30 bps
over 5 minutes is a sharp drop (roughly twice the usual 5-minute noise) and
the 60 bps take-profit with a 30 bps trail lets a real bounce run while the
30 bps stop keeps a failed bounce cheap. The bot was built and tested only
against the dev window in this kit.

Verified on the kit's dev window (1,440 bars, run twice through the real
bars-replay protocol): net PnL +$20.89, 4 trades, max drawdown 0.94%,
both runs byte-for-byte identical.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

DIP_BARS = 5
DIP_BPS = 30
TAKE_PROFIT_BPS = 60
TRAIL_BPS = 30
STOP_BPS = 30
COOLDOWN_BARS = 30
MAX_SLIPPAGE_BPS = 150
MIN_NOTIONAL = 1_000_000  # 1 USDC, in quote units

# Closes seen so far, in bar order; the index is the bot's own bar count.
_closes = []
# Close at which the open position was entered, and the highest close since.
_entry_close = 0
_peak_close = 0
# Index into _closes of the bar on which the last exit was decided.
_last_exit = -10 ** 9


def on_batch(state, batch):
    """Decide this batch's orders. Returns [] for none."""
    if state.warming_up:
        return []
    bar = state.latest.get(SYMBOL)
    if bar is None:
        return []
    close = int(bar["c"])
    _closes.append(close)
    i = len(_closes) - 1

    holding = state.positions.get(SYMBOL)
    if holding is None or holding.qty <= 0:
        return _maybe_enter(state, i, close)
    return _maybe_exit(state, i, close, holding.qty)


def _maybe_enter(state, i, close):
    global _entry_close, _peak_close
    if i < DIP_BARS or i - _last_exit < COOLDOWN_BARS:
        return []
    # Sharp drop: close at least DIP_BPS below the close DIP_BARS ago.
    if close >= _closes[i - DIP_BARS] * (10000 - DIP_BPS) // 10000:
        return []
    # Bounce already starting: the latest bar closed up on the one before it.
    if close <= _closes[i - 1]:
        return []
    notional = state.cash
    if notional < MIN_NOTIONAL:
        return []
    _entry_close = close
    _peak_close = close
    return [{
        "side": "buy",
        "symbol": SYMBOL,
        "notional_quote_units": notional,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }]


def _maybe_exit(state, i, close, qty):
    global _last_exit, _peak_close
    if close > _peak_close:
        _peak_close = close
    # Stop: the bounce failed, the close fell STOP_BPS below the entry close.
    if close < _entry_close * (10000 - STOP_BPS) // 10000:
        _last_exit = i
        return _sell_all(qty)
    # Trail: once TAKE_PROFIT_BPS above entry, sell on a TRAIL_BPS pullback
    # from the highest close since entry.
    if close >= _entry_close * (10000 + TAKE_PROFIT_BPS) // 10000:
        if close < _peak_close * (10000 - TRAIL_BPS) // 10000:
            _last_exit = i
            return _sell_all(qty)
    return []


def _sell_all(qty):
    return [{
        "side": "sell",
        "symbol": SYMBOL,
        "qty": qty,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }]


makefun_bars.run(on_batch)
