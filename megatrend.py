"""
KCI Mega trend — gom các CHỦ ĐỀ Naver (ngắn hạn, ~265 chủ đề, nhiễu) thành ~11 xu hướng lớn dài hạn.

Danh mục MEGATRENDS do người soạn (chọn tay số chủ đề Naver `no`), không tự suy. Muốn thêm/bớt chủ đề
chỉ cần sửa danh sách `themes`. Số chủ đề lấy từ https://m.stock.naver.com/api/stocks/theme (10/11/2026).

Hiệu suất xu hướng = trung bình đều % hôm nay của các chủ đề thành viên (mỗi chủ đề Naver đã là
trung bình các mã của nó); độ lan tỏa = Σ mã tăng / Σ mã trong các chủ đề.
Mọi số đều lấy realtime lúc gọi (không dùng lại kết quả cũ).
"""
from __future__ import annotations

import json
import os
from typing import Optional

import hotnews as H

MEGATRENDS: list[dict] = [
    {"key": "ai_memory", "name": "AI & bộ nhớ HBM", "icon": "🧠",
     "desc": "Hạ tầng AI: HBM, CXL/SOCAMM, đế bán dẫn, trung tâm dữ liệu, làm mát, quang học.",
     "themes": [536, 599, 547, 619, 545, 608, 557, 560, 586]},
    {"key": "semis", "name": "Chuỗi bán dẫn", "icon": "💾",
     "desc": "Sản xuất chip, thiết bị, vật liệu, bán dẫn hệ thống và bán dẫn công suất.",
     "themes": [155, 12, 14, 307, 533, 614]},
    {"key": "grid", "name": "Lưới điện & lưu trữ", "icon": "⚡",
     "desc": "Thiết bị điện, dây cáp, lưới điện thông minh, ESS — nhu cầu điện từ AI & điện khí hóa.",
     "themes": [123, 178, 229, 329]},
    {"key": "nuclear", "name": "Hạt nhân & năng lượng mới", "icon": "☢️",
     "desc": "Điện hạt nhân, nhiệt hạch, pin nhiên liệu SOFC, hydro.",
     "themes": [205, 187, 559, 386]},
    {"key": "ship", "name": "Đóng tàu & LNG", "icon": "🚢",
     "desc": "Đóng tàu, thiết bị tàu, chuỗi LNG.",
     "themes": [30, 94, 381]},
    {"key": "defense", "name": "Quốc phòng & vũ trụ", "icon": "🛡️",
     "desc": "Quốc phòng, hàng không vũ trụ, linh kiện máy bay, drone, SpaceX.",
     "themes": [144, 200, 379, 349, 584]},
    {"key": "robot", "name": "Robot & AI vật lý", "icon": "🤖",
     "desc": "Robot hình người / AI vật lý, robot công nghiệp, nhà máy thông minh.",
     "themes": [587, 505, 99, 392]},
    {"key": "battery", "name": "Pin & xe điện", "icon": "🔋",
     "desc": "Pin thứ cấp (sản xuất, vật liệu, thể rắn, LFP), xe điện.",
     "themes": [64, 449, 446, 472, 503, 227]},
    {"key": "bio", "name": "Bio & y tế", "icon": "🧬",
     "desc": "Thuốc béo phì, biosimilar, miễn dịch ung thư, AI y tế, botox.",
     "themes": [540, 241, 389, 543, 397]},
    {"key": "kculture", "name": "K-beauty & K-culture", "icon": "💄",
     "desc": "Mỹ phẩm, thiết bị làm đẹp, giải trí, âm nhạc.",
     "themes": [110, 513, 128, 98]},
    {"key": "valueup", "name": "Value-up & tài chính", "icon": "🏦",
     "desc": "Chỉ số/kế hoạch Value-up, chứng khoán, ngân hàng, holding.",
     "themes": [568, 571, 151, 165, 111]},
]


def trend_table(themes: list[dict]) -> list[dict]:
    """themes = kết quả hotnews.theme_ranking()[0]. → mỗi mega trend: % TB hôm nay, độ lan tỏa,
    chủ đề con mạnh nhất. Chủ đề không còn trên Naver thì bỏ qua (ghi số chủ đề thấy được)."""
    by_no = {t["no"]: t for t in themes}
    out = []
    for m in MEGATRENDS:
        members = [by_no[n] for n in m["themes"] if n in by_no]
        chgs = [t["chg"] for t in members if t.get("chg") is not None]
        rise = sum(t.get("rise") or 0 for t in members)
        total = sum(t.get("total") or 0 for t in members)
        best = max(members, key=lambda t: t["chg"] if t.get("chg") is not None else -999, default=None)
        out.append({**{k: m[k] for k in ("key", "name", "icon", "desc")},
                    "chg": sum(chgs) / len(chgs) if chgs else None,
                    "breadth": rise / total * 100 if total else None,
                    "rise": rise, "total": total,
                    "n_found": len(members), "n_themes": len(m["themes"]),
                    "best": best["name"] if best else None,
                    "best_chg": best.get("chg") if best else None})
    out.sort(key=lambda r: -(r["chg"] if r["chg"] is not None else -999))
    return out


def trend_leaders(key: str, details: dict, top: int = 10) -> list[dict]:
    """details = {no chủ đề: hotnews.theme_detail(no)}. → mã của mega trend, bỏ trùng, xếp % hôm nay ↓."""
    m = next(x for x in MEGATRENDS if x["key"] == key)
    seen: dict = {}
    for no in m["themes"]:
        for s in (details.get(no) or {}).get("stocks", []):
            seen.setdefault(s["ticker"], s)
    rows = sorted(seen.values(), key=lambda s: -(s["chg"] if s.get("chg") is not None else -999))
    return rows[:top]


def market_indices(codes=("KOSPI", "KOSDAQ")) -> list[dict]:
    """Chỉ số thị trường realtime (Naver). Lỗi mạng → bỏ qua chỉ số đó."""
    out = []
    for c in codes:
        j = H._get(f"https://m.stock.naver.com/api/index/{c}/basic")
        if not j:
            continue
        out.append({"code": c, "price": H._n(j.get("closePrice")),
                    "chg_pt": H._n(j.get("compareToPreviousClosePrice")),
                    "chg": H._n(j.get("fluctuationsRatio")),
                    "dir": (j.get("compareToPreviousPrice") or {}).get("name"),
                    "status": j.get("marketStatus"), "at": j.get("localTradedAt")})
    for r in out:   # Naver trả số tuyệt đối cho điểm thay đổi → gắn dấu theo chiều
        if r["dir"] in ("FALLING", "LOWER_LIMIT") and r["chg_pt"] and r["chg_pt"] > 0:
            r["chg_pt"] = -r["chg_pt"]
        if r["dir"] in ("FALLING", "LOWER_LIMIT") and r["chg"] and r["chg"] > 0:
            r["chg"] = -r["chg"]
    return out


def load_bot_state(path: str) -> Optional[dict]:
    """Kết quả lần chạy bot gần nhất (bot_hunt.state_of). Không có / hỏng → None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) and "picks" in s else None
    except (OSError, ValueError):
        return None


def trend_tickers(key: str, per_theme: int = 100) -> list[str]:
    """Toàn bộ mã của 1 mega trend (gộp thành viên các chủ đề Naver, bỏ trùng) — làm vũ trụ cho bot."""
    m = next(x for x in MEGATRENDS if x["key"] == key)
    det = H.parallel(lambda no: H.theme_detail(no, per_theme), m["themes"])
    return list(dict.fromkeys(s["ticker"] for no in m["themes"] for s in (det.get(no) or {}).get("stocks", [])))
