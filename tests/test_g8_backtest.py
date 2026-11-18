"""G8 — backtest (rà soát 18/11/2026): I28 vốn hóa khi chia tách, I29 gốc số cp theo giá Naver, I30 DART qua
dart_api.get_json, I59 cổ tức lịch sử None + đi qua pipeline.prepare, I60 g_high_mode, I61 t khi lợi suất chồng nhau.
Không mạng: DART/giá/Rf giả lập."""
import dataclasses
import datetime as dt
import json
import logging
import random
import time

import pytest

import backtest_dart as B
import backtest_growth as BG
import backtest_pipeline as BP
import backtest_presets as BPS
import dart_api
import pipeline as P
import screen as SC
import valuation as V
from test_pipeline import _row, _series

TODAY = dt.date(2026, 11, 18)


# ---------------------------------------------------------------- dữ liệu giả
def _year(y, shares, eps_shares=None, ni=1e11, eq=2e12, dps=500.0, full=True):
    """Bản ghi DART năm y: LN mẹ ni, EPS = ni / eps_shares (mặc định = shares), số cp shares."""
    eps_sh = eps_shares or shares
    rec = {"year": y, "v": B.VERSION, "fs_div": "CFS", "revenue": 1e12 * 1.05 ** (y - 2015),
           "op_profit": 1.2e11, "ni": ni, "ni_parent": ni, "equity_parent": eq, "eps": ni / eps_sh}
    if full:
        rec.update(shares_out=shares, dps=dps, shares_checked=True)
    return rec


def _leeno_fs(shares_2025_in_cache=False):
    """Kiểu LEENO: chia tách 1:5 ghi nhận ở báo cáo 2025 (EPS 2025 theo 5× số cp); 2015–2024: 10M cp."""
    fs = {y: _year(y, 1e7) for y in range(2015, 2025)}
    fs[2025] = _year(2025, 5e7 if shares_2025_in_cache else None, eps_shares=5e7, full=shares_2025_in_cache)
    if not shares_2025_in_cache:
        fs[2025].pop("shares_out", None)
    return fs


def _prices(px0=20_000.0):
    d0 = dt.date(2019, 1, 1)
    return [((d0 + dt.timedelta(days=k)).strftime("%Y%m%d"), px0) for k in range(0, 365 * 7 + 30)]


def _patch_market(monkeypatch, fs_by_ticker, full_strips_shares=True):
    def annual(t, y, full=True, cache_dir=None):
        rec = dict(fs_by_ticker[t].get(y) or {"year": y, "status": "013"})
        if not full and full_strips_shares and y not in fs_by_ticker[t].get("_full_cached", ()):
            for k in ("shares_out", "dps", "shares_checked"):
                rec.pop(k, None)
        return rec
    monkeypatch.setattr(B, "annual", annual)
    monkeypatch.setattr(B, "price_history", lambda sym, start="", cache_dir=None: _prices())
    monkeypatch.setattr(B, "fred_rf", lambda cache_dir=None: [("2020-01-01", 0.02)])


# ================================================================ I28 — vốn hóa khi chia tách
def test_i28_build_point_split_market_cap_and_no_share_break():
    fs = {y: _year(y, 1e7, ni=1e11, eq=2e12) for y in range(2016, 2021)}       # gốc cũ 10M cp
    p = B.build_point("000001", 2021, fs, _prices(20_000.0), _prices(2_000.0), [("2021-03-01", 0.02)],
                      None, split_ref_shares=5e7)                              # gốc giá Naver: 50M cp (tách 1:5)
    assert p["split_adj"] == 5.0
    row = p["row"]
    assert row["market_cap"] / row["price"] == pytest.approx(5e7)             # cũ: 10M / 5 = 2M cp
    dq = P.data_quality(row, [dict(h, _dart=True) for h in p["hist"]], [], 1.0, today=dt.date(2021, 4, 15))
    chk = next(c for c in dq["checks"] if c["Kiểm tra"].startswith("Số cổ phiếu"))
    assert not dq["share_break"] and chk["Kết quả"] == P.OK and dq["cap"] != "Low"     # cũ: ❌, trần Low


