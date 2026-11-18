"""Ô người dùng nhập ở trang 🧮 (rà soát 18/11/2026): ô trống / chữ / số phi lý phải bị chặn bằng số, không tự đổi
thành 0 hay năm nay. Dữ liệu giả, không mạng."""
import csv
import math

import pytest

import pipeline as P
import sector_models as S
from tests.test_pipeline import A, _D, _row, _series


# ---------------------------------------------------------------- I09 rNPV
def _asset(**kw):
    a = {"Tên": "X", "Pha": "Pha 3", "Doanh số đỉnh (억)": 70000, "Năm ra mắt": 2027, "PoS %": None,
         "Biên LN %": 30, "Chi phí PT còn lại (억)": 20000}
    a.update(kw)
    return a


def test_rnpv_valid_asset_passes():
    out = S.rnpv([_asset()], 0.10, 1e7, this_year=2026)
    assert out["ok"] and out["value"]


@pytest.mark.parametrize("kw", [
    {"Chi phí PT còn lại (억)": "20.000"},          # chữ — trước đây thành 20 (nhỏ 1000 lần)
    {"Chi phí PT còn lại (억)": "2.000"},
    {"Biên LN %": "35,5"},                          # chữ — trước đây thành 0
    {"Doanh số đỉnh (억)": "70.000"},               # trước đây thành 70
    {"Năm ra mắt": None},                           # trước đây thành năm nay
    {"Năm ra mắt": float("nan")},
    {"Năm ra mắt": "0"},
    {"Biên LN %": None},
    {"Doanh số đỉnh (억)": float("nan")},
    {"Chi phí PT còn lại (억)": None},
    {"Chi phí PT còn lại (억)": -20000},            # trước đây cộng vào giá trị
    {"Năm ra mắt": 12026},                          # trước đây OverflowError
    {"Năm ra mắt": 2026 + 31},
    {"PoS %": "50"},
])
def test_rnpv_bad_cells_fail_without_exception(kw):
    out = S.rnpv([_asset(**kw)], 0.10, 1e7, this_year=2026)
    assert not out["ok"] and out["value"] is None, out["checks"]
    assert any(c["Kết quả"] == S.FAIL for c in out["checks"])


def test_rnpv_blank_pos_still_uses_phase_default():
    out = S.rnpv([_asset(**{"PoS %": float("nan")})], 0.10, 1e7, this_year=2026)
    assert out["ok"] and out["rows"][0]["PoS dùng %"] == pytest.approx(57.8)


# ---------------------------------------------------------------- I46 cơ sở cổ phiếu
def test_implied_ev_per_gwh_uses_share_basis():
    # vốn hóa cp thường 9e11, cơ sở EPS 1,11× → vốn hóa cùng cơ sở 9,99e11
    old = S.implied_ev_per_gwh(9e11, 0, 100)
    new = S.implied_ev_per_gwh(9e11, 0, 100, shares=1.11e7, price=90_000)
    assert old == pytest.approx(90.0)
    assert new == pytest.approx(1.11e7 * 90_000 / 1e8 / 100)


# ---------------------------------------------------------------- I12 bảng dự phóng ④
def _drv(D, years=3):
    d = P.default_drivers(D["series"], D["row"], A, years=years)
    d[0].pop("_basis", None)
    return d


@pytest.mark.parametrize("col", ["Biên LN ròng %", "Tăng trưởng DT %", "Tỷ lệ cổ tức %"])
def test_blank_driver_cell_fails_gate_not_zero(col):
    D = _D()
    drv = _drv(D)
    drv[0][col] = float("nan")                          # data_editor trả NaN khi xóa ô
    ev = P.evaluate(D, A, drv)
    assert not ev["fg"]["ok"]
    assert any(col in c["Kiểm tra"] and c["Kết quả"] == P.FAIL for c in ev["fg"]["checks"])
    rows = {r["name"]: r for r in ev["rec"]["rows"]}
    rim = next(r for n, r in rows.items() if n.startswith("RIM"))
    assert not rim["pass"]                               # RIM/DDM bị chặn


def test_blank_margin_does_not_feed_forward_eps():
    D = _D()
    drv = _drv(D)
    ok = P.evaluate(D, A, drv)
    for d in drv:
        d["Biên LN ròng %"] = None
    bad = P.evaluate(D, A, drv)
    assert bad["base"].methods != {} and not bad["fg"]["ok"]
    assert ok["rec"]["fair"] != 0 and bad["rec"]["fair"] != 0


