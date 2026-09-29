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
