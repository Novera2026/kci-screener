"""
KCI Backtest POINT-IN-TIME trên số gốc DART — không cần tài khoản KRX.

Port từ KCI-Valuation-Lab (lab/backtest_pit.py + lab/dart_fs.py), chạy bằng chính các module
định giá của app (valuation / financials / screener).

Tại mỗi mốc T = phiên đầu tiên ≥ 15/4 năm Y (BCTC năm Y−1 đã nộp hạn 31/3), chỉ dùng thông
tin có tại T:
  - BCTC DART năm ≤ Y−1 (mã tài khoản IFRS chuẩn, ưu tiên hợp nhất CFS), số cổ phiếu và cổ
    tức/cp của năm Y−1;
  - giá đóng cửa tại T (Naver, đã điều chỉnh chia tách), beta 78 tuần trước T so với KOSPI;
  - Rf = TPCP 10 năm (FRED) của tháng trước T; median P/E–P/B peer cùng ngành KRX tính từ
    chính lát cắt T.
Định giá bằng valuation.value_stock rồi so với lợi suất `horizon` tháng sau T.

Đo:
  1. Định giá: IC (tương quan hạng upside ↔ lợi suất vượt KOSPI), ngũ phân vị, tỷ lệ mất >30%.
  2. Dự phóng: financials.project_forecast (dữ liệu ≤ Y−1) dự báo năm Y so với DART thực,
     đặt cạnh mốc naive (năm Y = năm Y−1).

Giới hạn: vũ trụ do người dùng chọn (thường là mã còn niêm yết → thiên lệch sống sót);
lợi suất chỉ tính giá, không gồm cổ tức; DART chưa điều chỉnh chia tách → tự điều chỉnh khi
số cổ phiếu đổi > 1,5 lần theo tỷ lệ "tròn", không tròn (sáp nhập…) thì bỏ điểm.

Cache JSON trong .cache/ (BCTC năm đã nộp: vĩnh viễn; giá: theo ngày; Rf: theo tháng).
Chạy thử:  python backtest_dart.py 005930 000660 105560
"""
from __future__ import annotations

import bisect
import concurrent.futures as _cf
import dataclasses
import datetime as _dt
import json
import os
import statistics as _st
import threading
import time
from typing import Optional

import pandas as pd
import requests

import dart_api
import financials
import valuation as V
from screener import beta_vs_market

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, ".cache")
EOK = 1e8
HIST_YEARS = 5          # số năm BCTC chỉ-lịch-sử trước mốc đầu (chuẩn hóa chu kỳ, dự phóng)
ANCHOR_MMDD = "0415"    # mốc định giá hằng năm: sau hạn nộp báo cáo năm (31/3)


# ---------------------------------------------------------------------------
# DART: BCTC năm theo mã tài khoản IFRS chuẩn (bền hơn khớp tên tài khoản)
# ---------------------------------------------------------------------------
IDS = {
    "revenue": ("ifrs-full_Revenue",),
    "op_profit": ("dart_OperatingIncomeLoss",),
    "ni": ("ifrs-full_ProfitLoss",),
    "ni_parent": ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
    "equity_parent": ("ifrs-full_EquityAttributableToOwnersOfParent",),
    "equity": ("ifrs-full_Equity",),
    "eps": ("ifrs-full_BasicEarningsLossPerShare",),
}
SJ = {"BS": {"equity_parent", "equity"},
      "IS": {"revenue", "op_profit", "ni", "ni_parent", "eps"}}
VERSION = 2              # v2: số cổ phiếu đọc được cả nhãn '의결권 있는 주식' / dòng '합계'
_LOCK = threading.Lock()
_LAST = [0.0]
MIN_GAP = 0.08          # giây giữa 2 lần gọi DART (≈ 750 lần/phút, dưới giới hạn)


def _num(s) -> Optional[float]:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _dart_get(path: str, **params) -> dict:
    with _LOCK:
        wait = MIN_GAP - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()
    for attempt in range(3):
        try:
            return requests.get(f"https://opendart.fss.or.kr/api/{path}",
                                params=dict(crtfc_key=dart_api.api_key(), **params),
                                timeout=20).json()
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {"status": "ERR"}