def test_eps_floor_gate_blocks_zero_margin_without_evidence():
    s = _series()
    r = _row(s)
    zero = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 0, "Tỷ lệ cổ tức %": 0}]
    g = P.forecast_gates(s, P.run_forecast(s, r, zero))
    assert not g["ok"] and any("EPS" in c["Kiểm tra"] for c in g["checks"])
    ev = {"k": 2, "growth": 0.05, "margin": 0.0}       # quý thực cho thấy hòa vốn → số thật, không chặn
    assert P.forecast_gates(s, P.run_forecast(s, r, zero), evidence=ev)["ok"]


def test_eps_floor_gate_skips_with_one_reported_loss_quarter():
    """Năm 1 mới có 1 quý thực (lỗ) → reported_evidence None nhưng biên ≤ 0 là số công bố: giả định MẶC ĐỊNH
    vẫn qua cổng (trước đây cổng sàn chặn RIM/DDM/FCFF của con số chuẩn)."""
    s = _series()
    r = _row(s)
    q = [{"period": "202512", "is_consensus": False, "revenue": 300, "op_profit": -5, "net_profit": -10,
          "ctrl_net": -10},
         {"period": "202603", "is_consensus": False, "revenue": 280, "op_profit": -8, "net_profit": -12,
          "ctrl_net": -12}]
    assert P.reported_evidence(s, q) is None
    d = P.default_drivers(s, r, A, years=3, quarters=q)
    d[0].pop("_basis", None)
    fc = P.run_forecast(s, r, d, q)
    assert fc[0]["EPS"] <= 0
    assert P.forecast_gates(s, fc, quarters=q)["ok"]
    D = {"row": dict(r, beta=1.0), "series": s, "quarters": q, "extras": {}, "peers": [], "ticker": "000001"}
    E = P.evaluate(D, A, d, P.identify(r), P.data_quality(r, s, q, 1.0), None)
    assert E["fg"]["ok"]
    assert not P.forecast_gates(s, fc)["ok"]           # không có quý → cổng sàn vẫn chặn


def test_missing_payout_key_still_means_zero():
    s = _series()
    r = _row(s)
    fc = P.run_forecast(s, r, [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10}])
    assert "_blank" not in fc[0] and fc[0]["DPS"] == 0


# ---------------------------------------------------------------- I26 bảng kịch bản ⑦
def test_shift_drivers_blank_and_clamp():
    d = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10},
         {"Năm": "2027", "Tăng trưởng DT %": 4, "Biên LN ròng %": -4}]
    assert P.shift_drivers(d, 0, 0)[0]["Biên LN ròng %"] == 0           # 0 là 0, không thành 1
    assert P.shift_drivers(d, 0, 2.5)[0]["Biên LN ròng %"] == pytest.approx(20)   # kẹp × 2
    assert P.shift_drivers(d, 0, 2.5)[1]["Biên LN ròng %"] == pytest.approx(0)    # biên âm không lật dương
    assert P.shift_drivers(d, 0, float("nan"))[0]["Biên LN ròng %"] is None
    assert P.shift_drivers(d, float("nan"), 1)[0]["Tăng trưởng DT %"] is None


def test_scenarios_blank_cell_blocks_that_scenario_and_expected():
    D = _D()
    drv = _drv(D, 5)
    spec = {k: dict(v) for k, v in P.SCENARIOS.items()}
    spec["Bear"]["mm"] = float("nan")
    sc = P.scenarios(D, A, drv, P.identify(D["row"]), {"cap": None}, None, spec)
    bear = sc["rows"][0]
    assert bear["Cổng dự phóng"].startswith("❌") and bear["Giá trị hợp lý"] is None
    assert sc["expected"] is None


@pytest.mark.parametrize("p_bear", [25, float("nan"), -0.25, 0.5])
def test_scenarios_probabilities_must_sum_to_one(p_bear):
    D = _D()
    drv = _drv(D, 5)
    spec = {k: dict(v) for k, v in P.SCENARIOS.items()}
    spec["Bear"]["p"] = p_bear
    sc = P.scenarios(D, A, drv, P.identify(D["row"]), {"cap": None}, None, spec)
    assert sc["expected"] is None and sc["p_note"]


