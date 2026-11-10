"""
KCI Screener — cào dữ liệu realtime hàng loạt cho cổ phiếu KOSPI/KOSDAQ.

Quy trình (giống VBA nhưng hiện đại):
  1. Nhập danh sách TÊN hoặc TICKER (tiếng Hàn/Anh).
  2. Hệ thống tự resolve tên -> mã 6 số (registry + danh sách KRX đầy đủ).
  3. Tự kéo data hàng loạt: giá, 52w high/low, market cap, PER, PBR, EPS, BPS, DIV, net income.
  4. Ra bảng đầy đủ + tính chỉ số toàn ngành (aggregate P/E, median, P/B, dividend yield...).

Nguồn dữ liệu (đa tầng, tự fallback):
  - pykrx (KRX official)  -> primary
  - Naver Finance JSON    -> fallback (chính nguồn VBA cũ dùng)

Phụ thuộc: pip install pykrx pandas requests openpyxl rapidfuzz
"""

from __future__ import annotations
import datetime as _dt
import time
import os
import re
import contextlib
import threading
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd


# _quiet đổi stdout/stderr + logging ở mức TOÀN CỤC → không an toàn đa luồng.
# Khóa này đảm bảo mỗi lúc chỉ 1 luồng ở trong vùng "im lặng" (chỉ ảnh hưởng
# pykrx — nguồn fallback hiếm khi gọi ở chế độ naver, nên gần như không nghẽn).
_QUIET_LOCK = threading.Lock()


@contextlib.contextmanager
def _quiet():
    """Nuốt stdout/stderr + logging của pykrx (nó tự in 'KRX 로그인 실패',
    'Error occurred...' khi một endpoint KRX bị chặn — vô hại vì có fallback Naver).
    Có khóa để dùng an toàn trong cào đa luồng."""
    import logging
    with _QUIET_LOCK:
        with open(os.devnull, "w") as dn, \
                contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):
            logging.disable(logging.CRITICAL)
            try:
                yield
            finally:
                logging.disable(logging.NOTSET)

# ----------------------------------------------------------------------------
# Cấu hình
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "tickers.csv")          # curated: sector EN + theme
FULL_REGISTRY_PATH = os.path.join(HERE, "tickers_all.csv")  # toàn sàn + industry_kr (업종)
RETRY = 4          # số lần thử lại mỗi call mạng
RETRY_WAIT = 1.5   # giây giữa các lần thử
NAVER_TIMEOUT = 8  # giây

# ----------------------------------------------------------------------------
# Tiện ích
# ----------------------------------------------------------------------------
def _today_kr() -> str:
    return _dt.datetime.now().strftime("%Y%m%d")


def _last_n_days(n: int = 400) -> tuple[str, str]:
    end = _dt.date.today()
    start = end - _dt.timedelta(days=n)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _retry(fn, n=RETRY, w=RETRY_WAIT):
    """Thử lại fn() tới khi ra kết quả khác rỗng, nếu không trả None."""
    for _ in range(n):
        try:
            r = fn()
            if r is None:
                time.sleep(w); continue
            if hasattr(r, "empty") and r.empty:
                time.sleep(w); continue
            return r
        except Exception:
            time.sleep(w)
    return None


