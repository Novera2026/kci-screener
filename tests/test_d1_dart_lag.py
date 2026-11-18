"""D1 — Naver chậm hơn DART (rà soát 18/11/2026, I05 + I14). Không mạng: DART/Naver giả lập."""
import datetime as dt
import json
import os

import pytest

import analysis as AN
import dcf as DCF
import financials as F
import pipeline as P
import valuation as V
from tests.test_pipeline import _row, _series

SH = 1e7          # số cp


# ---------------------------------------------------------------------------- dữ liệu giả
def _nq(p, rev, np_, cons=False, parent=0.9):
    c = np_ * parent
    return {"period": p, "is_consensus": cons, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "ctrl_net": c, "eps": round(c * 1e8 / SH)}


def _dq(y, q, rev, np_, parent=0.9, fs_div="CFS", cur="KRW", eqp=60000.0):
    return {"label": f"Q{q}.{str(y)[2:]}", "year": y, "q": q, "fs_div": fs_div, "currency": cur,
            "flow": {"revenue": rev, "op": np_ * 1.2, "ni": np_, "ni_parent": np_ * parent},
            "cf": {}, "bs": {"equity_parent": eqp}, "yoy": {}, "note": ""}


# Naver: 2025.09 → 2026.06 thực, 2026.09 đồng thuận (thấp hơn số thực 22%); DART: các quý đó + Q3/2026 đã nộp
NAVER_Q = [_nq("202509", 3000, 300), _nq("202512", 3100, 310), _nq("202603", 3200, 320), _nq("202606", 3300, 330),
           _nq("202609", 2700, 270, cons=True)]
DART_Q = [_dq(2025, 3, 3000, 300), _dq(2025, 4, 3100.4, 310.2), _dq(2026, 1, 3200, 320), _dq(2026, 2, 3300, 330),
          _dq(2026, 3, 3500, 360)]


# ---------------------------------------------------------------------------- I05: ghép quý DART
def test_merge_dart_quarters_replaces_consensus_with_filed_quarter():
    out, info = F.merge_dart_quarters(NAVER_Q, DART_Q)
    assert info["state"] == "merged" and info["added"] == ["202609"]
    q3 = next(c for c in out if c["period"] == "202609")
    assert not q3["is_consensus"] and q3["_src"] == "DART"
    assert (q3["revenue"], q3["net_profit"], q3["ctrl_net"]) == (3500, 360, pytest.approx(324))
    assert q3["eps"] == round(324 * 1e8 / SH)              # LN cổ đông mẹ ÷ số cp của quý Naver thực cuối
    assert [c["period"] for c in out] == ["202509", "202512", "202603", "202606", "202609"]


@pytest.mark.parametrize("bad, state", [
    ({"rev": 3300 * 1.03}, "mismatch"),                 # quý trùng lệch > 2%
    ({"cur": "USD"}, "currency"),
    ({"fs_div": "OFS"}, "fs_div"),
])
def test_merge_dart_quarters_refuses_when_sources_disagree(bad, state):
    dq = [dict(d) for d in DART_Q]
    dq[3] = _dq(2026, 2, bad.get("rev", 3300), 330, fs_div=bad.get("fs_div", "CFS"), cur=bad.get("cur", "KRW"))
    out, info = F.merge_dart_quarters(NAVER_Q, dq)
    assert info["state"] == state and not info["added"] and "2026.09" in info["note"]
    assert out == NAVER_Q                                   # giữ nguyên Naver


def test_merge_dart_quarters_noop_when_dart_not_newer():
    out, info = F.merge_dart_quarters(NAVER_Q, DART_Q[:-1])
    assert info["state"] == "none" and out == NAVER_Q


def _ser_ctrl():
    s = _series()
    for c in s:
        c["ctrl_net"] = c["net_profit"] * 0.9
        c["eps"] = c["ctrl_net"] * 1e8 / SH
    return s


def _scale(qs, k):
    out = []
    for c in qs:
        c = dict(c)
        for f in ("revenue", "op_profit", "net_profit", "ctrl_net"):
            c[f] = c[f] * k
        c["eps"] = round(c["ctrl_net"] * 1e8 / SH)
        out.append(c)
    return out


