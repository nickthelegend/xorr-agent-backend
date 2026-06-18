import os
import uuid
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from core.types import Candle, MarketContext, Signal, Quote
from strategies.registry import STRATEGIES
from filters.regime import is_actionable
import filters.confluence_score as confluence_score
import filters.volume_gate as volume_gate
import filters.whale_netflow as whale_netflow
from backtest.data_loader import get_klines_for_backtest
from config import settings

logger = logging.getLogger("xorr.backtest.engine")

# Realistic per-leg trading costs for a small ($1-2) swap of a LIQUID large-cap on
# PancakeSwap: negligible price impact (~10 bps realized slippage) + 0.25% pool fee.
# (The old model charged 50 bps slippage/leg = 1.5% round-trip, which is unrealistic
# for liquid tokens and made every non-runaway trade a guaranteed cost-loss.)
BT_SLIPPAGE = 0.001   # 10 bps realized slippage per leg
BT_FEE = 0.0025       # 0.25% PancakeSwap pool fee per leg

STRATEGY_ACCURACY = {
    "momentum_pullback": 0.75,
    "fib_golden_pocket": 0.72,
    "capitulation": 0.80,
    "news_catalyst": 0.85,
    "mean_reversion": 0.74,
    "trend_follow": 0.70,
    "vol_squeeze": 0.73,
    "whale_flow": 0.76,
    "donchian_breakout": 0.72,
    "rsi_reversion": 0.74,
    "xsect_momentum": 0.72,
}

@dataclass
class BacktestPosition:
    id: str
    symbol: str
    entry_time_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float
    invested: float
    strategy: str
    max_hold_min: int
    tp1_price: float = 0.0
    tp1_hit: bool = False
    highest_price: float = 0.0
    atr: float = 0.0
    is_shadow: bool = False

@dataclass
class BacktestTrade:
    id: str
    symbol: str
    strategy: str
    opened_at: datetime
    closed_at: datetime
    invested: float
    pnl_usd: float
    pnl_pct: float
    hold_minutes: float
    exit_reason: str
    status: str  # "win" | "loss" | "breakeven"
    r_realized: float
    is_shadow: bool = False

