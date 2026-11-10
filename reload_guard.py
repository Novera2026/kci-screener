"""
Tự nạp lại module của dự án khi file nguồn đã đổi (sau mỗi lần deploy lên Streamlit Cloud).

Vấn đề (11/11/2026): Cloud kéo mã mới về và chạy lại file TRANG bằng mã mới, nhưng các module dùng chung
(screener, valuation, pipeline…) đã import trong tiến trình vẫn là BẢN CŨ trong sys.modules → trang mới gọi hàm
mới (vd screener.fetch_kgb10y_detail) → ImportError, cho tới khi reboot App.

Cách xử lý: mỗi lần chạy, so thời điểm sửa file của từng module dự án với lúc nó được nạp. Có file mới hơn → bỏ
TOÀN BỘ module dự án khỏi sys.modules (để chúng nạp lại cùng một phiên bản, không trộn cũ/mới); lần import kế tiếp
đọc mã mới.
"""
from __future__ import annotations

import os
import sys

_REG = "_kci_module_mtimes"       # lưu trong module sys → còn nguyên qua các lần bỏ/nạp lại module dự án


def _proc_start() -> float:
    """Thời điểm tiến trình khởi động (Linux: /proc/self). Không xác định được → 0 (bỏ qua luật này)."""
    try:
        return os.stat("/proc/self").st_ctime
    except OSError:
        return 0.0


def project_modules(root: str) -> dict:
    """{tên: module} của các module nằm trong `root` (bỏ thư viện ngoài, môi trường ảo)."""
    root = os.path.realpath(root) + os.sep
    out = {}
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        rf = os.path.realpath(f)
        if rf.startswith(root) and os.sep + ".venv" + os.sep not in rf and "site-packages" not in rf:
            out[name] = m
    return out


def drop_stale(root: str, proc_start: float = None) -> list:
    """Bỏ mọi module dự án nếu có module có thể đang là bản cũ. Trả danh sách module đã bỏ ([] nếu không).

    Một module bị coi là cũ khi:
      • đã ghi mốc trước đó và file nay mới hơn mốc (deploy trong lúc tiến trình đang chạy), hoặc
      • lần đầu thấy trong tiến trình nhưng file được sửa SAU khi tiến trình khởi động (module có thể đã nạp
        trước lúc deploy — đúng tình huống Cloud 11/11/2026).
    Sau khi bỏ, mốc = thời điểm sửa file hiện tại → lần nạp lại kế tiếp không bị bỏ lặp lại."""
    reg = sys.__dict__.setdefault(_REG, {})
    start = _proc_start() if proc_start is None else proc_start
    mods = project_modules(root)
    stale = False
    for name, m in mods.items():
        try:
            mt = os.path.getmtime(m.__file__)
        except OSError:
            continue
        if name in reg:
            stale = stale or mt > reg[name]
        else:
            stale = stale or (start > 0 and mt > start)
        reg[name] = mt
    if not stale:
        return []
    for name in mods:
        sys.modules.pop(name, None)
    return sorted(mods)
