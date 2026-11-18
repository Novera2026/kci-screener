"""Rà soát 18/11/2026 — nhóm G1: cổng dữ liệu và chuỗi BCTC (I01, I03, I04, I13, I16, I44, I45, I49).
Chạy offline: requests / DART / Naver đều giả lập."""
import ast
import datetime as dt
import os

import pytest

import dart_api as DA
import dcf
import financials as F
import pipeline as P
import valuation as V

A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _series(n=5, rev0=1000.0, g=0.05, margin=0.10, eq0=5000.0, dps=500.0, shares=1e7, dart=True, end=2025, m="12"):
    out, eq = [], eq0
    for i in range(n):
        rev = rev0 * (1 + g) ** i
        np_ = rev * margin
        eq += np_ - dps * shares / 1e8
        out.append({"period": f"{end - n + 1 + i}{m}", "is_consensus": False, "revenue": rev, "op_profit": rev * 0.12,
                    "net_profit": np_, "equity": eq, "eps": np_ * 1e8 / shares, "bps": eq * 1e8 / shares, "dps": dps,
                    "roe": np_ / eq * 100, "debt_ratio": 80.0, "_dart": dart})
    return out


def _row(series, price=None, shares=1e7, ik="식품"):
    last = series[-1]
    price = price or last["bps"] * 1.0
    return {"ticker": "000001", "name": "Test", "price": price, "market_cap": price * shares,
            "eps": last["eps"], "bps": last["bps"], "dps": last["dps"], "industry_kr": ik, "sector": None}


def _chk(dq, name):
    return next(c for c in dq["checks"] if c["Kiểm tra"].startswith(name))


Q26 = [{"period": p, "is_consensus": False} for p in ("202603", "202606", "202609")]
PEERS = [{"ticker": t, "per": pe, "pbr": pb} for t, pe, pb in (("a", 10, 1.0), ("b", 12, 1.2), ("c", 14, 1.4))]


# ============================================================================ I01 — hạn nộp BCTC năm
@pytest.mark.parametrize("today, ok", [(dt.date(2026, 12, 31), True), (dt.date(2027, 1, 2), True),
                                       (dt.date(2027, 2, 15), True), (dt.date(2027, 3, 15), True),
                                       (dt.date(2027, 3, 31), True), (dt.date(2027, 4, 15), False)])
def test_fy_gate_follows_filing_deadline(today, ok):
    s = _series()                                                  # FY2021–FY2025
    dq = P.data_quality(_row(s), s, Q26, beta=0.9, today=today)
    c = _chk(dq, "Năm BCTC thực gần nhất")
    assert (c["Kết quả"] == P.OK) is ok, c
    if ok:
        assert dq["n_fail"] == 0 and dq["cap"] is None, dq["checks"]
        if today.year == 2027:                                      # giai đoạn chờ nộp: ghi chú, vẫn ✅
            assert "đang chờ BCTC năm 2026" in c["Ghi chú"] and "31/03" in c["Ghi chú"]
        else:
            assert c["Ghi chú"] == ""
    else:
        assert dq["cap"] == "Low" and "2026" in c["Ghi chú"]


def test_fy_gate_march_fiscal_year():
    s = _series(m="03")                                            # năm tài chính kết thúc tháng 3: FY2025 = 202503
    ok = _chk(P.data_quality(_row(s), s, Q26, beta=0.9, today=dt.date(2026, 6, 20)), "Năm BCTC thực gần nhất")
    assert ok["Kết quả"] == P.OK and "đang chờ BCTC năm 2026" in ok["Ghi chú"] and "30/06" in ok["Ghi chú"]
    late = _chk(P.data_quality(_row(s), s, Q26, beta=0.9, today=dt.date(2026, 7, 15)), "Năm BCTC thực gần nhất")
    assert late["Kết quả"] == P.FAIL


def test_fy_gate_shared_rule_and_bot_line_clean():
    assert P.expected_fy(dt.date(2027, 1, 2), 12) == 2025 and P.expected_fy(dt.date(2027, 4, 1), 12) == 2026
    import bot_hunt
    s = _series()
    dq = P.data_quality(_row(s), s, Q26, beta=0.9, today=dt.date(2027, 1, 5))
    warn = "; ".join(c["Kiểm tra"] for c in dq["checks"] if c["Kết quả"] == P.FAIL)     # như screen.analyze_one
    p = {"ticker": "000001", "name": "Test", "price": 1000.0, "fair": 1200.0, "upside": 20.0, "presets": [],
         "confidence": "Medium", "data_warn": warn}
    assert "lỗi dữ liệu" not in bot_hunt._line(p, {})