def _scale_d(dq, k):
    return [{**d, "flow": {f: v * k for f, v in d["flow"].items()}} for d in dq]


def test_prepare_uses_filed_dart_quarter_for_year1():
    s = _ser_ctrl()
    k = s[-1]["revenue"] / 12600                            # quý cùng cỡ với năm 2025 của chuỗi
    nq, dq = _scale(NAVER_Q, k), _scale_d(DART_Q, k)
    today = dt.date(2026, 11, 18)
    base = P.prepare("000001", _row(s), {"annual": s, "quarter": nq}, V.Assumptions(), [], 1.0, series=s, today=today)
    assert F._ytd_run_rate(P._reals(base["series"]), base["quarters"])["k"] == 2
    ctx = P.prepare("000001", _row(s), {"annual": s, "quarter": nq}, V.Assumptions(), [], 1.0, series=s, today=today,
                    dart_quarters=dq)
    assert ctx["dart_q"]["state"] == "merged"
    assert F._ytd_run_rate(P._reals(ctx["series"]), ctx["quarters"])["k"] == 3
    chk = {c["Kiểm tra"]: c for c in ctx["dq"]["checks"]}
    assert chk["Quý thực gần nhất"]["Giá trị"] == "2026.09" and "DART" in chk["Quý thực gần nhất"]["Ghi chú"]
    assert "Quý DART mới hơn Naver" not in chk
    assert "3 quý thực" in ctx["basis"]
    # cột dự phóng của chuỗi truyền sẵn được tính lại theo quý đã ghép
    fc_col = next(c for c in ctx["series"] if c.get("is_forecast"))
    assert fc_col["_basis"].startswith("3 quý thực")
    # EBIT 4 quý (EV/EBIT TTM) gồm quý DART
    assert DCF.ttm_ebit(ctx["quarters"]) == pytest.approx(
        sum(c["op_profit"] for c in nq[1:4]) + dq[4]["flow"]["op"])


def test_prepare_warns_when_dart_quarter_cannot_be_merged():
    s = _ser_ctrl()
    k = s[-1]["revenue"] / 12600
    nq, dq = _scale(NAVER_Q, k), _scale_d(DART_Q, k)
    dq[2] = {**dq[2], "flow": {**dq[2]["flow"], "revenue": dq[2]["flow"]["revenue"] * 1.05}}
    ctx = P.prepare("000001", _row(s), {"annual": s, "quarter": nq}, V.Assumptions(), [], 1.0, series=s,
                    today=dt.date(2026, 11, 18), dart_quarters=dq)
    assert ctx["dart_q"]["state"] == "mismatch"
    assert F._ytd_run_rate(P._reals(ctx["series"]), ctx["quarters"])["k"] == 2
    chk = {c["Kiểm tra"]: c for c in ctx["dq"]["checks"]}
    assert chk["Quý DART mới hơn Naver"]["Kết quả"] == P.WARN
    assert chk["Quý thực gần nhất"]["Giá trị"] == "2026.06"


def test_quarter_table_labels_dart_quarter_as_actual():
    out, _ = F.merge_dart_quarters(NAVER_Q, DART_Q)
    fc = [{"Năm": "2026", "Doanh thu (억)": 14000, "LN HĐ (억)": 1700, "LN ròng (억)": 1400, "LN cổ đông mẹ (억)": 1260},
          {"Năm": "2027", "Doanh thu (억)": 15000, "LN HĐ (억)": 1800, "LN ròng (억)": 1500, "LN cổ đông mẹ (억)": 1350}]
    rows = DCF.quarter_forecast(out, fc, SH)
    q3 = [r for r in rows if r["Quý"] == "2026.09"]
    assert len(q3) == 1 and q3[0]["Loại"] == "Thực (DART)" and q3[0]["DT (억)"] == 3500
    assert [r["Loại"] for r in rows if r["Quý"].startswith("2026")] == ["Thực", "Thực", "Thực (DART)", "Dự phóng"]


