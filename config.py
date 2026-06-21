"""
Central configuration for the BTC DCA strategy.
All parameterized ranges and fixed constants live here.
"""

# ── FIXED CONSTANTS ────────────────────────────────────────────────────────────
WEEKLY_DEPOSIT_USD = 725          # Weekly cash allocated to BTC
ROUND_NUMBER_STEP  = 1_000        # Gravity well resolution ($1k increments)

# ── PARAMETER SEARCH SPACE (for optimizer) ─────────────────────────────────────
PARAM_RANGES = {
    # Buy limit 1: X% under spot
    "bl1_pct":  [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],

    # Buy limit 2: X% under spot  (must stay > bl1_pct — enforced in optimizer)
    "bl2_pct":  [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0],

    # Tier 1 sell premium: X% over spot
    "t1_pct":   [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0],

    # Moving average window in weeks
    "ma_weeks": [4, 6, 8, 10, 12, 16, 20, 26],

    # Tier 1 sell size as % of weekly deposit
    "t1_size_pct": [10, 20, 30, 40, 50, 60, 70, 80, 100],

    # ATH trigger: sell Tier 2 when price > X% of ATH
    "ath_trigger_pct": [110, 115, 120, 125, 130],

    # Gravity well offset in USD (applied to all buy/sell adjustments)
    "gw_offset": [10, 25, 50, 75, 100],

    # Null-buy exception: % of weekly to buy at spot when neither limit filled
    "carry_spot_pct": [0, 25, 50, 75, 100],
}

# ── DEFAULTS (used by calculator when not optimized) ──────────────────────────
DEFAULTS = {
    "bl1_pct":         2.5,
    "bl2_pct":         8.0,
    "t1_pct":          8.0,
    "ma_weeks":        12,
    "t1_size_pct":     40,
    "ath_trigger_pct": 115,
    "t2_size_pct":     10,   # % of total holdings to sell per ATH tier event
    "gw_offset":       50,
    "carry_spot_pct":  50,
}