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
    stop_atr_multiple: float = 1.5
    target_atr_multiple: float = 2.25  # gives a >=1.5:1 reward:risk
    min_avg_dollar_volume: float = 5_000_000  # liquidity floor (20d avg $ vol)


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
]