def test_i28_split_point_passes_min_mcap_in_presets():
    fs = {y: _year(y, 1e7, ni=1e11, eq=2e12) for y in range(2016, 2021)}
    p = B.build_point("000001", 2021, fs, _prices(20_000.0), _prices(2_000.0), [("2021-03-01", 0.02)],
                      None, split_ref_shares=5e7)
    row = BPS._row_at(p)
    assert row["market_cap"] == pytest.approx(1e12)                           # 20.000 × 50M; cũ 4e10 < 5e11
    crit = next(v["stage1"] for v in SC.PRESETS.values() if (v.get("stage1") or {}).get("min_mcap") == 5e11)
    assert (row["market_cap"] or 0) >= crit["min_mcap"]


# ================================================================ I29 — gốc số cp của giá Naver
def test_i29_ref_shares_from_last_fy_and_eps_cross_check():
    ref, suspect = B._ref_shares(_leeno_fs(False), 2025)       # 2025 chưa có số cp → 2024 (10M) × hệ số EPS 5
    assert ref == pytest.approx(5e7) and not suspect
    ref2, _ = B._ref_shares(_leeno_fs(True), 2025)             # 2025 có số cp (50M) → khớp, không nhân thêm
    assert ref2 == pytest.approx(5e7)
    fs = {y: _year(y, 1e7) for y in range(2015, 2024)}
    fs[2024] = _year(2024, 1e7, eps_shares=1.7e7)
    fs[2025] = _year(2025, 1e7, eps_shares=1.8e7)              # 2 báo cáo mới nhất đều lệch không tròn
    assert B._ref_shares(fs, 2025) == (None, True)


def test_i29_newest_eps_noise_falls_back_to_previous_report():
    """Kiểu 003670: LN 2025 nhỏ, EPS trừ lãi trái phiếu vĩnh cửu → LN ÷ EPS ≈ 8,2× số cp (nhiễu); 2024 khớp → giữ."""
    fs = {y: _year(y, 7.74e7) for y in range(2015, 2025)}
    fs[2025] = _year(2025, 7.74e7, eps_shares=6.33e8, ni=3.2e10)
    assert B._ref_shares(fs, 2025) == (pytest.approx(7.74e7), False)


@pytest.mark.parametrize("y1, cached", [(2025, False), (2022, False), (2025, True), (2022, True)])
def test_i29_split_adj_same_whatever_window_and_cache(monkeypatch, y1, cached):
    fs = _leeno_fs(cached)
    _patch_market(monkeypatch, {"058470": fs})
    df, res = B.run_backtest_dart(["058470"], years=(2021, y1), today=TODAY)
    got = dict(zip(df["year"], df["split_adj"]))
    assert got[2021] == 5.0 and got[2022] == 5.0               # cũ: 1 (EPS gốc cũ so với giá gốc mới)
    assert not res["meta"]["basis_suspect"]


def test_i29_basis_suspect_ticker_dropped(monkeypatch):
    fs = {y: _year(y, 1e7) for y in range(2015, 2026)}
    fs[2024] = _year(2024, 1e7, eps_shares=1.7e7)
    fs[2025] = _year(2025, 1e7, eps_shares=1.8e7)
    _patch_market(monkeypatch, {"000002": fs})
    df, res = B.run_backtest_dart(["000002"], years=(2021, 2023), today=TODAY)
    assert len(df) == 0 and res["meta"]["basis_suspect"] == ["000002"]


# ================================================================ I30 — DART qua dart_api.get_json
class _Resp:
    def __init__(self, j):
        self._j = j

    def json(self):
        if isinstance(self._j, Exception):
            raise self._j
        return self._j


