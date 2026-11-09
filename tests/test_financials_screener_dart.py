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


def test_dart_year_cache_skips_repeat_requests(monkeypatch):
    calls = []

    def raw(cc, y):
        calls.append((cc, y))
        return {"period": f"{y}12", "revenue": 1.0} if y < 2026 else None
    monkeypatch.setattr(D, "_fetch_year_raw", raw)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    for _ in range(3):
        D._fetch_year("X", 2025)
        D._fetch_year("X", 2026)                    # chưa nộp → không nhớ, lần sau hỏi lại
    assert calls.count(("X", 2025)) == 1 and calls.count(("X", 2026)) == 3
    D._fetch_year("X", 2025)["revenue"] = 99        # sửa bản trả về không làm bẩn cache
    assert D._fetch_year("X", 2025)["revenue"] == 1.0


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
