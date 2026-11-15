"""Dự phóng, beta, chuẩn hóa tên tài khoản DART (không cần mạng)."""
import pytest

import dart_api as D
import financials as F
import screener as S


# ---- financials: dự phóng ----
def test_trend_value_monotonic_weights_recent_years():
    assert F._trend_value([0.01, 0.05, 0.10]) == pytest.approx((0.01 + 0.10 + 0.30) / 6)


def test_trend_value_noisy_uses_median():
    assert F._trend_value([0.05, 0.01, 0.09]) == pytest.approx(0.05)


def test_shares_estimate_ignores_negative_eps():
    assert F._shares_estimate([{"net_profit": 6, "eps": -11}], None, None) is None
    assert F._shares_estimate([{"net_profit": 12, "eps": 6}], None, None) == pytest.approx(2e8)


def test_forecast_recovering_company_not_dragged_to_median():
    # Samsung Heavy: lỗ → hồi phục. Median cũ cho LN dự phóng ~668억 (vô lý)
    base = {"is_consensus": False, "dps": 0, "bps": 5_285}
    reals = [dict(base, period="202312", revenue=80_094, net_profit=-1_556, op_profit=-1_000),
             dict(base, period="202412", revenue=99_031, net_profit=539, op_profit=600),
             dict(base, period="202512", revenue=106_500, net_profit=5_358, op_profit=6_000,
                  eps=631)]
    fc = F.project_forecast(reals)
    assert fc["is_forecast"] and fc["period"] == "202612"
    assert fc["net_profit"] > 2_000


def test_quarter_forecast_is_seasonal_yoy():
    q = [{"period": p, "is_consensus": False, "revenue": r, "net_profit": 10, "op_profit": 20,
          "eps": 5, "bps": 13_000}
         for p, r in [("202503", 6000), ("202506", 6300), ("202509", 6755),
                      ("202512", 6405), ("202603", 6374)]]
    fc = F.project_forecast_quarter(q)
    assert fc["period"] == "202606"
    assert fc["revenue"] == round(6300 * 6374 / 6000)     # cùng quý năm trước × YoY


# ---- screener: beta ----
def _closes(returns, start=100.0):
    out = [start]
    for r in returns:
        out.append(out[-1] * (1 + r))
    return out


MKT = [0.02 * ((-1) ** i) + 0.003 * (i % 5) for i in range(60)]


def test_beta_of_market_is_one():
    c = _closes(MKT)
    assert S.beta_vs_market(c, c) == pytest.approx(1.0)


def test_beta_is_clamped():
    assert S.beta_vs_market(_closes([3 * r for r in MKT]), _closes(MKT)) == pytest.approx(1.8)


def test_beta_needs_enough_weeks():
    assert S.beta_vs_market([1, 2, 3], [1, 2, 3]) is None


# ---- dart_api: tên tài khoản không thống nhất giữa các năm ----
def test_account_names_normalized():
    assert D._norm("자산 총계") == D._norm("자산총계")
    assert D._norm("부채 총계") in D._ACCOUNTS_NORM["liabilities"]
    assert D._norm("자본 및 부채 총계") not in D._ACCOUNTS_NORM["liabilities"]


# ---- dự phóng năm: dùng quý THỰC đã công bố của năm dự phóng ----
_BASE = {"is_consensus": False, "dps": 0, "bps": 10_000}
_REALS = [dict(_BASE, period="202312", revenue=1000, op_profit=100, net_profit=80, eps=800),
          dict(_BASE, period="202412", revenue=1100, op_profit=110, net_profit=88, eps=880),
          dict(_BASE, period="202512", revenue=1200, op_profit=120, net_profit=96, eps=960)]
_QTRS = [{"period": p, "is_consensus": c, "revenue": r, "op_profit": o, "net_profit": n}
         for p, c, r, o, n in [("202509", False, 300, 30, 24), ("202512", False, 320, 32, 26),
                               ("202603", False, 600, 90, 70), ("202606", False, 800, 140, 110),
                               ("202609", True, 900, 150, 120)]]


def test_forecast_uses_reported_quarters_of_forecast_year():
    fc = F.project_forecast(_REALS, quarters=_QTRS)
    # 2 quý thực 2026 (600 + 800) + 2 quý còn lại × TB 2 quý gần nhất (700); bỏ quý consensus
    assert fc["period"] == "202612" and fc["revenue"] == 2800
    assert fc["net_profit"] == 70 + 110 + 2 * 90
    assert fc["eps"] == round(360e8 / (96e8 / 960))
    assert "2 quý thực" in fc["_basis"]



