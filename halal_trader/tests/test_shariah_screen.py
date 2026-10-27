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


def _fund_with_summary(summary, **kw):
    from halal_trader.data_provider import CompanyFundamentals

    base = dict(symbol="X", sector="Industrials", industry="Specialty Industrial Machinery",
                market_cap=1e11, total_debt=1e9, cash_and_short_term_investments=1e9,
                receivables=1e9, currency="USD", business_summary=summary)
    base.update(kw)
    return CompanyFundamentals(**base)


def test_summary_scan_catches_defense_hidden_by_the_label():
    from halal_trader.shariah_screen import screen_company

    r = screen_company(_fund_with_summary(
        "The company supplies avionics and defense systems to military programs."))
    assert r.compliant is False
    assert any("business summary mentions 'defense'" in x for x in r.reasons)


def test_summary_allow_override_skips_only_the_summary_scan():
    from halal_trader.shariah_screen import screen_company

    f = _fund_with_summary("Provides defense-grade cybersecurity software.")
    assert screen_company(f).compliant is False
    assert screen_company(f, summary_cleared=True).compliant is True
    # ratios still apply even when summary-cleared
    f2 = _fund_with_summary("Provides defense-grade cybersecurity software.", total_debt=5e10)
    assert screen_company(f2, summary_cleared=True).compliant is False


def test_overrides_loader(tmp_path):
    from halal_trader.shariah_screen import load_overrides

    p = tmp_path / "ov.csv"
    p.write_text('symbol,action,reason,checked\nHON,exclude,"defense division",x\ncrwd,allow,"cleared on Zoya",x\nBAD,maybe,,x\n')
    ov = load_overrides(str(p))
    assert ov["HON"]["action"] == "exclude"
    assert ov["CRWD"]["action"] == "allow"
    assert "BAD" not in ov
