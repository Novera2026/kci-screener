"""
KCI — bảo vệ app công khai (rà soát 15/11/2026, đợt 2).

App chạy công khai trên Streamlit Community Cloud, dùng khóa DART, Toss và tài khoản KRX của chủ app. Trước đây
người lạ có thể nhập danh sách mã bao nhiêu cũng được, chạy backtest trên hàng trăm mã × 10 năm (hết hạn mức DART
trong ngày), dùng nguồn giá Toss / KRX của chủ app, đọc và ghi nhật ký định giá chung.

  • is_cloud()        : đang chạy trên Streamlit Cloud (thư mục /mount/src/…) — chỉ ở đó mới khóa; chạy trên máy
                        của anh thì mọi thứ mở như cũ. Ghi đè bằng biến môi trường KCI_PUBLIC=1/0.
  • owner_ok() / owner_panel() / owner_gate(): tính năng dùng khóa riêng chỉ mở sau khi nhập mật khẩu chủ app
                        (Secret KCI_OWNER_PASSWORD). Chưa đặt mật khẩu → các tính năng đó KHÓA trên Cloud.
  • clip_queries()    : cắt danh sách mã quá dài.
  • heavy_slot()      : giới hạn số tác vụ nặng chạy cùng lúc trên máy chủ (săn cổ phiếu, sàng lọc, quét toàn sàn…).
  • redact()          : che khóa API / token trong thông báo lỗi trước khi hiện ra giao diện hoặc log.
  • md_escape(), safe_url(): chữ từ nguồn ngoài (tiêu đề tin) không được biến thành định dạng / liên kết lạ.
"""
from __future__ import annotations

import contextlib
import hmac
import os
import re
import threading
import time
from typing import Optional

OWNER_SECRET = "KCI_OWNER_PASSWORD"
MAX_LIST = 200                 # số mã tối đa trong một danh sách nhập tay
MAX_QUERY_CHARS = 60           # độ dài tối đa của MỘT tên / mã khi tra
MAX_INPUT_CHARS = 6000         # độ dài tối đa của ô nhập danh sách
HEAVY_SLOTS = 2                # số tác vụ nặng chạy cùng lúc trên một máy chủ
MAX_TRIES = 5                  # số lần nhập sai mật khẩu trong một phiên


def is_cloud() -> bool:
    v = os.environ.get("KCI_PUBLIC")
    if v in ("0", "1"):
        return v == "1"
    return os.path.abspath(__file__).startswith("/mount/src/")