def _to_num(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    # bóc các hậu tố hay gặp của Naver: 26.88배, 12,372원, 0.50%, 1,668주
    s = (str(x).replace(",", "").replace("%", "").replace("원", "")
         .replace("배", "").replace("주", "").strip())
    if s in ("", "-", "N/A", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


_KR_UNITS = {"조": 1e12, "억": 1e8, "만": 1e4}
def _parse_kr_won(x) -> Optional[float]:
    """Parse số tiền kiểu Hàn '1,943조 8,876억' -> won (float)."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d,]+)\s*([조억만])", str(x)):
        total += float(num.replace(",", "")) * _KR_UNITS[unit]
        found = True
    return total if found else _to_num(x)


# ----------------------------------------------------------------------------
# Registry + name resolver
# ----------------------------------------------------------------------------
class TickerRegistry:
    """Kho lưu ticker (giống vùng lưu mã trong VBA) + resolve tên -> mã."""

    def __init__(self, path: str = REGISTRY_PATH,
                 full_path: str = FULL_REGISTRY_PATH):
        self.path = path
        cur = pd.read_csv(path, dtype={"ticker": str}) if os.path.exists(path) else \
            pd.DataFrame(columns=["name_en", "name_kr", "ticker", "sector",
                                  "market", "theme"])
        cur["ticker"] = cur["ticker"].astype(str).str.zfill(6)
        if "theme" not in cur.columns:
            cur["theme"] = ""
        # thứ tự dòng trong tickers.csv (bluechip xếp theo độ quan trọng) → ưu tiên khi
        # một tên ngắn khớp nhiều mã ("삼성", "KB"…); mã chỉ có ở danh sách toàn sàn xếp sau
        cur["_rank"] = range(len(cur))
        self.df = self._merge_full(cur, full_path)
        self.df["_rank"] = self.df["_rank"].fillna(len(cur) + 1e6)
        self._krx_name_map: dict[str, str] = {}   # ticker -> name_kr (toàn KRX)
        self._krx_loaded = False

    @staticmethod
    def _merge_full(cur: pd.DataFrame, full_path: str) -> pd.DataFrame:
        """Gộp registry curated với danh sách toàn sàn (tickers_all.csv).
        Curated giữ name_en/sector(EN)/theme; full bổ sung industry_kr (업종) cho
        TẤT CẢ mã + nạp các mã chưa có trong curated."""
        if "industry_kr" not in cur.columns:
            cur["industry_kr"] = pd.NA
        if not os.path.exists(full_path):
            return cur
        allp = pd.read_csv(full_path, dtype={"ticker": str})
        allp["ticker"] = allp["ticker"].astype(str).str.zfill(6)
        allp = allp.rename(columns={"name_kr": "_name_all", "market": "_market_all"})
        m = cur.drop(columns=["industry_kr"]).merge(
            allp[["ticker", "_name_all", "industry_kr", "_market_all"]],
            on="ticker", how="outer")
        # mã chỉ có ở full -> điền tên/sàn từ full, sector/theme để trống
        m["name_kr"] = m["name_kr"].fillna(m["_name_all"])
        m["name_en"] = m["name_en"].fillna(m["_name_all"])
        m["market"] = m["market"].fillna(m["_market_all"])
        m["theme"] = m["theme"].fillna("")
        m = m.drop(columns=["_name_all", "_market_all"])
        return m

    # --- toàn bộ danh sách KRX để resolve cả mã ngoài registry ---
    def _load_krx_names(self):
        if self._krx_loaded:
            return
        try:
            d = _today_kr()
            with _quiet():
                from pykrx import stock    # import trong _quiet để nuốt log login KRX
                for mkt in ("KOSPI", "KOSDAQ"):
                    tks = _retry(lambda m=mkt: stock.get_market_ticker_list(d, market=m)) or []
                    for t in tks:
                        try:
                            self._krx_name_map[t] = stock.get_market_ticker_name(t)
                        except Exception:
                            pass
        except Exception:
            pass
        self._krx_loaded = True

    def sector_of(self, ticker: str) -> Optional[str]:
        row = self.df[self.df["ticker"] == ticker]
        if row.empty:
            return None
        v = row["sector"].iloc[0]
        return v if pd.notna(v) else None

    def industry_of(self, ticker: str) -> Optional[str]:
        """Ngành KRX (업종) của mã — có cho TẤT CẢ mã toàn sàn."""
        if "industry_kr" not in self.df.columns:
            return None
        row = self.df[self.df["ticker"] == ticker]
        if row.empty:
            return None
        v = row["industry_kr"].iloc[0]
        return v if pd.notna(v) else None

    def name_of(self, ticker: str) -> Optional[str]:
        row = self.df[self.df["ticker"] == ticker]
        if not row.empty:
            return row["name_en"].iloc[0]
        return self._krx_name_map.get(ticker)

    def resolve(self, query: str) -> Optional[str]:
        """Nhập tên/ticker -> trả mã 6 ký tự. Match: ticker > exact name > chứa chuỗi > fuzzy."""
        return self.resolve_info(query)["ticker"]

    def resolve_info(self, query: str) -> dict:
        """Như resolve() nhưng cho biết KHỚP KIỂU GÌ để UI cảnh báo khi đoán:
        {query, ticker, how: ticker|exact|contains|preferred|fuzzy|None, alts: [tên khác]}.
        `contains`/`fuzzy` = đoán; `alts` = các mã khác cũng khớp (tên mơ hồ)."""
        q = str(query).strip()
        out = {"query": q, "ticker": None, "how": None, "alts": []}
        if not q:
            return out
        # 1) đã là ticker? (6 số, hoặc mã chữ-số KRX mới cấp từ 2024 như 0001A0)
        if q.isdigit() and len(q) <= 6:
            return dict(out, ticker=q.zfill(6), how="ticker")
        if _TICKER_RE.match(q.upper()):
            return dict(out, ticker=q.upper(), how="ticker")
        # 2) match registry (en/kr, không phân biệt hoa thường)
        ql = q.lower()
        for col in ("name_en", "name_kr"):
            hit = self.df[self.df[col].str.lower() == ql]
            if not hit.empty:
                return dict(out, ticker=hit["ticker"].iloc[0], how="exact")
        # 3) cổ phiếu ưu đãi (삼성전자우, 현대차2우B…): registry chỉ có cổ phiếu thường →
        #    KHÔNG được đoán sang mã thường (dữ liệu khác hẳn). Thử mã ưu đãi chuẩn KRX.
        pm = _PREF_RE.match(q)
        base = self.resolve_info(pm.group(1)) if pm else None
        if base and base["how"] == "exact" and base["ticker"].isdigit():
            for d in "5789":
                code = base["ticker"][:5] + d
                if _norm_name(_naver_name(code)) == _norm_name(q):
                    return dict(out, ticker=code, how="preferred")
            return out                  # không xác minh được → để trống, không đoán bừa
        # 4) match chứa chuỗi: ưu tiên tên BẮT ĐẦU bằng chuỗi gõ, rồi bluechip curated
        #    (thứ tự tickers.csv), rồi tên ngắn — "삼성" → 삼성전자 chứ không phải 삼성제약
        hit = self.df[self.df["name_en"].str.lower().str.contains(ql, na=False, regex=False)
                      | self.df["name_kr"].str.lower().str.contains(ql, na=False, regex=False)]
        if not hit.empty:
            def _key(r):
                names = [str(r["name_en"]).lower(), str(r["name_kr"]).lower()]
                return (not any(n.startswith(ql) for n in names), r["_rank"],
                        min(len(n) for n in names))
            ranked = sorted(hit.to_dict("records"), key=_key)
            return dict(out, ticker=ranked[0]["ticker"], how="contains",
                        alts=[r["name_kr"] for r in ranked[1:4]])
        # 5) fuzzy trên registry + toàn KRX
        try:
            from rapidfuzz import process, fuzz
            choices = {}
            for _, r in self.df.iterrows():
                choices[r["ticker"]] = f"{r['name_en']} {r['name_kr']}"
            self._load_krx_names()
            for t, nm in self._krx_name_map.items():
                choices.setdefault(t, nm)
            best = process.extractOne(q, choices, scorer=fuzz.WRatio)
            if best and best[1] >= 80:
                # best[2] là key (ticker) khi truyền dict
                return dict(out, ticker=best[2], how="fuzzy")
        except Exception:
            # fallback: quét KRX exact-contains
            self._load_krx_names()
            for t, nm in self._krx_name_map.items():
                if ql in nm.lower():
                    return dict(out, ticker=t, how="contains")
        return out

    def resolve_many(self, queries: list[str]) -> dict[str, Optional[str]]:
        return {q: self.resolve(q) for q in queries}


# Mã KRX: 6 số, hoặc chữ-số kiểu mới (0001A0, 0126Z0) cho mã niêm yết từ 2024
_TICKER_RE = re.compile(r"^[0-9][0-9A-Z]{5}$")
# Tên cổ phiếu ưu đãi: <tên gốc>[số]우[B/C][(전환)]
_PREF_RE = re.compile(r"^(.+?)\d?우[A-C]?(\(전환\))?$")


def _norm_name(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _naver_name(code: str) -> Optional[str]:
    """Tên Hàn của 1 mã theo Naver (để xác minh mã đoán ra). None nếu không có."""
    try:
        import requests
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=NAVER_TIMEOUT)
        return r.json().get("stockName") if r.ok else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Fetcher — kéo data 1 mã (đa nguồn)
# ----------------------------------------------------------------------------
@dataclass
class StockRow:
    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry_kr: Optional[str] = None      # ngành KRX (업종) — có cho mọi mã toàn sàn
    price: Optional[float] = None          # giá hiện tại (종가 mới nhất)
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    market_cap: Optional[float] = None     # 시가총액 (KRW)
    shares: Optional[float] = None         # 상장주식수
    per: Optional[float] = None
    pbr: Optional[float] = None
    eps: Optional[float] = None
    bps: Optional[float] = None
    div_yield: Optional[float] = None      # DIV %
    dps: Optional[float] = None
    net_income: Optional[float] = None     # ước từ market_cap/PER hoặc EPS*shares
    source: str = ""
    note: str = ""

    def to_dict(self):
        return asdict(self)


def _fetch_pykrx(ticker: str) -> StockRow:
    """Primary: pykrx (KRX official)."""
    row = StockRow(ticker=ticker, source="pykrx")
    s, e = _last_n_days(400)

    with _quiet():  # nuốt log nội bộ của pykrx (kể cả lúc import chạy login KRX)
        from pykrx import stock
        # Giá + 52w high/low từ OHLCV ~1 năm
        ohlcv = _retry(lambda: stock.get_market_ohlcv_by_date(s, e, ticker))
        if ohlcv is not None and not ohlcv.empty:
            row.price = _to_num(ohlcv["종가"].iloc[-1])
            last_252 = ohlcv.tail(252)
            row.high_52w = _to_num(last_252["고가"].max())
            row.low_52w = _to_num(last_252["저가"].min())

        # Market cap + shares
        cap = _retry(lambda: stock.get_market_cap_by_date(s, e, ticker))
        if cap is not None and not cap.empty:
            last = cap.iloc[-1]
            row.market_cap = _to_num(last.get("시가총액"))
            row.shares = _to_num(last.get("상장주식수"))

        # Fundamentals PER/PBR/EPS/BPS/DIV/DPS
        fund = _retry(lambda: stock.get_market_fundamental_by_date(s, e, ticker))
        if fund is not None and not fund.empty:
            last = fund.iloc[-1]
            row.per = _to_num(last.get("PER"))
            row.pbr = _to_num(last.get("PBR"))
            row.eps = _to_num(last.get("EPS"))
            row.bps = _to_num(last.get("BPS"))
            row.div_yield = _to_num(last.get("DIV"))
            row.dps = _to_num(last.get("DPS"))

    _derive_net_income(row)
    return row


def _fetch_naver(ticker: str) -> StockRow:
    """Fallback: Naver Finance JSON (nguồn VBA cũ). Chạy trên máy không bị chặn."""
    import requests
    row = StockRow(ticker=ticker, source="naver")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}

    # API tổng hợp mobile: chứa per/pbr/eps/bps/52w/marketValue
    url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
    try:
        r = requests.get(url, headers=headers, timeout=NAVER_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        row.name = j.get("stockName")
        totals = {d.get("code"): d.get("value") for d in (j.get("totalInfos") or [])}
        # giá tạm: đóng cửa phiên TRƯỚC (dealTrendInfos) — fetch_one/fetch_many ghi đè bằng
        # giá realtime (fetch_live_prices); P/E, vốn hóa ở totalInfos đã theo giá hiện tại
        deals = j.get("dealTrendInfos") or []
        if deals:
            row.price = _to_num(deals[0].get("closePrice"))
        row.market_cap = _parse_kr_won(totals.get("marketValue"))
        row.per = _to_num(totals.get("per"))
        row.pbr = _to_num(totals.get("pbr"))
        row.eps = _to_num(totals.get("eps"))
        row.bps = _to_num(totals.get("bps"))
        row.div_yield = _to_num(totals.get("dividendYieldRatio"))
        row.dps = _to_num(totals.get("dividend"))
        row.high_52w = _to_num(totals.get("highPriceOf52Weeks"))
        row.low_52w = _to_num(totals.get("lowPriceOf52Weeks"))
    except Exception as ex:
        row.note = f"naver integration fail: {str(ex)[:60]}"

    # itemSummary bổ sung giá nếu thiếu
    if row.price is None:
        try:
            url2 = f"https://api.finance.naver.com/service/itemSummary.naver?itemcode={ticker}"
            r2 = requests.get(url2, headers=headers, timeout=NAVER_TIMEOUT)
            j2 = r2.json()
            row.price = _to_num(j2.get("now"))
            row.per = row.per or _to_num(j2.get("per"))
            row.pbr = row.pbr or _to_num(j2.get("pbr"))
            row.eps = row.eps or _to_num(j2.get("eps"))
            row.bps = row.bps or _to_num(j2.get("bps"))
            row.market_cap = row.market_cap or _to_num(j2.get("marketSum"))
        except Exception:
            pass

    _derive_net_income(row)
    return row


def _derive_net_income(row: StockRow):
    """Ước net income: ưu tiên market_cap/PER, fallback EPS*shares."""
    if row.market_cap and row.per and row.per > 0:
        row.net_income = row.market_cap / row.per
    elif row.eps and row.shares:
        row.net_income = row.eps * row.shares
    # nếu market_cap thiếu mà có price*shares
    if row.market_cap is None and row.price and row.shares:
        row.market_cap = row.price * row.shares


def _fetch_toss(ticker: str) -> StockRow:
    """Toss Open API: giá realtime chính thức + 52w (không có fundamentals)."""
    cli = _toss_client()
    row = StockRow(ticker=ticker, source="toss")
    # Toss = giá realtime CHÍNH THỨC. 52T/fundamentals để pykrx/Naver lo
    # (candle Toss tối đa 200 phiên, không đủ 52 tuần chuẩn).
    try:
        p = cli.get_price(ticker)
        row.price = p.get("price")
    except Exception as e:
        row.note = f"toss price fail: {str(e)[:60]}"
    return row


_TOSS_SINGLETON = None
def _toss_client():
    """Tái dùng 1 TossClient (cache token) cho cả batch."""
    global _TOSS_SINGLETON
    if _TOSS_SINGLETON is None:
        from toss_api import TossClient
        _TOSS_SINGLETON = TossClient()
    return _TOSS_SINGLETON


_FETCHERS = {"pykrx": _fetch_pykrx, "naver": _fetch_naver, "toss": _fetch_toss}
_MERGE_FIELDS = ("price", "high_52w", "low_52w", "market_cap", "shares",
                 "per", "pbr", "eps", "bps", "div_yield", "dps")


def fetch_live_prices(tickers: list[str]) -> dict[str, float]:
    """Giá khớp lệnh REALTIME (trong phiên) / đóng cửa hôm nay (sau phiên) từ Naver polling,
    50 mã/lần gọi. {mã: giá}; mã lỗi/ngừng giao dịch không có trong kết quả."""
    import requests
    out: dict[str, float] = {}
    for i in range(0, len(tickers), 50):
        chunk = ",".join(tickers[i:i + 50])
        try:
            j = requests.get(
                f"https://polling.finance.naver.com/api/realtime/domestic/stock/{chunk}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=NAVER_TIMEOUT).json()
        except Exception:
            continue
        for d in (j or {}).get("datas", []):
            px = _to_num(d.get("closePrice"))
            if px and px > 0:
                out[str(d.get("itemCode"))] = px
    return out


def _overlay_live(rows: list[StockRow]) -> None:
    """Ghi đè giá bằng giá realtime cho các dòng không lấy giá từ Toss (đã realtime)."""
    need = [r for r in rows if "toss" not in r.source.split("+")]
    live = fetch_live_prices([r.ticker for r in need]) if need else {}
    for r in need:
        px = live.get(r.ticker)
        if px:
            r.price = px
            r.source = (r.source + "+rt") if r.source else "rt"


def fetch_one(ticker: str, registry: Optional[TickerRegistry] = None,
              prefer: str = "pykrx", live: bool = True) -> StockRow:
    """Kéo 1 mã, gộp đa nguồn theo thứ tự ưu tiên.

    prefer = 'pykrx' | 'naver' | 'toss'.
      - toss : giá realtime chính thức, nhưng fundamentals lấy từ pykrx/naver.
    Logic: đi theo order, field nào chưa có thì lấy từ nguồn kế tiếp (nguồn trước
    được ưu tiên cho field đã có). live=True: giá cuối cùng là giá realtime Naver
    (fetch_many tắt cờ này rồi lấy realtime theo lô 50 mã)."""
    orders = {
        "pykrx": ["pykrx", "naver"],
        "naver": ["naver"],            # Naver tự đủ (giá/52T/PER/PBR/EPS/BPS/cổ tức);
                                       # KHÔNG fallback pykrx (chậm, bị KRX chặn, vô dụng
                                       # trên cloud — mã lỗ có per=None là ĐÚNG, đừng cào lại).
        "toss":  ["toss", "naver"],    # Toss giá realtime, Naver fundamentals (bỏ pykrx)
    }
    order = orders.get(prefer, ["naver"])

    row = StockRow(ticker=ticker)
    used = []
    for src in order:
        try:
            r = _FETCHERS[src](ticker)
        except Exception as ex:
            if not row.note:
                row.note = f"{src} error: {str(ex)[:50]}"
            continue
        got = False
        for f in _MERGE_FIELDS:
            v = getattr(r, f)
            if v is not None and getattr(row, f) is None:
                setattr(row, f, v); got = True
        if got:
            used.append(src)
        row.name = row.name or r.name
        # đủ dữ liệu cốt lõi thì dừng sớm
        if row.price is not None and row.per is not None and row.market_cap is not None:
            break
    row.source = "+".join(used)
    if live:
        _overlay_live([row])
    _derive_net_income(row)
    if registry:
        row.name = registry.name_of(ticker) or row.name
        row.sector = registry.sector_of(ticker)
        row.industry_kr = registry.industry_of(ticker)
    return row


# ----------------------------------------------------------------------------
# Batch + aggregation
# ----------------------------------------------------------------------------
COLUMN_ORDER = ["ticker", "name", "sector", "price", "high_52w", "low_52w",
                "pct_from_high", "market_cap", "per", "pbr", "eps", "bps",
                "div_yield", "dps", "net_income", "source", "note"]

COLUMN_KR = {
    "ticker": "Mã", "name": "Tên", "sector": "Ngành", "price": "Giá",
    "high_52w": "Đỉnh 52T", "low_52w": "Đáy 52T", "pct_from_high": "% so đỉnh",
    "market_cap": "Vốn hóa", "per": "P/E", "pbr": "P/B", "eps": "EPS",
    "bps": "BPS", "div_yield": "Tỷ suất CT %", "dps": "DPS",
    "net_income": "LN ròng (ước)", "source": "Nguồn", "note": "Ghi chú",
}


def fetch_batch(queries: list[str], registry: Optional[TickerRegistry] = None,
                prefer: str = "pykrx", pause: float = 0.3, verbose: bool = True) -> pd.DataFrame:
    """Nhập danh sách tên/ticker -> DataFrame đầy đủ."""
    registry = registry or TickerRegistry()
    rows = []
    unresolved = []
    for q in queries:
        tk = registry.resolve(q)
        if tk is None:
            unresolved.append(q)
            if verbose:
                print(f"  [!] Không resolve được: {q}")
            continue
        if verbose:
            print(f"  [-] {q} -> {tk} ({registry.name_of(tk) or '?'})")
        row = fetch_one(tk, registry=registry, prefer=prefer)
        rows.append(row.to_dict())
        time.sleep(pause)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["pct_from_high"] = df.apply(
            lambda r: round((r["price"] / r["high_52w"] - 1) * 100, 1)
            if r.get("price") and r.get("high_52w") else None, axis=1)
        df = df.reindex(columns=COLUMN_ORDER)
    df.attrs["unresolved"] = unresolved
    return df


def fetch_many(queries: list[str], registry: Optional[TickerRegistry] = None,
               prefer: str = "naver", max_workers: int = 5, retries: int = 1,
               progress=None, resolutions: Optional[list] = None,
               live: bool = True) -> tuple[list[dict], list[str]]:
    """Cào nhiều mã SONG SONG (mặc định 5 luồng) — nhanh hơn nhiều mà vẫn lịch sự
    với máy chủ Naver. KHÔNG đổi độ chính xác: mỗi mã cào độc lập, kết quả giữ
    đúng ánh xạ mã↔dòng và đúng THỨ TỰ nhập.

    - retries: số lần thử lại thêm cho mã trả về RỖNG (mất giá) — lấp chỗ thiếu.
    - progress(done, total): callback cập nhật tiến trình (gọi mỗi mã xong).
    - resolutions: truyền list rỗng để nhận lại cách khớp từng dòng nhập
      (TickerRegistry.resolve_info + cờ 'dup' khi trùng mã đã có).
    - Mã nhập TRÙNG chỉ cào 1 lần (trùng làm méo median ngành & đếm peer).
    - live: giá cuối cùng là giá realtime Naver (1 lần gọi / 50 mã).
    Trả (list[dict] theo thứ tự nhập, list[str] mã không resolve được).
    """
    import concurrent.futures as _cf
    registry = registry or TickerRegistry()

    resolved: list[tuple[int, str]] = []   # (vị trí trong kết quả, ticker)
    unresolved: list[str] = []
    seen: set[str] = set()
    for q in queries:
        info = registry.resolve_info(q)
        tk = info["ticker"]
        if tk is None:
            unresolved.append(q)
        elif tk in seen:
            info["dup"] = True
        else:
            seen.add(tk)
            resolved.append((len(resolved), tk))
        if resolutions is not None:
            resolutions.append(info)

    rows: list[Optional[StockRow]] = [None] * len(resolved)

    def _work(tk: str) -> StockRow:
        last = None
        for attempt in range(retries + 1):
            try:
                r = fetch_one(tk, registry=registry, prefer=prefer, live=False)
                if r.price is not None:        # có giá = coi như thành công
                    return r
                last = r
            except Exception as ex:
                last = StockRow(ticker=tk, note=f"err: {str(ex)[:40]}")
            if attempt < retries:
                time.sleep(RETRY_WAIT)         # nghỉ rồi thử lại mã bị rỗng
        return last or StockRow(ticker=tk, note="no data")

    done = 0
    total = len(resolved)
    with _cf.ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futs = {ex.submit(_work, tk): idx for idx, tk in resolved}
        for fut in _cf.as_completed(futs):
            rows[futs[fut]] = fut.result()
            done += 1
            if progress:
                progress(done, total)
    got = [r for r in rows if r is not None]
    if live:
        _overlay_live(got)
    for r in got:
        _derive_net_income(r)
    return [r.to_dict() for r in got], unresolved


# ----------------------------------------------------------------------------
# Tham số thị trường LIVE: Rf (TPCP 10Y) + Beta (52 tuần vs KOSPI)
# ----------------------------------------------------------------------------
RF_LAST: dict = {}          # nguồn + thời điểm của lần lấy Rf gần nhất (để giao diện ghi rõ)


def fetch_kgb10y_detail() -> dict:
    """Lợi suất TPCP Hàn 10 năm (국고채 10년) — {value (thập phân), source, asof}.

    Ưu tiên Naver thời gian thực (KR10YT=RR, cập nhật trong phiên). Dự phòng FRED IRLTLT01KRM156N — chỉ là trung
    bình THÁNG và trễ 1–2 tháng (11/2026: FRED còn tháng 9 = 4,454% trong khi thị trường 3,87% → Ke cao hơn thực tế
    0,58 điểm, giá trị hợp lý bị đánh thấp ~11%). Lỗi cả hai → {}."""
    import requests
    try:
        j = requests.get("https://api.stock.naver.com/marketindex/bond/KR10YT=RR",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"},
                         timeout=10).json()
        v = float(str(j.get("closePrice")).replace(",", ""))
        if 0.0 < v < 20.0:
            out = {"value": v / 100.0, "source": "Naver 국고채 10년 (thời gian thực)",
                   "asof": str(j.get("localTradedAt") or "")[:16].replace("T", " ")}
            RF_LAST.clear(); RF_LAST.update(out)
            return out
    except Exception:
        pass
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=IRLTLT01KRM156N", timeout=10)
        r.raise_for_status()
        vals = [ln.split(",") for ln in r.text.strip().splitlines()[1:]]
        last = [v for v in vals if len(v) == 2 and v[1] not in (".", "")][-1]
        out = {"value": float(last[1]) / 100.0, "source": "FRED (trung bình tháng, có độ trễ)",
               "asof": last[0][:7]}
        RF_LAST.clear(); RF_LAST.update(out)
        return out
    except Exception:
        return {}


def fetch_kgb10y() -> Optional[float]:
    """Rf = lợi suất TPCP Hàn 10 năm dạng THẬP PHÂN (Naver thời gian thực, dự phòng FRED). None nếu lỗi."""
    return fetch_kgb10y_detail().get("value")


def fetch_weekly_closes(symbol: str) -> list[float]:
    """Giá đóng cửa TUẦN ~1.5 năm từ Naver chart (symbol mã hoặc 'KOSPI'). [] nếu lỗi."""
    try:
        import requests
        import json as _json
        s, e = _last_n_days(540)
        u = (f"https://api.finance.naver.com/siseJson.naver?symbol={symbol}"
             f"&requestType=1&startTime={s}&endTime={e}&timeframe=week")
        r = requests.get(u, headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://finance.naver.com/"}, timeout=10)
        rows = _json.loads(r.text.replace("'", '"'))
        return [float(x[4]) for x in rows[1:] if x and x[4] not in (None, "")]
    except Exception:
        return []


def beta_vs_market(stock_closes: list[float], market_closes: list[float],
                   lo: float = 0.5, hi: float = 1.8) -> Optional[float]:
    """Beta = cov(r_stock, r_market)/var(r_market) trên lợi suất TUẦN (giống 52주베타
    của WISEreport). Kẹp [lo, hi] theo house assumption. None nếu thiếu dữ liệu."""
    n = min(len(stock_closes), len(market_closes))
    if n < 30:
        return None
    s, m = stock_closes[-n:], market_closes[-n:]
    rs = [s[i] / s[i - 1] - 1 for i in range(1, n) if s[i - 1]]
    rm = [m[i] / m[i - 1] - 1 for i in range(1, n) if m[i - 1]]
    k = min(len(rs), len(rm))
    if k < 30:
        return None
    rs, rm = rs[-k:], rm[-k:]
    mb = sum(rm) / k
    var = sum((x - mb) ** 2 for x in rm) / k
    if var <= 0:
        return None
    ms = sum(rs) / k
    cov = sum((rs[i] - ms) * (rm[i] - mb) for i in range(k)) / k
    return max(lo, min(hi, cov / var))


def sector_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """Chỉ số toàn ngành. Aggregate P/E = ΣVốn hóa / ΣLN ròng (đúng chuẩn,
    không phải trung bình cộng P/E). Kèm median để tham chiếu.

    Nhóm theo ngành EN (curated) và — với mã ngoài curated — ngành KRX (업종), giống
    valuation.peer_key; median_P/E loại P/E ≤0 và >60 như median dùng để định giá."""
    if df.empty:
        return pd.DataFrame()
    out = []
    key = df["sector"].where(df["sector"].notna() & (df["sector"] != ""), None)
    if "industry_kr" in df.columns:
        key = key.where(key.notna(), df["industry_kr"])
    key = key.fillna("—")
    for sec, g in df.groupby(key, sort=False):
        cap = g["market_cap"].sum(min_count=1)
        ni = g["net_income"].sum(min_count=1)
        agg_pe = (cap / ni) if (cap and ni and ni > 0) else None
        # aggregate P/B = ΣVốn hóa / Σ(BPS*shares) ~ ΣVốn hóa / ΣBookValue
        book = (g["bps"] * (g["market_cap"] / g["price"])).sum(min_count=1) \
            if g["price"].notna().any() else None
        agg_pb = (cap / book) if (cap and book and book > 0) else None
        pe_ok = g["per"].where((g["per"] > 0) & (g["per"] <= 60))
        out.append({
            "sector": sec,
            "n": int(g["ticker"].notna().sum()),
            "agg_P/E": round(agg_pe, 2) if agg_pe else None,
            "median_P/E": round(pe_ok.median(), 2) if pe_ok.notna().any() else None,
            "mean_P/E": round(g["per"].mean(), 2) if g["per"].notna().any() else None,
            "agg_P/B": round(agg_pb, 2) if agg_pb else None,
            "median_P/B": round(g["pbr"].median(), 2) if g["pbr"].notna().any() else None,
            "median_DivYield%": round(g["div_yield"].median(), 2) if g["div_yield"].notna().any() else None,
            "total_MktCap": cap,
        })
    return pd.DataFrame(out).sort_values("total_MktCap", ascending=False, na_position="last")


# ----------------------------------------------------------------------------
# Export Excel
# ----------------------------------------------------------------------------
def export_excel(df: pd.DataFrame, agg: pd.DataFrame, path: str,
                 val: Optional[pd.DataFrame] = None,
                 table_df: Optional[pd.DataFrame] = None):
    """Xuất bảng + aggregate (+ định giá + bảng hiển thị nếu có) ra Excel formatted."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        if table_df is not None and not table_df.empty:    # bảng đúng như màn hình
            table_df.to_excel(xl, sheet_name="Bảng", index=False)
        d = df.copy()
        d.columns = [COLUMN_KR.get(c, c) for c in d.columns]
        d.to_excel(xl, sheet_name="Stocks", index=False)
        if not agg.empty:
            agg.to_excel(xl, sheet_name="Sector", index=False)
        if val is not None and not val.empty:
            val.to_excel(xl, sheet_name="Valuation", index=False)

        wb = xl.book
        head_fill = PatternFill("solid", fgColor="1F3864")
        head_font = Font(color="FFFFFF", bold=True, size=11)
        thin = Side(style="thin", color="D0D0D0")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for sh in wb.worksheets:
            for c in sh[1]:
                c.fill = head_fill; c.font = head_font
                c.alignment = Alignment(horizontal="center", vertical="center")
            for col in sh.columns:
                w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                sh.column_dimensions[get_column_letter(col[0].column)].width = min(max(w + 2, 10), 26)
            for r in sh.iter_rows(min_row=2):
                for c in r:
                    c.border = border
                    if isinstance(c.value, (int, float)):
                        c.number_format = "#,##0.00" if abs(c.value) < 1000 else "#,##0"
            sh.freeze_panes = "A2"
    return path


# ----------------------------------------------------------------------------
# Entry point đơn giản
# ----------------------------------------------------------------------------
def run(queries: list[str], out_path: str = "kci_screen_output.xlsx",
        prefer: str = "pykrx") -> tuple[pd.DataFrame, pd.DataFrame]:
    reg = TickerRegistry()
    print(f"== KCI Screener: {len(queries)} mã ==")
    df = fetch_batch(queries, registry=reg, prefer=prefer)
    agg = sector_aggregates(df)
    if not df.empty:
        export_excel(df, agg, out_path)
        print(f"\n[OK] Đã xuất: {out_path}")
        print(f"     {len(df)} mã | {df['per'].notna().sum()} mã có P/E")
    if df.attrs.get("unresolved"):
        print(f"[!] Chưa resolve: {df.attrs['unresolved']}")
    return df, agg


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        # Demo: so sánh P/E ngành ngân hàng
        args = ["KB Financial", "Shinhan Financial", "Hana Financial", "Woori Financial"]
    run(args)
