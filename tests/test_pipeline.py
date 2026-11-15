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
    # kịch bản dựng với tăng trưởng 8% cố định: lõi DCF earnings rơi khỏi cổng khi Ke tăng (kiểm fair_same_set)
    a_fix = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02, g_high_mode="fixed")
    for mult in (0.6, 1.2):
        D = _D(mult)
        drv = P.default_drivers(D["series"], D["row"], a_fix, years=3)
        drv[0].pop("_basis", None)
        se = P.sensitivity(D, a_fix, drv, P.identify(D["row"]), {"cap": None}, None, ke_shifts=(-0.005, 0.005),
                           g_grid=(0.02,))
        lo_ke, hi_ke = se["grid"][0]["values"][0.02], se["grid"][1]["values"][0.02]
        if mult == 0.6:      # mọi mô hình trong dải → giảm đơn điệu
            assert lo_ke and hi_ke and lo_ke > hi_ke
        else:                # Ke cao: hoặc lõi rơi khỏi cổng → ô ⚪ (không đổi bộ mô hình mà TĂNG), hoặc vẫn giảm
            # (DCF earnings sửa 10/11/2026 trừ tái đầu tư → lõi thấp hơn, kịch bản cũ không còn rơi khỏi cổng)
            assert lo_ke and (hi_ke is None or lo_ke > hi_ke)


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


def test_one_off_year1_kept_but_not_carried_to_later_years():
    """Năm 1 giữ số công bố (có lãi một lần) và qua cổng; năm 2+ xuất phát từ biên lõi, ≤ trần biên."""
    s = _series(rev0=1000, g=0.0, margin=0.10)
    r = _row(s)
    qs = [{"period": "202603", "is_consensus": False, "revenue": 250, "op_profit": 32, "net_profit": 25,
           "ctrl_net": 25},
          {"period": "202606", "is_consensus": False, "revenue": 250, "op_profit": 32, "net_profit": 300,
           "ctrl_net": 300}]                                              # LN > DT: lãi một lần
    ev = P.reported_evidence(s, qs)
    assert ev["margin"] < 0.15 < ev["margin_reported"]
    d = P.default_drivers(s, r, A, years=4, quarters=qs)
    d[0].pop("_basis", None)
    assert d[0]["Biên LN ròng %"] == pytest.approx(ev["margin_reported"] * 100, abs=0.3)   # năm 1 = số công bố
    assert all(x["Biên LN ròng %"] <= P.FC_MARGIN_MAX * 100 for x in d[1:])
    assert d[1]["Biên LN ròng %"] == pytest.approx(ev["margin"] * 100, abs=0.01)
    assert P.forecast_gates(s, P.run_forecast(s, r, d), evidence=ev)["ok"]
    dq = P.data_quality(r, s, qs, 1.0)
    assert any(c["Kiểm tra"].startswith("Lãi/lỗ bất thường") and c["Kết quả"] == "⚠️" for c in dq["checks"])


def test_overall_assessment_rules():
    rec = {"fair": 130, "upside": 30.0}
    q = {"score": 85, "grade": "A", "n_fail": 0}
    fh = {"score": 5, "max": 6}
    ps = {"position": [{"Chỉ số": "ROE %", "Hạng trong nước (%)": 70},
                       {"Chỉ số": "Biên LN HĐ %", "Hạng trong nước (%)": 60}]}
    o = P.overall_assessment(rec, fh, q, ps)
    assert o["pillars"]["Định giá"] == 80 and o["verdict"].startswith("🟢") and o["n_pillars"] == 4
    assert P.overall_assessment(rec, fh, {"score": 40, "grade": "D", "n_fail": 3}, ps)["verdict"].startswith("🔴 Rủi ro")
    assert P.overall_assessment({"fair": None, "upside": None}, fh, q, ps)["verdict"].startswith("⚪ Chưa đủ")
    assert P.overall_assessment(rec, fh, {"score": 55, "grade": "C", "n_fail": 1}, ps)["verdict"].startswith("🟡")
    bank_q = {"score": 83, "grade": "B", "n_fail": 0, "fin_limited": True}
    assert P.overall_assessment(rec, fh, bank_q, ps)["verdict"].startswith("🟡")    # không 🟢 khi chưa chấm đủ


