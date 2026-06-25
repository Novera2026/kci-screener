"""
KCI Coverage — đánh giá ĐỘ PHỦ DỮ LIỆU của 1 mã (kết hợp Naver + DART) rồi
KHUYẾN NGHỊ phương pháp định giá + TRẦN độ tin cậy.

Đây là lời giải cho vấn đề "cổ phiếu mới lên sàn chưa có dữ liệu": thay vì để
DCF/RIM ra số rác trên 1-2 năm lịch sử, hệ thống tự nhận biết độ phủ mỏng và
chuyển sang multiples forward + peer, đồng thời CHẶN confidence ở mức Low/Medium.

Nguồn dữ liệu, theo độ ưu tiên cho DN mới niêm yết:
  1. DART năm (사업보고서)            — chuẩn nhất, nhưng DN mới chưa có
  2. DART quý/bán niên (분기/반기)     — có SỚM hơn báo cáo năm
  3. Naver annual (gồm số bản cáo bạch IPO + consensus dự phóng)
  4. Peer-relative (khi gần như không có gì)

Dùng:
    import coverage
    rep = coverage.assess_coverage("462870")
    print(rep["valuation_mode"], rep["confidence_cap"])
"""
from __future__ import annotations
from typing import Optional

try:
    import financials
except Exception:
    financials = None
try:
    import dart_api
except Exception:
    dart_api = None


def _real_years_naver(annual: list[dict]) -> int:
    return len([c for c in annual if not c.get("is_consensus")])


def assess_coverage(ticker: str, sector: Optional[str] = None) -> dict:
    """Trả báo cáo độ phủ + khuyến nghị.

    {
      ticker, real_years, source_mix, has_forward, has_quarterly,
      is_new_listing, valuation_mode, confidence_cap, recommended_methods,
      avoid_methods, note
    }
    valuation_mode: intrinsic_ok | intrinsic_weak | relative_forward | peer_only
    """
    nav_annual, nav_quarter = [], []
    has_forward = False
    if financials is not None:
        try:
            nav = financials.fetch_financials(ticker)
            nav_annual = nav.get("annual", []) or []
            nav_quarter = nav.get("quarter", []) or []
            has_forward = any(c.get("is_consensus") for c in nav_annual)
        except Exception:
            pass

    nav_years = _real_years_naver(nav_annual)
    dart = {"dart_annual_years": 0, "has_quarterly": False, "coverage": "n/a"}
    if dart_api is not None and dart_api.available():
        try:
            dart = dart_api.data_status(ticker)
        except Exception:
            pass
    dart_years = dart.get("dart_annual_years", 0) or 0
    has_quarterly = bool(dart.get("has_quarterly")) or len(
        [c for c in nav_quarter if not c.get("is_consensus")]) >= 2

    real_years = max(nav_years, dart_years)
    # DN mới niêm yết: DART có <3 năm BCTC năm chính thức (dù Naver có thêm số từ
    # bản cáo bạch IPO). Track record công khai ngắn + cơ cấu vốn đổi lúc IPO →
    # vẫn phải thận trọng dù tổng số năm ≥ 3.
    dart_new = bool(dart.get("is_new_listing")) and dart_years < 3
    source_mix = []
    if dart_years:
        source_mix.append(f"DART {dart_years}n")
    if nav_years:
        source_mix.append(f"Naver {nav_years}n")
    if has_quarterly:
        source_mix.append("quý/bán niên")

    # ---- Định tuyến ----
    if real_years >= 3 and not dart_new:
        mode, cap, new = "intrinsic_ok", None, False
        rec = ["DCF (FCFF) / RIM", "EV/EBITDA", "P/E forward"]
        avoid: list[str] = []
        note = f"{real_years} năm thực → đủ chạy intrinsic. Confidence không bị chặn."
    elif real_years >= 3 and dart_new:
        mode, cap, new = "intrinsic_ok", "Medium", True
        rec = ["DCF/RIM (tham khảo)", "Forward multiples + peer", "Reverse DCF"]
        avoid = []
        note = (f"Đủ {real_years} năm số liệu nhưng chỉ {dart_years} năm BCTC chính thức "
                "sau niêm yết (phần còn lại từ bản cáo bạch IPO). Track record công khai "
                "ngắn → chạy intrinsic được nhưng TRẦN Medium, ưu tiên cross-check peer.")
    elif real_years == 2:
        mode, cap, new = "intrinsic_weak", "Medium", True
        rec = ["P/E & EV/EBITDA forward (consensus)", "Peer comparison", "Reverse DCF"]
        avoid = ["DCF nhiều giai đoạn (lịch sử quá ngắn để ước driver)"]
        note = ("2 năm thực → DCF rất nhạy, chỉ dùng tham khảo. Ưu tiên multiples "
                "forward + peer. Trần Medium.")
    elif real_years == 1 or has_quarterly:
        mode, cap, new = "relative_forward", "Low", True
        rec = ["Forward multiples từ consensus", "Peer-relative (ngành)",
               "Annualize quý/bán niên (thận trọng)"]
        avoid = ["DCF", "RIM", "DDM"]
        note = ("Mới niêm yết, ~1 năm hoặc chỉ có quý → KHÔNG ép intrinsic. Dùng "
                "consensus forward + so sánh peer. Trần Low.")
    else:
        mode, cap, new = "peer_only", "Low", True
        rec = ["Peer-relative (ngành cùng economics)", "Số bản cáo bạch IPO (DART 문서)",
               "Range theo P/E-P/B ngành"]
        avoid = ["DCF", "RIM", "DDM", "mọi intrinsic"]
        note = ("Gần như không có dữ liệu lịch sử → chỉ định giá tương đối theo peer + "
                "số trong bản cáo bạch IPO. Nêu rõ là ước lượng sơ bộ. Trần Low.")

    return {
        "ticker": ticker, "real_years": real_years,
        "source_mix": " + ".join(source_mix) or "không có",
        "has_forward": has_forward, "has_quarterly": has_quarterly,
        "is_new_listing": new, "valuation_mode": mode, "confidence_cap": cap,
        "recommended_methods": rec, "avoid_methods": avoid, "note": note,
    }


def coverage_badge(rep: dict) -> str:
    """Chuỗi ngắn để hiển thị trên app."""
    icon = {"intrinsic_ok": "🟢", "intrinsic_weak": "🟡",
            "relative_forward": "🟠", "peer_only": "🔴"}.get(rep["valuation_mode"], "⚪")
    if rep.get("is_new_listing") and icon == "🟢":   # đủ năm nhưng mới niêm yết
        icon = "🟡"
    flag = " · ⚠️ mới niêm yết" if rep.get("is_new_listing") else ""
    cap = rep["confidence_cap"] or "không chặn"
    return (f"{icon} {rep['real_years']} năm dữ liệu ({rep['source_mix']}) · "
            f"trần confidence: {cap}{flag}")


if __name__ == "__main__":
    import sys, json
    tk = sys.argv[1] if len(sys.argv) > 1 else "005930"
    rep = assess_coverage(tk)
    print(coverage_badge(rep))
    print(json.dumps(rep, ensure_ascii=False, indent=2))