def _statements(j: dict) -> dict:
    """fnlttSinglAcntAll → {field: KRW}. CIS tính như IS; mã cũ 'ifrs_' (trước 2018) chuẩn hóa."""
    out: dict = {}
    for it in j.get("list", []):
        sj = it.get("sj_div")
        sj = "IS" if sj == "CIS" else sj
        aid = it.get("account_id") or ""
        if aid.startswith("ifrs_"):
            aid = "ifrs-full_" + aid[5:]
        for f, ids in IDS.items():
            if aid in ids and f in SJ.get(sj, ()) and out.get(f) is None:
                out[f] = _num(it.get("thstrm_amount"))
        # DN không dùng mã chuẩn cho EPS ('-표준계정코드 미사용-'): khớp theo tên
        nm = (it.get("account_nm") or "").replace(" ", "")
        if out.get("eps") is None and sj == "IS" and "기본주당" in nm \
                and "우선주" not in nm and "희석" not in nm:
            out["eps"] = _num(it.get("thstrm_amount"))
    return out


def _add_shares(rec: dict, corp_code: str, year: int) -> dict:
    """Số cổ phiếu lưu hành (thường + ưu đãi) và cổ tức tiền mặt/cp cổ phiếu thường."""
    s = _dart_get("stockTotqySttus.json", corp_code=corp_code, bsns_year=str(year),
                  reprt_code="11011")
    # Nhãn dòng không thống nhất giữa DN: '보통주', '의결권 있는\n보통주', '의결권 있는 주식'…
    # → ưu tiên dòng '합계'; không có thì cộng các dòng cổ phiếu thường + ưu đãi.
    total = parts = 0.0
    for x in s.get("list", []) if s.get("status") == "000" else []:
        se = "".join((x.get("se") or "").split())
        v = _num(x.get("distb_stock_co")) or _num(x.get("istc_totqy")) or 0.0
        if se == "합계":
            total = v
        elif se.startswith(("보통주", "우선주", "의결권있는", "의결권없는")):
            parts += v
    rec["shares_out"] = total or parts or None
    a = _dart_get("alotMatter.json", corp_code=corp_code, bsns_year=str(year),
                  reprt_code="11011")
    for x in a.get("list", []) if a.get("status") == "000" else []:
        if "주당 현금배당금" in (x.get("se") or "") and \
                (x.get("stock_knd") or "").startswith("보통주"):
            rec["dps"] = _num(x.get("thstrm")) or 0.0
    rec["shares_checked"] = True
    return rec


def _fetch_year(corp_code: str, year: int, full: bool) -> dict:
    rec: dict = {"year": year, "v": VERSION}
    for fs in ("CFS", "OFS"):
        j = _dart_get("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year),
                      reprt_code="11011", fs_div=fs)
        if j.get("status") == "000":
            rec.update(_statements(j), fs_div=fs)
            break
        rec["status"] = j.get("status")
    if rec.get("fs_div") and full:
        _add_shares(rec, corp_code, year)
    return rec


