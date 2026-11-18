"""Rà soát 18/11/2026 — nhóm G10: REIT kỳ kế toán 6/3 tháng (I08) và ô 'Nguồn và độ mới dữ liệu' (C8).
Chạy offline: DART / Naver giả lập; số liệu 롯데리츠 (330590) và SK리츠 (395400) chép từ Naver 17–18/11/2026."""
import ast
import datetime as dt
import os
import threading

import pytest

import dart_api as DA
import financials as F
import pipeline as P
import valuation as V

A = V.Assumptions(rf=0.0398, erp=0.05, g_terminal=0.02)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = dt.date(2026, 11, 18)


def _c(p, rev, op, np_, ctrl, eps, bps, roe, dps, per, cons=False):
    return {"period": p, "label": f"{p[:4]}.{p[4:6]}.", "is_consensus": cons, "revenue": rev, "op_profit": op,
            "net_profit": np_, "ctrl_net": ctrl, "eps": eps, "bps": bps, "roe": roe, "dps": dps, "per": per}


_NONE = dict(rev=None, op=None, np_=None, ctrl=None, eps=None, bps=None, roe=None, dps=None, per=None)
# 롯데리츠: kỳ 6 tháng (cột 'annual' 202506 = H1, 202512 = H2, 202606 = H1)
LOTTE_A = [_c("202506", 700, 452, 165, 165, 57, 3992, 1.51, 117, 65.49),
           _c("202512", 709, 463, 196, 196, 68, 3943, 1.70, 123, 58.30),
           _c("202606", 821, 557, 238, 238, 82, 3903, 2.08, 139, 41.94),
           _c("202706", cons=True, **_NONE)]
LOTTE_Q = [_c("202506", 348, 226, 81, 81, 28, 3992, 2.48, 117, 37.73),
           _c("202509", 353, 230, 97, 97, 34, 3909, 3.01, None, 34.15),
           _c("202512", 356, 232, 99, 99, 34, 3943, 3.13, 123, 31.63),
           _c("202603", 389, 261, 106, 106, 37, 3857, 3.39, None, 34.92),
           _c("202606", 432, 296, 132, 132, 46, 3903, 3.80, 139, 22.97),
           _c("202609", cons=True, **_NONE)]
LOTTE_ROW = {"ticker": "330590", "name": "롯데리츠", "price": 3600.0, "market_cap": 1.0403e12, "eps": 82.0,
             "per": 43.9, "bps": 3903.0, "dps": 262.0, "industry_kr": "부동산", "sector": None}
# SK리츠: kỳ 3 tháng (cột 'annual' trùng cột quý)
SK_A = [_c("202512", 704, 645, 336, 324, 109, 6466, 2.07, 66, 53.08),
        _c("202603", 634, 539, 205, 194, 64, 6460, 1.03, 68, 96.58),
        _c("202606", 802, 717, 384, 372, 124, 6516, 1.94, 68, 43.74),
        _c("202706", cons=True, **_NONE)]
SK_Q = [_c("202506", 710, 632, 313, 301, 109, 6467, 4.05, 66, 20.23),
        _c("202509", 567, 503, 203, 191, 66, 6445, 5.35, 66, 16.83),
        _c("202512", 704, 645, 336, 324, 109, 6466, 7.31, 66, 14.44),
        _c("202603", 634, 539, 205, 194, 64, 6460, 5.39, 68, 17.96),
        _c("202606", 802, 717, 384, 372, 124, 6516, 5.63, 68, 14.89),
        _c("202609", cons=True, **_NONE)]
SK_ROW = {"ticker": "395400", "name": "SK리츠", "price": 4840.0, "market_cap": 1.18e12, "eps": 124.0, "per": 39.03,
          "bps": 6516.0, "dps": 268.0, "industry_kr": "부동산", "sector": None}


def _chk(dq, name):
    return next((c for c in dq["checks"] if c["Kiểm tra"].startswith(name)), None)


def _no_dart(monkeypatch):
    monkeypatch.setattr(DA, "available", lambda: False)


# ============================================================================ I08 — phát hiện kỳ ngắn
def test_short_period_detection():
    assert F.short_period_months(LOTTE_A) == 6
    assert F.short_period_months(SK_A) == 3
    yearly = [_c(f"{y}12", 100, 10, 8, 8, 10, 100, 8, 1, 10) for y in range(2021, 2026)]
    assert F.short_period_months(yearly) is None
    # một kỳ lẻ khi đổi năm tài chính không biến cả chuỗi năm thành kỳ ngắn
    stub = yearly[-3:] + [_c("202603", 25, 2, 2, 2, 2, 100, 2, 0, 10)]
    assert F.short_period_months(stub) is None


