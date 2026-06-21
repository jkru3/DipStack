"""
Backtester for the BTC DCA strategy.

Accounting model
----------------
All capital flows through a single `cash` ledger — no separate
`sold_earnings` or `carry_cash` variables that can be double-counted.

Cash in:
  + weekly_deposit every week, unconditionally
  + sell proceeds added back to cash when a sell order fills

Cash out:
  - buy cost deducted from cash when a buy order fills
  - unfilled buy funds stay in cash and roll forward automatically

Portfolio value at any point:
  btc * spot_price + cash

This means:
  - sold_earnings are implicitly captured (cash goes up when we sell,
    btc goes down — both sides of the trade are reflected)
  - carry is implicit (cash not spent stays in cash)
  - nothing is counted twice

Benchmarks simulated alongside the strategy every run:
  - Pure weekly DCA  : buy exactly weekly_deposit at spot each week
  - Buy-and-hold     : invest all capital on day 0 at the first price

Metrics
-------
  portfolio_roi       : (final_value / total_deposited) - 1
  btc_accumulation    : strategy_btc / dca_btc
  excess_return_vs_dca: final_value_strategy / final_value_dca - 1
  avg_cash_drag       : mean(cash / portfolio_value) across weeks
  max_drawdown_pct    : peak-to-trough drawdown on portfolio value
  sharpe_proxy        : portfolio_roi / (max_drawdown + epsilon)
  fill_rate           : limit orders filled / limit orders placed
"""

from __future__ import annotations
import itertools
import math
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import PARAM_RANGES, WEEKLY_DEPOSIT_USD
from strategy import StrategyParams, WeeklyState, generate_orders


# ── DATA FETCHING ──────────────────────────────────────────────────────────────

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    "?vs_currency=usd&days=1095&interval=daily"
)

def fetch_weekly_prices(verbose: bool = True) -> list[float]:
    """
    Fetch ~3 years of daily BTC prices from CoinGecko and resample weekly.
    Falls back to a synthetic series on network failure.
    """
    if verbose:
        print("  Fetching BTC price history from CoinGecko...")
    try:
        r = requests.get(COINGECKO_URL, timeout=15)
        r.raise_for_status()
        daily = [p[1] for p in r.json()["prices"]]
        weekly = daily[::7]
        if verbose:
            print(f"  {len(weekly)} weekly candles loaded "
                  f"({len(daily)} daily → sampled every 7th)")
        return weekly
    except Exception as e:
        if verbose:
            print(f"  [WARN] CoinGecko unavailable ({e}) — using synthetic price series")
        return _synthetic_btc(n_weeks=150)


def _synthetic_btc(n_weeks: int = 150, seed: int = 42) -> list[float]:
    """
    Deterministic log-normal random walk with BTC-like drift and volatility.
    Used as a reproducible fallback when the network is unavailable.
    """
    import random
    rng = random.Random(seed)
    prices = [30_000.0]
    for _ in range(n_weeks - 1):
        r = math.exp(0.005 + 0.09 * rng.gauss(0, 1))
        prices.append(max(5_000.0, prices[-1] * r))
    return prices


# ── RESULT DATACLASS ───────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    params: StrategyParams

    # ── Primary metrics ──
    portfolio_roi:          float   # (final_value / total_deposited) - 1, as %
    max_drawdown_pct:       float   # peak-to-trough on portfolio value, as %
    sharpe_proxy:           float   # portfolio_roi / (max_drawdown + 1)

    # ── BTC accumulation ──
    final_btc:              float   # BTC held at end
    dca_btc:                float   # BTC a simple weekly DCA would have accumulated
    btc_accumulation_ratio: float   # final_btc / dca_btc  (>1 = beating DCA in BTC terms)

    # ── Dollar comparison ──
    final_value_usd:        float   # btc * last_price + cash
    dca_final_value_usd:    float   # what pure DCA portfolio is worth
    bah_final_value_usd:    float   # what buy-and-hold is worth
    excess_return_vs_dca:   float   # final_value / dca_final_value - 1, as %

    # ── Efficiency ──
    fill_rate:              float   # limit orders filled / limit orders placed
    avg_cash_drag:          float   # mean(cash / portfolio_value) across weeks, as %
    total_deposited_usd:    float
    total_weeks:            int

    # ── Derived objective scores (set after construction) ──
    fill_roi:               float = 0.0   # portfolio_roi * fill_rate


# ── BACKTEST ENGINE ────────────────────────────────────────────────────────────