def annual(ticker: str, year: int, full: bool = True, cache_dir: str = CACHE_DIR) -> dict:
    """BCTC năm `year` (KRW). full=False: bỏ số cổ phiếu/cổ tức (năm chỉ dùng làm lịch sử).
    Cache JSON; năm chưa có báo cáo chỉ cache khi đã quá hạn nộp (30/6 năm sau)."""
    d = os.path.join(cache_dir, "dart_fs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{ticker}_{year}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("v") == VERSION:
            if not full or rec.get("shares_checked") or not rec.get("fs_div"):
                return rec
            cc = dart_api.corp_code_of(ticker)
            if cc:
                _add_shares(rec, cc, year)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(rec, f, ensure_ascii=False)
            return rec
    cc = dart_api.corp_code_of(ticker)
    if not cc or not dart_api.api_key():
        return {"year": year, "status": "NO_CORP"}
    rec = _fetch_year(cc, year, full)
    final = rec.get("fs_div") or _dt.date.today() > _dt.date(year + 1, 6, 30)
    if final and rec.get("status") != "ERR":
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    return rec


# ---------------------------------------------------------------------------
# Dữ liệu thị trường
# ---------------------------------------------------------------------------
def price_history(symbol: str, start: str = "20140101",
                  cache_dir: str = CACHE_DIR) -> list[tuple[str, float]]:
    """[(YYYYMMDD, giá đóng cửa)] ngày từ Naver (đã điều chỉnh chia tách). Cache theo ngày."""
    d = os.path.join(cache_dir, "prices")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{symbol}_{start}_{_dt.date.today():%Y%m%d}.json")
    if os.path.exists(p):
        with open(p) as f:
            return [tuple(x) for x in json.load(f)]
    try:
        r = requests.get("https://api.finance.naver.com/siseJson.naver",
                         params={"symbol": symbol, "requestType": 1, "startTime": start,
                                 "endTime": _dt.date.today().strftime("%Y%m%d"),
                                 "timeframe": "day"},
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.naver.com/"}, timeout=20)
        rows = json.loads(r.text.replace("'", '"'))
        out = [(str(x[0]), float(x[4])) for x in rows[1:] if x and x[4] not in (None, "")]
    except Exception:
        out = []
    if out:
        with open(p, "w") as f:
            json.dump(out, f)
    return out


def fred_rf(cache_dir: str = CACHE_DIR) -> list[tuple[str, float]]:
    """[(YYYY-MM-01, lợi suất TPCP Hàn 10Y dạng thập phân)] từ FRED IRLTLT01KRM156N."""
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, f"fred_kr10y_{_dt.date.today():%Y%m}.json")
    if os.path.exists(p):
        with open(p) as f:
            return [tuple(x) for x in json.load(f)]
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=IRLTLT01KRM156N",
                         timeout=15)
        out = []
        for ln in r.text.strip().splitlines()[1:]:
            a = ln.split(",")
            if len(a) == 2 and a[1] not in (".", ""):
                out.append((a[0], float(a[1]) / 100))
    except Exception:
        out = []
    if out:
        with open(p, "w") as f:
            json.dump(out, f)
    return out


def close_on_or_after(series: list[tuple[str, float]], day: str, max_gap: int = 10):
    """(ngày, giá) của phiên đầu tiên ≥ day trong vòng max_gap ngày lịch, không có thì None."""
    dates = [d for d, _ in series]
    i = bisect.bisect_left(dates, day)
    if i >= len(series):
        return None
    d0 = _dt.datetime.strptime(day, "%Y%m%d").date()
    d1 = _dt.datetime.strptime(series[i][0], "%Y%m%d").date()
    return series[i] if (d1 - d0).days <= max_gap else None


def weekly_before(series, day: str, n: int = 78) -> list[float]:
    """Giá đóng cửa cuối mỗi tuần (ISO) trước `day`, tối đa n tuần."""
    wk: dict = {}
    for d, px in series:
        if d >= day:
            break
        y, w, _ = _dt.datetime.strptime(d, "%Y%m%d").isocalendar()
        wk[(y, w)] = px
    return [wk[k] for k in sorted(wk)][-n:]


def rf_at(rf: list[tuple[str, float]], day: str) -> Optional[float]:
    """Rf của tháng TRƯỚC tháng của `day` (số đã công bố tại thời điểm đó)."""
    key = f"{day[:4]}-{day[4:6]}-01"
    xs = [v for d, v in rf if d < key]
    return xs[-1] if xs else None