def test_fetch_quarter_reports_offline_reads_cache_only(monkeypatch, tmp_path):
    import dart_api
    monkeypatch.setattr(dart_api, "available", lambda: True)
    monkeypatch.setattr(dart_api, "corp_code_of", lambda tk: "00000001")
    monkeypatch.setattr(dart_api, "_today", lambda: dt.date(2026, 11, 18))
    calls = []
    monkeypatch.setattr(dart_api, "fetch_statements", lambda *a, **k: calls.append(a) or [])
    rep = {"year": 2026, "rc": "11014", "fs_div": "CFS", "currency": "USD", "_v": AN.FULL_V,
           "pt": {"revenue": 10.0, "ni": 1.0, "assets": 50.0}, "ytd": {}, "yoy": {},
           "sj": {"revenue": "IS", "ni": "IS", "assets": "BS"}}
    with open(os.path.join(tmp_path, "00000001_2026_11014.json"), "w") as f:
        json.dump(rep, f)
    out = AN.fetch_quarter_reports("000001", cache_dir=str(tmp_path), offline=True)
    assert calls == [] and [r["rc"] for r in out] == ["11014"]
    assert AN.quarter_series(out)[0]["currency"] == "USD"          # đơn vị tiền đi theo quý → cổng KRW


def test_screen_and_summary_pass_cached_dart_quarters(monkeypatch):
    seen = []
    monkeypatch.setattr(AN, "fetch_quarter_reports", lambda tk, **k: seen.append(k) or [])
    assert P.cached_dart_quarters("000001") == [] and seen == [{"offline": True}]
    import inspect

    import screen
    src = inspect.getsource(screen.analyze_one)
    assert "dart_quarters=P.cached_dart_quarters(tk)" in src
    with open(os.path.join(os.path.dirname(P.__file__), "views", "tong_hop.py"), encoding="utf-8") as f:
        assert f.read().count("dart_quarters=_P.cached_dart_quarters(") == 2


# ---------------------------------------------------------------------------- I14: năm chỉ có ở DART
class _FakeDart:
    def __init__(self, rows):
        self.rows = rows

    def available(self):
        return True

    def annual_financials(self, ticker, years=6, errors=None, err_years=None):
        return [dict(r) for r in self.rows]


def _na(y, rev, np_, cons=False):
    c = np_ * 0.9
    return {"period": f"{y}12", "is_consensus": cons, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "ctrl_net": c, "eps": round(c * 1e8 / SH), "bps": 500000 + (y - 2022) * 10000, "dps": 1000.0,
            "roe": 10.0, "equity": 55000.0 + (y - 2022) * 1000}


def _da(y, rev, np_):
    return {"period": f"{y}12", "revenue": rev, "op_profit": None if np_ is None else np_ * 1.2, "net_profit": np_,
            "assets": 200000.0, "liabilities": 100000.0, "equity": 58000.0 + (y - 2022) * 1000,
            "debt_ratio": 100.0, "currency": "KRW", "fs_div": "CFS"}


NAVER_A = [_na(2022, 100000, 10000), _na(2023, 105000, 10500), _na(2024, 110000, 11000),
           _na(2025, 99000, 9000, cons=True)]
NAVER_Q25 = [_nq("202503", 28000, 2800), _nq("202506", 29000, 2900), _nq("202509", 30000, 3000),
             _nq("202512", 26000, 2600, cons=True)]
EQP25 = 64000.0


def _dart_full(monkeypatch, ni_parent=10800.0, ni=12000.0):
    monkeypatch.setattr(AN, "fetch_full", lambda tk, year=None, **k: {
        "year": year, "fs_div": "CFS", "currency": "KRW",
        "cur": {"ni_parent": ni_parent, "equity_parent": EQP25, "ni": ni}} if year == 2025 else None)