def secret(name: str) -> Optional[str]:
    """Biến môi trường, rồi Streamlit Secrets. Không có → None."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets[name]) if name in st.secrets else None
    except Exception:
        return None


# ---------------------------------------------------------------------------- chủ app
def owner_ok() -> bool:
    """Tính năng dùng khóa riêng có được mở không: chạy trên máy của anh → luôn mở; trên Cloud → sau khi mở khóa."""
    if not is_cloud():
        return True
    try:
        import streamlit as st
        return bool(st.session_state.get("_owner_ok"))
    except Exception:
        return False


def check_password(entered: str, expected: Optional[str]) -> bool:
    """So mật khẩu (thời gian so sánh không lộ độ dài khớp). Chưa đặt mật khẩu → luôn sai."""
    if not expected or not entered:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def owner_panel() -> None:
    """Ô mở khóa trong thanh bên (chỉ hiện trên Cloud)."""
    if not is_cloud():
        return
    import streamlit as st
    with st.sidebar.expander("🔒 Chủ app" + (" — đã mở khóa" if owner_ok() else "")):
        if owner_ok():
            st.caption("Đã mở các tính năng dùng khóa riêng (backtest, nguồn Toss / KRX, nhật ký định giá) cho phiên này.")
            if st.button("Khóa lại", key="_owner_lock"):
                st.session_state["_owner_ok"] = False
                st.rerun()
            return
        expected = secret(OWNER_SECRET)
        if not expected:
            st.caption(f"Chưa đặt mật khẩu: thêm `{OWNER_SECRET}` vào Secrets của Streamlit Cloud để mở các tính năng "
                       "dùng khóa riêng.")
            return
        tries = st.session_state.get("_owner_tries", 0)
        if tries >= MAX_TRIES:
            st.error(f"Đã nhập sai {MAX_TRIES} lần trong phiên này.")
            return
        with st.form("_owner_form", clear_on_submit=True):
            pw = st.text_input("Mật khẩu", type="password", max_chars=200)
            sent = st.form_submit_button("Mở khóa")
        if sent:
            if check_password(pw, expected):
                st.session_state["_owner_ok"] = True
                st.session_state["_owner_tries"] = 0
                st.rerun()
            else:
                time.sleep(1.0)                     # làm chậm việc dò mật khẩu
                st.session_state["_owner_tries"] = tries + 1
                st.error("Sai mật khẩu.")


def owner_gate(what: str) -> bool:
    """True nếu được dùng tính năng; không thì hiện lời giải thích (ô mở khóa ở thanh bên)."""
    if owner_ok():
        return True
    import streamlit as st
    st.info(f"🔒 {what} dùng khóa / tài khoản riêng của chủ app nên chỉ mở cho chủ app (ô **🔒 Chủ app** ở thanh bên). "
            "Bản chạy trên máy của anh không bị khóa.")
    return False


# ---------------------------------------------------------------------------- đầu vào
def clip_queries(text: str, limit: int = MAX_LIST) -> tuple[list[str], int]:
    """Tách danh sách mã/tên (dấu phẩy, xuống dòng), bỏ trùng, bỏ mục quá dài; trả (tối đa `limit` mục, số mục bị
    bỏ do vượt giới hạn)."""
    items = [x.strip() for x in str(text or "")[:MAX_INPUT_CHARS].replace(",", "\n").splitlines()]
    items = list(dict.fromkeys(x for x in items if x and len(x) <= MAX_QUERY_CHARS))
    return items[:limit], max(len(items) - limit, 0)


_REUTERS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,19}$")


def clean_codes(codes, limit: int = 10) -> list[str]:
    """Mã nước ngoài (Reuters của Naver) — chỉ chữ, số, '.', '-' (không cho '/', '?', '..' vào đường dẫn API)."""
    out = []
    for c in codes:
        c = str(c).strip()
        if _REUTERS.match(c) and ".." not in c and c not in out:
            out.append(c)
    return out[:limit]


# ---------------------------------------------------------------------------- tác vụ nặng
_heavy = threading.BoundedSemaphore(HEAVY_SLOTS)


@contextlib.contextmanager
def heavy_slot():
    """`with heavy_slot() as ok:` — ok = False khi máy chủ đang chạy đủ HEAVY_SLOTS tác vụ nặng (không chờ)."""
    got = _heavy.acquire(blocking=False)
    try:
        yield got
    finally:
        if got:
            _heavy.release()


BUSY_MSG = ("⏳ Máy chủ đang chạy tác vụ nặng khác (săn cổ phiếu / sàng lọc / quét toàn sàn). Vui lòng thử lại sau "
            "ít phút.")


# ---------------------------------------------------------------------------- hiển thị an toàn
_SECRET_PATTERNS = [
    (re.compile(r"(crtfc_key=)[^&\s'\"]+", re.I), r"\1***"),
    (re.compile(r"(/bot)\d+:[A-Za-z0-9_\-]+"), r"\1***"),
    (re.compile(r"((?:client_secret|clientSecret|appsecret|access_token|api_key|apikey)[\"']?\s*[=:]\s*[\"']?)"
                r"[^&\s'\",}]+", re.I), r"\1***"),
]


def redact(text) -> str:
    """Che khóa API, token trong chuỗi (URL lỗi của requests chứa nguyên tham số crtfc_key=…)."""
    s = str(text)
    for pat, rep in _SECRET_PATTERNS:
        s = pat.sub(rep, s)
    for name in ("DART_API_KEY", "OPENDART_API_KEY", "TOSS_CLIENT_SECRET", "TOSS_CLIENT_ID", "TELEGRAM_BOT_TOKEN",
                 "KRX_PW", OWNER_SECRET):
        v = os.environ.get(name)
        if v and len(v) >= 6:
            s = s.replace(v, "***")
    return s


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def md_escape(text) -> str:
    """Chữ từ nguồn ngoài (tiêu đề tin) hiện nguyên văn trong st.markdown — không thành liên kết, ảnh, định dạng."""
    return _MD_SPECIAL.sub(r"\\\1", str(text or "")).replace("\n", " ")


def safe_url(url) -> Optional[str]:
    """Chỉ cho liên kết http(s) không có ký tự phá cú pháp markdown."""
    u = str(url or "").strip()
    if not re.match(r"^https?://[^\s()<>\[\]]+$", u):
        return None
    return u
