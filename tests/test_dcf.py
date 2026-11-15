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


# ================================================================ rà soát 15/11/2026 — cầu nối EV → vốn chủ
def _fs_assoc(eq_income, op=1000.0, pretax=None, fin_net=0.0, book=5000.0):
    pretax = op + fin_net + eq_income if pretax is None else pretax
    return {"cur": {"borrowings": 300.0, "cash": 100.0, "equity": 4000.0, "equity_parent": 3800.0, "op": op,
                    "pretax": pretax, "fin_income": max(fin_net, 0.0), "fin_cost": max(-fin_net, 0.0),
                    "equity_income": eq_income, "equity_method": book}}


def test_bridge_adds_associates_only_when_outside_operating_profit():
    # lãi liên kết 400 nằm DƯỚI LN HĐ: LNTT − LN HĐ − tài chính ≈ 400 → EBIT không chứa → cộng giá trị sổ sách
    out = DCF.bridge(_fs_assoc(400.0))
    assert out["associates"] == 5000.0 and out["net_debt"] == 200.0 and out["minority"] == 200.0
    # holding ghi lãi liên kết TRONG LN HĐ: phần còn lại ≈ 0 ≠ lãi liên kết → không cộng (tránh đếm hai lần)
    assert DCF.bridge(_fs_assoc(400.0, pretax=1010.0))["associates"] == 0.0
    # không đọc được lãi liên kết → không kết luận → không cộng
    fs = _fs_assoc(400.0)
    fs["cur"]["equity_income"] = None
    assert DCF.bridge(fs)["associates"] == 0.0


def test_ev_ebit_uses_operating_ev_and_adds_associates_back():
    fs = _fs_assoc(400.0)
    # EV của phần tạo ra EBIT = vốn hóa 10.000 + nợ ròng 200 + thiểu số 200 − liên kết 5.000 = 5.400
    assert DCF.ev_ebit_of(10_000e8, 1000.0, fs) == pytest.approx(5.4)
    peers = [{"EV/EBIT": 5.0}, {"EV/EBIT": 6.0}, {"EV/EBIT": 7.0}]
    out = DCF.ev_ebit_ttm(1000.0, fs, peers, shares=1e8)
    # 6 × 1.000 − 200 − 200 + 5.000 = 10.600억 → 10.600 KRW/cp
    assert out["value"] == pytest.approx(10_600.0)
    assert any(r["Khoản mục"].startswith("+ Công ty liên kết") for r in out["rows"])
    # không có khoản liên kết → bảng không thêm dòng trống
    plain = DCF.ev_ebit_ttm(1000.0, {"cur": {"borrowings": 300.0, "cash": 100.0}}, peers, shares=1e8)
    assert plain["value"] == pytest.approx(5_800.0) and not any("liên kết" in r["Khoản mục"] for r in plain["rows"])


def test_quarter_forecast_eps_follows_parent_profit():
    qs = [_q("202509", 100, 10, 8), _q("202512", 125, 12, 10), _q("202603", 110, 11, 9, 90), _q("202606", 110, 11, 9, 90)]
    fc = [{"Năm": "2026", "Doanh thu (억)": 440.0, "LN HĐ (억)": 44.0, "LN ròng (억)": 36.0, "LN cổ đông mẹ (억)": 18.0}]
    out = DCF.quarter_forecast(qs, fc, shares=1e7)
    fut = [r for r in out if r["Loại"] == "Dự phóng"]
    assert len(fut) == 2 and sum(r["LN ròng (억)"] for r in fut) == pytest.approx(18.0)
    for r in fut:                                                    # cổ đông mẹ hưởng 50% → EPS bằng nửa LN/cp
        assert r["EPS"] == pytest.approx(r["LN ròng (억)"] * 0.5 * 1e8 / 1e7)


def test_associates_test_ignores_sign_of_finance_cost():
    # cùng một báo cáo, chi phí tài chính ghi số âm hay dương phải cho cùng kết luận
    pos = _fs_assoc(400.0, fin_net=-50.0)
    neg = _fs_assoc(400.0, fin_net=-50.0)
    neg["cur"]["fin_cost"] = -neg["cur"]["fin_cost"]
    assert DCF.bridge(pos)["associates"] == DCF.bridge(neg)["associates"] == 5000.0