def test_forecast_eps_uses_controlling_profit_when_reported():
    # tập đoàn có cổ đông thiểu số lớn: LN hợp nhất dương nhưng LN công ty mẹ âm
    reals = [dict(c, ctrl_net=c["net_profit"] / 2) for c in _REALS]   # cp = 48e8/960 = 5e6
    q = [dict(c, ctrl_net=-c["net_profit"]) for c in _QTRS]
    fc = F.project_forecast(reals, quarters=q)
    assert fc["net_profit"] == 360                       # LN hợp nhất vẫn giữ để hiển thị
    assert fc["eps"] == round(-360e8 / 5e6)              # EPS theo LN công ty mẹ

def test_forecast_without_quarters_keeps_annual_formula():
    # lab (KCI-Valuation-Lab) gọi không truyền quý → giữ nguyên công thức CAGR cũ
    assert F.project_forecast(_REALS)["revenue"] == round(1200 * 1.2 ** 0.5)
    non_dec = [dict(c, period=c["period"][:4] + "03") for c in _REALS]
    assert F.project_forecast(non_dec, quarters=_QTRS)["revenue"] == round(1200 * 1.2 ** 0.5)


def test_consensus_eps_same_year_only():
    annual = _REALS + [{"period": "202612", "is_consensus": True, "eps": 1500}]
    assert F.consensus_eps(annual, "202612") == 1500
    assert F.consensus_eps(annual, "202712") is None


# ---- screener: nhận diện mã ----
@pytest.fixture(scope="module")
def reg():
    r = S.TickerRegistry()
    r._krx_loaded = True            # không gọi pykrx/KRX trong test offline
    return r


def test_resolve_short_name_prefers_bluechip(reg):
    assert reg.resolve("삼성") == "005930"          # trước đây ra 삼성제약
    assert reg.resolve("Samsung") == "005930"       # trước đây ra Samsung Fire & Marine
    assert reg.resolve("KB") == "105560"            # trước đây ra KBI동양철관
    info = reg.resolve_info("삼성")
    assert info["how"] == "contains" and info["alts"]


def test_resolve_alphanumeric_ticker(reg):
    assert reg.resolve("0001a0") == "0001A0"


def test_preferred_share_never_maps_to_common(reg, monkeypatch):
    monkeypatch.setattr(S, "_naver_name", lambda code: None)
    assert reg.resolve("삼성전자우") is None         # trước đây lặng lẽ ra 005930 (cp thường)
    monkeypatch.setattr(S, "_naver_name",
                        lambda code: "삼성전자우" if code == "005935" else "x")
    info = reg.resolve_info("삼성전자우")
    assert (info["ticker"], info["how"]) == ("005935", "preferred")


def test_fetch_many_dedupes_and_overlays_live_price(reg, monkeypatch):
    calls = []

    def fake(tk, registry=None, prefer="naver", live=True):
        calls.append((tk, live))
        return S.StockRow(ticker=tk, price=100.0, source="naver")
    monkeypatch.setattr(S, "fetch_one", fake)
    monkeypatch.setattr(S, "fetch_live_prices", lambda tks: {"005930": 123.0})
    res = []
    rows, un = S.fetch_many(["005930", "삼성전자", "000660", "zzzz_khong_co"],
                            registry=reg, resolutions=res)
    assert sorted(calls) == [("000660", False), ("005930", False)]   # mã trùng cào 1 lần
    assert [r["ticker"] for r in rows] == ["005930", "000660"]
    assert (rows[0]["price"], rows[0]["source"]) == (123.0, "naver+rt")
    assert (rows[1]["price"], rows[1]["source"]) == (100.0, "naver")
    assert un == ["zzzz_khong_co"] and res[1].get("dup")


def test_fetch_live_prices_batches_50(monkeypatch):
    urls = []

    class _R:
        def __init__(self, codes):
            self.codes = codes

        def json(self):
            return {"datas": [{"itemCode": c, "closePrice": "1,234"} for c in self.codes]
                    + [{"itemCode": "HALT", "closePrice": "0"}]}

    def fake_get(url, **kw):
        urls.append(url)
        return _R(url.rsplit("/", 1)[1].split(","))
    monkeypatch.setattr("requests.get", fake_get)
    out = S.fetch_live_prices([f"{i:06d}" for i in range(120)])
    assert len(urls) == 3 and len(out) == 120 and out["000007"] == 1234.0


