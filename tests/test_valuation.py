"""Kiểm tra toán định giá (không cần mạng)."""
import pandas as pd
import pytest

import valuation as V

A = V.Assumptions(erp=0.065)  # các phép tính tay bên dưới: Rf 3.2%, ERP 6.5%, beta 1.0 → Ke 9.7%


def test_ke_and_beta_clamp():
    assert A.ke() == pytest.approx(0.097)
    assert A.ke_with(5.0) == pytest.approx(0.032 + 1.8 * 0.065)   # kẹp trên 1.8
    assert A.ke_with(0.1) == pytest.approx(0.032 + 0.5 * 0.065)   # kẹp dưới 0.5


def test_default_erp_is_damodaran_and_stock_beta_is_blume_adjusted():
    d = V.Assumptions()
    assert d.erp == pytest.approx(0.0487)                         # Damodaran Hàn Quốc 01/2026 (như lab)
    assert d.ke_stock(1.74) == pytest.approx(0.032 + (0.67 * 1.74 + 0.33) * 0.0487)   # Hynix 52T
    assert d.ke_stock(0.1) == pytest.approx(0.032 + (0.67 * 0.5 + 0.33) * 0.0487)     # kẹp rồi Blume
    assert d.ke_stock(None) == pytest.approx(d.ke())              # không có beta riêng → beta mặc định
    assert V.Assumptions(blume=False).ke_stock(1.74) == pytest.approx(0.032 + 1.74 * 0.0487)


def test_srim_known_value():
    # RI = (15% − 9.7%) × 100k = 5,300 ; Fair = BPS + RI·w/(1+Ke−w)
    assert V.srim(100_000, 0.15, 0.097, 0.9) == pytest.approx(100_000 + 5_300 * 0.9 / 0.197)


def test_justified_pb_guards():
    assert V.justified_pb_value(100_000, 0.01, 0.097, 0.02) is None      # ROE ≤ g → vỡ
    assert V.justified_pb_value(100_000, 0.15, 0.097, 0.02) == pytest.approx(
        100_000 * 0.13 / 0.077)


def test_rim_multistage_differs_and_respects_ke():
    hi = V.rim_multistage(100_000, 0.20, 0.097, 5, 0.02, payout=0.0)
    assert hi > V.justified_pb_value(100_000, 0.20, 0.097, 0.02)    # lăn vốn chủ → cao hơn
    assert V.rim_multistage(100_000, 0.08, 0.097, 5, 0.02, payout=0.5) < 100_000  # ROE<Ke


def test_ev_ebit_value():
    # EBIT 100억 × median 10 = EV 1,000억 − nợ ròng 200억 = 800억 / 1e6 cp = 80,000원
    assert V.ev_ebit_value(100, 200, 1_000_000, 10) == pytest.approx(80_000)
    assert V.ev_ebit_value(-5, 0, 1_000_000, 10) is None             # EBIT âm
    assert V.ev_ebit_value(100, 2_000, 1_000_000, 10) is None        # vốn chủ âm
    assert V.ev_ebit_value(100, 0, 1_000_000, None) is None          # thiếu peer


def test_fcff_requires_positive_free_cash_flow():
    kw = dict(debt_val=0, net_debt=0, shares=1e6, ke=0.1, kd=0.05, tax=0.22,
              g_high=0.05, years=5, g_term=0.02, equity_val=1_000)
    assert V.fcff_value(cfo=100, capex=500, **kw) is None            # đốt tiền
    assert V.fcff_value(cfo=500, capex=100, **kw) > 0


def test_normalized_metrics_falls_back_to_naver_roe():
    hist = [{"net_profit": 1, "roe": 5.0}, {"net_profit": 1, "roe": 15.0},
            {"net_profit": 1, "roe": 25.0}]
    m = V.normalized_metrics(hist)
    assert m["median_roe"] == pytest.approx(0.15)
    assert m["n_years"] == 3


def _row(t, sec, per, pbr=1.0):
    return {"ticker": t, "name": t, "sector": sec, "price": 10_000, "eps": 1_000,
            "bps": 20_000, "dps": 300, "per": per, "pbr": pbr}


def test_value_batch_needs_three_peers():
    df = pd.DataFrame([_row("A", "Bank", 10.0), _row("B", "Bank", 8.0),
                       _row("C", "Bank", 12.0), _row("D", "Telecom", 15.0)])
    res, med = V.value_batch(df, A)
    assert med["Bank"]["pe"] == pytest.approx(10.0)
    assert med["Telecom"]["pe"] is None                 # 1 mã → không so với chính nó
    d = next(r for r in res if r.ticker == "D")
    assert d.methods.get("P/E vs median ngành") is None