def test_default_drivers_mid_cycle_margin_never_below_breakeven():
    """Lịch sử lỗ nhiều năm (trung vị biên âm) → biên dài hạn mặc định chặn ở 0%, không giả định lỗ vĩnh viễn
    (005950 이수화학: trung vị −1,7% → RIM ≤ 0 → không có mô hình chính, 10/11/2026)."""
    s = _series(n=6, margin=-0.02)
    s[1]["net_profit"] = s[1]["revenue"] * 0.05
    d = P.default_drivers(s, _row(s), A, years=5, cyclical=True)
    ms = [x["Biên LN ròng %"] for x in d[1:]]
    assert ms == sorted(ms)                                  # trượt từ lỗ hiện tại LÊN hòa vốn, không xuống −x%
    assert d[-1]["Biên LN ròng %"] == pytest.approx(0.0)
    assert "hòa vốn" in d[0]["_basis"]


def test_default_drivers_positive_mid_cycle_unchanged():
    s = _series(n=6, margin=0.08)
    d = P.default_drivers(s, _row(s), A, years=5, cyclical=True)
    assert d[-1]["Biên LN ròng %"] == pytest.approx(8.0, abs=0.01)
    assert "hòa vốn" not in d[0]["_basis"]


def test_unpublished_reason_states_direction():
    rows = [{"name": "A", "role": "primary", "value": 10.0}, {"name": "B", "role": "primary", "value": 20.0}]
    assert "< 0.4× giá" in P.unpublished_reason(rows, 100.0)
    rows_hi = [{"name": "A", "role": "primary", "value": 300.0}, {"name": "B", "role": "primary", "value": 400.0}]
    assert "> 2.5× giá" in P.unpublished_reason(rows_hi, 100.0)
    rows_none = [{"name": "A", "role": "primary", "value": None}, {"name": "B", "role": "primary", "value": None}]
    assert "tính được" in P.unpublished_reason(rows_none, 100.0)
    rec = P.reconcile(100.0, [dict(r, blocked=None) for r in rows], None, [])
    assert rec["fair"] is None and rec["status"].startswith("⚪ không công bố — mọi mô hình chính < 0.4× giá")


def test_unpublished_reason_uses_core_value_even_if_self_flagged():
    """373220: lõi KCI tự gắn '⚪ ngoài vùng tin cậy' (0,31×) + RIM 0,10× → vẫn phải nói 'thị giá vượt xa'."""
    rows = [{"name": "Lõi", "role": "primary", "value": 31.0, "blocked": "⚪ ngoài vùng tin cậy"},
            {"name": "RIM", "role": "primary", "value": 10.0, "blocked": None},
            {"name": "P/E", "role": "cross", "value": 500.0}]
    assert "< 0.4× giá" in P.unpublished_reason(rows, 100.0)
    rows_fc = [{"name": "Lõi", "role": "primary", "value": None, "blocked": None},
               {"name": "RIM", "role": "primary", "value": 10.0, "blocked": "cổng dự phóng: 2 vi phạm"}]
    assert "dự phóng bị cổng" in P.unpublished_reason(rows_fc, 100.0)


def test_share_count_break_blocks_forecast_models():
    """Số cp hiện tại gấp ~3 số cp của BCTC năm (sáp nhập) → ❌ dữ liệu, dự phóng bị chặn, không nuôi P/E forward."""
    s = _series()
    r = _row(s)
    r["market_cap"] *= 3                                  # vốn hóa ÷ giá = 3× số cp cũ
    dq = P.data_quality(r, s, [], beta=1.0, today=dt.date(2026, 11, 9))
    assert dq["share_break"]
    drv = P.default_drivers(s, r, A, years=3)
    drv[0].pop("_basis", None)
    D = {"row": dict(r, beta=1.0), "series": s, "quarters": [], "extras": {}, "peers": [], "ticker": "000001"}
    E = P.evaluate(D, A, drv, P.identify(r), dq, None)
    assert not E["fg"]["ok"]
    rim = next(x for x in E["rec"]["rows"] if x["name"].startswith("RIM tường minh"))
    assert not rim["pass"] and "cổng dự phóng" in rim["why"]
    ok = P.data_quality(_row(s), s, [], beta=1.0, today=dt.date(2026, 11, 9))
    assert not ok["share_break"]


def test_run_forecast_starts_from_fiscal_year_end_book():
    """BPS ở row là cuối QUÝ gần nhất (đã gồm LN năm 1) → dự phóng phải bắt đầu từ BPS cuối năm tài chính."""
    s = _series()
    r = _row(s)
    r["bps"] = s[-1]["bps"] * 1.3                          # BPS Naver giữa năm, đã cộng LN nửa năm
    fc = P.run_forecast(s, r, [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 0}])
    assert fc[0]["BPS đầu kỳ"] == pytest.approx(s[-1]["bps"])


