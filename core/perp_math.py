"""Perpetual-futures math — isolated-margin liquidation price, unrealized PnL,
and the equity contribution of a position (spot or perp).

These are the primitives the whole agent uses to (a) size perps so a single
liquidation can never breach the competition's ~30% max-drawdown disqualification
gate, and (b) value the portfolio correctly when perp positions are open.

Conventions
-----------
A perp position stores:
  - ``invested``  = USDT margin posted (collateral at risk)
  - ``size``      = notional token units = margin * leverage / entry_price
  - ``direction`` = "long" | "short"
  - ``leverage``  = e.g. 3.0
A spot position stores ``size`` token units and ``invested`` USDT cost; it is
equivalent to a 1x long whose "margin" equals its full notional.

All functions are pure and never raise on bad input (they clamp / return 0.0),
so they are safe to call from the hot monitor loop.
"""
from __future__ import annotations

# Maintenance-margin rate assumed for the venue (Aster/Hyperliquid isolated).
# Conservative 0.5% — pulls the computed liquidation price slightly *closer* to
# entry than a zero-MMR model, which is the safe direction for risk control.
DEFAULT_MMR = 0.005


def notional_units(margin_usd: float, leverage: float, entry_price: float) -> float:
    """Token units controlled by a perp = margin * leverage / entry."""
    if entry_price <= 0 or margin_usd <= 0 or leverage <= 0:
        return 0.0
    return (margin_usd * leverage) / entry_price


def liquidation_price(entry_price: float, leverage: float, direction: str,
                      mmr: float = DEFAULT_MMR) -> float:
    """Isolated-margin liquidation price.

    long  -> entry * (1 - 1/lev + mmr)   (price falling wipes the margin)
    short -> entry * (1 + 1/lev - mmr)   (price rising wipes the margin)

    At 3x this is ~33% away from entry, so a strategy stop of a few percent
    exits long before liquidation — liquidation is a backstop, not an exit.
    """
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    inv = 1.0 / leverage
    if str(direction).lower() == "short":
        return entry_price * (1.0 + inv - mmr)
    return entry_price * max(0.0, 1.0 - inv + mmr)


def unrealized_pnl(direction: str, size_units: float, entry_price: float,
                   mark_price: float) -> float:
    """USD unrealized PnL of a position.

    long  -> size * (mark - entry)
    short -> size * (entry - mark)
    For spot (1x long) this is exactly size*(mark-entry).
    """
    if size_units <= 0 or entry_price <= 0 or mark_price <= 0:
        return 0.0
    if str(direction).lower() == "short":
        return size_units * (entry_price - mark_price)
    return size_units * (mark_price - entry_price)


def pnl_pct_on_margin(direction: str, entry_price: float, mark_price: float,
                      leverage: float) -> float:
    """Return on posted margin, in percent (leverage-amplified price move).

    A 6% adverse price move at 3x = -18% on margin. This is what the user
    actually gains/loses on collateral; the raw price move drives stops/TPs.
    """
    if entry_price <= 0 or mark_price <= 0:
        return 0.0
    price_move = (mark_price - entry_price) / entry_price
    if str(direction).lower() == "short":
        price_move = -price_move
    return price_move * leverage * 100.0


def perp_equity(margin_usd: float, direction: str, size_units: float,
                entry_price: float, mark_price: float) -> float:
    """Equity contribution of a perp = margin + unrealized PnL, floored at 0
    (a fully-liquidated perp contributes nothing, never negative)."""
    upnl = unrealized_pnl(direction, size_units, entry_price, mark_price)
    return max(0.0, float(margin_usd) + upnl)


def position_equity(pos, mark_price: float) -> float:
    """Equity (USD) contributed by a Position-like object at ``mark_price``.

    Perp  -> margin + directional uPnL (floored at 0).
    Spot  -> size * price (token units valued at market).
    Falls back to ``invested`` when price is unavailable.
    """
    price = float(mark_price) if mark_price and mark_price > 0 else 0.0
    is_perp = bool(getattr(pos, "is_perp", False))
    if is_perp:
        if price <= 0:
            return float(getattr(pos, "margin_usd", 0.0) or getattr(pos, "invested", 0.0))
        return perp_equity(
            getattr(pos, "margin_usd", 0.0) or getattr(pos, "invested", 0.0),
            getattr(pos, "direction", "long"),
            getattr(pos, "size", 0.0),
            getattr(pos, "entry_price", 0.0),
            price,
        )
    # spot
    if price <= 0:
        return float(getattr(pos, "invested", 0.0))
    return float(getattr(pos, "size", 0.0)) * price


def liquidation_distance_pct(pos, mark_price: float) -> float:
    """How far (percent of price) the mark is from the liquidation price.
    Small positive = danger. Returns a large number when not a perp / unknown."""
    if not getattr(pos, "is_perp", False):
        return 1e9
    liq = float(getattr(pos, "liquidation_price", 0.0) or 0.0)
    if liq <= 0 or not mark_price or mark_price <= 0:
        return 1e9
    return abs(mark_price - liq) / mark_price * 100.0