def test_synthetic_half_year_chain_is_not_collapsed_by_year(monkeypatch):
    _no_dart(monkeypatch)
    syn = [{"period": p, "label": p, "is_consensus": False, "revenue": rev, "net_profit": np_, "eps": np_, "roe": 1.0}
           for p, rev, np_ in [("202406", 100, 10), ("202412", 110, 12), ("202506", 120, 14), ("202512", 130, 16),
                               ("202606", 140, 18)]]
    out = F._merge_dart(syn, "000000")
    # trước 18/11/2026: gom theo năm → 202412/202512/202606, mỗi điểm là một kỳ 6 tháng
    last = out[-1]
    assert last["period"] == "202606" and last["_ttm"] and last["_period_months"] == 6
    assert last["revenue"] == 130 + 140 and last["eps"] == 16 + 18
    assert out[-2]["period"] == "202506" and out[-2]["revenue"] == 110 + 120 and out[-2]["_ttm"]
    assert sum(c["revenue"] for c in out if c["_ttm"]) == 270 + 230


def test_lotte_reit_ttm_from_two_halves(monkeypatch):
    _no_dart(monkeypatch)
    out = F._merge_dart(LOTTE_A, "330590", quarters=LOTTE_Q)
    last = out[-1]
    assert last["period"] == "202606" and last["_ttm"] and last["_ttm_eps"]
    assert (last["revenue"], last["net_profit"], last["ctrl_net"], last["eps"], last["dps"]) == (1530, 434, 434, 150,
                                                                                                 262)
    assert last["net_margin"] == round(434 / 1530 * 100, 2)
    assert 3.5 < last["roe"] < 4.0                    # ROE 12 tháng, không phải 2,08% của một kỳ 6 tháng
    assert last["bps"] == 3903                       # số dư: kỳ gần nhất
    assert out[0]["_ttm"] is False and out[0]["_scale"] == 2.0     # năm cũ chỉ có một kỳ → ước ×2, gắn cờ


def test_sk_reit_ttm_from_four_quarters(monkeypatch):
    _no_dart(monkeypatch)
    last = F._merge_dart(SK_A, "395400", quarters=SK_Q)[-1]
    assert last["_ttm"] and last["_period_months"] == 3
    assert last["revenue"] == 567 + 704 + 634 + 802 and last["eps"] == 66 + 109 + 64 + 124
    assert [p for p, _ in last["_ttm_parts"]] == ["202606", "202603", "202512", "202509"]


def test_prepare_replaces_short_eps_per_and_warns(monkeypatch):
    _no_dart(monkeypatch)
    for row, a, q, eps in ((LOTTE_ROW, LOTTE_A, LOTTE_Q, 150), (SK_ROW, SK_A, SK_Q, 363)):
        ctx = P.prepare(row["ticker"], dict(row), {"annual": a, "quarter": q}, A, [], 1.0, today=TODAY)
        assert ctx["row"]["eps"] == eps and ctx["row"]["_eps_period"] == row["eps"]
        assert ctx["row"]["per"] == round(row["price"] / eps, 2)           # 24,0 / 13,33 thay vì 43,9 / 39,0
        c = _chk(ctx["dq"], "Độ dài kỳ kế toán")
        assert c and c["Kết quả"] == P.WARN and not ctx["dq"]["period_break"]
    assert round(LOTTE_ROW["price"] / 150, 1) == 24.0


def test_unannualized_short_series_fails_and_blocks_forecast_models():
    # chuỗi kỳ ngắn đi thẳng vào data_quality (không qua năm hóa) → ❌, trần Low, chặn mô hình dựa trên dự phóng
    syn = [{"period": p, "is_consensus": False, "revenue": rev, "op_profit": rev * 0.6, "net_profit": np_,
            "eps": np_, "bps": 1000.0, "roe": 1.0, "dps": 5.0}
           for p, rev, np_ in [("202406", 100, 10), ("202412", 110, 12), ("202506", 120, 14), ("202512", 130, 16),
                               ("202606", 140, 18)]]
    row = {"ticker": "000009", "price": 1000.0, "market_cap": 1e11, "eps": 18.0, "bps": 1000.0, "dps": 10.0,
           "industry_kr": "부동산", "sector": None}
    dq = P.data_quality(row, syn, [], 0.8, today=TODAY)
    c = _chk(dq, "Độ dài kỳ kế toán")
    assert c and c["Kết quả"] == P.FAIL and dq["period_break"] and dq["cap"] == "Low"
    ctx = P.assess("000009", row, {}, A, [], 0.8, series=syn, today=TODAY)
    rim = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("RIM"))
    assert not rim["pass"]