# ================================================================ rà soát 15/11/2026 — đợt 1
def _series_ctrl(parent=1.0, shares=1e7, **kw):
    """Chuỗi có tách LN cổ đông mẹ (như dữ liệu Naver thật): ctrl_net = LN × parent, EPS theo ctrl_net."""
    s = _series(shares=shares, **kw)
    for c in s:
        c["ctrl_net"] = c["net_profit"] * parent
        c["eps"] = c["ctrl_net"] * 1e8 / shares
    return s


def _quarters(year, n, np_q, ctrl_q, shares, rev_q=300.0):
    return [{"period": f"{year}{m:02d}", "is_consensus": False, "revenue": rev_q, "op_profit": np_q * 1.2,
             "net_profit": np_q, "ctrl_net": ctrl_q, "eps": ctrl_q * 1e8 / shares} for m in (3, 6, 9, 12)[:n]]


def test_run_forecast_uses_parent_profit_and_current_shares():
    """Năm trước cổ đông mẹ hưởng 45% LN trên 10 triệu cp; năm nay 32% trên 8 triệu cp (đã hủy cổ phiếu quỹ)."""
    s = _series_ctrl(parent=0.45)
    # LN quý đủ lớn để sai số làm tròn (1억, 1 KRW) của số cp suy ra < 2% — quý quá nhỏ bị bỏ, xem share_basis
    q = _quarters(2026, 2, np_q=350.0, ctrl_q=350.0 * 0.32, shares=8e6, rev_q=3000.0)
    r = _row(s)
    fc = P.run_forecast(s, r, [{"Năm": "2026", "Tăng trưởng DT %": 0, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 0},
                               {"Năm": "2027", "Tăng trưởng DT %": 0, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 0}], q)
    np1 = s[-1]["revenue"] * 0.10
    assert fc[0]["LN ròng (억)"] == pytest.approx(np1)
    assert fc[0]["LN cổ đông mẹ (억)"] == pytest.approx(np1 * 0.32)
    assert fc[0]["EPS"] == pytest.approx(np1 * 0.32 * 1e8 / 8e6)
    assert fc[1]["EPS"] == pytest.approx(fc[0]["EPS"])
    # không truyền quý (backtest / dữ liệu cũ) → tỷ lệ và số cp của năm thực gần nhất
    old = P.run_forecast(s, r, [{"Năm": "2026", "Tăng trưởng DT %": 0, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 0}])
    assert old[0]["EPS"] == pytest.approx(np1 * 0.45 * 1e8 / 1e7)


def test_data_quality_share_count_moved_warns_break_fails():
    s = _series_ctrl()
    today = dt.date(2026, 11, 9)
    moved = _quarters(2026, 2, 30.0, 30.0, shares=0.8e7)             # −20%: hủy cổ phiếu quỹ → ⚠️, không chặn
    r = _row(s, shares=0.8e7)
    dq = P.data_quality(r, s, moved, beta=1.0, today=today)
    row = next(c for c in dq["checks"] if c["Kiểm tra"] == "Số cổ phiếu (cơ sở EPS)")
    assert row["Kết quả"] == P.WARN and not dq["share_break"]
    merged = _quarters(2026, 2, 30.0, 30.0, shares=2.7e7)            # 2,7×: sáp nhập → ❌, chặn dự phóng mỗi cổ phiếu
    dq = P.data_quality(_row(s, shares=2.7e7), s, merged, beta=1.0, today=today)
    assert dq["share_break"] and dq["cap"] == "Low"


def test_data_quality_parent_ratio_gate():
    s = _series_ctrl(parent=0.9)
    q = _quarters(2026, 2, np_q=30.0, ctrl_q=-10.0, shares=1e7)      # hợp nhất lãi, công ty mẹ lỗ
    dq = P.data_quality(_row(s), s, q, beta=1.0, today=dt.date(2026, 11, 9))
    assert dq["parent_break"]
    drv = P.default_drivers(s, _row(s), A, years=3, quarters=q)
    drv[0].pop("_basis", None)
    D = {"row": dict(_row(s), beta=1.0), "series": s, "quarters": q, "extras": {}, "peers": [], "ticker": "000001"}
    E = P.evaluate(D, A, drv, P.identify(_row(s)), dq, P.reported_evidence(s, q))
    assert not E["fg"]["ok"] and any("LN cổ đông mẹ" in c["Kiểm tra"] for c in E["fg"]["checks"])


