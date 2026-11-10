"""
Backtest tăng trưởng giai đoạn đầu (g_high) của "DCF earnings" / DDM 2 giai đoạn: 8% cố định cho mọi mã (hiện tại)
so với các cách tính RIÊNG từng mã, point-in-time (chỉ dữ liệu ≤ Y−1).

  fixed8      : 8%/năm (hiện tại)
  sustainable : ROE × (1 − tỷ lệ cổ tức) năm gần nhất — tăng trưởng bền vững (sách định giá chuẩn), kẹp [0; 15%]
  hist_cagr   : CAGR doanh thu các năm thực ≤ Y−1, kẹp [0; 15%]
  forecast    : trung bình tăng trưởng doanh thu trong bảng dự phóng mặc định của quy trình, kẹp [0; 15%]
  no_excess   : bằng g dài hạn (không tăng trưởng vượt trội) — thận trọng nhất

Chấm IC (upside ↔ lợi suất vượt KOSPI 12 tháng) của KẾT LUẬN quy trình và của LÕI KCI.
"""
from __future__ import annotations

from typing import Optional

import backtest_pipeline as BP
import valuation as V

G_BAND = (0.0, 0.15)


def _clamp(x: Optional[float], lo=G_BAND[0], hi=G_BAND[1]) -> Optional[float]:
    return None if x is None else min(max(x, lo), hi)


def g_sustainable(row, hist, drv):
    last = hist[-1] if hist else {}
    eps, bps, dps = V._num(last.get("eps")), V._num(last.get("bps")), V._num(last.get("dps")) or 0.0
    if not eps or not bps or eps <= 0 or bps <= 0:
        return 0.0
    return _clamp(eps / bps * (1 - min(max(dps / eps, 0.0), 1.0)))


def g_hist_cagr(row, hist, drv):
    revs = [V._num(h.get("revenue")) for h in hist if V._num(h.get("revenue"))]
    if len(revs) < 3 or revs[0] <= 0 or revs[-1] <= 0:
        return 0.0
    return _clamp((revs[-1] / revs[0]) ** (1 / (len(revs) - 1)) - 1)


def g_forecast(row, hist, drv):
    gs = [V._num(d.get("Tăng trưởng DT %")) for d in (drv or []) if V._num(d.get("Tăng trưởng DT %")) is not None]
    return _clamp(sum(gs) / len(gs) / 100) if gs else 0.0


def g_no_excess(row, hist, drv):
    return V.Assumptions().g_term_capped()


VARIANTS = {"fixed8": None, "sustainable": g_sustainable, "hist_cagr": g_hist_cagr, "forecast": g_forecast,
            "no_excess": g_no_excess}


def run(points: list[dict], a: Optional[V.Assumptions] = None) -> dict:
    out = {}
    for name, fn in VARIANTS.items():
        pipe = BP.pipeline_points(points, a, g_high_fn=fn)
        pub = [r for r in pipe if r["published"]]
        dcf_pts = [r for r in pipe if "DCF earnings" in (r.get("base_method") or "")]
        out[name] = {"ic_conclusion": BP._ic(pub, "upside"), "ic_core": BP._ic(pipe, "base_upside"),
                     "ic_core_dcf_only": BP._ic(dcf_pts, "base_upside"), "n_published": len(pub),
                     "n_points": len(pipe), "n_dcf_auto": len(dcf_pts),
                     "median_g": sorted(r["g_high"] for r in pipe)[len(pipe) // 2] if pipe else None}
    return out


def report(res: dict, meta: str = "") -> str:
    def f(x, fmt="+.3f"):
        return "—" if x is None else format(x, fmt)
    L = ["# Backtest tăng trưởng giai đoạn đầu (g_high) — DCF earnings / DDM 2 giai đoạn", ""]
    if meta:
        L += [f"_{meta}_", ""]
    L += ["| Cách | g trung vị | IC kết luận (t) | Công bố | IC lõi KCI (t) | IC lõi — điểm dùng DCF earnings (n) |",
          "|---|---:|---:|---:|---:|---:|"]
    for k, v in res.items():
        c, b, d = v["ic_conclusion"], v["ic_core"], v["ic_core_dcf_only"]
        L.append(f"| {k} | {v['median_g'] * 100:.1f}% | {f(c['mean'])} ({f(c['t'], '.2f')}) | "
                 f"{v['n_published']}/{v['n_points']} | {f(b['mean'])} ({f(b['t'], '.2f')}) | "
                 f"{f(d['mean'])} ({d['n']}) |")
    return "\n".join(L)
