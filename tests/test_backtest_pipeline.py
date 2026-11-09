"""Backtest quy trình (backtest_pipeline.py) — điểm giả, không cần mạng."""
import pytest

import backtest_pipeline as BP
from test_pipeline import _series, _row


def _point(year, mult, ret, kret=0.0, ticker="000001", industry="식품"):
    s = _series()
    r = _row(s, price=s[-1]["bps"] * mult)
    r.update(ticker=ticker, industry_kr=industry, per=r["price"] / r["eps"], pbr=r["price"] / r["bps"])
    weekly = [100 * (1.01 ** i) for i in range(80)]
    return {"year": year, "date": f"{year}0415", "row": r, "hist": s, "stock_weekly": weekly,
            "kospi_weekly": weekly, "rf": 0.03, "fwd_date": f"{year + 1}0415", "ret": ret, "kospi_ret": kret,
            "split_adj": 1.0}


def test_pipeline_points_point_in_time_fields():
    pts = [_point(2023, m, ret, ticker=f"00000{i}") for i, (m, ret) in enumerate([(0.6, 0.2), (1.0, 0.0), (1.4, -0.1)])]
    out = BP.pipeline_points(pts)
    assert len(out) == 3
    r = out[0]
    assert r["excess"] == pytest.approx(0.2)
    assert r["raw_ratio"] is not None
    assert r["published"] == (r["fair"] is not None)


def test_score_compares_on_same_points():
    pipe = [{"year": 2023, "ticker": f"{i:06d}", "published": i % 2 == 0, "upside": 0.1 * i if i % 2 == 0 else None,
             "raw_upside": 0.1 * i, "raw_ratio": 1 + 0.1 * i, "confidence": "Medium" if i % 2 == 0 else "—",
             "agree": False, "bucket": "X", "ret": 0.01 * i, "excess": 0.01 * i} for i in range(20)]
    core = [{"year": 2023, "ticker": f"{i:06d}", "upside": -0.1 * i} for i in range(20)]
    res = BP.score(pipe, core)
    assert res["n_published"] == 10
    assert res["ic"]["Kết luận công bố (quy trình)"]["pooled"] == pytest.approx(1.0)
    assert res["ic"]["Lõi KCI (thô) — mọi điểm"]["pooled"] == pytest.approx(-1.0)
    md = BP.report_markdown(res)
    assert "Backtest quy trình định giá" in md and "Theo độ tin cậy" in md
