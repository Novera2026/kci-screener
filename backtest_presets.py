"""
Backtest POINT-IN-TIME các bộ tiêu chuẩn sàng lọc (screen.PRESETS) trên thị trường Hàn.

Tại mỗi mốc T = 15/4 năm Y (dữ liệu của backtest_dart: BCTC DART ≤ Y−1, giá tại T, peer trong lát cắt):
  • Tầng 1: screen.apply_preset trên lát cắt năm Y (P/E, P/B, EPS, BPS, cổ tức = DPS/giá, vốn hóa tại T).
  • Tầng 2: screen.stage2_check với số liệu BCTC đầy đủ DART năm Y−1 (analysis.fetch_full: F-score, thanh toán
    hiện hành, nợ/VCSH, hạng chất lượng) + 'có năm lỗ' trong lịch sử ≤ Y−1 + 'giá trị hợp lý qua cổng' từ
    backtest_pipeline (đúng quy trình định giá tại T).
  • Danh mục chia đều các mã qua tầng 2 (và riêng tầng 1), giữ 12 tháng.

Đo, theo năm và gộp:
  • lợi suất vượt KOSPI và lợi suất vượt TRUNG BÌNH DANH SÁCH GỐC (chia đều cả danh sách gốc cùng năm) — danh sách gốc là top vốn hóa
    HIỆN TẠI nên có thiên lệch sống sót; so với chính danh sách gốc tách được phần 'chọn mã' khỏi thiên lệch đó;
  • tỷ lệ mã thắng danh sách gốc, tỷ lệ mất > 30%, số mã/năm; t-thống kê của chênh lệch theo năm.
Lợi suất chỉ tính giá (không cổ tức) — bất lợi cho bộ cổ tức cao.
"""
from __future__ import annotations

import statistics as _st
from typing import Optional

import analysis as AN
import backtest_dart as B
import backtest_pipeline as BP
import pipeline as P
import screen as SC
import valuation as V


def collect(tickers: list[str], years=(2021, 2025), registry=None, a: Optional[V.Assumptions] = None,
            progress=None) -> tuple[list[dict], list[dict]]:
    """Tải dữ liệu bằng backtest_dart (cache chung) → (points, pipe) — pipe = kết quả quy trình tại từng điểm."""
    captured: dict = {}
    orig = B.value_points

    def _spy(points, a_=None):
        captured["points"] = points
        return orig(points, a_)
    B.value_points = _spy
    try:
        B.run_backtest_dart(tickers, years=years, a=a, registry=registry, progress=progress)
    finally:
        B.value_points = orig
    points = captured.get("points", [])
    return points, BP.pipeline_points(points, a)


def _row_at(p: dict) -> dict:
    r = dict(p["row"])
    price, dps = r.get("price"), r.get("dps")
    r["div_yield"] = (dps / price * 100) if (dps and price) else 0.0
    return r


def stage2_inputs(p: dict, pipe_by: dict, fs_cache: dict) -> dict:
    """Số liệu tầng 2 tại mốc T của điểm p (chỉ dữ liệu ≤ Y−1)."""
    tk, y = p["row"]["ticker"], p["year"]
    key = (tk, y - 1)
    if key not in fs_cache:
        try:
            fs_cache[key] = AN.fetch_full(tk, y - 1)
        except Exception:
            fs_cache[key] = None
    fs = fs_cache[key]
    row = p["row"]
    bucket = P.identify(row)["bucket"]
    q = AN.quality(fs, (row["market_cap"] / 1e8) if row.get("market_cap") else None, bucket) if fs else {}
    pr = pipe_by.get((y, tk), {})
    return {"Ngành": row.get("industry_kr"), "Chất lượng TS": q.get("grade"), "F-score": q.get("fscore"),
            "Thanh toán hiện hành": q.get("current_ratio"), "Nợ/VCSH": q.get("debt_equity"),
            "Có năm lỗ": any((h.get("net_profit") or 0) <= 0 for h in p["hist"]),
            "Giá trị hợp lý": pr.get("fair")}


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": _st.mean(xs), "median": _st.median(xs)}