# ============================================================================ I03 — BCTC Naver lỗi mạng
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_fetch_financials_retries_and_flags_error(monkeypatch):
    import requests
    calls = []

    def down(url, headers=None, timeout=None):
        calls.append(url)
        raise requests.exceptions.ReadTimeout("timeout")
    monkeypatch.setattr(F, "FETCH_RETRY_WAITS", (0, 0))
    monkeypatch.setattr(F.requests, "get", down)
    out = F.fetch_financials("005380")
    assert out["annual"] == [] and out["quarter"] == []
    assert set(out["_error"]) == {"annual", "quarter"} and len(calls) == 6     # 1 lần + 2 lần thử lại mỗi endpoint
    n = {"k": 0}

    def flaky(url, headers=None, timeout=None):
        n["k"] += 1
        if n["k"] == 1:
            raise requests.exceptions.ConnectionError("reset")
        return _Resp({"financeInfo": {"trTitleList": [], "rowList": []}})
    monkeypatch.setattr(F.requests, "get", flaky)
    assert "_error" not in F.fetch_financials("005380")                      # lần thử lại thành công → không cờ lỗi


def test_naver_error_caps_low_and_blocks_per_share_models():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    today = dt.date(2026, 11, 9)
    good = P.assess("000001", r, {"annual": [], "quarter": []}, A, PEERS, 1.0, series=s, today=today)
    assert not any(c["Kiểm tra"].startswith("BCTC Naver") for c in good["dq"]["checks"])
    bad = P.assess("000001", r, {"annual": [], "quarter": [], "_error": {"annual": "ReadTimeout"}}, A, PEERS, 1.0,
                   series=s, today=today)
    c = _chk(bad["dq"], "BCTC Naver (EPS/BPS/LN cổ đông mẹ)")
    assert c["Kết quả"] == P.FAIL and "ReadTimeout" in c["Giá trị"]
    assert bad["dq"]["cap"] == "Low" and bad["dq"]["share_break"]
    assert bad["rec"]["confidence"] in ("Low", None, "—") or bad["rec"]["fair"] is None
    rim = next(x for x in bad["E"]["rec"]["rows"] if x["name"].startswith("RIM tường minh"))
    assert not rim["pass"]
    assert any(ch["Kiểm tra"].startswith("BCTC Naver") for ch in bad["E"]["fg"]["checks"])


def test_dart_only_series_without_eps_is_flagged():
    s = _series()
    for c in s:                                                     # chuỗi chỉ có số DART: không EPS / LN cổ đông mẹ
        c.pop("eps")
    dq = P.prepare("000001", _row(_series()), {}, A, PEERS, 1.0, series=s, today=dt.date(2026, 11, 9))["dq"]
    assert _chk(dq, "BCTC Naver")["Kết quả"] == P.FAIL and dq["cap"] == "Low"


def _load_fin_cached(fetch):
    """Hàm _fin_cached* của views/tong_hop.py, chạy với st.cache_data giả (ghi nhớ như Streamlit: ngoại lệ không lưu)."""
    src = open(os.path.join(ROOT, "views", "tong_hop.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    keep = [n for n in tree.body if getattr(n, "name", "") in ("_FinIncomplete", "_fin_cached_ok", "_fin_cached")]
    assert len(keep) == 3

    class _St:
        @staticmethod
        def cache_data(**kw):
            def deco(fn):
                memo = {}

                def wrap(*a):
                    if a not in memo:
                        memo[a] = fn(*a)
                    return memo[a]
                return wrap
            return deco
    ns = {"st": _St, "fetch_financials": fetch}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "tong_hop", "exec"), ns)
    return ns["_fin_cached"]