def test_sector_aggregates_groups_by_krx_industry_when_no_sector():
    import pandas as pd
    base = {"price": 100.0, "market_cap": 1e12, "net_income": 1e11, "pbr": 1.0,
            "bps": 100.0, "div_yield": 2.0}
    df = pd.DataFrame([dict(base, ticker="A", sector="Bank", industry_kr="은행", per=10.0),
                       dict(base, ticker="B", sector=None, industry_kr="조선", per=8.0),
                       dict(base, ticker="C", sector=None, industry_kr="조선", per=-5.0),
                       dict(base, ticker="D", sector=None, industry_kr="화학", per=12.0)])
    agg = S.sector_aggregates(df).set_index("sector")
    assert set(agg.index) == {"Bank", "조선", "화학"}      # trước đây B/C/D gộp chung 1 nhóm NaN
    assert agg.loc["조선", "median_P/E"] == 8.0           # median bỏ P/E âm


def test_dart_year_cache_skips_repeat_requests(monkeypatch, tmp_path):
    calls = []

    def raw(cc, y):
        calls.append((cc, y))
        return {"period": f"{y}12", "revenue": 1.0} if y < 2026 else None
    monkeypatch.setattr(D, "_fetch_year_raw", raw)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    monkeypatch.setattr(D, "YEAR_CACHE_DIR", str(tmp_path))      # không ghi vào .cache thật của dự án
    for _ in range(3):
        D._fetch_year("X", 2025)
        D._fetch_year("X", 2026)                    # chưa nộp → không nhớ, lần sau hỏi lại
    assert calls.count(("X", 2025)) == 1 and calls.count(("X", 2026)) == 3
    D._fetch_year("X", 2025)["revenue"] = 99        # sửa bản trả về không làm bẩn cache
    assert D._fetch_year("X", 2025)["revenue"] == 1.0
    # báo cáo năm đã nộp còn được nhớ trên đĩa: tiến trình mới (bộ nhớ trống) không gọi lại DART
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    assert D._fetch_year("X", 2025)["revenue"] == 1.0 and calls.count(("X", 2025)) == 1


# ---------------------------------------------------------------- lãi/lỗ bất thường một lần (10/11/2026)
def _yr(y, rev, op, np_):
    return {"period": f"{y}12", "is_consensus": False, "revenue": rev, "op_profit": op, "net_profit": np_,
            "ctrl_net": np_, "eps": np_, "bps": 1000, "dps": 0}


def _q(p, rev, op, np_):
    return {"period": p, "is_consensus": False, "revenue": rev, "op_profit": op, "net_profit": np_, "ctrl_net": np_}


def test_conversion_ratio_uses_own_history():
    import financials as F
    reals = [_yr(2023, 100, 10, 12), _yr(2024, 100, 10, 12), _yr(2025, 100, 10, 12)]   # lãi liên kết đều
    assert F.conversion_ratio(reals) == pytest.approx(1.2)
    assert F.conversion_ratio(reals[:2]) == F.DEFAULT_CONV        # < 3 năm: không tin tỷ lệ riêng


def test_one_off_not_flagged_when_structural():
    """Kiểu Hyundai Mobis: LN thuần ~1,2× LN HĐ đều đặn → không phải bất thường."""
    import financials as F
    assert F.one_off_core(_q("202606", 100, 10, 12.5), 1.2) is None


def test_one_off_flagged_when_far_from_own_ratio_or_above_revenue():
    import financials as F
    assert F.one_off_core(_q("202606", 79, 60, 94), 0.84) == pytest.approx(60 * 0.84)   # LN > DT (Hynix)
    assert F.one_off_core(_q("202512", 100, 30, 115), 1.36) == pytest.approx(30 * 1.36)  # 3,8× vs 1,36
    assert F.one_off_core(_q("202512", 100, 2, 5), 0.8) is None     # lệch tỷ lệ nhưng nhỏ so với doanh thu


