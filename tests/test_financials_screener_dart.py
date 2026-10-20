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