def test_tong_hop_fin_cache_skips_error_and_empty():
    state = {"down": True, "n": 0}

    def fetch(tk):
        state["n"] += 1
        if state["down"]:
            return {"annual": [], "quarter": [], "_error": {"annual": "ReadTimeout", "quarter": "ReadTimeout"}}
        return {"annual": [{"period": "202512"}], "quarter": []}
    fc = _load_fin_cached(fetch)
    assert fc("005380").get("_error")
    state["down"] = False
    assert fc("005380")["annual"] and state["n"] == 2              # Naver hồi phục → lấy lại, không giữ bản rỗng
    assert fc("005380")["annual"] and state["n"] == 2              # bản đủ thì được cache


def test_screen_analyze_one_lists_naver_failure(monkeypatch):
    import analysis as AN
    import screen as SC
    import financials
    s = _series()
    for c in s:
        c.pop("eps")
    monkeypatch.setattr(financials, "fetch_financials",
                        lambda tk: {"annual": [], "quarter": [], "_error": {"annual": "ReadTimeout"}})
    monkeypatch.setattr(financials, "build_annual_series", lambda *a, **k: s)
    monkeypatch.setattr(AN, "fetch_full", lambda tk, errors=None: None)
    out = SC.analyze_one("000001", _row(_series()), [], A, None, [], save=False, peers=PEERS)
    assert "BCTC Naver" in out["Cảnh báo dữ liệu"] and out["Độ tin cậy"] in ("Low", "—")


# ============================================================================ I04 — DART lỗi giữa lượt lấy
class _FakeDart:
    def __init__(self, rows, errs=None, err_years=None):
        self.rows, self.errs, self.err_years = rows, errs or [], err_years or []

    def available(self):
        return True

    def annual_financials(self, ticker, years=6, errors=None, err_years=None):
        if errors is not None:
            errors.extend(self.errs)
        if err_years is not None:
            err_years.extend(self.err_years)
        return [dict(r) for r in self.rows]


def _nv(y, rev, np_):
    return {"period": f"{y}12", "is_consensus": False, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "eps": 100, "bps": 1000, "roe": 10.0}


def _dt(y, rev, np_):
    return {"period": f"{y}12", "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_, "assets": rev * 2,
            "liabilities": rev, "equity": rev, "debt_ratio": 100.0, "currency": "KRW"}


NAVER3 = [_nv(2023, 1000, 100), _nv(2024, 1100, 110), _nv(2025, 1200, 120)]
ERR = ["lỗi kết nối DART (ConnectionError)"]


def test_merge_dart_truncated_chain_is_error_not_cached(monkeypatch):
    # bản lưu 2024–25, DART lỗi từ 2023 → chuỗi cụt: phải là 'error' (❌, Low), không phải 'cached' (⚠️)
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2024, 1100, 110), _dt(2025, 1200, 120)], ERR, [2023]))
    out = F._merge_dart(NAVER3, "X")
    assert out[-1]["_src"]["dart"] == "error"
    dq = P.data_quality(_row(_series()), out, Q26, beta=1.0, today=dt.date(2026, 11, 9))
    assert _chk(dq, "Số năm có số gốc DART")["Kết quả"] == P.FAIL and dq["cap"] == "Low"
    # lỗi ở một năm cũ hơn mọi năm Naver nhưng vẫn ≤ năm gần nhất → vẫn là chuỗi cụt
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(y, 1000 + 100 * (y - 2023), 100 + 10 * (y - 2023))
                                                  for y in (2022, 2023, 2024, 2025)], ERR, [2021]))
    assert F._merge_dart(NAVER3, "X")[-1]["_src"]["dart"] == "error"
    # chỉ lỗi ở năm mới hơn năm Naver gần nhất, các năm lưu phủ đủ → 'cached'
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2023, 1000, 100), _dt(2024, 1100, 110), _dt(2025, 1200, 120)],
                                                 ERR, [2026]))
    assert F._merge_dart(NAVER3, "X")[-1]["_src"]["dart"] == "cached"


