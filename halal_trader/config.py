"""Tunable parameters for the screening and signal engine.

Nothing in this file is a Shariah ruling. The thresholds below follow the
widely-cited Dow Jones Islamic Market / S&P Shariah "33% of market cap"
methodology as a common industry approximation. Different scholars and
indices (AAOIFI, MSCI, FTSE) use slightly different denominators and cutoffs
-- adjust these constants if you follow a specific standard or a specific
scholar's ruling.
"""

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Shariah business-activity (sector/industry) screen
# ---------------------------------------------------------------------------
# Any stock whose sector or industry string (as reported by the data
# provider) contains one of these substrings (case-insensitive) is excluded
# outright, regardless of its financial ratios. This list is deliberately
# conservative. Edit it if your own understanding of a category differs
# (e.g. some scholars permit non-offensive defense contractors, Islamic
# banks/takaful obviously should not be excluded but generic data providers
# rarely distinguish them from conventional banks -- flag those for manual
# review instead of trusting the screen blindly).
EXCLUDED_SECTOR_KEYWORDS = [
    "bank",              # conventional, interest-based banking
    "insurance",         # conventional insurance (takaful is an exception,
                          # but is not distinguishable from this field alone)
    "capital markets",   # brokerages / interest-based financial services
    "asset management",
    "credit services",
    "mortgage",
    "beverages - wineries",
    "beverages - brewers",
    "distillers",
    "tobacco",
    "gambling",
    "casino",
    "resorts & casinos",
    "adult",             # adult entertainment
    "pornography",
    "pork",
    "defense",           # weapons/arms manufacturers (conservative default)
    "aerospace & defense",
]

# Financial ratio thresholds (each ratio must be STRICTLY BELOW this value).
# Denominator is market capitalization, per the common DJIM/S&P methodology.
MAX_DEBT_TO_MARKET_CAP = 0.33
MAX_CASH_AND_INTEREST_SECURITIES_TO_MARKET_CAP = 0.33
MAX_RECEIVABLES_TO_MARKET_CAP = 0.33

# Optional: non-permissible (interest/haram) income as a fraction of
# revenue. Many providers don't expose this cleanly; when unavailable the
# screen just records "insufficient data" rather than failing the stock.
MAX_NON_PERMISSIBLE_INCOME_RATIO = 0.05


# ---------------------------------------------------------------------------
# Short-term technical signal (max holding period = 1 trading week)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalParams:
    max_holding_days: int = 5          # 1 trading week
    rsi_period: int = 14
    rsi_min: float = 45.0              # avoid deeply oversold / falling knives
    rsi_max: float = 65.0              # avoid already-overbought entries
    fast_sma: int = 20
    slow_sma: int = 50
    min_relative_volume: float = 1.2   # today's volume vs its 20d average
    atr_period: int = 14
    # Widened from 1.5/2.25 on 2026-10-06: ahead in both halves of the
    # split-sample test and the full-period grid (see backtests/2026-10-06-*).
    # Same 1.5:1 ratio; a 2.0-ATR stop loses more per share when hit, so the
    # sizing formula must set the position size (same euro risk = fewer shares).
    stop_atr_multiple: float = 2.0
    target_atr_multiple: float = 3.0   # gives a >=1.5:1 reward:risk
    min_avg_dollar_volume: float = 5_000_000  # liquidity floor (20d avg $ vol)
    # Warning-only thresholds (never disqualify a candidate -- untested):
    near_high_warn_pct: float = 3.0    # warn if within this % of the 52-week high
    min_rr_to_high: float = 1.0        # warn if reward:risk up to the 52w high is below this
    shock_lookback_days: int = 5       # post-shock warning: look back this many bars
    shock_move_pct: float = 8.0        # ...flag any single-day move beyond +/- this %


DEFAULT_SIGNAL_PARAMS = SignalParams()


# ---------------------------------------------------------------------------
# Starter research universe.
# ---------------------------------------------------------------------------
# A candidate list of large, liquid, well-known tickers to *check*, not a
# list of stocks that are already known to be halal. Every one of them still
# has to pass the sector + ratio screen in shariah_screen.py. Feel free to
# replace/extend this with your own watchlist (see data/universe_default.csv).
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "NVDA", "AVGO", "ADBE", "CRM", "ORCL", "CSCO",
    "AMD", "QCOM", "TXN", "INTU", "NOW", "PANW", "SNPS", "CDNS",
    "COST", "PG", "KO", "PEP", "MDLZ", "CL", "EL",
    "JNJ", "UNH", "LLY", "ABBV", "MRK", "TMO", "ABT", "DHR", "ISRG",
    "XOM", "CVX",
    "HD", "NKE", "MCD", "SBUX", "LOW",
    "LIN", "APD", "SHW",
    "ASML", "SAP", "SIE.DE", "AIR.PA", "MC.PA",
    "CCJ",  # Cameco -- uranium miner, added for uranium-theme screening
    # -- expanded Sept 2026: broader sector/geography coverage --
    "TSM", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ON",  # semis
    "ADSK", "WDAY", "TEAM", "SHOP",  # software
    "VRTX", "REGN", "GILD", "AMGN",  # biotech
    "SYK", "BSX", "MDT", "EW", "ZTS",  # healthcare devices/animal health
    "CAT", "DE", "HON", "UPS",  # industrials
    "FCX", "NEM", "ECL",  # materials/mining
    "COP", "SLB", "EOG",  # energy
    "CMG", "YUM", "TJX", "ROST",  # consumer
    "ADS.DE", "BAS.DE", "OR.PA", "DTE.DE",  # EU
]


# ---------------------------------------------------------------------------
# Trading calendar / execution assumptions
# ---------------------------------------------------------------------------
# NYSE full-day closures (Columbus Day is NOT one). Used only to count the
# 5-trading-day hold window; extend this list when it runs out.
NYSE_HOLIDAYS = [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]

# Assumed worse-than-trigger fill on market-style exits (stop / time-stop),
# in percent. The tracker applies it; targets are limit orders (no slippage).
EXIT_SLIPPAGE_PCT = 0.1

# Portfolio-risk warning thresholds (report warnings only, never filters).
CORRELATION_WARN = 0.7     # flag a candidate this correlated with an open position
FEE_PER_ORDER_EUR = 1.0    # Trade Republic flat fee per executed order
TYPICAL_POSITION_EUR = 500 # position size the report's cost-drag note assumes

# Order-ticket sizing (report output only -- nothing places orders).
# position size = (PORTFOLIO_EUR * RISK_PCT_PER_TRADE%) / (entry - stop).
# RISK_PCT_PER_TRADE set by the user: 5.0 on 2026-10-06, lowered to 2.0 the
# same day. The report warns next to the tickets whenever it exceeds 2%.
PORTFOLIO_EUR = 1405.0        # update when the account value changes (positions only, cash unknown; 2026-10-06 10:33)
RISK_PCT_PER_TRADE = 2.0
MAX_POSITION_PCT_OF_PORTFOLIO = 100.0  # hard cap: never size past the account (no leverage)