def test_run_rate_does_not_extrapolate_one_off():
    import financials as F
    reals = [_yr(2023, 400, 40, 32), _yr(2024, 400, 40, 32), _yr(2025, 400, 40, 32)]
    qs = [_q("202603", 100, 10, 8), _q("202606", 100, 10, 60)]            # Q2: lãi một lần +52
    out = F._ytd_run_rate(reals, qs)
    # số công bố giữ nguyên (8 + 60), 2 quý còn lại = TB(8, lõi 8) = 8, KHÔNG phải TB(8, 60) = 34
    assert out["net_profit"] == pytest.approx(8 + 60 + 2 * 8)
    assert out["net_profit_core"] == pytest.approx(8 + 8 + 2 * 8)
    assert [o["period"] for o in out["one_offs"]] == ["202606"]


def test_normalize_code_common_formats():
    """Mã viết kiểu HTS/DART (A005950), Yahoo (.KS/.KQ), TradingView (KRX:) đều nhận ra; tên thì không đổi."""
    from screener import normalize_code
    for q in ("A005950", "a005950", "A 005950", "005950.KS", "005950.kq", "KRX:005950", "KOSPI: 005950", "005950"):
        assert normalize_code(q) == "005950", q
    assert normalize_code("A0001A0") == "0001A0"
    for q in ("Samsung", "삼성전자", "ABC", "A1234567", "", "Apple"):
        assert normalize_code(q) is None, q


# ================================================================ rà soát 15/11/2026 — đợt 1
# ---- số cổ phiếu hiện hành + EPS cổ đông mẹ ----
def _y(period, np_, ctrl, eps, rev=1000.0):
    return {"period": period, "is_consensus": False, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "ctrl_net": ctrl, "eps": eps, "bps": 100_000, "dps": 0}


def _qq(period, np_, ctrl, eps, rev=300.0):
    return {"period": period, "is_consensus": False, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "ctrl_net": ctrl, "eps": eps}


def test_share_basis_uses_latest_quarter_not_last_year():
    # tách công ty cuối năm: EPS năm tính trên 67,7 triệu cp bình quân, quý mới nhất trên 46,3 triệu cp
    reals = [_y("202412", 10_000, 10_000, 14_771), _y("202512", 12_000, 12_000, 17_725)]     # 12.000억 ÷ 17.725 = 67,7 triệu
    q = [_qq("202603", 4_000, 4_000, 8_639), _qq("202606", 4_500, 4_500, 9_719)]              # ÷ → 46,3 triệu
    sb = F.share_basis(reals, q, market_cap=46.3e6 * 1_400_000, price=1_400_000)
    assert sb["shares"] == pytest.approx(46.3e6, rel=0.001) and sb["source"] == "quý 2026.06"
    assert sb["prev"] == pytest.approx(67.7e6, rel=0.001) and sb["listed"] == pytest.approx(46.3e6)
    # không có quý dùng được → cơ sở của năm gần nhất
    assert F.share_basis(reals, [], None, None)["shares"] == pytest.approx(67.7e6, rel=0.001)


def test_share_basis_skips_quarters_with_tiny_eps_and_handles_losses():
    reals = [_y("202512", 100, 100, 1_000)]                                    # 10 triệu cp
    tiny = [_qq("202606", 0.3, 0.3, 3)]                                        # EPS 3 KRW: làm tròn → không tin
    assert F.share_basis(reals, tiny, None, None)["source"] == "năm 2025"
    loss = [_qq("202606", -50, -50, -500)]                                     # quý lỗ: cùng dấu → vẫn suy được
    assert F.share_basis(reals, loss, None, None)["shares"] == pytest.approx(1e7)


def test_share_basis_legacy_when_no_parent_profit():
    reals = [{"period": "202512", "net_profit": 96, "eps": 960}]               # chuỗi backtest: không có ctrl_net
    sb = F.share_basis(reals, None, 5e9, 500)
    assert sb["shares"] == pytest.approx(96e8 / 960) and sb["source"] == "LN ròng ÷ EPS năm"
    assert F.parent_ratio(reals)["rest"] == 1.0


def test_parent_ratio_follows_reported_quarters():
    reals = [_y("202412", 1000, 450, 4_500), _y("202512", 1000, 450, 4_500)]   # năm trước: mẹ 45%
    q = [_qq("202603", 300, 96, 960), _qq("202606", 300, 96, 960)]             # năm nay: mẹ 32%
    pr = F.parent_ratio(reals, F._ytd_run_rate(reals, q))
    assert pr["y1"] == pytest.approx(0.32) and pr["rest"] == pytest.approx(0.32) and pr["reliable"]
    assert pr["last"] == pytest.approx(0.45)
    # không có quý → tỷ lệ năm gần nhất
    assert F.parent_ratio(reals)["rest"] == pytest.approx(0.45)


