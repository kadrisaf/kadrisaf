from datetime import date

from halal_trader.universe_health import collect, parse_exclusions, render

REPORT = """# Halal short-term watchlist -- {d}

## Excluded on Shariah screen

- **ORCL**: debt/market-cap 41% >= 33%
- **AIR.PA**: excluded business activity: 'Aerospace & Defense' matches keyword 'defense'

## Shariah-compliant, but no qualifying setup today

- **AAPL**: relative volume 1.1x < 1.2x
"""


def test_parse_exclusions_reads_only_the_shariah_section():
    ex = parse_exclusions(REPORT.format(d="2026-10-01"))
    assert set(ex) == {"ORCL", "AIR.PA"}
    assert "AAPL" not in ex


def test_collect_counts_across_reports_and_render_flags_persistent(tmp_path):
    for d in ["2026-10-01", "2026-10-02", "2026-10-03"]:
        (tmp_path / f"{d}.md").write_text(REPORT.format(d=d))
    old = REPORT.format(d="2026-08-01").replace("ORCL", "OLDCO")
    (tmp_path / "2026-08-01.md").write_text(old)  # outside the window

    rows, n = collect(str(tmp_path), days=30, today=date(2026, 10, 13))
    assert n == 3
    assert {r["symbol"]: r["count"] for r in rows} == {"ORCL": 3, "AIR.PA": 3}

    md = render(rows, n, 30, date(2026, 10, 13))
    assert "**ORCL** | 3/3 reports" in md and "candidates for removal" in md
