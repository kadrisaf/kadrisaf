from halal_trader.data_provider import CompanyFundamentals
from halal_trader.shariah_screen import screen_company


def _fundamentals(**overrides):
    base = dict(
        symbol="TST",
        sector="Technology",
        industry="Software—Application",
        market_cap=100_000_000_000,
        total_debt=1_000_000_000,       # 1% of market cap
        cash_and_short_term_investments=2_000_000_000,  # 2%
        receivables=1_000_000_000,      # 1%
        currency="USD",
    )
    base.update(overrides)
    return CompanyFundamentals(**base)


def test_low_debt_tech_company_passes():
    result = screen_company(_fundamentals())

    assert result.compliant is True
    assert result.reasons == []
    assert result.debt_ratio == 0.01


def test_conventional_bank_is_excluded_by_sector():
    result = screen_company(
        _fundamentals(sector="Financial Services", industry="Banks—Regional")
    )

    assert result.compliant is False
    assert any("business activity" in r for r in result.reasons)


def test_alcohol_producer_is_excluded_by_sector():
    result = screen_company(
        _fundamentals(sector="Consumer Defensive", industry="Beverages - Brewers")
    )

    assert result.compliant is False
    assert any("business activity" in r for r in result.reasons)


def test_high_debt_company_is_excluded_by_ratio():
    result = screen_company(
        _fundamentals(total_debt=40_000_000_000)  # 40% of market cap
    )

    assert result.compliant is False
    assert any("debt/market-cap" in r for r in result.reasons)
    assert result.debt_ratio == 0.4


def test_high_cash_and_interest_securities_is_excluded_by_ratio():
    result = screen_company(
        _fundamentals(cash_and_short_term_investments=35_000_000_000)  # 35%
    )

    assert result.compliant is False
    assert any("cash & interest-bearing" in r for r in result.reasons)


def test_high_receivables_is_excluded_by_ratio():
    result = screen_company(_fundamentals(receivables=34_000_000_000))  # 34%

    assert result.compliant is False
    assert any("receivables/market-cap" in r for r in result.reasons)


def test_missing_data_is_noted_but_does_not_fail_the_screen():
    result = screen_company(
        _fundamentals(market_cap=None, total_debt=None, cash_and_short_term_investments=None, receivables=None)
    )

    assert result.compliant is True
    assert any("market cap" in n for n in result.notes)