def test_parent_ratio_unreliable_when_signs_differ():
    reals = [_y("202512", 1000, 900, 9_000)]
    q = [_qq("202603", 100, -50, -500), _qq("202606", 100, -50, -500)]         # hợp nhất lãi, công ty mẹ lỗ
    pr = F.parent_ratio(reals, F._ytd_run_rate(reals, q))
    assert pr["y1"] == pytest.approx(-0.5)                                     # năm 1: số công bố, giữ nguyên
    assert not pr["reliable"] and pr["rest"] == pytest.approx(0.9)             # năm 2+: không suy được → tỷ lệ năm trước


def test_forecast_eps_on_current_share_count():
    reals = [_y("202412", 10_000, 10_000, 14_771, rev=40_000), _y("202512", 12_000, 12_000, 17_725, rev=45_000)]
    q = [_qq("202603", 4_000, 4_000, 8_639, rev=12_000), _qq("202606", 4_500, 4_500, 9_719, rev=13_000)]
    fc = F.project_forecast(reals, quarters=q)
    ctrl_fy = 4_000 + 4_500 + 2 * 4_250                                        # 2 quý thực + 2 quý theo TB
    assert fc["eps"] == pytest.approx(ctrl_fy * 1e8 / 46.3e6, rel=0.002)       # KHÔNG chia cho 67,7 triệu cp năm trước


# ---- đối chiếu DART ↔ Naver ----
class _FakeDart:
    def __init__(self, rows, errs=None):
        self.rows, self.errs = rows, errs or []

    def available(self):
        return True

    def annual_financials(self, ticker, years=6, errors=None):
        if errors is not None:
            errors.extend(self.errs)
        return [dict(r) for r in self.rows]


def _nv(y, rev, np_):
    return {"period": f"{y}12", "is_consensus": False, "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_,
            "eps": 100, "bps": 1000}


def _dt(y, rev, np_, cur="KRW"):
    return {"period": f"{y}12", "revenue": rev, "op_profit": np_ * 1.2, "net_profit": np_, "assets": rev * 2,
            "liabilities": rev, "equity": rev, "debt_ratio": 100.0, "currency": cur}


NAVER3 = [_nv(2023, 1000, 100), _nv(2024, 1100, 110), _nv(2025, 1200, 120)]


def test_merge_dart_uses_dart_when_sources_agree(monkeypatch):
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2022, 900, 90), _dt(2023, 1000.4, 100.2), _dt(2024, 1100, 110),
                                                  _dt(2025, 1199.6, 120.3)]))
    out = F._merge_dart(NAVER3, "X")
    assert [c["period"][:4] for c in out] == ["2022", "2023", "2024", "2025"]
    assert out[-1]["revenue"] == 1199.6 and out[-1]["assets"] == 2399.2 and all(c["_dart"] for c in out)
    assert out[-1]["_src"]["dart"] == "ok" and not out[-1]["_src"]["gaps"]


def test_merge_dart_drops_foreign_currency_statements(monkeypatch):
    # Doosan Bobcat: DART bằng USD → 0,07% số Naver. Không trộn; không coi là lỗi nguồn (nguyên nhân đã rõ)
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(y, v * 0.0007, v * 0.00007, "USD") for y, v in
                                                  ((2023, 1000), (2024, 1100), (2025, 1200))]))
    out = F._merge_dart(NAVER3, "X")
    assert [c["revenue"] for c in out] == [1000, 1100, 1200] and not any(c.get("_dart") for c in out)
    assert out[-1]["_src"]["dart"] == "currency" and "USD" in out[-1]["_src"]["note"]


def test_merge_dart_latest_year_mismatch_drops_dart(monkeypatch):
    # SK Square: doanh thu DART = 13,5% Naver ở năm gần nhất → hai nguồn không cùng một con số
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2022, 900, 90), _dt(2024, 1100, 110), _dt(2025, 162, 120)]))
    out = F._merge_dart(NAVER3, "X")
    assert out[-1]["_src"]["dart"] == "mismatch" and "2025" in out[-1]["_src"]["note"]
    assert [c["period"][:4] for c in out] == ["2023", "2024", "2025"] and out[-1]["revenue"] == 1200
    # LN thuần đọc sai dòng (56 vs 401) cũng là mâu thuẫn nguồn
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2025, 1200, 17)]))
    assert F._merge_dart(NAVER3, "X")[-1]["_src"]["dart"] == "mismatch"