def test_data_quality_reads_source_status():
    today = dt.date(2026, 11, 9)

    def dq_with(src):
        s = _series()
        for c in s:
            c["_src"] = src
        return P.data_quality(_row(s), s, [{"period": "202609", "is_consensus": False}], beta=1.0, today=today)
    ok = dq_with({"dart": "ok", "note": "", "gaps": []})
    assert ok["n_fail"] == 0 and not ok["source_break"]
    err = dq_with({"dart": "error", "note": "lỗi kết nối DART (ConnectionError)", "gaps": []})
    assert err["cap"] == "Low" and not err["source_break"]           # lỗi mạng: hạ Low, không chặn mô hình
    mm = dq_with({"dart": "mismatch", "note": "năm 2025: doanh thu DART 162 vs Naver 1.200억", "gaps": []})
    assert mm["source_break"] and mm["cap"] == "Low"
    cur = dq_with({"dart": "currency", "note": "BCTC DART lập bằng USD", "gaps": []})
    assert not cur["source_break"] and cur["n_fail"] == 0            # nguyên nhân đã rõ, số Naver dùng được → ⚠️
    rest = dq_with({"dart": "ok", "note": "", "gaps": [{"year": "2024", "field": "revenue", "dart": 1430.0,
                                                        "naver": 1100.0, "ratio": 1.3}]})
    assert any(c["Kiểm tra"].startswith("Điều chỉnh hồi tố") and c["Kết quả"] == P.WARN for c in rest["checks"])


def test_source_break_blocks_forecast_models():
    s = _series()
    for c in s:
        c["_src"] = {"dart": "mismatch", "note": "x", "gaps": []}
    r = _row(s)
    dq = P.data_quality(r, s, [], beta=1.0, today=dt.date(2026, 11, 9))
    drv = P.default_drivers(s, r, A, years=3)
    drv[0].pop("_basis", None)
    D = {"row": dict(r, beta=1.0), "series": s, "quarters": [], "extras": {}, "peers": [], "ticker": "000001"}
    E = P.evaluate(D, A, drv, P.identify(r), dq, None)
    rim = next(x for x in E["rec"]["rows"] if x["name"].startswith("RIM tường minh"))
    assert not rim["pass"] and "cổng dự phóng" in rim["why"]


def test_missing_beta_or_rf_caps_confidence_low():
    s = _series()
    q = [{"period": "202609", "is_consensus": False}]
    today = dt.date(2026, 11, 9)
    assert P.data_quality(_row(s), s, q, beta=None, today=today)["cap"] == "Low"
    dq = P.data_quality(_row(s), s, q, beta=1.0, today=today,
                        feeds={"rf": {"ok": False, "note": "Không lấy được Rf — dùng mặc định 3,2%"}})
    assert dq["cap"] == "Low" and any("Rf" in c["Kiểm tra"] for c in dq["checks"])
    assert P.data_quality(_row(s), s, q, beta=1.0, today=today, feeds={"rf": {"ok": True}})["cap"] is None


def test_confidence_needs_independent_confirmation():
    two = [{"name": "a", "value": 10_000, "role": "primary"}, {"name": "b", "value": 10_500, "role": "primary"}]
    one = [{"name": "a", "value": 10_000, "role": "primary"},
           {"name": "b", "value": 3_000, "role": "primary"},          # 0,3× giá → bị cổng chặn
           {"name": "x", "value": 9_500, "role": "cross"}]
    # hai mô hình chính đồng ý nhưng KHÔNG có phép neo peer → không được High
    assert P.reconcile(10_000, two, anchor=None, caps=[])["confidence"] == "Medium"
    assert P.reconcile(10_000, two, anchor=10_200, caps=[])["confidence"] == "High"
    # chỉ một mô hình chính qua cổng: mô hình đối chiếu không cứu được; phép neo peer xác nhận thì Medium
    assert P.reconcile(10_000, one, anchor=None, caps=[])["confidence"] == "Low"
    assert P.reconcile(10_000, one, anchor=30_000, caps=[])["confidence"] == "Low"
    assert P.reconcile(10_000, one, anchor=11_000, caps=[])["confidence"] == "Medium"


def test_fs_mismatch_gates_full_statements():
    s = _series()
    fs = {"year": 2025, "currency": "KRW", "cur": {"revenue": s[-1]["revenue"]}}
    assert P.fs_mismatch(fs, s) is None
    assert "USD" in P.fs_mismatch({**fs, "currency": "USD"}, s)
    assert "doanh thu" in P.fs_mismatch({**fs, "cur": {"revenue": s[-1]["revenue"] * 0.135}}, s)
    assert P.fs_mismatch(None, s) is None


