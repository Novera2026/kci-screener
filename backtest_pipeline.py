"""
Backtest QUY TRÌNH ĐỊNH GIÁ (trang 🧮, pipeline.evaluate) trên đúng các lát cắt point-in-time của backtest_dart.

Mỗi điểm (mã, mốc 15/4 năm Y): chỉ dùng BCTC DART ≤ Y−1, giá tại mốc, beta 78 tuần trước mốc, Rf tháng trước
mốc, peer cùng ngành KRX trong chính lát cắt. Giả định dự phóng = default_drivers (như người dùng không sửa
gì). Không có BCTC quý point-in-time → năm 1 dự phóng theo CAGR × biên xu hướng.

Đo (so cùng điểm với lõi KCI của backtest_dart):
  1. IC upside ↔ lợi suất vượt KOSPI 12 tháng: kết luận đã công bố, tín hiệu thô (trước cổng), lõi KCI.
  2. Theo độ tin cậy High / Medium / Low: lợi suất vượt, tỷ lệ thắng thị trường, sai số |fair/giá − 1|.
  3. Hiệu quả cổng: điểm bị giữ lại có tệ hơn điểm được công bố không (cùng hướng tín hiệu thô).
  4. Hiệu chỉnh dải: lợi suất vượt theo nhóm fair_thô / giá — dải [0,4; 2,5] có đặt đúng chỗ không.
  5. Mô hình chính đồng thuận (±20%) so với không đồng thuận.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import statistics as _st
from typing import Optional

import analysis as AN
import backtest_dart as B
import pipeline as P
import valuation as V
from screener import beta_vs_market

RATIO_BINS = [(0.0, 0.4), (0.4, 0.67), (0.67, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, float("inf"))]


def _raw_signal(rec: dict) -> Optional[float]:
    """Tín hiệu thô của quy trình: trung vị mọi mô hình CHÍNH tính được (trước cổng số)."""
    vals = [r["value"] for r in rec["rows"] if r["role"] == "primary" and r.get("value")]
    return V._median(vals) if vals else None


def pipeline_points(points: list[dict], a: Optional[V.Assumptions] = None, years_fc: int = 5,
                    with_fs: bool = True, g_high_fn=None) -> list[dict]:
    """Chạy pipeline.evaluate cho từng điểm của backtest_dart.build_point.

    g_high_fn(row, hist, drivers) → tăng trưởng giai đoạn đầu RIÊNG từng mã (thay a.g_high cố định, dùng trong
    "DCF earnings" và DDM 2 giai đoạn). None = giữ a.g_high. Chỉ dùng dữ liệu ≤ Y−1 (hist) và dự phóng tại mốc."""
    a = a or V.Assumptions()
    by_year: dict = {}
    for p in points:
        by_year.setdefault(p["year"], []).append(p)
    out = []
    for year, pts in sorted(by_year.items()):
        rows = [p["row"] for p in pts]
        for p in pts:
            row = dict(p["row"])
            row["beta"] = beta_vs_market(p["stock_weekly"], p["kospi_weekly"])
            hist = [dict(h, _dart=True) for h in p["hist"]]
            peers = [r for r in rows if r.get("industry_kr") == row.get("industry_kr")]
            ap = dataclasses.replace(a, rf=p["rf"]) if p["rf"] is not None else a
            D = {"row": row, "series": hist, "quarters": [], "extras": {}, "peers": peers,
                 "ticker": row["ticker"]}
            if with_fs:                               # BCTC đầy đủ năm Y−1 (point-in-time) → FCFF-DCF được chấm
                try:
                    D["fs_full"] = AN.fetch_full(row["ticker"], year - 1)
                except Exception:
                    D["fs_full"] = None
            try:
                idn = P.identify(row)
                t_day = _dt.date(int(p["date"][:4]), int(p["date"][4:6]), int(p["date"][6:8]))
                dq = P.data_quality(row, hist, [], row["beta"], today=t_day)
                drv = P.default_drivers(hist, row, ap, years=years_fc, cyclical=idn["cyclical"],
                                         financial=idn["financial"])
                if drv:
                    drv[0].pop("_basis", None)
                if g_high_fn is not None:
                    ap = dataclasses.replace(ap, g_high=g_high_fn(row, hist, drv))
                ev = P.evaluate(D, ap, drv, idn, dq)
            except Exception:
                continue
            rec, price = ev["rec"], row["price"]
            raw = _raw_signal(rec)
            prim = [r["value"] for r in rec["rows"] if r["role"] == "primary" and r["pass"]]
            agree = (len(prim) >= 2 and rec["fair"] is not None
                     and all(abs(v / rec["fair"] - 1) <= P.AGREE_BAND for v in prim))
            excess = (p["ret"] - p["kospi_ret"]) if (p["ret"] is not None and p["kospi_ret"] is not None) else None
            out.append({
                "year": year, "ticker": row["ticker"], "name": row.get("name"), "bucket": idn["bucket"],
                "price": price, "fair": rec["fair"], "published": rec["fair"] is not None,
                "confidence": rec["confidence"] if rec["fair"] is not None else "—",
                "upside": (rec["fair"] / price - 1) if rec["fair"] else None,
                "raw_ratio": (raw / price) if (raw and price) else None,
                "raw_upside": (raw / price - 1) if (raw and price) else None,
                "fc_ok": ev["fg"]["ok"], "n_primary_pass": len(prim), "agree": agree,
                "base_upside": ((ev["base"].fair_raw / price - 1) if (ev["base"].fair_raw and price) else None),
                "base_method": ev["base"].auto_method, "g_high": ap.g_high,
                "fcff_upside": ((ev["fcff"]["value"] / price - 1) if (ev.get("fcff") and ev["fcff"].get("value")
                                                                       and price) else None),
                "fcff_pass": any(r["name"].startswith("FCFF-DCF") and r["pass"] for r in rec["rows"]),
                "dq_cap": dq["cap"], "ret": p["ret"], "kospi_ret": p["kospi_ret"], "excess": excess})
    return out


# ---------------------------------------------------------------------------- đo
def _grp(rows: list[dict], key: str = "upside") -> dict:
    xs = [r for r in rows if r.get("excess") is not None]
    if not xs:
        return {"n": 0}
    err = [abs(r[key]) for r in xs if r.get(key) is not None]
    return {"n": len(xs), "mean_excess": _st.mean(r["excess"] for r in xs),
            "median_excess": _st.median(r["excess"] for r in xs),
            "beat_mkt": sum(r["excess"] > 0 for r in xs) / len(xs),
            "loss30": sum(r["ret"] < -0.30 for r in xs) / len(xs),
            "median_abs_gap": _st.median(err) if err else None}


def _ic(rows: list[dict], key: str) -> dict:
    """IC theo năm (Spearman, ≥8 điểm/năm), trung bình, t, IC gộp."""
    xs = [r for r in rows if r.get(key) is not None and r.get("excess") is not None]
    by = {}
    for y in sorted({r["year"] for r in xs}):
        g = [r for r in xs if r["year"] == y]
        ic = B.spearman([r[key] for r in g], [r["excess"] for r in g])
        if ic is not None:
            by[y] = ic
    ics = list(by.values())
    mean = _st.mean(ics) if ics else None
    t = (mean / (_st.stdev(ics) / len(ics) ** 0.5)) if len(ics) >= 3 and _st.stdev(ics) > 0 else None
    return {"n": len(xs), "by_year": by, "mean": mean, "t": t,
            "pooled": B.spearman([r[key] for r in xs], [r["excess"] for r in xs])}


def score(pipe: list[dict], core: list[dict]) -> dict:
    """So quy trình với lõi KCI trên CÙNG tập (mã, năm)."""
    core_by = {(r["year"], r["ticker"]): r for r in core}
    for r in pipe:
        c = core_by.get((r["year"], r["ticker"]))
        r["core_upside"] = c.get("upside") if c else None
    pub = [r for r in pipe if r["published"]]
    res = {
        "n_points": len(pipe), "n_published": len(pub),
        "ic": {
            "Kết luận công bố (quy trình)": _ic(pub, "upside"),
            "Lõi KCI — cùng các điểm đã công bố": _ic(pub, "core_upside"),
            "Tín hiệu thô quy trình — mọi điểm": _ic(pipe, "raw_upside"),
            "Lõi KCI (thô) — mọi điểm": _ic(pipe, "core_upside"),
        },
        "by_confidence": {c: _grp([r for r in pub if r["confidence"] == c])
                          for c in ("High", "Medium", "Low")},
        "published_vs_withheld": {
            "Công bố": _grp(pub, "raw_upside"),
            "Bị giữ lại": _grp([r for r in pipe if not r["published"] and r["raw_upside"] is not None],
                               "raw_upside"),
        },
        "ratio_bins": [], "agreement": {
            "Mô hình chính đồng thuận ±20%": _grp([r for r in pub if r["agree"]]),
            "Không đồng thuận / chỉ 1 mô hình": _grp([r for r in pub if not r["agree"]]),
        },
        "by_bucket": {},
    }
    for lo, hi in RATIO_BINS:
        g = [r for r in pipe if r["raw_ratio"] is not None and lo <= r["raw_ratio"] < hi]
        res["ratio_bins"].append({"bin": (lo, hi), **_grp(g, "raw_upside")})
    for b in sorted({r["bucket"] for r in pub}):
        res["by_bucket"][b] = _grp([r for r in pub if r["bucket"] == b])
    # tín hiệu "rẻ" (upside > +20%) có thật sự thắng không — công bố vs giữ lại
    res["cheap_signal"] = {
        "Công bố, upside > +20%": _grp([r for r in pub if (r["upside"] or 0) > 0.2]),
        "Giữ lại, upside thô > +20%": _grp([r for r in pipe if not r["published"]
                                            and (r["raw_upside"] or 0) > 0.2]),
    }
    return res


def run(tickers: list[str], years=(2021, 2025), registry=None, a: Optional[V.Assumptions] = None,
        progress=None, cache_dir: str = B.CACHE_DIR) -> tuple[list[dict], list[dict], dict]:
    """Tải dữ liệu bằng backtest_dart (dùng chung cache), chạy lõi + quy trình, trả (pipe, core, score)."""
    captured: dict = {}
    orig = B.value_points

    def _spy(points, a_=None):                    # lấy đúng danh sách điểm backtest_dart dựng
        captured["points"] = points
        return orig(points, a_)
    B.value_points = _spy
    try:
        df_core, _ = B.run_backtest_dart(tickers, years=years, a=a, registry=registry, progress=progress,
                                         cache_dir=cache_dir)
    finally:
        B.value_points = orig
    core = df_core.to_dict("records") if len(df_core) else []
    pipe = pipeline_points(captured.get("points", []), a)
    return pipe, core, score(pipe, core)


# ---------------------------------------------------------------------------- báo cáo
def _p(x, d=1):
    return "—" if x is None else f"{x:+.{d}%}"


def _row(name, g):
    if not g.get("n"):
        return f"| {name} | 0 | — | — | — | — | — |"
    gap = "—" if g.get("median_abs_gap") is None else f"{g['median_abs_gap']:.0%}"
    return (f"| {name} | {g['n']} | {_p(g['median_excess'])} | {_p(g['mean_excess'])} | {g['beat_mkt']:.0%} | "
            f"{g['loss30']:.0%} | {gap} |")


_HDR = ["| Nhóm | n | Vượt KOSPI trung vị | Vượt KOSPI TB | Thắng thị trường | Mất >30% | |fair/giá−1| trung vị |",
        "|---|---:|---:|---:|---:|---:|---:|"]


def report_markdown(res: dict, meta: Optional[dict] = None) -> str:
    L = [f"# Backtest quy trình định giá — {res['n_points']} điểm, {res['n_published']} được công bố "
         f"({res['n_published'] / max(res['n_points'], 1):.0%})", ""]
    if meta:
        L += [f"_{meta}_", ""]
    L += ["## 1. Khả năng dự báo (IC upside ↔ lợi suất vượt KOSPI 12 tháng)", "",
          "| Tín hiệu | n | IC TB theo năm | t | IC gộp | IC từng năm |", "|---|---:|---:|---:|---:|---|"]
    def f(x, fmt):
        return "—" if x is None else format(x, fmt)
    for k, v in res["ic"].items():
        yrs = " · ".join(f"{y}: {x:+.2f}" for y, x in v["by_year"].items())
        L.append(f"| {k} | {v['n']} | {f(v['mean'], '+.3f')} | {f(v['t'], '.2f')} | {f(v['pooled'], '+.3f')} "
                 f"| {yrs} |")
    L += ["", "## 2. Theo độ tin cậy (điểm được công bố)", ""] + _HDR
    L += [_row(c, g) for c, g in res["by_confidence"].items()]
    L += ["", "## 3. Hiệu quả cổng số", ""] + _HDR
    L += [_row(c, g) for c, g in res["published_vs_withheld"].items()]
    L += [_row(c, g) for c, g in res["cheap_signal"].items()]
    L += ["", "## 4. Hiệu chỉnh dải: lợi suất theo fair thô / giá", ""] + _HDR
    for b in res["ratio_bins"]:
        lo, hi = b["bin"]
        L.append(_row(f"{lo:.2f}–{'∞' if hi == float('inf') else f'{hi:.2f}'}×", b))
    L += ["", "## 5. Đồng thuận giữa các mô hình chính", ""] + _HDR
    L += [_row(c, g) for c, g in res["agreement"].items()]
    L += ["", "## 6. Theo nhóm ngành (điểm được công bố)", ""] + _HDR
    L += [_row(c, g) for c, g in res["by_bucket"].items()]
    L += ["", "_Lợi suất chỉ tính giá, không gồm cổ tức; danh sách gốc là mã đang niêm yết (thiên lệch sống sót); "
              "giả định dự phóng = mặc định của App, không có BCTC quý point-in-time._"]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from screener import TickerRegistry
    reg = TickerRegistry()
    tks = sys.argv[1:] or [str(t).zfill(6) for t in reg.df[reg.df["sector"].notna()]["ticker"]]
    pipe, core, res = run(tks, registry=reg, progress=lambda s, x: print(f"  [{x:4.0%}] {s}", flush=True))
    print(report_markdown(res))