def test_annual_financials_reports_error_years(monkeypatch):
    def fy(cc, y, offline=False):
        if offline:
            return {"period": f"{y}12", "revenue": 1.0} if y == 2022 else None
        if y == 2023:
            raise DA.DartError("lỗi kết nối DART (ConnectionError)")
        return {"period": f"{y}12", "revenue": 1.0}
    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(DA, "corp_code_of", lambda t: "X")
    monkeypatch.setattr(DA, "_fetch_year", fy)
    errs, ey = [], []
    out = DA.annual_financials("005380", years=6, end_year=2025, errors=errs, err_years=ey)
    assert ey == [2023] and errs and [r["period"] for r in out] == ["202212", "202412", "202512"]
    # năm lỗi nhưng bản lưu (đời cũ) vẫn có → không thiếu năm nào
    monkeypatch.setattr(DA, "_fetch_year", lambda cc, y, offline=False: (
        {"period": f"{y}12", "revenue": 1.0} if offline or y != 2023 else (_ for _ in ()).throw(DA.DartError("x"))))
    ey = []
    DA.annual_financials("005380", years=3, end_year=2025, errors=[], err_years=ey)
    assert ey == []


# ============================================================================ I13 — cột Q4 của Naver trong run-rate
def test_quarter_ok_gate():
    assert F.quarter_ok({"revenue": 100, "net_profit": 10}, [90, 110, 120])
    assert not F.quarter_ok({"revenue": 0, "net_profit": 0}, [90])
    assert not F.quarter_ok({"revenue": 100, "net_profit": 150}, [90])            # LN > doanh thu
    assert not F.quarter_ok({"revenue": 25, "net_profit": 1}, [90, 100, 110])     # < 1/3 trung vị
    assert not F.quarter_ok({"revenue": 400, "net_profit": 1}, [90, 100, 110])    # > 3×
    assert F.quarter_ok({"revenue": 25, "net_profit": 1}, [])                     # không có lân cận → chỉ hai điều đầu


def test_dart_q4_gate_uses_quarter_ok_for_profit_above_revenue():
    import analysis as AN

    def rep(y, rc, pt, ytd=None):
        return {"year": y, "rc": rc, "fs_div": "CFS", "pt": pt, "ytd": ytd or {}, "yoy": {},
                "sj": {k: "IS" for k in pt}}
    q = [rep(2025, "11013", {"revenue": 100, "ni": 8}), rep(2025, "11012", {"revenue": 100, "ni": 8}),
         rep(2025, "11014", {"revenue": 100, "ni": 8}, ytd={"revenue": 300, "ni": 24}),
         rep(2025, "11011", {"revenue": 400, "ni": 224})]                             # Q4: DT 100, LN 200 > DT
    q4 = AN.quarter_series(q)[-1]
    assert q4["flow"] == {} and "LN thuần" in q4["note"]


# số Naver thật (cắt ngày) — 207940 ngày 20/05/2026, 005950 ngày 20/08/2026
def _n(p, rev, op, np_, ctrl, eps, bps=None):
    return {"period": p, "is_consensus": False, "revenue": rev, "op_profit": op, "net_profit": np_, "ctrl_net": ctrl,
            "eps": eps, "bps": bps, "dps": 0}


SB_A = [_n("202312", 36946, 11137, 8577, 8577, 11950, 136967), _n("202412", 34971, 13214, 10833, 10833, 15094, 151933),
        _n("202512", 45570, 20692, 17844, 17844, 26350, 159796)]
SB_Q = [_n("202506", 10142, 4772, 3244, 3244, 4520), _n("202509", 16602, 7288, 5745, 5745, 8004),
        _n("202512", 3086, 3781, 5100, 5100, 9158), _n("202603", 12571, 5808, 4692, 4692, 10052)]
IS_A = [_n("202312", 19955, -560, -281, -313, -1282, 7144), _n("202412", 19162, -514, -382, -474, -2087, 4734),
        _n("202512", 16595, 69, -405, -311, -1247, 3655)]
IS_Q = [_n("202506", 3399, 39, -47, -109, -451), _n("202509", 4418, 64, 3, 6, 23),
        _n("202512", 4772, -34, -223, -132, -502), _n("202603", 6359, 254, 208, 211, 803)]


def test_run_rate_drops_naver_q4_artifact():
    ytd = F._ytd_run_rate(SB_A, SB_Q)
    assert ytd["dropped"] == ["202512"] and ytd["revenue"] == pytest.approx(4 * 12571)
    px, sh = 1326708.0, 4692e8 / 10052
    row = {"ticker": "207940", "price": px, "market_cap": px * sh, "eps": 31734, "bps": 169913, "dps": 0,
           "industry_kr": "생물공학", "sector": None}
    drv = P.default_drivers(SB_A, row, A, years=3, quarters=SB_Q)
    assert drv[0]["Tăng trưởng DT %"] > 0
    dq = P.data_quality(row, SB_A, SB_Q, beta=1.0, today=dt.date(2026, 5, 20))
    assert _chk(dq, "Quý suy ra bị loại")["Kết quả"] == P.WARN


