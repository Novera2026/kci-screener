"""
Hồ sơ định giá 1 mã — mỗi bước của quy trình ghi phần của mình, bước sau đọc lại (và trang sàng lọc ghi cùng định
dạng). File JSON: dossiers/{mã}_{YYYYMMDD}.json — một hồ sơ mỗi mã mỗi ngày, ghi đè từng mục khi chạy lại.

Không đưa lên git (dữ liệu người dùng). Trên Streamlit Cloud thư mục mất khi App khởi động lại.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(ROOT, "dossiers")


def _clean(x):
    """JSON không nhận NaN/inf → None; tuple → list; số numpy → float."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        try:
            x = x.item()
        except Exception:
            pass
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def path_for(ticker: str, day: Optional[_dt.date] = None, base: str = DIR) -> str:
    day = day or _dt.date.today()
    return os.path.join(base, f"{ticker}_{day:%Y%m%d}.json")


def load(ticker: str, day: Optional[_dt.date] = None, base: str = DIR) -> dict:
    p = path_for(ticker, day, base)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


KEEP_DAYS = 30            # hồ sơ cũ hơn chừng này ngày bị xóa
MAX_FILES = 3000          # và không giữ quá chừng này file (xóa file cũ nhất)
_last_prune: dict = {}


def prune(base: str = DIR, keep_days: int = KEEP_DAYS, max_files: int = MAX_FILES,
          today: Optional[_dt.date] = None) -> int:
    """Dọn thư mục hồ sơ: mọi lượt định giá (kể cả người lạ trên app công khai) đều ghi một file — trước 15/11/2026
    thư mục chỉ lớn lên. Trả số file đã xóa."""
    if not os.path.isdir(base):
        return 0
    today = today or _dt.date.today()
    files = []
    for fn in os.listdir(base):
        if not fn.endswith(".json") or "_" not in fn:
            continue
        try:
            day = _dt.datetime.strptime(fn[:-5].rsplit("_", 1)[1], "%Y%m%d").date()
        except ValueError:
            continue
        files.append((day, fn))
    files.sort()
    old = [fn for d, fn in files if (today - d).days > keep_days]
    rest = [fn for d, fn in files if (today - d).days <= keep_days]
    extra = rest[:max(len(rest) - max_files, 0)]
    n = 0
    for fn in old + extra:
        try:
            os.remove(os.path.join(base, fn))
            n += 1
        except OSError:
            pass
    return n


def save_section(ticker: str, section: str, data, day: Optional[_dt.date] = None, base: str = DIR,
                 meta: Optional[dict] = None) -> str:
    """Ghi/ghi đè 1 mục (vd 'peers', 'financials', 'quality', 'valuation', 'overall') của hồ sơ hôm nay."""
    os.makedirs(base, exist_ok=True)
    import time as _time
    if _time.time() - _last_prune.get(base, 0) > 3600:      # dọn tối đa mỗi giờ một lần
        _last_prune[base] = _time.time()
        prune(base)
    d = load(ticker, day, base)
    d.setdefault("ticker", ticker)
    d.setdefault("sections", {})[section] = {"saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
                                              "data": _clean(data)}
    if meta:
        d.setdefault("meta", {}).update(_clean(meta))
    p = path_for(ticker, day, base)
    import threading as _th
    tmp = f"{p}.{os.getpid()}.{_th.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def section(ticker: str, name: str, day: Optional[_dt.date] = None, base: str = DIR):
    return ((load(ticker, day, base).get("sections") or {}).get(name) or {}).get("data")


def list_dossiers(base: str = DIR) -> list[dict]:
    """Mọi hồ sơ: [{ticker, date, sections, path}] mới nhất trước."""
    if not os.path.isdir(base):
        return []
    out = []
    for fn in os.listdir(base):
        if fn.endswith(".json") and "_" in fn:
            tk, day = fn[:-5].rsplit("_", 1)
            d = load(tk, _dt.datetime.strptime(day, "%Y%m%d").date(), base)
            out.append({"ticker": tk, "date": day, "sections": sorted((d.get("sections") or {}).keys()),
                        "name": (d.get("meta") or {}).get("name"), "path": os.path.join(base, fn)})
    return sorted(out, key=lambda r: r["date"], reverse=True)
