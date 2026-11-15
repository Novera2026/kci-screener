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
import json
import time
import zipfile
import threading
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

def _norm(s) -> str:
    """Bỏ MỌI dấu cách + strip — DN nộp tên tài khoản khác nhau giữa các năm
    (vd '자산총계' vs '자산 총계'), khớp chính xác sẽ trượt nếu không chuẩn hóa."""
    return (s or "").replace(" ", "").strip()


# account_nm (Hàn) -> field. Có nhiều biến thể tên tài khoản giữa các DN/năm.
_ACCOUNTS = {
    "revenue":     {"매출액", "수익(매출액)", "영업수익", "매출"},
    "op_profit":   {"영업이익", "영업이익(손실)"},
    "net_profit":  {"당기순이익", "당기순이익(손실)", "당기순이익(당기순손실)"},
    "assets":      {"자산총계"},
    "liabilities": {"부채총계"},
    "equity":      {"자본총계"},
}
# bản chuẩn hóa (bỏ dấu cách) để so khớp bền vững
_ACCOUNTS_NORM = {f: {_norm(x) for x in names} for f, names in _ACCOUNTS.items()}


def api_key() -> Optional[str]:
    return os.environ.get("DART_API_KEY") or os.environ.get("OPENDART_API_KEY")


def available() -> bool:
    return bool(api_key())


# ---------------------------------------------------------------------------
# Gọi fnlttSinglAcntAll — MỘT cửa cho mọi module (rà soát 15/11/2026).
# Trước đây mỗi nơi tự gọi requests rồi `except: continue`: lỗi mạng bị coi như "không có báo cáo" → App lặng lẽ
# lùi sang báo cáo riêng lẻ (OFS) hoặc số Naver 3 năm và vẫn công bố (Hyundai Motor: doanh thu 787.668 thay vì
# 1.862.545; Samsung +19%). DART còn ĐÓNG KẾT NỐI khi bị gọi dồn dập (15/11: 53/200 mã mất hết số DART giữa một lượt
# quét 5 luồng). Nay:
#   • phân biệt "DART xác nhận không có báo cáo" (mã 013 → None) với "không lấy được" (lỗi mạng / hạn mức / khóa →
#     DartError sau khi thử lại) — chỉ trường hợp đầu mới được lùi CFS → OFS;
#   • giãn nhịp toàn tiến trình (≤ ~6 lần gọi/giây) và ngắt mạch ~90 giây sau 3 lần hỏng liên tiếp để không nã tiếp
#     vào máy chủ đang chặn.
# ---------------------------------------------------------------------------
class DartError(RuntimeError):
    """DART không trả được dữ liệu (KHÁC với 'không có báo cáo')."""


NO_DATA = "013"                # 조회된 데이타가 없습니다
MIN_GAP = 0.15                 # giây tối thiểu giữa hai lần gọi (toàn tiến trình)
RETRY_WAITS = (0.8, 2.4)       # chờ trước lần thử lại 1, 2
BREAKER_AFTER = 3              # số lần hỏng LIÊN TIẾP (đã gồm thử lại) thì ngắt mạch
BREAKER_SECS = 90
STATS = {"ok": 0, "nodata": 0, "errors": 0, "streak": 0, "open_until": 0.0, "last_error": ""}
_gate = threading.Lock()
_last_call = [0.0]


# ---------------------------------------------------------------------------
# Hạn mức gọi DART THEO NGÀY của tiến trình này (rà soát 15/11/2026, đợt 2). OpenDART cho mỗi khóa khoảng 20.000 lần
# gọi/ngày; app công khai trên Cloud dùng chung khóa với bot và máy của anh. Hết hạn mức → DartError, KHÔNG gọi tiếp
# (dữ liệu đã lưu vẫn dùng được). Đếm lưu ở .cache/dart_budget.json để khởi động lại App không xóa số đếm.
# Mặc định 10.000/ngày trên Cloud, không giới hạn trên máy của anh; đổi bằng KCI_DART_DAILY_BUDGET (0 = không giới hạn).
# ---------------------------------------------------------------------------
BUDGET_PATH = os.path.join(_HERE, ".cache", "dart_budget.json")
_BUDGET = {"day": None, "used": 0}