@pytest.fixture
def fake_dart(monkeypatch):
    """requests.get giả cho DART: answers[(endpoint, fs_div)] = dict | Exception. Đếm HTTP và nhịp dart_api."""
    calls, throttled = [], []
    answers: dict = {}

    def get(url, params=None, timeout=None):
        ep = url.rsplit("/", 1)[-1]
        calls.append((ep, (params or {}).get("fs_div")))
        a = answers.get((ep, (params or {}).get("fs_div")), answers.get((ep, None), {"status": "013"}))
        if isinstance(a, Exception):
            raise a
        return _Resp(a)
    real_throttle = dart_api._throttle

    def throttle():
        throttled.append(1)
        return real_throttle()
    monkeypatch.setattr(dart_api.requests, "get", get)
    monkeypatch.setattr(dart_api, "_throttle", throttle)
    monkeypatch.setattr(dart_api, "MIN_GAP", 0.0)
    monkeypatch.setattr(dart_api, "RETRY_WAITS", (0.0, 0.0))
    monkeypatch.setattr(dart_api, "api_key", lambda: "test-key")
    monkeypatch.setattr(dart_api, "corp_code_of", lambda t: "00000001")
    return calls, throttled, answers


FS_OK = {"status": "000", "list": [{"sj_div": "IS", "account_id": "ifrs-full_Revenue", "thstrm_amount": "1000"}]}


def _cache_file(tmp_path, t="000001", y=2019):
    return tmp_path / "dart_fs" / f"{t}_{y}.json"


@pytest.mark.parametrize("cfs", [{"status": "800"}, {"status": "020"}, {"status": "012"},
                                 ConnectionError("reset")])
def test_i30_error_status_not_cached_and_no_ofs_fallback(fake_dart, tmp_path, cfs):
    calls, _, answers = fake_dart
    answers[("fnlttSinglAcntAll.json", "CFS")] = cfs
    answers[("fnlttSinglAcntAll.json", "OFS")] = FS_OK
    rec = B.annual("000001", 2019, full=False, cache_dir=str(tmp_path))
    assert rec["status"] == "ERR" and not rec.get("fs_div")
    assert not _cache_file(tmp_path).exists()                                 # cũ: lưu vĩnh viễn
    assert all(fs == "CFS" for _, fs in calls)                                # lỗi ≠ 'không có' → không lùi OFS


def test_i30_cfs_error_then_ofs_013_not_cached(fake_dart, tmp_path):
    calls, _, answers = fake_dart
    answers[("fnlttSinglAcntAll.json", "CFS")] = ConnectionError("x")
    answers[("fnlttSinglAcntAll.json", "OFS")] = {"status": "013"}
    rec = B.annual("000001", 2019, full=False, cache_dir=str(tmp_path))
    assert rec["status"] == "ERR" and not _cache_file(tmp_path).exists()


def test_i30_both_013_cached_as_no_report(fake_dart, tmp_path):
    calls, _, answers = fake_dart
    rec = B.annual("000001", 2019, full=False, cache_dir=str(tmp_path))
    assert rec.get("fs_div") is None and rec["status"] == "013"
    saved = json.loads(_cache_file(tmp_path).read_text())
    assert saved["status"] == "013" and saved.get("fs_div") is None
    n = len(calls)
    B.annual("000001", 2019, full=False, cache_dir=str(tmp_path))
    assert len(calls) == n                                                    # 'không có' đã xác nhận → đọc cache


def test_i30_ofs_used_only_after_cfs_013(fake_dart, tmp_path):
    calls, _, answers = fake_dart
    answers[("fnlttSinglAcntAll.json", "OFS")] = FS_OK
    rec = B.annual("000001", 2019, full=False, cache_dir=str(tmp_path))
    assert rec["fs_div"] == "OFS" and _cache_file(tmp_path).exists()


def test_i30_share_error_not_marked_checked_and_refetched(fake_dart, tmp_path):
    calls, _, answers = fake_dart
    answers[("fnlttSinglAcntAll.json", "CFS")] = FS_OK
    answers[("stockTotqySttus.json", None)] = ConnectionError("x")
    answers[("alotMatter.json", None)] = {"status": "013"}
    rec = B.annual("000001", 2019, full=True, cache_dir=str(tmp_path))
    assert not rec.get("shares_checked") and rec.get("shares_err")
    saved = json.loads(_cache_file(tmp_path).read_text())
    assert not saved.get("shares_checked") and "shares_err" not in saved      # cũ: shares_checked=True, None mãi
    answers[("stockTotqySttus.json", None)] = {"status": "000", "list": [{"se": "합계", "istc_totqy": "100",
                                                                            "distb_stock_co": "90"}]}
    rec2 = B.annual("000001", 2019, full=True, cache_dir=str(tmp_path))      # lần sau lấy lại số cp
    assert rec2["shares_checked"] and rec2["shares_out"] == 90.0
    assert json.loads(_cache_file(tmp_path).read_text())["shares_checked"]