def test_merge_dart_restated_older_year_keeps_naver(monkeypatch):
    # Samsung Biologics 2024: DART gốc 45.473, Naver đã điều chỉnh hồi tố 34.971 → giữ Naver cho năm đó, ghi nhận
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2023, 1000, 100), _dt(2024, 1430, 143), _dt(2025, 1200, 120)]))
    out = F._merge_dart(NAVER3, "X")
    y24 = next(c for c in out if c["period"].startswith("2024"))
    assert y24["revenue"] == 1100 and y24["net_profit"] == 110 and y24["assets"] == 2860     # cân đối vẫn từ DART
    assert out[-1]["_src"]["dart"] == "ok"
    assert {(g["year"], g["field"]) for g in out[-1]["_src"]["gaps"]} >= {("2024", "revenue"), ("2024", "net_profit")}


def test_merge_dart_reports_errors_instead_of_hiding_them(monkeypatch):
    # DART lỗi và các năm có được KHÔNG phủ tới năm thực gần nhất của Naver → thiếu dữ liệu thật → "error" (❌)
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2023, 1000, 100)], errs=["lỗi kết nối DART (ConnectionError)"]))
    out = F._merge_dart(NAVER3, "X")
    assert out[-1]["_src"]["dart"] == "error" and "ConnectionError" in out[-1]["_src"]["note"]
    assert out[0].get("_dart")                                  # năm lấy được vẫn dùng, nhưng đã gắn cờ không đầy đủ
    # DART lỗi khi hỏi năm MỚI HƠN, các năm đã lưu phủ tới năm gần nhất → chuỗi đủ, chỉ ghi nhận "cached" (⚠️)
    monkeypatch.setattr(F, "dart_api", _FakeDart([_dt(2024, 1100, 110), _dt(2025, 1200, 120)],
                                                 errs=["lỗi kết nối DART (ConnectionError)"]))
    out = F._merge_dart(NAVER3, "X")
    assert out[-1]["_src"]["dart"] == "cached" and "đã lưu" in out[-1]["_src"]["note"]


# ---- dart_api: lỗi ≠ không có báo cáo ----
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _dart_env(monkeypatch, handler):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(D, "RETRY_WAITS", (0, 0))
    monkeypatch.setattr(D, "MIN_GAP", 0)
    monkeypatch.setattr(D, "STATS", {"ok": 0, "nodata": 0, "errors": 0, "streak": 0, "open_until": 0.0, "last_error": ""})
    monkeypatch.setattr(D.requests, "get", handler)


def test_fetch_statements_distinguishes_no_data_from_failure(monkeypatch):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(params["fs_div"])
        if params["fs_div"] == "OFS":
            return _Resp({"status": "013", "message": "조회된 데이타가 없습니다."})
        raise ConnectionError("https://opendart…crtfc_key=test-key")
    _dart_env(monkeypatch, get)
    assert D.fetch_statements("00000001", 2025, fs_div="OFS") is None            # DART xác nhận không có
    with pytest.raises(D.DartError) as e:
        D.fetch_statements("00000001", 2025, fs_div="CFS")
    assert calls.count("CFS") == 3                                               # 1 lần + 2 lần thử lại
    assert "test-key" not in str(e.value)                                        # không lộ khóa API trong thông báo
    assert D.error_count() == 1


def test_fetch_statements_retries_then_succeeds_and_breaker_opens(monkeypatch):
    n = {"k": 0}

    def flaky(url, params=None, timeout=None):
        n["k"] += 1
        if n["k"] < 3:
            raise ConnectionResetError()
        return _Resp({"status": "000", "list": [{"account_nm": "자산총계"}]})
    _dart_env(monkeypatch, flaky)
    assert D.fetch_statements("1", 2025) == [{"account_nm": "자산총계"}]

    def down(url, params=None, timeout=None):
        n["k"] += 1
        raise ConnectionResetError()
    monkeypatch.setattr(D.requests, "get", down)
    for _ in range(D.BREAKER_AFTER):
        with pytest.raises(D.DartError):
            D.fetch_statements("1", 2025)
    before = n["k"]
    with pytest.raises(D.DartError):                                             # mạch đã ngắt: không nã thêm vào DART
        D.fetch_statements("1", 2025)
    assert n["k"] == before


