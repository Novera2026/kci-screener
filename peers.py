"""
KCI — Bước 1 của quy trình: so sánh đối thủ cùng ngành TRONG và NGOÀI nước, lưu vào hồ sơ cho các bước sau.

Nguồn:
  • Trong nước: danh sách cùng ngành của Naver (integration.industryCompareInfo) ∪ top vốn hóa cùng 업종 KRX;
    chỉ số từ screener.fetch_many + financials.fetch_financials (như phần còn lại của App).
  • Nước ngoài: API cổ phiếu thế giới của Naver (api.stock.naver.com/stock/{reutersCode}/basic, /finance/annual) —
    P/E, P/B, vốn hóa KRW, doanh thu/EBIT/LN ròng năm đã quy ra 억원. Danh sách đối thủ theo nhóm ngành do KCI
    chọn và đã kiểm mã (10/11/2026); người dùng thêm/bớt được trên trang.

Đối thủ nước ngoài CHỈ ĐỂ SO SÁNH, không vào phép neo định giá: cổ phiếu Hàn có chiết khấu kinh niên, trộn bội số
TSMC/Nvidia vào phép neo sẽ đẩy giá trị hợp lý lên có hệ thống. Thay vào đó báo riêng mức chiết khấu so với quốc tế.
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import math
from typing import Optional

import requests

import valuation as V

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
_WORLD = "https://api.stock.naver.com/stock"
_TIMEOUT = 10

# Mã Reuters đã kiểm trên Naver ngày 10/11/2026 (mã không trả dữ liệu đã bỏ).
FOREIGN_PEERS: dict[str, list[str]] = {
    "Semiconductor": ["TSM", "MU.O", "INTC.O", "NVDA.O", "AMAT.O"],
    "Battery": ["300750.SZ", "6752.T", "1211.HK", "ALB"],
    "Auto": ["7203.T", "GM", "F"],
    "Shipbuilding": ["600150.SS", "7011.T"],
    "Defense": ["LMT", "RTX", "NOC", "GD"],
    "Steel/Chemicals": ["NUE", "5401.T", "DOW", "MT", "LYB"],
    "Internet/Platform": ["GOOGL.O", "META.O", "0700.HK", "4689.T", "NTES.O"],
    "Bank": ["JPM", "8306.T", "BAC", "C", "8316.T"],
    "Brokerage": ["GS", "MS", "8604.T"],
    "Insurance": ["MET", "8766.T", "AIG", "PRU", "8725.T"],
    "Telecom": ["VZ", "T", "9432.T", "9433.T", "TMUS.O"],
    "Utility": ["NEE", "9501.T", "DUK", "SO", "9503.T"],
    "Pharma/Biotech": ["LLY", "PFE", "AMGN.O", "2269.HK", "REGN.O"],
    "Consumer/Retail": ["EL", "4911.T", "PG", "ULTA.O"],
    "Entertainment": ["WMG.O", "TME"],
    "Holdco": ["8058.T", "8001.T", "9984.T"],
}
METRICS = ["P/E", "P/B", "Tăng trưởng DT 3 năm %", "Biên LN HĐ %", "Biên LN ròng %", "ROE %", "Tỷ suất cổ tức %"]
LOWER_IS_CHEAPER = {"P/E", "P/B"}
PE_MAX = 200.0                    # P/E > 200 hoặc ≤ 0 → không đưa vào trung vị (vd Berkshire 0,01× trên Naver)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).replace(",", "").replace("배", "").replace("%", "").strip()
    if s in ("", "N/A", "-", "null"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _cagr(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2 or xs[0] <= 0 or xs[-1] <= 0:
        return None
    return ((xs[-1] / xs[0]) ** (1 / (len(xs) - 1)) - 1) * 100


# ---------------------------------------------------------------------------- hồ sơ 1 công ty
def domestic_profile(row: dict, annual: list[dict]) -> dict:
    """Từ dòng screener + BCTC năm Naver (đã có) → các chỉ số so sánh."""
    reals = [c for c in annual if not c.get("is_consensus")]
    last = reals[-1] if reals else {}
    pe, pb = V._num(row.get("per")), V._num(row.get("pbr"))
    roe = V._num(last.get("roe"))
    return {"Mã": row.get("ticker"), "Tên": row.get("name"), "Thị trường": "🇰🇷 Hàn Quốc",
            "Vốn hóa (tỷ KRW)": (row["market_cap"] / 1e9) if row.get("market_cap") else None,
            "P/E": pe, "P/B": pb,
            "Tăng trưởng DT 3 năm %": _cagr([c.get("revenue") for c in reals[-4:]]),
            "Biên LN HĐ %": V._num(last.get("op_margin")), "Biên LN ròng %": V._num(last.get("net_margin")),
            "ROE %": roe, "Tỷ suất cổ tức %": V._num(row.get("div_yield")), "Năm BCTC": str(last.get("period", ""))[:4]}


def foreign_profile(code: str) -> Optional[dict]:
    """Hồ sơ 1 mã nước ngoài từ Naver (giá trị tiền đã quy KRW). None nếu Naver không có."""
    try:
        b = requests.get(f"{_WORLD}/{code}/basic", headers=_H, timeout=_TIMEOUT).json()
        a = requests.get(f"{_WORLD}/{code}/finance/annual", headers=_H, timeout=_TIMEOUT).json()
    except Exception:
        return None
    if not b.get("stockName"):
        return None
    info = {t.get("code"): t.get("value") for t in b.get("stockItemTotalInfos", [])}
    cols = [t["key"] for t in a.get("trTitleList", []) if t.get("isConsensus") == "N"]
    rows = {r["title"]: r["columns"] for r in a.get("rowList", [])}

    def series(title):
        c = rows.get(title, {})
        return [_f((c.get(k) or {}).get("krw")) for k in cols]
    rev, ebit, ni = series("매출액"), series("EBIT"), series("당기순이익")
    pe, pb = _f(info.get("per")), _f(info.get("pbr"))
    last = len(cols) - 1
    rv = rev[last] if cols else None
    mc = _f(b.get("marketValueKrwRaw"))
    return {"Mã": code, "Tên": b.get("stockName"),
            "Thị trường": f"🌐 {b.get('nationName') or (b.get('stockExchangeType') or {}).get('nationName') or ''}".strip(),
            "Vốn hóa (tỷ KRW)": mc / 1e9 if mc else None, "P/E": pe, "P/B": pb,
            "Tăng trưởng DT 3 năm %": _cagr(rev),
            "Biên LN HĐ %": (ebit[last] / rv * 100) if (cols and rv and ebit[last] is not None) else None,
            "Biên LN ròng %": (ni[last] / rv * 100) if (cols and rv and ni[last] is not None) else None,
            # ROE ≈ P/B ÷ P/E (cùng giá, LN và vốn chủ gần nhất)
            "ROE %": (pb / pe * 100) if (pe and pb and pe > 0) else None,
            "Tỷ suất cổ tức %": _f(info.get("dividendYieldRatio")),
            "Năm BCTC": cols[-1][:4] if cols else ""}


# ---------------------------------------------------------------------------- danh sách đối thủ
def naver_domestic_peers(ticker: str) -> list[str]:
    """Danh sách đối thủ trong nước Naver gợi ý cho mã (integration.industryCompareInfo)."""
    try:
        j = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration", headers=_H,
                         timeout=_TIMEOUT).json()
    except Exception:
        return []
    return [str(x.get("itemCode")).zfill(6) for x in (j.get("industryCompareInfo") or [])
            if x.get("stockType") == "domestic" and x.get("itemCode") and str(x.get("itemCode")).zfill(6) != ticker]


def _plausible(p: dict) -> bool:
    pe = p.get("P/E")
    return pe is None or 0 < pe <= PE_MAX


def _median(xs):
    return V._median([x for x in xs if x is not None])


def peer_medians(profiles: list[dict]) -> dict:
    out = {}
    for m in METRICS:
        vals = [p.get(m) for p in profiles if p.get(m) is not None]
        if m == "P/E":
            vals = [v for v in vals if 0 < v <= PE_MAX]
        if m == "P/B":
            vals = [v for v in vals if v > 0]
        out[m] = _median(vals) if len(vals) >= 2 else None
    return out


def position(self_p: dict, dom: list[dict], med_dom: dict, med_for: dict) -> list[dict]:
    """Từng chỉ số: mã vs trung vị trong nước / nước ngoài + hạng phần trăm trong nước + nhận xét bằng số."""
    rows = []
    for m in METRICS:
        v = self_p.get(m)
        vals = sorted(p[m] for p in dom if p.get(m) is not None)
        pct = (sum(1 for x in vals if x < v) + 0.5 * sum(1 for x in vals if x == v)) / len(vals) * 100 \
            if (v is not None and vals) else None
        md, mf = med_dom.get(m), med_for.get(m)
        note = ""
        if v is not None and md:
            r = v / md if md else None
            if m in LOWER_IS_CHEAPER and v > 0:
                note = ("rẻ hơn" if r < 0.85 else "đắt hơn" if r > 1.15 else "ngang") + f" trong nước ({r:.2f}×)"
            elif m not in LOWER_IS_CHEAPER:
                d = v - md
                note = ("cao hơn" if d > 0 else "thấp hơn") + f" trung vị trong nước {abs(d):.1f} điểm"
        rows.append({"Chỉ số": m, "Mã": v, "Trung vị trong nước": md, "Trung vị nước ngoài": mf,
                     "Hạng trong nước (%)": pct, "Nhận xét": note})
    return rows


def foreign_discount(self_p: dict, med_for: dict) -> dict:
    """Chiết khấu so với đối thủ nước ngoài: P/E, P/B của mã ÷ trung vị nước ngoài − 1 (âm = rẻ hơn)."""
    out = {}
    for m in ("P/E", "P/B"):
        v, f = self_p.get(m), med_for.get(m)
        out[m] = (v / f - 1) * 100 if (v and f and v > 0) else None
    return out


def fetch_foreign(codes: list[str]) -> list[dict]:
    """Hồ sơ các mã nước ngoài (song song). Mã Naver không có bị bỏ."""
    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        return [p for p in ex.map(foreign_profile, codes) if p]


def domestic_candidates(ticker: str, industry_kr: Optional[str], registry, n: int = 12) -> list[str]:
    """Đối thủ trong nước: gợi ý của Naver ∪ top vốn hóa cùng 업종, CHỈ cổ phiếu thường có trong danh mục
    (bỏ cổ phiếu ưu đãi như 005935 của chính Samsung)."""
    import pipeline as _P
    known = set(registry.df["ticker"]) if registry is not None else None
    cands = naver_domestic_peers(ticker) + (_P.industry_peers(industry_kr, ticker, n) if industry_kr else [])
    out = [t for t in dict.fromkeys(cands) if t != ticker and (known is None or t in known)]
    return out[:n]


def build_peer_set(row: dict, annual: list[dict], bucket: Optional[str], dom_rows: list[dict],
                   dom_fins: dict, foreign_codes: Optional[list[str]] = None,
                   foreign_profiles: Optional[list[dict]] = None) -> dict:
    """Ghép đủ bước 1: hồ sơ mã + đối thủ trong nước (dom_rows/dom_fins đã tải) + nước ngoài (truyền sẵn
    foreign_profiles, hoặc tải theo foreign_codes / danh sách mặc định của nhóm ngành). Trả dict thuần JSON."""
    self_p = domestic_profile(row, annual)
    dom = [domestic_profile(r, (dom_fins.get(r["ticker"]) or {}).get("annual", [])) for r in dom_rows
           if r.get("ticker") != row.get("ticker")]
    if foreign_profiles is not None:
        fr = foreign_profiles
    else:
        fr = fetch_foreign(foreign_codes if foreign_codes is not None else FOREIGN_PEERS.get(bucket or "", []))
    fr_ok = [p for p in fr if _plausible(p)]
    med_dom, med_for = peer_medians(dom), peer_medians(fr_ok)
    return {"asof": _dt.datetime.now().isoformat(timespec="seconds"), "bucket": bucket, "self": self_p,
            "domestic": dom, "foreign": fr, "foreign_excluded": [p["Mã"] for p in fr if not _plausible(p)],
            "median_domestic": med_dom, "median_foreign": med_for,
            "position": position(self_p, dom, med_dom, med_for),
            "foreign_discount": foreign_discount(self_p, med_for)}
