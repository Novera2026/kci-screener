"""Quy trình định giá (pipeline.py) — dữ liệu giả, không cần mạng."""
import datetime as dt

import pytest

import pipeline as P
import valuation as V

A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)


def _series(n=5, rev0=1000.0, g=0.05, margin=0.10, eq0=5000.0, dps=500.0, shares=1e7, dart=True):
    """n năm thực: DT tăng g/năm, biên LN cố định, EPS = LN·1e8/số cp."""
    out, eq = [], eq0
    for i in range(n):
        rev = rev0 * (1 + g) ** i
        np_ = rev * margin
        eps = np_ * 1e8 / shares
        eq += np_ - dps * shares / 1e8
        out.append({"period": f"{2021 + i}12", "is_consensus": False, "revenue": rev, "op_profit": rev * 0.12,
                    "net_profit": np_, "equity": eq, "eps": eps, "bps": eq * 1e8 / shares, "dps": dps,
                    "roe": np_ / eq * 100, "debt_ratio": 80.0, "_dart": dart})
    return out


def _row(series, price=None, shares=1e7):
    last = series[-1]
    price = price or last["bps"] * 1.0
    return {"ticker": "000001", "name": "Test", "price": price, "market_cap": price * shares,
            "eps": last["eps"], "bps": last["bps"], "dps": last["dps"], "industry_kr": "식품", "sector": None}


def test_identify_buckets():
    assert P.identify({"industry_kr": "은행"})["financial"]
    assert P.identify({"industry_kr": "반도체와반도체장비"})["cyclical"]
    assert P.identify({"industry_kr": "전기유틸리티"})["dividend_model"]


def test_data_quality_clean_has_no_cap():
    s = _series()
    q = [{"period": "202609", "is_consensus": False}]
    dq = P.data_quality(_row(s), s, q, beta=1.0, today=dt.date(2026, 11, 9))
    assert dq["n_fail"] == 0 and dq["cap"] is None, dq["checks"]


def test_data_quality_negative_book_and_old_data_fail():
    s = _series(n=2)
    r = _row(s)
    r["bps"] = -100
    dq = P.data_quality(r, s, [], beta=None, today=dt.date(2026, 11, 9))
    assert dq["cap"] == "Low"
    assert dq["n_fail"] >= 2      # <3 năm + BPS âm


def test_ratio_table_flags_outlier_year():
    s = _series()
    s[2]["net_profit"] *= 5        # năm đột biến
    rows = P.ratio_table(s)
    assert rows[2]["Năm bất thường"]
    assert rows[1]["Tăng trưởng DT %"] == pytest.approx(5.0)


def test_default_drivers_fade_to_long_growth():
    s = _series(g=0.10)
    d = P.default_drivers(s, _row(s), A, years=5)
    assert len(d) == 5
    assert d[-1]["Tăng trưởng DT %"] == pytest.approx(A.g_term_capped() * 100)
    assert d[0]["Tăng trưởng DT %"] > d[-1]["Tăng trưởng DT %"]


def test_run_forecast_rolls_book():
    s = _series()
    r = _row(s)
    d = [{"Năm": "2026", "Tăng trưởng DT %": 10, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 50}]
    fc = P.run_forecast(s, r, d)
    f = fc[0]
    assert f["Doanh thu (억)"] == pytest.approx(s[-1]["revenue"] * 1.1)
    assert f["DPS"] == pytest.approx(f["EPS"] * 0.5)
    assert f["BPS cuối kỳ"] == pytest.approx(f["BPS đầu kỳ"] + f["EPS"] - f["DPS"])


def test_forecast_gates_block_absurd_growth_and_margin():
    s = _series()
    r = _row(s)
    bad = [{"Năm": "2026", "Tăng trưởng DT %": 80, "Biên LN ròng %": 40, "Tỷ lệ cổ tức %": 0}]
    g = P.forecast_gates(s, P.run_forecast(s, r, bad))
    assert not g["ok"] and g["n_fail"] >= 2
    ok = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 30}]
    assert P.forecast_gates(s, P.run_forecast(s, r, ok))["ok"]