def test_i30_breaker_open_makes_no_http(fake_dart, tmp_path, monkeypatch):
    calls, _, _ = fake_dart
    monkeypatch.setitem(dart_api.STATS, "open_until", time.time() + 60)
    rec = B.annual("000001", 2019, full=True, cache_dir=str(tmp_path))
    assert rec["status"] == "ERR" and calls == [] and not _cache_file(tmp_path).exists()


def test_i30_every_call_through_dart_api_gate(fake_dart, tmp_path):
    calls, throttled, answers = fake_dart
    answers[("fnlttSinglAcntAll.json", "CFS")] = FS_OK
    before = dart_api.STATS["ok"] + dart_api.STATS["nodata"]
    B.annual("000001", 2019, full=True, cache_dir=str(tmp_path))
    assert len(calls) == 3 and len(throttled) == len(calls)                   # nhịp chung của dart_api
    assert dart_api.STATS["ok"] + dart_api.STATS["nodata"] - before == 3      # STATS dùng chung
    assert not hasattr(B, "MIN_GAP") and not hasattr(B, "_LOCK")


def test_i30_errors_reported_in_meta_and_missing_shares_logged(monkeypatch, caplog):
    fs = {y: _year(y, 1e7) for y in range(2015, 2026)}
    fs[2021] = {"year": 2021, "status": "ERR", "message": "DART báo mã 020: vượt hạn mức"}
    fs[2022] = dict(_year(2022, 1e7), shares_err="lỗi kết nối DART")
    for k in ("shares_out", "shares_checked"):
        fs[2022].pop(k, None)
    _patch_market(monkeypatch, {"000003": fs}, full_strips_shares=False)
    with caplog.at_level(logging.INFO, logger="backtest_dart"):
        df, res = B.run_backtest_dart(["000003"], years=(2021, 2024), today=TODAY)
    errs = {(e["ticker"], e["year"]) for e in res["meta"]["dart_errors"]}
    assert ("000003", 2021) in errs and ("000003", 2022) in errs
    assert any("thiếu số cổ phiếu" in r.getMessage() for r in caplog.records)


# ================================================================ I59 — cổ tức lịch sử, đi qua prepare()
def test_i59_hist_row_dps_unknown_is_none():
    base = {"year": 2020, "fs_div": "CFS", "eps": 100.0, "equity_parent": 1e9, "shares_out": 1e6}
    assert B._hist_row(base, 1.0)["dps"] is None                              # năm chỉ-lịch-sử: không biết
    assert B._hist_row(dict(base, shares_checked=True), 1.0)["dps"] == 0.0    # đã kiểm, không trả cổ tức
    assert B._hist_row(dict(base, dps=500.0, shares_checked=True), 5.0)["dps"] == 100.0


def _point(year=2026, mult=1.0, ret=0.1, ticker="000001", margin=0.10, eq0=5000.0):
    s = _series(margin=margin, eq0=eq0)
    r = _row(s, price=s[-1]["bps"] * mult)
    r.update(ticker=ticker, per=r["price"] / r["eps"], pbr=r["price"] / r["bps"])
    weekly = [100 * (1.01 ** i) + (i % 3) for i in range(80)]
    return {"year": year, "date": f"{year}0415", "row": r, "hist": s, "stock_weekly": weekly,
            "kospi_weekly": [100 * (1.01 ** i) for i in range(80)], "rf": 0.03, "fwd_date": f"{year + 1}0415",
            "ret": ret, "kospi_ret": 0.0, "split_adj": 1.0}