def test_prepare_assess_is_the_single_valuation_path(monkeypatch):
    """prepare() dựng đúng những gì evaluate() cần; assess() = prepare + evaluate — trang nào gọi cũng ra một số."""
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    peers = [{"ticker": t, "per": pe, "pbr": pb} for t, pe, pb in (("a", 10, 1.0), ("b", 12, 1.2), ("c", 14, 1.4))]
    ctx = P.assess("000001", r, {"annual": [], "quarter": []}, A, peers, 1.0, series=s, today=dt.date(2026, 11, 9))
    # cùng kết quả với cách ghép tay từng bước
    row = dict(r, beta=1.0)
    idn = P.identify(row)
    dq = P.data_quality(row, s, [], 1.0, today=dt.date(2026, 11, 9))
    drv = P.default_drivers(s, row, A, years=5)
    drv[0].pop("_basis", None)
    E = P.evaluate({"row": row, "series": s, "quarters": [], "extras": {}, "peers": peers, "ticker": "000001"},
                   A, drv, idn, dq, None)
    assert ctx["rec"]["fair"] == pytest.approx(E["rec"]["fair"]) and ctx["rec"]["confidence"] == E["rec"]["confidence"]
    assert ctx["drivers"] == drv and ctx["D"]["peers"] is peers
    # BCTC đầy đủ không cùng đơn vị → không vào FCFF, có ⚠️ trong bảng kiểm tra; ngành tài chính thì không xét
    fs_usd = {"year": 2025, "currency": "USD", "fs_div": "CFS", "cur": {"revenue": 1.0}, "prev": {}}
    c2 = P.prepare("000001", r, {}, A, peers, 1.0, series=s, fs_full=fs_usd, today=dt.date(2026, 11, 9))
    assert c2["D"]["fs_full"] is None and c2["fs_note"]
    assert any("FCFF" in c["Kiểm tra"] and c["Kết quả"] == P.WARN for c in c2["dq"]["checks"])
    bank = dict(r, industry_kr="은행")
    c3 = P.prepare("000001", bank, {}, A, peers, 1.0, series=s, fs_full=fs_usd, today=dt.date(2026, 11, 9))
    assert c3["fs_note"] is None


def test_industry_peers_cache_is_per_run_and_skips_failures(monkeypatch):
    calls = []

    def ranked(ik):
        calls.append(ik)
        return None if ik == "lỗi" else ["000001", "000002", "000003"]
    monkeypatch.setattr(P, "_industry_ranked", ranked)
    cache = {}
    assert P.industry_peers("식품", "000002", 2, cache=cache) == ["000001", "000003"]
    assert P.industry_peers("식품", "000001", 2, cache=cache) == ["000002", "000003"]
    assert calls == ["식품"]                                           # ngành chỉ gọi một lần trong một lần chạy
    assert P.industry_peers("lỗi", None, 2, cache=cache) == [] and "lỗi" not in cache


def test_share_basis_ignores_periods_with_large_rounding_error_and_never_double_counts_minorities():
    import financials as F
    # quý LN cổ đông mẹ 3억 (làm tròn từ 3,4억), EPS 113 → số cp suy ra lệch 12% → bỏ, dùng năm
    reals = [{"period": "202512", "is_consensus": False, "net_profit": 100.0, "ctrl_net": 60.0, "eps": 2000.0}]
    q = [{"period": "202606", "is_consensus": False, "net_profit": 5.0, "ctrl_net": 3.0, "eps": 113.0}]
    assert F.share_basis(reals, q, None, None)["source"] == "năm 2025"
    assert F.share_basis(reals, q, None, None)["shares"] == pytest.approx(60e8 / 2000)      # theo LN cổ đông MẸ
    # không kỳ nào đủ chính xác → số cp niêm yết; KHÔNG lùi về LN hợp nhất ÷ EPS (phồng theo cổ đông thiểu số)
    tiny = [{"period": "202512", "is_consensus": False, "net_profit": 10.0, "ctrl_net": 6.0, "eps": 60.0}]
    sb = F.share_basis(tiny, [], market_cap=1e7 * 5_000, price=5_000)
    assert sb["source"] == "vốn hóa ÷ giá" and sb["shares"] == pytest.approx(1e7)
    # năm mới nhất chỉ THIẾU số cổ đông mẹ (không phải trái dấu) → dùng tỷ lệ của năm gần nhất có số, vẫn tin được
    ys = [{"period": "202412", "is_consensus": False, "net_profit": 100.0, "ctrl_net": 95.0, "eps": 950.0},
          {"period": "202512", "is_consensus": False, "net_profit": 120.0, "ctrl_net": None, "eps": None}]
    pr = F.parent_ratio(ys)
    assert pr["reliable"] and pr["rest"] == pytest.approx(0.95)

