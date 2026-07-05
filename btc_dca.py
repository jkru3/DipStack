#!/usr/bin/env python3
"""
BTC DCA — Regime-Aware Strategy
================================
Run:  python3 btc_dca.py

Detects whether Bitcoin is in a BULL, BEAR, or NEUTRAL market using
its 20-week moving average, then scales your weekly deposit accordingly:
more capital when price is cheap, less when price is expensive.

Every week you simply buy at market. No limit orders. No cash sitting idle.
The regime multiplier is the entire mechanism — it's what the data shows works.

First run: downloads ~12 years of BTC history, optimizes multipliers.
Takes ~30 seconds. Re-optimizes automatically every 30 days.

Dependencies:  pip install requests yfinance
"""

import itertools
import json
import math
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

missing = []
try:
    import requests
except ImportError:
    missing.append("requests")
try:
    import yfinance as yf
except ImportError:
    missing.append("yfinance")
if missing:
    print(f"Missing dependencies. Run:  pip install {' '.join(missing)}")
    sys.exit(1)

HERE            = Path(__file__).resolve().parent
CACHE_FILE      = HERE / ".btc_cache.json"
PARAMS_FILE     = HERE / ".btc_params.json"
MEMORY_FILE     = HERE / ".btc_memory.json"

# ── Configure these two numbers ───────────────────────────────────────────────
BTC_TARGET_PCT = 20.0   # Target Bitcoin as % of your total portfolio
BASE_DEPOSIT   = 2000   # Your total weekly investment ($) — across all assets.
                        # The BTC slice starts at BASE_DEPOSIT × BTC_TARGET_PCT%,
                        # then the regime multiplier scales it up or down from there.

# ── Portfolio guardrails ───────────────────────────────────────────────────────
# These are hard band limits, not nudges. The regime signal runs freely inside
# the band. At the edges, the suggested deposit is overridden.
#
# BTC < FLOOR: treat as BEAR regardless of regime — buy aggressively.
#   Rationale: a 75% BTC crash can drag a 40% allocation down to 10% fast.
#   You want to be buying into that, not sitting at your regular allocation.
#
# BTC > CEILING: skip the BTC buy entirely this week.
#   Rationale: you're already overconcentrated. Adding more fuel makes
#   the eventual correction more painful.
#
# Both overrides are shown clearly in the output so you can still choose
# to ignore them — they're suggestions, not automatic trades.
BTC_FLOOR_PCT   = 10.0   # below this → force bear-rate BTC buy
BTC_CEILING_PCT = 40.0   # above this → skip BTC buy this week

# Derived: baseline BTC allocation per week before regime scaling
_BTC_BASE = BASE_DEPOSIT * BTC_TARGET_PCT / 100   # e.g. $2000 × 20% = $400

PARAMS_MAX_AGE  = 30 * 86400
PRICE_CACHE_AGE = 6  * 3600
REGIME_MA_WEEKS = 20


# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_regime(prices: list[float]) -> str:
    """
    BULL:    price > 20w MA  AND  MA is rising
    BEAR:    price < 20w MA  AND  MA is falling
    NEUTRAL: everything else (transitioning)

    Detection-with-lag, not prediction. 1–4 weeks late on turns, which
    is fine — the goal is correct positioning during the bulk of each regime.
    """
    if len(prices) < REGIME_MA_WEEKS + 1:
        return "NEUTRAL"
    ma_now  = sum(prices[-REGIME_MA_WEEKS:])     / REGIME_MA_WEEKS
    ma_prev = sum(prices[-REGIME_MA_WEEKS-1:-1]) / REGIME_MA_WEEKS
    above   = prices[-1] > ma_now
    rising  = ma_now > ma_prev
    if above and rising:
        return "BULL"
    if not above and not rising:
        return "BEAR"
    return "NEUTRAL"


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Params:
    # How much of BASE_DEPOSIT to deploy each week, by regime.
    # This is the entire mechanism — deploy more when cheap, less when expensive.
    bear_multiplier:    float = 2.0    # e.g. 2.0 = double your base in a bear
    neutral_multiplier: float = 1.0
    bull_multiplier:    float = 0.75   # slightly less — price is expensive

    # ATH exit: the only sell.
    # Never sell on routine bounces. Only when price is genuinely euphoric.
    ath_trigger_pct:    float = 115.0  # sell when price > X% of all-time high
    t2_size_pct:        float = 10.0   # % of total BTC holdings to sell per trigger

    # Recovery guard: if both legs of a crash buy filled, don't sell until
    # price recovers above this moving average.
    ma_weeks:           int   = 12


