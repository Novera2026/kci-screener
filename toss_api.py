"""
Toss Securities Open API adapter — nguồn giá realtime CHÍNH THỨC cho KCI Screener.

Base URL : https://openapi.tossinvest.com
Auth     : OAuth 2.0 Client Credentials (Basic ID:SECRET -> Bearer token)
Dùng cho : giá hiện tại, candles (=> 52w high/low), stock master (name->ticker), tỷ giá.
KHÔNG có : PER/PBR/EPS/BPS  (fundamentals vẫn lấy từ pykrx/Naver trong screener.py).

⚠️ BẢO MẬT
  - KHÔNG hardcode key/secret. Đọc từ biến môi trường TOSS_CLIENT_ID / TOSS_CLIENT_SECRET.
  - Để credentials trong file .env cục bộ (xem .env.example). KHÔNG commit .env lên git.

⚠️ XÁC MINH ENDPOINT
  Các path/field dưới đây dựa trên tài liệu công khai (6/2026). Toss đang GA dần nên
  tên path/field có thể đổi. Hàm download_openapi_spec() tải spec chính thức
  (/openapi-docs/latest/openapi.json) để bạn đối chiếu chính xác trước khi chạy thật.

Phụ thuộc: pip install requests python-dotenv
"""

from __future__ import annotations
import os
import time
import json
import datetime as _dt
from typing import Optional, Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # tự nạp .env nếu có
except Exception:
    pass

BASE_URL = os.environ.get("TOSS_BASE_URL", "https://openapi.tossinvest.com")
TOKEN_PATH = "/oauth2/token"
DEFAULT_TIMEOUT = 10