def test_last_period_without_twelve_months_fails(monkeypatch):
    _no_dart(monkeypatch)
    out = F._merge_dart(SK_A, "395400", quarters=[])         # 3 kỳ 3 tháng, không có quý → chỉ 9 tháng
    assert out[-1]["_ttm"] is False and out[-1]["_scale"] == round(12 / 9, 3)
    ctx = P.prepare("395400", dict(SK_ROW), {"annual": SK_A, "quarter": []}, A, [], 1.0, today=TODAY)
    assert _chk(ctx["dq"], "Độ dài kỳ kế toán")["Kết quả"] == P.FAIL and ctx["dq"]["period_break"]
    assert ctx["dq"]["cap"] == "Low"


class _FakeDart:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, ticker, years=6, errors=None, err_years=None):
        return [dict(r) for r in self.rows]


def _dart_h2(scale=1.0):
    revs = {2021: 562, 2022: 579, 2023: 594, 2024: 656, 2025: 709}
    return [{"period": f"{y}12", "revenue": r * (scale if y == 2025 else 1.0), "op_profit": r * 0.65,
             "net_profit": r * 0.27 * (scale if y == 2025 else 1.0), "assets": 30000.0, "liabilities": 18000.0,
             "equity": 12000.0, "debt_ratio": 150.0, "currency": "KRW"} for y, r in revs.items()]


def test_dart_short_periods_merge_by_full_period(monkeypatch):
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", _FakeDart(_dart_h2()))
    out = F._merge_dart(LOTTE_A, "330590", quarters=LOTTE_Q)
    assert out[-1]["_src"]["dart"] == "ok"
    assert [c["period"] for c in out] == ["202206", "202306", "202406", "202506", "202606"]   # 5 kỳ DART H2 2021–25
    p25 = out[-2]                                    # H2/2024 (DART) + H1/2025 (Naver) = đủ 12 tháng
    assert p25["_ttm"] and p25["revenue"] == 656 + 700 and p25["_dart_periods"] == ["202412"]
    assert out[-1]["revenue"] == 709 + 821 and out[-1]["equity"] == 12000.0
    assert out[0]["_scale"] == 2.0 and out[0]["revenue"] == 562 * 2


def test_dart_twelve_month_report_against_half_year_is_mismatch(monkeypatch):
    # DART báo 12 tháng cho kỳ Naver 6 tháng → hai nguồn không cùng một số: bỏ DART, ❌ đối chiếu
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", _FakeDart(_dart_h2(scale=2.0)))
    out = F._merge_dart(LOTTE_A, "330590", quarters=LOTTE_Q)
    assert out[-1]["_src"]["dart"] == "mismatch" and not any(c.get("_dart") for c in out)


# ============================================================================ C8 — ô nguồn và độ mới dữ liệu
def _write_meta(path, at):
    import json
    with open(DA.CORP_DL_PATH, "w", encoding="utf-8") as f:
        f.write("stock_code,corp_code,corp_name\n330590,00000001,x\n")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"downloaded_at": at}, f)


