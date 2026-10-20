"""
KCI Growth — quét "siêu cổ phiếu tăng trưởng" (SEPA của Minervini + CAN SLIM rút gọn).

3 trụ, mỗi trụ chấm theo QUY TẮC MINH BẠCH (đạt/không đạt từng tiêu chí):
  1. Biểu đồ  — Trend Template 8 tiêu chí (MA50/150/200, đỉnh/đáy 52 tuần, RS),
                kèm tín hiệu phụ: tích lũy khối lượng, nền siết (biên độ co lại), gần đỉnh.
  2. Tăng trưởng — doanh thu / LN thuần quý so CÙNG KỲ (YoY), biên LN HĐ mở rộng,
                tăng trưởng năm, dự phóng đồng thuận năm tới.
  3. Tin tức  — số tin gần đây + từ khóa xúc tác tích cực / tiêu cực trong tiêu đề.

Nguồn: Naver (xếp hạng vốn hóa, nến ngày, BCTC quý/năm, tin tức).
Toss (tùy chọn): giá realtime + cảnh báo mã (투자경고, 단기과열, VI…).
Đây là BỘ LỌC, không phải khuyến nghị đầu tư.
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import html
import json
from typing import Optional

import requests

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
_TIMEOUT = 10


def _num(x) -> Optional[float]:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# Vũ trụ quét: xếp hạng vốn hóa (đã lọc ETF/ETN qua stockEndType)
# ----------------------------------------------------------------------------
def universe_by_cap(min_cap_eok: float = 1000, markets=("KOSPI", "KOSDAQ"),
                    max_pages: int = 30) -> list[dict]:
    """Danh sách cổ phiếu thường có vốn hóa ≥ min_cap_eok (억), giảm dần theo vốn hóa.
    Trả [{ticker, name, market, cap_eok, price}]."""
    out = []
    for mk in markets:
        for page in range(1, max_pages + 1):
            try:
                j = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}",
                                 params={"page": page, "pageSize": 100},
                                 headers=_H, timeout=_TIMEOUT).json()
            except Exception:
                break
            stocks = j.get("stocks") or []
            if not stocks:
                break
            below = False
            for s in stocks:
                cap = _num(s.get("marketValueRaw"))
                cap_eok = cap / 1e8 if cap else None
                if cap_eok is not None and cap_eok < min_cap_eok:
                    below = True
                    break
                if s.get("stockEndType") != "stock":
                    continue
                out.append({"ticker": str(s.get("itemCode")).zfill(6),
                            "name": s.get("stockName"), "market": mk,
                            "cap_eok": cap_eok, "price": _num(s.get("closePriceRaw"))})
            if below:
                break
    return out


# ----------------------------------------------------------------------------
# Trụ 1 — Biểu đồ
# ----------------------------------------------------------------------------
def fetch_daily(symbol: str, cal_days: int = 560) -> dict:
    """Nến NGÀY từ Naver (~380 phiên với 560 ngày lịch). {} nếu lỗi."""
    end = _dt.date.today()
    start = end - _dt.timedelta(days=cal_days)
    u = (f"https://api.finance.naver.com/siseJson.naver?symbol={symbol}&requestType=1"
         f"&startTime={start:%Y%m%d}&endTime={end:%Y%m%d}&timeframe=day")
    try:
        r = requests.get(u, headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://finance.naver.com/"}, timeout=_TIMEOUT)
        rows = json.loads(r.text.replace("'", '"'))[1:]
    except Exception:
        return {}
    rows = [x for x in rows if x and x[4] not in (None, "", 0)]
    if not rows:
        return {}
    return {"date": [str(x[0]) for x in rows],
            "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
            "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
            "volume": [float(x[5] or 0) for x in rows]}


def _sma(xs: list, n: int, end_offset: int = 0) -> Optional[float]:
    """SMA n phiên, kết thúc cách hiện tại end_offset phiên."""
    if len(xs) < n + end_offset:
        return None
    seg = xs[len(xs) - end_offset - n: len(xs) - end_offset]
    return sum(seg) / n


CHART_CRITERIA = [
    "Giá > MA150 và MA200",
    "MA150 > MA200",
    "MA200 đi lên ≥ 1 tháng",
    "MA50 > MA150 và MA200",
    "Giá > MA50",
    "Giá ≥ 30% trên đáy 52T",
    "Giá trong 25% từ đỉnh 52T",
    "RS ≥ ngưỡng (mạnh hơn thị trường)",
]


def chart_metrics(d: dict) -> Optional[dict]:
    """Trend Template 7 tiêu chí đầu (tiêu chí RS chấm sau khi có percentile toàn vũ trụ)
    + tín hiệu phụ. Cần ≥ 230 phiên."""
    c, h, lo, v = d.get("close", []), d.get("high", []), d.get("low", []), d.get("volume", [])
    n = len(c)
    if n < 230:
        return None
    price = c[-1]
    ma50, ma150, ma200 = _sma(c, 50), _sma(c, 150), _sma(c, 200)
    ma200_1m = _sma(c, 200, end_offset=22)
    hi52, lo52 = max(h[-252:]), min(lo[-252:])

    crit = {
        CHART_CRITERIA[0]: price > ma150 and price > ma200,
        CHART_CRITERIA[1]: ma150 > ma200,
        CHART_CRITERIA[2]: ma200_1m is not None and ma200 > ma200_1m,
        CHART_CRITERIA[3]: ma50 > ma150 and ma50 > ma200,
        CHART_CRITERIA[4]: price > ma50,
        CHART_CRITERIA[5]: price >= 1.3 * lo52,
        CHART_CRITERIA[6]: price >= 0.75 * hi52,
    }

    def ret(k):
        return c[-1] / c[-1 - k] - 1 if n > k and c[-1 - k] else 0.0

    # RS kiểu IBD: nặng quý gần nhất
    rs_raw = 0.4 * ret(63) + 0.2 * ret(126) + 0.2 * ret(189) + 0.2 * ret(min(252, n - 1))

    # Tích lũy: tổng KL ngày tăng / ngày giảm trong 50 phiên
    up = sum(v[i] for i in range(n - 50, n) if c[i] > c[i - 1])
    dn = sum(v[i] for i in range(n - 50, n) if c[i] < c[i - 1])
    ud = up / dn if dn else None

    # Nền siết: biên độ 10 phiên so với 50 phiên (càng nhỏ càng chặt)
    def rng(k):
        return (max(h[-k:]) - min(lo[-k:])) / price if price else None
    r10, r50 = rng(10), rng(50)
    tight = r10 / r50 if (r10 is not None and r50) else None

    vol50 = sum(v[-50:]) / 50 if n >= 50 else None
    return {
        "price": price, "ma50": ma50, "ma150": ma150, "ma200": ma200,
        "hi52": hi52, "lo52": lo52,
        "pct_from_hi": (price / hi52 - 1) * 100 if hi52 else None,
        "pct_from_lo": (price / lo52 - 1) * 100 if lo52 else None,
        "ret_3m": ret(63) * 100, "ret_12m": ret(min(252, n - 1)) * 100,
        "rs_raw": rs_raw, "ud_ratio": ud, "tight": tight,
        "vol_ratio": (v[-1] / vol50) if vol50 else None,
        "crit": crit,
    }


def assign_rs(metrics: dict[str, dict], ref: Optional[list] = None) -> None:
    """Gán RS 1–99 = percentile của rs_raw (cách IBD).
    ref = phân phối rs_raw của TOÀN SÀN (nếu có) → mã trong danh sách ngắn được xếp hạng
    so với cả thị trường, thay vì chỉ so với nhau."""
    import bisect
    if ref:
        srt = sorted(ref)
        for m in metrics.values():
            if m:
                pos = bisect.bisect_right(srt, m["rs_raw"])
                m["rs"] = max(1, min(99, round(pos / len(srt) * 99)))
        return
    items = [(t, m["rs_raw"]) for t, m in metrics.items() if m]
    items.sort(key=lambda x: x[1])
    n = len(items)
    for i, (t, _) in enumerate(items):
        metrics[t]["rs"] = max(1, min(99, round((i + 1) / n * 99))) if n else None


def finalize_chart(m: dict, rs_min: int = 70) -> dict:
    m["crit"][CHART_CRITERIA[7]] = (m.get("rs") or 0) >= rs_min
    m["chart_pass"] = sum(1 for ok in m["crit"].values() if ok)
    # "Gần điểm mua": sát đỉnh + nền chặt
    m["near_pivot"] = (m.get("pct_from_hi") is not None and m["pct_from_hi"] >= -5
                       and m.get("tight") is not None and m["tight"] <= 0.5)
    return m


# ----------------------------------------------------------------------------
# Trụ 2 — Tăng trưởng doanh thu / lợi nhuận
# ----------------------------------------------------------------------------
def _yoy(cur, prev) -> Optional[float]:
    if cur is None or prev is None:
        return None
    if prev > 0:
        return (cur / prev - 1) * 100
    if cur > 0 >= prev:
        return float("inf")           # chuyển lỗ → lãi (흑자전환)
    return None


def growth_metrics(fin: dict) -> dict:
    """fin = financials.fetch_financials(ticker). Đơn vị DT/LN = 억원."""
    q = [c for c in fin.get("quarter", []) if not c.get("is_consensus")]
    a = [c for c in fin.get("annual", []) if not c.get("is_consensus")]
    a_e = [c for c in fin.get("annual", []) if c.get("is_consensus")]
    g: dict = {}
    if len(q) >= 5:
        last, ya = q[-1], q[-5]
        g["q_period"] = last.get("period")
        g["rev_q"] = last.get("revenue")
        g["np_q"] = last.get("net_profit")
        g["rev_yoy"] = _yoy(last.get("revenue"), ya.get("revenue"))
        g["np_yoy"] = _yoy(last.get("net_profit"), ya.get("net_profit"))

        def opm(c):
            return (c["op_profit"] / c["revenue"] * 100
                    if c.get("op_profit") is not None and c.get("revenue") else None)
        g["opm_now"], g["opm_ya"] = opm(last), opm(ya)
    if len(q) >= 2:
        g["rev_qoq"] = _yoy(q[-1].get("revenue"), q[-2].get("revenue"))
        g["np_qoq"] = _yoy(q[-1].get("net_profit"), q[-2].get("net_profit"))
    if len(a) >= 2:
        g["rev_y"] = _yoy(a[-1].get("revenue"), a[-2].get("revenue"))
        g["np_y"] = _yoy(a[-1].get("net_profit"), a[-2].get("net_profit"))
    if a and a_e:
        g["np_fwd"] = _yoy(a_e[-1].get("net_profit"), a[-1].get("net_profit"))
    g["quarters"] = q[-5:]
    return g


def growth_criteria(g: dict, rev_th: float = 25, np_th: float = 25) -> dict:
    """Tiêu chí tăng trưởng → {tên: True/False/None (thiếu dữ liệu)}."""
    def ge(x, th):
        return None if x is None else x >= th
    opm_up = (None if g.get("opm_now") is None or g.get("opm_ya") is None
              else g["opm_now"] > g["opm_ya"])
    return {
        f"DT quý cùng kỳ ≥ {rev_th:.0f}%": ge(g.get("rev_yoy"), rev_th),
        f"LN thuần quý cùng kỳ ≥ {np_th:.0f}%": ge(g.get("np_yoy"), np_th),
        "Biên LN HĐ mở rộng (vs cùng kỳ)": opm_up,
        "DT năm gần nhất tăng ≥ 15%": ge(g.get("rev_y"), 15),
        "Đồng thuận: LN năm tới tăng": ge(g.get("np_fwd"), 0),
    }


# ----------------------------------------------------------------------------
# Trụ 3 — Tin tức (từ khóa xúc tác trong tiêu đề)
# ----------------------------------------------------------------------------
POS_KW = ["수주", "공급계약", "공급 계약", "단일판매", "신고가", "최대 실적", "사상 최대",
          "역대 최대", "최대 매출", "흑자전환", "흑자 전환", "어닝 서프라이즈", "깜짝 실적",
          "호실적", "실적 개선", "목표가 상향", "목표주가 상향", "상향", "매수", "증설",
          "수혜", "돌파", "상한가", "승인", "허가", "특허", "양산", "납품", "MOU", "독점"]
NEG_KW = ["유상증자", "전환사채", "적자", "하향", "소송", "감자", "관리종목", "횡령",
          "배임", "급락", "하한가", "매도", "거래정지", "불성실", "투자경고", "투자주의",
          "투자위험", "리콜", "부진", "쇼크", "상장폐지"]


def fetch_news(ticker: str, n: int = 30) -> list[dict]:
    try:
        j = requests.get(f"https://m.stock.naver.com/api/news/stock/{ticker}",
                         params={"pageSize": n, "page": 1}, headers=_H,
                         timeout=_TIMEOUT).json()
    except Exception:
        return []
    out = []
    for blk in j or []:
        for it in blk.get("items", []):
            title = html.unescape(it.get("title") or "")
            out.append({"dt": it.get("datetime", ""), "title": title,
                        "office": it.get("officeName", ""),
                        "url": (f"https://n.news.naver.com/mnews/article/"
                                f"{it.get('officeId')}/{it.get('articleId')}")})
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


# Tin tổng hợp thị trường (không riêng mã) → trung tính, tránh "코스피 급락" bị tính là tin xấu của mã
MARKET_KW = ["코스피", "코스닥", "증시", "시총 상위", "뉴욕", "마감시황", "장 마감"]


def tag_title(title: str) -> int:
    if any(k in title for k in MARKET_KW):
        return 0
    pos = any(k in title for k in POS_KW)
    neg = any(k in title for k in NEG_KW)
    return 1 if pos and not neg else (-1 if neg and not pos else 0)


def news_metrics(items: list[dict], today: Optional[_dt.date] = None) -> dict:
    today = today or _dt.date.today()

    def age(dt):
        try:
            return (today - _dt.datetime.strptime(dt[:8], "%Y%m%d").date()).days
        except Exception:
            return 999
    recent30 = [it for it in items if age(it["dt"]) <= 30]
    for it in items:
        it["tag"] = tag_title(it["title"])
    pos = sum(1 for it in recent30 if it["tag"] > 0)
    neg = sum(1 for it in recent30 if it["tag"] < 0)
    n7 = sum(1 for it in items if age(it["dt"]) <= 7)
    return {"n7": n7, "n30": len(recent30), "pos": pos, "neg": neg,
            "crit": {"Có tin xúc tác tích cực (30 ngày)": pos >= 1,
                     "Tin tích cực > tiêu cực (30 ngày)": pos > neg,
                     "Được chú ý: ≥ 3 tin trong 7 ngày": n7 >= 3},
            "items": items[:10]}


# ----------------------------------------------------------------------------
# Toss (tùy chọn): giá realtime + cảnh báo mã
# ----------------------------------------------------------------------------
WARN_VI = {"LIQUIDATION_TRADING": "Đang thanh lý (sắp hủy niêm yết)",
           "OVERHEATED": "Quá nóng ngắn hạn", "INVESTMENT_WARNING": "Cảnh báo đầu tư",
           "INVESTMENT_RISK": "Rủi ro đầu tư", "VI_STATIC_AND_DYNAMIC": "VI tĩnh+động",
           "VI_STATIC": "VI tĩnh", "VI_DYNAMIC": "VI động", "STOCK_WARRANTS": "Chứng quyền"}


def toss_overlay(tickers: list[str]) -> tuple[dict, dict, Optional[str]]:
    """(giá realtime {mã: giá}, cảnh báo {mã: [mô tả]}, lỗi hoặc None)."""
    try:
        from toss_api import TossClient
        cli = TossClient()
        prices = cli.get_prices(tickers)
        warns = {}
        for t in tickers:
            try:
                res = cli._get(f"/api/v1/stocks/{t}/warnings", {})
                lst = res if isinstance(res, list) else (res or {}).get("result", [])
                w = [WARN_VI.get(x.get("warningType"), x.get("warningType"))
                     for x in lst if isinstance(x, dict)]
                if w:
                    warns[t] = w
            except Exception:
                pass
        return prices, warns, None
    except Exception as ex:
        return {}, {}, f"{type(ex).__name__}: {str(ex)[:120]}"


# ----------------------------------------------------------------------------
# Song song (5 luồng — lịch sự với Naver)
# ----------------------------------------------------------------------------
def parallel_map(fn, keys: list, workers: int = 5, progress=None) -> dict:
    out, done = {}, 0
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, k): k for k in keys}
        for f in _cf.as_completed(futs):
            k = futs[f]
            try:
                out[k] = f.result()
            except Exception:
                out[k] = None
            done += 1
            if progress:
                progress(done, len(keys))
    return out


def score_pct(crit: dict) -> Optional[float]:
    """% tiêu chí đạt trên số tiêu chí CÓ dữ liệu (None = thiếu, không tính)."""
    avail = [v for v in crit.values() if v is not None]
    return (sum(1 for v in avail if v) / len(avail) * 100) if avail else None


def label(chart_pass: int, growth_pct: Optional[float], rs: Optional[int]) -> str:
    gp = growth_pct or 0
    if chart_pass >= 7 and gp >= 80 and (rs or 0) >= 80:
        return "🚀 Siêu tăng trưởng"
    if chart_pass >= 6 and gp >= 60:
        return "📈 Đang setup"
    return "👀 Theo dõi"