def daily_budget() -> int:
    """Hạn mức/ngày: KCI_DART_DAILY_BUDGET nếu đặt; không thì 10.000 trên bản công khai (Cloud) — chừa nửa hạn mức
    của khóa cho bot và máy của anh — và không giới hạn khi chạy trên máy của anh."""
    v = os.environ.get("KCI_DART_DAILY_BUDGET")
    if v is not None:
        try:
            return max(int(v), 0)
        except ValueError:
            pass
    try:
        import guard
        return 10000 if guard.is_cloud() else 0
    except Exception:
        return 10000


def _budget_save() -> None:
    try:
        os.makedirs(os.path.dirname(BUDGET_PATH), exist_ok=True)
        tmp = f"{BUDGET_PATH}.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_BUDGET, f)
        os.replace(tmp, BUDGET_PATH)
    except Exception:
        pass


def budget_take(n: int = 1) -> bool:
    """Trừ n lần gọi vào hạn mức hôm nay. False = đã hết (đừng gọi DART)."""
    cap = daily_budget()
    today = time.strftime("%Y%m%d")
    with _gate:
        if _BUDGET["day"] != today:
            used = 0
            try:
                with open(BUDGET_PATH, encoding="utf-8") as f:
                    saved = json.load(f)
                if saved.get("day") == today:
                    used = int(saved.get("used") or 0)
            except Exception:
                pass
            _BUDGET.update(day=today, used=used)
        if cap and _BUDGET["used"] + n > cap:
            return False
        _BUDGET["used"] += n
        if _BUDGET["used"] % 20 == 0 or (cap and _BUDGET["used"] >= cap):
            _budget_save()
        return True


def budget_used() -> int:
    return int(_BUDGET["used"] or 0) if _BUDGET["day"] == time.strftime("%Y%m%d") else 0


BUDGET_MSG = "đã dùng hết hạn mức gọi DART trong ngày của App — dùng số liệu đã lưu, thử lại ngày mai"


def _throttle() -> None:
    with _gate:                                  # giữ khóa khi chờ → các luồng xếp hàng, mỗi lần gọi cách nhau MIN_GAP
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


# Nghĩa các mã trạng thái của OpenDART (để cảnh báo trên app nói rõ NGUYÊN NHÂN, không chỉ "lỗi").
DART_STATUS = {
    "010": "khóa API chưa đăng ký — kiểm tra DART_API_KEY trong Secrets",
    "011": "khóa API đang bị tạm ngừng sử dụng",
    "012": "DART không cho địa chỉ IP của máy chủ này truy cập",
    "020": "đã vượt hạn mức gọi DART trong ngày của khóa (khoảng 20.000 lần)",
    "021": "vượt số công ty được tra trong một lần gọi",
    "100": "tham số gửi lên DART không hợp lệ",
    "101": "DART từ chối truy cập (truy cập không hợp lệ)",
    "800": "DART đang bảo trì hệ thống",
    "900": "DART gặp lỗi chưa xác định ở phía máy chủ của họ",
    "901": "khóa API đã hết hạn sử dụng (thời hạn lưu thông tin cá nhân)",
}


def describe_exception(e: BaseException) -> str:
    """Lỗi mạng khi gọi DART → câu tiếng Việt nói rõ loại lỗi (không chứa URL vì URL có khóa API)."""
    name = type(e).__name__
    if "Timeout" in name:
        return f"DART trả lời quá chậm (quá {_TIMEOUT} giây, {name})"
    if name in ("JSONDecodeError", "ValueError") or "JSON" in name:
        return "DART trả về trang không phải dữ liệu (thường là đang bảo trì hoặc chặn tạm thời)"
    if "SSL" in name:
        return f"lỗi bảo mật kết nối tới DART ({name})"
    if "Connection" in name or "Reset" in name or "Refused" in name:
        return f"DART đóng / từ chối kết nối ({name}) — thường do gọi dồn dập, thử lại sau ít phút"
    return f"lỗi kết nối DART ({name})"


def error_count() -> int:
    """Số lần gọi DART hỏng từ đầu tiến trình — nơi gọi so trước/sau để biết lượt lấy của mình có đầy đủ không."""
    return STATS["errors"]