def test_source_line_and_stale_corp_map(monkeypatch):
    _no_dart(monkeypatch)
    ctx = P.prepare("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, [], 1.0, today=TODAY,
                    src_info={"rf_asof": "17/11/2026", "beta_weeks": 52})
    line = ctx["dq"]["source_line"]
    assert "Naver quý 2026.06" in line and "Rf 17/11/2026" in line and "beta 52 tuần" in line
    c = _chk(ctx["dq"], "Nguồn và độ mới dữ liệu")
    assert c["Kết quả"] == P.OK                                    # DART tắt: không xét danh mục corp_code
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", lambda *a, **k: [])
    _write_meta(DA.CORP_META_PATH, "2026-10-01T00:00:00+00:00")
    old = P.prepare("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, [], 1.0, today=TODAY)
    c = _chk(old["dq"], "Nguồn và độ mới dữ liệu")
    assert c["Kết quả"] == P.WARN and "01/10/2026" in c["Giá trị"]
    assert old["dq"]["n_warn"] == ctx["dq"]["n_warn"] + 1
    _write_meta(DA.CORP_META_PATH, "2026-11-10T00:00:00+00:00")
    new = P.prepare("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, [], 1.0, today=TODAY)
    assert _chk(new["dq"], "Nguồn và độ mới dữ liệu")["Kết quả"] == P.OK


def test_source_cell_repeats_flags_without_double_counting():
    s = [{"period": f"{y}12", "is_consensus": False, "revenue": 1000.0 + y, "op_profit": 120.0, "net_profit": 100.0,
          "eps": 1000.0, "bps": 10000.0, "dps": 300.0, "roe": 10.0, "equity": 1000.0, "_dart": True,
          "_src": {"dart": "error", "note": "DART lỗi", "gaps": []}} for y in range(2021, 2026)]
    row = {"ticker": "000001", "price": 10000.0, "market_cap": 1e11, "eps": 1000.0, "bps": 10000.0, "dps": 300.0}
    peers = [{"ticker": "000002", "fetch_ok": False}, {"ticker": "000003", "per": 10.0}]
    base = P.data_quality(row, s, [], 1.0, today=TODAY, feeds={"Bộ đối thủ": {"ok": False, "note": "x"}})
    info = P._source_info({}, s, [], [], peers)
    dq = P.data_quality(row, s, [], 1.0, today=TODAY, feeds={"Bộ đối thủ": {"ok": False, "note": "x"}},
                        sources=info)
    assert (dq["n_fail"], dq["n_warn"], dq["cap"]) == (base["n_fail"], base["n_warn"], base["cap"])
    assert "DART năm 2021–2025 ❌ lỗi" in dq["source_line"] and "đối thủ 1/2 ❌" in dq["source_line"]


def test_screen_and_bot_carry_source_line(monkeypatch):
    import analysis as AN
    import bot_hunt as BH
    import screen as SC
    _no_dart(monkeypatch)
    monkeypatch.setattr(F, "fetch_financials", lambda tk: {"annual": LOTTE_A, "quarter": LOTTE_Q})
    monkeypatch.setattr(AN, "fetch_full", lambda tk, errors=None: None)
    out = SC.analyze_one("330590", dict(LOTTE_ROW), [], A, None, [], save=False, peers=[])
    assert out["Nguồn dữ liệu"].startswith("Naver quý 2026.06")
    p = {"ticker": "330590", "name": "롯데리츠", "price": 3600.0, "fair": 4000.0, "upside": 11.0, "verdict": "🟢",
         "presets": [], "data_warn": "Độ dài kỳ kế toán", "sources": out["Nguồn dữ liệu"]}
    assert "Naver quý 2026.06" in BH._line(p, {})
    assert "Nguồn dữ liệu" in BH._EXPORT_FIELDS


# ============================================================================ hạn mức DART theo phiên ở luồng phụ
def test_growth_parallel_map_uses_session_pool(monkeypatch):
    import growth as G
    seen = []
    mark = threading.local()
    monkeypatch.setattr(DA, "pool_kwargs", lambda: {"initializer": lambda: setattr(mark, "s", "khách"),
                                                    "initargs": ()})
    G.parallel_map(lambda k: seen.append(getattr(mark, "s", None)) or k, [1, 2, 3], workers=2)
    assert seen and all(x == "khách" for x in seen)


def _func_src(path, name):
    src = open(os.path.join(ROOT, path), encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(name)


def test_dart_thread_pools_carry_session():
    assert "pool_kwargs()" in _func_src("screen.py", "deep_stage")
    page = "pages/3_🧮_Dinh_gia.py"
    assert "pool_kwargs()" in _func_src(page, "_load_static_raw")
    assert "pool_kwargs()" in _func_src(page, "_load_market")


# ============================================================================ I08 — sửa sau review (lưới kỳ DART…)
def _half(p, rev=300.0, np_=100.0, eps=50.0):
    return {"period": p, "label": p, "is_consensus": False, "revenue": rev, "op_profit": rev * 0.6,
            "net_profit": np_, "ctrl_net": np_, "eps": eps, "bps": 5000.0, "roe": 2.0, "dps": 50.0, "per": 40.0}


def _dart_dec(years=range(2020, 2026), rev=300.0, np_=100.0):
    return [{"period": f"{y}12", "revenue": rev, "op_profit": rev * 0.6, "net_profit": np_, "assets": 1e4,
             "liabilities": 5e3, "equity": 5e3, "debt_ratio": 100.0, "currency": "KRW"} for y in years]


def test_off_grid_reit_drops_december_dart_no_double_count(monkeypatch):
    # REIT kỳ 6 tháng kết thúc tháng 3/9 (như 293940): DART 'YYYY12' không trùng kỳ nào → không ghép (từng ra TTM
    # 18 tháng: doanh thu 900, EPS ×12/9)
    calls = []
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", lambda *a, **k: calls.append(1) or _dart_dec())
    nav = [_half(p) for p in ("202503", "202509", "202603", "202609")]
    out = F._merge_dart(nav, "293940")
    last = out[-1]
    assert (last["revenue"], last["net_profit"], last["eps"]) == (600, 200, 100) and last["_ttm"] and last["_ttm_eps"]
    assert last["_ttm_parts"] == [("202609", 6), ("202603", 6)]
    assert not any(c.get("_dart") for c in out) and not calls            # không tốn lượt DART
    assert out[-1]["_src"]["dart"] == "none" and "không trùng" in out[-1]["_src"]["note"]
    ctx = P.prepare("293940", {"ticker": "293940", "price": 4000.0, "market_cap": 4e11, "eps": 50.0, "per": 80.0,
                               "bps": 5000.0, "dps": 100.0, "industry_kr": "부동산"},
                    {"annual": nav, "quarter": []}, A, [], 1.0, today=TODAY)
    assert ctx["row"]["eps"] == 100 and ctx["row"]["per"] == 40.0
    assert _chk(ctx["dq"], "Độ dài kỳ kế toán")["Kết quả"] == P.WARN


def test_off_grid_quarterly_reit_drops_december_dart(monkeypatch):
    # REIT kỳ 3 tháng kết thúc 1/4/7/10 (như 448730)
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", lambda *a, **k: _dart_dec(rev=150.0, np_=50.0))
    nav = [_half(p, 150.0, 50.0, 25.0) for p in ("202507", "202510", "202601", "202604", "202607", "202610")]
    last = F._merge_dart(nav, "448730")[-1]
    assert (last["revenue"], last["net_profit"], last["eps"]) == (600, 200, 100) and last["_ttm"]
    assert [p for p, _ in last["_ttm_parts"]] == ["202610", "202607", "202604", "202601"]
    assert not last.get("_dart")


def test_short_path_cached_requires_full_coverage(monkeypatch):
    # như test_g1_data_gates ở nhánh năm: DART lỗi, err_years rỗng, bản lưu chỉ 2021–22 → chuỗi cụt → 'error' (❌)
    def fake(ticker, years=6, errors=None, err_years=None):
        errors.append("lỗi DART 020")
        return _dart_dec(years=(2021, 2022))
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", fake)
    nav = [_half(p) for p in ("202506", "202512", "202606")]
    assert F._merge_dart(nav, "330590")[-1]["_src"]["dart"] == "error"

    def full(ticker, years=6, errors=None, err_years=None):           # bản lưu đủ 2021–25 → 'cached' (⚠️)
        errors.append("lỗi DART 020")
        return _dart_dec(years=range(2021, 2026))
    monkeypatch.setattr(DA, "annual_financials", full)
    assert F._merge_dart(nav, "330590")[-1]["_src"]["dart"] == "cached"


def test_short_path_latest_dart_without_naver_twin_is_dropped(monkeypatch):
    # kỳ 3 tháng, Naver chỉ có 202603/202606/202609 (không có cột 202512) → không đối chiếu được ±5% → không ghép
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", lambda *a, **k: _dart_dec(rev=999.0, np_=1.0))
    nav = [_half(p, 150.0, 50.0, 25.0) for p in ("202603", "202606", "202609")]
    out = F._merge_dart(nav, "000010")
    assert not any(c.get("_dart") for c in out) and out[-1]["_src"]["dart"] == "none"


def test_scaled_years_not_counted_as_real_and_quarter_only_dropped(monkeypatch):
    # Lotte + DART: năm 202206 chỉ có H2/2021 (×2) → không tính là năm BCTC thực
    monkeypatch.setattr(DA, "available", lambda: True)
    monkeypatch.setattr(DA, "annual_financials", _FakeDart(_dart_h2()))
    ctx = P.prepare("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, [], 1.0, today=TODAY)
    reals = [c for c in ctx["series"] if not c.get("is_consensus")]
    n_ttm = sum(1 for c in reals if c["_ttm"])
    c = _chk(ctx["dq"], "Số năm BCTC thực")
    assert c["Giá trị"] == n_ttm < len(reals) and "ước từ một phần kỳ" in c["Ghi chú"]
    # kỳ 3 tháng: năm cũ chỉ có quý 4 của DART (× 4) → bỏ khỏi chuỗi (mùa vụ méo CAGR)
    monkeypatch.setattr(DA, "annual_financials", lambda *a, **k: _dart_dec(years=range(2021, 2026), rev=704.0,
                                                                            np_=336.0))
    out = F._merge_dart(SK_A, "395400", quarters=SK_Q)
    assert out[-1]["_src"]["dart"] == "ok"
    assert all((c.get("_scale") or 0) <= F.TTM_MAX_SCALE for c in out[:-1])


def test_short_target_annualizes_peer_short_period_pe_and_keeps_anchor(monkeypatch):
    """PER kỳ 6 tháng của đối thủ quy về 12 tháng (× 6/12) thay vì bỏ: phép neo peer vẫn có ≥ 2 phương pháp → cổng
    [0,25; 4]× phép neo không bị tắt cho REIT kỳ ngắn (rà soát cuối 18/11/2026)."""
    _no_dart(monkeypatch)
    peers = [{"ticker": t, "per": 40.0, "pbr": 0.9} for t in ("000101", "000102", "000103", "000104", "000105")]
    ctx = P.assess("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, peers, 1.0, today=TODAY)
    E = ctx["E"]
    assert E["pm"]["pe"] == pytest.approx(20.0)                           # 40 × 6/12, không nhân chéo EPS 12 tháng × 40
    pe_row = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("P/E × trung vị peer"))
    assert pe_row.get("value") == pytest.approx(ctx["row"]["eps"] * 20.0, rel=1e-6)
    pb_row = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("P/B × trung vị peer"))
    assert pb_row.get("value")
    assert E["anchor"] is not None


def test_short_period_model_far_above_pb_anchor_is_blocked(monkeypatch):
    """REIT kỳ ngắn, PER đối thủ không dùng được (không có): phép neo = P/B đơn lẻ, mô hình chính > 4× phép neo bị chặn
    dù vẫn trong [0,4; 2,5]× giá."""
    _no_dart(monkeypatch)
    peers = [{"ticker": t, "per": None, "pbr": 0.15} for t in ("000101", "000102", "000103", "000104", "000105")]
    ctx = P.assess("330590", dict(LOTTE_ROW), {"annual": LOTTE_A, "quarter": LOTTE_Q}, A, peers, 1.0, today=TODAY)
    E = ctx["E"]
    pb = E["base"].methods.get("P/B vs median ngành")
    assert pb and E["anchor"] == pytest.approx(pb)
    price = ctx["row"]["price"]
    for r in ctx["rec"]["rows"]:
        if r["role"] == "primary" and r.get("value") and r["pass"]:
            assert 0.25 <= r["value"] / E["anchor"] <= 4.0, r
    far = [r for r in ctx["rec"]["rows"] if r["role"] == "primary" and r.get("value") and not r.get("blocked")
           and 0.4 <= r["value"] / price <= 2.5 and r["value"] / E["anchor"] > 4.0]
    assert far and all(not r["pass"] and "phép neo" in r["why"] for r in far)


def test_prepare_loss_making_reit_net_income_is_ttm(monkeypatch):
    _no_dart(monkeypatch)
    a = [_half(p, 300.0, -40.0, -20.0) for p in ("202506", "202512", "202606")]
    row = {"ticker": "000011", "price": 3000.0, "market_cap": 3e11, "eps": -20.0, "per": None, "bps": 5000.0,
           "dps": 0.0, "net_income": -4e9, "industry_kr": "부동산"}
    ctx = P.prepare("000011", row, {"annual": a, "quarter": []}, A, [], 1.0, today=TODAY)
    assert ctx["row"]["per"] is None and ctx["row"]["eps"] == -40.0
    assert ctx["row"]["net_income"] == -80.0 * 1e8                    # LN 12 tháng, không giữ số kỳ 6 tháng