def test_parent_ratio_k1_from_reported_quarters_only():
    pr = F.parent_ratio(IS_A, F._ytd_run_rate(IS_A, IS_Q))
    assert pr["y1"] == pytest.approx(211 / 208) and pr["reliable"]
    sh = 211e8 / 803
    row = {"ticker": "005950", "price": 15000.0, "market_cap": 15000.0 * sh, "eps": 1000, "bps": 4425, "dps": 0,
           "industry_kr": "화학", "sector": None}
    dq = P.data_quality(row, IS_A, IS_Q, beta=1.0, today=dt.date(2026, 8, 20))
    assert not dq["parent_break"]
    # EPS năm 1 của project_forecast và run_forecast cùng một cơ sở: LN hợp nhất năm 1 × tỷ lệ quý thực
    fc1 = F.project_forecast(IS_A, row["market_cap"], row["price"], quarters=IS_Q)
    shares = F.share_basis(IS_A, IS_Q, row["market_cap"], row["price"])["shares"]
    net1 = F._ytd_run_rate(IS_A, IS_Q)["net_profit"]
    assert fc1["eps"] == pytest.approx(net1 * 211 / 208 * 1e8 / shares, abs=1)      # số đã làm tròn
    drv = P.default_drivers(IS_A, row, A, years=2, quarters=IS_Q)
    drv[0].pop("_basis", None)
    f0 = P.run_forecast(IS_A, row, drv, IS_Q)[0]
    assert f0["LN cổ đông mẹ (억)"] / f0["LN ròng (억)"] == pytest.approx(211 / 208)


def test_parent_break_row_shows_failing_ratio():
    reals = [_n("202512", 1000, 120, 100, 90, 900)]
    q = [_n("202603", 300, 36, 30, -10, -100), _n("202606", 300, 36, 30, -10, -100)]     # hợp nhất lãi, mẹ lỗ
    row = {"price": 10000.0, "market_cap": 1e11, "eps": 900, "bps": 10000, "industry_kr": "식품"}
    c = _chk(P.data_quality(row, reals, q, beta=1.0, today=dt.date(2026, 11, 9)), "LN cổ đông mẹ")
    assert c["Kết quả"] == P.FAIL and not c["Giá trị"].startswith("0.90")


# ============================================================================ I16 — số DART không phải KRW / ngân hàng
EX_USD = {"year": 2025, "currency": "USD", "cfo": 8.22, "capex": 2.0, "debt": 10.0, "net_debt": -4.0, "tax_rate": 0.22,
          "ebit": 5.0}


def test_usd_extras_dropped_no_wacc_no_fcff():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    ctx = P.assess("000001", r, {}, A, PEERS, 1.0, series=s, extras=EX_USD, today=dt.date(2026, 11, 9))
    assert ctx["D"]["extras"] == {}
    assert ctx["E"]["coc"]["wacc"] is None and ctx["E"]["coc"]["debt_eok"] is None
    assert not any(x["name"].startswith("FCFF-DCF (DART") for x in ctx["rec"]["rows"])
    c = _chk(ctx["dq"], "Số DART phụ")
    assert c["Kết quả"] == P.WARN and "USD" in c["Ghi chú"]
    coc = P.cost_of_capital(A, 1.0, 1e12, EX_USD)
    assert coc["wacc"] is None and "USD" in coc["wacc_note"]