def fetch_statements(corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS",
                     timeout: Optional[float] = None) -> Optional[list]:
    """Danh sách tài khoản của 1 báo cáo (fnlttSinglAcntAll).

    Trả list (có thể rỗng) khi DART trả dữ liệu; None khi DART XÁC NHẬN không có báo cáo đó (mã 013).
    Lỗi mạng, bị đóng kết nối, vượt hạn mức (020), khóa sai… → thử lại rồi ném DartError — không bao giờ coi lỗi là
    'không có'. Thông báo lỗi không chứa URL (URL có khóa API)."""
    if time.time() < STATS["open_until"]:
        with _gate:
            STATS["errors"] += 1
        wait = max(int(STATS["open_until"] - time.time()), 1)
        raise DartError(f"tạm ngừng gọi DART khoảng {wait} giây sau {BREAKER_AFTER} lần lỗi liên tiếp — nguyên nhân: "
                        f"{STATS['last_error'] or 'không rõ'}")
    last = "lỗi không rõ"
    for i in range(len(RETRY_WAITS) + 1):
        if not budget_take():
            with _gate:
                STATS["errors"] += 1
            raise DartError(BUDGET_MSG)
        _throttle()
        try:
            j = requests.get(f"{_BASE}/fnlttSinglAcntAll.json",
                             params={"crtfc_key": api_key(), "corp_code": corp_code, "bsns_year": str(year),
                                     "reprt_code": reprt_code, "fs_div": fs_div},
                             timeout=timeout or _TIMEOUT).json()
        except Exception as e:                    # không ghi str(e): chứa URL kèm khóa API
            last = describe_exception(e)
        else:
            if not isinstance(j, dict):
                j = {"status": "bad-body"}
            st = str(j.get("status"))
            if st == "000":
                with _gate:
                    STATS["ok"] += 1
                    STATS["streak"] = 0
                return j.get("list") or []
            if st == NO_DATA:
                with _gate:
                    STATS["nodata"] += 1
                    STATS["streak"] = 0
                return None
            last = ("DART trả dữ liệu không đúng dạng" if st == "bad-body" else
                    f"DART báo mã {st}: {DART_STATUS.get(st) or str(j.get('message') or 'không rõ')}")
            if st not in ("800", "900", "bad-body"):     # hạn mức / khóa: thử lại ngay cũng vô ích
                break
        if i < len(RETRY_WAITS):
            time.sleep(RETRY_WAITS[i])
    with _gate:
        STATS["errors"] += 1
        STATS["streak"] += 1
        STATS["last_error"] = last
        if STATS["streak"] >= BREAKER_AFTER:
            STATS["open_until"] = time.time() + BREAKER_SECS
    raise DartError(last)


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
    if not key or not budget_take():
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


# BCTC năm đã nộp gần như không đổi → nhớ trong tiến trình 12 giờ. Panel Chi tiết gọi
# lại mỗi lần rerun (kéo slider): trước đây mỗi lần ~6 request DART tuần tự × 3 lượt.
# Chỉ nhớ kết quả CÓ số (năm chưa nộp báo cáo vẫn được hỏi lại).
_YEAR_CACHE: dict = {}
_YEAR_TTL = 12 * 3600
# Báo cáo năm ĐÃ NỘP không đổi → nhớ thêm trên đĩa (như .cache/dart_full của analysis): lần chạy sau không gọi lại
# DART (mỗi mã ~7 lần gọi/lượt — nguyên nhân chính làm DART đóng kết nối). Đổi cách đọc tài khoản → tăng _YEAR_V.
YEAR_CACHE_DIR = os.path.join(_HERE, ".cache", "dart_year")
_YEAR_V = 4


def _fetch_year(corp_code: str, year: int, offline: bool = False) -> Optional[dict]:
    """Các tài khoản chính của 1 năm (사업보고서): bộ nhớ tiến trình → cache đĩa → DART. Chỉ nhớ kết quả CÓ số.
    Lỗi DART (khác 'chưa nộp') → DartError. offline=True (DART đang lỗi): chỉ đọc cache, kể cả bản đời cũ."""
    hit = _YEAR_CACHE.get((corp_code, year))
    if hit and time.time() - hit[0] < _YEAR_TTL:
        return dict(hit[1])
    fp = os.path.join(YEAR_CACHE_DIR, f"{corp_code}_{year}.json")
    rec = None
    if os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                rec = json.load(f)
            rec = rec if (isinstance(rec, dict) and rec.get("period") and (rec.get("_v") == _YEAR_V or offline)) else None
        except Exception:
            rec = None
    if rec is None and offline:
        return None
    if rec is None:
        rec = _fetch_year_raw(corp_code, year)
        if rec:
            try:
                os.makedirs(YEAR_CACHE_DIR, exist_ok=True)
                tmp = f"{fp}.{os.getpid()}.{threading.get_ident()}"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({**rec, "_v": _YEAR_V}, f, ensure_ascii=False)
                os.replace(tmp, fp)
            except Exception:
                pass
    if rec:
        rec.pop("_v", None)
        _YEAR_CACHE[(corp_code, year)] = (time.time(), dict(rec))
    return rec