def test_dart_only_year_gets_per_share_figures(monkeypatch):
    monkeypatch.setattr(F, "dart_api", _FakeDart([_da(y, r, n) for y, r, n in
                                                  ((2022, 100000, 10000), (2023, 105000, 10500),
                                                   (2024, 110000, 11000), (2025, 120000, 12000))]))
    _dart_full(monkeypatch)
    out = F._merge_dart(NAVER_A, "X", quarters=NAVER_Q25)
    y25 = out[-1]
    assert y25["period"] == "202512" and y25["_dart_only"] and y25["_src"]["dart"] == "ok"
    assert y25["ctrl_net"] == 10800.0 and y25["eps"] == round(10800 * 1e8 / SH)
    assert y25["bps"] == round(EQP25 * 1e8 / SH) and y25["equity_parent"] == EQP25


def test_dart_only_year_without_revenue_not_added(monkeypatch):
    # ngân hàng / bảo hiểm: DART không có dòng doanh thu → giữ năm Naver cũ + quý thực
    monkeypatch.setattr(F, "dart_api", _FakeDart([_da(2023, 105000, 10500), _da(2024, 110000, 11000),
                                                  _da(2025, None, 12000)]))
    _dart_full(monkeypatch)
    out = F._merge_dart(NAVER_A, "X", quarters=NAVER_Q25)
    assert out[-1]["period"] == "202412" and out[-1]["_src"]["dart"] == "ok"


@pytest.mark.parametrize("q4_real", [False, True])
def test_dart_only_year_inflated_is_source_mismatch(monkeypatch, q4_real):
    # DART năm 2025 ×3 (đơn vị / phạm vi sai) — trước đây vẫn '_src ok'
    monkeypatch.setattr(F, "dart_api", _FakeDart([_da(2023, 105000, 10500), _da(2024, 110000, 11000),
                                                  _da(2025, 360000, 36000)]))
    _dart_full(monkeypatch, ni_parent=32400.0, ni=36000.0)
    q = [dict(c) for c in NAVER_Q25]
    if q4_real:
        q[-1]["is_consensus"] = False
    out = F._merge_dart(NAVER_A, "X", quarters=q)
    assert out[-1]["_src"]["dart"] == "mismatch" and "2025" in out[-1]["_src"]["note"]
    assert ("tổng 4 quý" in out[-1]["_src"]["note"]) == q4_real
    assert out[-1]["period"] == "202412" and not any(c.get("_dart") for c in out)


def test_run_forecast_never_takes_quarter_bps_as_b0():
    s = _series()
    s[-1]["bps"] = None
    r = _row(_series())
    r["bps"] = 999999.0                                   # BPS cuối quý của row — không được làm B0
    drv = [{"Năm": "2026", "Tăng trưởng DT %": 5, "Biên LN ròng %": 10, "Tỷ lệ cổ tức %": 30}]
    assert P.run_forecast(s, r, drv) == []
    s[-1]["equity_parent"] = 6000.0
    fc = P.run_forecast(s, r, drv)
    sh = F.share_basis(P._reals(s), None, r["market_cap"], r["price"])["shares"]
    assert fc[0]["BPS đầu kỳ"] == pytest.approx(6000.0 * 1e8 / sh)


def test_project_forecast_base_revenue_is_latest_year():
    s = _series()
    s[-1]["revenue"] = None                               # năm gốc thiếu doanh thu (DART ngân hàng)
    assert F.project_forecast(s) is None
    assert "năm gốc 2025 thiếu doanh thu" in P.no_forecast_reason(s, [])


def test_quarter_age_ok_when_annual_covers_latest_quarter():
    s = _series()                                         # năm thực gần nhất 2025 (chỉ có ở DART)
    q = [{"period": "202509", "is_consensus": False}]
    dq = P.data_quality(_row(s), s, q, beta=1.0, today=dt.date(2026, 4, 5))
    chk = {c["Kiểm tra"]: c for c in dq["checks"]}
    assert chk["Quý thực gần nhất"]["Kết quả"] == P.OK
    # năm BCTC cũ hơn quý → vẫn ⚠️ khi quý quá cũ
    dq2 = P.data_quality(_row(s[:-1]), s[:-1], q, beta=1.0, today=dt.date(2026, 4, 5))
    assert {c["Kiểm tra"]: c for c in dq2["checks"]}["Quý thực gần nhất"]["Kết quả"] == P.WARN