def _first(d: dict, *keys, default=None):
    """Lấy giá trị đầu tiên có trong dict theo nhiều tên key (phòng schema đổi)."""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def _num(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


class TossClient:
    def __init__(self, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None,
                 base_url: str = BASE_URL):
        self.client_id = client_id or os.environ.get("TOSS_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET")
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Thiếu credentials. Đặt TOSS_CLIENT_ID / TOSS_CLIENT_SECRET trong .env "
                "hoặc biến môi trường. KHÔNG dán secret vào code/chat."
            )

    # ---------------- Auth ----------------
    def _get_token(self) -> str:
        # cache: refresh trước hạn 5 phút
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        url = self.base_url + TOKEN_PATH
        resp = requests.post(
            url,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),  # Basic auth
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        j = resp.json()
        self._token = _first(j, "access_token", "accessToken")
        exp = _first(j, "expires_in", "expiresIn", default=3600)
        self._token_exp = time.time() + float(exp)
        if not self._token:
            raise RuntimeError(f"Không lấy được access_token. Response: {j}")
        return self._token

    def _headers(self, account: Optional[str] = None) -> dict:
        h = {"Authorization": f"Bearer {self._get_token()}",
             "Accept": "application/json"}
        if account:
            h["X-Tossinvest-Account"] = account
        return h

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = self.base_url + path
        r = requests.get(url, headers=self._headers(), params=params or {},
                         timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        # Toss bọc payload trong {"result": ...}; lỗi trong {"error": ...}
        if isinstance(j, dict) and "result" in j:
            return j["result"]
        return j

    # ---------------- Market Data ----------------
    # Endpoint xác minh từ spec chính thức (openapi.json 6/2026):
    #   GET /api/v1/prices?symbols=...   (batch, tối đa 200 mã, field: lastPrice)
    #   GET /api/v1/candles?symbol=...&interval=1d|1m&count<=200
    #   GET /api/v1/price-limits?symbol=...
    #   GET /api/v1/stocks?symbols=...
    def get_prices(self, codes) -> dict:
        """Giá nhiều mã 1 lần. codes = list hoặc chuỗi 'a,b,c' (tối đa 200).
        Trả {code: price_float}."""
        if not isinstance(codes, str):
            codes = ",".join(codes)
        d = self._get("/api/v1/prices", {"symbols": codes})
        out = {}
        for item in (d or []):
            sym = _first(item, "symbol", "code")
            if sym is not None:
                out[str(sym)] = _num(_first(item, "lastPrice", "price", "currentPrice"))
        return out

    def get_price(self, stock_code: str) -> dict:
        """Giá hiện tại 1 mã. GET /api/v1/prices?symbols=<code>"""
        d = self._get("/api/v1/prices", {"symbols": stock_code})
        if isinstance(d, list):
            d = d[0] if d else {}
        return {
            "price": _num(_first(d, "lastPrice", "price", "currentPrice")),
            "timestamp": _first(d, "timestamp"),
            "currency": _first(d, "currency"),
            "raw": d,
        }

    def get_candles(self, stock_code: str, interval: str = "1d",
                    count: int = 200) -> list:
        """Candles. GET /api/v1/candles. interval: '1d' (ngày) hoặc '1m' (phút).
        ⚠️ count tối đa 200 → đỉnh/đáy chỉ ~200 phiên (xấp xỉ 9–10 tháng, KHÔNG đủ 52T)."""
        d = self._get("/api/v1/candles",
                      {"symbol": stock_code, "interval": interval,
                       "count": min(count, 200)})
        if isinstance(d, dict):
            d = _first(d, "candles", "items", "list", default=[])
        return d or []

    def get_52w_high_low(self, stock_code: str) -> tuple[Optional[float], Optional[float]]:
        """Đỉnh/đáy từ candles ngày. ⚠️ Tối đa 200 phiên (≈ xấp xỉ, không phải 52T chuẩn)
        — với 52T chuẩn nên dùng pykrx/Naver."""
        candles = self.get_candles(stock_code, interval="1d", count=200)
        highs, lows = [], []
        for c in candles:
            h = _num(_first(c, "highPrice", "high", "h"))
            l = _num(_first(c, "lowPrice", "low", "l"))
            if h is not None:
                highs.append(h)
            if l is not None:
                lows.append(l)
        return (max(highs) if highs else None, min(lows) if lows else None)

    def get_price_limit(self, stock_code: str) -> dict:
        """Trần/sàn. GET /api/v1/price-limits?symbol=<code>"""
        d = self._get("/api/v1/price-limits", {"symbol": stock_code})
        return {
            "upper": _num(_first(d, "upperLimitPrice", "upperLimit", "upper")),
            "lower": _num(_first(d, "lowerLimitPrice", "lowerLimit", "lower")),
            "raw": d,
        }

    # ---------------- Stock / Market Info ----------------
    def get_stocks(self, symbols) -> list:
        """Thông tin mã: name, market, securityType, sharesOutstanding...
        GET /api/v1/stocks?symbols=...  (yêu cầu danh sách mã, tối đa 200).
        ⚠️ API KHÔNG có endpoint liệt kê toàn bộ mã theo market — phải truyền sẵn list."""
        if not isinstance(symbols, str):
            symbols = ",".join(symbols)
        d = self._get("/api/v1/stocks", {"symbols": symbols})
        if isinstance(d, dict):
            d = _first(d, "stocks", "items", "list", default=[d])
        return d or []

    def get_exchange_rate(self) -> dict:
        """USD/KRW. GET /api/v1/exchange-rate"""
        return self._get("/api/v1/exchange-rate")

    # ---------------- Tiện ích ----------------
    def quote(self, stock_code: str) -> dict:
        """Gói gọn: giá + 52w (dùng trong screener)."""
        out = {"ticker": stock_code, "source": "toss"}
        try:
            p = self.get_price(stock_code)
            out["price"] = p["price"]
        except Exception as e:
            out["note"] = f"toss price fail: {str(e)[:60]}"
        try:
            hi, lo = self.get_52w_high_low(stock_code)
            out["high_52w"], out["low_52w"] = hi, lo
        except Exception as e:
            out.setdefault("note", "")
            out["note"] += f" | toss candles fail: {str(e)[:50]}"
        return out

    def download_openapi_spec(self, path: str = "toss_openapi.json") -> str:
        """Tải spec chính thức để đối chiếu endpoint/field chính xác."""
        url = self.base_url + "/openapi-docs/latest/openapi.json"
        r = requests.get(url, headers=self._headers(), timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r.json(), f, ensure_ascii=False, indent=2)
        return path


def enrich_registry_from_toss(codes, out_csv: str = "tickers_toss.csv") -> str:
    """Lấy thông tin mã từ Toss cho danh sách `codes` cho trước rồi ghi CSV.

    ⚠️ Toss Open API KHÔNG có endpoint liệt kê TOÀN BỘ mã theo market, nên phải
    truyền sẵn danh sách `codes` (vd lấy từ tickers.csv hoặc pykrx). Stock master
    Toss cũng không có cột 'sector' → cột sector để trống, bổ sung thủ công nếu cần.
    """
    import csv
    cli = TossClient()
    rows = []
    for s in cli.get_stocks(codes):
        code = _first(s, "symbol", "code", "stockCode")
        name = _first(s, "englishName", "name")
        name_kr = _first(s, "name", "korName", default=name)
        mkt = _first(s, "market", default="")
        if code:
            rows.append([name, name_kr, str(code).zfill(6), "", mkt])
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name_en", "name_kr", "ticker", "sector", "market"])
        w.writerows(rows)
    return out_csv


if __name__ == "__main__":
    # Test nhanh: cần TOSS_CLIENT_ID / TOSS_CLIENT_SECRET trong .env
    cli = TossClient()
    print("Token OK:", bool(cli._get_token()))
    print("Samsung price:", cli.get_price("005930"))
    print("Samsung 52w:", cli.get_52w_high_low("005930"))