def test_year_fetch_does_not_fall_back_to_separate_statements_on_error(monkeypatch, tmp_path):
    seen = []

    def fs(cc, year, rc="11011", fs_div="CFS", timeout=None):
        seen.append(fs_div)
        if fs_div == "CFS":
            raise D.DartError("lỗi kết nối DART (ConnectionError)")
        return [{"account_nm": "매출액", "thstrm_amount": "78766800000000", "currency": "KRW"}]   # số RIÊNG LẺ
    monkeypatch.setattr(D, "fetch_statements", fs)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    monkeypatch.setattr(D, "YEAR_CACHE_DIR", str(tmp_path))
    with pytest.raises(D.DartError):
        D._fetch_year("X", 2025)
    assert seen == ["CFS"] and not list(tmp_path.iterdir())                      # không lùi OFS, không ghi cache
    # DART xác nhận không có bản hợp nhất → lúc đó mới dùng bản riêng lẻ
    monkeypatch.setattr(D, "fetch_statements", lambda cc, y, rc="11011", fs_div="CFS", timeout=None:
                        None if fs_div == "CFS" else [{"account_nm": "매출액", "thstrm_amount": "100000000000",
                                                       "currency": "KRW"}])
    rec = D._fetch_year("X", 2025)
    assert rec["fs_div"] == "OFS" and rec["revenue"] == 1000 and rec["currency"] == "KRW"


def test_annual_financials_surfaces_partial_failure(monkeypatch):
    calls = []

    def fy(cc, y, offline=False):
        calls.append((y, offline))
        if offline:                                              # sau lỗi: chỉ còn các năm đã lưu trên đĩa
            return {"period": f"{y}12", "revenue": 1.0} if y in (2021, 2022) else None
        if y == 2023:
            raise D.DartError("DART trả mã lỗi 020 (vượt hạn mức gọi trong ngày)")
        return {"period": f"{y}12", "revenue": 1.0}
    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(D, "corp_code_of", lambda t: "X")
    monkeypatch.setattr(D, "_fetch_year", fy)
    errs = []
    out = D.annual_financials("005930", years=6, end_year=2025, errors=errs)
    assert [r["period"] for r in out] == ["202112", "202212", "202412", "202512"] and errs and "020" in errs[0]
    assert all(off for y, off in calls if y < 2023)              # không gọi DART nữa sau lỗi đầu tiên