def _assess_window(monkeypatch, dart_rev25, industry="식품"):
    monkeypatch.setattr(F, "dart_api", _FakeDart([_da(y, r, n) for y, r, n in
                                                  ((2022, 100000, 10000), (2023, 105000, 10500),
                                                   (2024, 110000, 11000), (2025, dart_rev25, 12000))]))
    _dart_full(monkeypatch)
    row = {"ticker": "000002", "name": "T", "price": 60000.0, "market_cap": 60000.0 * SH, "eps": 10000.0,
           "bps": 700000.0, "dps": 1000.0, "industry_kr": industry, "sector": None}
    return P.assess("000002", row, {"annual": NAVER_A, "quarter": NAVER_Q25}, V.Assumptions(), [], 1.0,
                    today=dt.date(2026, 3, 20))


def test_assess_in_dart_year_window_keeps_forecast_models(monkeypatch):
    ctx = _assess_window(monkeypatch, 120000)             # DT 2025 DART khớp 9 tháng Naver + Q4 hợp lý
    fc = ctx["E"]["fc"]
    assert ctx["drivers"] and fc
    assert fc[0]["Năm"] == "2026" and fc[0]["BPS đầu kỳ"] == pytest.approx(round(EQP25 * 1e8 / SH))
    rim = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("RIM"))
    assert rim["value"] is not None and not str(rim.get("blocked") or "").startswith("không có dự phóng")


def test_assess_bank_in_dart_year_window_uses_quarters(monkeypatch):
    ctx = _assess_window(monkeypatch, None, industry="은행")   # DART không có doanh thu → giữ 2024 + 3 quý 2025
    assert P._reals(ctx["series"])[-1]["period"] == "202412"
    assert ctx["drivers"] and ctx["drivers"][0]["Năm"] == "2025" and "3 quý thực" in ctx["basis"]
    rim = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("RIM"))
    assert rim["value"] is not None


# ---------------------------------------------------------------------------- I05: trang 🧮 (AppTest, không mạng)
from tests.test_g2_page_inputs import _ok, page  # noqa: E402,F401


def _nq0(p, rev, np_, cons=False):
    return {"period": p, "is_consensus": cons, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "eps": round(np_ * 1e8 / SH)}


def _dq0(y, q, rev, np_):
    return {"label": f"Q{q}.{str(y)[2:]}", "year": y, "q": q, "fs_div": "CFS", "currency": "KRW",
            "flow": {"revenue": rev, "op": np_ * 1.2, "ni": np_}, "cf": {}, "bs": {}, "yoy": {}, "note": ""}


def test_page_step4_shows_filed_dart_quarter_not_forecast(page, monkeypatch):
    import financials
    s = _series()
    nq = [_nq0("202509", 300, 30), _nq0("202512", 310, 31), _nq0("202603", 320, 32), _nq0("202606", 330, 33),
          _nq0("202609", 250, 25, cons=True)]
    dq = [_dq0(2025, 3, 300, 30), _dq0(2025, 4, 310, 31), _dq0(2026, 1, 320, 32), _dq0(2026, 2, 330, 33),
          _dq0(2026, 3, 360, 37)]
    monkeypatch.setattr(financials, "fetch_financials", lambda tk: {"annual": s, "quarter": nq})
    monkeypatch.setattr(AN, "quarter_series", lambda reps: [dict(d) for d in dq])
    _ok(page.run())
    tabs = [df.value for df in page.dataframe if "Loại" in getattr(df.value, "columns", [])]
    assert tabs, "không thấy bảng dự phóng quý ở bước ④"
    q = tabs[0]
    q3 = q[q["Quý"] == "2026.09"]
    assert len(q3) == 1 and q3["Loại"].iloc[0] == "Thực (DART)" and q3["DT (억)"].iloc[0] == 360
    chk = [df.value for df in page.dataframe if "Kiểm tra" in getattr(df.value, "columns", [])]
    assert any(((c["Kiểm tra"] == "Quý thực gần nhất") & (c["Giá trị"] == "2026.09")).any() for c in chk)