def backtest(prices: list[float], params: StrategyParams) -> Optional[BacktestResult]:
    """
    Simulate the strategy over a weekly price series.

    Single cash ledger — no split between carry and sold_earnings.
    All capital enters as deposits; sell proceeds return to cash;
    buy costs deduct from cash. Nothing is counted twice.

    Returns None if the series is too short for the MA window.
    """
    ma_w = params.ma_weeks
    if len(prices) < ma_w + 4:
        return None

    # ── Strategy state ────────────────────────────────────────────────────────
    cash        = 0.0   # uninvested USD (carry + sell proceeds — unified)
    btc         = 0.0
    total_dep   = 0.0
    b1_filled   = False
    b2_filled   = False
    peak_value  = 1e-9  # avoid div-by-zero on first week
    max_dd      = 0.0
    fills       = 0
    possible    = 0
    cash_drag_samples: list[float] = []

    # ── Benchmark: pure weekly DCA ────────────────────────────────────────────
    dca_btc  = 0.0
    dca_cash = 0.0   # DCA also accumulates cash; invested at spot each week

    # ── Benchmark: buy-and-hold ───────────────────────────────────────────────
    # Invests all capital deposited over the period on day 0 at prices[ma_w]
    # We calculate this analytically at the end.

    start_price = prices[ma_w]

    for i in range(ma_w, len(prices) - 1):
        spot      = prices[i]
        next_spot = prices[i + 1]
        ma_price  = sum(prices[i - ma_w : i]) / ma_w
        run_ath   = max(prices[: i + 1])

        # ── Weekly deposit ────────────────────────────────────────────────────
        cash      += params.weekly_deposit
        total_dep += params.weekly_deposit

        # ── DCA benchmark: buy at spot every week ────────────────────────────
        dca_btc += params.weekly_deposit / spot

        # ── Generate orders ───────────────────────────────────────────────────
        # Pass current cash minus the deposit we just added as carry
        # (the deposit itself is "new money", carry is old unspent money)
        carry_for_bl2 = max(0.0, cash - params.weekly_deposit)

        state = WeeklyState(
            spot=spot, ath=run_ath, ma_price=ma_price,
            btc_holdings=btc,
            carry_cash=carry_for_bl2,
            sold_earnings=0.0,   # proceeds already absorbed into cash
            bl1_filled_last_week=b1_filled,
            bl2_filled_last_week=b2_filled,
        )

        plan  = generate_orders(state, params)
        new_b1 = False
        new_b2 = False

        for order in plan.orders:
            if order.side == "BUY":
                if order.order_type == "MARKET":
                    # Spot buy (exception): costs usd_value, gives quantity_btc
                    cost = min(order.usd_value, cash)   # can't spend more than we have
                    if cost > 0:
                        btc  += cost / spot
                        cash -= cost

                elif order.order_type == "LIMIT":
                    possible += 1
                    fills_this = next_spot <= order.price
                    if fills_this:
                        cost = min(order.usd_value, cash)
                        if cost > 0:
                            btc  += cost / order.price
                            cash -= cost
                        fills += 1
                        if order.label == "BL1":
                            new_b1 = True
                        elif order.label == "BL2":
                            new_b2 = True
                    # If limit doesn't fill: cash stays in cash — no action needed

            elif order.side == "SELL":
                if next_spot >= order.price:
                    qty  = min(order.quantity_btc, btc)  # can't sell more than held
                    proceeds = qty * order.price
                    btc  -= qty
                    cash += proceeds   # proceeds return to unified cash pool

        b1_filled = new_b1
        b2_filled = new_b2

        # ── Drawdown tracking ─────────────────────────────────────────────────
        port_value = btc * spot + cash
        peak_value = max(peak_value, port_value)
        dd = (peak_value - port_value) / peak_value
        max_dd = max(max_dd, dd)

        # ── Cash drag sample ──────────────────────────────────────────────────
        if port_value > 0:
            cash_drag_samples.append(cash / port_value)

    # ── Final accounting ──────────────────────────────────────────────────────
    last_price      = prices[-1]
    final_value     = btc * last_price + cash   # cash is unambiguously "real" here

    # DCA benchmark final value
    dca_final_value = dca_btc * last_price

    # Buy-and-hold: invest total_dep at start_price, hold to end
    bah_btc         = total_dep / start_price
    bah_final_value = bah_btc * last_price

    # Metrics
    roi             = (final_value / total_dep - 1) * 100 if total_dep > 0 else 0.0
    btc_acc_ratio   = (btc / dca_btc) if dca_btc > 0 else 0.0
    excess_vs_dca   = (final_value / dca_final_value - 1) * 100 if dca_final_value > 0 else 0.0
    fill_rate       = fills / possible if possible > 0 else 0.0
    avg_cash_drag   = (sum(cash_drag_samples) / len(cash_drag_samples) * 100
                       if cash_drag_samples else 0.0)
    sharpe_proxy    = roi / (max_dd * 100 + 1)
    week_count      = len(prices) - 1 - ma_w

    result = BacktestResult(
        params=params,
        portfolio_roi=roi,
        max_drawdown_pct=max_dd * 100,
        sharpe_proxy=sharpe_proxy,
        final_btc=btc,
        dca_btc=dca_btc,
        btc_accumulation_ratio=btc_acc_ratio,
        final_value_usd=final_value,
        dca_final_value_usd=dca_final_value,
        bah_final_value_usd=bah_final_value,
        excess_return_vs_dca=excess_vs_dca,
        fill_rate=fill_rate,
        avg_cash_drag=avg_cash_drag,
        total_deposited_usd=total_dep,
        total_weeks=week_count,
    )
    result.fill_roi = roi * fill_rate
    return result