def test_year_cache_used_offline_even_if_old_version(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    monkeypatch.setattr(D, "YEAR_CACHE_DIR", str(tmp_path))
    (tmp_path / "X_2024.json").write_text(json.dumps({"period": "202412", "revenue": 5.0, "_v": 1}))
    monkeypatch.setattr(D, "_fetch_year_raw", lambda cc, y: (_ for _ in ()).throw(AssertionError("không được gọi")))
    assert D._fetch_year("X", 2024, offline=True)["revenue"] == 5.0
    assert D._fetch_year("X", 2023, offline=True) is None


def test_fetch_statements_non_object_body_is_an_error(monkeypatch):
    _dart_env(monkeypatch, lambda url, params=None, timeout=None: _Resp(["không phải object"]))
    with pytest.raises(D.DartError) as e:
        D.fetch_statements("1", 2025)
    assert "không đúng dạng" in str(e.value)


def test_statement_currency():
    assert D.statement_currency([{"currency": "USD"}, {"currency": "USD"}, {"currency": ""}]) == "USD"
    assert D.statement_currency([{}]) == "KRW"


# ---- Rf: không lặng lẽ dùng 3,2% ----
def test_rf_for_valuation_flags_fallbacks(monkeypatch):
    live = {"value": 0.0399, "source": "Naver", "asof": "2026-11-13 16:00", "live": True}
    monkeypatch.setattr(S, "RF_LAST", {})
    monkeypatch.setattr(S, "fetch_kgb10y_detail", lambda: dict(live))
    assert S.rf_for_valuation()["live"] is True
    # nguồn thời gian thực lỗi nhưng tiến trình đã từng lấy được → dùng lần gần nhất, gắn cờ
    monkeypatch.setattr(S, "RF_LAST", dict(live))
    monkeypatch.setattr(S, "fetch_kgb10y_detail", lambda: {})
    d = S.rf_for_valuation()
    assert d["value"] == 0.0399 and d["live"] is False and "gần nhất" in d["note"]
    # chỉ còn FRED (trung bình tháng, trễ) → vẫn không phải số trực tiếp
    monkeypatch.setattr(S, "RF_LAST", {})
    monkeypatch.setattr(S, "fetch_kgb10y_detail", lambda: {"value": 0.0445, "source": "FRED", "asof": "2026-09", "live": False})
    d = S.rf_for_valuation()
    assert d["value"] == 0.0445 and d["live"] is False and "FRED" in d["note"]
    monkeypatch.setattr(S, "fetch_kgb10y_detail", lambda: {})
    d = S.rf_for_valuation()
    assert d["value"] == S.RF_HARD_DEFAULT and d["live"] is False


def test_main_accounts_prefers_standard_id_then_name():
    def it(sj, aid, nm, v):
        return {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": str(v)}
    items = [it("CF", "x", "당기순이익", 999e8),                                   # cùng tên ở báo cáo khác — không lấy trước
             it("IS", "ifrs-full_ProfitLoss", "연결당기순이익", 1036e8),            # tên lạ nhưng mã chuẩn (Hyundai Motor)
             it("IS", "-표준계정코드 미사용-", "매출액", 5000e8),                    # không mã chuẩn → theo tên
             it("BS", "ifrs-full_Assets", "자산 총계", 9000e8)]
    acc = D.main_accounts(items)
    assert acc == {"net_profit": 1036.0, "revenue": 5000.0, "assets": 9000.0}
    # chỉ có ở báo cáo khác → vẫn lấy (bậc 3), không mất dữ liệu so với cách cũ
    assert D.main_accounts([it("CF", "x", "당기순이익", 77e8)]) == {"net_profit": 77.0}


def test_net_profit_never_read_from_equity_statement():
    """펌텍코리아 2025: KQKD không có dòng LN thuần; dòng '당기순이익' ở báo cáo thay đổi vốn chủ (SCE) là cột của cổ đông
    thiểu số (56억). Đúng: LN cổ đông mẹ + LN thiểu số = 344 + 56 = 400 (Naver 401)."""
    def it(sj, aid, nm, v):
        return {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": str(int(v * 1e8))}
    items = [it("SCE", "ifrs-full_ProfitLoss", "당기순이익", 56),
             it("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익", 344),
             it("CIS", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 56)]
    assert D.main_accounts(items)["net_profit"] == pytest.approx(400)
    assert D.main_accounts([it("SCE", "x", "당기순이익", 56)]) == {}            # chỉ có ở SCE → không lấy


def test_dart_errors_name_the_root_cause(monkeypatch):
    """Cảnh báo trên app phải nói được NGUYÊN NHÂN: mã trạng thái DART, loại lỗi mạng, và khi đã ngắt mạch thì vẫn
    nhắc lại lỗi gốc (lần đầu trên máy chủ Cloud mới chỉ hiện 'đang từ chối kết nối', không biết vì sao)."""
    import requests as _rq

    class R:
        def __init__(self, p):
            self.p = p

        def json(self):
            return self.p
    _dart_env(monkeypatch, lambda url, params=None, timeout=None: R({"status": "012", "message": "접근할 수 없는 IP"}))
    with pytest.raises(D.DartError) as e:
        D.fetch_statements("1", 2025)
    assert "012" in str(e.value) and "IP" in str(e.value)

    def slow(url, params=None, timeout=None):
        raise _rq.ReadTimeout("https://opendart…crtfc_key=test-key")
    monkeypatch.setattr(D.requests, "get", slow)
    for _ in range(D.BREAKER_AFTER - 1):
        with pytest.raises(D.DartError):
            D.fetch_statements("1", 2025)
    with pytest.raises(D.DartError) as e:
        D.fetch_statements("1", 2025)
    assert "quá chậm" in str(e.value) and "test-key" not in str(e.value)
    with pytest.raises(D.DartError) as e:                               # đã ngắt mạch: vẫn nêu nguyên nhân gốc
        D.fetch_statements("1", 2025)
    assert "tạm ngừng gọi DART" in str(e.value) and "quá chậm" in str(e.value)
    assert "đóng / từ chối kết nối" in D.describe_exception(ConnectionResetError())