def test_i59_pipeline_points_matches_assess():
    pts = [_point(mult=m, ticker=f"00000{i}") for i, m in enumerate((0.7, 1.0, 1.3))]
    out = {r["ticker"]: r for r in BP.pipeline_points(pts, with_fs=False)}
    rows = [p["row"] for p in pts]
    a = dataclasses.replace(V.Assumptions(), rf=0.03)
    for p in pts:
        row = p["row"]
        beta = BP.beta_vs_market(p["stock_weekly"], p["kospi_weekly"])
        ctx = P.assess(row["ticker"], row, None, a, rows, beta, series=[dict(h, _dart=True) for h in p["hist"]],
                       years=5, today=dt.date(2026, 4, 15))
        r = out[row["ticker"]]
        assert r["fair"] == ctx["rec"]["fair"] and r["dq_cap"] == ctx["dq"]["cap"]
        assert r["confidence"] == (ctx["rec"]["confidence"] if ctx["rec"]["fair"] is not None else "—")


def test_i59_usd_fs_full_blocked_via_prepare(monkeypatch):
    monkeypatch.setattr(BP.AN, "fetch_full", lambda tk, y: {"currency": "USD", "year": 2025,
                                                             "cur": {"revenue": 1.0}})
    out = BP.pipeline_points([_point()])
    assert out and "USD" in (out[0]["fs_note"] or "") and out[0]["fcff_upside"] is None


def test_i59_hist_years_in_other_unit_dropped():
    p = _point()
    usd = [dict(h, eps=h["eps"] / 1300, bps=h["bps"] / 1300) for h in p["hist"]]
    p_last_usd = dict(p, hist=p["hist"][:-1] + usd[-1:])
    assert BP.pipeline_points([p_last_usd], with_fs=False) == []              # năm Y−1 khác đơn vị → bỏ điểm
    assert not BP._unit_ok(usd[0], p["row"]["price"]) and BP._unit_ok(p["hist"][0], p["row"]["price"])
    assert not BP._unit_ok(dict(p["hist"][0], currency="USD"), p["row"]["price"])


def test_i59_high_pb_krw_years_kept():
    """P/B cao thật (Alteogen ~100–150×, năm cũ BPS nhỏ) vẫn giữ; chỉ năm lệch BPS năm Y−1 > 300 lần bị bỏ."""
    p = _point()
    price = p["row"]["price"]
    hist = [dict(h, bps=price / 150) for h in p["hist"][:-1]] + p["hist"][-1:]
    assert BP._unit_hist(hist) == hist                                        # cũ: bỏ năm giá / BPS > 100
    usd_old = [dict(hist[0], bps=hist[-1]["bps"] / 1300)] + hist[1:]
    assert BP._unit_hist(usd_old) == hist[1:]
    assert BP._unit_ok(dict(hist[-1], bps=price / 150), price)                # năm Y−1 P/B 150 vẫn tính
    out = BP.pipeline_points([dict(p, hist=hist)], with_fs=False)
    assert len(out) == 1


def test_i59_dart_rows_without_eps_not_capped_low():
    """Điểm kiểu build_point: DART không có dòng EPS mọi năm, LN mẹ có đủ → không có ❌ 'BCTC Naver' / trần Low."""
    fs = {y: _year(y, 1e7, ni=1e11, eq=2e12) for y in range(2015, 2021)}
    for r in fs.values():
        r["eps"] = None
    wig = [(d, v * (1 + 0.03 * ((i * 7) % 5 - 2) / 2)) for i, (d, v) in enumerate(_prices(2_000.0))]
    px = [(d, v * 10 * (1 + 0.01 * (i % 3))) for i, (d, v) in enumerate(wig)]         # giá tuần dao động → có beta
    p = B.build_point("000001", 2021, fs, px, wig, [("2021-03-01", 0.02)], None, split_ref_shares=1e7)
    assert p and all(h["eps"] is None and h["net_profit"] for h in p["hist"])
    out = BP.pipeline_points([p], with_fs=False)
    assert out and out[0]["dq_cap"] != "Low"
    beta = BP.beta_vs_market(p["stock_weekly"], p["kospi_weekly"])
    hist = [dict(h, _dart=True, ctrl_net=h["net_profit"]) for h in p["hist"]]
    ctx = P.prepare("000001", dict(p["row"]), None, V.Assumptions(), [p["row"]], beta, series=hist,
                    years=5, today=dt.date(2021, 4, 15))
    assert not any("BCTC Naver" in str(c.get("Kiểm tra")) for c in ctx["dq"]["checks"])