def add_months(day: str, months: int) -> str:
    """YYYYMMDD + n tháng (ngày kẹp ≤ 28)."""
    d = _dt.datetime.strptime(day, "%Y%m%d").date()
    m = d.month - 1 + months
    return _dt.date(d.year + m // 12, m % 12 + 1, min(d.day, 28)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Lát cắt point-in-time
# ---------------------------------------------------------------------------
NICE_FACTORS = (2, 2.5, 3, 4, 5, 10, 20, 25, 50)


def split_factor(ref_shares: Optional[float], shares: Optional[float], tol: float = 0.03):
    """Hệ số đưa số/cp DART về cùng cơ sở giá Naver (đã điều chỉnh chia tách/thưởng cổ phiếu).

    Số cổ phiếu đổi < 1,5 lần → 1 (mua lại/phát hành nhỏ). Đổi mạnh và khớp tỷ lệ "tròn"
    (2, 5, 10… hoặc nghịch đảo) trong ±3% → tỷ lệ đó. Đổi mạnh nhưng không tròn (sáp nhập,
    phát hành lớn) → None: không xác định được → bỏ điểm."""
    if not ref_shares or not shares:
        return 1.0
    r = ref_shares / shares
    if 1 / 1.5 < r < 1.5:
        return 1.0
    for f in NICE_FACTORS:
        for c in (f, 1 / f):
            if abs(r / c - 1) <= tol:
                return float(c)
    return None


def _hist_row(f: dict, adj: float) -> dict:
    """Bản ghi năm DART → định dạng financials của KCI (tiền 억, số/cp KRW)."""
    def e(x):
        return x / EOK if x is not None else None
    sh = f.get("shares_out")
    eq = f.get("equity_parent") or f.get("equity")
    return {"period": f"{f['year']}12", "is_consensus": False,
            "revenue": e(f.get("revenue")), "op_profit": e(f.get("op_profit")),
            "net_profit": e(f.get("ni_parent") if f.get("ni_parent") is not None
                            else f.get("ni")),
            "equity": e(eq),
            "eps": f["eps"] / adj if f.get("eps") is not None else None,
            "bps": eq / sh / adj if (eq and sh) else None,
            "dps": (f.get("dps") or 0.0) / adj}


def build_point(ticker: str, year: int, fs: dict, px_series, kospi, rf, registry,
                split_ref_shares: Optional[float], horizon_months: int = 12) -> Optional[dict]:
    """Dữ liệu tại mốc T của năm `year` (BCTC năm year−1). None nếu thiếu dữ liệu."""
    last = fs.get(year - 1) or {}
    if not last.get("fs_div") or not last.get("shares_out"):
        return None
    t = close_on_or_after(px_series, f"{year}{ANCHOR_MMDD}")
    if not t:
        return None
    t1 = close_on_or_after(px_series, add_months(t[0], horizon_months))
    k0 = close_on_or_after(kospi, t[0])
    k1 = close_on_or_after(kospi, t1[0]) if t1 else None
    adj = split_factor(split_ref_shares, last["shares_out"])
    if adj is None:
        return None
    hist = [_hist_row(fs[y], adj) for y in sorted(fs) if y <= year - 1 and fs[y].get("fs_div")]
    h = hist[-1]
    price = t[1]
    eps, bps = h["eps"], h["bps"]
    row = {"ticker": ticker, "name": registry.name_of(ticker) if registry else None,
           "sector": registry.sector_of(ticker) if registry else None,
           "industry_kr": registry.industry_of(ticker) if registry else None,
           "price": price, "eps": eps, "bps": bps, "dps": h["dps"],
           "per": price / eps if eps and eps > 0 else None,
           "pbr": price / bps if bps and bps > 0 else None,
           "market_cap": price * last["shares_out"] / adj}
    return {"year": year, "date": t[0], "row": row, "hist": hist[-6:],
            "stock_weekly": weekly_before(px_series, t[0]),
            "kospi_weekly": weekly_before(kospi, t[0]), "rf": rf_at(rf, t[0]),
            "fwd_date": t1[0] if t1 else None,
            "ret": (t1[1] / price - 1) if t1 else None,
            "kospi_ret": (k1[1] / k0[1] - 1) if (k0 and k1) else None, "split_adj": adj}


def value_points(points: list[dict], a: Optional[V.Assumptions] = None) -> list[dict]:
    """Định giá từng điểm bằng giả định `a` (Rf thay bằng Rf tại mốc), median peer cùng ngành
    tính trong cùng lát cắt năm, beta 78 tuần trước mốc."""
    a = a or V.Assumptions()
    by_year: dict = {}
    for p in points:
        by_year.setdefault(p["year"], []).append(p)
    out = []
    for year, pts in sorted(by_year.items()):
        rows = [p["row"] for p in pts]
        for p in pts:
            row = dict(p["row"])
            ik = row.get("industry_kr")
            peers = [r for r in rows if r.get("industry_kr") == ik and r["ticker"] != row["ticker"]]
            med_pe = V._robust_median([V._num(r.get("per")) for r in peers], 0.0, V.PE_CAP)
            med_pb = V._robust_median([V._num(r.get("pbr")) for r in peers], 0.0)
            row["beta"] = beta_vs_market(p["stock_weekly"], p["kospi_weekly"])
            fc = financials.project_forecast(p["hist"], row.get("market_cap"), row.get("price"))
            if fc and fc.get("eps"):
                row["fwd_eps"] = fc["eps"]
                row["fwd_roe"] = fc["eps"] / row["bps"] if row.get("bps") else None
            ap = dataclasses.replace(a, rf=p["rf"]) if p["rf"] is not None else a
            sec = V.effective_sector(row.get("sector"), ik)
            hist = p["hist"] if sec in V.CYCLICAL_SECTORS else None
            try:
                res = V.value_stock(row, ap, med_pe, med_pb, history=hist)
            except Exception:
                continue
            price = row["price"]
            out.append({
                "year": year, "date": p["date"], "ticker": row["ticker"],
                "name": row.get("name"), "industry": ik, "sector_eff": sec,
                "price": price, "fair": res.fair_value, "method": res.auto_method,
                "confidence": res.confidence,
                "upside": (res.fair_value / price - 1) if res.fair_value else None,
                "abs_err": abs(res.fair_value / price - 1) if res.fair_value else None,
                "method_upsides": {m: (x / price - 1) for m, x in res.methods.items()
                                   if x and price},
                "fwd_date": p["fwd_date"], "ret": p["ret"], "kospi_ret": p["kospi_ret"],
                "excess": (p["ret"] - p["kospi_ret"])
                if (p["ret"] is not None and p["kospi_ret"] is not None) else None,
                "beta": row["beta"], "rf": ap.rf, "split_adj": p["split_adj"]})
    return out


# ---------------------------------------------------------------------------
# Đo lường
# ---------------------------------------------------------------------------
def spearman(xs: list[float], ys: list[float], min_n: int = 8) -> Optional[float]:
    """Tương quan hạng Spearman (hạng trung bình khi trùng). None nếu < min_n điểm."""
    n = len(xs)
    if n < min_n:
        return None

    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def evaluate(vals: list[dict], key: str = "upside") -> dict:
    """IC theo năm + IC gộp, ngũ phân vị trong từng năm và tỷ lệ mất vốn cho tín hiệu `key`
    ('upside' = fair value chính, hoặc tên 1 phương pháp trong method_upsides)."""
    def sig(r):
        return r.get(key) if key == "upside" else (r.get("method_upsides") or {}).get(key)
    xs = [r for r in vals if sig(r) is not None and r.get("excess") is not None]
    ic_year = {}
    for y in sorted({r["year"] for r in xs}):
        g = [r for r in xs if r["year"] == y]
        ic = spearman([sig(r) for r in g], [r["excess"] for r in g])
        if ic is not None:
            ic_year[y] = round(ic, 3)
    ics = list(ic_year.values())
    mean_ic = _st.mean(ics) if ics else None
    t = (mean_ic / (_st.stdev(ics) / len(ics) ** 0.5)) \
        if len(ics) >= 3 and _st.stdev(ics) > 0 else None
    pooled = spearman([sig(r) for r in xs], [r["excess"] for r in xs])
    # ngũ phân vị trong từng năm (Q1 = upside cao nhất = "rẻ nhất"), năm ít hơn 5 điểm bỏ qua
    quint: dict = {q: [] for q in range(1, 6)}
    for y in sorted({r["year"] for r in xs}):
        g = sorted([r for r in xs if r["year"] == y], key=lambda r: -sig(r))
        if len(g) < 5:
            continue
        for i, r in enumerate(g):
            quint[min(5, i * 5 // len(g) + 1)].append(r)
    qrows = []
    for q, rs in quint.items():
        if rs:
            qrows.append({"q": q, "n": len(rs),
                          "mean_excess": _st.mean(r["excess"] for r in rs),
                          "median_excess": _st.median(r["excess"] for r in rs),
                          "median_ret": _st.median(r["ret"] for r in rs),
                          "loss30": sum(r["ret"] < -0.30 for r in rs) / len(rs),
                          "beat_mkt": sum(r["excess"] > 0 for r in rs) / len(rs)})
    return {"signal": key, "n": len(xs), "ic_by_year": ic_year, "mean_ic": mean_ic,
            "t_stat": t, "ic_pooled": pooled, "quintiles": qrows}


def forecast_accuracy(fs_by_ticker: dict, years) -> list[dict]:
    """project_forecast(dữ liệu ≤ Y−1) vs DART thực năm Y, cạnh mốc naive (năm Y = năm Y−1)."""
    out = []
    for tk, fs in fs_by_ticker.items():
        for y in years:
            if not (fs.get(y) or {}).get("fs_div"):
                continue
            hist = [_hist_row(fs[k], 1.0) for k in sorted(fs) if k <= y - 1 and fs[k].get("fs_div")]
            if len(hist) < 2:
                continue
            fc = financials.project_forecast(hist[-5:])
            act = _hist_row(fs[y], 1.0)
            naive = hist[-1]
            for m in ("revenue", "op_profit", "net_profit"):
                av = act.get(m)
                f_k, f_n = (fc or {}).get(m), naive.get(m)
                # chỉ so trên CÙNG cặp (mã, năm, chỉ tiêu) để công bằng giữa hai nguồn
                if av is None or abs(av) < 1e-9 or f_k is None or f_n is None:
                    continue
                for src, f in (("kci", f_k), ("naive", f_n)):
                    out.append({"ticker": tk, "year": y, "metric": m, "source": src,
                                "ape": abs(f - av) / abs(av), "err": (f - av) / abs(av)})
    return out


def accuracy_table(rows: list[dict]) -> list[dict]:
    g: dict = {}
    for r in rows:
        g.setdefault((r["metric"], r["source"]), []).append(r)
    out = []
    for (m, s), xs in sorted(g.items()):
        apes = [x["ape"] for x in xs]
        out.append({"metric": m, "source": s, "n": len(xs), "median_ape": _st.median(apes),
                    "bias_median": _st.median(x["err"] for x in xs),
                    "within_10pct": sum(a <= 0.10 for a in apes) / len(apes)})
    return out


def kci_vs_naive_wins(rows: list[dict]) -> dict:
    """Tỷ lệ cặp (mã, năm, chỉ tiêu) mà KCI sai ít hơn naive."""
    pair: dict = {}
    for r in rows:
        pair.setdefault((r["ticker"], r["year"], r["metric"]), {})[r["source"]] = r["ape"]
    res: dict = {}
    for (tk, y, m), d in pair.items():
        if "kci" in d and "naive" in d:
            res.setdefault(m, []).append(d["kci"] < d["naive"])
    return {m: sum(v) / len(v) for m, v in res.items()}


# ---------------------------------------------------------------------------
# Runner end-to-end
# ---------------------------------------------------------------------------
def run_backtest_dart(tickers: list[str], years: tuple[int, int] = (2021, 2025),
                      horizon_months: int = 12, a: Optional[V.Assumptions] = None,
                      registry=None, progress=None, cache_dir: str = CACHE_DIR,
                      workers: int = 4) -> tuple[pd.DataFrame, dict]:
    """Chạy backtest cho `tickers`, mốc 15/4 các năm years[0]..years[1].

    progress(text, frac) để cập nhật tiến trình. Trả (DataFrame điểm, kết quả đo lường).
    Cần DART_API_KEY. Lần đầu ~30–60 giây cho 15 mã × 5 năm, sau đó đọc cache."""
    t0 = time.time()
    y0, y1 = years
    today = _dt.date.today()
    last_fy = today.year - 1 if today >= _dt.date(today.year, 4, 1) else today.year - 2
    tickers = list(dict.fromkeys(str(t).strip().upper().zfill(6) for t in tickers if str(t).strip()))

    def _p(text, frac):
        if progress:
            progress(text, frac)

    # năm chỉ-lịch-sử (không cần số cổ phiếu) + năm làm mốc (cần số cổ phiếu, cổ tức)
    # + năm y1 để chấm dự phóng
    jobs = [(t, y, False) for t in tickers for y in range(y0 - 1 - HIST_YEARS, y0 - 1)]
    jobs += [(t, y, True) for t in tickers for y in range(y0 - 1, min(y1 - 1, last_fy) + 1)]
    jobs += [(t, y1, False) for t in tickers if y1 <= last_fy]
    fs: dict = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(annual, t, y, full, cache_dir): (t, y) for t, y, full in jobs}
        for i, fut in enumerate(_cf.as_completed(futs), 1):
            t, y = futs[fut]
            try:
                fs.setdefault(t, {})[y] = fut.result()
            except Exception:
                fs.setdefault(t, {})[y] = {"year": y, "status": "ERR"}
            if i % 10 == 0 or i == len(jobs):
                _p(f"BCTC DART: {i}/{len(jobs)} năm-mã", 0.7 * i / max(len(jobs), 1))
    have = sum(1 for t in fs for y in fs[t] if fs[t][y].get("fs_div"))

    _p("Giá lịch sử Naver + Rf FRED...", 0.75)
    start = f"{y0 - 2}0101"
    kospi = price_history("KOSPI", start, cache_dir)
    rf = fred_rf(cache_dir)
    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        px = dict(zip(tickers, ex.map(lambda t: price_history(t, start, cache_dir), tickers)))

    _p("Định giá từng mốc...", 0.9)
    points = []
    for t in tickers:
        f_t = fs.get(t, {})
        ref = next((f_t[y].get("shares_out") for y in sorted(f_t, reverse=True)
                    if f_t[y].get("shares_out")), None)
        for y in range(y0, y1 + 1):
            if _dt.date(y, 4, 15) > today:
                continue
            p = build_point(t, y, f_t, px.get(t) or [], kospi, rf, registry, ref,
                            horizon_months)
            if p:
                points.append(p)
    vals = value_points(points, a)

    res = {"auto": evaluate(vals)}
    methods = sorted({k for r in vals for k in (r.get("method_upsides") or {})})
    res["methods"] = {k: evaluate(vals, k) for k in methods}
    res["by_confidence"] = {c: evaluate([r for r in vals if r["confidence"] == c])
                            for c in ("High", "Medium", "Low")
                            if any(r["confidence"] == c for r in vals)}
    acc = forecast_accuracy(fs, range(y0, min(y1, last_fy) + 1))
    res["forecast"] = {"table": accuracy_table(acc), "kci_beats_naive": kci_vs_naive_wins(acc)}
    res["meta"] = {"tickers": len(tickers), "dart_years_ok": have, "dart_years": len(jobs),
                   "points": len(vals),
                   "points_with_return": sum(1 for r in vals if r["excess"] is not None),
                   "years": [y0, y1], "horizon_months": horizon_months,
                   "no_price": [t for t in tickers if not px.get(t)],
                   # mã không có điểm nào (vd tập đoàn tài chính: DART chưa có BCTC cấu trúc
                   # trước năm tài chính 2023)
                   "no_points": [t for t in tickers if not any(r["ticker"] == t for r in vals)],
                   "kospi_ok": bool(kospi), "rf_ok": bool(rf),
                   "split_adjusted_points": sum(1 for r in vals if r["split_adj"] != 1.0),
                   "seconds": round(time.time() - t0, 1)}
    _p("Xong", 1.0)
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "method_upsides"} for r in vals])
    return df, res


