"""
Build `tickers_all.csv` — TOÀN BỘ cổ phiếu KOSPI/KOSDAQ kèm ngành KRX (업종),
cào từ Naver mobile API (ổn định, không cần KRX login).

  GET /api/stocks/industry                  -> 79 nhóm ngành (no, name)
  GET /api/stocks/industry/{no}?page&pageSize -> thành viên (itemCode, stockName, sosok)

sosok: '0' = KOSPI, '1' = KOSDAQ. Crawl theo ngành nên tự loại ETF/ETN.

Chạy lại khi muốn cập nhật danh sách:  python build_full_registry.py
"""
from __future__ import annotations
import csv
import re
import time
import requests

BASE = "https://m.stock.naver.com/api/stocks/industry"
THEME_BASE = "https://m.stock.naver.com/api/stocks/theme"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
OUT = "tickers_all.csv"
THEME_OUT = "themes_all.csv"
PAGE_SIZE = 100
PAUSE = 0.15

# Lọc bỏ: ETF/ETN (stockEndType != stock), SPAC (스팩), cổ phiếu ưu đãi (우선주).
_PREF_RE = re.compile(r"우[A-Z]?$")   # tên kết thúc 우 / 우B / 우C ...


def _excluded(name: str, code: str, end_type: str) -> bool:
    if end_type and end_type != "stock":          # ETF / ETN
        return True
    if not name:
        return True
    if "스팩" in name:                              # SPAC (기업인수목적)
        return True
    # ưu đãi: tên kết thúc 우 VÀ ticker không tận cùng 0 (cổ phiếu thường tận cùng 0,
    # ưu đãi tận cùng 5/7/9/K) — chừa các công ty thường tên kết thúc 우 (성우, 이오플로우...)
    if _PREF_RE.search(name) and not code.endswith("0"):
        return True
    return False


def _get(url, params=None):
    r = requests.get(url, headers=H, params=params or {}, timeout=10)
    r.raise_for_status()
    return r.json()


def list_industries() -> list[dict]:
    groups, page = [], 1
    while True:
        j = _get(BASE, {"page": page, "pageSize": 100})
        g = j.get("groups") or []
        groups.extend(g)
        if page * 100 >= j.get("totalCount", 0) or not g:
            break
        page += 1
    return groups


def _members(base: str, no: int) -> list[dict]:
    out, page = [], 1
    while True:
        j = _get(f"{base}/{no}", {"page": page, "pageSize": PAGE_SIZE})
        st = j.get("stocks") or []
        out.extend(st)
        total = j.get("totalCount", 0)
        if page * PAGE_SIZE >= total or not st:
            break
        page += 1
        time.sleep(PAUSE)
    return out


def industry_members(no: int) -> list[dict]:
    return _members(BASE, no)


def list_groups(base: str) -> list[dict]:
    groups, page = [], 1
    while True:
        j = _get(base, {"page": page, "pageSize": 100})
        g = j.get("groups") or []
        groups.extend(g)
        if page * 100 >= j.get("totalCount", 0) or not g:
            break
        page += 1
    return groups


def build_themes():
    """Cào toàn bộ 테마 (theme) của Naver -> themes_all.csv (theme_kr, ticker).
    Quan hệ nhiều-nhiều: 1 mã có thể thuộc nhiều chủ đề."""
    themes = list_groups(THEME_BASE)
    print(f"\nSố chủ đề (테마): {len(themes)}")
    rows, skipped = [], 0
    for i, g in enumerate(themes, 1):
        no, name = g["no"], g["name"]
        try:
            members = _members(THEME_BASE, no)
        except Exception as e:
            print(f"  [!] chủ đề {name} lỗi: {str(e)[:50]}")
            continue
        kept = 0
        for m in members:
            code = str(m.get("itemCode") or "").zfill(6)
            if not code or len(code) != 6:
                continue
            if _excluded(m.get("stockName") or "", code, m.get("stockEndType")):
                skipped += 1
                continue
            rows.append([name, code])
            kept += 1
        if i % 30 == 0 or i == len(themes):
            print(f"  [{i}/{len(themes)}] ... (cặp theme-mã: {len(rows)})")
        time.sleep(PAUSE)
    with open(THEME_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["theme_kr", "ticker"])
        w.writerows(rows)
    print(f"[OK] {len(themes)} chủ đề · {len(rows)} cặp -> {THEME_OUT} (loại {skipped})")


def main():
    inds = list_industries()
    print(f"Số ngành: {len(inds)}")
    rows = {}  # ticker -> [name_kr, ticker, industry_kr, market]
    skipped = 0
    for i, g in enumerate(inds, 1):
        no, name = g["no"], g["name"]
        try:
            members = industry_members(no)
        except Exception as e:
            print(f"  [!] ngành {name} lỗi: {str(e)[:50]}")
            continue
        kept = 0
        for m in members:
            code = str(m.get("itemCode") or "").zfill(6)
            if not code or len(code) != 6:
                continue
            nm = m.get("stockName") or ""
            if _excluded(nm, code, m.get("stockEndType")):
                skipped += 1
                continue
            mkt = "KOSPI" if str(m.get("sosok")) == "0" else "KOSDAQ"
            rows[code] = [nm, code, name, mkt]
            kept += 1
        print(f"  [{i}/{len(inds)}] {name}: giữ {kept}/{len(members)} (tổng {len(rows)})")
        time.sleep(PAUSE)
    print(f"\nĐã loại (ETF/ETN/SPAC/ưu đãi): {skipped}")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name_kr", "ticker", "industry_kr", "market"])
        for code in sorted(rows):
            w.writerow(rows[code])
    print(f"\n[OK] Đã ghi {len(rows)} mã -> {OUT}")
    build_themes()


if __name__ == "__main__":
    main()