def run_presets(points: list[dict], pipe: list[dict], keys: Optional[list[str]] = None,
                fs_cache: Optional[dict] = None) -> dict:
    """Áp từng bộ tiêu chuẩn lên từng lát cắt năm, đo danh mục tầng 1 và tầng 2."""
    keys = keys or list(SC.PRESETS)
    fs_cache = {} if fs_cache is None else fs_cache
    pipe_by = {(r["year"], r["ticker"]): r for r in pipe}
    by_year: dict = {}
    for p in points:
        if p.get("ret") is not None and p.get("kospi_ret") is not None:
            by_year.setdefault(p["year"], []).append(p)
    out = {}
    for k in keys:
        yrs = []
        for y, pts in sorted(by_year.items()):
            rows = [_row_at(p) for p in pts]
            univ = _st.mean(p["ret"] for p in pts)
            kospi = pts[0]["kospi_ret"]
            st1 = {x["Mã"] for x in SC.apply_preset(rows, k) if x["Qua lọc"]}
            p1 = [p for p in pts if p["row"]["ticker"] in st1]
            p2 = [p for p in p1 if SC.stage2_check(stage2_inputs(p, pipe_by, fs_cache), k)[0]]
            rec = {"year": y, "n_univ": len(pts), "univ_ret": univ, "kospi_ret": kospi}
            for tag, grp in (("t1", p1), ("t2", p2)):
                rets = [p["ret"] for p in grp]
                rec[tag] = {"n": len(grp), "ret": _st.mean(rets) if rets else None,
                            "vs_univ": (_st.mean(rets) - univ) if rets else None,
                            "vs_kospi": (_st.mean(rets) - kospi) if rets else None,
                            "beat_univ": (sum(r > univ for r in rets) / len(rets)) if rets else None,
                            "loss30": (sum(r < -0.30 for r in rets) / len(rets)) if rets else None,
                            "tickers": [p["row"]["ticker"] for p in grp]}
            yrs.append(rec)
        summ = {}
        for tag in ("t1", "t2"):
            d = [r[tag]["vs_univ"] for r in yrs if r[tag]["vs_univ"] is not None]
            dk = [r[tag]["vs_kospi"] for r in yrs if r[tag]["vs_kospi"] is not None]
            t = (_st.mean(d) / (_st.stdev(d) / len(d) ** 0.5)) if len(d) >= 3 and _st.stdev(d) > 0 else None
            all_r = [p for r in yrs for p in r[tag]["tickers"]]
            summ[tag] = {"years": len(d), "mean_vs_univ": _st.mean(d) if d else None, "t": t,
                         "years_beat_univ": sum(x > 0 for x in d), "mean_vs_kospi": _st.mean(dk) if dk else None,
                         "avg_n": _st.mean(r[tag]["n"] for r in yrs) if yrs else 0,
                         "beat_univ": _st.mean(r[tag]["beat_univ"] for r in yrs if r[tag]["beat_univ"] is not None)
                         if any(r[tag]["beat_univ"] is not None for r in yrs) else None,
                         "loss30": _st.mean(r[tag]["loss30"] for r in yrs if r[tag]["loss30"] is not None)
                         if any(r[tag]["loss30"] is not None for r in yrs) else None,
                         "n_picks": len(all_r)}
        out[k] = {"years": yrs, "summary": summ}
    return out


# ---------------------------------------------------------------------------- báo cáo
def _p(x, d=1):
    return "—" if x is None else f"{x:+.{d}%}"


def report_markdown(res: dict, meta: str = "") -> str:
    L = ["# Backtest các bộ tiêu chuẩn sàng lọc (point-in-time)", ""]
    if meta:
        L += [f"_{meta}_", ""]
    L += ["## Tổng hợp — danh mục chia đều, giữ 12 tháng từ 15/4", "",
          "| Bộ | Tầng | Mã/năm | Vượt TB danh sách gốc/năm | t | Năm thắng TB | Vượt KOSPI TB/năm | Mã thắng TB | Mất >30% |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, v in res.items():
        for tag, lab in (("t1", "1 (thị trường)"), ("t2", "1+2 (đủ)")):
            s = v["summary"][tag]
            def pct0(x):
                return "—" if x is None else f"{x:.0%}"
            t = "—" if s["t"] is None else f"{s['t']:.2f}"
            L.append(f"| {SC.PRESETS[k]['name']} | {lab} | {s['avg_n']:.1f} | {_p(s['mean_vs_univ'])} | {t} | "
                     f"{s['years_beat_univ']}/{s['years']} | {_p(s['mean_vs_kospi'])} | "
                     f"{pct0(s['beat_univ'])} | {pct0(s['loss30'])} |")
    L += ["", "## Theo năm — tầng 1+2, vượt trung bình danh sách gốc (số mã)", ""]
    yrs = sorted({r["year"] for v in res.values() for r in v["years"]})
    L += ["| Bộ | " + " | ".join(str(y) for y in yrs) + " |", "|---|" + "---:|" * len(yrs)]
    for k, v in res.items():
        by = {r["year"]: r for r in v["years"]}
        L.append(f"| {SC.PRESETS[k]['name']} | " + " | ".join(
            f"{_p(by[y]['t2']['vs_univ'], 0)} ({by[y]['t2']['n']})" if y in by else "—" for y in yrs) + " |")
    L += ["", "_Lợi suất chỉ tính giá (không gồm cổ tức); danh sách gốc = top vốn hóa hiện tại (thiên lệch sống sót — vì vậy "
              "so với trung bình chính danh sách gốc); 5 năm là mẫu nhỏ, |t| < 2 coi như chưa có ý nghĩa thống kê._"]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from screener import TickerRegistry
    reg = TickerRegistry()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    tks = SC.top_by_cap("ALL", n, reg)
    pts, pipe = collect(tks, registry=reg, progress=lambda s, x: print(f"  [{x:4.0%}] {s}", flush=True))
    print(report_markdown(run_presets(pts, pipe), f"Danh sách gốc: top {len(tks)} vốn hóa hiện tại; {len(pts)} điểm PIT"))