SEARCH_SPACE = {
    "bear_multiplier":    [1.5, 2.0, 2.5, 3.0],
    "neutral_multiplier": [0.75, 1.0, 1.25],
    "bull_multiplier":    [0.5, 0.75, 1.0],
    "ath_trigger_pct":    [110, 115, 120, 125, 130],
    "t2_size_pct":        [5, 8, 10, 12, 15],
    "ma_weeks":           [8, 12, 16, 20],
}
# 4×3×3×5×5×4 = 3,600 combinations — optimizer finishes in seconds


# ─────────────────────────────────────────────────────────────────────────────
# ORDER GENERATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Order:
    side:  str    # BUY / SELL
    kind:  str    # MARKET / LIMIT-ATH
    price: float
    btc:   float
    usd:   float
    label: str
    note:  str

@dataclass
class Plan:
    regime:  str
    deposit: float
    orders:  list
    notes:   list
    warnings:list

    def __init__(self, regime: str, deposit: float):
        self.regime   = regime
        self.deposit  = deposit
        self.orders   = []
        self.notes    = []
        self.warnings = []


def generate_orders(
    spot: float,
    ath: float,
    ma_price: float,
    btc_held: float,
    price_history: list[float],
    base_deposit: float,
    p: Params,
    override_deposit: Optional[float] = None,
) -> Plan:
    regime  = detect_regime(price_history + [spot])
    mult    = {"BEAR":    p.bear_multiplier,
               "NEUTRAL": p.neutral_multiplier,
               "BULL":    p.bull_multiplier}[regime]
    deposit = round(override_deposit if override_deposit is not None
                    else base_deposit * mult, 2)

    plan = Plan(regime=regime, deposit=deposit)

    # ── Buy at market ─────────────────────────────────────────────────────────
    regime_notes = {
        "BEAR":    f"BEAR — price below falling 20w MA. "
                   f"Deploying {p.bear_multiplier}× (${deposit:,.0f}) at market.",
        "NEUTRAL": f"NEUTRAL — sideways market. "
                   f"Deploying {p.neutral_multiplier}× (${deposit:,.0f}) at market.",
        "BULL":    f"BULL — price above rising 20w MA. "
                   f"Deploying {p.bull_multiplier}× (${deposit:,.0f}) at market.",
    }
    plan.notes.append(regime_notes[regime])
    plan.orders.append(Order(
        side="BUY", kind="MARKET", price=spot,
        btc=deposit / spot, usd=deposit,
        label="Market buy",
        note=f"${deposit:,.0f} at current price"
    ))

    # ── ATH sell ──────────────────────────────────────────────────────────────
    ath_threshold = ath * (p.ath_trigger_pct / 100)
    if spot > ath_threshold:
        btc_sell = btc_held * (p.t2_size_pct / 100)
        if btc_sell > 0:
            plan.notes.append(
                f"ATH zone: ${spot:,.0f} > {p.ath_trigger_pct:.0f}% of ATH "
                f"${ath:,.0f} — selling {p.t2_size_pct:.0f}% of holdings"
            )
            plan.orders.append(Order(
                side="SELL", kind="LIMIT-ATH", price=spot,
                btc=btc_sell, usd=btc_sell * spot,
                label="ATH sell",
                note=f"Sell {p.t2_size_pct:.0f}% of {btc_held:.5f} BTC "
                     f"≈ ${btc_sell*spot:,.0f}  |  only sell: price in euphoria zone"
            ))
        else:
            plan.warnings.append("ATH zone — BTC holdings = 0, nothing to sell")

    return plan


