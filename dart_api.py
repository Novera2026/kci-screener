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
import re
import csv
import json
import time
import zipfile
import threading
from collections import deque
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
BUDGET_MSG = "đã dùng hết hạn mức gọi DART trong ngày của App — dùng số liệu đã lưu, thử lại ngày mai"
_BUDGET = {"day": None, "used": 0, "guest": 0}   # guest = phần khách đã dùng (để chặn GUEST_SHARE)


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


def _today_key() -> str:
    """Ngày hạn mức theo giờ Hàn Quốc (máy chủ Cloud chạy UTC — trước đây 0–9 giờ sáng KST còn tính vào ngày hôm qua)."""
    try:
        import guard
        return guard.today_kst().strftime("%Y%m%d")
    except Exception:
        return time.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Hạn mức THEO PHIÊN cho khách trên Cloud (rà soát 18/11/2026, cải tiến C7). Hạn mức ngày dùng chung cả App: một khách
# (bot săn, Sàng lọc, nút Làm mới, ô EV/EBIT) có thể làm cạn DART của mọi người. Nay trên Cloud, phiên chưa mở khóa
# chủ app có:
#   • tối đa SESSION_PER_HOUR lượt/giờ (thùng token theo phiên, nạp lại đều);
#   • khách nói chung chỉ được dùng tới GUEST_SHARE hạn mức ngày (phần còn lại để chủ app / bot);
# và toàn tiến trình tối đa WINDOW_MAX lượt trong WINDOW_SECS giây. Chủ app (và máy của anh) không bị hạn mức phiên.
# Luồng phụ (ThreadPool) không có phiên Streamlit → tạo pool bằng ThreadPoolExecutor(..., **dart_api.pool_kwargs())
# để luồng phụ mang phiên khách của luồng chính; pool không làm vậy chỉ chịu hạn mức ngày và trần 5 phút.
# ---------------------------------------------------------------------------
SESSION_PER_HOUR = 300
GUEST_SHARE = 0.5
WINDOW_SECS, WINDOW_MAX = 300, 600
CLOUD_MIN_GAP = 0.25           # trên Cloud giãn nhịp hơn (15/11 bị chặn IP ở 0,15 s)
_SESSIONS: dict = {}           # session_id → [token còn lại, thời điểm nạp]
_WINDOW: deque = deque()
_LOCAL = threading.local()     # phiên khách gắn cho luồng phụ (pool_kwargs)
SESSION_MSG = ("đã dùng hết hạn mức gọi DART của phiên này ({n} lượt/giờ cho khách) — dùng số liệu đã lưu, thử lại "
               "sau ít phút hoặc mở khóa chủ app")
GUEST_MSG = "khách đã dùng hết phần hạn mức DART trong ngày dành cho khách — dùng số liệu đã lưu, thử lại ngày mai"
WINDOW_MSG = "App đang gọi DART quá dồn dập ({n} lượt / {m} phút) — tạm dừng ít phút để DART không chặn"


def _is_cloud() -> bool:
    try:
        import guard
        return guard.is_cloud()
    except Exception:
        return False


def _guest_session() -> Optional[str]:
    """session_id của phiên KHÁCH trên Cloud (chưa mở khóa chủ app); None khi chạy trên máy anh, chủ app, hoặc luồng
    phụ không gắn phiên. Luồng phụ tạo bằng pool_kwargs() dùng phiên của luồng chính đã tạo pool."""
    if not _is_cloud():
        return None
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx(suppress_warning=True)
        sid = getattr(ctx, "session_id", None) if ctx else None
        if not sid:
            return getattr(_LOCAL, "sid", None)
        import guard
        return None if guard.owner_ok() else str(sid)
    except Exception:
        return getattr(_LOCAL, "sid", None)


def _bind_session(sid: Optional[str]) -> None:
    _LOCAL.sid = sid


def pool_kwargs() -> dict:
    """Tham số cho ThreadPoolExecutor(max_workers=..., **pool_kwargs()): luồng phụ gọi DART tính vào hạn mức phiên /
    phần của khách đang mở trang (chụp phiên lúc tạo pool, ở luồng chính)."""
    return {"initializer": _bind_session, "initargs": (_guest_session(),)}


def is_guest() -> bool:
    """Phiên này là khách trên Cloud (chịu hạn mức phiên)."""
    return _guest_session() is not None