# ── GRID SEARCH ────────────────────────────────────────────────────────────────

OBJECTIVES = {
    "roi":       lambda r: r.portfolio_roi,
    "sharpe":    lambda r: r.sharpe_proxy,
    "btc":       lambda r: r.btc_accumulation_ratio,
    "excess":    lambda r: r.excess_return_vs_dca,
    "filled":    lambda r: r.fill_roi,
}

def _eval_combo(args) -> Optional[tuple[float, BacktestResult]]:
    """Multiprocessing worker: evaluate one parameter combination."""
    prices, combo_dict, t2_size_pct, objective = args
    p = combo_dict
    if p["bl2_pct"] <= p["bl1_pct"]:
        return None
    params = StrategyParams(
        bl1_pct         = p["bl1_pct"],
        bl2_pct         = p["bl2_pct"],
        t1_pct          = p["t1_pct"],
        ma_weeks        = p["ma_weeks"],
        t1_size_pct     = p["t1_size_pct"],
        ath_trigger_pct = p["ath_trigger_pct"],
        t2_size_pct     = t2_size_pct,
        gw_offset       = p["gw_offset"],
        carry_spot_pct  = p["carry_spot_pct"],
        weekly_deposit  = WEEKLY_DEPOSIT_USD,
    )
    result = backtest(prices, params)
    if result is None:
        return None
    score = OBJECTIVES[objective](result)
    return (score, result)


def optimize(
    prices: list[float],
    objective: str = "sharpe",
    t2_size_pct: float = 10.0,
    verbose: bool = True,
    n_workers: Optional[int] = None,
) -> BacktestResult:
    """
    Grid-search all parameter combinations in PARAM_RANGES using multiprocessing.
    Returns the BacktestResult with the highest score on the chosen objective.
    """
    import multiprocessing as mp

    if objective not in OBJECTIVES:
        raise ValueError(
            f"Unknown objective '{objective}'. Choose from: {list(OBJECTIVES)}"
        )

    keys   = list(PARAM_RANGES.keys())
    values = list(PARAM_RANGES.values())
    combos = [dict(zip(keys, c)) for c in itertools.product(*values)]
    total  = len(combos)
    cores  = n_workers or mp.cpu_count()

    if verbose:
        print(f"\n  Grid search: {total:,} combinations "
              f"× objective={objective} × {cores} workers")

    work = [(prices, c, t2_size_pct, objective) for c in combos]

    best_result: Optional[BacktestResult] = None
    best_score  = -math.inf
    done = skipped = 0
    start = time.time()

    with mp.Pool(processes=cores) as pool:
        for item in pool.imap_unordered(_eval_combo, work, chunksize=256):
            done += 1
            if item is None:
                skipped += 1
            else:
                score, result = item
                if score > best_score:
                    best_score  = score
                    best_result = result

            if verbose and done % max(1, total // 40) == 0:
                elapsed = time.time() - start
                eta     = elapsed / done * (total - done)
                print(
                    f"  [{done/total*100:5.1f}%] {done:,}/{total:,}  "
                    f"best={best_score:.3f}  ETA {eta:.0f}s    ",
                    end="\r",
                )

    elapsed = time.time() - start
    if verbose:
        print(f"\n  Done. {total-skipped:,} valid in {elapsed:.1f}s "
              f"({skipped:,} skipped)  [{(total-skipped)/elapsed:,.0f} combos/s]")

    if best_result is None:
        raise RuntimeError("No valid parameter combination found.")
    return best_result