"""SOTP (định giá tổng từng phần) — đọc bảng nhập tay của trang Định giá tổng hợp.

Tách khỏi views/tong_hop.py để kiểm thử được mà không chạy cả trang (18/11/2026):
  - ô 'Giá trị (억)' gõ '5,000' / '5000억' trước đây làm sập trang (float() trần) → nay đưa vào cảnh báo;
  - mã gõ bằng tên chỉ nhận khi KHỚP CHẮC (mã / đúng tên / cổ phiếu ưu đãi đã xác minh) —
    'SK On' từng bị đoán mờ sang 000660 (SK hynix, vốn hóa ~7,7 triệu 억);
  - có mã mà % sở hữu trống → cảnh báo, không lặng lẽ bỏ dòng.
"""
from __future__ import annotations

from typing import Callable, Optional

from valuation import _num

COL_NAME = "Mảng/Công ty con"
COL_CODE = "Mã niêm yết"
COL_STAKE = "% sở hữu"
COL_MANUAL = "Giá trị (억) — nếu chưa niêm yết"

SURE_HOW = ("ticker", "exact", "preferred")     # các kiểu khớp được coi là chắc chắn


def resolve_sure(registry, query: str) -> dict:
    """{ticker, how, query}: ticker=None nếu chỉ khớp kiểu đoán (contains/fuzzy) hoặc không khớp."""
    try:
        info = registry.resolve_info(query)
    except Exception:
        info = {"ticker": None, "how": None}
    how = info.get("how")
    tk = info.get("ticker") if how in SURE_HOW else None
    return {"query": str(query).strip(), "ticker": tk, "how": how}


def _blank(x) -> bool:
    """Ô trống: None, NaN hoặc chuỗi rỗng (chuỗi có chữ KHÔNG trống — để còn cảnh báo không đọc được)."""
    if x is None:
        return True
    if isinstance(x, str):
        return not x.strip()
    return isinstance(x, float) and x != x


def parse_sotp_rows(rows, mktcap_fn: Callable[[str], Optional[dict]]) -> tuple[list, list]:
    """rows: DataFrame (hoặc list dict) của bảng SOTP. mktcap_fn(mã) → {mc (억), ticker, name, how} hoặc None.
    Trả (parts, warns); parts: {Mảng, Mã, Đã khớp, % sở hữu, Giá trị (억)} — Giá trị None khi không đọc được."""
    recs = rows.to_dict("records") if hasattr(rows, "to_dict") else list(rows)
    parts, warns = [], []
    for prow in recs:
        nm = str(prow.get(COL_NAME) or "").strip()
        sub = str(prow.get(COL_CODE) or "").strip()
        stake_raw = prow.get(COL_STAKE)
        manual_raw = prow.get(COL_MANUAL)
        if nm.lower() in ("nan", "none"):
            nm = ""
        if sub.lower() in ("nan", "none"):
            sub = ""
        stake = _num(stake_raw)
        manual = _num(manual_raw)
        has_manual = not _blank(manual_raw)
        if not (nm or sub or has_manual):
            continue
        label = nm or sub
        matched, val = "", None
        if sub and stake is None and not has_manual:
            warns.append(f"'{label}': có mã '{sub}' nhưng % sở hữu trống → không tính dòng này")
        elif sub and stake is not None:
            if stake < 0 or stake > 100:
                warns.append(f"'{label}': % sở hữu {stake:g} ngoài 0–100 → không tính dòng này")
            else:
                info = mktcap_fn(sub) or {}
                if info.get("ticker"):
                    matched = f"{info['ticker']} {info.get('name') or ''}".strip()
                mc = _num(info.get("mc"))
                if not info.get("ticker"):
                    warns.append(f"không nhận diện chắc chắn mã '{sub}' — nhập mã 6 số hoặc đúng tên công ty")
                elif mc is None or mc <= 0:
                    warns.append(f"không lấy được vốn hóa của '{sub}'")
                else:
                    val = round(mc * stake / 100)
        elif has_manual:            # mảng chưa niêm yết (hoặc có mã nhưng để trống %) → Giá trị nhập tay
            if manual is None:
                warns.append(f"'{label}': không đọc được Giá trị (억) '{manual_raw}' — chỉ nhập số, không dấu phẩy/chữ")
            else:
                val = manual
        parts.append({"Mảng": label, "Mã": sub, "Đã khớp": matched,
                      "% sở hữu": stake, "Giá trị (억)": val})
    return parts, warns
