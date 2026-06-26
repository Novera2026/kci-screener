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


def _read_corp_csv() -> Optional[dict]:
    """Đọc cache CSV (kể cả CŨ) — không xét tuổi."""
    if not os.path.exists(_CORP_CACHE):
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


def _cache_fresh() -> bool:
    return (os.path.exists(_CORP_CACHE)
            and time.time() - os.path.getmtime(_CORP_CACHE) <= 30 * 86400)


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
        if _cache_fresh():                       # cache mới → dùng luôn
            _corp_map = _read_corp_csv() or {}
        else:                                    # cũ/thiếu → thử tải lại
            try:
                _corp_map = _download_corp_map() or {}
            except Exception:
                _corp_map = {}
            if not _corp_map:                    # tải fail → DÙNG cache cũ nếu có
                _corp_map = _read_corp_csv() or {}
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


# ---------------------------------------------------------------------------
# Số liệu cho EV/EBIT + FCFF (1 lần gọi/mã, năm gần nhất)
# ---------------------------------------------------------------------------
def _norm(s) -> str:
    return (s or "").replace(" ", "").strip()


# (field, các sj_div hợp lệ, tập tên tài khoản). Báo cáo KQKD nằm ở IS (2 báo cáo)
# HOẶC CIS (1 báo cáo gộp — holdco như HD한국조선해양). _debt/_capex = cộng dồn.
_FIELDS = [
    ("cash",         {"BS"},         {"현금및현금성자산"}),
    ("_debt",        {"BS"},         {"단기차입금", "유동성장기부채", "사채", "장기차입금"}),
    ("ebit",         {"IS", "CIS"},  {"영업이익", "영업이익(손실)"}),
    ("finance_cost", {"IS", "CIS"},  {"금융비용"}),
    ("tax",          {"IS", "CIS"},  {"법인세비용", "법인세비용(수익)"}),
    ("pretax",       {"IS", "CIS"},  {"법인세비용차감전순이익",
                                      "법인세비용차감전순이익(손실)", "법인세차감전순이익"}),
    ("cfo",          {"CF"},         {"영업활동현금흐름", "영업활동으로 인한 현금흐름",
                                      "영업활동으로 인한 순현금흐름"}),
    ("_capex",       {"CF"},         {"유형자산의취득", "무형자산의취득"}),
]
_FIELDS_NORM = [(f, sj, {_norm(x) for x in names}) for f, sj, names in _FIELDS]


def valuation_extras(ticker: str, year: Optional[int] = None) -> dict:
    """Lấy số (억원) cho EV/EBIT + FCFF từ DART trong 1 lần gọi/năm gần nhất.

    Trả {year, ebit, cash, debt, net_debt, cfo, capex, finance_cost, tax_rate, ...}
    hoặc {} nếu không có key / không đủ dữ liệu. Đơn vị: 억원; tax_rate dạng thập phân.
    """
    if not available():
        return {}
    cc = corp_code_of(ticker)
    if not cc:
        return {}
    yrs = [year] if year else [time.localtime().tm_year - 1, time.localtime().tm_year - 2]
    for y in yrs:
        for fs_div in ("CFS", "OFS"):
            try:
                j = requests.get(
                    f"{_BASE}/fnlttSinglAcntAll.json",
                    params={"crtfc_key": api_key(), "corp_code": cc, "bsns_year": str(y),
                            "reprt_code": "11011", "fs_div": fs_div},
                    timeout=_TIMEOUT).json()
            except Exception:
                continue
            if j.get("status") != "000":
                continue
            out = {"year": y, "fs_div": fs_div, "debt": 0.0, "capex": 0.0}
            for it in j.get("list", []):
                sj = it.get("sj_div")
                nm = _norm(it.get("account_nm"))
                v = _num(it.get("thstrm_amount"))
                if v is None:
                    continue
                v8 = v / 1e8
                for field, sj_ok, nset in _FIELDS_NORM:
                    if sj in sj_ok and nm in nset:
                        if field == "_debt":
                            out["debt"] += v8
                        elif field == "_capex":
                            out["capex"] += abs(v8)
                        elif out.get(field) is None:
                            out[field] = v8
            if out.get("ebit") is None or out.get("cfo") is None:   # thiếu cốt lõi
                continue
            out["net_debt"] = round((out.get("debt") or 0) - (out.get("cash") or 0))
            out["debt"] = round(out["debt"])
            out["capex"] = round(out["capex"])
            pre, tax = out.get("pretax"), out.get("tax")
            tr = (tax / pre) if (pre and pre > 0 and tax is not None) else 0.22
            out["tax_rate"] = round(min(max(tr, 0.0), 0.40), 3)
            return out
    return {}