# ================================================================ I60 — g_high thật sự được dùng
def test_i60_g_high_fn_changes_result():
    pts = [_point(mult=m, ticker=f"00000{i}", margin=0.15, eq0=1000.0)
           for i, m in enumerate((1.5, 2.0, 2.5, 3.0))]                       # ROE > Ke: tăng trưởng có giá trị
    lo = BP.pipeline_points(pts, with_fs=False, g_high_fn=lambda r, h, d: 0.0)
    hi = BP.pipeline_points(pts, with_fs=False, g_high_fn=lambda r, h, d: 0.15)
    assert [r["g_high"] for r in lo] == [0.0] * len(lo) and all(r["g_high"] == 0.15 for r in hi)
    assert any(a["base_upside"] != b["base_upside"] for a, b in zip(lo, hi))  # cũ: mode sustainable bỏ qua g_high


def test_i60_growth_variants_and_actual_g():
    assert BG.VARIANTS["current"] is None and BG.VARIANTS["fixed8"](None, [], []) == 0.08
    pts = [_point(mult=m, ticker=f"00000{i}") for i, m in enumerate((0.8, 1.2))]
    cur = BP.pipeline_points(pts, with_fs=False)                              # như App: sustainable
    r0 = pts[0]["row"]
    assert cur[0]["g_high"] == pytest.approx(V.sustainable_growth(r0["eps"], r0["bps"], r0["dps"]))
    assert cur[0]["g_high"] != 0.08


# ================================================================ I61 — t khi giữ > 12 tháng
def _ar1(n, rho, rng):
    x, out = 0.0, []
    for _ in range(n):
        x = rho * x + rng.gauss(0, 0.05)
        out.append(0.02 + x)
    return out


def test_i61_t_stat_unchanged_at_12m_and_corrected_beyond():
    ics = [0.10, 0.12, 0.08, 0.11, 0.09, 0.13]
    t12, note12 = B.ic_t_stat(ics, 12)
    import statistics as st
    assert t12 == pytest.approx(st.mean(ics) / (st.stdev(ics) / len(ics) ** 0.5)) and note12 is None
    rng = random.Random(7)
    for _ in range(20):
        xs = _ar1(8, 0.8, rng)
        t_plain, _ = B.ic_t_stat(xs, 12)
        t24, note = B.ic_t_stat(xs, 24)
        assert note and (t24 is None or abs(t24) <= abs(t_plain) + 1e-12)
    xs = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12]                    # IC bền theo năm → tự tương quan dương
    assert abs(B.ic_t_stat(xs, 24)[0]) < abs(B.ic_t_stat(xs, 12)[0])


def test_i61_evaluate_and_report_note():
    vals = [{"year": y, "upside": (i + (y % 3)) / 10, "excess": i / 100 + 0.001 * y, "ret": i / 100,
             "method_upsides": {}} for y in range(2018, 2024) for i in range(10)]
    e12, e24 = B.evaluate(vals), B.evaluate(vals, horizon_months=24)
    assert e12["t_note"] is None and e24["t_note"]
    assert e24["ic_by_year"] == e12["ic_by_year"] and e24["mean_ic"] == e12["mean_ic"]
    res = {"meta": {"points_with_return": 60, "tickers": 10, "years": [2018, 2023], "horizon_months": 24},
           "auto": e24, "forecast": {"kci_beats_naive": {}}}
    assert "Newey" in B.report_markdown(res)
    pipe = [dict(v, ticker=str(i)) for i, v in enumerate(vals)]
    assert BP._ic(pipe, "upside", 24)["t_note"] and BP._ic(pipe, "upside")["t_note"] is None


def test_i61_run_backtest_passes_horizon(monkeypatch):
    fss = {f"{i:06d}": {y: _year(y, 1e7, ni=1e11 * (1 + 0.1 * i)) for y in range(2015, 2026)} for i in range(1, 9)}
    _patch_market(monkeypatch, fss, full_strips_shares=False)
    _, res = B.run_backtest_dart(list(fss), years=(2021, 2023), horizon_months=24, today=TODAY)
    assert res["meta"]["horizon_months"] == 24 and res["meta"]["t_note"] == B.T_NOTE_OVERLAP
    assert res["auto"]["t_note"] == B.T_NOTE_OVERLAP