def test_bank_gets_no_fcff_fallback_or_wacc():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2, ik="은행")
    ex = {"year": 2025, "currency": "KRW", "cfo": 900.0, "capex": 0.0, "debt": 50000.0, "net_debt": 40000.0,
          "tax_rate": 0.22}
    ctx = P.assess("000001", r, {}, A, PEERS, 1.0, series=s, extras=ex, today=dt.date(2026, 11, 9))
    assert not any(x["name"].startswith("FCFF-DCF (DART") for x in ctx["rec"]["rows"])
    assert ctx["E"]["coc"]["wacc"] is None and "tài chính" in ctx["E"]["coc"]["wacc_note"]
    # doanh nghiệp thường, số KRW cùng năm → vẫn có FCFF dự phòng và WACC như trước
    r2 = _row(s, price=s[-1]["bps"] * 1.2)
    ctx2 = P.assess("000001", r2, {}, A, PEERS, 1.0, series=s, extras=dict(ex, debt=50.0, net_debt=40.0),
                    today=dt.date(2026, 11, 9))
    assert any(x["name"].startswith("FCFF-DCF (DART") for x in ctx2["rec"]["rows"])
    assert ctx2["E"]["coc"]["wacc"] is not None


def test_ev_ebit_of_rejects_foreign_currency_statements():
    fs = {"currency": "KRW", "year": 2025, "fs_div": "CFS", "cur": {"cash": 10.0, "borrowings": 20.0}, "prev": {}}
    assert dcf.ev_ebit_of(1e12, 500.0, fs) is not None
    assert dcf.ev_ebit_of(1e12, 500.0, {**fs, "currency": "USD"}) is None


# ============================================================================ I44 — BCTC đầy đủ năm cũ hơn năm gốc
def test_fs_mismatch_rejects_older_year():
    s = _series()                                                  # tới 2025
    fs25 = {"year": 2025, "currency": "KRW", "cur": {"revenue": s[-1]["revenue"]}}
    assert P.fs_mismatch(fs25, s) is None
    why = P.fs_mismatch({**fs25, "year": 2024, "cur": {"revenue": s[-2]["revenue"]}}, s)
    assert why and "2024" in why and "2025" in why


def test_prepare_drops_old_full_statements_and_extras():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    fs24 = {"year": 2024, "currency": "KRW", "fs_div": "CFS", "cur": {"revenue": s[-2]["revenue"]}, "prev": {}}
    ex24 = {"year": 2024, "currency": "KRW", "cfo": 150.0, "capex": 50.0, "debt": 50.0, "net_debt": 40.0,
            "tax_rate": 0.22}
    ctx = P.assess("000001", r, {}, A, PEERS, 1.0, series=s, fs_full=fs24, extras=ex24, today=dt.date(2026, 11, 9))
    assert ctx["D"]["fs_full"] is None and ctx["D"]["extras"] == {}
    assert not any(x["name"].startswith("FCFF-DCF") for x in ctx["rec"]["rows"])
    assert _chk(ctx["dq"], "BCTC đầy đủ DART")["Kết quả"] == P.WARN


# ============================================================================ I45 — ROE vs EPS/BPS bình quân năm
def _roe_series(bps_prev, bps_now, roe=35.0, eps=35_000.0):
    s = _series(n=2)
    s[0]["bps"], s[1]["bps"], s[1]["roe"], s[1]["eps"] = bps_prev, bps_now, roe, eps
    return s


def test_roe_check_uses_average_annual_book():
    s = _roe_series(100_000, 100_000)
    r = dict(_row(s), bps=145_000)                                 # BPS cuối quý gần nhất (đã tăng mạnh)
    c = _chk(P.data_quality(r, s, Q26, beta=1.0, today=dt.date(2026, 11, 9)), "ROE công bố")
    assert c["Kết quả"] == P.OK and "bình quân" in c["Ghi chú"]
    # Hynix-like: ROE trên VCSH bình quân, BPS tăng 60% trong năm
    s = _roe_series(80_000, 128_000, roe=35.0, eps=36_400.0)
    c = _chk(P.data_quality(dict(_row(s), bps=180_000), s, Q26, beta=1.0, today=dt.date(2026, 11, 9)), "ROE công bố")
    assert c["Kết quả"] == P.OK
    # thiếu BPS năm trước → dùng BPS năm gần nhất
    s = _roe_series(None, 100_000)
    c = _chk(P.data_quality(dict(_row(s), bps=145_000), s, Q26, beta=1.0, today=dt.date(2026, 11, 9)), "ROE công bố")
    assert c["Kết quả"] == P.OK and c["Giá trị"] == "lệch 0.0 điểm %"