# ---------------------------------------------------------------------------
# Báo cáo quý/bán niên + đánh giá độ phủ dữ liệu (cho DN MỚI NIÊM YẾT)
# ---------------------------------------------------------------------------
_REPRT = {"11013": "Q1", "11012": "반기", "11014": "Q3", "11011": "년"}


def _fetch_report(corp_code: str, year: int, reprt_code: str) -> Optional[dict]:
    """Như _fetch_year nhưng cho reprt_code bất kỳ (quý/bán niên/năm).
    11011=년(사업보고서) · 11012=반기 · 11013=1분기 · 11014=3분기."""
    key = api_key()
    for fs_div in ("CFS", "OFS"):
        try:
            r = requests.get(
                f"{_BASE}/fnlttSinglAcntAll.json",
                params={"crtfc_key": key, "corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": reprt_code,
                        "fs_div": fs_div}, timeout=_TIMEOUT)
            j = r.json()
        except Exception:
            continue
        if j.get("status") != "000":
            continue
        rec = {"period": f"{year}-{_REPRT.get(reprt_code, reprt_code)}",
               "year": year, "reprt_code": reprt_code,
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
        if rec.get("liabilities") is not None and rec.get("equity"):
            rec["debt_ratio"] = round(rec["liabilities"] / rec["equity"] * 100, 1)
        return rec
    return None


def quarterly_financials(ticker: str, max_reports: int = 4) -> list[dict]:
    """Các báo cáo quý/bán niên gần nhất (thứ tự thời gian tăng dần).

    HỮU ÍCH cho DN MỚI NIÊM YẾT: chưa có 사업보고서 năm nhưng đã nộp 분기/반기 →
    vẫn lấy được số thực gần nhất thay vì rỗng.
    """
    if not available():
        return []
    cc = corp_code_of(ticker)
    if not cc:
        return []
    this_year = time.localtime().tm_year
    seq = [(y, rc) for y in (this_year, this_year - 1)
           for rc in ("11014", "11012", "11013", "11011")]  # Q3 → 반기 → Q1 → năm
    out, miss = [], 0
    for y, rc in seq:
        if len(out) >= max_reports or miss >= 6:
            break
        rec = _fetch_report(cc, y, rc)
        if rec:
            out.append(rec); miss = 0
        else:
            miss += 1
    out.reverse()
    return out


def data_status(ticker: str) -> dict:
    """Đánh giá độ phủ dữ liệu DART → định tuyến phương pháp + trần confidence.

    Trả: {available, corp_code, dart_annual_years, has_quarterly, coverage,
          is_new_listing, suggested_mode, confidence_cap, note}.
    coverage: full | thin | quarterly_only | none | no_corp_code.
    """
    if not available():
        return {"available": False, "note": "Chưa có DART_API_KEY trong .env"}
    cc = corp_code_of(ticker)
    if not cc:
        return {"available": True, "corp_code": None, "dart_annual_years": 0,
                "has_quarterly": False, "coverage": "no_corp_code",
                "is_new_listing": None, "suggested_mode": "peer_only",
                "confidence_cap": "Low",
                "note": "Không có corp_code — chưa map hoặc chưa hiện diện trên DART."}
    annual = annual_financials(ticker, years=6)
    n = len(annual)
    q = quarterly_financials(ticker) if n < 2 else []
    if n >= 3:
        cov, new, mode, cap = "full", False, "intrinsic_ok", None
        note = f"{n} năm BCTC năm → đủ chạy DCF/RIM intrinsic."
    elif n in (1, 2):
        cov, new, mode, cap = "thin", True, "relative_forward", "Medium"
        note = f"Chỉ {n} năm BCTC năm → ưu tiên multiples forward + peer; trần Medium."
    elif q:
        cov, new, mode, cap = "quarterly_only", True, "relative_forward", "Low"
        note = (f"Chưa có BCTC năm; có {len(q)} báo cáo quý/bán niên → annualize thận "
                "trọng + consensus + peer; KHÔNG ép DCF; trần Low.")
    else:
        cov, new, mode, cap = "none", True, "peer_only", "Low"
        note = ("DART chưa có số kết cấu → dùng bản cáo bạch IPO (증권신고서/투자설명서) + "
                "consensus Naver + peer-relative; KHÔNG chạy DCF/RIM; trần Low.")
    return {"available": True, "corp_code": cc, "dart_annual_years": n,
            "has_quarterly": bool(q), "coverage": cov, "is_new_listing": new,
            "suggested_mode": mode, "confidence_cap": cap, "note": note}


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