# ---------------------------------------------------------------------------- sửa theo rà soát nhóm D1
def test_quarter_age_still_warns_when_quarter_feed_stuck_at_annual_q4():
    # năm 2025 + quý kẹt ở 2025.12: tới 20/08/2026 (8 tháng) vẫn ⚠️ — 'năm đã gồm quý' không được mở cổng tuổi quý
    s = _series()
    q = [{"period": "202509", "is_consensus": False}, {"period": "202512", "is_consensus": False}]
    for today in (dt.date(2026, 8, 20), dt.date(2026, 11, 18)):
        dq = P.data_quality(_row(s), s, q, beta=1.0, today=today)
        assert {c["Kiểm tra"]: c for c in dq["checks"]}["Quý thực gần nhất"]["Kết quả"] == P.WARN
    dq = P.data_quality(_row(s), s, q, beta=1.0, today=dt.date(2026, 5, 20))
    assert {c["Kiểm tra"]: c for c in dq["checks"]}["Quý thực gần nhất"]["Kết quả"] == P.OK


def test_prepare_bank_dart_quarter_revenue_definition_no_warn():
    # ngân hàng: 'doanh thu' quý DART (~0,3× Naver), LN thuần khớp → không ghép, không ⚠️ cấu trúc
    s = _ser_ctrl()
    k = s[-1]["revenue"] / 12600
    nq, dq = _scale(NAVER_Q, k), _scale_d(DART_Q, k)
    dq = [{**d, "flow": {**d["flow"], "revenue": d["flow"]["revenue"] * 0.3}} for d in dq]
    r = _row(s)
    r["industry_kr"], r["sector"] = "은행", None
    assert P.identify(r)["financial"]
    ctx = P.prepare("105560", r, {"annual": s, "quarter": nq}, V.Assumptions(), [], 1.0, series=s,
                    today=dt.date(2026, 11, 18), dart_quarters=dq)
    chk = {c["Kiểm tra"]: c for c in ctx["dq"]["checks"]}
    assert ctx["dart_q"]["state"] == "none" and "Quý DART mới hơn Naver" not in chk
    assert ctx["quarters"] == nq


def test_merge_dart_quarters_skips_voided_restated_overlap_quarter():
    # Q4.25 DART bị để trống (năm điều chỉnh lại: Q4 = năm − 9 tháng không dùng) — các quý trùng khác khớp → vẫn ghép
    empty = {"revenue": None, "op": None, "ni": None, "ni_parent": None}
    dq = [dict(d) for d in DART_Q]
    dq[1] = {**dq[1], "flow": dict(empty)}
    _out, info = F.merge_dart_quarters(NAVER_Q, dq)
    assert info["state"] == "merged" and info["added"] == ["202609"]
    # không còn quý trùng nào đối chiếu được → no_overlap (không phải 'mismatch')
    dq2 = [{**d, "flow": dict(empty)} for d in DART_Q[:-1]] + [DART_Q[-1]]
    _out, info2 = F.merge_dart_quarters(NAVER_Q, dq2)
    assert info2["state"] == "no_overlap"


def test_dart_only_year_seasonal_q4_not_added_not_mismatch(monkeypatch):
    # Q4 suy ra = 90.000 (> 3× trung vị 3 quý) nhưng năm ÷ run-rate 9 tháng = 1,5 → không gãy nguồn: không thêm năm,
    # không ❌ 'mismatch' (giữ 2024 + quý thực như trước khi DART nộp)
    monkeypatch.setattr(F, "dart_api", _FakeDart([_da(2023, 105000, 10500), _da(2024, 110000, 11000),
                                                  _da(2025, 177000, 18000)]))
    _dart_full(monkeypatch, ni_parent=16200.0, ni=18000.0)
    out = F._merge_dart(NAVER_A, "X", quarters=NAVER_Q25)
    assert out[-1]["period"] == "202412" and out[-1]["_src"]["dart"] == "ok"
    assert not any(c.get("_dart_only") for c in out)
