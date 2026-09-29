from halal_trader.macro import _label_vix


def test_label_vix_bands():
    assert _label_vix(12.0) == "calm"
    assert _label_vix(14.99) == "calm"
    assert _label_vix(15.0) == "normal"
    assert _label_vix(19.99) == "normal"
    assert _label_vix(20.0) == "elevated"
    assert _label_vix(29.99) == "elevated"
    assert _label_vix(30.0) == "very elevated"
    assert _label_vix(50.0) == "very elevated"