def test_value_batch_excludes_pe_outliers():
    df = pd.DataFrame([_row(t, "X", p) for t, p in
                       [("a", 10.0), ("b", 12.0), ("c", 14.0), ("d", 336.0), ("e", -5.0)]])
    _, med = V.value_batch(df, A)
    assert med["X"]["pe"] == pytest.approx(12.0)        # bỏ 336 và P/E âm


def test_peer_key_falls_back_to_industry():
    assert V.peer_key({"sector": None, "industry_kr": "조선"}) == "조선"
    assert V.peer_key({"sector": "Bank", "industry_kr": "은행"}) == "Bank"


def test_cyclical_uses_midcycle_when_history_given():
    row = {"ticker": "S", "name": "s", "sector": "Shipbuilding", "price": 100_000,
           "eps": 20_000, "bps": 100_000, "dps": 0, "per": 5.0, "pbr": 1.0}
    hist = [{"net_profit": 1, "roe": 2.0}, {"net_profit": 1, "roe": 10.0},
            {"net_profit": 1, "roe": 18.0}]
    res, _ = V.value_batch(pd.DataFrame([row]), A, histories={"S": hist})
    assert "mid-cycle" in res[0].auto_method
    assert res[0].confidence == "Low"                   # 3 năm < 1 chu kỳ → chưa tin cao
    hist6 = hist + [{"net_profit": 1, "roe": r} for r in (8.0, 12.0, 14.0)]
    res6, _ = V.value_batch(pd.DataFrame([row]), A, histories={"S": hist6})
    assert res6[0].confidence == "Medium"               # đủ ≥5 năm → Medium


def _rv(roe, price=100_000, bps=100_000, dps=0, sector="Insurance", **kw):
    row = {"ticker": "T", "name": "t", "sector": sector, "price": price, "eps": roe * bps,
           "bps": bps, "dps": dps}
    row.update(kw)
    return row


def test_book_intrinsic_is_median_of_three_not_degenerate_pb_roe():
    # ROE 2,5% ≈ g 2%: P/B-ROE hợp lý ≈ 0,07× book (suy biến), S-RIM ≈ 0,63× → trung vị bộ ba
    r = V.value_stock(_rv(0.025), A)
    trio = [r.methods[k] for k in ("S-RIM", "P/B-ROE (justified)", "RIM nhiều giai đoạn") if r.methods.get(k)]
    assert len(trio) == 3 and r.methods["P/B-ROE (justified)"] < 0.1 * 100_000
    assert r.fair_raw == pytest.approx(sorted(trio)[1])
    assert "trung vị 3 PP" in r.auto_method


def test_triangulates_with_peer_anchor_when_intrinsic_far():
    # nội tại ≈ 1,7× book; peer P/E 5 × EPS 15.000 = 75.000 và P/B 0,7 → 70.000: lệch > 2× → 50/50
    r = V.value_stock(_rv(0.15, price=60_000, dps=3_000, sector="Bank"), A,
                      sector_median_pe=5.0, sector_median_pb=0.7)
    intr = sorted(r.methods[k] for k in ("S-RIM", "P/B-ROE (justified)", "RIM nhiều giai đoạn"))[1]
    anchor = sorted([r.methods["P/E vs median ngành"], r.methods["P/B vs median ngành"]])
    anchor = sum(anchor) / 2
    assert intr / anchor > 2 and r.triangulated and r.status.startswith("△")
    assert r.fair_raw == pytest.approx(0.5 * intr + 0.5 * anchor)
    assert r.confidence == "Low"


def test_reliability_gate_withholds_extreme_fair_value():
    # Doosan-like: ROE 2,2% ≈ g, giá 2,7× book → fair < 0,4× giá → không công bố
    r = V.value_stock(_rv(0.022, price=270_000), A)
    assert r.fair_value is None and r.upside_pct is None and not r.reliable
    assert r.fair_raw and r.fair_raw / 270_000 < 0.4
    assert r.status.startswith("⚪") and r.confidence == "Low"
    assert r.to_dict()["fair_value"] is None and r.to_dict()["fair_raw"] == round(r.fair_raw)
    ok = V.value_stock(_rv(0.12, price=150_000), A)            # trong vùng → công bố
    assert ok.reliable and ok.fair_value and 0.4 <= ok.fair_value / 150_000 <= 2.5
