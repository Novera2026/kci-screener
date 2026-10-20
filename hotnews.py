"""
KCI Hot News — theo dõi CHỦ ĐỀ, TIN TỨC & CÔNG BỐ đang dẫn dắt thị trường Hàn.

Nguồn (không cần key, trừ DART):
  Naver  — xếp hạng chủ đề realtime (/api/stocks/theme), thành viên + mô tả chủ đề,
           tin chính / tin nhanh / tin đọc nhiều (/api/news/list), tin theo mã,
           giá realtime hàng loạt (polling.finance.naver.com, 50 mã/lần).
  DART   — danh sách công bố trong ngày (/api/list.json) → lọc loại có giá trị.

Ưu tiên tin GIÁ TRỊ CAO: điểm 0–100 theo loại sự kiện (công bố chính thức > hợp đồng /
kết quả kinh doanh / phê duyệt > nâng mục tiêu > tin chung > bình luận thị trường),
cộng điểm khi có số tiền, gắn chủ đề đang nóng, tin mới. Chấm bằng TỪ KHÓA trên tiêu đề
(chưa hiểu ngữ cảnh) — là công cụ ưu tiên đọc, không phải kết luận.
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import html
import re
from typing import Optional

import requests

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
_T = 10
THEME_BASE = "https://m.stock.naver.com/api/stocks/theme"


def _n(x) -> Optional[float]:
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=_H, timeout=_T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Chủ đề
# ----------------------------------------------------------------------------
def theme_ranking(max_pages: int = 4) -> tuple[list[dict], str]:
    """Xếp hạng chủ đề theo % thay đổi HÔM NAY (Naver, realtime). → (danh sách, trạng thái TT)."""
    out, status = [], ""
    for page in range(1, max_pages + 1):
        j = _get(THEME_BASE, {"page": page, "pageSize": 100})
        if not j:
            break
        status = j.get("marketStatus", status)
        for g in j.get("groups") or []:
            total = g.get("totalCount") or 0
            rise = g.get("riseCount") or 0
            out.append({"no": g.get("no"), "name": g.get("name"),
                        "chg": _n(g.get("changeRate")), "rise": rise,
                        "fall": g.get("fallCount") or 0, "total": total,
                        "breadth": rise / total * 100 if total else None})
        if page * 100 >= (j.get("totalCount") or 0):
            break
    out.sort(key=lambda x: -(x["chg"] if x["chg"] is not None else -999))
    return out, status


def theme_detail(no: int, n: int = 40) -> dict:
    """Mô tả chủ đề + thành viên (giá, % hôm nay, GTGD, vốn hóa, lý do thuộc chủ đề)."""
    j = _get(f"{THEME_BASE}/{no}", {"page": 1, "pageSize": n}) or {}
    reasons = j.get("themeItemInfoMap") or {}
    stocks = []
    for s in j.get("stocks") or []:
        if s.get("stockEndType", "stock") != "stock":
            continue
        code = str(s.get("itemCode")).zfill(6)
        tv = _n(s.get("accumulatedTradingValue"))           # đơn vị: 백만원
        stocks.append({"ticker": code, "name": s.get("stockName"),
                       "price": _n(s.get("closePrice")), "chg": _n(s.get("fluctuationsRatio")),
                       "tv_eok": tv / 100 if tv is not None else None,
                       "cap_eok": _n(s.get("marketValue")),
                       "reason": reasons.get(code) or reasons.get(s.get("itemCode"), "")})
    stocks.sort(key=lambda x: -(x["chg"] if x["chg"] is not None else -999))
    return {"desc": j.get("themeDescription") or "", "stocks": stocks,
            "info": j.get("groupInfo") or {}}


def realtime(tickers: list[str]) -> dict:
    """Giá & % thay đổi realtime, 50 mã/lần gọi. → {mã: {price, chg, tv}}."""
    out = {}
    for i in range(0, len(tickers), 50):
        chunk = ",".join(tickers[i:i + 50])
        j = _get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{chunk}")
        for d in (j or {}).get("datas", []):
            out[str(d.get("itemCode")).zfill(6)] = {
                "price": _n(d.get("closePrice")), "chg": _n(d.get("fluctuationsRatio")),
                "tv": d.get("accumulatedTradingValue")}
    return out


# ----------------------------------------------------------------------------
# Tin tức
# ----------------------------------------------------------------------------
NEWS_CATS = {"flashnews": "Tin nhanh", "mainnews": "Tin chính", "ranknews": "Đọc nhiều"}


def _parse_dt(s: str) -> Optional[_dt.datetime]:
    s = (s or "").strip()
    for fmt, k in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        if len(s) >= k:
            try:
                return _dt.datetime.strptime(s[:k], fmt)
            except ValueError:
                pass
    return None


def market_news(category: str, n: int = 30) -> list[dict]:
    out = []
    for it in _get("https://m.stock.naver.com/api/news/list",
                   {"category": category, "pageSize": n, "page": 1}) or []:
        out.append({"title": html.unescape(it.get("tit") or ""),
                    "summary": html.unescape(it.get("subcontent") or "")[:300],
                    "dt": _parse_dt(it.get("dt")), "office": it.get("ohnm") or "",
                    "url": f"https://n.news.naver.com/mnews/article/{it.get('oid')}/{it.get('aid')}",
                    "src": NEWS_CATS.get(category, category)})
    return out


def stock_news(ticker: str, n: int = 10, name: str = "") -> list[dict]:
    out = []
    j = _get(f"https://m.stock.naver.com/api/news/stock/{ticker}", {"pageSize": n, "page": 1})
    for blk in j or []:
        for it in blk.get("items", []):
            out.append({"title": html.unescape(it.get("title") or ""),
                        "summary": html.unescape(it.get("body") or "")[:300],
                        "dt": _parse_dt(it.get("datetime")), "office": it.get("officeName") or "",
                        "url": (f"https://n.news.naver.com/mnews/article/"
                                f"{it.get('officeId')}/{it.get('articleId')}"),
                        "src": "Tin theo mã", "ticker_hint": ticker, "hint_name": name})
    return out


# Phân tầng từ khóa (tiêu đề) — tầng cao = sự kiện làm thay đổi giá trị doanh nghiệp
S_KW = ["수주", "공급계약", "공급 계약", "계약 체결", "최대 실적", "사상 최대", "역대 최대",
        "최대 매출", "어닝 서프라이즈", "깜짝 실적", "흑자전환", "흑자 전환", "승인", "허가",
        "기술수출", "기술이전", "인수", "합병", "상한가", "신고가", "독점", "세계 최초",
        "국내 최초", "정부 지원", "국책", "임상 성공", "단독"]
A_KW = ["목표가", "목표주가", "상향", "매수", "증설", "양산", "납품", "MOU", "협약", "신사업",
        "수혜", "기대감", "급등", "강세", "투자 확대", "수출"]
ANALYST_KW = ["목표가", "목표주가", "리포트", "證", "증권은", "전망", "투자의견"]
RISK_KW = ["유상증자", "전환사채", "소송", "횡령", "배임", "거래정지", "하한가", "상장폐지",
           "관리종목", "급락", "적자전환", "적자 전환", "쇼크", "리콜", "압수수색", "제재",
           "감자", "불성실", "투자경고", "투자위험"]
MARKET_KW = ["코스피", "코스닥", "증시", "마감", "시황", "환율", "뉴욕", "개장"]
_AMOUNT = re.compile(r"\d[\d,\.]*\s*(조|억)\s*(원|달러)?|\d[\d,\.]*\s*(만|억)\s*달러")


_PARTICLES = set("은는이가을를의에와과도로만측")


def _hangul(ch: str) -> bool:
    return "\uac00" <= ch <= "\ud7a3"


def _word_in(name: str, text: str) -> bool:
    """Tên mã xuất hiện như MỘT TỪ (không phải một phần từ khác: '하이브' ≠ '하이브리드').
    Sau tên chỉ được là hết chuỗi, ký tự không phải chữ Hàn/chữ số, hoặc 1 trợ từ."""
    i = text.find(name)
    while i >= 0:
        before = text[i - 1] if i > 0 else " "
        j = i + len(name)
        after = text[j] if j < len(text) else " "
        ok_before = not (_hangul(before) or before.isalnum())
        ok_after = (not (_hangul(after) or after.isalnum())) or (
            after in _PARTICLES and (j + 1 >= len(text) or not _hangul(text[j + 1])
                                     or text[j:j + 2] in ("으로", "에서", "까지", "부터")))
        if ok_before and ok_after:
            return True
        i = text.find(name, i + 1)
    return False


def score_news(item: dict, hot_kw: dict, name_map: list, themes_of: dict,
               now: Optional[_dt.datetime] = None) -> dict:
    """Chấm điểm GIÁ TRỊ 0–100 cho 1 tin. hot_kw = {từ khóa chủ đề nóng: tên chủ đề};
    name_map = [(tên mã, mã)] sắp tên dài trước; themes_of = {mã: [chủ đề]}."""
    t = item["title"]
    now = now or _dt.datetime.now()
    kind, pol = "⚪ Tin chung", 0
    base = 25
    # mã liên quan: tên mã phải xuất hiện trong tiêu đề (luồng tin theo mã của Naver lẫn
    # cả tin tổng hợp thị trường → chỉ nhận mã gợi ý nếu tên có trong tiêu đề/tóm tắt)
    stocks = []
    hint, hname = item.get("ticker_hint"), item.get("hint_name") or ""
    if hint and hname and (hname in t or hname in item.get("summary", "")):
        stocks.append(hint)
    for nm, code in name_map:
        if len(stocks) >= 3:
            break
        if code not in stocks and _word_in(nm, t):
            stocks.append(code)
    title_stock = bool(stocks)
    market_wrap = any(k in t for k in MARKET_KW) and not title_stock

    if market_wrap:
        kind, pol, base = "⚪ Thị trường chung", 0, 30
    elif any(k in t for k in RISK_KW):
        kind, pol, base = "🔴 Rủi ro", -1, 60
    elif any(k in t for k in S_KW):
        kind, pol, base = "🟢 Xúc tác mạnh", 1, 70
    elif any(k in t for k in ANALYST_KW):
        kind, pol, base = "🔵 Phân tích / dự báo", 1, 50
    elif any(k in t for k in A_KW):
        kind, pol, base = "🟢 Tích cực", 1, 45

    themes = set()
    for kw, th in hot_kw.items():
        if kw in t:
            themes.add(th)
    hot_hit = bool(themes)
    for code in stocks:
        for th in themes_of.get(code, []):
            if th in hot_kw.values():
                themes.add(th)
                hot_hit = True

    s = base
    why = []
    if "특징주" in t:
        s += 15
        why.append("tin giải thích biến động (특징주)")
    if _AMOUNT.search(t):
        s += 10
        why.append("có số tiền cụ thể")
    if hot_hit:
        s += 10
        why.append("thuộc chủ đề đang nóng")
    if stocks:
        s += 5
    age_h = ((now - item["dt"]).total_seconds() / 3600) if item.get("dt") else 99
    if age_h <= 3:
        s += 10
        why.append("mới trong 3 giờ")
    elif age_h <= 12:
        s += 5
    return {**item, "score": min(100, round(s)), "kind": kind, "pol": pol,
            "stocks": stocks, "themes": sorted(themes), "why": why, "age_h": age_h}


EXCLUDE_THEMES = ("기업인수목적회사", "SPAC", "우선주")


def hot_themes(themes: list[dict], top: int = 15, min_members: int = 5) -> list[dict]:
    """Chủ đề NÓNG = đang TĂNG hôm nay, đủ thành viên, bỏ SPAC/우선주; xếp theo % tăng."""
    return [t for t in themes
            if (t["chg"] or 0) > 0 and t["total"] >= min_members
            and not any(x in t["name"] for x in EXCLUDE_THEMES)][:top]


def hot_keywords(themes: list[dict], top: int = 15) -> dict:
    """Từ khóa nhận diện chủ đề nóng trong tiêu đề: tên chủ đề bỏ phần trong ngoặc.
    `themes` nên là kết quả hot_themes()."""
    kw = {}
    for t in themes[:top]:
        core = re.split(r"[\(\/]", t["name"])[0].strip()
        if len(core) >= 2:
            kw[core] = t["name"]
    return kw


# ----------------------------------------------------------------------------
# Công bố DART — chỉ giữ loại có giá trị
# ----------------------------------------------------------------------------
# (mẫu trong tên báo cáo, nhãn tiếng Việt, chiều: +1 tốt / −1 rủi ro / 0 quan trọng, điểm)
DART_TYPES = [
    ("횡령", "Tham ô / bội tín", -1, 95), ("배임", "Tham ô / bội tín", -1, 95),
    ("회생절차", "Thủ tục phục hồi (gần phá sản)", -1, 95), ("부도", "Vỡ nợ", -1, 95),
    ("상장적격성", "Xem xét đủ điều kiện niêm yết", -1, 90),
    ("관리종목", "Bị đưa vào diện quản lý", -1, 90),
    ("거래정지", "Tạm ngừng giao dịch", -1, 90), ("영업정지", "Đình chỉ kinh doanh", -1, 85),
    ("유무상증자", "Tăng vốn có + không thu tiền", 0, 70),
    ("유상증자", "Phát hành thêm cổ phiếu (pha loãng)", -1, 85),
    ("감자", "Giảm vốn", -1, 80),
    ("전환사채권발행", "Phát hành trái phiếu chuyển đổi (CB)", -1, 75),
    ("신주인수권부사채권발행", "Phát hành trái phiếu kèm quyền mua (BW)", -1, 75),
    ("교환사채권발행", "Phát hành trái phiếu hoán đổi (EB)", -1, 60),
    ("불성실공시", "Vi phạm công bố thông tin", -1, 75),
    ("소송", "Kiện tụng", -1, 70),
    ("자기주식처분", "Bán cổ phiếu quỹ", -1, 50),
    ("전환청구권행사", "CB chuyển thành cổ phiếu (pha loãng)", -1, 45),
    ("단일판매", "Hợp đồng bán hàng / cung cấp lớn", 1, 90),
    ("공급계약", "Hợp đồng bán hàng / cung cấp lớn", 1, 90),
    ("기술이전", "Chuyển giao / xuất khẩu công nghệ", 1, 85),
    ("품목허가", "Được cấp phép sản phẩm", 1, 85),
    ("잠정실적", "Kết quả kinh doanh sơ bộ", 0, 85),
    ("영업(잠정)실적", "Kết quả kinh doanh sơ bộ", 0, 85),
    ("손익구조", "Doanh thu / lợi nhuận biến động ≥ 30%", 0, 80),
    ("투자판단관련주요경영사항", "Sự kiện trọng yếu (thử nghiệm, phê duyệt…)", 0, 80),
    ("공개매수", "Chào mua công khai", 1, 80),
    ("무상증자", "Phát hành cổ phiếu thưởng", 1, 75),
    ("주식소각", "Hủy cổ phiếu", 1, 75),
    ("합병", "Sáp nhập", 0, 75),
    ("자기주식취득", "Mua cổ phiếu quỹ", 1, 70), ("자기주식 취득", "Mua cổ phiếu quỹ", 1, 70),
    ("최대주주변경", "Thay đổi cổ đông lớn nhất", 0, 70),
    ("신규시설투자", "Đầu tư nhà máy / cơ sở mới", 1, 65),
    ("타법인주식및출자증권취득", "Mua cổ phần công ty khác (M&A)", 0, 65),
    ("기술도입", "Nhận chuyển giao công nghệ", 1, 60),
    ("특허권취득", "Được cấp bằng sáng chế", 1, 55),
    ("현금ㆍ현물배당", "Chia cổ tức", 1, 55), ("현금·현물배당", "Chia cổ tức", 1, 55),
    ("대량보유상황보고서", "Cổ đông ≥ 5% thay đổi sở hữu", 0, 45),
]


def classify_dart(report_nm: str) -> Optional[tuple]:
    nm = (report_nm or "").replace(" ", "")
    for pat, label, pol, sc in DART_TYPES:
        if pat.replace(" ", "") in nm:
            return label, pol, sc
    return None


def dart_disclosures(days: int = 1, max_pages: int = 15) -> tuple[list[dict], Optional[str]]:
    """Công bố của DN NIÊM YẾT (KOSPI/KOSDAQ) trong `days` ngày gần nhất, chỉ loại có giá trị.
    → (danh sách, lỗi hoặc None)."""
    try:
        import dart_api
    except Exception as ex:
        return [], f"không nạp được dart_api: {ex}"
    key = dart_api.api_key()
    if not key:
        return [], "thiếu DART_API_KEY"
    end = _dt.date.today()
    bgn = end - _dt.timedelta(days=max(days - 1, 0))
    out, err = [], None
    for page in range(1, max_pages + 1):
        try:
            j = requests.get("https://opendart.fss.or.kr/api/list.json",
                             params={"crtfc_key": key, "bgn_de": f"{bgn:%Y%m%d}",
                                     "end_de": f"{end:%Y%m%d}", "page_count": 100,
                                     "page_no": page}, timeout=15).json()
        except Exception as ex:
            err = str(ex)[:80]
            break
        if j.get("status") not in ("000",):
            if j.get("status") != "013":                  # 013 = không có dữ liệu
                err = f"DART {j.get('status')}: {j.get('message')}"
            break
        for it in j.get("list", []):
            if it.get("corp_cls") not in ("Y", "K") or not it.get("stock_code"):
                continue
            c = classify_dart(it.get("report_nm"))
            if not c:
                continue
            label, pol, sc = c
            rpt = (it.get("report_nm") or "").strip()
            out.append({"date": it.get("rcept_dt"), "rcept_no": it.get("rcept_no"),
                        "market": "KOSPI" if it.get("corp_cls") == "Y" else "KOSDAQ",
                        "ticker": str(it.get("stock_code")).zfill(6),
                        "name": it.get("corp_name"), "report": rpt, "label": label,
                        "pol": pol, "score": sc - (10 if "정정" in rpt else 0),
                        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no')}"})
        if page >= (j.get("total_page") or 1):
            break
    out.sort(key=lambda x: x["rcept_no"] or "", reverse=True)
    return out, err


def parallel(fn, keys: list, workers: int = 5) -> dict:
    out = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, k): k for k in keys}
        for f in _cf.as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = None
    return out
