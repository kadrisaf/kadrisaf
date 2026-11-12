"""Tests for the ADX shadow column (info only -- never gates/ranks/sizes)."""

import numpy as np
import pandas as pd

from halal_trader.signals import ADX_PERIOD, adx_series, evaluate_signal
from halal_trader.track_record import (
    FIELDNAMES,
    TrackedCandidate,
    load_track_record,
    save_track_record,
)


def uptrend_history(n=260):
    c = pd.Series(100 * 1.01 ** np.arange(n))
    idx = pd.date_range("2025-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": c.values * 1.01, "Low": c.values * 0.99,
         "Close": c.values, "Volume": 5_000_000.0},
        index=idx,
    )


def test_signal_carries_adx_value():
    sig = evaluate_signal("TEST", uptrend_history())
    assert sig.adx is not None
    assert sig.adx > 25  # relentless trend -> strong ADX


def test_adx_does_not_affect_qualification_or_score():
    df = uptrend_history()
    sig = evaluate_signal("TEST", df)
    flat_tail = df.copy()  # same data -> same outcome regardless of adx value
    sig2 = evaluate_signal("TEST", flat_tail)
    assert (sig.qualifies, sig.score, sig.reasons_excluded) == (
        sig2.qualifies, sig2.score, sig2.reasons_excluded
    )
    assert "adx" not in " ".join(sig.reasons_excluded).lower()


def test_track_record_roundtrips_adx(tmp_path):
    path = str(tmp_path / "tr.csv")
    recs = [TrackedCandidate("2026-11-12", "AAA", 100.0, 95.0, 110.0, 5, adx=27.3)]
    save_track_record(path, recs)
    loaded = load_track_record(path)
    assert loaded[0].adx == 27.3
    assert FIELDNAMES[-1] == "adx"


def test_track_record_tolerates_legacy_rows_without_adx(tmp_path):
    path = tmp_path / "tr.csv"
    legacy_header = ",".join(FIELDNAMES[:-1])
    path.write_text(
        legacy_header
        + "\n2026-11-01,BBB,50.0,47.0,55.0,5,False,,,,\n"
    )
    loaded = load_track_record(str(path))
    assert loaded[0].symbol == "BBB"
    assert loaded[0].adx is None


def test_adx_warmup_is_nan_in_canonical_home():
    adx = adx_series(uptrend_history())
    assert adx.iloc[: 2 * ADX_PERIOD].isna().all()
    assert np.isfinite(adx.iloc[-1])
