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
import json
import os
import contextlib
import threading
from dataclasses import dataclass, field, asdict
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
    last = None
    for _ in range(n):
        try:
            r = fn()
            if r is None:
                time.sleep(w); continue
            if hasattr(r, "empty") and r.empty:
                time.sleep(w); continue
            return r
        except Exception as e:  # noqa
            last = e
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
    import re
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
        self.df = self._merge_full(cur, full_path)
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
            from pykrx import stock
            d = _today_kr()
            with _quiet():
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
        """Nhập tên/ticker -> trả mã 6 số. Match: ticker > exact name > fuzzy."""
        q = str(query).strip()
        if not q:
            return None
        # 1) đã là ticker?
        digits = q.zfill(6) if q.isdigit() else None
        if digits and len(digits) == 6:
            return digits
        # 2) match registry (en/kr, không phân biệt hoa thường)
        ql = q.lower()
        for col in ("name_en", "name_kr"):
            hit = self.df[self.df[col].str.lower() == ql]
            if not hit.empty:
                return hit["ticker"].iloc[0]
        # 3) match chứa chuỗi trong registry
        for col in ("name_en", "name_kr"):
            hit = self.df[self.df[col].str.lower().str.contains(ql, na=False, regex=False)]
            if not hit.empty:
                return hit["ticker"].iloc[0]
        # 4) fuzzy trên registry + toàn KRX
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
                return best[2]
        except Exception:
            # fallback: quét KRX exact-contains
            self._load_krx_names()
            for t, nm in self._krx_name_map.items():
                if ql in nm.lower():
                    return t
        return None

    def resolve_many(self, queries: list[str]) -> dict[str, Optional[str]]:
        return {q: self.resolve(q) for q in queries}


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
        totals = {d.get("code"): d.get("value") for d in (j.get("totalInfos") or [])}
        # giá hiện tại: phiên gần nhất trong dealTrendInfos
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
    from toss_api import TossClient  # lazy import, chỉ khi dùng
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


def fetch_one(ticker: str, registry: Optional[TickerRegistry] = None,
              prefer: str = "pykrx") -> StockRow:
    """Kéo 1 mã, gộp đa nguồn theo thứ tự ưu tiên.

    prefer = 'pykrx' | 'naver' | 'toss'.
      - toss : giá realtime chính thức, nhưng fundamentals lấy từ pykrx/naver.
    Logic: đi theo order, field nào chưa có thì lấy từ nguồn kế tiếp (nguồn trước
    được ưu tiên cho field đã có)."""
    orders = {
        "pykrx": ["pykrx", "naver"],
        "naver": ["naver", "pykrx"],
        "toss":  ["toss", "pykrx", "naver"],   # Toss giá, pykrx/naver fundamentals
    }
    order = orders.get(prefer, ["pykrx", "naver"])

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
        # đủ dữ liệu cốt lõi thì dừng sớm
        if row.price is not None and row.per is not None and row.market_cap is not None:
            break
    row.source = "+".join(used)
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
               progress=None) -> tuple[list[dict], list[str]]:
    """Cào nhiều mã SONG SONG (mặc định 5 luồng) — nhanh hơn nhiều mà vẫn lịch sự
    với máy chủ Naver. KHÔNG đổi độ chính xác: mỗi mã cào độc lập, kết quả giữ
    đúng ánh xạ mã↔dòng và đúng THỨ TỰ nhập.

    - retries: số lần thử lại thêm cho mã trả về RỖNG (mất giá) — lấp chỗ thiếu.
    - progress(done, total): callback cập nhật tiến trình (gọi mỗi mã xong).
    Trả (list[dict] theo thứ tự nhập, list[str] mã không resolve được).
    """
    import concurrent.futures as _cf
    registry = registry or TickerRegistry()

    resolved: list[tuple[int, str]] = []   # (vị trí trong kết quả, ticker)
    unresolved: list[str] = []
    for q in queries:
        tk = registry.resolve(q)
        if tk is None:
            unresolved.append(q)
        else:
            resolved.append((len(resolved), tk))

    rows: list[Optional[dict]] = [None] * len(resolved)

    def _work(tk: str) -> dict:
        last = None
        for attempt in range(retries + 1):
            try:
                r = fetch_one(tk, registry=registry, prefer=prefer)
                if r.price is not None:        # có giá = coi như thành công
                    return r.to_dict()
                last = r
            except Exception as ex:
                last = StockRow(ticker=tk, note=f"err: {str(ex)[:40]}")
            if attempt < retries:
                time.sleep(RETRY_WAIT)         # nghỉ rồi thử lại mã bị rỗng
        return (last or StockRow(ticker=tk, note="no data")).to_dict()

    done = 0
    total = len(resolved)
    with _cf.ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futs = {ex.submit(_work, tk): idx for idx, tk in resolved}
        for fut in _cf.as_completed(futs):
            rows[futs[fut]] = fut.result()
            done += 1
            if progress:
                progress(done, total)
    return [r for r in rows if r is not None], unresolved


def sector_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """Chỉ số toàn ngành. Aggregate P/E = ΣVốn hóa / ΣLN ròng (đúng chuẩn,
    không phải trung bình cộng P/E). Kèm median để tham chiếu."""
    if df.empty:
        return pd.DataFrame()
    out = []
    groups = df.groupby("sector", dropna=False) if df["sector"].notna().any() \
        else [("Tất cả", df)]
    for sec, g in groups:
        cap = g["market_cap"].sum(min_count=1)
        ni = g["net_income"].sum(min_count=1)
        agg_pe = (cap / ni) if (cap and ni and ni > 0) else None
        # aggregate P/B = ΣVốn hóa / Σ(BPS*shares) ~ ΣVốn hóa / ΣBookValue
        book = (g["bps"] * (g["market_cap"] / g["price"])).sum(min_count=1) \
            if g["price"].notna().any() else None
        agg_pb = (cap / book) if (cap and book and book > 0) else None
        out.append({
            "sector": sec,
            "n": int(g["ticker"].notna().sum()),
            "agg_P/E": round(agg_pe, 2) if agg_pe else None,
            "median_P/E": round(g["per"].median(), 2) if g["per"].notna().any() else None,
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
