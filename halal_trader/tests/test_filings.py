from halal_trader.filings import FilingItem, _parse_filings

CIK = 1045810  # NVIDIA's real CIK, used only as a realistic fixture value


def _submissions(forms, dates, accessions, docs):
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "primaryDocument": docs,
            }
        }
    }


def test_parse_filings_filters_to_requested_forms_and_builds_url():
    data = _submissions(
        forms=["10-Q", "8-K", "4"],
        dates=["2026-09-01", "2026-08-28", "2026-08-20"],
        accessions=["0001045810-26-000073", "0001045810-26-000070", "0001045810-26-000065"],
        docs=["q2fy27pr.htm", "item8k.htm", "form4.xml"],
    )

    items = _parse_filings(data, CIK, forms=("8-K",), limit=3)

    assert items == [
        FilingItem(
            form="8-K",
            filed="2026-08-28",
            url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000070/item8k.htm",
        )
    ]


def test_parse_filings_respects_limit():
    data = _submissions(
        forms=["8-K", "8-K", "8-K"],
        dates=["2026-09-01", "2026-08-15", "2026-07-01"],
        accessions=["0001-26-000001", "0001-26-000002", "0001-26-000003"],
        docs=["a.htm", "b.htm", "c.htm"],
    )

    items = _parse_filings(data, CIK, forms=("8-K",), limit=2)

    assert len(items) == 2
    assert [i.filed for i in items] == ["2026-09-01", "2026-08-15"]


def test_parse_filings_returns_empty_list_for_malformed_payload():
    assert _parse_filings({}, CIK, forms=("8-K",), limit=3) == []
    assert _parse_filings({"filings": {}}, CIK, forms=("8-K",), limit=3) == []
    assert _parse_filings({"filings": {"recent": {}}}, CIK, forms=("8-K",), limit=3) == []


def test_parse_filings_defaults_to_no_8k_present():
    data = _submissions(
        forms=["10-Q", "4"],
        dates=["2026-09-01", "2026-08-20"],
        accessions=["0001-26-000001", "0001-26-000002"],
        docs=["a.htm", "b.htm"],
    )

    assert _parse_filings(data, CIK, forms=("8-K",), limit=3) == []


FORM4_SALE = b"""<?xml version="1.0"?><ownershipDocument><nonDerivativeTable>
<nonDerivativeTransaction>
  <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
  <transactionAmounts><transactionShares><value>1000</value></transactionShares>
  <transactionPricePerShare><value>273.59</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
<nonDerivativeTransaction>
  <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
  <transactionAmounts><transactionShares><value>500</value></transactionShares>
  <transactionPricePerShare><value>10</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
<nonDerivativeTransaction>
  <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
  <transactionAmounts><transactionShares><value>200</value></transactionShares>
  <transactionPricePerShare><value>270</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_parse_form4_counts_only_open_market_codes():
    from halal_trader.filings import _parse_form4_xml

    sells, buys, sold = _parse_form4_xml(FORM4_SALE)
    assert (sells, buys) == (1, 1)  # the 'M' option exercise is ignored
    assert sold == 1000 * 273.59


def test_recent_form4_urls_filters_by_form_and_window():
    from datetime import date
    from halal_trader.filings import _recent_form4_urls

    data = {"filings": {"recent": {
        "form": ["4", "8-K", "4", "4"],
        "filingDate": ["2026-10-08", "2026-10-07", "2026-09-01", "bad-date"],
        "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4"],
        "primaryDocument": ["xslF345X05/wk-form4.xml", "x.htm", "form4.xml", "f.xml"],
    }}}
    urls = _recent_form4_urls(data, 1835632, days=14, today=date(2026, 10, 13))
    assert len(urls) == 1
    assert urls[0].endswith("/000126 1/wk-form4.xml".replace(" ", "").replace("126 1", "126" + "1"))
    assert "xsl" not in urls[0]  # XSL directory stripped -> raw XML