def test_rim_explicit_equals_book_when_roe_equals_ke():
    """ROE = Ke ⇒ siêu lợi nhuận 0 ⇒ giá trị = BPS."""
    ke = 0.10
    fc = [{"EPS": 100.0, "BPS đầu kỳ": 1000.0}, {"EPS": 100.0, "BPS đầu kỳ": 1000.0}]
    out = P.rim_explicit(1000.0, fc, ke)
    assert out["value"] == pytest.approx(1000.0)
    assert P.rim_explicit(-5, fc, ke) is None


def test_ddm_explicit_gordon_consistency():
    ke, g = 0.08, 0.02
    fc = [{"DPS": 100.0}]
    v = P.ddm_explicit(fc, ke, g)["value"]
    assert v == pytest.approx(100 / 1.08 + 100 * 1.02 / 0.06 / 1.08)
    assert P.ddm_explicit([{"DPS": 0.0}], ke, g) is None


def test_reconcile_gates_extreme_values():
    cands = [{"name": "a", "value": 10_000, "role": "primary"},
             {"name": "b", "value": 11_000, "role": "primary"},
             {"name": "c", "value": 40_000, "role": "primary"},          # 4× giá → loại
             {"name": "d", "value": 9_000, "role": "cross", "blocked": "cổng dự phóng"}]
    rec = P.reconcile(10_000, cands, anchor=10_000, caps=[None])
    assert rec["fair"] == pytest.approx(10_500)
    assert rec["confidence"] == "High"
    passed = {r["name"]: r["pass"] for r in rec["rows"]}
    assert passed == {"a": True, "b": True, "c": False, "d": False}


def test_reconcile_withholds_when_nothing_passes_and_caps_confidence():
    rec = P.reconcile(10_000, [{"name": "a", "value": 1_000, "role": "primary"}], None, [None])
    assert rec["fair"] is None and rec["status"].startswith("⚪")
    rec = P.reconcile(10_000, [{"name": "a", "value": 10_000, "role": "primary"},
                               {"name": "b", "value": 10_500, "role": "primary"}], None, ["Low"])
    assert rec["confidence"] == "Low"
    # peer anchor xa > 4× → loại dù trong dải giá
    rec = P.reconcile(10_000, [{"name": "a", "value": 20_000, "role": "primary"}], anchor=4_000, caps=[])
    assert rec["fair"] is None


def test_peer_medians_excludes_self_and_needs_three():
    rows = [{"ticker": "x", "per": 100, "pbr": 9}, {"ticker": "a", "per": 10, "pbr": 1},
            {"ticker": "b", "per": 12, "pbr": 1.2}]
    assert P.peer_medians(rows, exclude="x")["pe"] is None           # chỉ 2 peer
    rows.append({"ticker": "c", "per": 14, "pbr": 1.4})
    pm = P.peer_medians(rows, exclude="x")
    assert pm["pe"] == 12 and pm["pb"] == 1.2


def test_end_to_end_report_renders():
    s = _series()
    r = _row(s)
    d = P.default_drivers(s, r, A, years=3)
    d[0].pop("_basis", None)
    fc = P.run_forecast(s, r, d)
    fg = P.forecast_gates(s, fc)
    coc = P.cost_of_capital(A, 1.0, r["market_cap"], {})
    rim = P.rim_explicit(r["bps"], fc, coc["ke"])
    rec = P.reconcile(r["price"], [{"name": "RIM", "value": rim["value"], "role": "primary"}], None, [None])
    md = P.report_markdown({"ticker": "000001", "name": "Test", "date": "2026-11-09", "price": r["price"],
                            "identify": P.identify(r), "dq": P.data_quality(r, s, [], 1.0), "forecast": fc,
                            "fgates": fg, "coc": coc, "conclusion": rec, "peers": {}, "anchor": None})
    assert "Báo cáo định giá" in md and "RIM" in md