def _session_tokens(sid: str, now: float) -> list:
    b = _SESSIONS.get(sid)
    if b is None:
        if len(_SESSIONS) > 2000:            # phiên cũ (> 1 giờ không gọi) đã nạp đầy lại → bỏ cho nhẹ bộ nhớ
            for k in [k for k, v in _SESSIONS.items() if now - v[1] > 3600]:
                del _SESSIONS[k]
        b = _SESSIONS[sid] = [float(SESSION_PER_HOUR), now]
    b[0] = min(float(SESSION_PER_HOUR), b[0] + (now - b[1]) * SESSION_PER_HOUR / 3600.0)
    b[1] = now
    return b


def _budget_sync(today: str) -> None:
    """Đổi ngày (KST) → đọc số đã dùng hôm nay từ dart_budget.json (khởi động lại App không xóa số đếm). Gọi trong _gate."""
    if _BUDGET["day"] == today:
        return
    used = guest = 0
    try:
        with open(BUDGET_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("day") == today:
            used = int(saved.get("used") or 0)
            guest = int(saved.get("guest") or 0)
    except Exception:
        pass
    _BUDGET.update(day=today, used=used, guest=guest)


WINDOW_WAIT_MAX = WINDOW_SECS + 5      # chờ tối đa (giây) khi trần 5 phút đầy, rồi mới từ chối


def _budget_check(n: int = 1, take: bool = True) -> Optional[str]:
    """None = được gọi (và đã trừ n lượt nếu take); không thì lý do (tiếng Việt) vì sao không được gọi DART.
    Trần 5 phút (Cloud) đầy thì CHỜ tới khi lượt cũ nhất ra khỏi cửa sổ (ngoài _gate) thay vì từ chối: giãn nhịp
    CLOUD_MIN_GAP cho tới 1.200 lượt/5 phút nên từ chối làm lượt quét dài gãy giữa chừng sau ~150 giây, các mã còn lại
    mang lỗi DART dù DART không lỗi (rà soát cuối 18/11/2026). budget_allows (take=False) không chờ và không bị trần này
    chặn (mỗi lượt gọi sau đó tự chờ)."""
    deadline = time.time() + WINDOW_WAIT_MAX
    while True:
        why, wait = _budget_check_once(n, take)
        if why != _WINDOW_FULL:
            return why
        if not take:          # ước lượng trước tác vụ nặng: cửa sổ đầy chỉ làm CHẬM (mỗi lượt sẽ chờ), không chặn
            return None
        now = time.time()
        if n > WINDOW_MAX or now + wait > deadline:
            return WINDOW_MSG.format(n=WINDOW_MAX, m=WINDOW_SECS // 60)
        time.sleep(max(wait, 0.05))


_WINDOW_FULL = object()


def _budget_check_once(n: int, take: bool):
    cap = daily_budget()
    today = _today_key()
    sid = _guest_session()
    cloud = _is_cloud()
    now = time.time()
    with _gate:
        _budget_sync(today)
        if cap and _BUDGET["used"] + n > cap:
            return BUDGET_MSG, 0.0
        if sid is not None:
            if cap and _BUDGET.get("guest", 0) + n > cap * GUEST_SHARE:
                return GUEST_MSG, 0.0
            if _session_tokens(sid, now)[0] < n:
                return SESSION_MSG.format(n=SESSION_PER_HOUR), 0.0
        if cloud:
            while _WINDOW and now - _WINDOW[0] > WINDOW_SECS:
                _WINDOW.popleft()
            if len(_WINDOW) + n > WINDOW_MAX:
                k = min(len(_WINDOW) + n - WINDOW_MAX, len(_WINDOW)) - 1      # lượt phải ra khỏi cửa sổ
                return _WINDOW_FULL, (_WINDOW[k] + WINDOW_SECS - now + 0.01) if k >= 0 else 0.0
        if not take:
            return None, 0.0
        _BUDGET["used"] += n
        if sid is not None:
            _SESSIONS[sid][0] -= n
            _BUDGET["guest"] = _BUDGET.get("guest", 0) + n
        if cloud:
            _WINDOW.extend([now] * n)
        if _BUDGET["used"] % 20 == 0 or (cap and _BUDGET["used"] >= cap):
            _budget_save()
        return None, 0.0


def budget_take(n: int = 1) -> bool:
    """Trừ n lần gọi vào hạn mức (ngày; phiên khách và trần 5 phút trên Cloud). False = không được gọi DART."""
    return _budget_check(n) is None


def budget_allows(n: int) -> tuple[bool, str]:
    """Trước một tác vụ nặng: còn đủ n lượt không (không trừ). Trả (được chạy, lý do nếu không)."""
    why = _budget_check(max(int(n), 0), take=False) if n > 0 else None
    return why is None, why or ""


def budget_left() -> Optional[int]:
    """Số lượt DART còn được gọi trong phiên này: min(hạn mức ngày còn lại, phần của khách, thùng token phiên).
    None = không giới hạn (máy của anh, KCI_DART_DAILY_BUDGET=0, chủ app không đặt hạn mức ngày)."""
    cap = daily_budget()
    sid = _guest_session()
    today = _today_key()
    left = []
    with _gate:
        _budget_sync(today)
        if cap:
            left.append(cap - int(_BUDGET["used"] or 0))
            if sid is not None:
                left.append(int(cap * GUEST_SHARE) - int(_BUDGET.get("guest") or 0))
        if sid is not None:
            left.append(int(_session_tokens(sid, time.time())[0]))
    return max(min(left), 0) if left else None


def budget_used() -> int:
    """Số lượt DART cả App đã dùng hôm nay (KST) — đọc cả dart_budget.json sau khi khởi động lại."""
    today = _today_key()
    with _gate:
        _budget_sync(today)
        return int(_BUDGET["used"] or 0)


def _throttle() -> None:
    gap = max(MIN_GAP, CLOUD_MIN_GAP) if _is_cloud() else MIN_GAP
    with _gate:                                  # giữ khóa khi chờ → các luồng xếp hàng, mỗi lần gọi cách nhau MIN_GAP
        wait = _last_call[0] + gap - time.time()
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


def get_json(path: str, timeout: Optional[float] = None, **params) -> dict:
    """GET một endpoint JSON của OpenDART qua MỘT cửa chung (rà soát 18/11/2026) — mọi module (BCTC, công bố của
    Tin nóng, backtest) dùng hàm này thay vì tự gọi requests.

    Hợp đồng:
      • path: tên endpoint sau /api/, vd 'list.json', 'fnlttSinglAcntAll.json'; crtfc_key tự thêm, không truyền vào.
      • Trả dict JSON khi DART trả status '000' (có dữ liệu) hoặc '013' (DART XÁC NHẬN không có) — nơi gọi đọc j['status'].
      • Ném DartError (thông báo tiếng Việt, KHÔNG chứa URL/khóa) khi: ngắt mạch đang mở (BREAKER_AFTER lần hỏng liên
        tiếp), hết hạn mức (ngày / phiên khách / trần 5 phút — budget_take), lỗi mạng hoặc body không phải object sau
        khi thử lại, hoặc status khác ('020' vượt hạn mức khóa, '010' khóa sai, '800' bảo trì…).
      • Mỗi lần gửi (kể cả thử lại) trừ 1 lượt hạn mức và qua giãn nhịp toàn tiến trình; cập nhật STATS (ok / nodata /
        errors / streak / open_until / last_error)."""
    if time.time() < STATS["open_until"]:
        with _gate:
            STATS["errors"] += 1
        wait = max(int(STATS["open_until"] - time.time()), 1)
        raise DartError(f"tạm ngừng gọi DART khoảng {wait} giây sau {BREAKER_AFTER} lần lỗi liên tiếp — nguyên nhân: "
                        f"{STATS['last_error'] or 'không rõ'}")
    url = f"{_BASE}/{str(path).lstrip('/')}"
    last = "lỗi không rõ"
    for i in range(len(RETRY_WAITS) + 1):
        why = _budget_check()
        if why:
            with _gate:
                STATS["errors"] += 1
            raise DartError(why)
        _throttle()
        try:
            j = requests.get(url, params={"crtfc_key": api_key(), **params}, timeout=timeout or _TIMEOUT).json()
        except Exception as e:                    # không ghi str(e): chứa URL kèm khóa API
            last = describe_exception(e)
        else:
            if not isinstance(j, dict):
                j = {"status": "bad-body"}
            st = str(j.get("status"))
            if st in ("000", NO_DATA):
                with _gate:
                    STATS["ok" if st == "000" else "nodata"] += 1
                    STATS["streak"] = 0
                return j
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


def fetch_statements(corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS",
                     timeout: Optional[float] = None) -> Optional[list]:
    """Danh sách tài khoản của 1 báo cáo (fnlttSinglAcntAll).

    Trả list (có thể rỗng) khi DART trả dữ liệu; None khi DART XÁC NHẬN không có báo cáo đó (mã 013).
    Lỗi mạng, bị đóng kết nối, vượt hạn mức (020), khóa sai… → thử lại rồi ném DartError — không bao giờ coi lỗi là
    'không có'. Thông báo lỗi không chứa URL (URL có khóa API)."""
    j = get_json("fnlttSinglAcntAll.json", timeout=timeout, corp_code=corp_code, bsns_year=str(year),
                 reprt_code=reprt_code, fs_div=fs_div)
    if str(j.get("status")) == NO_DATA:
        return None
    return j.get("list") or []


# ---------------------------------------------------------------------------
# corp_code map (stock ticker 6 số -> corp_code 8 số của DART)
# ---------------------------------------------------------------------------
# Danh mục tải về lưu ở .cache/dart_corp.csv + .cache/dart_corp.meta.json {'downloaded_at'} (rà soát 18/11/2026, I50).
# dart_corp.csv trong repo chỉ là bản DỰ PHÒNG, chỉ đọc: git không giữ mtime nên bản clone mới trên Cloud luôn trông
# "mới" và không bao giờ tải lại (18 mã niêm yết sau 20/10 không có số DART); trên máy anh, tải lại ghi đè thẳng file
# git đang theo dõi. Độ mới đọc từ meta; mã lạ (vừa niêm yết) → tải lại danh mục tối đa 1 lần/ngày/tiến trình.
CORP_DL_PATH = os.path.join(_HERE, ".cache", "dart_corp.csv")
CORP_META_PATH = os.path.join(_HERE, ".cache", "dart_corp.meta.json")
CORP_MAX_AGE = 30 * 86400
_corp_map: Optional[dict] = None
_corp_lock = threading.Lock()
_corp_retry = {"day": None}            # ngày (KST) đã thử tải lại danh mục trong tiến trình này
_TICKER_RE = re.compile(r"^[0-9][0-9A-Z]{5}$")


def _read_corp_csv(path: Optional[str] = None) -> Optional[dict]:
    """Đọc danh mục CSV (kể cả CŨ) — không xét tuổi. Mặc định: bản đã tải về, không có thì bản trong repo."""
    if path is None:
        return _read_corp_csv(CORP_DL_PATH) or _read_corp_csv(_CORP_CACHE)
    if not os.path.exists(path):
        return None
    out = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("stock_code"):
                    out[row["stock_code"]] = row["corp_code"]
        return out or None
    except Exception:
        return None


def _corp_meta() -> dict:
    try:
        with open(CORP_META_PATH, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _cache_fresh() -> bool:
    """Danh mục đã tải về còn mới (≤ 30 ngày theo meta). Thiếu meta / thiếu bản tải về → coi là cũ."""
    try:
        import datetime as _dt
        at = _dt.datetime.fromisoformat(str(_corp_meta().get("downloaded_at")))
        age = time.time() - at.timestamp()
    except Exception:
        return False
    return os.path.exists(CORP_DL_PATH) and 0 <= age <= CORP_MAX_AGE


def corp_map_date() -> str:
    """Ngày của danh mục đang dùng (dd/mm/yyyy) — để ghi chú khi một mã không có corp_code."""
    at = str(_corp_meta().get("downloaded_at") or "")
    if len(at) >= 10 and os.path.exists(CORP_DL_PATH):
        return f"{at[8:10]}/{at[5:7]}/{at[:4]}"
    return "bản lưu trong repo"


def _download_corp_map() -> dict:
    """Tải corpCode.xml (ZIP) từ DART, lọc DN có stock_code, lưu .cache/dart_corp.csv (ghi file tạm rồi thay) + meta.
    Không ghi đè dart_corp.csv của repo."""
    key = api_key()
    if not key or time.time() < STATS["open_until"]:      # ngắt mạch đang mở (DART chặn) → không gọi thêm
        return {}
    if not budget_take():
        return {}
    _throttle()
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
    if not out:
        return {}
    try:
        import datetime as _dt
        os.makedirs(os.path.dirname(CORP_DL_PATH), exist_ok=True)
        tmp = f"{CORP_DL_PATH}.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["stock_code", "corp_code", "corp_name"])
            w.writerows(rows)
        os.replace(tmp, CORP_DL_PATH)
        tmp = f"{CORP_META_PATH}.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"downloaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "n": len(out)}, f)
        os.replace(tmp, CORP_META_PATH)
    except Exception:
        pass
    return out


def _try_download() -> dict:
    try:
        return _download_corp_map() or {}
    except Exception as e:                    # ghi vào STATS như get_json (không ghi str(e): URL có khóa)
        with _gate:
            STATS["errors"] += 1
            STATS["streak"] += 1
            STATS["last_error"] = "tải danh mục corpCode: " + describe_exception(e)
            if STATS["streak"] >= BREAKER_AFTER:
                STATS["open_until"] = time.time() + BREAKER_SECS
        return {}


_corp_busy = {"on": False}             # một luồng đang tải danh mục (tải NGOÀI _corp_lock)


def corp_code_of(ticker: str) -> Optional[str]:
    """corp_code DART của mã. Tải danh mục (tới 45 giây) diễn ra NGOÀI _corp_lock, chỉ một luồng tải; luồng khác dùng
    bản đang có (hoặc bản cũ / bản repo) thay vì đứng chờ (rà soát cuối 18/11/2026). Mã lạ chỉ kích hoạt tải lại khi
    là cổ phiếu thường (đuôi 0) — cổ phiếu ưu đãi (005935) không có corp_code riêng."""
    global _corp_map
    tk = str(ticker).strip().upper().zfill(6)
    today = _today_key()
    mode = None
    with _corp_lock:
        if _corp_map is None and _cache_fresh():     # bản tải về còn mới → dùng luôn
            _corp_map = _read_corp_csv(CORP_DL_PATH) or None
        m = _corp_map
        if m is None:
            if not _corp_busy["on"]:                 # cũ/thiếu → thử tải lại (1 lần trong ngày)
                _corp_busy["on"], _corp_retry["day"], mode = True, today, "init"
        elif (m.get(tk) is None and _TICKER_RE.match(tk) and tk.endswith("0")
              and _corp_retry["day"] != today and not _corp_busy["on"]):
            _corp_busy["on"], _corp_retry["day"], mode = True, today, "refresh"   # mã lạ (vừa niêm yết?)
    if mode is None:
        if m is None:                                # luồng khác đang tải → bản cũ / bản repo, không chờ
            return (_read_corp_csv() or {}).get(tk)
        return m.get(tk)
    fresh = {}
    try:
        fresh = _try_download()
    finally:
        with _corp_lock:
            if mode == "init":
                _corp_map = fresh or _read_corp_csv() or {}       # tải lỗi → DÙNG bản cũ / bản repo
            elif fresh:
                _corp_map = fresh
            _corp_busy["on"] = False
            m = _corp_map
    return m.get(tk)


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
_YEAR_CACHE: dict = {}
_YEAR_TTL = 12 * 3600
# Báo cáo năm ĐÃ NỘP không đổi → nhớ thêm trên đĩa (như .cache/dart_full của analysis): lần chạy sau không gọi lại
# DART (mỗi mã ~7 lần gọi/lượt — nguyên nhân chính làm DART đóng kết nối). Đổi cách đọc tài khoản → tăng _YEAR_V.
YEAR_CACHE_DIR = os.path.join(_HERE, ".cache", "dart_year")
_YEAR_V = 4
# "DART xác nhận KHÔNG có báo cáo" (013) cũng được nhớ (rà soát 18/11/2026, I32) — trước đây mã dưới 6 năm XBRL (ngân
# hàng, bảo hiểm, mã niêm yết sau 2020) tốn 6 lượt DART vô ích mỗi lần chạy lại. File {corp}_{năm}.none.json; tin
# NONE_OLD_TTL với năm ≤ năm nay − 2 (không còn nộp muộn), NONE_NEW_TTL với năm gần hơn. Kỳ CHƯA KẾT THÚC thì không
# hỏi DART. Lỗi DART (DartError) không bao giờ được nhớ.
NONE_NEW_TTL = 6 * 3600
NONE_OLD_TTL = 30 * 86400


def _today():
    """Ngày Hàn Quốc (guard.today_kst) — test thay guard._now_utc để giả ngày."""
    import guard
    return guard.today_kst()


def none_ttl(year: int, today=None) -> int:
    """Giây được tin kết quả 'DART không có báo cáo' của năm tài chính `year`."""
    today = today or _today()
    return NONE_OLD_TTL if int(year) <= today.year - 2 else NONE_NEW_TTL


def period_open(year: int, reprt_code: str = "11011", today=None) -> bool:
    """Kỳ báo cáo chưa kết thúc (reporting.state = 'not_ended') → chắc chắn chưa có báo cáo, không hỏi DART."""
    import reporting
    try:
        return reporting.state(int(year), str(reprt_code), today or _today()) == "not_ended"
    except Exception:
        return False


def _read_none(fp: str, year: int, v) -> bool:
    """File đánh dấu 'không có báo cáo' còn hiệu lực (đúng phiên bản, chưa quá TTL)."""
    try:
        with open(fp, encoding="utf-8") as f:
            m = json.load(f)
        return (isinstance(m, dict) and m.get("_v") == v
                and 0 <= time.time() - float(m.get("at") or 0) < none_ttl(year))
    except Exception:
        return False


def _write_json_atomic(fp: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        tmp = f"{fp}.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, fp)
    except Exception:
        pass


def _fetch_year(corp_code: str, year: int, offline: bool = False) -> Optional[dict]:
    """Các tài khoản chính của 1 năm (사업보고서): bộ nhớ tiến trình → cache đĩa → DART. Nhớ cả kết quả 'DART không có
    báo cáo' (013, có hạn — xem none_ttl); kỳ chưa kết thúc → None không gọi DART.
    Lỗi DART (khác 'chưa nộp') → DartError, không nhớ. offline=True (DART đang lỗi): chỉ đọc cache, kể cả bản đời cũ."""
    hit = _YEAR_CACHE.get((corp_code, year))
    if hit and time.time() - hit[0] < (_YEAR_TTL if hit[1] else none_ttl(year)):
        return dict(hit[1]) if hit[1] else None
    fp = os.path.join(YEAR_CACHE_DIR, f"{corp_code}_{year}.json")
    fp_none = os.path.join(YEAR_CACHE_DIR, f"{corp_code}_{year}.none.json")
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
        if period_open(year, "11011") or _read_none(fp_none, year, _YEAR_V):
            return None
        rec = _fetch_year_raw(corp_code, year)            # DartError lọt ra ngoài → không ghi gì
        if rec:
            _write_json_atomic(fp, {**rec, "_v": _YEAR_V})
        else:                                             # DART xác nhận không có / không có tài khoản chính
            _write_json_atomic(fp_none, {"at": time.time(), "_v": _YEAR_V})
            _YEAR_CACHE[(corp_code, year)] = (time.time(), None)
            return None
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
                      end_year: Optional[int] = None, errors: Optional[list] = None,
                      err_years: Optional[list] = None) -> list[dict]:
    """4 năm THỰC gần nhất từ DART (mới→: trả theo thứ tự tăng dần).

    Mỗi phần tử: {period 'YYYY12', revenue, op_profit, net_profit, assets,
    liabilities, equity, debt_ratio, currency} đơn vị 억원 (theo `currency` — thường KRW). [] nếu không có key/không match.
    errors: truyền một list để NHẬN thông báo khi DART lỗi giữa chừng. Sau lỗi đầu tiên hàm KHÔNG gọi DART nữa mà đọc
    tiếp các năm đã lưu trên đĩa (tháng 2: năm vừa kết thúc chưa nộp + DART lỗi không được làm mất 5 năm đã có).
    err_years: list nhận NĂM gặp lỗi DART mà không có bản lưu (để biết chuỗi bị cụt ở năm đã nộp hay chỉ thiếu năm có
    thể chưa nộp).
    """
    if not available():
        return []
    cc = corp_code_of(ticker)
    if not cc:
        return []
    if end_year is None:
        import guard                                  # theo lịch Hàn Quốc (Cloud chạy UTC)
        end_year = guard.today_kst().year - 1         # năm tài chính gần nhất đã nộp
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
            if err_years is not None and not rec:       # năm lỗi mà bản lưu cũng không có → năm đó bị thiếu
                err_years.append(y)
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
                                      "영업활동으로 인한 순현금흐름", "영업활동으로부터의 현금흐름"}),
    ("_capex",       {"CF"},         {"유형자산의취득", "무형자산의취득"}),
]
_FIELDS_NORM = [(f, sj, {_norm(x) for x in names}) for f, sj, names in _FIELDS]
_FIELD_IDS = {"cfo": {"ifrs-full_CashFlowsFromUsedInOperatingActivities"}}   # tên dòng lạ vẫn khớp được theo mã chuẩn
# Cache đĩa theo (corp, năm) — rà soát 18/11/2026, I32: trước đây không có, mã không đủ số (105560…) tải 4 báo cáo
# mỗi lần rồi trả {}. Có số: lâu dài (báo cáo đã nộp). {} (DART không có báo cáo / có báo cáo nhưng thiếu EBIT, CFO):
# nhớ có hạn (none_ttl). Lỗi DART: không nhớ.
EXTRAS_CACHE_DIR = os.path.join(_HERE, ".cache", "dart_extras")
_EXTRAS_V = 1