# ─────────────────────────────────────────────────────────────────────────────
# BACKTESTER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    params:           Params
    portfolio_roi:    float    # (final_value / total_deposited - 1) × 100
    max_drawdown_pct: float
    sharpe_proxy:     float    # roi / (drawdown + 1)
    btc_ratio:        float    # strategy BTC / same-dollars-at-spot BTC
    bear_btc_ratio:   float    # btc_ratio during bear weeks only
    bull_btc_ratio:   float    # btc_ratio during bull weeks only
    neutral_btc_ratio:float
    final_btc:        float
    dca_btc:          float    # BTC if same dollars deployed at spot every week
    total_deposited:  float
    final_value:      float
    dca_final_value:  float
    bah_final_value:  float    # buy-and-hold: lump sum at first price
    weeks:            int


def _backtest(prices: list[float], p: Params, base: float) -> Optional[BacktestResult]:
    if len(prices) < REGIME_MA_WEEKS + p.ma_weeks + 4:
        return None

    cash = btc = dca_btc = total_dep = 0.0
    peak = 1e-9
    max_dd = 0.0
    start_idx   = REGIME_MA_WEEKS
    start_price = prices[start_idx]

    regime_btc = {"BEAR": 0.0, "BULL": 0.0, "NEUTRAL": 0.0}
    regime_dca = {"BEAR": 0.0, "BULL": 0.0, "NEUTRAL": 0.0}

    for i in range(start_idx, len(prices) - 1):
        spot      = prices[i]
        next_spot = prices[i + 1]
        history   = prices[max(0, i - REGIME_MA_WEEKS - 1):i]
        regime    = detect_regime(history + [spot])

        plan = generate_orders(
            spot=spot, ath=max(prices[:i+1]),
            ma_price=sum(prices[i-p.ma_weeks:i]) / p.ma_weeks,
            btc_held=btc, price_history=history,
            base_deposit=base, p=p,
        )

        # Deposit and buy
        cash      += plan.deposit
        total_dep += plan.deposit

        # DCA benchmark: flat base every week at spot — no regime scaling.
        # This is what you'd accumulate by just buying $BASE each week without
        # any strategy. The btc_ratio measures whether regime timing adds value
        # on top of that baseline.
        dca_btc            += base / spot
        regime_dca[regime] += base / spot

        btc_before = btc
        for o in plan.orders:
            if o.side == "BUY" and o.kind == "MARKET":
                cost = min(o.usd, cash)
                btc  += cost / spot
                cash -= cost
            elif o.side == "SELL" and next_spot >= o.price:
                qty   = min(o.btc, btc)
                cash += qty * o.price
                btc  -= qty

        regime_btc[regime] += btc - btc_before

        port   = btc * spot + cash
        peak   = max(peak, port)
        max_dd = max(max_dd, (peak - port) / peak)

    last        = prices[-1]
    final_value = btc * last + cash
    dca_btc_total = dca_btc   # flat-base DCA BTC
    dca_final   = dca_btc_total * last
    bah_final   = (total_dep / start_price) * last
    week_count  = len(prices) - 1 - start_idx
    roi         = (final_value / total_dep - 1) * 100 if total_dep else 0.0

    def _ratio(reg):
        return regime_btc[reg] / regime_dca[reg] if regime_dca[reg] > 0 else 1.0

    return BacktestResult(
        params=p,
        portfolio_roi=roi,
        max_drawdown_pct=max_dd * 100,
        sharpe_proxy=roi / (max_dd * 100 + 1),
        btc_ratio=btc / dca_btc if dca_btc else 0.0,
        bear_btc_ratio=_ratio("BEAR"),
        bull_btc_ratio=_ratio("BULL"),
        neutral_btc_ratio=_ratio("NEUTRAL"),
        final_btc=btc,
        dca_btc=dca_btc,
        total_deposited=total_dep,
        final_value=final_value,
        dca_final_value=dca_final,
        bah_final_value=bah_final,
        weeks=week_count,
    )


