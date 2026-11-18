"""
Lịch công bố BCTC Hàn Quốc dùng chung (rà soát 18/11/2026, cải tiến C2).

Trước đây mỗi nơi tự suy năm / kỳ (today.year − 1, range tới today.year + 1) nên sinh lỗi ❌ hàng loạt từ 1/1 và hỏi
DART kỳ chưa kết thúc. Một quy tắc cho mọi nơi:

  • period_end(y, rc) : ngày kết thúc kỳ báo cáo (năm tài chính tháng 12).
  • due_date(y, rc)   : hạn nộp — 사업보고서 31/3 năm sau, 1분기 15/5, 반기 14/8, 3분기 14/11.
  • state(y, rc, today): 'not_ended' (kỳ chưa hết) · 'pending' (đã hết, còn trong hạn nộp) · 'due' (đã quá hạn nộp).
  • expected_fy(today, fy_end_month): năm tài chính gần nhất đã quá hạn nộp báo cáo năm.

rc là reprt_code của DART: 11011 năm · 11013 Q1 · 11012 bán niên · 11014 Q3. `today` luôn truyền vào (mặc định
ngày Hàn Quốc guard.today_kst()), không tự lấy giờ máy chủ.
"""
from __future__ import annotations

import calendar
import datetime as _dt
from typing import Optional

ANNUAL, Q1, HALF, Q3 = "11011", "11013", "11012", "11014"

# rc → (tháng kết thúc kỳ, (tháng hạn, ngày hạn), hạn rơi vào năm sau?)
_CAL = {
    Q1: (3, (5, 15), False),
    HALF: (6, (8, 14), False),
    Q3: (9, (11, 14), False),
    ANNUAL: (12, (3, 31), True),
}


def _month_end(y: int, m: int) -> _dt.date:
    return _dt.date(y, m, calendar.monthrange(y, m)[1])


def period_end(y: int, rc: str = ANNUAL) -> _dt.date:
    """Ngày kết thúc kỳ `rc` của năm tài chính `y` (năm tài chính tháng 12)."""
    return _month_end(int(y), _CAL[str(rc)][0])


def due_date(y: int, rc: str = ANNUAL) -> _dt.date:
    """Hạn nộp báo cáo kỳ `rc` của năm `y`."""
    _, (m, d), nxt = _CAL[str(rc)]
    return _dt.date(int(y) + (1 if nxt else 0), m, d)


def state(y: int, rc: str, today: _dt.date) -> str:
    """'not_ended' khi kỳ chưa kết thúc (kể cả đúng ngày cuối kỳ), 'pending' khi đã kết thúc nhưng chưa quá hạn nộp
    (kể cả đúng ngày hạn), 'due' khi đã quá hạn nộp."""
    if today <= period_end(y, rc):
        return "not_ended"
    if today <= due_date(y, rc):
        return "pending"
    return "due"


def fy_due_date(fy: int, fy_end_month: int = 12) -> _dt.date:
    """Hạn nộp 사업보고서 của năm tài chính kết thúc tháng `fy_end_month` năm `fy`: cuối tháng thứ 3 sau đó."""
    m = int(fy_end_month) + 3
    y = int(fy) + (m - 1) // 12
    return _month_end(y, (m - 1) % 12 + 1)


def expected_fy(today: _dt.date, fy_end_month: Optional[int] = 12) -> int:
    """Năm tài chính gần nhất đã QUÁ hạn nộp báo cáo năm (năm tài chính đặt theo năm kết thúc kỳ).

    FY tháng 12: today ≥ 1/4 → today.year − 1, ngược lại today.year − 2."""
    m = int(fy_end_month or 12)
    if not 1 <= m <= 12:
        m = 12
    fy = today.year
    while today <= fy_due_date(fy, m):
        fy -= 1
    return fy
