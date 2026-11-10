"""dcf.py — dự phóng quý, FCFF-DCF, EV/EBIT forward (không mạng)."""
import pytest

import dcf as DCF


def _q(p, rev, op=None, np_=None, eps=None):
    return {"period": p, "is_consensus": False, "revenue": rev, "op_profit": op, "net_profit": np_, "eps": eps}


def test_quarter_forecast_sums_to_annual_and_keeps_reported():
    qs = [_q("202509", 100, 10, 8), _q("202512", 125, 12, 10), _q("202603", 110, 11, 9), _q("202606", 110, 11, 9)]
    fc = [{"Năm": "2026", "Doanh thu (억)": 700, "LN HĐ (억)": 70, "LN ròng (억)": 56},
          {"Năm": "2027", "Doanh thu (억)": 800, "LN HĐ (억)": 80, "LN ròng (억)": 64}]
    out = DCF.quarter_forecast(qs, fc, shares=1e7)
    y26 = [r for r in out if r["Quý"].startswith("2026")]
    assert [r["Loại"] for r in y26] == ["Thực", "Thực", "Dự phóng", "Dự phóng"]
    assert sum(r["DT (억)"] for r in y26) == pytest.approx(700)
    assert sum(r["LN ròng (억)"] for r in y26) == pytest.approx(56)
    # mùa vụ ổn định (lệch ≤ 1,3×): Q4 (12) = 1,25× Q3 (09) trong 4 quý thực → phần còn lại chia 100:125
    q3, q4 = y26[2]["DT (억)"], y26[3]["DT (억)"]
    assert q4 == pytest.approx(1.25 * q3)
    y27 = [r for r in out if r["Quý"].startswith("2027")]
    assert len(y27) == 4 and sum(r["DT (억)"] for r in y27) == pytest.approx(800)


def _fs(**over):
    cur = dict(revenue=1000.0, ppe=400.0, intangibles=100.0, current_assets=300.0, cash=100.0, st_deposits=0.0,
               current_liab=100.0, borrowings=200.0, equity=800.0, equity_parent=750.0, pretax=100.0, tax=22.0)
    cur.update(over)
    return {"year": 2025, "fs_div": "CFS", "cur": cur, "prev": {}}


def test_fcff_no_growth_equals_perpetuity():
    """Không tăng trưởng (g = 0, doanh thu phẳng): FCFF = NOPAT mọi năm → EV = NOPAT / WACC."""
    fc = [{"Năm": str(2026 + i), "Doanh thu (억)": 1000.0, "LN HĐ (억)": 100.0} for i in range(5)]
    out = DCF.fcff_dcf(fc, _fs(), ke=0.10, rf=0.03, g=0.0, market_cap=2000e8, shares=1e7, tax=0.22)
    w = out["wacc"]
    assert out["ev"] == pytest.approx(78.0 / w, rel=1e-6)
    br = {b["Khoản mục"]: b["억원"] for b in out["bridge"]}
    assert br["= Giá trị vốn chủ"] == pytest.approx(out["ev"] - (200 - 100) - 50)       # nợ ròng 100, thiểu số 50
    assert all(r["Tái đầu tư (억)"] == pytest.approx(0) for r in out["rows"])


def test_fcff_growth_requires_reinvestment():
    fc = [{"Năm": "2026", "Doanh thu (억)": 1100.0, "LN HĐ (억)": 110.0}]
    out = DCF.fcff_dcf(fc, _fs(), ke=0.10, rf=0.03, g=0.02, market_cap=2000e8, shares=1e7, tax=0.22)
    s2c = 1000 / (400 + 100 + 100)                       # vốn đầu tư = 400 + 100 + (300 − 100 − 100)
    assert out["rows"][0]["Tái đầu tư (억)"] == pytest.approx(100 / s2c)
    assert DCF.fcff_dcf(fc, _fs(), 0.10, 0.03, 0.02, 2000e8, 1e7) is not None
    assert DCF.fcff_dcf([{"Năm": "2026", "Doanh thu (억)": 1, "LN HĐ (억)": None}], _fs(), 0.1, 0.03, 0.02,
                        2000e8, 1e7) is None


def test_ev_ebit_ttm_needs_three_peers():
    qs = [_q(p, 250, 25) for p in ("202509", "202512", "202603", "202606")]
    assert DCF.ttm_ebit(qs) == pytest.approx(100)
    assert DCF.ttm_ebit(qs[:3]) is None
    peers = [{"EV/EBIT": 8.0}, {"EV/EBIT": 10.0}]
    assert DCF.ev_ebit_ttm(100.0, _fs(), peers, 1e7) is None
    peers.append({"EV/EBIT": 12.0})
    peers.append({"EV/EBIT": 80.0})                      # ngoài dải → loại
    out = DCF.ev_ebit_ttm(100.0, _fs(), peers, 1e7)
    assert out["median"] == pytest.approx(10.0) and out["n"] == 3
    assert out["value"] == pytest.approx((1000 - 100 - 50) * 1e8 / 1e7)
    assert DCF.ev_ebit_of(1000e8, 100.0, _fs()) == pytest.approx((1000 + 100 + 50) / 100)


def test_seasonality_only_when_stable_and_negative_terminal_withheld():
    growing = [_q("202509", 100), _q("202512", 150), _q("202603", 200), _q("202606", 230)]
    assert DCF.seasonal_weights(growing)["_even"]
    stable = [_q("202509", 100), _q("202512", 120), _q("202603", 95), _q("202606", 105)]
    w = DCF.seasonal_weights(stable)
    assert not w["_even"] and w["12"] > w["03"]
    fc = [{"Năm": "2026", "Doanh thu (억)": 1000.0, "LN HĐ (억)": 50.0},
          {"Năm": "2027", "Doanh thu (억)": 1000.0, "LN HĐ (억)": -20.0}]
    out = DCF.fcff_dcf(fc, _fs(), 0.10, 0.03, 0.02, 2000e8, 1e7, tax=0.22)
    assert out["value"] is None and "≤ 0" in out["note"]
