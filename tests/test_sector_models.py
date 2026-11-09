"""Mô hình chuyên ngành (sector_models.py) — dữ liệu giả, không cần mạng."""
import pytest

import pipeline as P
import sector_models as S
import valuation as V
from test_pipeline import _D, _series

A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)


# ---------------------------------------------------------------- backlog
def test_backlog_burns_evenly_without_new_orders():
    s = _series(rev0=1000, g=0.0)                         # DT năm gần nhất 1.000
    r = S.backlog_drivers(s, backlog=3000, burn_years=3, backlog_margin=0.08, new_order_ratio=0.0, years=5)
    assert r["ok"]
    assert r["revenue"][:3] == pytest.approx([1000, 1000, 1000])
    assert r["revenue"][3:] == pytest.approx([0, 0])


def test_backlog_fractional_years_and_new_orders_keep_scale():
    s = _series(rev0=1000, g=0.0)
    r = S.backlog_drivers(s, backlog=2500, burn_years=2.5, backlog_margin=0.08, new_order_ratio=0.0, years=4)
    assert sum(r["revenue"]) == pytest.approx(2500)        # 1000 + 1000 + 500
    r = S.backlog_drivers(s, backlog=3000, burn_years=3, backlog_margin=0.08, new_order_ratio=1.0, years=5)
    assert r["revenue"][0] == pytest.approx(1000) and r["revenue"][-1] > 900   # book-to-bill 1 giữ quy mô


def test_backlog_gates_block_unit_errors_and_margin():
    s = _series(rev0=1000, g=0.0, margin=0.10)
    r = S.backlog_drivers(s, backlog=50_000, burn_years=3, backlog_margin=0.08, new_order_ratio=1.0)
    assert not r["ok"]                                     # 50 năm doanh thu → nhập sai đơn vị
    r = S.backlog_drivers(s, backlog=3000, burn_years=3, backlog_margin=0.35, new_order_ratio=1.0)
    assert not r["ok"]                                     # biên 35% > max 10% + 10 điểm


# ---------------------------------------------------------------- công suất
def test_capacity_revenue_and_asp_gate():
    s = _series(rev0=1000, g=0.0)                          # 1.000억 trên 10 GWh × 50% → 200억/GWh
    r = S.capacity_drivers(s, 10, [12, 14], 0.5, 200, 0.05)
    assert r["ok"] and r["revenue"] == pytest.approx([1200, 1400])
    r = S.capacity_drivers(s, 10, [12, 14], 0.5, 400, 0.05)   # giá bán 2× ngụ ý → ❌
    assert not r["ok"]


def test_ev_per_gwh():
    assert S.ev_per_gwh_value(100, 50, 1000, 1e7) == pytest.approx((5000 - 1000) * 1e8 / 1e7)
    assert S.ev_per_gwh_value(100, 5, 1000, 1e7) is None      # vốn chủ âm
    assert S.implied_ev_per_gwh(1e12, 2000, 100) == pytest.approx(120)


# ---------------------------------------------------------------- rNPV
def _asset(**kw):
    a = {"Tên": "X", "Pha": "Pha 3", "Doanh số đỉnh (억)": 1000, "Năm ra mắt": 2026, "PoS %": None,
         "Biên LN %": 30, "Chi phí PT còn lại (억)": 0}
    a.update(kw)
    return a


def test_rnpv_approved_flat_matches_annuity():
    """Đã phê duyệt, ramp 1 năm: 10 năm × 1000 × 30% chiết khấu 10% → niên kim."""
    out = S.rnpv([_asset(Pha="Đã phê duyệt")], r=0.10, shares=1e6, this_year=2026, ramp_years=1, exclusivity=10)
    ann = sum(300 / 1.1 ** t for t in range(1, 11))
    assert out["sum_assets"] == pytest.approx(ann)
    assert out["value"] == pytest.approx(ann * 1e8 / 1e6)


def test_rnpv_uses_phase_pos_and_caps_manual_pos():
    base = S.rnpv([_asset()], 0.10, 1e6, this_year=2026)
    assert base["rows"][0]["PoS dùng %"] == pytest.approx(57.8)
    over = S.rnpv([_asset(**{"Pha": "Pha 1", "PoS %": 30})], 0.10, 1e6, this_year=2026)   # 30% > 1,5×7,9%
    assert not over["ok"] and over["value"] is None


def test_rnpv_cost_and_net_cash():
    a = _asset(**{"Năm ra mắt": 2028, "Chi phí PT còn lại (억)": 200})
    out = S.rnpv([a], 0.10, 1e6, net_cash_eok=500, this_year=2026)
    assert out["rows"][0]["PV chi phí PT (억)"] == pytest.approx(100 / 1.1 + 100 / 1.1 ** 2)
    assert out["equity"] == pytest.approx(out["sum_assets"] + 500)


# ---------------------------------------------------------------- tích hợp evaluate
def test_extra_candidate_goes_through_gates_and_follows_ke():
    D = _D(0.6)
    drv = P.default_drivers(D["series"], D["row"], A, years=3)
    drv[0].pop("_basis", None)
    price = D["row"]["price"]
    D["extra"] = [lambda coc: {"name": "rNPV test", "value": price * (1 + (0.10 - coc["ke"]) * 5),
                               "role": "primary"}]
    rows = {r["name"]: r for r in P.evaluate(D, A, drv)["rec"]["rows"]}
    assert rows["rNPV test"]["pass"]
    D["extra"] = [lambda coc: {"name": "rNPV test", "value": price * 10, "role": "primary"}]
    rows = {r["name"]: r for r in P.evaluate(D, A, drv)["rec"]["rows"]}
    assert not rows["rNPV test"]["pass"]                  # 10× giá → cổng chặn
