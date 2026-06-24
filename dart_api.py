"""
KCI DART — OpenDART (dart.fss.or.kr) client cho báo cáo tài chính GỐC.

Khác với Naver (chỉ 3 năm thực + 1 dự phóng, và bảng cân đối phải SUY RA từ 부채비율),
OpenDART cấp số liệu THỰC nộp lên cơ quan quản lý: 자산총계/부채총계/자본총계 (tổng tài
sản/nợ/vốn chủ) chính xác + 4 năm thực + doanh thu/LN gốc.

Cần API key MIỄN PHÍ tại https://opendart.fss.or.kr (회원가입 → 인증키 신청).
Đặt vào file .env:   DART_API_KEY=khoa_cua_ban
(KHÔNG dán key vào chat / không commit .env.)

Endpoint dùng:
  GET /api/corpCode.xml         -> ZIP chứa CORPCODE.xml (map stock_code -> corp_code)
  GET /api/fnlttSinglAcntAll.json (corp_code, bsns_year, reprt_code, fs_div)
       reprt_code: 11011=사업보고서(năm). fs_div: CFS(연결, ưu tiên) / OFS(별도).
       Một lần gọi trả thstrm(năm đó)+frmtrm(năm trước)+bfefrmtrm(2 năm trước).
Đơn vị tiền trả về = KRW (원); module quy đổi sang 억원 (÷1e8) cho đồng bộ Naver.
"""
from __future__ import annotations
import io
import os
import csv
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_BASE = "https://opendart.fss.or.kr/api"
_TIMEOUT = 15
_HERE = os.path.dirname(os.path.abspath(__file__))
_CORP_CACHE = os.path.join(_HERE, "dart_corp.csv")     # stock_code,corp_code,corp_name

# account_nm (Hàn) -> field. Có nhiều biến thể tên tài khoản giữa các DN.
_ACCOUNTS = {
    "revenue":     {"매출액", "수익(매출액)", "영업수익", "매출"},
    "op_profit":   {"영업이익", "영업이익(손실)"},
    "net_profit":  {"당기순이익", "당기순이익(손실)", "당기순이익(당기순손실)"},
    "assets":      {"자산총계"},
    "liabilities": {"부채총계"},
    "equity":      {"자본총계"},
}


def api_key() -> Optional[str]:
    return os.environ.get("DART_API_KEY") or os.environ.get("OPENDART_API_KEY")


def available() -> bool:
    return bool(api_key())


# ---------------------------------------------------------------------------
# corp_code map (stock ticker 6 số -> corp_code 8 số của DART)
# ---------------------------------------------------------------------------
_corp_map: Optional[dict] = None


def _load_corp_cache() -> Optional[dict]:
    if not os.path.exists(_CORP_CACHE):
        return None
    # cache quá 30 ngày thì coi như cũ (DN mới niêm yết)
    if time.time() - os.path.getmtime(_CORP_CACHE) > 30 * 86400:
        return None
    out = {}
    try:
        with open(_CORP_CACHE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("stock_code"):
                    out[row["stock_code"]] = row["corp_code"]
        return out or None
    except Exception:
        return None


def _download_corp_map() -> dict:
    """Tải corpCode.xml (ZIP) từ DART, lọc DN có stock_code, lưu cache csv."""
    key = api_key()
    if not key:
        return {}
    r = requests.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": key},
                     timeout=_TIMEOUT * 3)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_bytes = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml_bytes)
    out, rows = {}, []
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        cc = (el.findtext("corp_code") or "").strip()
        nm = (el.findtext("corp_name") or "").strip()
        if sc and cc:                       # chỉ giữ DN niêm yết
            out[sc] = cc
            rows.append((sc, cc, nm))
    try:
        with open(_CORP_CACHE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["stock_code", "corp_code", "corp_name"])
            w.writerows(rows)
    except Exception:
        pass
    return out


def corp_code_of(ticker: str) -> Optional[str]:
    global _corp_map
    if _corp_map is None:
        _corp_map = _load_corp_cache()
        if _corp_map is None:
            try:
                _corp_map = _download_corp_map()
            except Exception:
                _corp_map = {}
    return _corp_map.get(str(ticker).zfill(6))


# ---------------------------------------------------------------------------
# Báo cáo tài chính năm
# ---------------------------------------------------------------------------
def _num(x) -> Optional[float]:
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def _fetch_year(corp_code: str, year: int) -> Optional[dict]:
    """Lấy các tài khoản chính của 1 năm (사업보고서). Ưu tiên 연결(CFS), fallback 별도(OFS)."""
    key = api_key()
    for fs_div in ("CFS", "OFS"):
        try:
            r = requests.get(
                f"{_BASE}/fnlttSinglAcntAll.json",
                params={"crtfc_key": key, "corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": "11011",
                        "fs_div": fs_div},
                timeout=_TIMEOUT)
            j = r.json()
        except Exception:
            continue
        if j.get("status") != "000":
            continue
        rec = {"period": f"{year}12", "label": str(year),
               "is_consensus": False, "fs_div": fs_div}
        found = False
        for item in j.get("list", []):
            nm = (item.get("account_nm") or "").strip()
            for field, names in _ACCOUNTS.items():
                if nm in names and rec.get(field) is None:
                    v = _num(item.get("thstrm_amount"))
                    if v is not None:
                        rec[field] = v / 1e8           # 원 -> 억원
                        found = True
        if not found:
            continue
        # nợ/vốn chủ thực từ bảng cân đối
        if rec.get("liabilities") is not None and rec.get("equity"):
            rec["debt_ratio"] = round(rec["liabilities"] / rec["equity"] * 100, 1)
        return rec
    return None


def annual_financials(ticker: str, years: int = 4,
                      end_year: Optional[int] = None) -> list[dict]:
    """4 năm THỰC gần nhất từ DART (mới→: trả theo thứ tự tăng dần).

    Mỗi phần tử: {period 'YYYY12', revenue, op_profit, net_profit, assets,
    liabilities, equity, debt_ratio} đơn vị 억원. [] nếu không có key/không match.
    """
    if not available():
        return []
    cc = corp_code_of(ticker)
    if not cc:
        return []
    if end_year is None:
        end_year = time.localtime().tm_year - 1     # năm tài chính gần nhất đã nộp
    out, y, miss = [], end_year, 0
    # dò ngược: bỏ qua tối đa 2 năm trống (báo cáo năm nay có thể chưa nộp)
    while len(out) < years and miss < 3 and y > end_year - years - 3:
        rec = _fetch_year(cc, y)
        if rec:
            out.append(rec)
            miss = 0
        else:
            miss += 1
        y -= 1
    out.reverse()
    return out


if __name__ == "__main__":            # tự kiểm tra nhanh: python dart_api.py 005930
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "005930"
    if not available():
        print("⚠️  Chưa có DART_API_KEY trong .env"); sys.exit(1)
    print("corp_code:", corp_code_of(tk))
    for r in annual_financials(tk):
        print(r["label"], "| DT", r.get("revenue"), "| LN thuần", r.get("net_profit"),
              "| Tài sản", r.get("assets"), "| Nợ", r.get("liabilities"),
              "| Nợ/VCSH%", r.get("debt_ratio"))
