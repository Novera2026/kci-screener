"""
KCI — quy trình SÀNG LỌC cổ phiếu rẻ rồi ĐỊNH GIÁ SÂU danh sách rẻ (trang 🔍).

  ① Vũ trụ    : top vốn hóa (Naver marketValue) / ngành KRX / chủ đề / nhập tay
  ② Lọc rẻ    : P/E, P/B so với trung vị NGÀNH trong chính vũ trụ (≥ 3 mã cùng ngành, thiếu thì so cả danh sách),
                tỷ suất cổ tức, bắt buộc có lãi; điểm rẻ = trung bình (P/E ÷ ngành, P/B ÷ ngành) — càng thấp càng rẻ
  ③ Định giá sâu: mỗi mã chạy ĐÚNG quy trình trang 🧮 (giả định mặc định, cổng số, chất lượng tài sản, tổng quan)
                với đối thủ = các mã cùng ngành trong vũ trụ; lưu hồ sơ dossiers/ cho từng mã
  ④ Xếp hạng  : kết luận theo quy tắc số → chênh lệch → chất lượng

Bằng chứng: backtest 2021–25 (backtest_pipeline.py) — IC upside đã công bố +0,106/năm (t 3,2). Lọc rẻ đơn thuần
theo P/E thấp chưa được kiểm chứng riêng; nó chỉ để thu hẹp danh sách trước bước định giá.
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
            "Tổng quan": overall["total"], "Kết luận": overall["verdict"]}


def deep_stage(tickers: list[str], rows_by_tk: dict, universe_rows: list[dict], a: V.Assumptions, registry,
               progress=None, workers: int = 4) -> list[dict]:
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
    return rank(out)


def rank(results: list[dict]) -> list[dict]:
    """Kết luận (🟢 trước) → chênh lệch giảm dần → điểm chất lượng."""
    return sorted(results, key=lambda r: (_verdict_rank(r.get("Kết luận", "")),
                                          -(r.get("Chênh %") if r.get("Chênh %") is not None else -999),
                                          -(r.get("Điểm chất lượng") or 0)))
