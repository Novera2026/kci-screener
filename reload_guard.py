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


_SCAN: dict = {}                  # {root: (frozenset tên module, [tên module dự án])} — quét lại khi tập module đổi


def _in_root(f: str, roots: tuple) -> bool:
    """Lọc bằng so chuỗi trước (rẻ); chỉ realpath đường dẫn tương đối hoặc đường dẫn đã qua lọc."""
    if os.sep + ".venv" + os.sep in f or "site-packages" in f:
        return False
    if os.path.isabs(f) and not f.startswith(roots):
        return False
    rf = os.path.realpath(f)
    return rf.startswith(roots[-1]) and os.sep + ".venv" + os.sep not in rf and "site-packages" not in rf


def project_modules(root: str) -> dict:
    """{tên: module} của các module nằm trong `root` (bỏ thư viện ngoài, môi trường ảo).

    Chạy ở MỖI lần chạy lại của mọi người: trước 18/11/2026 gọi realpath cho cả ~1.500 module (~34 ms CPU). Nay
    loại bằng so chuỗi trước và nhớ kết quả theo tập (tên, id module) — quét lại khi có module mới nạp / bị bỏ / bị thay."""
    roots = (os.path.abspath(root) + os.sep, os.path.realpath(root) + os.sep)
    mods = sys.modules
    # khóa gồm cả DANH TÍNH module: AppTest / Streamlit thay sys.modules['__main__'] bằng module khác cùng tên → tập
    # tên không đổi nhưng module đã đổi (rà soát cuối 18/11/2026: trả '__main__' nằm ngoài dự án)
    key = frozenset((n, id(m)) for n, m in list(mods.items()))
    hit = _SCAN.get(roots)
    if hit is None or hit[0] != key:
        names = [n for n, m in list(mods.items())
                 if getattr(m, "__file__", None) and _in_root(m.__file__, roots)]
        hit = _SCAN[roots] = (key, names)
    return {n: mods[n] for n in hit[1] if n in mods}


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