def test_reported_quarters_relax_year1_gate_only_by_numbers():
    """Năm 1 đã có quý thực tăng mạnh: dải nới theo số công bố; vượt số công bố vẫn bị chặn."""
    s = _series()
    r = _row(s)
    ev = {"k": 2, "growth": 0.80, "margin": 0.35}
    y1 = [{"Năm": "2026", "Tăng trưởng DT %": 80, "Biên LN ròng %": 35, "Tỷ lệ cổ tức %": 0}]
    assert not P.forecast_gates(s, P.run_forecast(s, r, y1))["ok"]
    assert P.forecast_gates(s, P.run_forecast(s, r, y1), evidence=ev)["ok"]
    y1[0]["Tăng trưởng DT %"] = 120
    assert not P.forecast_gates(s, P.run_forecast(s, r, y1), evidence=ev)["ok"]


def test_high_needs_peer_anchor_agreement():
    c = [{"name": "a", "value": 15_000, "role": "primary"}, {"name": "b", "value": 15_500, "role": "primary"}]
    assert P.reconcile(10_000, c, anchor=14_000, caps=[])["confidence"] == "High"
    assert P.reconcile(10_000, c, anchor=8_000, caps=[])["confidence"] == "Medium"


# ---------------------------------------------------------------- giai đoạn 2
def _D(price_mult=1.2):
    s = _series()
    r = _row(s, price=s[-1]["bps"] * price_mult)
    r["beta"] = 1.0
    peers = [{"ticker": t, "per": pe, "pbr": pb} for t, pe, pb in
             (("a", 10, 1.0), ("b", 12, 1.2), ("c", 14, 1.4))]
    return {"row": r, "series": s, "quarters": [], "extras": {}, "peers": peers, "ticker": r["ticker"]}


def test_evaluate_runs_all_models():
    D = _D()
    drv = P.default_drivers(D["series"], D["row"], A, years=3)
    drv[0].pop("_basis", None)
    ev = P.evaluate(D, A, drv)
    names = [r["name"] for r in ev["rec"]["rows"]]
    assert any("RIM" in n for n in names) and ev["anchor"] is not None
    assert ev["rec"]["fair"] is not None


def test_shift_drivers_keeps_reported_year_and_handles_losses():
    d = [{"Năm": "2026", "Tăng trưởng DT %": 10, "Biên LN ròng %": 10},
         {"Năm": "2027", "Tăng trưởng DT %": 5, "Biên LN ròng %": -4}]
    out = P.shift_drivers(d, -3, 0.85, keep_year1=True)
    assert out[0] == d[0]
    assert out[1]["Tăng trưởng DT %"] == 2
    assert out[1]["Biên LN ròng %"] == pytest.approx(-4 * 1.15)    # bear: lỗ sâu hơn


def test_scenarios_ordered_and_expected_value():
    D = _D()
    drv = P.default_drivers(D["series"], D["row"], A, years=5)
    drv[0].pop("_basis", None)
    sc = P.scenarios(D, A, drv, P.identify(D["row"]), {"cap": None}, None)
    rim = {r["Kịch bản"]: r["RIM tường minh"] for r in sc["rows"]}
    assert rim["Bear"] < rim["Base"] < rim["Bull"]
    if all(r["Giá trị hợp lý"] for r in sc["rows"]):
        assert sc["expected"] == pytest.approx(sum(r["Xác suất"] * r["Giá trị hợp lý"] for r in sc["rows"]))


def test_scenarios_withhold_expected_when_one_blocked():
    D = _D()
    drv = P.default_drivers(D["series"], D["row"], A, years=3)
    drv[0].pop("_basis", None)
    spec = {"Bear": {"dg": -3, "mm": 0.85, "p": 0.25}, "Base": {"dg": 0, "mm": 1, "p": 0.5},
            "Bull": {"dg": 60, "mm": 5.0, "p": 0.25}}          # bull phi lý → cổng chặn RIM
    sc = P.scenarios(D, A, drv, P.identify(D["row"]), {"cap": None}, None, spec)
    assert sc["rows"][2]["Cổng dự phóng"].startswith("❌")