# ============================================================================ I49 — doanh thu năm cuối âm
@pytest.mark.parametrize("revs", [[1000, 900, 800, -5], [1000, -5], [0, 900, 1000], [1000, 900, 0]])
def test_negative_or_zero_revenue_never_crashes(revs, monkeypatch):
    monkeypatch.setattr(F, "dart_api", None)
    reals = [{"period": f"{2022 + i}12", "is_consensus": False, "revenue": v, "op_profit": 10.0, "net_profit": 8.0,
              "eps": 80.0, "bps": 1000.0, "dps": 0.0, "equity": 100.0} for i, v in enumerate(revs)]
    fc = F.project_forecast(reals, 1e10, 1000.0)
    if revs[-1] <= 0:
        assert fc is None
    else:
        assert isinstance(fc["revenue"], (int, float)) and not isinstance(fc["revenue"], complex)
    assert F._rev_cagr(revs) is None or isinstance(F._rev_cagr(revs), float)
    F.build_annual_series("000001", reals, 1e10, 1000.0)
    F.forecast_eps(reals, 1e10, 1000.0)
    row = {"ticker": "000001", "price": 1000.0, "market_cap": 1e10, "eps": 80.0, "bps": 1000.0, "industry_kr": "식품"}
    P.assess("000001", row, {"annual": reals, "quarter": []}, A, PEERS, 1.0, today=dt.date(2026, 11, 9))
    P.default_drivers(reals, row, A)
    fh = F.assess_financials(reals)
    g = next(c for c in fh["checks"] if c["name"] == "Doanh thu tăng trưởng")
    assert "j" not in str(g["value"]) and (g["value"] == "—" or g["value"].endswith("%/năm"))


def test_valuation_page_guards_prepare_and_uses_clean_extras():
    src = open(os.path.join(ROOT, "pages", "3_🧮_Dinh_gia.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    tries = [n for n in tree.body if isinstance(n, ast.Try)]
    assert any("P.prepare(" in ast.unparse(t.body[0]) for t in tries)         # bọc try, không hiện traceback
    assert 'extras = CTX["D"]["extras"]' in src and "P.cost_of_capital(A, beta, mc, extras, idn)" in src
    assert "Thiếu DART KRW — nhập tay" in src


# ============================================================================ sửa sau rà soát nhóm G1 (v2)
def test_lagging_dart_report_counts_one_warning():
    # Tháng 1–3: Naver có cột sơ bộ năm mới, BCTC đầy đủ + số DART phụ còn năm cũ → 1 ⚠️ (cùng một nguyên nhân), không
    # tự hạ trần Medium
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    today = dt.date(2026, 11, 18)

    def run(fs_y, ex_y, cur="KRW"):
        fs = {"year": fs_y, "currency": "KRW", "fs_div": "CFS", "cur": {"revenue": s[fs_y - 2026]["revenue"]},
              "prev": {}}
        ex = {"year": ex_y, "currency": cur, "cfo": 150.0, "capex": 50.0, "debt": 50.0, "net_debt": 40.0,
              "tax_rate": 0.22}
        return P.assess("000001", r, {"annual": [], "quarter": Q26}, A, PEERS, 1.0, series=s, fs_full=fs,
                        extras=ex, today=today)
    base = run(2025, 2025)["dq"]
    lag = run(2024, 2024)
    dq = lag["dq"]
    # dòng ⚠️ vẫn hiện nhưng KHÔNG đếm vào trần (chỉ mất mô hình đối chiếu; trang khác không truyền fs_full/extras)
    assert _chk(dq, "BCTC đầy đủ DART")["Kết quả"] == P.WARN
    assert dq["n_warn"] == base["n_warn"] and dq["cap"] == base["cap"], dq["checks"]
    assert not any(c["Kiểm tra"].startswith("Số DART phụ") for c in dq["checks"])
    assert "WACC" in _chk(dq, "BCTC đầy đủ DART")["Ghi chú"] and lag["D"]["extras"] == {}
    # nguyên nhân độc lập (BCTC đầy đủ đúng năm, số DART phụ năm cũ / USD) → dòng ⚠️ riêng
    for ex_y, cur in ((2024, "KRW"), (2025, "USD")):
        dq2 = run(2025, ex_y, cur)["dq"]
        assert _chk(dq2, "Số DART phụ")["Kết quả"] == P.WARN
        assert dq2["n_warn"] == base["n_warn"] and dq2["cap"] == base["cap"]


def test_confidence_same_with_or_without_fs_full_and_extras():
    """Cùng mã, cùng giả định, cùng ngày: 🧮 (fs_full + extras), Sàng lọc (chỉ fs_full), Tổng hợp (không có gì) phải
    ra CÙNG trần dq['cap'] và cùng rec['confidence'] (rà soát cuối 18/11/2026)."""
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    today = dt.date(2026, 11, 18)
    fs = {"year": 2024, "currency": "KRW", "fs_div": "CFS", "cur": {"revenue": s[-2]["revenue"]}, "prev": {}}
    ex = {"year": 2024, "currency": "KRW", "cfo": 150.0, "capex": 50.0, "debt": 50.0, "net_debt": 40.0,
          "tax_rate": 0.22}
    outs = [P.assess("000001", r, {"annual": [], "quarter": Q26}, A, PEERS, 1.0, series=s, today=today, **kw)
            for kw in ({"fs_full": fs, "extras": ex}, {"fs_full": fs}, {"extras": ex}, {})]
    assert len({o["dq"]["cap"] for o in outs}) == 1, [o["dq"]["cap"] for o in outs]
    assert len({o["rec"]["confidence"] for o in outs}) == 1, [o["rec"]["confidence"] for o in outs]
    assert len({round(o["rec"]["fair"] or 0) for o in outs}) == 1


def test_merge_dart_cached_when_errored_year_recovered_from_store(monkeypatch):
    # DART lỗi nhưng mọi năm đọc được từ bản lưu (err_years rỗng), đủ năm của Naver → 'cached' (⚠️), như bản gốc
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2023, 1000, 100), _dt(2024, 1100, 110), _dt(2025, 1200, 120)],
                                                 ERR, []))
    assert F._merge_dart(NAVER3, "X")[-1]["_src"]["dart"] == "cached"

    class _Old(_FakeDart):                    # bản cũ không có err_years → không biết năm lỗi → 'error'; gọi 1 lần
        n = 0

        def annual_financials(self, ticker, years=6, errors=None):
            _Old.n += 1
            errors.extend(self.errs)
            return [dict(r) for r in self.rows]
    monkeypatch.setattr(F, "dart_api", _Old([_dt(2023, 1000, 100), _dt(2024, 1100, 110), _dt(2025, 1200, 120)], ERR))
    assert F._merge_dart(NAVER3, "X")[-1]["_src"]["dart"] == "error" and _Old.n == 1