def _score(r: BacktestResult) -> float:
    # Reward bear-market accumulation (the strategy's structural edge)
    # and penalize excessive drawdown.
    return 0.6 * r.bear_btc_ratio + 0.4 * r.sharpe_proxy


def _eval(args):
    prices, combo, base = args
    try:
        p = Params(**combo)
    except TypeError:
        return None
    r = _backtest(prices, p, base)
    if r is None:
        return None
    return (_score(r), r)


def optimize(prices: list[float], base: float) -> BacktestResult:
    keys   = list(SEARCH_SPACE.keys())
    combos = [dict(zip(keys, c)) for c in itertools.product(*SEARCH_SPACE.values())]
    total  = len(combos)
    work   = [(prices, c, base) for c in combos]

    print(f"  Searching {total:,} combinations across {len(prices)} weeks of history...")

    best: Optional[BacktestResult] = None
    best_score = -math.inf
    done = 0
    start = time.time()

    def record(item):
        nonlocal best, best_score, done
        done += 1
        if item and item[0] > best_score:
            best_score, best = item
        if done % max(1, total // 20) == 0:
            eta = (time.time() - start) / done * (total - done)
            print(f"  {done/total*100:5.1f}%  best: {best_score:.3f}  ETA {eta:.0f}s",
                  end="\r")

    try:
        with mp.Pool() as pool:
            for item in pool.imap_unordered(_eval, work, chunksize=64):
                record(item)
    except Exception:
        for w in work:
            record(_eval(w))

    print(f"\n  Done in {time.time()-start:.0f}s")
    if best is None:
        raise RuntimeError("No valid combinations found.")
    return best


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO ALLOCATION
# ─────────────────────────────────────────────────────────────────────────────

def allocate(
    investable: float,
    btc_value: float,
    other_value: float,
    regime: str,
    params: Params,
) -> tuple[float, float, dict]:
    """
    Split this week's investable cash between BTC and other investments.

    Normal operation (BTC within 10–40% band):
        BTC amount = investable × BTC_TARGET_PCT% × regime_multiplier
        Other      = investable - BTC amount

    At the floor (BTC < 10%):
        Override regime. Deploy at bear_multiplier rate regardless.
        You're underexposed — buy aggressively.

    At the ceiling (BTC > 40%):
        Skip BTC buy entirely. Put everything into other investments.
        You're overconcentrated — don't add more.

    Both overrides display what the regime would have suggested,
    so the user can still choose to ignore the override.
    """
    total           = btc_value + other_value
    current_btc_pct = (btc_value / total * 100) if total > 0 else 0.0

    mult = {"BEAR":    params.bear_multiplier,
            "NEUTRAL": params.neutral_multiplier,
            "BULL":    params.bull_multiplier}[regime]
    regime_btc = round(min(investable, investable * (BTC_TARGET_PCT / 100) * mult), 2)

    # ── Guardrail: floor ──────────────────────────────────────────────────────
    if total > 0 and current_btc_pct < BTC_FLOOR_PCT:
        bear_btc = round(min(investable, investable * (BTC_TARGET_PCT / 100)
                             * params.bear_multiplier), 2)
        return bear_btc, round(investable - bear_btc, 2), {
            "total": total, "current_btc_pct": current_btc_pct,
            "override": "FLOOR", "regime_btc": regime_btc,
            "override_btc": bear_btc, "multiplier": mult,
        }

    # ── Guardrail: ceiling ────────────────────────────────────────────────────
    if total > 0 and current_btc_pct > BTC_CEILING_PCT:
        return 0.0, investable, {
            "total": total, "current_btc_pct": current_btc_pct,
            "override": "CEILING", "regime_btc": regime_btc,
            "override_btc": 0.0, "multiplier": mult,
        }

    # ── Normal: regime drives the split ──────────────────────────────────────
    return regime_btc, round(investable - regime_btc, 2), {
        "total": total, "current_btc_pct": current_btc_pct,
        "override": None, "regime_btc": regime_btc,
        "override_btc": regime_btc, "multiplier": mult,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_price_history() -> list[float]:
    cached = _load_json(CACHE_FILE)
    if cached and time.time() - cached.get("at", 0) < PRICE_CACHE_AGE:
        prices = cached["prices"]
        print(f"  Using cached price history "
              f"({len(prices)} weeks, {(time.time()-cached['at'])/60:.0f} min old)")
        return prices

    print("  Fetching BTC price history from Yahoo Finance...", end=" ", flush=True)
    try:
        df = yf.Ticker("BTC-USD").history(period="max")
        if df.empty:
            raise ValueError("empty dataframe")
        weekly = df["Close"].resample("W").last().dropna()
        prices = [float(p) for p in weekly]
        if len(prices) < 60:
            raise ValueError(f"only {len(prices)} weeks returned")
        print(f"got {len(prices)} weekly candles (~{len(prices)/52:.1f} years)")
        _save_json(CACHE_FILE, {"at": time.time(), "prices": prices})
        return prices
    except Exception as e:
        print(f"failed ({e})")
        print("\n  ERROR: Could not fetch price history. Check internet and retry.")
        sys.exit(1)


def fetch_live_price() -> Optional[float]:
    for url, extract in [
        ("https://api.coinbase.com/v2/prices/spot?currency=USD",
         lambda d: float(d["data"]["amount"])),
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
         lambda d: float(d["bitcoin"]["usd"])),
    ]:
        try:
            r = requests.get(url, timeout=6)
            r.raise_for_status()
            p = extract(r.json())
            if p > 0:
                return p
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None

def _save_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass

def load_params() -> Optional[Params]:
    d = _load_json(PARAMS_FILE)
    if not d:
        return None
    if time.time() - d.get("saved_at", 0) > PARAMS_MAX_AGE:
        return None
    try:
        return Params(**{k: v for k, v in d.items()
                         if k in Params.__dataclass_fields__})
    except Exception:
        return None

def save_params(p: Params) -> None:
    d = asdict(p)
    d["saved_at"] = time.time()
    _save_json(PARAMS_FILE, d)

def load_memory() -> dict:
    return _load_json(MEMORY_FILE) or {
        "ath": 0.0,
        "price_history": [],
    }

def save_memory(m: dict) -> None:
    _save_json(MEMORY_FILE, m)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

REGIME_LABEL = {"BULL": "▲ BULL", "BEAR": "▼ BEAR", "NEUTRAL": "◆ NEUTRAL"}

def print_backtest_summary(r: BacktestResult) -> None:
    print(f"\n  {'':22}  {'Strategy':>10}  {'Flat DCA':>10}  {'Buy&Hold':>10}")
    print(f"  {'─'*57}")
    print(f"  {'BTC accumulated':22}  {r.final_btc:>10.4f}  {r.dca_btc:>10.4f}  {'—':>10}")
    print(f"  {'BTC ratio vs flat DCA':22}  {r.btc_ratio:>9.3f}x  {'1.000x':>10}")
    print(f"  {'Final value ($)':22}  {r.final_value:>10,.0f}  "
          f"{r.dca_final_value:>10,.0f}  {r.bah_final_value:>10,.0f}")
    print(f"  {'Strategy deployed ($)':22}  {r.total_deposited:>10,.0f}")
    print(f"  {'Max drawdown':22}  {r.max_drawdown_pct:>9.1f}%")
    print()
    print(f"  Regime breakdown (vs flat ${BASE_DEPOSIT:,}/week DCA):")
    print(f"  {'─'*50}")
    for label, ratio in [("Bear markets",  r.bear_btc_ratio),
                          ("Neutral",       r.neutral_btc_ratio),
                          ("Bull markets",  r.bull_btc_ratio)]:
        mark  = "✓" if ratio >= 1.0 else "·"
        delta = (ratio - 1.0) * 100
        sign  = "+" if delta >= 0 else ""
        print(f"  {mark} {label:<14}  {ratio:.3f}x  ({sign}{delta:.1f}% BTC)")
    print()
    print(f"  Flat DCA: ${_BTC_BASE:,.0f}/week into BTC at spot ({BTC_TARGET_PCT:.0f}% of ${BASE_DEPOSIT:,} base, no regime scaling).")
    print(f"  A ratio > 1.0 means regime-scaling genuinely accumulated more BTC.")
    print()


def print_allocation(btc_amount: float, other_amount: float, ctx: dict) -> None:
    inv      = btc_amount + other_amount
    cur      = ctx["current_btc_pct"]
    mult     = ctx["multiplier"]
    override = ctx["override"]
    regime_btc = ctx["regime_btc"]

    print("─" * 60)
    print("  PORTFOLIO SNAPSHOT")
    print("─" * 60)

    if ctx["total"] > 0:
        filled = max(0, min(20, int(cur / 5)))
        bar    = "█" * filled + "░" * (20 - filled)
        print(f"\n  BTC allocation:  {cur:.1f}%   band: {BTC_FLOOR_PCT:.0f}% – {BTC_CEILING_PCT:.0f}%")
        print(f"  [{bar}]")
        floor_spaces = int(BTC_FLOOR_PCT / 5) * 2
        ceil_spaces  = int(BTC_CEILING_PCT / 5) * 2
        print(f"   {' ' * floor_spaces}↑{BTC_FLOOR_PCT:.0f}%{' ' * (ceil_spaces - floor_spaces - 4)}↑{BTC_CEILING_PCT:.0f}%")
    else:
        print("\n  No portfolio data yet — using regime signal only")

    if override == "FLOOR":
        print(f"\n  ⚠  FLOOR: BTC at {cur:.1f}% is below your {BTC_FLOOR_PCT:.0f}% minimum.")
        print(f"     Regime would suggest ${regime_btc:,.0f} — overriding to bear rate.")
        print(f"     Suggested: buy aggressively to rebuild exposure.")
    elif override == "CEILING":
        print(f"\n  ⚠  CEILING: BTC at {cur:.1f}% is above your {BTC_CEILING_PCT:.0f}% maximum.")
        print(f"     Regime would suggest ${regime_btc:,.0f} — skipping BTC buy.")
        print(f"     Suggested: put everything into other investments this week.")
    else:
        print(f"\n  ✓ Within band. Regime signal active ({mult}× multiplier).")

    print(f"\n  You have ${inv:,.0f} to invest this round.")
    if override is None:
        btc_base = inv * (BTC_TARGET_PCT / 100)
        print(f"  ${btc_base:,.0f} base ({BTC_TARGET_PCT:.0f}%)  ×  {mult}× regime  =  ${btc_amount:,.0f} BTC")
    print()
    print(f"  → ${btc_amount:,.0f} into Bitcoin        ({btc_amount/inv*100:.0f}%)")
    print(f"  → ${other_amount:,.0f} into other investments  ({other_amount/inv*100:.0f}%)")
    if override is not None:
        print(f"\n  You can override this — final call is always yours.")
    print()


def print_orders(plan: Plan) -> None:
    print("─" * 60)
    print(f"  {REGIME_LABEL.get(plan.regime, plan.regime)} — "
          f"ORDERS  (${plan.deposit:,.0f} into BTC this round)")
    print("─" * 60)
    for note in plan.notes:
        print(f"\n  ℹ  {note}")
    for w in plan.warnings:
        print(f"\n  ⚠  {w}")
    print()
    for i, o in enumerate(plan.orders, 1):
        marker = "▲ BUY " if o.side == "BUY" else "▼ SELL"
        kind   = "at market now" if o.kind == "MARKET" else f"limit at ${o.price:,.0f}"
        print(f"  {i}. {marker}  —  {kind}")
        print(f"        {o.btc:.6f} BTC  (≈ ${o.usd:,.0f})")
        print(f"        {o.note}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# INPUT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    try:
        val = input(prompt).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

def ask_float(prompt: str, default: Optional[float] = None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = ask(f"  {prompt}{suffix}: ")
        if raw == "" and default is not None:
            return default
        try:
            v = float(raw.replace("$", "").replace(",", ""))
            if v >= 0:
                return v
        except ValueError:
            pass
        print("    Enter a number (e.g. 725 or 105000)")

def ask_yn(prompt: str, default: bool = False) -> bool:
    hint = " [Y/n]" if default else " [y/N]"
    raw  = ask(f"  {prompt}{hint}: ").lower()
    if raw == "":
        return default
    return raw.startswith("y")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("  ₿  BTC DCA — Regime-Aware Strategy")
    print("─" * 60)

    # ── Step 1: Load or optimize params ──────────────────────────────────────
    params = load_params()
    if params is None:
        print("\n  No settings found (or over 30 days old) — running optimizer.\n")
        prices = fetch_price_history()
        result = optimize(prices, _BTC_BASE)
        params = result.params
        save_params(params)
        print_backtest_summary(result)
    else:
        print("  Settings loaded  (re-optimizes automatically every 30 days)\n")

    # ── Step 2: Live price ────────────────────────────────────────────────────
    print("  Fetching live BTC price...", end=" ", flush=True)
    spot = fetch_live_price()
    if spot:
        print(f"${spot:,.2f}")
        if not ask_yn(f"  Use ${spot:,.0f}?", default=True):
            spot = ask_float("Current BTC price ($)")
    else:
        print("unavailable")
        spot = ask_float("Current BTC price ($)")

    # ── Step 3: Update memory ─────────────────────────────────────────────────
    mem = load_memory()
    mem["price_history"].append(spot)
    mem["price_history"] = mem["price_history"][-(REGIME_MA_WEEKS + 2):]
    mem["ath"] = max(mem.get("ath", spot), spot)

    # ── Step 4: Questions ─────────────────────────────────────────────────────
    print()
    investable   = ask_float("How much do you have to invest this round ($)")
    btc_held     = ask_float("Your total BTC holdings (BTC)", default=0.0)
    other_value  = ask_float(
        "Value of your other investments — stocks, ETFs, 401k ($)", default=0.0)
    btc_value    = btc_held * spot
    print()

    # ── Step 5: Detect regime, allocate ──────────────────────────────────────
    regime = detect_regime(mem["price_history"])
    btc_amount, other_amount, ctx = allocate(
        investable=investable,
        btc_value=btc_value,
        other_value=other_value,
        regime=regime,
        params=params,
    )
    print_allocation(btc_amount, other_amount, ctx)

    # ── Step 6: Generate and print orders ─────────────────────────────────────
    ma_price = (sum(mem["price_history"][-params.ma_weeks:]) /
                min(len(mem["price_history"]), params.ma_weeks))
    plan = generate_orders(
        spot=spot, ath=mem["ath"], ma_price=ma_price,
        btc_held=btc_held, price_history=mem["price_history"],
        base_deposit=_BTC_BASE, p=params,
        override_deposit=btc_amount,
    )
    print_orders(plan)

    # ── Step 7: Save state ────────────────────────────────────────────────────
    save_memory(mem)
    print("  Saved. Come back next time you have cash to invest.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(0)