def test_sensitivity_higher_ke_lower_value():
    for mult in (0.6, 1.2):
        D = _D(mult)
        drv = P.default_drivers(D["series"], D["row"], A, years=3)
        drv[0].pop("_basis", None)
        se = P.sensitivity(D, A, drv, P.identify(D["row"]), {"cap": None}, None, ke_shifts=(-0.005, 0.005),
                           g_grid=(0.02,))
        lo_ke, hi_ke = se["grid"][0]["values"][0.02], se["grid"][1]["values"][0.02]
        if mult == 0.6:      # mọi mô hình trong dải → giảm đơn điệu
            assert lo_ke and hi_ke and lo_ke > hi_ke
        else:                # Ke cao đẩy lõi xuống dưới 0,4× giá → ô đó ⚪, không đổi bộ mô hình mà TĂNG
            assert lo_ke and hi_ke is None


def test_shifted_assumptions_only_moves_ke():
    a2 = P.ShiftedAssumptions(rf=0.03, erp=0.05, ke_shift=0.01)
    assert a2.ke_stock(1.0) == pytest.approx(A.ke_stock(1.0) + 0.01)
    assert a2.g_term_capped() == A.g_term_capped()


def test_football_field_only_passing_models():
    rec = {"fair": 100, "low": 90, "high": 110,
           "rows": [{"name": "ok", "value": 100, "pass": True}, {"name": "bad", "value": 999, "pass": False}]}
    ff = P.football_field({"low_52w": 80, "high_52w": 120}, rec, None, None)
    labels = [f["Dải"] for f in ff]
    assert "ok" in labels and "bad" not in labels and "Giá 52 tuần" in labels


def test_log_roundtrip_and_tracking(tmp_path):
    path = str(tmp_path / "log.csv")
    ctx = {"ticker": "000001", "name": "Test", "price": 100.0,
           "conclusion": {"fair": 150.0, "low": 120.0, "high": 170.0, "upside": 50.0, "confidence": "Medium",
                          "status": "✓"},
           "coc": {"ke": 0.08, "g_term": 0.02}}
    drv = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 30}]
    P.log_append(path, P.log_entry(ctx, drv, A, None))
    P.log_append(path, P.log_entry({**ctx, "ticker": "000002"}, drv, A, None))
    rows = P.log_read(path, "000001")
    assert len(rows) == 1 and rows[0]["Giá trị hợp lý"] == "150"
    t = P.log_track(rows, 125.0)[0]
    assert t["Giá đã đi %"] == pytest.approx(25.0)
    assert t["Đi được về giá trị %"] == pytest.approx(50.0)


def test_report_html_renders_tables_and_escapes():
    md = "# Tiêu đề <x>\n\n- **đậm** thường\n\n| A | B |\n|---|---:|\n| 1 | 2 |\n\n_ghi chú_"
    h = P.report_html(md, "T")
    assert "<h1>Tiêu đề &lt;x&gt;</h1>" in h and "<b>đậm</b>" in h
    assert "<td>1</td><td>2</td>" in h and "<i>ghi chú</i>" in h and "---" not in h


def test_fair_same_set_withholds_when_member_drops_out():
    rec = {"rows": [{"name": "a", "value": 10, "pass": True, "role": "primary"},
                    {"name": "b", "value": 30, "pass": False, "role": "primary"}]}
    assert P.fair_same_set(rec, ["a"]) == 10
    assert P.fair_same_set(rec, ["a", "b"]) is None


def test_scenarios_not_informative_when_forecast_blocked():
    D = _D()
    bad = [{"Năm": "2026", "Tăng trưởng DT %": 90, "Biên LN ròng %": 60, "Tỷ lệ cổ tức %": 0}]
    sc = P.scenarios(D, A, bad, P.identify(D["row"]), {"cap": None}, None)
    assert not sc["informative"] and sc["expected"] is None and sc["low"] is None