def test_fetch_financials_no_retry_on_client_error(monkeypatch):
    import requests
    calls = []

    class _R404:
        status_code = 404

        def raise_for_status(self):
            raise requests.exceptions.HTTPError("404", response=self)

    def nf(url, headers=None, timeout=None):
        calls.append(timeout)
        return _R404()
    monkeypatch.setattr(F, "FETCH_RETRY_WAITS", (0, 0))
    monkeypatch.setattr(F.requests, "get", nf)
    out = F.fetch_financials("005380")
    assert len(calls) == 2 and set(out["_error"]) == {"annual", "quarter"}           # 404: không thử lại
    calls.clear()

    def slow(url, headers=None, timeout=None):
        calls.append(timeout)
        raise requests.exceptions.ReadTimeout("t")
    monkeypatch.setattr(F.requests, "get", slow)
    F.fetch_financials("005380")
    assert calls[:3] == [F._TIMEOUT, F.FETCH_RETRY_TIMEOUT, F.FETCH_RETRY_TIMEOUT]   # lần thử lại hết giờ ngắn hơn


def test_tong_hop_fin_skips_prefetched_error_copy():
    src = open(os.path.join(ROOT, "views", "tong_hop.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if getattr(n, "name", "") == "_fin")
    bad = {"annual": [{"period": "202512"}], "quarter": [], "_error": {"quarter": "ReadTimeout"}}
    good = {"annual": [{"period": "202512"}], "quarter": [{"period": "202609"}]}

    class _St:
        session_state = {"screen": {"fin": {"A": bad, "B": good}}}
    ns = {"st": _St, "_fin_cached": lambda tk: {"fresh": tk}}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "tong_hop", "exec"), ns)
    assert ns["_fin"]("A") == {"fresh": "A"} and ns["_fin"]("B") is good