def _pct(x, d=1):
    return "—" if x is None else f"{x:+.{d}%}"


def report_markdown(res: dict) -> str:
    """Tóm tắt kết quả bằng Markdown tiếng Việt (dùng trong app và khi chạy dòng lệnh)."""
    m, a = res["meta"], res["auto"]
    L = [f"#### Backtest point-in-time — {m['points_with_return']} điểm có lợi suất, "
         f"{m['tickers']} mã, mốc 15/4 các năm {m['years'][0]}–{m['years'][1]}, "
         f"giữ {m['horizon_months']} tháng", "",
         "- **IC** = tương quan hạng giữa upside của mô hình và lợi suất vượt KOSPI sau đó. "
         "≈0: không dự báo được · >0,05: tín hiệu yếu · >0,10: tín hiệu tốt. Ít mã/ít năm thì "
         "rất nhiễu — xem thêm IC gộp và ngũ phân vị.",
         "- Vũ trụ là các mã anh nhập (thường đang còn niêm yết) → thiên lệch sống sót; lợi "
         "suất chỉ tính giá, không gồm cổ tức.", ""]
    ic = "—" if a["mean_ic"] is None else f"{a['mean_ic']:.3f}"
    t = "" if a.get("t_stat") is None else f" (t = {a['t_stat']:.2f})"
    pooled = "—" if a["ic_pooled"] is None else f"{a['ic_pooled']:.3f}"
    by_year = " · ".join(f"{y}: {v:+.3f}" for y, v in a["ic_by_year"].items()) \
        or "— (cần ≥8 mã/năm)"
    L += [f"**Fair value chính của KCI:** IC trung bình theo năm **{ic}**{t} · IC gộp mọi "
          f"năm **{pooled}** · IC từng năm: {by_year}", ""]
    if a["quintiles"]:
        L += ["| Ngũ phân vị (1 = rẻ nhất theo KCI) | n | Vượt KOSPI trung vị | Vượt KOSPI TB "
              "| Lợi suất trung vị | Mất >30% | Thắng thị trường |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
        for q in a["quintiles"]:
            L.append(f"| {q['q']} | {q['n']} | {_pct(q['median_excess'])} | "
                     f"{_pct(q['mean_excess'])} | {_pct(q['median_ret'])} | "
                     f"{q['loss30']:.0%} | {q['beat_mkt']:.0%} |")
    f = res["forecast"]
    if f["kci_beats_naive"]:
        names = {"revenue": "doanh thu", "op_profit": "LN hoạt động", "net_profit": "LN thuần"}
        L += ["", "**Dự phóng 1 năm của KCI sai ít hơn mốc naive (năm sau = năm trước) ở** " +
              ", ".join(f"{names.get(k, k)} {v:.0%}" for k, v in f["kci_beats_naive"].items()) +
              " số cặp (mã, năm)."]
    L += ["", "_Backtest đo chất lượng mô hình trong quá khứ; không bảo đảm kết quả tương lai "
          "và không phải khuyến nghị đầu tư._"]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from screener import TickerRegistry
    tks = sys.argv[1:] or ["005930", "000660", "035420", "105560", "055550", "005380",
                           "000270", "005490", "051910", "207940", "068270", "012450",
                           "015760", "017670"]
    if not dart_api.available():
        print("⚠️  Cần DART_API_KEY trong .env"); sys.exit(1)
    df, res = run_backtest_dart(tks, registry=TickerRegistry(),
                                progress=lambda s, x: print(f"  [{x:4.0%}] {s}", flush=True))
    print(report_markdown(res))
    print(json.dumps(res["meta"], ensure_ascii=False))