# Mã tài khoản chuẩn của 6 chỉ tiêu chính + loại báo cáo chứa chúng. Đọc theo bậc: (1) mã chuẩn, đúng loại báo cáo
# → (2) tên, đúng loại báo cáo → (riêng LN thuần) phần cổ đông mẹ + phần thiểu số → (3) tên ở báo cáo khác, TRỪ báo
# cáo thay đổi vốn chủ (SCE). Chỉ đọc theo tên bỏ sót 125 giá trị trên 748 báo cáo năm của các mã lớn; còn đọc tên ở
# SCE thì lấy nhầm cột cổ đông thiểu số (펌텍코리아 2025: 56 thay vì 401) — rà soát 15/11/2026.
_STD_IDS = {"revenue": ("ifrs-full_Revenue",), "op_profit": ("dart_OperatingIncomeLoss",),
            "net_profit": ("ifrs-full_ProfitLoss",), "assets": ("ifrs-full_Assets",),
            "liabilities": ("ifrs-full_Liabilities",), "equity": ("ifrs-full_Equity",)}
_BS_FIELDS = {"assets", "liabilities", "equity"}


def main_accounts(items: list) -> dict:
    """6 chỉ tiêu chính của 1 báo cáo (억원): revenue, op_profit, net_profit, assets, liabilities, equity."""
    out = {}
    for field, names in _ACCOUNTS_NORM.items():
        sjs = {"BS"} if field in _BS_FIELDS else {"IS", "CIS"}
        def first(ok):
            return next((x for x in (_num(it.get("thstrm_amount")) for it in items if ok(it)) if x is not None), None)
        v = first(lambda it: it.get("sj_div") in sjs and it.get("account_id") in _STD_IDS[field])
        if v is None:
            v = first(lambda it: it.get("sj_div") in sjs and _norm(it.get("account_nm")) in names)
        if v is None and field == "net_profit":
            par = first(lambda it: it.get("sj_div") in sjs
                        and it.get("account_id") == "ifrs-full_ProfitLossAttributableToOwnersOfParent")
            nci = first(lambda it: it.get("sj_div") in sjs
                        and it.get("account_id") == "ifrs-full_ProfitLossAttributableToNoncontrollingInterests")
            v = (par + (nci or 0.0)) if par is not None else None
        if v is None:
            v = first(lambda it: it.get("sj_div") != "SCE" and _norm(it.get("account_nm")) in names)
        if v is not None:
            out[field] = v / 1e8                   # 원 -> 억원
    return out


def _parse_year(items: list, year: int, fs_div: str) -> Optional[dict]:
    """Danh sách tài khoản 1 báo cáo năm → {revenue, op_profit, net_profit, assets, liabilities, equity} (억원)."""
    rec = {"period": f"{year}12", "label": str(year), "is_consensus": False, "fs_div": fs_div}
    acc = main_accounts(items)
    if not acc:
        return None
    rec.update(acc)
    rec["currency"] = statement_currency(items)
    # nợ/vốn chủ thực từ bảng cân đối
    if rec.get("liabilities") is not None and rec.get("equity"):
        rec["debt_ratio"] = round(rec["liabilities"] / rec["equity"] * 100, 1)
    return rec


def statement_currency(items: list) -> str:
    """Đơn vị tiền của báo cáo (trường `currency` của DART). Doosan Bobcat, Kolon TissueGene… nộp bằng USD — số đó
    KHÔNG được đọc như KRW. Thiếu trường → 'KRW'."""
    seen: dict = {}
    for it in items:
        c = str(it.get("currency") or "").strip().upper()
        if c:
            seen[c] = seen.get(c, 0) + 1
    return max(seen, key=seen.get) if seen else "KRW"