class BacktestEngine:
    def __init__(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime,
        trading_start_time: datetime = None,
        quality_mode: bool = True,
        confluence_threshold: int = 78,
        enabled_strategies: List[str] = None,
        base_trade_size: float = 2.0,
        progress_callback = None
    ):
        self.symbols = [s.upper() for s in symbols]
        self.start_time = start_time
        self.end_time = end_time
        self.trading_start_time = trading_start_time or start_time
        self.quality_mode = quality_mode
        self.confluence_threshold = confluence_threshold
        self.enabled_strategies = enabled_strategies or list(STRATEGIES.keys())
        self.base_trade_size = base_trade_size
        self.progress_callback = progress_callback

        # Replay state
        self.cash = 100.0  # Simulated initial balance
        self.current_equity = 100.0
        self.peak_equity = 100.0
        self.open_positions: List[BacktestPosition] = []
        self.closed_trades: List[BacktestTrade] = []
        
        # Cooldowns
        self.cooldowns: Dict[str, int] = {}  # symbol -> expiry timestamp ms
        
        # Strategy stats for Arbiter v2
        # strategy -> list of R_realized values
        self.strategy_real_trades: Dict[str, List[float]] = {name: [] for name in STRATEGIES.keys()}
        self.strategy_shadow_trades: Dict[str, List[float]] = {name: [] for name in STRATEGIES.keys()}
        self.suspended_strategies: Set[str] = set()
        
        # Learning loop weights
        self.strategy_weights = {name: 1.0 for name in STRATEGIES.keys()}
        
        # Data caches
        self.data_5m: Dict[str, pd.DataFrame] = {}
        self.data_1h: Dict[str, pd.DataFrame] = {}
        self.data_1d: Dict[str, pd.DataFrame] = {}
        self.btc_1d: Optional[pd.DataFrame] = None

    async def load_data(self):
        """Loads and caches all necessary historical data from Binance."""
        days = (self.end_time - self.start_time).days + 10  # Include a warm-up buffer
        
        # Always fetch BTCUSDT daily candles for regime classification
        self.btc_1d = await get_klines_for_backtest("BTCUSDT", "1d", days=days)
        
        # Load for all tokens
        for sym in self.symbols:
            df_5m = await get_klines_for_backtest(sym, "5m", days=days)
            if not df_5m.empty:
                self.data_5m[sym] = df_5m
                
            df_1h = await get_klines_for_backtest(sym, "1h", days=days)
            if not df_1h.empty:
                self.data_1h[sym] = df_1h
                
            df_1d = await get_klines_for_backtest(sym, "1d", days=days)
            if not df_1d.empty:
                self.data_1d[sym] = df_1d

    def _prepare_fast(self):
        """Precompute Candle lists + sorted timestamp arrays so the hot loop can
        slice history with O(log n) searchsorted instead of O(n) DataFrame masks.
        This is the difference between a backtest taking minutes vs. tens of minutes."""
        def conv(df):
            if df is None or df.empty:
                return [], np.array([], dtype=np.int64)
            ot = df["open_time"].to_numpy(dtype=np.int64)
            o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy()
            c = df["close"].to_numpy(); v = df["volume"].to_numpy()
            candles = [
                Candle(
                    ts=datetime.fromtimestamp(int(ot[i]) / 1000.0, timezone.utc),
                    open=float(o[i]), high=float(h[i]), low=float(l[i]),
                    close=float(c[i]), volume=float(v[i]),
                )
                for i in range(len(ot))
            ]
            return candles, ot

        self._c5, self._t5, self._idx5, self._close5, self._cumvol5 = {}, {}, {}, {}, {}
        self._c1h, self._t1h = {}, {}
        self._c1d, self._t1d = {}, {}
        for sym, df in self.data_5m.items():
            candles, ot = conv(df)
            self._c5[sym] = candles
            self._t5[sym] = ot
            self._idx5[sym] = {int(t): i for i, t in enumerate(ot)}
            self._close5[sym] = df["close"].to_numpy(dtype=float)
            self._cumvol5[sym] = np.cumsum(df["volume"].to_numpy(dtype=float))
        for sym, df in self.data_1h.items():
            candles, ot = conv(df)
            self._c1h[sym] = candles
            self._t1h[sym] = ot
        for sym, df in self.data_1d.items():
            candles, ot = conv(df)
            self._c1d[sym] = candles
            self._t1d[sym] = ot
        self._btc_c1d, self._btc_t1d = conv(self.btc_1d)

    def get_equity(self, ts_ms: int, quotes: Dict[str, Quote]) -> float:
        """Calculates total portfolio value including open positions."""
        val = self.cash
        for pos in self.open_positions:
            if pos.is_shadow:
                continue
            quote = quotes.get(pos.symbol)
            if quote:
                val += pos.size * quote.price
            else:
                val += pos.invested
        return val

    def rebalance_weights_sim(self):
        """Rebalances weights based on backtest history."""
        # Group stats by strategy
        for strat in self.enabled_strategies:
            real_trades = [t for t in self.closed_trades if t.strategy == strat and not t.is_shadow]
            # Take last 50
            recent = real_trades[-50:]
            if not recent:
                self.strategy_weights[strat] = 1.0
            else:
                E = sum(t.r_realized for t in recent) / len(recent)
                weight = max(0.1, min(2.0, 1.0 + E))
                self.strategy_weights[strat] = round(weight, 3)

    def evaluate_arbiter_v2(self, strategy: str):
        """Applies Arbiter v2 auto-suspend and revive rules."""
        # 1. Check Suspend
        real_r = self.strategy_real_trades[strategy]
        if len(real_r) >= 10:
            window = real_r[-20:]
            E = sum(window) / len(window)
            if E < 0.0:
                # diversity floor: keep at least 3 strategies active
                active_count = len(self.enabled_strategies) - len(self.suspended_strategies)
                if active_count > 3:
                    if strategy not in self.suspended_strategies:
                        self.suspended_strategies.add(strategy)
                        logger.info(f"[ARBITER] Suspended strategy {strategy} (Expectancy={E:.2f}R)")

        # 2. Check Revive — require a sustained positive shadow run (8 trades, >0.25R)
        if strategy in self.suspended_strategies:
            shadow_r = self.strategy_shadow_trades[strategy]
            if len(shadow_r) >= 8:
                window_shadow = shadow_r[-8:]
                shadow_E = sum(window_shadow) / len(window_shadow)
                if shadow_E > 0.25:
                    self.suspended_strategies.remove(strategy)
                    # Clear history to promote new baseline (as specified in compacted context)
                    self.strategy_real_trades[strategy].clear()
                    self.strategy_shadow_trades[strategy].clear()
                    logger.info(f"[ARBITER] Revived strategy {strategy} (Shadow Expectancy={shadow_E:.2f}R)")

    async def run(self) -> List[BacktestTrade]:
        """Runs the backtest simulation."""
        # Find all 5m timestamps overlapping in our loaded data
        if not self.data_5m:
            logger.error("No data loaded for backtest")
            return []
            
        # Get start/end timestamps in ms
        start_ms = int(self.start_time.timestamp() * 1000)
        end_ms = int(self.end_time.timestamp() * 1000)
        
        # Get list of unique 5m open times sorted
        all_timestamps = set()
        for df in self.data_5m.values():
            ts = df[(df["open_time"] >= start_ms) & (df["open_time"] <= end_ms)]["open_time"].tolist()
            all_timestamps.update(ts)
            
        timestamps = sorted(list(all_timestamps))
        if not timestamps:
            logger.error("No overlapping timestamps found in backtest range")
            return []
            
        total_steps = len(timestamps)
        logger.info(f"Starting backtest with {total_steps} steps...")

        # Precompute fast-access arrays/lists
        self._prepare_fast()

        # Override fetch_binance_klines
        import data.binance_klines as bk

        # Fast fetcher: slice the precomputed Candle list at the searchsorted index
        async def mock_fetch(symbol: str, interval: str = "5m", limit: int = 100):
            sym = symbol.upper()
            if interval == "5m":
                candles = self._c5.get(sym); ts = self._t5.get(sym)
            elif interval == "1h":
                candles = self._c1h.get(sym); ts = self._t1h.get(sym)
            elif interval == "1d":
                candles = self._c1d.get(sym); ts = self._t1d.get(sym)
                if candles is None:
                    candles = self._btc_c1d; ts = self._btc_t1d
            else:
                candles = None; ts = None

            if not candles or ts is None or len(ts) == 0:
                return []
            # number of bars with open_time <= current_ts
            idx = int(np.searchsorted(ts, current_ts, side="right"))
            if idx <= 0:
                return []
            return candles[max(0, idx - limit):idx]

        # Apply override
        bk._backtest_fetch_override = mock_fetch
        
        # Also mock get_cached_mcp_skill
        import data.cmc_mcp as cmc_mcp
        async def mock_mcp(unique_name: str, parameters: Optional[dict] = None, client = None, ttl_minutes = 30):
            if "whale" in unique_name or "transfer" in unique_name:
                return {"net_flow": 75000.0}
            return {}
        cmc_mcp._backtest_mcp_override = mock_mcp

        # Mock CEX sanity to always pass (capture original so we can restore it;
        # otherwise an in-process backtest would permanently disable the live check)
        import filters.cex_sanity as cex_sanity
        _orig_passes_cex_sanity = cex_sanity.passes_cex_sanity
        async def mock_cex_sanity(symbol: str, cmc_price: float) -> bool:
            return True
        cex_sanity.passes_cex_sanity = mock_cex_sanity

        # Align global quality mode with this run so strategies' gate_threshold() matches
        _orig_quality_mode = settings.quality_mode
        settings.quality_mode = self.quality_mode

        try:
            for step, current_ts in enumerate(timestamps):
                current_dt = datetime.fromtimestamp(current_ts / 1000.0, timezone.utc)
                
                # Report progress
                if self.progress_callback and step % 50 == 0:
                    pct = int((step / total_steps) * 100)
                    default_sym = self.symbols[0] if self.symbols else ""
                    self.progress_callback(pct, len(self.closed_trades), default_sym)

                # --- 1. Synthesize Quotes & Market Context ---
                quotes: Dict[str, Quote] = {}
                for sym in self.symbols:
                    i = self._idx5.get(sym, {}).get(current_ts)
                    if i is None:
                        continue
                    closes = self._close5[sym]
                    cumvol = self._cumvol5[sym]
                    close_p = float(closes[i])

                    pct_1h = 0.0
                    if i >= 12 and closes[i - 12] > 0:
                        pct_1h = ((close_p - closes[i - 12]) / closes[i - 12]) * 100.0

                    pct_24h = 0.0
                    if i >= 288 and closes[i - 288] > 0:
                        pct_24h = ((close_p - closes[i - 288]) / closes[i - 288]) * 100.0

                    # trailing 288-bar (24h) volume via cumulative sum
                    lo = max(0, i - 287)
                    window_vol = float(cumvol[i] - (cumvol[lo - 1] if lo > 0 else 0.0))
                    vol_24h = window_vol * close_p  # USD approximate

                    quotes[sym] = Quote(
                        symbol=sym,
                        price=close_p,
                        pct_1h=pct_1h,
                        pct_24h=pct_24h,
                        volume_24h=vol_24h,
                        market_cap=vol_24h * 10,  # rough MC fallback
                        last_updated=current_dt
                    )
                            
                # Unified Market Context
                # Regime classification from daily BTCUSDT
                regime_str = "CHOP"
                if self.btc_1d is not None:
                    # Filter daily BTC up to current timestamp
                    btc_df = self.btc_1d[self.btc_1d["open_time"] <= current_ts].tail(30)
                    if len(btc_df) >= 2:
                        btc_closes = btc_df["close"].tolist()
                        curr_c = btc_closes[-1]
                        prev_c = btc_closes[-2]
                        btc_return = (curr_c - prev_c) / prev_c
                        
                        # 20 EMA
                        ema_20 = float(btc_df["close"].ewm(span=20, adjust=False).mean().iloc[-1])
                        
                        # Mock F&G to 50
                        if curr_c > ema_20 and btc_return > 0.005:
                            regime_str = "TREND_UP"
                        elif curr_c < ema_20 and btc_return < -0.005:
                            regime_str = "TREND_DOWN"
                            
                ctx = MarketContext(
                    timestamp=current_dt,
                    fear_greed_value=50,
                    fear_greed_label="Neutral",
                    btc_dominance=55.0,
                    total_market_cap_usd=2.5e12,
                    total_market_cap_change_24h=1.5,
                    bnb_price_usd=600.0,
                    quotes=quotes,
                    open_positions=[],
                    regime=regime_str
                )

                # --- 2. Monitor Exits ---
                still_open = []
                for pos in self.open_positions:
                    i = self._idx5.get(pos.symbol, {}).get(current_ts)
                    if i is None:
                        still_open.append(pos)
                        continue

                    candle = self._c5[pos.symbol][i]
                    low = candle.low
                    high = candle.high
                    close = candle.close
                    
                    hold_min = (current_ts - pos.entry_time_ms) / 60000.0
                    
                    exit_triggered = False
                    exit_reason = ""
                    exit_price = close
                    
                    # Track highest price seen
                    pos.highest_price = max(pos.highest_price, high)
                    pnl_pct = ((close - pos.entry_price) / pos.entry_price) * 100.0
                    
                    # A. Hard time stop
                    if hold_min >= pos.max_hold_min:
                        exit_triggered = True
                        exit_reason = "MAX_HOLD_TIME"
                        exit_price = close
                        
                    # B. Stop Loss (SL)
                    elif low <= pos.stop_loss and not pos.tp1_hit:
                        exit_triggered = True
                        exit_reason = "SL_HIT"
                        exit_price = pos.stop_loss
                        
                    # C. Take Profit (TP)
                    elif pos.take_profit > 0 and high >= pos.take_profit and not pos.tp1_hit:
                        exit_triggered = True
                        exit_reason = "TP_HIT"
                        exit_price = pos.take_profit
                        
                    # D. Trailing/Profit locks per strategy
                    elif pos.strategy == "momentum_pullback":
                        if pnl_pct >= 2.0 and not pos.tp1_hit:
                            pos.tp1_hit = True
                            pos.stop_loss = close * 0.985
                        elif pos.tp1_hit:
                            peak_trail = pos.highest_price * 0.985
                            pos.stop_loss = max(pos.stop_loss, peak_trail)
                            if low <= pos.stop_loss:
                                exit_triggered = True
                                exit_reason = "TRAIL_STOP_PROFIT"
                                exit_price = pos.stop_loss
                                
                    elif pos.strategy == "mean_reversion":
                        if high >= pos.tp1_price and not pos.tp1_hit:
                            pos.tp1_hit = True
                            # Realize 50% at tp1_price
                            # size is halved, invested is halved
                            half_size = pos.size * 0.5
                            half_invested = pos.invested * 0.5
                            
                            # Exit half
                            exit_usd = half_size * pos.tp1_price * (1.0 - BT_SLIPPAGE) * (1.0 - BT_FEE)
                            realized_pnl = exit_usd - half_invested
                            realized_pct = (realized_pnl / half_invested) * 100.0
                            
                            initial_risk = half_invested * (0.004) # SL is 0.4%
                            r_val = realized_pnl / initial_risk if initial_risk > 0 else 0.0
                            
                            trade = BacktestTrade(
                                id=pos.id + "_tp1",
                                symbol=pos.symbol,
                                strategy=pos.strategy,
                                opened_at=datetime.fromtimestamp(pos.entry_time_ms / 1000.0, timezone.utc),
                                closed_at=current_dt,
                                invested=half_invested,
                                pnl_usd=round(realized_pnl, 4),
                                pnl_pct=round(realized_pct, 2),
                                hold_minutes=round(hold_min, 1),
                                exit_reason="TP1_HALF",
                                status="win" if realized_pnl > 0 else "loss",
                                r_realized=round(r_val, 2),
                                is_shadow=pos.is_shadow
                            )
                            self.closed_trades.append(trade)
                            
                            # Update statistics
                            if not pos.is_shadow:
                                self.cash += exit_usd
                                self.strategy_real_trades[pos.strategy].append(r_val)
                            else:
                                self.strategy_shadow_trades[pos.strategy].append(r_val)
                                
                            self.evaluate_arbiter_v2(pos.strategy)
                            
                            # Update active position
                            pos.size = half_size
                            pos.invested = half_invested
                            
                        # Check exit for remaining half
                        if low <= pos.stop_loss:
                            exit_triggered = True
                            exit_reason = "SL_HIT"
                            exit_price = pos.stop_loss
                        elif high >= pos.take_profit:
                            exit_triggered = True
                            exit_reason = "TP_HIT"
                            exit_price = pos.take_profit
                            
                    elif pos.strategy == "trend_follow":
                        if pos.atr > 0 and high >= pos.entry_price + pos.atr:
                            pos.tp1_hit = True  # use tp1_hit to track trailing start
                        if pos.tp1_hit:
                            trailing_stop = pos.highest_price - 1.2 * pos.atr
                            pos.stop_loss = max(pos.stop_loss, trailing_stop)
                            if low <= pos.stop_loss:
                                exit_triggered = True
                                exit_reason = "TRAIL_STOP_PROFIT"
                                exit_price = pos.stop_loss
                                
                    elif pos.strategy == "whale_flow":
                        if pnl_pct >= 2.0:
                            pos.tp1_hit = True
                        if pos.tp1_hit:
                            trailing_stop = pos.highest_price * 0.985
                            pos.stop_loss = max(pos.stop_loss, trailing_stop)
                            if low <= pos.stop_loss:
                                exit_triggered = True
                                exit_reason = "TRAIL_STOP_PROFIT"
                                exit_price = pos.stop_loss

                    elif pos.strategy in ("donchian_breakout", "xsect_momentum"):
                        # Once +2.5% in profit, move stop to breakeven+ and trail 3%
                        # below the peak so a breakout can run while protecting gains.
                        if pnl_pct >= 2.5 and not pos.tp1_hit:
                            pos.tp1_hit = True
                            pos.stop_loss = max(pos.stop_loss, pos.entry_price * 1.005)
                        if pos.tp1_hit:
                            trailing_stop = pos.highest_price * 0.97
                            pos.stop_loss = max(pos.stop_loss, trailing_stop)
                            if low <= pos.stop_loss:
                                exit_triggered = True
                                exit_reason = "TRAIL_STOP_PROFIT"
                                exit_price = pos.stop_loss

                    # E. Stagnation check
                    if not exit_triggered and hold_min > 45.0 and abs(pnl_pct) < 0.2 and not pos.tp1_hit:
                        exit_triggered = True
                        exit_reason = "STAGNATION_EXIT"
                        exit_price = close
                        
                    if exit_triggered:
                        # Close position
                        exit_usd = pos.size * exit_price * (1.0 - BT_SLIPPAGE) * (1.0 - BT_FEE)
                        pnl_usd = exit_usd - pos.invested
                        pnl_pct = (pnl_usd / pos.invested) * 100.0 if pos.invested > 0 else 0.0
                        
                        # Initial risk = invested * (stop_loss_pct / 100)
                        # Let's approximate stop loss pct from entry to stop loss
                        sl_diff = abs(pos.entry_price - pos.stop_loss)
                        initial_risk = pos.invested * (sl_diff / pos.entry_price)
                        r_val = pnl_usd / initial_risk if initial_risk > 0 else 0.0
                        
                        trade = BacktestTrade(
                            id=pos.id,
                            symbol=pos.symbol,
                            strategy=pos.strategy,
                            opened_at=datetime.fromtimestamp(pos.entry_time_ms / 1000.0, timezone.utc),
                            closed_at=current_dt,
                            invested=pos.invested,
                            pnl_usd=round(pnl_usd, 4),
                            pnl_pct=round(pnl_pct, 2),
                            hold_minutes=round(hold_min, 1),
                            exit_reason=exit_reason,
                            status="win" if pnl_usd > 0 else "loss",
                            r_realized=round(r_val, 2),
                            is_shadow=pos.is_shadow
                        )
                        self.closed_trades.append(trade)
                        
                        if not pos.is_shadow:
                            self.cash += exit_usd
                            self.strategy_real_trades[pos.strategy].append(r_val)
                            # Apply cooldown
                            cooldown_min = 45.0 if pnl_usd > 0 else (360.0 if hold_min <= 10.0 else 180.0)
                            self.cooldowns[pos.symbol] = current_ts + int(cooldown_min * 60000.0)
                        else:
                            self.strategy_shadow_trades[pos.strategy].append(r_val)
                            
                        self.evaluate_arbiter_v2(pos.strategy)
                    else:
                        still_open.append(pos)
                        
                self.open_positions = still_open

                # --- 3. Evaluate Entries ---
                if current_dt >= self.trading_start_time:
                    # Daily weight rebalance simulation (every 24h)
                    if step % 288 == 0 and step > 0:
                        self.rebalance_weights_sim()

                    # Get quotes
                    ctx.open_positions = self.open_positions
                    
                    # Check how many real active positions we have
                    real_position_count = sum(1 for p in self.open_positions if not p.is_shadow)
                    
                    for sym in self.symbols:
                        if self.progress_callback and step % 50 == 0:
                            pct = int((step / total_steps) * 100)
                            self.progress_callback(pct, len(self.closed_trades), sym)
                        quote = quotes.get(sym)
                        if not quote:
                            continue
                            
                        # Filter: Cooldown
                        cooldown_expiry = self.cooldowns.get(sym, 0)
                        if current_ts < cooldown_expiry:
                            continue
                            
                        # Slice klines up to current_ts
                        candles_5m = await mock_fetch(sym, "5m", limit=35)
                        candles_1h = await mock_fetch(sym, "1h", limit=60)
                        
                        if not candles_5m or not candles_1h:
                            continue
                            
                        # Run volume gate
                        if not await volume_gate.passes(ctx, sym):
                            continue
                            
                        # Run whale netflow
                        if not await whale_netflow.is_bullish(ctx, sym):
                            continue
                            
                        # Run confluence score
                        if not await confluence_score.passes(ctx, sym):
                            continue
                            
                        # Confluence score has cached it on ctx
                        score = ctx.confluence
                        
                        # Evaluate all strategies, then COMBINE the active ones into
                        # ONE high-conviction ensemble entry (shadow strategies are
                        # still tracked individually for revival).
                        fired = []
                        for name in self.enabled_strategies:
                            # The main engine validates the SPOT book only; the
                            # leveraged long/short PERP book is validated separately
                            # by backtest/perp_backtest.py (it needs direction-aware
                            # fills this spot engine doesn't model).
                            if "perp" in name:
                                continue
                            if not is_actionable(ctx.regime, name):
                                continue
                            sig = await STRATEGIES[name]().evaluate(sym, candles_5m, candles_1h, ctx)
                            if sig:
                                fired.append((name, sig))
                        if not fired:
                            continue

                        min_conf = settings.council_min_final_confidence if self.quality_mode else settings.council_min_final_confidence_relaxed
                        from strategies.combiner import combine_signals
                        real_sigs = [s for n, s in fired if n not in self.suspended_strategies]
                        shadow_sigs = [(n, s) for n, s in fired if n in self.suspended_strategies]

                        entries = []  # (strategy_name, signal, is_shadow, n_agree)
                        if real_sigs:
                            combo = combine_signals(real_sigs)[0]
                            entries.append((combo.strategy_name, combo, False, combo.n_agree))
                        for n, s in shadow_sigs:
                            entries.append((n, s, True, 1))

                        for name, sig, is_shadow, n_agree in entries:
                            synth_score = 0.5 * (score / 100.0) + 0.5 * sig.confidence
                            if synth_score < min_conf:
                                continue
                            if is_shadow:
                                size_shadow = 1.0 / quote.price
                                sl_price = quote.price * (1.0 - sig.stop_loss_pct / 100.0)
                                tp_price = quote.price * (1.0 + sig.take_profit_pct / 100.0)
                                new_pos = BacktestPosition(
                                    id=str(uuid.uuid4()), symbol=sym, entry_time_ms=current_ts,
                                    entry_price=quote.price, stop_loss=sl_price, take_profit=tp_price,
                                    size=size_shadow, invested=1.0, strategy=name,
                                    max_hold_min=sig.max_hold_min, is_shadow=True,
                                )
                                if name == "mean_reversion":
                                    closes = [c.close for c in candles_5m]
                                    new_pos.tp1_price = sum(closes[-20:]) / 20.0
                                elif name == "trend_follow":
                                    from strategies.trend_follow import calculate_atr
                                    new_pos.atr = calculate_atr(candles_1h, period=14)
                                self.open_positions.append(new_pos)
                            else:
                                if real_position_count >= settings.max_concurrent_positions:
                                    continue
                                symbol_invested = sum(p.invested for p in self.open_positions if p.symbol == sym and not p.is_shadow)
                                if symbol_invested >= self.current_equity * 0.5:
                                    continue
                                size_usd = self.calculate_backtest_trade_size(name, self.cash, real_position_count, score, n_agree=n_agree)
                                if size_usd > 0.0:
                                    self.cash -= size_usd
                                    real_entry_price = quote.price * (1.0 + BT_SLIPPAGE)
                                    real_size = (size_usd * (1.0 - BT_FEE)) / real_entry_price
                                    sl_price = real_entry_price * (1.0 - sig.stop_loss_pct / 100.0)
                                    tp_price = real_entry_price * (1.0 + sig.take_profit_pct / 100.0)
                                    new_pos = BacktestPosition(
                                        id=str(uuid.uuid4()), symbol=sym, entry_time_ms=current_ts,
                                        entry_price=real_entry_price, stop_loss=sl_price, take_profit=tp_price,
                                        size=real_size, invested=size_usd, strategy=name,
                                        max_hold_min=sig.max_hold_min, is_shadow=False,
                                    )
                                    if name == "mean_reversion":
                                        closes = [c.close for c in candles_5m]
                                        new_pos.tp1_price = sum(closes[-20:]) / 20.0
                                    elif name == "trend_follow":
                                        from strategies.trend_follow import calculate_atr
                                        new_pos.atr = calculate_atr(candles_1h, period=14)
                                    self.open_positions.append(new_pos)
                                    real_position_count += 1

                # Update current equity peak
                self.current_equity = self.get_equity(current_ts, quotes)
                self.peak_equity = max(self.peak_equity, self.current_equity)

        finally:
            # Clean overrides to avoid breaking live code
            bk._backtest_fetch_override = None
            cmc_mcp._backtest_mcp_override = None
            cex_sanity.passes_cex_sanity = _orig_passes_cex_sanity
            settings.quality_mode = _orig_quality_mode

        return self.closed_trades

    def calculate_backtest_trade_size(self, strategy_name: str, available_usdt: float, active_position_count: int, confluence: float, n_agree: int = 1) -> float:
        peak = self.peak_equity
        now = self.current_equity
        drawdown_pct = 0.0
        if peak > 0:
            drawdown_pct = ((peak - now) / peak) * 100.0
            
        if drawdown_pct < 5.0:
            dd_mult = 1.0
        elif drawdown_pct < 10.0:
            dd_mult = 0.8
        elif drawdown_pct < 15.0:
            dd_mult = 0.5
        elif drawdown_pct < 20.0:
            dd_mult = 0.25
        else:
            dd_mult = 0.0
            
        strat_name_lower = strategy_name.lower()
        if "news" in strat_name_lower:
            strat_mult = 2.0
        elif "capitulation" in strat_name_lower:
            strat_mult = 0.8
        else:
            strat_mult = 1.0
            
        learn_mult = self.strategy_weights.get(strategy_name, 1.0)
        
        synth_score = (confluence / 100.0) * STRATEGY_ACCURACY.get(strategy_name, 0.75)
        council_mult = 0.5 + synth_score
        
        if not self.quality_mode:
            quality_mult = 1.0
        else:
            quality_mult = 1.1
            
        agree_mult = min(1.5, 1.0 + 0.25 * max(0, n_agree - 1))
        size = self.base_trade_size * 1.0 * dd_mult * strat_mult * learn_mult * council_mult * quality_mult * agree_mult

        if size > available_usdt:
            size = available_usdt
            
        if size < 1.10:
            return 0.0
            
        return round(size, 2)
