"""
Core strategy logic.

All buy/sell price calculations, gravity well adjustments,
and order generation live here — no I/O, no side effects.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math

from config import ROUND_NUMBER_STEP, WEEKLY_DEPOSIT_USD


# ── DATA CLASSES ───────────────────────────────────────────────────────────────

@dataclass
class Order:
    side: str           # "BUY" | "SELL"
    order_type: str     # "LIMIT" | "MARKET" | "LIMIT (ATH)"
    price: float
    quantity_btc: float
    usd_value: float
    label: str
    note: str

@dataclass
class WeeklyState:
    spot: float
    ath: float
    ma_price: float
    btc_holdings: float
    carry_cash: float       # cash carried from prior unfilled weeks
    sold_earnings: float    # proceeds from prior sells to redeploy
    bl1_filled_last_week: bool
    bl2_filled_last_week: bool

@dataclass
class StrategyParams:
    bl1_pct:         float = 2.5
    bl2_pct:         float = 8.0
    t1_pct:          float = 8.0
    ma_weeks:        int   = 12
    t1_size_pct:     float = 40.0
    ath_trigger_pct: float = 115.0
    t2_size_pct:     float = 10.0
    gw_offset:       float = 50.0
    carry_spot_pct:  float = 50.0
    weekly_deposit:  float = WEEKLY_DEPOSIT_USD

@dataclass
class OrderPlan:
    orders: list[Order] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exceptions_triggered: list[str] = field(default_factory=list)


# ── GRAVITY WELL LOGIC ─────────────────────────────────────────────────────────

def nearest_gravity_well(price: float, step: int = ROUND_NUMBER_STEP) -> float:
    """Return the nearest round-number gravity well to price."""
    return round(price / step) * step


def well_below(price: float, step: int = ROUND_NUMBER_STEP) -> float:
    """Largest round-number well strictly below price."""
    return math.floor(price / step) * step


def well_above(price: float, step: int = ROUND_NUMBER_STEP) -> float:
    """Smallest round-number well strictly above price."""
    return math.ceil(price / step) * step


def adjust_buy_for_well(raw_price: float, offset: float) -> tuple[float, float]:
    """
    For buy orders: place just above the well below the target price.
    Returns (adjusted_price, well_used).
    If raw_price is itself on a well, step down one.
    """
    w = well_below(raw_price)
    if w == raw_price:
        w -= ROUND_NUMBER_STEP
    adjusted = w + offset
    # Safety: adjusted must not exceed raw_price (defeats the purpose)
    adjusted = min(adjusted, raw_price - 1)
    return adjusted, w


def adjust_sell_for_well(raw_price: float, offset: float) -> tuple[float, float]:
    """
    For sell orders: place just below the well above the target price.
    Returns (adjusted_price, well_used).
    """
    w = well_above(raw_price)
    if w == raw_price:
        w += ROUND_NUMBER_STEP
    adjusted = w - offset
    # Safety: adjusted must not fall below raw_price
    adjusted = max(adjusted, raw_price + 1)
    return adjusted, w


# ── ORDER GENERATION ───────────────────────────────────────────────────────────

def generate_orders(state: WeeklyState, params: StrategyParams) -> OrderPlan:
    """
    Generate this week's buy and sell orders from current market state.
    Raises ValueError for invalid inputs caught early.
    """
    plan = OrderPlan()

    _validate_inputs(state, params, plan)
    if any("FATAL" in w for w in plan.warnings):
        return plan

    weekly = params.weekly_deposit
    spot   = state.spot

    # ── EXCEPTION A: neither buy limit filled last week ──────────────────────
    if not state.bl1_filled_last_week and not state.bl2_filled_last_week:
        spot_buy_usd = weekly * (params.carry_spot_pct / 100)
        remainder    = weekly - spot_buy_usd
        plan.exceptions_triggered.append(
            f"Neither limit filled last week → buying {params.carry_spot_pct:.0f}% "
            f"(${spot_buy_usd:,.0f}) at spot, carrying over ${remainder:,.0f}"
        )
        if spot_buy_usd > 0:
            qty = spot_buy_usd / spot
            plan.orders.append(Order(
                side="BUY", order_type="MARKET",
                price=spot, quantity_btc=qty, usd_value=spot_buy_usd,
                label="BL-SPOT (exception)",
                note=f"{params.carry_spot_pct:.0f}% of weekly at market; "
                     f"${remainder:,.0f} carries to BL2 pool"
            ))

    # ── BUY LIMIT 1 ──────────────────────────────────────────────────────────
    bl1_raw, bl1_adj, bl1_well = _buy_limit_price(spot, params.bl1_pct, params.gw_offset)
    bl1_qty = weekly / bl1_adj
    plan.orders.append(Order(
        side="BUY", order_type="LIMIT",
        price=bl1_adj, quantity_btc=bl1_qty, usd_value=weekly,
        label="BL1",
        note=f"{params.bl1_pct}% under spot → raw ${bl1_raw:,.0f} → "
             f"+${params.gw_offset:.0f} above ${bl1_well:,.0f} well"
    ))

    # ── BUY LIMIT 2 ──────────────────────────────────────────────────────────
    bl2_raw, bl2_adj, bl2_well = _buy_limit_price(spot, params.bl2_pct, params.gw_offset)
    bl2_pool = weekly + state.carry_cash + state.sold_earnings
    bl2_qty  = bl2_pool / bl2_adj
    pool_breakdown = (
        f"${weekly:,.0f} weekly"
        + (f" + ${state.carry_cash:,.0f} carry" if state.carry_cash else "")
        + (f" + ${state.sold_earnings:,.0f} sold earnings" if state.sold_earnings else "")
    )
    plan.orders.append(Order(
        side="BUY", order_type="LIMIT",
        price=bl2_adj, quantity_btc=bl2_qty, usd_value=bl2_pool,
        label="BL2",
        note=f"{params.bl2_pct}% under spot → raw ${bl2_raw:,.0f} → "
             f"+${params.gw_offset:.0f} above ${bl2_well:,.0f} well | "
             f"pool = {pool_breakdown}"
    ))

    # ── EXCEPTION B: both limits filled last week, wait for MA recovery ───────
    both_filled_last_week = state.bl1_filled_last_week and state.bl2_filled_last_week
    price_below_ma        = state.spot < state.ma_price

    if both_filled_last_week and price_below_ma:
        plan.exceptions_triggered.append(
            f"Both limits filled last week + price (${spot:,.0f}) below "
            f"{params.ma_weeks}w MA (${state.ma_price:,.0f}) → "
            f"NO SELL until recovery. {_pct(spot, state.ma_price):.1f}% of MA."
        )
        return plan   # skip all sells

    # ── ATH CHECK ────────────────────────────────────────────────────────────
    ath_threshold = state.ath * (params.ath_trigger_pct / 100)
    in_ath_zone   = spot > ath_threshold

    if in_ath_zone:
        plan.exceptions_triggered.append(
            f"ATH zone: ${spot:,.0f} > {params.ath_trigger_pct:.0f}% of ATH "
            f"${state.ath:,.0f} (threshold ${ath_threshold:,.0f}) → Tier 2 active, Tier 1 skipped"
        )

    # ── TIER 1 SELL (skip in ATH zone) ───────────────────────────────────────
    if not in_ath_zone:
        t1_raw, t1_adj, t1_well = _sell_limit_price(spot, params.t1_pct, params.gw_offset)
        t1_usd = weekly * (params.t1_size_pct / 100)
        t1_qty = t1_usd / spot

        if state.btc_holdings >= t1_qty:
            plan.orders.append(Order(
                side="SELL", order_type="LIMIT",
                price=t1_adj, quantity_btc=t1_qty, usd_value=t1_qty * t1_adj,
                label="T1",
                note=f"{params.t1_pct}% over spot → raw ${t1_raw:,.0f} → "
                     f"-${params.gw_offset:.0f} below ${t1_well:,.0f} well | "
                     f"{params.t1_size_pct:.0f}% of weekly"
            ))
        else:
            plan.warnings.append(
                f"Tier 1 sell requires {t1_qty:.5f} BTC but holdings = "
                f"{state.btc_holdings:.5f} BTC — order skipped"
            )

    # ── TIER 2 ATH SELL ──────────────────────────────────────────────────────
    if in_ath_zone:
        t2_qty = state.btc_holdings * (params.t2_size_pct / 100)
        t2_usd = t2_qty * spot
        if t2_qty > 0:
            plan.orders.append(Order(
                side="SELL", order_type="LIMIT (ATH)",
                price=spot, quantity_btc=t2_qty, usd_value=t2_usd,
                label="T2-ATH",
                note=f"Sell {params.t2_size_pct:.0f}% of {state.btc_holdings:.5f} BTC holdings "
                     f"≈ ${t2_usd:,.0f}"
            ))
        else:
            plan.warnings.append("Tier 2 ATH: zero BTC holdings — nothing to sell")

    return plan


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _buy_limit_price(spot: float, pct: float, offset: float) -> tuple[float, float, float]:
    raw = spot * (1 - pct / 100)
    adj, well = adjust_buy_for_well(raw, offset)
    return raw, adj, well


def _sell_limit_price(spot: float, pct: float, offset: float) -> tuple[float, float, float]:
    raw = spot * (1 + pct / 100)
    adj, well = adjust_sell_for_well(raw, offset)
    return raw, adj, well


def _pct(a: float, b: float) -> float:
    return (a / b * 100) if b else 0.0


def _validate_inputs(state: WeeklyState, params: StrategyParams, plan: OrderPlan) -> None:
    if state.spot <= 0:
        plan.warnings.append("FATAL: spot price must be > 0")
    if state.ath <= 0:
        plan.warnings.append("FATAL: ATH must be > 0")
    if state.ma_price <= 0:
        plan.warnings.append("FATAL: MA price must be > 0")
    if params.bl2_pct <= params.bl1_pct:
        plan.warnings.append(
            f"WARNING: BL2 discount ({params.bl2_pct}%) should exceed "
            f"BL1 discount ({params.bl1_pct}%) — orders may overlap"
        )
    if params.gw_offset < 10 or params.gw_offset > 100:
        plan.warnings.append(
            f"WARNING: gravity well offset ${params.gw_offset:.0f} outside "
            f"recommended $10–$100 range"
        )
    if state.btc_holdings < 0:
        plan.warnings.append("WARNING: negative BTC holdings — check inputs")