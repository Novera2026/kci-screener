"""
KCI — quy trình SÀNG LỌC cổ phiếu rẻ rồi ĐỊNH GIÁ SÂU danh sách rẻ (trang 🔍).

  ① Vũ trụ    : top vốn hóa (Naver marketValue) / ngành KRX / chủ đề / nhập tay
  ② Lọc rẻ    : P/E, P/B so với trung vị NGÀNH trong chính vũ trụ (≥ 3 mã cùng ngành, thiếu thì so cả danh sách),
                tỷ suất cổ tức, bắt buộc có lãi; điểm rẻ = trung bình (P/E ÷ ngành, P/B ÷ ngành) — càng thấp càng rẻ
  ③ Định giá sâu: mỗi mã chạy ĐÚNG quy trình trang 🧮 (giả định mặc định, cổng số, chất lượng tài sản, tổng quan)
                với đối thủ = các mã cùng ngành trong vũ trụ; lưu hồ sơ dossiers/ cho từng mã
  ④ Xếp hạng  : kết luận theo quy tắc số → chênh lệch → chất lượng

Bằng chứng (backtest 2021–25, sau rà soát 10/11/2026): IC upside đã công bố +0,117 (78 mã, t 2,9) / +0,076 (top 200,
t 1,95) — xếp HẠNG có tín hiệu yếu. Nhưng MUA nhóm 'rẻ' (chênh > 0) trong top 200 thua trung bình vũ trụ ~7%/năm
(2023–25 thị trường dẫn bởi mã tăng trưởng đắt) — danh sách là để nghiên cứu, không phải danh mục mua.
"""
from __future__ import annotations

import concurrent.futures as _cf
import csv
import os
from typing import Optional

import requests

import analysis as AN
import dossier as DS
import peers as PR
import pipeline as P
import valuation as V

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
ROOT = os.path.dirname(os.path.abspath(__file__))
FIN_INDUSTRIES = {"은행", "증권", "생명보험", "손해보험", "카드", "기타금융", "창업투자"}


# ---------------------------------------------------------------------------- ① vũ trụ
def top_by_cap(market: str, n: int, registry) -> list[str]:
    """Top n cổ phiếu thường theo vốn hóa (Naver xếp sẵn giảm dần). market: KOSPI / KOSDAQ / ALL."""
    mkts = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    known = set(registry.df["ticker"])
    pairs = []
    for mk in mkts:
        page = 1
        while len([p for p in pairs if p[2] == mk]) < n and page <= 10:
            try:
                j = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}",
                                 params={"page": page, "pageSize": 100}, headers=_H, timeout=10).json()
            except Exception:
                break
            st = j.get("stocks") or []
            if not st:
                break
            for s in st:
                tk = str(s.get("itemCode") or "").zfill(6)
                if s.get("stockEndType") == "stock" and tk in known:
                    pairs.append((tk, float(s.get("marketValueRaw") or 0), mk))
            page += 1
    pairs.sort(key=lambda p: -p[1])
    return [p[0] for p in pairs[:n]]


def by_industry(industry_kr: str, registry) -> list[str]:
    return [str(t).zfill(6) for t in registry.df[registry.df["industry_kr"] == industry_kr]["ticker"]]


def by_theme(theme: str) -> list[str]:
    path = os.path.join(ROOT, "themes_all.csv")
    if not os.path.exists(path):
        return []
    return [str(r["ticker"]).zfill(6) for r in csv.DictReader(open(path, encoding="utf-8")) if r["theme_kr"] == theme]