def test_scenarios_default_expected_unchanged():
    D = _D()
    drv = _drv(D, 5)
    sc = P.scenarios(D, A, drv, P.identify(D["row"]), {"cap": None}, None)
    assert sc["p_note"] is None
    if all(r["Giá trị hợp lý"] for r in sc["rows"]):
        assert sc["expected"] == pytest.approx(sum(r["Xác suất"] * r["Giá trị hợp lý"] for r in sc["rows"]))


# ---------------------------------------------------------------- I65 / I68 nhật ký
def _ctx(**kw):
    c = {"ticker": "000001", "name": "Test", "price": 100.0,
         "conclusion": {"fair": 150.0, "low": 120.0, "high": 170.0, "upside": 50.0, "confidence": "Medium",
                        "status": "✓"},
         "coc": {"ke": 0.08, "g_term": 0.02}}
    c.update(kw)
    return c


DRV = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 30}]


def test_log_entry_marks_user_edits_and_scenario_spec(tmp_path):
    scen = {"rows": [{"Kịch bản": "Bear", "Δ tăng trưởng (điểm %)": -3, "× biên LN": 0.5, "Xác suất": 0.25}]}
    e = P.log_entry(_ctx(edited="2 ô dự phóng"), DRV, A, scen)
    assert e["Đã sửa tay"] == "2 ô dự phóng" and '"mm": 0.5' in e["Kịch bản"]
    assert P.log_entry(_ctx(), DRV, A, None)["Đã sửa tay"] == ""
    path = str(tmp_path / "log.csv")
    P.log_append(path, e)
    assert P.log_read(path)[0]["Đã sửa tay"] == "2 ô dự phóng"


def test_log_append_rotates_when_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LOG_MAX_ROWS", 3)
    path = str(tmp_path / "valuation_log.csv")
    for _ in range(4):
        P.log_append(path, P.log_entry(_ctx(), DRV, A, None))
    assert (tmp_path / "valuation_log.1.csv").exists()
    assert len(P.log_read(path)) == 1 and len(P.log_read(str(tmp_path / "valuation_log.1.csv"))) == 3


def test_log_append_migrates_old_header(tmp_path):
    path = str(tmp_path / "log.csv")
    old = P.LOG_FIELDS[:16]                                 # file trước 18/11/2026: chưa có cột sửa tay
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({k: P.log_entry(_ctx(), DRV, A, None)[k] for k in old})
    P.log_append(path, P.log_entry(_ctx(edited="1 giả định"), DRV, A, None))
    rows = P.log_read(path)
    assert len(rows) == 2 and rows[0]["Mã"] == "000001" and rows[1]["Đã sửa tay"] == "1 giả định"
    assert all(None not in r for r in rows)                 # không dòng nào lệch cột


def test_report_markdown_flags_hand_edits():
    s = _series()
    r = _row(s)
    rec = {"fair": 150.0, "low": 120.0, "high": 170.0, "upside": 5.0, "confidence": "Low", "status": "✓",
           "rows": []}
    ctx = {"ticker": "000001", "name": "Test", "date": "2026-11-18", "price": r["price"], "identify": P.identify(r),
           "dq": P.data_quality(r, s, [], 1.0), "forecast": [], "fgates": {"ok": False, "checks": [], "n_fail": 0},
           "coc": P.cost_of_capital(A, 1.0, r["market_cap"], {}), "conclusion": rec, "peers": {}, "anchor": None}
    assert "Đã sửa tay" not in P.report_markdown(ctx)
    md = P.report_markdown({**ctx, "edited": "1 ô dự phóng", "default_fair": 140.0})
    assert "✏️ **Đã sửa tay:** 1 ô dự phóng" in md and "140" in md
    assert not math.isnan(rec["fair"])


def test_rnpv_cost_hint_only_for_cost_cell():
    def note(o):
        return " ".join(c["Ghi chú"] for c in o["checks"] if c["Kết quả"] == "❌")
    yr = S.rnpv([_asset(**{"Năm ra mắt": None})], 0.10, 1e7, this_year=2026)
    cost = S.rnpv([_asset(**{"Chi phí PT còn lại (억)": None})], 0.10, 1e7, this_year=2026)
    assert not yr["ok"] and "chi phí PT" not in note(yr)
    assert not cost["ok"] and "chi phí PT = 0" in note(cost)