def _extras_years(year: Optional[int]) -> list:
    if year:
        return [year]
    t = _today()
    return [y for y in (t.year - 1, t.year - 2) if not period_open(y, "11011", t)]


def _extras_cached(cc: str, y: int):
    """('rec', dict) | ('none', None) | ('miss', None) — đọc cache đĩa của valuation_extras."""
    fp = os.path.join(EXTRAS_CACHE_DIR, f"{cc}_{y}.json")
    try:
        with open(fp, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        return "miss", None
    if not isinstance(m, dict) or m.get("_v") != _EXTRAS_V:
        return "miss", None
    if m.get("rec"):
        return "rec", dict(m["rec"])
    if 0 <= time.time() - float(m.get("at") or 0) < none_ttl(y):
        return "none", None
    return "miss", None


def extras_cached(ticker: str, year: Optional[int] = None) -> bool:
    """valuation_extras(ticker) trả được từ cache đĩa, không cần gọi DART (để ước lượng lượt gọi trước tác vụ nặng)."""
    if not available():
        return True
    cc = corp_code_of(ticker)
    if not cc:
        return True
    for y in _extras_years(year):
        kind, _ = _extras_cached(cc, y)
        if kind == "rec":
            return True
        if kind == "miss":
            return False
    return True


def _parse_extras(items: list, y: int, fs_div: str) -> dict:
    out = {"year": y, "fs_div": fs_div, "debt": 0.0, "capex": 0.0, "currency": statement_currency(items)}
    for it in items:
        sj = it.get("sj_div")
        nm = _norm(it.get("account_nm"))
        v = _num(it.get("thstrm_amount"))
        if v is None:
            continue
        v8 = v / 1e8
        for field, sj_ok, nset in _FIELDS_NORM:
            if sj in sj_ok and (nm in nset or it.get("account_id") in _FIELD_IDS.get(field, ())):
                if field == "_debt":
                    out["debt"] += v8
                elif field == "_capex":
                    out["capex"] += abs(v8)
                elif out.get(field) is None:
                    out[field] = v8
    if out.get("ebit") is None or out.get("cfo") is None:   # thiếu cốt lõi
        return {}
    out["net_debt"] = round((out.get("debt") or 0) - (out.get("cash") or 0))
    out["debt"] = round(out["debt"])
    out["capex"] = round(out["capex"])
    pre, tax = out.get("pretax"), out.get("tax")
    tr = (tax / pre) if (pre and pre > 0 and tax is not None) else 0.22
    out["tax_rate"] = round(min(max(tr, 0.0), 0.40), 3)
    return out


def valuation_extras(ticker: str, year: Optional[int] = None, errors: Optional[list] = None) -> dict:
    """Lấy số (억원) cho EV/EBIT + FCFF từ DART trong 1 lần gọi/năm gần nhất.

    Trả {year, ebit, cash, debt, net_debt, cfo, capex, finance_cost, tax_rate, currency, ...}
    hoặc {} nếu không có key / không đủ dữ liệu. Đơn vị: 억원; tax_rate dạng thập phân.
    errors: list nhận thông báo khi DART lỗi (khi đó {} KHÔNG có nghĩa là 'không có dữ liệu').
    Kết quả nhớ trên đĩa theo (corp, năm) — xem EXTRAS_CACHE_DIR.
    """
    if not available():
        return {}
    cc = corp_code_of(ticker)
    if not cc:
        return {}
    for y in _extras_years(year):
        kind, rec = _extras_cached(cc, y)
        if kind == "rec":
            return rec
        if kind == "none":
            continue
        out = {}
        for fs_div in ("CFS", "OFS"):
            try:
                items = fetch_statements(cc, y, "11011", fs_div)
            except DartError as e:
                if errors is not None:
                    errors.append(str(e))
                return {}
            if not items:
                continue
            out = _parse_extras(items, y, fs_div)
            if out:
                break
        _write_json_atomic(os.path.join(EXTRAS_CACHE_DIR, f"{cc}_{y}.json"),
                           {"_v": _EXTRAS_V, "at": time.time(), "rec": out})
        if out:
            return dict(out)
    return {}


# ---------------------------------------------------------------------------
# Báo cáo quý/bán niên + đánh giá độ phủ dữ liệu (cho DN MỚI NIÊM YẾT)
# ---------------------------------------------------------------------------
_REPRT = {"11013": "Q1", "11012": "반기", "11014": "Q3", "11011": "년"}
# Cache đĩa (rà soát 18/11/2026, I32): có số → lâu dài; 'không có' (013) → none_ttl. Kỳ chưa kết thúc → không hỏi.
REPORT_CACHE_DIR = os.path.join(_HERE, ".cache", "dart_report")
_REPORT_V = 1


def _fetch_report(corp_code: str, year: int, reprt_code: str, errors: Optional[list] = None) -> Optional[dict]:
    """Như _fetch_year nhưng cho reprt_code bất kỳ (quý/bán niên/năm).
    11011=년(사업보고서) · 11012=반기 · 11013=1분기 · 11014=3분기.
    None khi kỳ chưa kết thúc, DART không có báo cáo, hoặc DART lỗi — lỗi thì thêm thông báo vào `errors` (nếu truyền)
    và KHÔNG nhớ (chỉ 013 mới được nhớ)."""
    if period_open(year, reprt_code):
        return None
    fp = os.path.join(REPORT_CACHE_DIR, f"{corp_code}_{year}_{reprt_code}.json")
    try:
        with open(fp, encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict) and m.get("_v") == _REPORT_V:
            if m.get("rec"):
                return dict(m["rec"])
            if 0 <= time.time() - float(m.get("at") or 0) < none_ttl(year):
                return None
    except Exception:
        pass
    found = None
    for fs_div in ("CFS", "OFS"):
        try:
            items = fetch_statements(corp_code, year, reprt_code, fs_div)
        except DartError as e:
            if errors is not None:
                errors.append(str(e))
            return None
        if not items:
            continue
        acc = main_accounts(items)
        if not acc:
            continue
        found = {"period": f"{year}-{_REPRT.get(reprt_code, reprt_code)}",
                 "year": year, "reprt_code": reprt_code,
                 "is_consensus": False, "fs_div": fs_div, **acc}
        if found.get("liabilities") is not None and found.get("equity"):
            found["debt_ratio"] = round(found["liabilities"] / found["equity"] * 100, 1)
        break
    _write_json_atomic(fp, {"_v": _REPORT_V, "at": time.time(), "rec": found})
    return dict(found) if found else None


def quarterly_financials(ticker: str, max_reports: int = 4, errors: Optional[list] = None) -> list[dict]:
    """Các báo cáo quý/bán niên gần nhất (thứ tự thời gian tăng dần).

    HỮU ÍCH cho DN MỚI NIÊM YẾT: chưa có 사업보고서 năm nhưng đã nộp 분기/반기 →
    vẫn lấy được số thực gần nhất thay vì rỗng. Bỏ kỳ chưa kết thúc. DART lỗi → dừng ngay ở lỗi đầu tiên (không nã
    tiếp vào máy chủ đang chặn), thông báo thêm vào `errors`.
    """
    if not available():
        return []
    cc = corp_code_of(ticker)
    if not cc:
        return []
    today = _today()
    seq = [(y, rc) for y in (today.year, today.year - 1)
           for rc in ("11014", "11012", "11013", "11011")      # Q3 → 반기 → Q1 → năm
           if not period_open(y, rc, today)]
    out, miss, errs = [], 0, []
    for y, rc in seq:
        if len(out) >= max_reports or miss >= 6:
            break
        rec = _fetch_report(cc, y, rc, errors=errs)
        if errs:
            break
        if rec:
            out.append(rec); miss = 0
        else:
            miss += 1
    if errors is not None:
        errors.extend(errs)
    out.reverse()
    return out


def data_status(ticker: str) -> dict:
    """Đánh giá độ phủ dữ liệu DART → định tuyến phương pháp + trần confidence.

    Trả: {available, corp_code, dart_annual_years, has_quarterly, coverage,
          is_new_listing, suggested_mode, confidence_cap, note}.
    coverage: full | thin | quarterly_only | none | no_corp_code | error.
    """
    if not available():
        return {"available": False, "note": "Chưa có DART_API_KEY trong .env"}
    cc = corp_code_of(ticker)
    if not cc:
        return {"available": True, "corp_code": None, "dart_annual_years": 0,
                "has_quarterly": False, "coverage": "no_corp_code",
                "is_new_listing": None, "suggested_mode": "peer_only",
                "confidence_cap": "Low",
                "note": f"Không có corp_code (danh mục DART ngày {corp_map_date()}) — chưa map hoặc chưa hiện diện "
                        "trên DART."}
    errs: list = []
    annual = annual_financials(ticker, years=6, errors=errs)
    n = len(annual)
    if errs:        # DART lỗi ≠ "chưa có báo cáo": không được kết luận 'mới niêm yết' từ một lần gọi hỏng
        return {"available": True, "corp_code": cc, "dart_annual_years": n, "has_quarterly": False,
                "coverage": "error", "is_new_listing": None, "suggested_mode": None, "confidence_cap": "Low",
                "note": f"DART chưa trả được dữ liệu ({errs[0]}) — độ phủ chưa đánh giá được, thử lại sau."}
    q = quarterly_financials(ticker, errors=errs) if n < 2 else []
    if errs:        # lỗi ở báo cáo quý cũng ≠ "không có": không xếp 'none'/'mới niêm yết' (I51)
        return {"available": True, "corp_code": cc, "dart_annual_years": n, "has_quarterly": bool(q),
                "coverage": "error", "is_new_listing": None, "suggested_mode": None, "confidence_cap": "Low",
                "note": f"DART lỗi khi lấy báo cáo quý ({errs[0]}) — độ phủ chưa đánh giá được, thử lại sau."}
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
