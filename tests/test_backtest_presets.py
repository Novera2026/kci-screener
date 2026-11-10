"""Backtest các bộ tiêu chuẩn (backtest_presets.py) — điểm giả, không mạng (fetch_full trả None)."""
import pytest

import backtest_presets as BPS


def _pt(tk, year, pe, pb, ret, kret=0.0, dps=300.0, price=10000.0):
    eps, bps = price / pe, price / pb
    return {"year": year, "date": f"{year}0415",
            "row": {"ticker": tk, "name": tk, "industry_kr": "식품", "sector": None, "price": price, "eps": eps,
                    "bps": bps, "dps": dps, "per": pe, "pbr": pb, "market_cap": price * 1e8},
            "hist": [{"net_profit": 100}], "ret": ret, "kospi_ret": kret}


def test_preset_portfolio_vs_universe(monkeypatch):
    monkeypatch.setattr(BPS.AN, "fetch_full", lambda tk, y: None)
    pts = []
    for y in (2022, 2023, 2024):
        pts += [_pt("cheap1", y, 6, 0.6, 0.30), _pt("cheap2", y, 7, 0.7, 0.20),
                _pt("mid", y, 12, 1.2, 0.0), _pt("dear1", y, 25, 3.0, -0.20), _pt("dear2", y, 30, 3.5, -0.10)]
    pipe = [{"year": p["year"], "ticker": p["row"]["ticker"], "fair": 1.0} for p in pts]
    res = BPS.run_presets(pts, pipe, keys=["graham_number"])
    s1 = res["graham_number"]["summary"]["t1"]
    y = res["graham_number"]["years"][0]
    assert set(y["t1"]["tickers"]) == {"cheap1", "cheap2", "mid"}     # P/E×P/B ≤ 22,5 (mid: 14,4)
    assert y["univ_ret"] == pytest.approx(0.04)
    assert y["t1"]["vs_univ"] == pytest.approx(0.5 / 3 - 0.04)
    assert s1["years_beat_univ"] == 3 and s1["mean_vs_univ"] == pytest.approx(0.5 / 3 - 0.04)
    # tầng 2 cần hạng chất lượng ≥ C — không có BCTC (fetch_full None) → không mã nào qua
    assert res["graham_number"]["summary"]["t2"]["n_picks"] == 0
    md = BPS.report_markdown(res)
    assert "Backtest các bộ tiêu chuẩn" in md and "chr(" not in md


def test_div_yield_point_in_time():
    r = BPS._row_at(_pt("x", 2023, 10, 1.0, 0.0, dps=500.0, price=10000.0))
    assert r["div_yield"] == pytest.approx(5.0)