# ---------------------------------------------------------------------------- ② lọc rẻ
def cheap_stage(rows: list[dict], max_pe: float = 15.0, max_rel: float = 0.8, min_div: float = 0.0,
                require_profit: bool = True, skip_financials: bool = False) -> list[dict]:
    """Chấm rẻ tương đối theo ngành TRONG vũ trụ. Trả mọi mã kèm cột 'Qua lọc' và lý do, xếp rẻ → đắt."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.get("industry_kr"), []).append(r)
    all_pe = V._robust_median([V._num(r.get("per")) for r in rows], 0.0, V.PE_CAP)
    all_pb = V._robust_median([V._num(r.get("pbr")) for r in rows], 0.0)
    out = []
    for r in rows:
        ik = r.get("industry_kr")
        g = [x for x in groups.get(ik, []) if x["ticker"] != r["ticker"]]
        pe_m = V._robust_median([V._num(x.get("per")) for x in g], 0.0, V.PE_CAP)
        pb_m = V._robust_median([V._num(x.get("pbr")) for x in g], 0.0)
        scope = "ngành"
        if pe_m is None and pb_m is None:
            pe_m, pb_m, scope = all_pe, all_pb, "cả danh sách"
        pe, pb, dy = V._num(r.get("per")), V._num(r.get("pbr")), V._num(r.get("div_yield")) or 0.0
        rel_pe = pe / pe_m if (pe and pe > 0 and pe_m) else None
        rel_pb = pb / pb_m if (pb and pb > 0 and pb_m) else None
        rels = [x for x in (rel_pe, rel_pb) if x is not None]
        score = sum(rels) / len(rels) if rels else None
        why = []
        if require_profit and not (pe and pe > 0):
            why.append("lỗ / không có P/E")
        if pe and pe > max_pe:
            why.append(f"P/E {pe:.1f} > {max_pe:g}")
        if score is None or score > max_rel:
            why.append(f"không rẻ hơn {scope} (điểm {score:.2f})" if score is not None else "thiếu so sánh")
        if dy < min_div:
            why.append(f"cổ tức {dy:.1f}% < {min_div:g}%")
        if skip_financials and ik in FIN_INDUSTRIES:
            why.append("ngành tài chính (đã loại)")
        out.append({"Mã": r["ticker"], "Tên": r.get("name"), "Ngành": ik, "Giá": V._num(r.get("price")),
                    "P/E": pe, "P/B": pb, "P/E ÷ ngành": rel_pe, "P/B ÷ ngành": rel_pb, "So với": scope,
                    "Cổ tức %": dy, "Điểm rẻ": score, "Qua lọc": not why, "Lý do loại": "; ".join(why)})
    return sorted(out, key=lambda x: (not x["Qua lọc"], x["Điểm rẻ"] if x["Điểm rẻ"] is not None else 9))


# ---------------------------------------------------------------------------- ③ định giá sâu
VERDICT_ORDER = ["🟢", "🟡", "⚪ Giá gần", "🔴 Giá cao", "🔴 Rủi ro", "⚪ Chưa"]


def _verdict_rank(v: str) -> int:
    return next((i for i, k in enumerate(VERDICT_ORDER) if v.startswith(k)), len(VERDICT_ORDER))


def analyze_one(tk: str, row: dict, universe_rows: list[dict], a: V.Assumptions, registry,
                kospi_weekly: list[float], save: bool = True) -> dict:
    """Toàn bộ quy trình 🧮 cho 1 mã với giả định mặc định (không đối thủ nước ngoài để nhanh)."""
    from financials import assess_financials, build_annual_series, fetch_financials
    from screener import beta_vs_market, fetch_weekly_closes
    row = dict(row)
    fin = fetch_financials(tk)
    series = build_annual_series(tk, fin.get("annual", []), row.get("market_cap"), row.get("price"), years=6,
                                 quarters=fin.get("quarter"))
    quarters = fin.get("quarter", [])
    try:
        row["beta"] = beta_vs_market(fetch_weekly_closes(tk), kospi_weekly) if kospi_weekly else None
    except Exception:
        row["beta"] = None
    idn = P.identify(row)
    dq = P.data_quality(row, series, quarters, row["beta"])
    ev = P.reported_evidence(series, quarters)
    drv = P.default_drivers(series, row, a, years=a.years_high, quarters=quarters, cyclical=idn["cyclical"],
                            financial=idn["financial"])
    if drv:
        drv[0].pop("_basis", None)
    peers_same = [r for r in universe_rows if r.get("industry_kr") == row.get("industry_kr") and r["ticker"] != tk]
    D = {"row": row, "series": series, "quarters": quarters, "extras": {}, "peers": peers_same, "ticker": tk}
    E = P.evaluate(D, a, drv, idn, dq, ev)
    try:
        fs = AN.fetch_full(tk)
    except Exception:
        fs = None
    mc = row.get("market_cap")
    quality = AN.quality(fs, mc / 1e8 if mc else None, idn["bucket"]) if fs else None
    fh = assess_financials(fin.get("annual", []), row.get("sector"), row.get("industry_kr"))
    ps = PR.build_peer_set(row, fin.get("annual", []), idn["bucket"], peers_same, {}, foreign_profiles=[])
    overall = P.overall_assessment(E["rec"], fh, quality, ps)
    rec = E["rec"]
    if save:
        try:
            meta = {"name": row.get("name"), "industry_kr": row.get("industry_kr"), "bucket": idn["bucket"],
                    "price": row.get("price"), "source": "sàng lọc"}
            DS.save_section(tk, "valuation", {"fair": rec["fair"], "low": rec["low"], "high": rec["high"],
                                              "upside": rec["upside"], "confidence": rec["confidence"],
                                              "status": rec["status"], "models": rec["rows"]}, meta=meta)
            if quality:
                DS.save_section(tk, "quality", quality)
            DS.save_section(tk, "overall", overall)
        except Exception:
            pass
    return {"Mã": tk, "Tên": row.get("name"), "Ngành": row.get("industry_kr"), "Nhóm": idn["bucket"],
            "Giá": row.get("price"), "Giá trị hợp lý": rec["fair"], "Chênh %": rec["upside"],
            "Độ tin cậy": rec["confidence"] if rec["fair"] else "—", "Trạng thái": rec["status"],
            "Chất lượng TS": quality["grade"] if quality else "—",
            "Điểm chất lượng": quality["score"] if quality else None,
            "Tổng quan": overall["total"], "Kết luận": overall["verdict"],
            # số liệu cho tầng 2 của bộ tiêu chuẩn
            "F-score": (quality or {}).get("fscore"), "Thanh toán hiện hành": (quality or {}).get("current_ratio"),
            "Nợ/VCSH": (quality or {}).get("debt_equity"),
            "Có năm lỗ": any((c.get("net_profit") or 0) <= 0 for c in series if not c.get("is_consensus"))}


def deep_stage(tickers: list[str], rows_by_tk: dict, universe_rows: list[dict], a: V.Assumptions, registry,
               progress=None, workers: int = 4, preset: Optional[str] = None,
               overrides2: Optional[dict] = None) -> list[dict]:
    from screener import fetch_weekly_closes
    kospi = fetch_weekly_closes("KOSPI")
    out, done = [], 0

    def _one(tk):
        try:
            return analyze_one(tk, rows_by_tk[tk], universe_rows, a, registry, kospi)
        except Exception as e:                       # một mã lỗi không làm hỏng cả danh sách
            return {"Mã": tk, "Tên": rows_by_tk[tk].get("name"), "Kết luận": f"⚪ Chưa định giá được ({str(e)[:60]})"}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_one, tickers):
            out.append(r)
            done += 1
            if progress:
                progress(done, len(tickers))
    if preset:
        for r in out:
            ok, why = stage2_check(r, preset, overrides2)
            r["Đạt tiêu chuẩn"], r["Lý do chưa đạt"] = ("✅" if ok else "❌"), why
    return rank(out)


def rank(results: list[dict]) -> list[dict]:
    """Đạt tiêu chuẩn tầng 2 trước → kết luận (🟢 trước) → chênh lệch giảm dần → điểm chất lượng."""
    return sorted(results, key=lambda r: (r.get("Đạt tiêu chuẩn") == "❌",
                                          _verdict_rank(r.get("Kết luận", "")),
                                          -(r.get("Chênh %") if r.get("Chênh %") is not None else -999),
                                          -(r.get("Điểm chất lượng") or 0)))


# ============================================================================ BỘ TIÊU CHUẨN "RẺ" CÓ SẴN
# Mỗi bộ = tầng 1 (dữ liệu thị trường, lọc nhanh cả vũ trụ) + tầng 2 (kiểm sau khi định giá sâu, cần BCTC DART).
# Ghi rõ chỗ điều chỉnh so với bản gốc vì dữ liệu Hàn Quốc / dữ liệu App có.
PRESETS: dict[str, dict] = {
    "kci": {
        "name": "KCI — rẻ so với ngành & chất lượng (khuyến nghị)",
        "source": "Quy tắc nhà KCI; phần định giá đã backtest 2021–25 (IC +0,117 trên 78 mã (t 2,9) · +0,076 trên top 200 (t 1,95)).",
        "desc": "Có lãi, ROE ≥ 8%, điểm rẻ (P/E và P/B ÷ trung vị ngành) ≤ 0,8 → sau định giá: chất lượng tài sản "
                "không hạng D, có giá trị hợp lý được công bố.",
        "stage1": {"require_profit": True, "max_rel": 0.8, "min_roe": 8.0},
        "stage2": {"min_grade": "C", "need_fair": True},
    },
    "graham": {
        "name": "Graham phòng thủ",
        "source": "B. Graham, The Intelligent Investor (1973), chương 14 — tiêu chuẩn cho nhà đầu tư phòng thủ.",
        "desc": "P/E ≤ 15, P/B ≤ 1,5, P/E × P/B ≤ 22,5, có trả cổ tức, vốn hóa ≥ 1.000 tỷ KRW → sau định giá: "
                "thanh toán hiện hành ≥ 1,5, có lãi mọi năm trong dữ liệu.",
        "adapt": "Graham dùng LN trung bình 3 năm và thanh toán hiện hành ≥ 2, cổ tức 20 năm, LN 10 năm; App dùng "
                 "P/E hiện tại, thanh toán hiện hành ≥ 1,5 (DN Hàn ít giữ tài sản ngắn hạn hơn DN Mỹ thập niên 70), "
                 "có cổ tức hiện tại và có lãi mọi năm trong 4–6 năm dữ liệu.",
        "stage1": {"require_profit": True, "max_pe": 15.0, "max_pb": 1.5, "max_pe_pb": 22.5, "min_div": 0.01,
                   "min_mcap": 1e12},
        "stage2": {"min_current_ratio": 1.5, "no_loss_years": True},
    },
    "graham_number": {
        "name": "Số Graham (giá ≤ √(22,5 × EPS × BPS))",
        "source": "Công thức 'Graham number' suy từ hai ngưỡng P/E ≤ 15 và P/B ≤ 1,5 của Graham.",
        "desc": "EPS > 0, BPS > 0, giá ≤ số Graham; xếp theo biên an toàn (số Graham ÷ giá − 1) → sau định giá: "
                "chất lượng tài sản không hạng D.",
        "stage1": {"require_profit": True, "max_pe_pb": 22.5, "sort": "graham_margin"},
        "stage2": {"min_grade": "C"},
    },
    "valueup": {
        "name": "P/B thấp – ROE cao (Value-up Hàn Quốc)",
        "source": "Chương trình Corporate Value-up (FSC/KRX, 2024) nhắm DN P/B < 1; lọc thêm ROE để tránh bẫy giá trị.",
        "desc": "P/B ≤ 1,0, ROE ≥ 10%, có lãi → sau định giá: chất lượng tài sản không hạng D, nợ/VCSH ≤ 200% "
                "(trừ ngành tài chính).",
        "stage1": {"require_profit": True, "max_pb": 1.0, "min_roe": 10.0},
        "stage2": {"min_grade": "C", "max_debt_equity": 2.0},
    },
    "dividend": {
        "name": "Cổ tức cao bền vững",
        "source": "Tiêu chí lợi suất cổ tức phổ biến; tỷ lệ chi trả ≈ tỷ suất cổ tức × P/E.",
        "desc": "Tỷ suất cổ tức ≥ 4%, P/E ≤ 15 (tỷ lệ chi trả ≤ ~60–70%), ROE ≥ 6% → sau định giá: có lãi mọi năm, "
                "chất lượng tài sản không hạng D.",
        "stage1": {"require_profit": True, "min_div": 4.0, "max_pe": 15.0, "min_roe": 6.0, "max_payout": 0.7},
        "stage2": {"min_grade": "C", "no_loss_years": True},
    },
    "magic": {
        "name": "Magic Formula (biến thể Greenblatt)",
        "source": "J. Greenblatt, The Little Book That Beats the Market (2005).",
        "desc": "Bỏ tài chính & tiện ích, vốn hóa ≥ 500 tỷ KRW; xếp hạng tổng (hạng lợi suất LN E/P + hạng ROE), lấy "
                "30 mã đầu → sau định giá: chất lượng tài sản không hạng D.",
        "adapt": "Bản gốc dùng EBIT/EV và ROIC; dữ liệu thị trường của App có E/P và ROE nên dùng hai chỉ số này.",
        "stage1": {"require_profit": True, "exclude_fin": True, "exclude_util": True, "min_mcap": 5e11,
                   "magic_top": 30},
        "stage2": {"min_grade": "C"},
    },
    "piotroski": {
        "name": "Piotroski — P/B thấp + F-score cao",
        "source": "J. Piotroski (2000), 'Value Investing: The Use of Historical Financial Statement Information'.",
        "desc": "20% mã có P/B thấp nhất vũ trụ → sau định giá: F-score ≥ 7/8.",
        "adapt": "Bản gốc 9 tiêu chí, chọn F ≥ 8; App tính 8 tiêu chí (thiếu phát hành cổ phiếu) nên dùng ≥ 7/8.",
        "stage1": {"pb_bottom_pct": 0.20},
        "stage2": {"min_fscore": 7},
    },
}
UTIL_INDUSTRIES = {"전기유틸리티", "가스유틸리티", "복합유틸리티"}
GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


def _metrics(r: dict) -> dict:
    pe, pb = V._num(r.get("per")), V._num(r.get("pbr"))
    eps, bps = V._num(r.get("eps")), V._num(r.get("bps"))
    price = V._num(r.get("price"))
    roe = (eps / bps * 100) if (eps is not None and bps and bps > 0) else None
    gn = (22.5 * eps * bps) ** 0.5 if (eps and bps and eps > 0 and bps > 0) else None
    return {"pe": pe, "pb": pb, "roe": roe, "div": V._num(r.get("div_yield")) or 0.0,
            "mcap": V._num(r.get("market_cap")), "ey": (1 / pe) if (pe and pe > 0) else None,
            "graham_margin": (gn / price - 1) if (gn and price) else None}


def apply_preset(rows: list[dict], key: str, overrides: Optional[dict] = None) -> list[dict]:
    """Tầng 1 của bộ tiêu chuẩn `key` trên toàn vũ trụ. overrides: sửa ngưỡng (vd {'max_pe': 12})."""
    crit = {**PRESETS[key]["stage1"], **(overrides or {})}
    base = {r["Mã"]: r for r in cheap_stage(rows, max_pe=1e9, max_rel=1e9, min_div=0.0, require_profit=False)}
    pb_cut = None
    if crit.get("pb_bottom_pct"):
        pbs = sorted(m for m in (_metrics(r)["pb"] for r in rows) if m and m > 0)
        if pbs:
            pb_cut = pbs[max(0, int(len(pbs) * crit["pb_bottom_pct"]) - 1)]
    out = []
    for r in rows:
        m = _metrics(r)
        ik = r.get("industry_kr")
        why = []
        if crit.get("require_profit") and not (m["pe"] and m["pe"] > 0):
            why.append("lỗ / không có P/E")
        if crit.get("max_pe") and m["pe"] and m["pe"] > crit["max_pe"]:
            why.append(f"P/E {m['pe']:.1f} > {crit['max_pe']:g}")
        if crit.get("max_pb") and (m["pb"] is None or m["pb"] > crit["max_pb"]):
            why.append(f"P/B {m['pb'] or 0:.2f} > {crit['max_pb']:g}")
        if crit.get("max_pe_pb") and not (m["pe"] and m["pb"] and 0 < m["pe"] * m["pb"] <= crit["max_pe_pb"]):
            why.append(f"P/E × P/B > {crit['max_pe_pb']:g}")
        if crit.get("min_roe") and (m["roe"] is None or m["roe"] < crit["min_roe"]):
            why.append(f"ROE {m['roe'] or 0:.1f}% < {crit['min_roe']:g}%")
        if crit.get("min_div") and m["div"] < crit["min_div"]:
            why.append("không trả cổ tức" if crit["min_div"] < 0.1 else f"cổ tức {m['div']:.1f}% < {crit['min_div']:g}%")
        if crit.get("max_payout") and m["pe"] and m["div"] * m["pe"] / 100 > crit["max_payout"]:
            why.append(f"tỷ lệ chi trả ≈ {m['div'] * m['pe']:.0f}% > {crit['max_payout'] * 100:.0f}%")
        if crit.get("min_mcap") and (m["mcap"] or 0) < crit["min_mcap"]:
            why.append(f"vốn hóa < {crit['min_mcap'] / 1e9:,.0f} tỷ")
        if crit.get("exclude_fin") and ik in FIN_INDUSTRIES:
            why.append("ngành tài chính")
        if crit.get("exclude_util") and ik in UTIL_INDUSTRIES:
            why.append("ngành tiện ích")
        if pb_cut is not None and not (m["pb"] and 0 < m["pb"] <= pb_cut):
            why.append(f"P/B ngoài {crit['pb_bottom_pct']:.0%} thấp nhất (≤ {pb_cut:.2f})")
        b = base.get(r["ticker"], {})
        if crit.get("max_rel") and (b.get("Điểm rẻ") is None or b["Điểm rẻ"] > crit["max_rel"]):
            why.append(f"không rẻ hơn {b.get('So với', 'ngành')} (điểm {b['Điểm rẻ']:.2f})" if b.get("Điểm rẻ")
                       else "thiếu so sánh ngành")
        out.append({**b, "ROE %": m["roe"], "Lợi suất LN (E/P) %": m["ey"] * 100 if m["ey"] else None,
                    "Biên an toàn Graham %": m["graham_margin"] * 100 if m["graham_margin"] is not None else None,
                    "Vốn hóa (tỷ KRW)": m["mcap"] / 1e9 if m["mcap"] else None,
                    "Qua lọc": not why, "Lý do loại": "; ".join(why)})
    # Magic Formula: xếp hạng tổng trong nhóm qua điều kiện, chỉ giữ magic_top mã đầu
    if crit.get("magic_top"):
        ok = [x for x in out if x["Qua lọc"] and x["Lợi suất LN (E/P) %"] and x["ROE %"] is not None]
        r_ey = {x["Mã"]: i for i, x in enumerate(sorted(ok, key=lambda x: -x["Lợi suất LN (E/P) %"]))}
        r_roe = {x["Mã"]: i for i, x in enumerate(sorted(ok, key=lambda x: -x["ROE %"]))}
        for x in out:
            if x["Mã"] in r_ey:
                x["Hạng Magic"] = r_ey[x["Mã"]] + r_roe[x["Mã"]] + 2
        keep = {x["Mã"] for x in sorted((x for x in ok), key=lambda x: x["Hạng Magic"])[:crit["magic_top"]]}
        for x in out:
            if x["Qua lọc"] and x["Mã"] not in keep:
                x["Qua lọc"], x["Lý do loại"] = False, f"ngoài top {crit['magic_top']} Magic Formula"
    sort = crit.get("sort")

    def key_fn(x):
        if crit.get("magic_top"):
            v = x.get("Hạng Magic") or 9999
        elif sort == "graham_margin":
            v = -(x.get("Biên an toàn Graham %") or -999)
        else:
            v = x.get("Điểm rẻ") if x.get("Điểm rẻ") is not None else 9
        return (not x["Qua lọc"], v)
    return sorted(out, key=key_fn)


def stage2_check(res: dict, key: str, overrides: Optional[dict] = None) -> tuple[bool, str]:
    """Tầng 2 (sau định giá sâu). res = 1 dòng của deep_stage (có các cột số liệu DART)."""
    crit = {**PRESETS[key]["stage2"], **(overrides or {})}
    why = []
    if crit.get("need_fair") and not res.get("Giá trị hợp lý"):
        why.append("không có giá trị hợp lý qua cổng")
    g = res.get("Chất lượng TS")
    if crit.get("min_grade") and GRADE_ORDER.get(g, 0) < GRADE_ORDER[crit["min_grade"]]:
        why.append(f"chất lượng tài sản {g or '—'} < {crit['min_grade']}")
    if crit.get("min_fscore") is not None:
        f = res.get("F-score")
        if f is None or f < crit["min_fscore"]:
            why.append(f"F-score {f if f is not None else '—'} < {crit['min_fscore']}")
    if crit.get("min_current_ratio"):
        cr = res.get("Thanh toán hiện hành")
        fin = res.get("Ngành") in FIN_INDUSTRIES
        if not fin and (cr is None or cr < crit["min_current_ratio"]):
            why.append(f"thanh toán hiện hành {cr or 0:.2f} < {crit['min_current_ratio']}")
    if crit.get("max_debt_equity") and res.get("Ngành") not in FIN_INDUSTRIES:
        de = res.get("Nợ/VCSH")
        if de is not None and de > crit["max_debt_equity"]:
            why.append(f"nợ/VCSH {de * 100:.0f}% > {crit['max_debt_equity'] * 100:.0f}%")
    if crit.get("no_loss_years") and res.get("Có năm lỗ"):
        why.append("có năm lỗ trong dữ liệu")
    return (not why, "; ".join(why))


# Kết quả backtest_presets.py (chạy lại 10/11/2026 sau rà soát định giá): top 200 vốn hóa hiện tại, mốc 15/4/2021–2025, giữ 12 tháng, tầng 1+2,
# danh mục chia đều. vs_univ = vượt trung bình vũ trụ cùng năm (TB/năm); t = t-thống kê theo năm; |t| < 2 = chưa có
# ý nghĩa thống kê. Hiện cạnh mỗi bộ trên trang 🔍 để người dùng thấy bằng chứng trước khi chọn.
BACKTEST_2021_2025 = {
    "kci": {"vs_univ": -0.0043, "t": -0.10, "years_beat": "1/5", "vs_kospi": 0.1136, "avg_n": 20.4, "loss30": 0.085},
    "graham": {"vs_univ": -0.0774, "t": -1.36, "years_beat": "1/5", "vs_kospi": 0.0406, "avg_n": 19.4, "loss30": 0.007},
    "graham_number": {"vs_univ": -0.0290, "t": -0.90, "years_beat": "2/5", "vs_kospi": 0.0889, "avg_n": 61.4, "loss30": 0.054},
    "valueup": {"vs_univ": 0.0650, "t": 1.32, "years_beat": "4/5", "vs_kospi": 0.1829, "avg_n": 12.6, "loss30": 0.027},
    "dividend": {"vs_univ": 0.0196, "t": 0.15, "years_beat": "2/5", "vs_kospi": 0.1375, "avg_n": 12.8, "loss30": 0.008},
    "magic": {"vs_univ": 0.0315, "t": 0.75, "years_beat": "3/5", "vs_kospi": 0.1494, "avg_n": 28.8, "loss30": 0.097},
    "piotroski": {"vs_univ": -0.1898, "t": -1.04, "years_beat": "2/4", "vs_kospi": -0.0985, "avg_n": 3.6, "loss30": 0.0},
}