def _fetch_year_raw(corp_code: str, year: int) -> Optional[dict]:
    """Các tài khoản chính của 1 năm (사업보고서). Ưu tiên 연결(CFS); chỉ lùi sang 별도(OFS) khi DART xác nhận không có
    bản hợp nhất hoặc bản hợp nhất không có tài khoản chính nào. Lỗi DART → DartError (không lùi OFS)."""
    for fs_div in ("CFS", "OFS"):
        items = fetch_statements(corp_code, year, "11011", fs_div)
        if not items:
            continue
        rec = _parse_year(items, year, fs_div)
        if rec:
            return rec
    return None


def annual_financials(ticker: str, years: int = 4,
                      end_year: Optional[int] = None, errors: Optional[list] = None) -> list[dict]:
    """4 năm THỰC gần nhất từ DART (mới→: trả theo thứ tự tăng dần).

    Mỗi phần tử: {period 'YYYY12', revenue, op_profit, net_profit, assets,
    liabilities, equity, debt_ratio, currency} đơn vị 억원 (theo `currency` — thường KRW). [] nếu không có key/không match.
    errors: truyền một list để NHẬN thông báo khi DART lỗi giữa chừng. Sau lỗi đầu tiên hàm KHÔNG gọi DART nữa mà đọc
    tiếp các năm đã lưu trên đĩa (tháng 2: năm vừa kết thúc chưa nộp + DART lỗi không được làm mất 5 năm đã có).
    """
    if not available():
        return []
    cc = corp_code_of(ticker)
    if not cc:
        return []
    if end_year is None:
        end_year = time.localtime().tm_year - 1     # năm tài chính gần nhất đã nộp
    out, y, miss, offline = [], end_year, 0, False
    # dò ngược: bỏ qua tối đa 2 năm trống (báo cáo năm nay có thể chưa nộp)
    while len(out) < years and miss < 3 and y > end_year - years - 3:
        try:
            rec = _fetch_year(cc, y, offline=offline)
        except DartError as e:
            if errors is not None:
                errors.append(str(e))
            offline = True
            rec = _fetch_year(cc, y, offline=True)
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


def valuation_extras(ticker: str, year: Optional[int] = None, errors: Optional[list] = None) -> dict:
    """Lấy số (억원) cho EV/EBIT + FCFF từ DART trong 1 lần gọi/năm gần nhất.

    Trả {year, ebit, cash, debt, net_debt, cfo, capex, finance_cost, tax_rate, currency, ...}
    hoặc {} nếu không có key / không đủ dữ liệu. Đơn vị: 억원; tax_rate dạng thập phân.
    errors: list nhận thông báo khi DART lỗi (khi đó {} KHÔNG có nghĩa là 'không có dữ liệu').
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
                items = fetch_statements(cc, y, "11011", fs_div)
            except DartError as e:
                if errors is not None:
                    errors.append(str(e))
                return {}
            if not items:
                continue
            out = {"year": y, "fs_div": fs_div, "debt": 0.0, "capex": 0.0, "currency": statement_currency(items)}
            for it in items:
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
    for fs_div in ("CFS", "OFS"):
        try:
            items = fetch_statements(corp_code, year, reprt_code, fs_div)
        except DartError:
            return None
        if not items:
            continue
        rec = {"period": f"{year}-{_REPRT.get(reprt_code, reprt_code)}",
               "year": year, "reprt_code": reprt_code,
               "is_consensus": False, "fs_div": fs_div}
        acc = main_accounts(items)
        if not acc:
            continue
        rec.update(acc)
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
    errs: list = []
    annual = annual_financials(ticker, years=6, errors=errs)
    n = len(annual)
    if errs:        # DART lỗi ≠ "chưa có báo cáo": không được kết luận 'mới niêm yết' từ một lần gọi hỏng
        return {"available": True, "corp_code": cc, "dart_annual_years": n, "has_quarterly": False,
                "coverage": "error", "is_new_listing": None, "suggested_mode": None, "confidence_cap": "Low",
                "note": f"DART chưa trả được dữ liệu ({errs[0]}) — độ phủ chưa đánh giá được, thử lại sau."}
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
