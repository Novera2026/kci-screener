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
    hi52 = max(h[-252:])
    lo52 = min((x for x in lo[-252:] if x > 0), default=None)   # bỏ phiên giá 0 (ngừng GD)

    crit = {
        CHART_CRITERIA[0]: price > ma150 and price > ma200,
        CHART_CRITERIA[1]: ma150 > ma200,
        CHART_CRITERIA[2]: ma200_1m is not None and ma200 > ma200_1m,
        CHART_CRITERIA[3]: ma50 > ma150 and ma50 > ma200,
        CHART_CRITERIA[4]: price > ma50,
        CHART_CRITERIA[5]: lo52 is not None and price >= 1.3 * lo52,
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

    # --- Độ giãn (đã chạy bao xa) ---
    def rsi(xs, k=14):                      # RSI Wilder
        gains = losses = 0.0
        for i in range(len(xs) - k, len(xs)):
            ch = xs[i] - xs[i - 1]
            gains += max(ch, 0)
            losses += max(-ch, 0)
        if losses == 0:
            return 100.0
        return 100 - 100 / (1 + gains / losses)
    prior_hi = max(h[-60:-10]) if n >= 60 else None   # đỉnh nền trước đó (bỏ 10 phiên gần)
    breakout_ext = ((price / prior_hi - 1) * 100) if prior_hi else None
    avg_val20 = sum(c[i] * v[i] for i in range(n - 20, n)) / 20 / 1e8   # 억원/phiên
    return {
        "avg_val20": avg_val20,
        "ext_ma50": (price / ma50 - 1) * 100 if ma50 else None,
        "ext_ma200": (price / ma200 - 1) * 100 if ma200 else None,
        "ret_1m": ret(21) * 100,
        "rsi14": rsi(c),
        "breakout_ext": breakout_ext,
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
# Giai đoạn: lọc mã ĐÃ CHẠY QUÁ XA (rủi ro đu đỉnh)
# ----------------------------------------------------------------------------
DEFAULT_LIMITS = {"ext_ma50": 25.0, "from_low": 200.0, "ret_3m": 100.0, "rsi": 80.0,
                  "mid_ext_ma50": 15.0, "mid_from_low": 100.0, "breakout": 10.0}


def stage_of(m: dict, lim: Optional[dict] = None) -> tuple[str, list]:
    """→ (giai đoạn, lý do).
    🔥 Đã chạy xa: cách MA50 > 25% · tăng > 200% từ đáy 52T · +100% trong 3 tháng · RSI > 80
    🌿 Đang chạy : cách MA50 > 15% · tăng > 100% từ đáy · đã vượt đỉnh nền > 10%
    🌱 Sớm       : còn lại (gần nền / điểm mua — vùng rủi ro/lợi nhuận tốt nhất)."""
    L = {**DEFAULT_LIMITS, **(lim or {})}
    late, mid = [], []
    e50, flo, r3, rsi = (m.get("ext_ma50"), m.get("pct_from_lo"), m.get("ret_3m"),
                         m.get("rsi14"))
    if e50 is not None and e50 > L["ext_ma50"]:
        late.append(f"cách MA50 +{e50:.0f}%")
    if flo is not None and flo > L["from_low"]:
        late.append(f"+{flo:.0f}% từ đáy 52T")
    if r3 is not None and r3 > L["ret_3m"]:
        late.append(f"+{r3:.0f}% trong 3 tháng")
    if rsi is not None and rsi > L["rsi"]:
        late.append(f"RSI {rsi:.0f} quá mua")
    if late:
        return "🔥 Đã chạy xa", late
    bo = m.get("breakout_ext")
    if e50 is not None and e50 > L["mid_ext_ma50"]:
        mid.append(f"cách MA50 +{e50:.0f}%")
    if flo is not None and flo > L["mid_from_low"]:
        mid.append(f"+{flo:.0f}% từ đáy 52T")
    if bo is not None and bo > L["breakout"]:
        mid.append(f"đã vượt đỉnh nền +{bo:.0f}%")
    if mid:
        return "🌿 Đang chạy", mid
    return "🌱 Sớm", ["gần nền/điểm mua, chưa giãn"]


def group_strength(mets: dict, groups: dict, min_members: int = 5,
                   shrink: int = 5) -> dict:
    """Sức mạnh từng nhóm (chủ đề/ngành) trên dữ liệu vừa quét.
    raw = 50% RS trung vị + 50% độ rộng (% thành viên đạt xu hướng ≥ 6/8);
    score = co về mức nền thị trường cho nhóm ít mã: (n·raw + k·nền)/(n + k)
    → nhóm 3–5 mã "may mắn" không chiếm top; nhóm lớn & mạnh thật mới dẫn đầu.
    → {nhóm: {score, med_rs, breadth, ret3m, n, rank, pct}} (pct = phân vị 0–100)."""
    allm = [m for m in mets.values() if m]
    base = (0.5 * 50 + 0.5 * (sum(1 for m in allm if m.get("chart_pass", 0) >= 6)
                              / max(len(allm), 1) * 100))
    out = {}
    for name, tks in groups.items():
        ms = [mets[t] for t in tks if t in mets and mets[t]]
        if len(ms) < min_members:
            continue
        rs = sorted(m.get("rs") or 0 for m in ms)
        r3 = sorted(m.get("ret_3m") or 0 for m in ms)
        med_rs = rs[len(rs) // 2]
        breadth = sum(1 for m in ms if m.get("chart_pass", 0) >= 6) / len(ms) * 100
        raw = 0.5 * med_rs + 0.5 * breadth
        n_ = len(ms)
        out[name] = {"score": (n_ * raw + shrink * base) / (n_ + shrink), "med_rs": med_rs,
                     "breadth": breadth, "ret3m": r3[len(r3) // 2], "n": len(ms)}
    ranked = sorted(out, key=lambda k: -out[k]["score"])
    for i, k in enumerate(ranked):
        out[k]["rank"] = i + 1
        out[k]["pct"] = round((1 - i / max(len(ranked), 1)) * 100)
    return out


def meta_for(tk: str, themes_of: dict, ind_of: dict, theme_rank: dict,
             ind_rank: dict, top_k: int = 10) -> dict:
    """Mã có thuộc nhóm DẪN DẮT (meta) không: chủ đề mạnh nhất của mã nằm top-K chủ đề,
    hoặc ngành nằm top-5 ngành. meta_pct = phân vị sức mạnh nhóm tốt nhất (0–100)."""
    best = None
    for th in themes_of.get(tk, []):
        r = theme_rank.get(th)
        if r and (best is None or r["score"] > best[1]["score"]):
            best = (th, r)
    ind = ind_of.get(tk)
    ir = ind_rank.get(ind) if ind else None
    in_meta = bool((best and best[1]["rank"] <= top_k) or (ir and ir["rank"] <= 5))
    pct = max([x for x in ((best[1]["pct"] if best else None),
                           (ir["pct"] if ir else None)) if x is not None], default=None)
    theme_hit = bool(best and best[1]["rank"] <= top_k)
    ind_hit = bool(ir and ir["rank"] <= 5)
    if theme_hit:
        why = f"{best[0]} (chủ đề #{best[1]['rank']})"
    elif ind_hit:
        why = f"{ind} (ngành #{ir['rank']})"
    elif best:
        why = f"{best[0]} (chủ đề #{best[1]['rank']})"
    else:
        why = f"{ind} (ngành #{ir['rank']})" if ir else "—"
    via = "theme" if theme_hit else ("industry" if ind_hit else None)
    return {"in_meta": in_meta, "meta_pct": pct, "why": why, "via": via,
            "theme": best[0] if best else None,
            "theme_rank": best[1]["rank"] if best else None,
            "ind": ind, "ind_rank": ir["rank"] if ir else None}


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
# Lớp RÀ SOÁT: vì sao mã được chọn + chấm điểm lựa chọn (thang 100, minh bạch)
# ----------------------------------------------------------------------------
SEVERE_KW = ["유상증자", "전환사채", "신주인수권부사채", "소송", "횡령", "배임", "감사의견",
             "거래정지", "상장폐지", "불성실공시", "관리종목", "투자주의환기"]
GRADES = [(80, "A", "Chọn mạnh"), (65, "B", "Đáng cân nhắc"),
          (50, "C", "Theo dõi thêm"), (0, "D", "Yếu — cân nhắc loại")]


def grade_of(score: float) -> tuple[str, str]:
    for th, g, txt in GRADES:
        if score >= th:
            return g, txt
    return "D", GRADES[-1][2]


def review(m: dict, g: dict, nm: dict, rev_th: float = 25, np_th: float = 25,
           toss_warn: Optional[list] = None) -> dict:
    """Rà soát 1 mã đã qua bộ lọc: cộng điểm theo 6 hạng mục (tối đa 100) rồi TRỪ điểm
    cho rủi ro (tăng trưởng nền thấp, LN không đến từ doanh thu, tin xấu nghiêm trọng,
    thanh khoản kém, dấu hiệu phân phối, đang nóng, bị sàn cảnh báo).
    → {score, grade, grade_txt, parts[], pros[], cons[], summary}."""
    parts, pros, cons = [], [], []

    def add(name, pts, mx, why):
        parts.append({"Hạng mục": name, "Điểm": round(max(pts, 0), 1), "Tối đa": mx,
                      "Diễn giải": why})
        return max(pts, 0)

    total = 0.0
    # 1) Xu hướng giá (25)
    cp, rs = m.get("chart_pass", 0), m.get("rs") or 0
    total += add("Xu hướng giá", cp / 8 * 20 + (5 if rs >= 90 else 3 if rs >= 80 else 0), 25,
                 f"{cp}/8 tiêu chí Trend Template · RS {rs}")
    if cp >= 7:
        pros.append((7, f"xu hướng tăng chuẩn ({cp}/8 tiêu chí)"))
    if rs >= 90:
        pros.append((8, f"giá mạnh hơn {rs}% thị trường (RS {rs})"))

    # 2) Điểm vào — chưa giãn (20)
    stg = m.get("stage", "")
    pts = 15 if stg.startswith("🌱") else 7 if stg.startswith("🌿") else 0
    tight = m.get("tight")
    if m.get("near_pivot"):
        pts += 5
    elif tight is not None and tight <= 0.5:
        pts += 3
    e50 = m.get("ext_ma50")
    total += add("Điểm vào (chưa giãn)", pts, 20,
                 f"{stg} · cách MA50 {e50:+.0f}%" + (f" · nền siết {tight:.2f}"
                                                     if tight is not None else ""))
    if stg.startswith("🌱"):
        pros.append((5, "còn gần nền giá, chưa chạy xa"))
    elif stg.startswith("🌿"):
        cons.append("đã rời nền (" + "; ".join(m.get("stage_why", [])) + ") — dư địa ít hơn")
    if m.get("near_pivot"):
        pros.append((4, "sát điểm mua (gần đỉnh 52T + nền chặt)"))

    # 3) Tăng trưởng (25)
    ry, ny = g.get("rev_yoy"), g.get("np_yoy")
    opm_now, opm_ya = g.get("opm_now"), g.get("opm_ya")
    pts = 0
    if ry is not None:
        pts += 10 if ry >= 50 else 8 if ry >= rev_th else 3 if ry > 0 else 0
    if ny is not None:
        pts += 10 if ny >= 50 else 8 if ny >= np_th else 3 if ny > 0 else 0
    if opm_now is not None and opm_ya is not None and opm_now > opm_ya:
        pts += 3
    if (g.get("np_fwd") or -1) > 0:
        pts += 2

    def _p(x):
        return "lỗ→lãi" if x == float("inf") else (">+999%" if x > 999 else f"{x:+.0f}%")
    total += add("Tăng trưởng DT/LN", min(pts, 25), 25,
                 f"DT quý {_p(ry) if ry is not None else '—'} · LN quý "
                 f"{_p(ny) if ny is not None else '—'} so cùng kỳ"
                 + (f" · biên LN HĐ {opm_ya:.0f}%→{opm_now:.0f}%"
                    if opm_now is not None and opm_ya is not None else ""))
    if ry is not None and ry >= rev_th:
        pros.append((1, f"doanh thu quý tăng {_p(ry)} so cùng kỳ"))
    if ny is not None and ny >= np_th:
        pros.append((2, f"lợi nhuận quý {_p(ny)} so cùng kỳ"))

    # 4) Meta (15)
    mt = m.get("meta") or {}
    if mt.get("in_meta") is None:
        pts, why = 7, "chưa xác định meta (cần quét toàn sàn)"
    elif mt.get("via") == "theme":
        pts, why = 15, f"thuộc nhóm dẫn dắt: {mt.get('why')}"
    elif mt.get("via") == "industry":
        pts, why = 10, f"ngành mạnh: {mt.get('why')}"
    else:
        pts, why = round((mt.get("meta_pct") or 0) / 100 * 5), f"ngoài meta: {mt.get('why')}"
    total += add("Meta (nhóm dẫn dắt)", pts, 15, why)
    if mt.get("via"):
        pros.append((3, why))
    elif mt.get("in_meta") is False:
        cons.append("không thuộc nhóm đang dẫn dắt")

    # 5) Tin tức (10)
    pos, neg, n7 = nm.get("pos", 0), nm.get("neg", 0), nm.get("n7", 0)
    total += add("Tin tức / xúc tác", (5 if pos >= 1 else 0) + (3 if pos > neg else 0)
                 + (2 if n7 >= 3 else 0), 10, f"+{pos} tích cực / −{neg} tiêu cực (30 ngày) · "
                 f"{n7} tin/7 ngày")
    pos_titles = [it["title"] for it in nm.get("items", []) if it.get("tag", 0) > 0]
    if pos_titles:
        pros.append((6, f"có xúc tác: “{pos_titles[0][:45]}”"))

    # 6) Dòng tiền (5)
    ud = m.get("ud_ratio")
    total += add("Dòng tiền (tích lũy KL)", 5 if ud and ud >= 1.2 else 2 if ud and ud >= 1.0
                 else 0, 5, f"KL ngày tăng / ngày giảm = {ud:.2f}" if ud else "—")
    if ud and ud >= 1.2:
        pros.append((9, "khối lượng ngày tăng áp đảo (đang được gom)"))

    # --- Điểm trừ (rủi ro) ---
    def ded(pts, why):
        nonlocal total
        total += pts
        cons.append(why)
        parts.append({"Hạng mục": "⚠️ Trừ điểm", "Điểm": pts, "Tối đa": 0, "Diễn giải": why})

    if ny == float("inf") or (ny is not None and ny > 500):
        ded(-5, "LN tăng đột biến từ nền thấp / vừa chuyển lãi — cần quý sau xác nhận")
    if ry is not None and ny is not None and ry < 10 and ny > 50:
        ded(-5, "LN tăng mạnh nhưng doanh thu gần như đứng yên — có thể do khoản bất thường")
    if opm_now is not None and opm_ya is not None and opm_now < opm_ya:
        ded(-3, f"biên LN HĐ thu hẹp ({opm_ya:.0f}% → {opm_now:.0f}%)")
    if ry is None and ny is None:
        ded(-5, "thiếu số liệu quý để xác nhận tăng trưởng")
    severe = [it["title"] for it in nm.get("items", [])
              if any(k in it["title"] for k in SEVERE_KW)]
    if severe:
        ded(-10, f"tin rủi ro nghiêm trọng: “{severe[0][:45]}”")
    elif neg > pos:
        ded(-5, "tin tiêu cực nhiều hơn tích cực (30 ngày)")
    rsi = m.get("rsi14")
    if rsi is not None and rsi > 70:
        ded(-3, f"RSI {rsi:.0f} — đang nóng ngắn hạn, dễ rung lắc")
    if ud is not None and ud < 0.8:
        ded(-5, "khối lượng ngày giảm lớn hơn ngày tăng — dấu hiệu phân phối")
    av = m.get("avg_val20")
    if av is not None and av < 10:
        ded(-5, f"thanh khoản thấp (~{av:.0f}억/phiên) — khó mua/bán khối lượng lớn")
    if toss_warn:
        ded(-15, "sàn đang cảnh báo: " + ", ".join(toss_warn))

    pros = [t for _, t in sorted(pros, key=lambda x: x[0])]   # lý do đặc trưng trước
    score = max(0.0, min(100.0, total))
    gr, gtxt = grade_of(score)
    summary = ("Được chọn vì " + "; ".join(pros[:3]) + "." if pros else "Ít điểm mạnh nổi bật.")
    if cons:
        summary += " Cần lưu ý: " + "; ".join(cons[:3]) + "."
    return {"score": round(score), "grade": gr, "grade_txt": gtxt, "parts": parts,
            "pros": pros, "cons": cons, "summary": summary}


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


def label(chart_pass: int, growth_pct: Optional[float], rs: Optional[int],
          stage: str = "🌱 Sớm", in_meta: bool = True) -> str:
    gp = growth_pct or 0
    if stage.startswith("🔥"):
        return "🔥 Đã chạy xa — không đuổi"
    if chart_pass >= 7 and gp >= 80 and (rs or 0) >= 80 and in_meta:
        return "🚀 Siêu cổ phiếu tiềm năng"
    if chart_pass >= 6 and gp >= 60:
        return "📈 Đang setup"
    return "👀 Theo dõi"
