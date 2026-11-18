"""
KCI Pipeline — quy trình định giá 1 mã từ đầu đến cuối (Giai đoạn 1: bước 1–6 + báo cáo tóm tắt).

  1. Nhận diện doanh nghiệp      → loại doanh nghiệp, mô hình chính (valuation.recommend_method)
  2. Dữ liệu & chất lượng         → data_quality(): kiểm bằng SỐ, trả trần độ tin cậy
  3. Phân tích & chuẩn hóa        → ratio_table(): tăng trưởng/biên/ROE từng năm, đánh dấu năm bất thường
  4. Dự phóng nhiều năm           → default_drivers() + run_forecast(): 3–5 năm, người dùng chỉnh được;
                                    forecast_gates(): cổng cứng bằng số cho giả định dự phóng
  5. Chi phí vốn                  → cost_of_capital(): Rf, β (Blume), ERP, Ke, Kd, WACC
  6. Định giá đa mô hình          → rim_explicit()/ddm_explicit() trên dự phóng + valuation.value_stock (lõi KCI)
     Kết luận                     → reconcile(): mỗi ứng viên phải qua cổng [0,4; 2,5]× giá và [0,25; 4]× phép
                                    neo peer; trung vị các mô hình chính qua cổng = giá trị hợp lý.

Nguyên tắc (giống valuation.py): số học deterministic, thiếu dữ liệu → None + hạ độ tin cậy, không bịa.
Một con số phi lý bị chặn bằng số — không có trường giải trình nào mở được cổng.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import math
import os
from typing import Optional

import guard as _G
import valuation as V
from reporting import expected_fy, fy_due_date
from financials import (PARENT_RATIO_BAND, _has_parent_profit, _trend_value, _ytd_run_rate, conversion_ratio,
                        one_off_core, parent_ratio, project_forecast, share_basis, short_period_months)

# ---------------------------------------------------------------------------- hằng số cổng
FC_GROWTH_BAND = (-0.30, 0.40)    # tăng trưởng doanh thu 1 năm ngoài dải → ❌
FC_MARGIN_HEADROOM = 0.10         # biên LN ròng dự phóng > max lịch sử + 10 điểm % → ❌
FC_MARGIN_MAX = 0.50              # biên LN ròng > 50% (phi tài chính) → ❌
FC_ROE_MAX = 0.60                 # ROE dự phóng > 60% → ❌
TV_SHARE_WARN = 0.75              # giá trị cuối kỳ chiếm > 75% giá trị nội tại → ⚠️
AGREE_BAND = 0.20                 # các mô hình chính lệch nhau ≤ ±20% quanh trung vị → mới được High
HIGH_ANCHOR_BAND = (2 / 3, 1.5)   # …và giá trị hợp lý trong [0,67; 1,5]× phép neo peer (nếu có) → mới được High
_FIN_BUCKETS = {"Bank", "Brokerage", "Insurance"}

OK, WARN, FAIL = "✅", "⚠️", "❌"


def _num(x) -> Optional[float]:
    return V._num(x)


def _check(name: str, value, status: str, note: str = "") -> dict:
    return {"Kiểm tra": name, "Giá trị": value, "Kết quả": status, "Ghi chú": note}


# ============================================================================ Bước 1 — nhận diện
def identify(row: dict) -> dict:
    """Loại doanh nghiệp + mô hình chính/đối chiếu + có phải ngành chu kỳ / tài chính."""
    sector, ik = row.get("sector"), row.get("industry_kr")
    nm = next((x for x in (row.get("name_kr"), row.get("name")) if isinstance(x, str) and x.strip()), None)
    sec_eff = V.effective_sector(sector if isinstance(sector, str) else None,
                                 ik if isinstance(ik, str) else None, nm)     # holding phi tài chính → Holdco (I57)
    guide = V.recommend_method(sector if isinstance(sector, str) else None,
                               ik if isinstance(ik, str) else None, nm)
    return {
        "bucket": sec_eff or "Doanh nghiệp vận hành thông thường",
        "industry_kr": ik, "sector": sector,
        "primary": guide["primary"], "cross": guide["cross"], "auto": guide["auto"],
        "data_ok": guide.get("data_ok", False), "note": guide["note"],
        "cyclical": sec_eff in V.CYCLICAL_SECTORS,
        "financial": sec_eff in _FIN_BUCKETS,
        "dividend_model": guide["auto"] == "DDM",
    }


# ============================================================================ Bước 2 — chất lượng dữ liệu
def _reals(series: list[dict]) -> list[dict]:
    return [c for c in (series or []) if not c.get("is_consensus")]


def data_quality(row: dict, series: list[dict], quarters: Optional[list[dict]] = None,
                 beta: Optional[float] = None, today: Optional[_dt.date] = None,
                 feeds: Optional[dict] = None, sources: Optional[dict] = None) -> dict:
    """Kiểm dữ liệu bằng số. Trả {checks, cap, n_fail, n_warn, share_break, parent_break, source_break, period_break,
    source_line}.
    sources: tóm tắt nguồn của lần chạy (prepare → _source_info) cho ô 'Nguồn và độ mới dữ liệu' (C8).

    cap: trần độ tin cậy — có ❌ → 'Low'; ≥2 ⚠️ → 'Medium'; còn lại None (không chặn).
    feeds: tình trạng các nguồn số liệu thị trường của lần chạy, vd {"rf": {"ok": False, "note": "..."}} — nguồn lỗi
    là ❌ (kết quả vẫn tính trên giá trị dự phòng nhưng không được quá Low); nguồn có "share_break" (BCTC Naver thiếu)
    còn chặn mô hình theo mỗi cổ phiếu. Lỗi DART / lệch DART–Naver đọc từ `_src` của chuỗi năm (financials._merge_dart)."""
    today = today or _G.today_kst()      # ngày Hàn Quốc (máy chủ Cloud chạy UTC)
    reals = _reals(series)
    checks: list[dict] = []
    price, mc = _num(row.get("price")), _num(row.get("market_cap"))
    eps, bps = _num(row.get("eps")), _num(row.get("bps"))

    # kỳ ngắn (I08): năm chỉ ước từ một phần kỳ (× 12 ÷ số tháng có số) không phải năm BCTC thực
    n_scaled = sum(1 for c in reals if c.get("_period_months") and not c.get("_ttm"))
    n = len(reals) - n_scaled
    checks.append(_check("Số năm BCTC thực", n,
                         OK if n >= 5 else WARN if n >= 3 else FAIL,
                         ("≥5 năm mới trọn một chu kỳ" if n < 5 else "")
                         + (f"{'; ' if n < 5 else ''}không tính {n_scaled} năm ước từ một phần kỳ" if n_scaled else "")))
    # Kỳ kế toán ngắn (REIT 6/3 tháng — I08): năm hóa được kỳ gần nhất (đủ 12 tháng) → ⚠️; không → ❌ và chặn mô hình
    # dựa trên dự phóng (LN/EPS gốc chỉ là 1/2–1/4 năm). Chuỗi có kỳ ngắn mà chưa năm hóa cũng là ❌.
    period_break = False
    pm = (reals[-1].get("_period_months") if reals else None) or short_period_months(reals)
    if pm:
        last0 = reals[-1]
        ok_ttm = bool(last0.get("_period_months") and last0.get("_ttm") and last0.get("_ttm_eps"))
        period_break = not ok_ttm
        n_est = sum(1 for c in reals if c.get("_period_months") and not c.get("_ttm"))
        checks.append(_check("Độ dài kỳ kế toán", f"kỳ {pm} tháng → 12 tháng gần nhất (TTM)" if ok_ttm
                             else f"kỳ {pm} tháng — không dựng được 12 tháng gần nhất",
                             WARN if ok_ttm else FAIL,
                             (f"BCTC báo theo kỳ {pm} tháng: LN, EPS, cổ tức, ROE đã cộng thành 12 tháng"
                              + (f"; {n_est} năm cũ chỉ có một phần kỳ → ước × 12 ÷ số tháng có số" if n_est else ""))
                             if ok_ttm else
                             f"BCTC báo theo kỳ {pm} tháng và không đủ kỳ/quý để cộng thành 12 tháng gần nhất — LN, EPS "
                             "chỉ là một phần năm → chặn mô hình dựa trên dự phóng"))
    dart_n = sum(1 for c in reals if c.get("_dart"))
    src = (reals[-1].get("_src") if reals else None) or {}
    src_state = src.get("dart")
    source_break = src_state == "mismatch"
    if src_state == "error":
        checks.append(_check("Số năm có số gốc DART", dart_n, FAIL,
                             f"DART không trả đủ dữ liệu ({src.get('note')}) — chuỗi năm KHÔNG ĐẦY ĐỦ, kết quả sẽ đổi khi "
                             "DART trả lời lại. Thử lại sau ít phút"))
    elif src_state == "mismatch":
        checks.append(_check("Đối chiếu DART ↔ Naver", src.get("note"), FAIL,
                             "Hai nguồn không khớp ở năm gần nhất (đơn vị tiền, định nghĩa doanh thu hoặc đọc sai tài "
                             "khoản) → chỉ dùng số Naver và chặn mô hình dựa trên dự phóng"))
    elif src_state == "currency":
        checks.append(_check("Số năm có số gốc DART", 0, WARN, src.get("note")))
    elif src_state == "cached":
        checks.append(_check("Số năm có số gốc DART", dart_n, WARN, src.get("note")))
    else:
        checks.append(_check("Số năm có số gốc DART", dart_n, OK if dart_n >= 3 else WARN,
                             "" if dart_n >= 3 else (src.get("note") or "Thiếu DART → dùng số Naver (đã làm tròn, 3–4 năm)")))
    restated = [g for g in (src.get("gaps") or []) if g["field"] == "revenue" and not source_break
                and not (g["ratio"] and 0.9 <= g["ratio"] <= 1.1)]
    if restated:
        checks.append(_check("Điều chỉnh hồi tố / đổi phạm vi hợp nhất",
                             "; ".join(f"{g['year']}: DART gốc {g['dart']:,.0f} → {g['naver']:,.0f}억" for g in restated),
                             WARN, "Dùng số đã điều chỉnh (Naver) cho các năm này; các năm trước đó khác phạm vi nên tăng "
                                   "trưởng và biên lịch sử kém chắc"))
    checks.append(_check("Giá & vốn hóa", f"{price:,.0f}" if price else "—",
                         OK if (price and mc) else FAIL,
                         "" if (price and mc) else "Không có giá → không kiểm được vùng tin cậy"))
    checks.append(_check("BPS > 0", f"{bps:,.0f}" if bps is not None else "—",
                         OK if (bps and bps > 0) else FAIL,
                         "" if (bps and bps > 0) else "Vốn chủ âm/thiếu → mô hình sổ sách không dùng được"))

    last = reals[-1] if reals else {}
    eps_last = _num(last.get("eps")) or eps
    # Số cổ phiếu của dự phóng = cơ sở EPS mới nhất (financials.share_basis). So chéo với (a) cơ sở của BCTC năm gần
    # nhất và (b) số cp niêm yết (vốn hóa ÷ giá). Ngoài [0,5; 1,5]: sáp nhập / tách / niêm yết mới → BPS đầu kỳ và lịch
    # sử không còn cùng gốc với số cp hiện tại (HD건설기계 2,7× sau sáp nhập 2026) → chặn mô hình dựa trên dự phóng mỗi
    # cổ phiếu. Trong dải nhưng đã đổi > 10% (Samsung Biologics 0,69×, KT&G 0,88×): EPS dự phóng dùng số cp mới, chỉ ⚠️.
    sb = share_basis(reals, quarters, mc, price)
    s_now, s_prev, listed = sb["shares"], sb["prev"], sb["listed"]
    share_break = False
    if s_now and listed:
        r_list = listed / s_now
        r_prev = (s_now / s_prev) if s_prev else None
        lo_b, hi_b = SHARE_BREAK_BAND
        share_break = (not lo_b <= r_list <= hi_b) or (r_prev is not None and not lo_b <= r_prev <= hi_b)
        moved = r_prev is not None and not 0.9 <= r_prev <= 1.1
        off_list = not 0.7 <= r_list <= 1.3
        val = (f"{s_now / 1e6:,.1f} triệu ({sb['source']})"
               + (f" · năm trước {s_prev / 1e6:,.1f}" if (s_prev and abs(r_prev - 1) > 0.005) else "")
               + f" · niêm yết {listed / 1e6:,.1f}")
        note = ("Số cp đổi mạnh so với BCTC năm (sáp nhập/tách/phát hành) — chặn mô hình dựa trên dự phóng mỗi cổ phiếu"
                if share_break else
                f"Số cp đã đổi {(r_prev - 1) * 100:+.0f}% so với BCTC năm (mua lại/hủy/phát hành) — EPS dự phóng tính trên "
                "số cp mới" if moved else
                "Số cp niêm yết lệch > 30% so với cơ sở EPS (cổ phiếu ưu đãi / cổ phiếu quỹ) — số mỗi cổ phiếu tính trên "
                "cơ sở EPS" if off_list else "")
        checks.append(_check("Số cổ phiếu (cơ sở EPS)", val, FAIL if share_break else WARN if (moved or off_list) else OK,
                             note))
    # Tỷ lệ LN cổ đông mẹ ÷ LN hợp nhất dùng để đổi LN dự phóng → EPS (chỉ khi dữ liệu có tách cổ đông mẹ)
    parent_break = False
    if _has_parent_profit(reals, quarters):
        pr = parent_ratio(reals, _ytd_run_rate(reals, quarters))
        parent_break = not pr["reliable"]
        dev = (pr["rest"] / pr["last"] - 1) if (pr["reliable"] and pr["last"] and pr["last"] > 0) else None
        unstable = dev is not None and abs(dev) > 0.25
        # dòng ❌ hiện tỷ lệ GÂY lỗi (không phải tỷ lệ dự phòng đang dùng cho năm 2+)
        shown = pr.get("raw") if parent_break else pr["rest"]
        checks.append(_check("LN cổ đông mẹ ÷ LN hợp nhất",
                             (f"{shown:.2f}" if shown is not None else "trái dấu") + f" ({pr['source']})"
                             + (f" · năm trước {pr['last']:.2f}" if pr["last"] is not None else ""),
                             FAIL if parent_break else WARN if unstable else OK,
                             "LN hợp nhất và LN cổ đông mẹ trái dấu hoặc tỷ lệ ngoài "
                             f"[{PARENT_RATIO_BAND[0]}; {PARENT_RATIO_BAND[1]}] — không đổi được LN dự phóng sang EPS → chặn "
                             "mô hình dựa trên dự phóng mỗi cổ phiếu" if parent_break else
                             "Tỷ lệ đổi > 25% so với năm trước: phần của cổ đông thiểu số biến động, EPS các năm sau kém "
                             "chắc" if unstable else ""))
    # BPS × số cp so với VCSH (DART — có cả cổ đông thiểu số nên chỉ cảnh báo). So CÙNG KỲ: BPS cuối năm tài chính ×
    # số cp cơ sở EPS của chính năm đó ↔ VCSH cuối năm. (BPS ở row là của quý gần nhất: đem so với VCSH cuối năm
    # trước sẽ báo động giả khi lợi nhuận trong năm lớn — Samsung nửa đầu 2026.) Chuỗi không tách cổ đông mẹ: cách cũ.
    eq = _num(last.get("equity"))
    if _has_parent_profit(reals, quarters):
        bps_chk, shares = _num(last.get("bps")) or bps, (s_prev or s_now or listed)
    else:
        bps_chk, shares = bps, listed
    if shares and bps_chk and eq and eq > 0:
        r = bps_chk * shares / 1e8 / eq
        checks.append(_check("BPS × số cp ÷ VCSH", f"{r:.2f}×",
                             OK if 0.6 <= r <= 1.3 else WARN,
                             "" if 0.6 <= r <= 1.3 else "Lệch lớn: cổ đông thiểu số lớn hoặc BPS khác kỳ"))
    # Cột Q4 của Naver (cả năm − 9 tháng) phi lý → bị loại khỏi run-rate năm 1 (financials.quarter_ok)
    ytd_rr = _ytd_run_rate(reals, quarters)
    if ytd_rr and ytd_rr.get("dropped"):
        checks.append(_check("Quý suy ra bị loại khỏi run-rate",
                             ", ".join(f"{str(p)[:4]}.{str(p)[4:6]}" for p in ytd_rr["dropped"]), WARN,
                             "Doanh thu quý (cả năm − 9 tháng) ≤ 0, LN > doanh thu hoặc lệch > 3× các quý lân cận — "
                             "năm 1 ước theo các quý thực còn lại"))
    # ROE công bố so với EPS/BPS. Naver tính ROE trên VCSH BÌNH QUÂN năm → so với BPS bình quân đầu–cuối năm tài chính
    # (BPS ở row là cuối QUÝ gần nhất: báo ⚠️ sai ở mã tăng vốn chủ nhanh — Hynix lệch 28 điểm, rà soát 18/11/2026)
    roe_rep = _num(last.get("roe"))
    bps_now = _num(last.get("bps"))
    bps_prev = _num(reals[-2].get("bps")) if len(reals) >= 2 else None
    bps_avg = ((bps_prev + bps_now) / 2 if (bps_now and bps_now > 0 and bps_prev and bps_prev > 0)
               else bps_now if (bps_now and bps_now > 0) else None)
    if roe_rep is not None and eps_last and bps_avg:
        d = abs(roe_rep - eps_last / bps_avg * 100)
        checks.append(_check("ROE công bố vs EPS/BPS", f"lệch {d:.1f} điểm %",
                             OK if d <= 5 else WARN,
                             "trên BPS bình quân năm" if d <= 5 else
                             "ROE công bố lệch EPS ÷ BPS bình quân năm — khác kỳ / khác định nghĩa"))
    # Độ mới của BCTC năm: theo HẠN NỘP 사업보고서 (cuối tháng thứ 3 sau năm tài chính — reporting.expected_fy), không
    # theo năm dương lịch: từ 1/1 tới hạn nộp, BCTC năm trước chưa có là bình thường (trước 18/11/2026 mọi mã ❌ từ 1/1)
    p_last = str(last.get("period", ""))
    try:
        y_last = int(p_last[:4])
    except ValueError:
        y_last = None
    if y_last:
        fy_m = int(p_last[4:6]) if (p_last[4:6].isdigit() and 1 <= int(p_last[4:6]) <= 12) else 12
        exp_y = expected_fy(today, fy_m)
        if y_last >= exp_y:
            nxt = y_last + 1
            nxt_end = _dt.date(nxt, fy_m, calendar.monthrange(nxt, fy_m)[1])     # ngày kết thúc năm tài chính kế
            checks.append(_check("Năm BCTC thực gần nhất", y_last, OK,
                                 f"đang chờ BCTC năm {nxt} (hạn {fy_due_date(nxt, fy_m):%d/%m})"
                                 if today > nxt_end else ""))
        else:
            checks.append(_check("Năm BCTC thực gần nhất", y_last, FAIL,
                                 f"BCTC năm quá cũ — BCTC năm {exp_y} đã quá hạn nộp "
                                 f"({fy_due_date(exp_y, fy_m):%d/%m/%Y})"))
    # Quý thực gần nhất
    qs = [c for c in (quarters or []) if not c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()]
    if qs:
        p = str(qs[-1]["period"])
        # tuổi tính từ kỳ MỚI HƠN giữa quý thực cuối và BCTC năm gần nhất (năm chỉ có ở DART đã gồm quý mà Naver chưa có
        # — I14); quý đứng ở Q4 của năm BCTC mà quá 6 tháng vẫn ⚠️ (nguồn quý bị kẹt)
        p_ref = max(p[:6], p_last[:6]) if p_last[:6].isdigit() else p[:6]
        q_age = (today.year - int(p_ref[:4])) * 12 + today.month - int(p_ref[4:6])
        fresh = q_age <= 6
        checks.append(_check("Quý thực gần nhất", f"{p[:4]}.{p[4:6]}", OK if fresh else WARN,
                             ("số đã nộp DART (Naver chưa cập nhật quý này)" if qs[-1].get("_src") == "DART" else "")
                             if fresh else "Quý mới nhất >6 tháng — dự phóng năm 1 bỏ qua số mới"))
    else:
        checks.append(_check("Quý thực gần nhất", "—", WARN, "Không có BCTC quý"))
    # lãi/lỗ bất thường một lần trong các quý thực (so với tỷ lệ LN thuần/LN HĐ của chính mã)
    ratio = conversion_ratio(reals)
    odd = [c for c in qs[-5:] if one_off_core(c, ratio) is not None]
    if qs:
        checks.append(_check("Lãi/lỗ bất thường (quý)",
                             ", ".join(f"{str(c['period'])[:4]}.{str(c['period'])[4:6]}: LN {c['net_profit']:,.0f}억 "
                                       f"vs lõi {one_off_core(c, ratio):,.0f}억" for c in odd) or "không",
                             WARN if odd else OK,
                             f"LN thuần/LN HĐ lệch xa tỷ lệ thường của mã ({ratio:.2f}) — số công bố giữ nguyên, "
                             "nhưng KHÔNG ngoại suy cho các quý chưa công bố" if odd else ""))
    # Không có beta riêng → Ke tính với β = 1 (cao hơn ~1,5 điểm % so với β chạm sàn: KT&G −21%, KB −18%). Đó là giả
    # định thay cho số liệu nên kết quả không được quá Low (trước 15/11/2026 chỉ là ⚠️ và có mã còn LÊN High).
    checks.append(_check("Beta riêng mã (52 tuần)", f"{beta:.2f}" if beta else "—",
                         OK if beta else FAIL,
                         "" if beta else "Không lấy được giá tuần (lỗi mạng hoặc niêm yết < 30 tuần) → dùng β = 1"))
    share_why = None
    no_cap: set = set()            # dòng chỉ để biết (chỉ mất mô hình ĐỐI CHIẾU) — hiện nhưng không đếm vào trần
    for name, f in (feeds or {}).items():
        if f and not f.get("ok", True):
            level = f.get("level") or FAIL
            label = {"rf": "Lãi suất phi rủi ro (Rf)"}.get(name, name)
            note = str(f.get("note") or "Đang dùng giá trị dự phòng")
            if f.get("no_cap") and level == WARN:
                no_cap.add(label)
                note += " (chỉ mất mô hình đối chiếu — không tính vào trần tin cậy)"
            checks.append(_check(label,
                                 f.get("value") or ("không lấy được trực tiếp" if level == FAIL else "không dùng được"),
                                 level, note))
            if f.get("share_break"):       # nguồn thiếu số mỗi cổ phiếu (EPS/BPS/LN cổ đông mẹ) → chặn như số cp đổi
                share_break, share_why = True, name
    # Ô tóm tắt nguồn và độ mới (C8): các cờ đã có dòng riêng ở trên (lỗi DART, BCTC Naver, bộ đối thủ, quý DART chưa
    # ghép, Rf) chỉ được NHẮC lại ở đây, không đếm hai lần; ô này chỉ tự chấm danh mục corp_code > 30 ngày (⚠️)
    source_line = None
    if sources is not None:
        line, st_ = source_summary(sources, checks, today)
        source_line = line
        checks.append(_check("Nguồn và độ mới dữ liệu", line, st_,
                             "Danh mục mã → corp_code DART đã quá 30 ngày — mã mới niêm yết có thể thiếu số DART"
                             if st_ == WARN else "Tóm tắt; ❌/⚠️ của từng nguồn đã tính ở dòng riêng"))

    n_fail = sum(1 for c in checks if c["Kết quả"] == FAIL)
    # dòng 'no_cap' (BCTC đầy đủ / số DART phụ khác năm hoặc đơn vị) không đếm: mỗi trang truyền fs_full/extras khác
    # nhau (🧮 cả hai, Sàng lọc chỉ fs_full, Tổng hợp không) → đếm vào thì cùng mã cùng ngày ra trần khác nhau
    n_warn = sum(1 for c in checks if c["Kết quả"] == WARN and c["Kiểm tra"] not in no_cap)
    cap = "Low" if n_fail else ("Medium" if n_warn >= 2 else None)
    return {"checks": checks, "cap": cap, "n_fail": n_fail, "n_warn": n_warn, "share_break": share_break,
            "parent_break": parent_break, "source_break": source_break, "share_break_why": share_why,
            "period_break": period_break, "source_line": source_line}


CORP_MAP_MAX_DAYS = 30            # danh mục corp_code DART cũ hơn → ⚠️ (C8, I50)


def _source_info(fin: Optional[dict], series: list[dict], quarters: Optional[list[dict]],
                 dart_quarters: Optional[list[dict]], peers: Optional[list[dict]],
                 extra: Optional[dict] = None) -> dict:
    """Tóm tắt nguồn của một lần chạy (C8): quý thực gần nhất của Naver / DART, năm DART đầu–cuối và trạng thái, số
    đối thủ lấy được, ngày danh mục corp_code (theo meta, không theo mtime). extra: {rf_asof, beta_weeks} của trang gọi."""
    def last_q(qs):
        ps = [str(c.get("period", ""))[:6] for c in (qs or []) if not c.get("is_consensus")
              and str(c.get("period", ""))[:6].isdigit()]
        return max(ps) if ps else None
    reals = _reals(series)
    src = (reals[-1].get("_src") if reals else None) or {}
    dart_y = sorted({str(x)[:4] for c in reals if c.get("_dart")          # kỳ ngắn: năm của chính các kỳ DART
                     for x in (c.get("_dart_periods") or [c.get("period", "")])})
    rows = [r for r in (peers or []) if not r.get("_list_error")]
    info = {"naver_q": last_q((fin or {}).get("quarter")), "dart_q": last_q(dart_quarters),
            "used_q": last_q(quarters), "dart_years": (dart_y[0], dart_y[-1]) if dart_y else None,
            "dart_state": src.get("dart"), "peers_ok": sum(1 for r in rows if r.get("fetch_ok") is not False),
            "peers_n": len(rows), "corp_date": None}
    try:
        import dart_api as _D
        if _D.available():
            at = str(_D._corp_meta().get("downloaded_at") or "")
            info["corp_date"] = (_dt.date.fromisoformat(at[:10]) if (len(at) >= 10 and os.path.exists(_D.CORP_DL_PATH))
                                 else "repo")
    except Exception:
        pass
    info.update({k: v for k, v in (extra or {}).items() if v is not None})
    return info


def source_summary(info: dict, checks: Optional[list[dict]] = None,
                   today: Optional[_dt.date] = None) -> tuple[str, str]:
    """Một dòng 'nguồn và độ mới' dùng chung cho 🧮, Tổng hợp, Sàng lọc, bot + trạng thái của riêng ô này (✅ / ⚠️ khi
    danh mục corp_code > CORP_MAP_MAX_DAYS ngày hoặc chỉ có bản dự phòng trong repo)."""
    today = today or _G.today_kst()

    def fq(p):
        return f"{p[:4]}.{p[4:6]}" if p else "—"
    bad = {c["Kiểm tra"]: c["Kết quả"] for c in (checks or []) if c.get("Kết quả") in (WARN, FAIL)}
    parts = [f"Naver quý {fq(info.get('naver_q'))}"]
    if info.get("dart_q"):
        merged = info.get("used_q") and info["used_q"] > (info.get("naver_q") or "")
        parts.append(f"DART quý {fq(info['dart_q'])}" + (" (đã ghép)" if merged else
                                                          f" {bad['Quý DART mới hơn Naver']}"
                                                          if "Quý DART mới hơn Naver" in bad else ""))
    dy = info.get("dart_years")
    stt = {"error": f"{FAIL} lỗi", "mismatch": f"{FAIL} lệch Naver", "cached": f"{WARN} bản lưu",
           "currency": f"{WARN} khác KRW"}.get(info.get("dart_state"), "")
    parts.append(("DART năm " + (f"{dy[0]}–{dy[1]}" if dy and dy[0] != dy[1] else dy[0] if dy else "—")
                  + (f" {stt}" if stt else "")))
    if "BCTC Naver (EPS/BPS/LN cổ đông mẹ)" in bad:
        parts.append(f"BCTC Naver {FAIL}")
    if info.get("peers_n") is not None:
        parts.append(f"đối thủ {info.get('peers_ok', 0)}/{info['peers_n']}" + (f" {FAIL}" if "Bộ đối thủ" in bad else ""))
    status = OK
    cd = info.get("corp_date")
    if cd:
        if isinstance(cd, _dt.date):
            age = (today - cd).days
            stale = age > CORP_MAP_MAX_DAYS
            parts.append(f"danh mục corp_code {cd:%d/%m/%Y}" + (f" {WARN} {age} ngày" if stale else ""))
        else:
            stale = True
            parts.append(f"danh mục corp_code: bản dự phòng trong repo {WARN}")
        status = WARN if stale else OK
    if info.get("rf_asof"):
        parts.append(f"Rf {info['rf_asof']}" + (f" {bad['Lãi suất phi rủi ro (Rf)']}" if "Lãi suất phi rủi ro (Rf)" in bad
                                                 else ""))
    if info.get("beta_weeks"):
        parts.append(f"beta {info['beta_weeks']} tuần")
    return " · ".join(parts), status


# ============================================================================ Bước 3 — phân tích
def ratio_table(series: list[dict]) -> list[dict]:
    """Tỉ số từng năm thực + cờ năm bất thường (ROE lệch > 2× trung vị hoặc khác dấu)."""
    reals = _reals(series)
    rows, prev = [], None
    for c in reals:
        rev, op, np_, eq = (_num(c.get(k)) for k in ("revenue", "op_profit", "net_profit", "equity"))
        roe = (np_ / eq) if (np_ is not None and eq) else (
            _num(c.get("roe")) / 100 if c.get("roe") is not None else None)
        eps, dps = _num(c.get("eps")), _num(c.get("dps"))
        rows.append({
            "Năm": str(c.get("period", ""))[:4],
            "Doanh thu (억)": rev,
            "Tăng trưởng DT %": (rev / prev - 1) * 100 if (rev and prev and prev > 0) else None,
            "Biên LN HĐ %": op / rev * 100 if (op is not None and rev) else None,
            "Biên LN ròng %": np_ / rev * 100 if (np_ is not None and rev) else None,
            "ROE %": roe * 100 if roe is not None else None,
            "Nợ/VCSH %": _num(c.get("debt_ratio")),
            "Tỷ lệ cổ tức %": dps / eps * 100 if (dps and eps and eps > 0) else None,
        })
        prev = rev
    roes = [r["ROE %"] for r in rows if r["ROE %"] is not None]
    med = V._median(roes)
    ratio = conversion_ratio(reals)
    odd_years = {str(c.get("period", ""))[:4] for c in reals if one_off_core(c, ratio) is not None}
    for r in rows:
        x, flag = r["ROE %"], ""
        if x is not None and med is not None and med != 0:
            if (x > 0) != (med > 0):
                flag = "ROE khác dấu trung vị"
            elif abs(x) > 2 * abs(med) or abs(x) < 0.5 * abs(med):
                flag = "ROE lệch >2× trung vị"
        if r["Năm"] in odd_years:
            flag = (flag + " · " if flag else "") + "LN thuần lệch LN HĐ (lãi/lỗ một lần?)"
        r["Năm bất thường"] = flag
    return rows


# ============================================================================ Bước 4 — dự phóng
def _hist_net_margins(reals: list[dict]) -> list[float]:
    return [c["net_profit"] / c["revenue"] for c in reals
            if c.get("net_profit") is not None and c.get("revenue")]


def _payout_hist(reals: list[dict]) -> Optional[float]:
    xs = [c["dps"] / c["eps"] for c in reals
          if c.get("dps") is not None and c.get("eps") and c["eps"] > 0]
    m = V._median(xs)
    return min(max(m, 0.0), 0.9) if m is not None else None


def reported_evidence(series: list[dict], quarters: Optional[list[dict]]) -> Optional[dict]:
    """Năm dự phóng đã có ≥ 2 quý THỰC công bố → tăng trưởng/biên của năm đó là bằng chứng số, không phải giả
    định. Trả {k, growth, margin} hoặc None. Cổng dự phóng nới theo bằng chứng này (không theo lời giải thích)."""
    reals = _reals(series)
    ytd = _ytd_run_rate(reals, quarters)
    rev_last = _num(reals[-1].get("revenue")) if reals else None
    if not ytd or ytd["k"] < 2 or not rev_last or rev_last <= 0 or ytd.get("net_profit") is None:
        return None
    rep = ytd["net_profit"] / ytd["revenue"]
    core = ytd.get("net_profit_core", ytd["net_profit"]) / ytd["revenue"]
    # margin = biên LÕI (bỏ lãi/lỗ bất thường một lần) — dùng cho 'max lịch sử' và điểm xuất phát năm 2+;
    # margin_reported = biên công bố (gồm phần một lần) — chỉ là trần của RIÊNG năm 1
    return {"k": ytd["k"], "growth": ytd["revenue"] / rev_last - 1, "margin": core, "margin_reported": rep,
            "one_offs": ytd.get("one_offs", []), "conv_ratio": ytd.get("conv_ratio")}


MID_MARGIN_FLOOR = 0.0          # sàn biên LN ròng dài hạn mặc định (hòa vốn) — xem default_drivers


SHARE_BREAK_BAND = (0.5, 1.5)    # EPS × số cp hiện tại ÷ LN năm: ngoài dải = số cp đã đổi mạnh (data_quality)


def default_drivers(series: list[dict], row: dict, a: V.Assumptions, years: int = 5,
                    quarters: Optional[list[dict]] = None, cyclical: bool = False,
                    financial: bool = False) -> list[dict]:
    """Giả định mặc định cho dự phóng (người dùng sửa trên bảng):

      • Năm 1: theo financials.project_forecast (đã tính các quý thực đã công bố của năm đó).
      • Tăng trưởng DT: năm 1 giữ nguyên nếu đã có ≥ 2 quý thực, không thì kẹp [-20%; +30%]; năm 2+ trượt
        tuyến tính từ g năm 1 đã kẹp về g dài hạn ở năm cuối.
      • Biên LN ròng: ngành chu kỳ / biên năm 1 âm → trượt dần về trung vị lịch sử (mid-cycle);
        còn lại giữ biên xu hướng.
      • Tỷ lệ cổ tức: trung vị lịch sử (kẹp 0–90%), không có → 0.
      • Năm 1 có lãi/lỗ bất thường một lần (quý thực): năm 1 giữ số công bố; năm 2+ xuất phát từ biên LÕI và
        (doanh nghiệp phi tài chính) không vượt trần biên của cổng dự phóng — mặc định luôn qua cổng.
    """
    reals = _reals(series)
    if len(reals) < 2:
        return []
    mc, price = _num(row.get("market_cap")), _num(row.get("price"))
    fc1 = project_forecast(reals, mc, price, quarters=quarters)
    rev_last = _num(reals[-1].get("revenue"))
    if not fc1 or not rev_last or rev_last <= 0 or not fc1.get("revenue"):
        return []
    ev = reported_evidence(series, quarters)
    g1 = fc1["revenue"] / rev_last - 1
    if not ev:                                     # chỉ là ngoại suy → kẹp; có ≥2 quý thực thì giữ nguyên
        g1 = min(max(g1, -0.20), 0.30)
    hm = _hist_net_margins(reals)
    m1 = (fc1["net_profit"] / fc1["revenue"]) if fc1.get("net_profit") is not None else _trend_value(hm)
    if m1 is None:
        return []
    m_mid = V._median(hm)
    # Biên dài hạn (mid-cycle) không được âm: trung vị lịch sử âm (vd 이수화학 2020–25, 4/6 năm lỗ) mà trượt về đó
    # = giả định LỖ VĨNH VIỄN trên doanh thu vẫn tăng → RIM ≤ 0 → "không tính được" → mất mô hình chính dù năm 1
    # đã có lãi quý thực. Doanh nghiệp hoạt động liên tục: sàn hòa vốn (0%) — ROE 0% < Ke nên vẫn thận trọng
    # (giá trị dưới sổ sách ở trạng thái dừng). Người dùng sửa được ở bảng ④; cổng số giữ nguyên.
    mid_floored = m_mid is not None and m_mid < MID_MARGIN_FLOOR
    if mid_floored:
        m_mid_raw, m_mid = m_mid, MID_MARGIN_FLOOR
    m1_rest = ev["margin"] if ev else m1          # điểm xuất phát năm 2+: biên lõi, không mang phần một lần
    fade_margin = (cyclical or m1_rest <= 0) and m_mid is not None
    g_long = a.g_term_capped()
    payout = _payout_hist(reals) or 0.0
    y0 = int(str(reals[-1]["period"])[:4])
    # Năm 2+ là GIẢ ĐỊNH, không phải số công bố: trượt từ g năm 1 đã kẹp [-20%; +30%] về g dài hạn — đợt
    # tăng vọt của năm 1 (bằng chứng quý thực) không được ngoại suy sang các năm sau.
    g_from = min(max(g1, -0.20), 0.30)
    conv = conversion_ratio(reals)
    out = []
    for t in range(1, years + 1):
        k = (t - 1) / (years - 1) if years > 1 else 1.0
        g = g1 if t == 1 else g_from + (g_long - g_from) * k
        if t == 1:
            m = m1
        else:
            m = (m1_rest + (m_mid - m1_rest) * k) if fade_margin else m1_rest
            if not financial:
                m = min(m, FC_MARGIN_MAX)
        # biên LN HĐ (cho EBIT/FCFF): năm 1 theo dự phóng công thức nếu có; còn lại = biên ròng ÷ tỷ lệ LN thuần/LN HĐ
        # của chính mã (cùng tỷ lệ dùng để nhận diện lãi một lần)
        opm = (fc1["op_profit"] / fc1["revenue"]) if (t == 1 and fc1.get("op_profit") is not None) else m / conv
        out.append({"Năm": str(y0 + t), "Tăng trưởng DT %": round(g * 100, 1),
                    "Biên LN HĐ %": round(opm * 100, 2),
                    "Biên LN ròng %": round(m * 100, 2), "Tỷ lệ cổ tức %": round(payout * 100, 1)})
    if out:
        out[0]["_basis"] = fc1.get("_basis", "") + (
            f" · biên dài hạn: trung vị lịch sử âm ({m_mid_raw * 100:.1f}%) → chặn ở hòa vốn "
            f"{MID_MARGIN_FLOOR * 100:.0f}%" if (mid_floored and fade_margin) else "")
    return out


def no_forecast_reason(series: list[dict], drivers: Optional[list[dict]] = None) -> str:
    """Lý do KHÔNG có dự phóng (để ghi đúng ở ô bị chặn — trước đây luôn là 'thiếu doanh thu/LN ≥ 2 năm')."""
    reals = _reals(series)
    y = str(reals[-1].get("period", ""))[:4] if reals else ""
    if sum(1 for c in reals if c.get("revenue") is not None) < 2:
        return "không có dự phóng (thiếu doanh thu/LN ≥ 2 năm)"
    if reals[-1].get("revenue") is None:
        return f"không có dự phóng (năm gốc {y} thiếu doanh thu)"
    if not drivers:
        return f"không có dự phóng (năm gốc {y} thiếu doanh thu/LN để dựng giả định)"
    if reals[-1].get("bps") is None and reals[-1].get("equity_parent") is None:
        return f"không có dự phóng (năm gốc {y} thiếu BPS / VCSH cổ đông mẹ)"
    return "không có dự phóng (thiếu số cp cơ sở hoặc doanh thu năm gốc)"


DRIVER_REQUIRED = ("Tăng trưởng DT %", "Biên LN ròng %", "Tỷ lệ cổ tức %")   # ô bắt buộc là số (thiếu cổ tức = 0)


def run_forecast(series: list[dict], row: dict, drivers: list[dict],
                 quarters: Optional[list[dict]] = None) -> list[dict]:
    """Áp giả định → DT, LN, EPS, DPS, BPS, ROE từng năm.

    EPS = LN ròng (hợp nhất) × tỷ lệ cổ đông mẹ × 1e8 ÷ số cp, với số cp = cơ sở EPS MỚI NHẤT và tỷ lệ cổ đông mẹ theo
    các quý thực đã công bố (financials.share_basis / parent_ratio — cùng cơ sở với project_forecast). Trước 15/11/2026
    dùng LN hợp nhất năm trước ÷ EPS năm trước cho mọi năm: sai khi số cp đổi (Samsung Biologics: EPS 2026 dự phóng
    26.570 trong khi nửa đầu năm đã đạt 19.278) hoặc phần cổ đông thiểu số đổi (SK Inc +39%, Doosan −51%)."""
    reals = _reals(series)
    if not reals or not drivers:
        return []
    mc, price = _num(row.get("market_cap")), _num(row.get("price"))
    shares = share_basis(reals, quarters, mc, price)["shares"]
    rho = parent_ratio(reals, _ytd_run_rate(reals, quarters))
    rev = _num(reals[-1].get("revenue"))
    # B0 = BPS CUỐI NĂM TÀI CHÍNH gần nhất: năm 1 dự phóng là CẢ năm sau đó. BPS Naver ở row là cuối QUÝ gần nhất (đã
    # gồm LN các quý của năm 1) → cộng EPS cả năm lên đó đếm hai lần (005930: 63.997 cuối 2025 vs 86.052 Q2/2026)
    # Thiếu BPS năm: VCSH cổ đông mẹ ÷ số cp; vẫn thiếu → không dự phóng. KHÔNG lùi về BPS của row (cuối quý — năm chỉ
    # có ở DART từng làm B0 lệch: Samsung −5,3%, Hynix −15,7% — rà soát 18/11/2026, I14)
    bps = _num(reals[-1].get("bps"))
    eqp = _num(reals[-1].get("equity_parent"))
    if bps is None and eqp is not None and shares:
        bps = eqp * 1e8 / shares
    if not shares or not rev or bps is None:
        return []
    conv = conversion_ratio(reals)
    out = []
    for i, d in enumerate(drivers):
        # ô trống / không phải số (data_editor trả NaN khi xóa ô) → ghi _blank để forecast_gates chặn; KHÔNG coi là 0
        # (biên trống từng thành EPS 0 mà vẫn ✅ qua cổng — rà soát 18/11/2026). Số 0 tạm chỉ để dựng dòng.
        blank = [k for k in DRIVER_REQUIRED if _num(d.get(k)) is None and (k in d or k != "Tỷ lệ cổ tức %")]
        g = (_num(d.get("Tăng trưởng DT %")) or 0.0) / 100
        m = (_num(d.get("Biên LN ròng %")) or 0.0) / 100
        po = min(max((_num(d.get("Tỷ lệ cổ tức %")) or 0.0) / 100, 0.0), 1.0)
        opm = _num(d.get("Biên LN HĐ %"))
        opm = opm / 100 if opm is not None else m / conv
        bps_open = bps
        rev = rev * (1 + g)
        np_ = rev * m
        np_parent = np_ * (rho["y1"] if i == 0 else rho["rest"])
        eps = np_parent * 1e8 / shares
        dps = max(eps * po, 0.0)
        bps = bps + eps - dps
        out.append({"Năm": d.get("Năm"), "Doanh thu (억)": rev, "LN HĐ (억)": rev * opm, "LN ròng (억)": np_,
                    "LN cổ đông mẹ (억)": np_parent,
                    "Tăng trưởng DT %": g * 100, "Biên LN HĐ %": opm * 100, "Biên LN ròng %": m * 100,
                    "EPS": eps, "DPS": dps, "BPS đầu kỳ": bps_open, "BPS cuối kỳ": bps,
                    "ROE %": eps / bps_open * 100 if bps_open and bps_open > 0 else None})
        if blank:
            out[-1]["_blank"] = blank
    return out


def forecast_gates(series: list[dict], fc: list[dict], financial: bool = False,
                   evidence: Optional[dict] = None, quarters: Optional[list[dict]] = None) -> dict:
    """Cổng cứng bằng số cho dự phóng. Có ❌ → mô hình dựa trên dự phóng KHÔNG vào kết luận.

    evidence (reported_evidence): năm 1 đã có ≥2 quý thực → dải tăng trưởng năm 1 nới tới số đã công bố
    (+5 điểm), và biên LN công bố được tính vào 'max lịch sử'. Chỉ số thực mới nới được cổng.
    quarters: năm 1 đã có ≥ 1 quý thực → bỏ cổng sàn EPS (số lỗ đã công bố là bằng chứng, không phải giả định)."""
    reals = _reals(series)
    hm = _hist_net_margins(reals)
    if evidence:
        hm = hm + [evidence["margin"]]
    m_max = max(hm) if hm else None
    checks = []
    for i, f in enumerate(fc):
        y = f["Năm"]
        if f.get("_blank"):
            checks.append(_check(f"{y}: " + ", ".join(f["_blank"]), "trống / không phải số", FAIL,
                                 "điền số vào ô giả định (không tự coi là 0)"))
            continue
        g = f["Tăng trưởng DT %"] / 100
        lo, hi = FC_GROWTH_BAND
        if i == 0 and evidence:
            lo, hi = min(lo, evidence["growth"] - 0.05), max(hi, evidence["growth"] + 0.05)
        if not lo <= g <= hi:
            checks.append(_check(f"{y}: tăng trưởng DT", f"{g * 100:+.1f}%", FAIL,
                                 f"ngoài [{lo * 100:.0f}%; +{hi * 100:.0f}%]"))
        m = f["Biên LN ròng %"] / 100
        # năm 1 đã có ≥2 quý thực: biên ≤ biên CÔNG BỐ (+2 điểm) là số thật, không phải giả định → qua
        # (gồm cả lãi một lần đã ghi nhận — nó vào vốn chủ MỘT lần; năm 2+ không mang theo)
        y1_reported = (i == 0 and evidence is not None
                       and m <= evidence.get("margin_reported", evidence["margin"]) + 0.02)
        if not y1_reported and m_max is not None and m > m_max + FC_MARGIN_HEADROOM:
            checks.append(_check(f"{y}: biên LN ròng", f"{m * 100:.1f}%", FAIL,
                                 f"> max lịch sử/đã công bố {m_max * 100:.1f}% + {FC_MARGIN_HEADROOM * 100:.0f} điểm"))
        if not y1_reported and not financial and m > FC_MARGIN_MAX:
            checks.append(_check(f"{y}: biên LN ròng", f"{m * 100:.1f}%", FAIL,
                                 f"> {FC_MARGIN_MAX * 100:.0f}% (doanh nghiệp phi tài chính)"))
        roe = f.get("ROE %")
        if not y1_reported and roe is not None and roe / 100 > FC_ROE_MAX:
            checks.append(_check(f"{y}: ROE", f"{roe:.1f}%", FAIL, f"> {FC_ROE_MAX * 100:.0f}%"))
        if f["BPS cuối kỳ"] is not None and f["BPS cuối kỳ"] <= 0:
            checks.append(_check(f"{y}: BPS", f"{f['BPS cuối kỳ']:,.0f}", FAIL, "vốn chủ âm"))
    # sàn: EPS năm 1 ≤ 0 khi năm gần nhất có lãi và mọi năm lịch sử đều có lãi — trừ khi năm 1 đã có quý thực
    # (dù chỉ 1 quý: run-rate từ quý lỗ thật cho biên ≤ 0 là số công bố, không phải ô bị xóa)
    eps_last = _num(reals[-1].get("eps")) if reals else None
    has_q = evidence is not None or _ytd_run_rate(reals, quarters) is not None
    if (fc and not fc[0].get("_blank") and not has_q and fc[0]["EPS"] <= 0 and eps_last and eps_last > 0
            and hm and min(hm) > 0):
        checks.append(_check(f"{fc[0]['Năm']}: EPS", f"{fc[0]['EPS']:,.0f}", FAIL,
                             f"≤ 0 trong khi EPS năm gần nhất {eps_last:,.0f} và biên lịch sử đều > 0"))
    n_fail = sum(1 for c in checks if c["Kết quả"] == FAIL)
    if not checks:
        checks.append(_check("Giả định dự phóng", "trong dải", OK, ""))
    return {"checks": checks, "ok": n_fail == 0 and bool(fc), "n_fail": n_fail}


# ============================================================================ Bước 5 — chi phí vốn
def cost_of_capital(a: V.Assumptions, beta_raw: Optional[float], market_cap: Optional[float] = None,
                    extras: Optional[dict] = None, idn: Optional[dict] = None) -> dict:
    """Ke (CAPM, β Blume), Kd ≈ Rf + 1,5%, WACC theo nợ vay DART (nếu có).

    Không tính WACC (wacc_note ghi lý do) khi ngành tài chính (tiền gửi/nợ là nguyên liệu kinh doanh — IBK từng ra WACC
    3,71% < Ke 6,44%) hoặc số DART không lập bằng KRW (Doosan Bobcat: nợ 억 USD so với vốn hóa KRW → WACC = Ke)."""
    b_used = None
    if beta_raw is not None:
        b_used = min(max(beta_raw, 0.5), 1.8)
        if a.blume:
            b_used = 0.67 * b_used + 0.33
    ke = a.ke_stock(beta_raw)
    kd = a.rf + 0.015
    ex = extras or {}
    tax = ex.get("tax_rate", 0.22)
    debt = ex.get("debt")
    cur = str(ex.get("currency") or "KRW")
    wacc_note = None
    if (idn or {}).get("financial"):
        debt, wacc_note = None, "không tính (ngân hàng / tài chính)"
    elif cur != "KRW":
        debt, wacc_note = None, f"không tính (BCTC {cur})"
    wacc = V._wacc(ke, kd, tax, (market_cap or 0) / 1e8, debt) if (debt is not None and market_cap) else None
    return {"rf": a.rf, "erp": a.erp, "beta_raw": beta_raw, "beta_used": b_used if b_used else a.beta,
            "ke": ke, "kd": kd, "tax": tax, "debt_eok": debt, "wacc": wacc, "g_term": a.g_term_capped(),
            "wacc_note": wacc_note}


# ============================================================================ Bước 6 — mô hình trên dự phóng
def rim_explicit(bps0: Optional[float], fc: list[dict], ke: float, w: float = 0.9) -> Optional[dict]:
    """RIM tường minh: V = B0 + Σ RI_t/(1+Ke)^t + RI_N·w/(1+Ke−w)/(1+Ke)^N, RI_t = EPS_t − Ke·B_{t−1}.
    Trả {value, tv_share} hoặc None (B0 ≤ 0 / không có dự phóng / giá trị ≤ 0)."""
    if not fc or not bps0 or bps0 <= 0:
        return None
    pv, ri, rows = 0.0, 0.0, []
    for t, f in enumerate(fc, 1):
        ri = f["EPS"] - ke * f["BPS đầu kỳ"]
        df = 1 / (1 + ke) ** t
        pv += ri * df
        rows.append({"Năm": f.get("Năm", t), "BPS đầu kỳ": f["BPS đầu kỳ"], "EPS": f["EPS"], "ROE %": f.get("ROE %"),
                     "Ke × BPS": ke * f["BPS đầu kỳ"], "Lợi nhuận thặng dư (RI)": ri, "Hệ số chiết khấu": df,
                     "PV RI": ri * df})
    n = len(fc)
    tv = ri * w / (1 + ke - w) / (1 + ke) ** n
    v = bps0 + pv + tv
    if not math.isfinite(v) or v <= 0:
        return None
    return {"value": v, "tv_share": abs(tv) / v, "rows": rows,
            "bridge": [{"Khoản mục": "BPS hiện tại", "KRW/cp": bps0}, {"Khoản mục": "+ Σ PV RI", "KRW/cp": pv},
                       {"Khoản mục": f"+ PV RI cuối kỳ (w = {w})", "KRW/cp": tv},
                       {"Khoản mục": "= Giá trị/cp", "KRW/cp": v}]}


def ddm_explicit(fc: list[dict], ke: float, g: float) -> Optional[dict]:
    """DDM tường minh: Σ DPS_t/(1+Ke)^t + D_{N+1}/(Ke−g)/(1+Ke)^N. Cần cổ tức > 0 năm cuối.

    Cuối kỳ (rà soát 18/11/2026, I07): D_{N+1} = EPS_{N+1} × (1 − g/ROE), EPS_{N+1} = ROE_N × BPS cuối năm N (lợi nhuận
    sạch, cùng gốc RIM); ROE < Ke → g = 0, trả hết lợi nhuận (V.terminal_dividend). Bản cũ DPS_N(1+g) giữ payout lịch
    sử mà vẫn tăng g mãi mãi: DDM dưới sổ sách dù ROE > Ke. Thiếu EPS/BPS → giữ cách cũ; EPS/BPS ≤ 0 → None."""
    if not fc or ke <= g or fc[-1]["DPS"] <= 0:
        return None
    rows = [{"Năm": f.get("Năm", t), "DPS": f["DPS"], "Hệ số chiết khấu": 1 / (1 + ke) ** t,
             "PV DPS": f["DPS"] / (1 + ke) ** t} for t, f in enumerate(fc, 1)]
    pv = sum(r["PV DPS"] for r in rows)
    n = len(fc)
    last = fc[-1]
    eps_n, b_open, b_end = (_num(last.get(k)) for k in ("EPS", "BPS đầu kỳ", "BPS cuối kỳ"))
    if eps_n is None or b_open is None or b_end is None:
        d_next, g_eff = last["DPS"] * (1 + g), g
        lbl = f"+ PV cổ tức cuối kỳ (g = {g * 100:.1f}%)"
    else:
        # EPS/BPS năm cuối ≤ 0 → không có DDM (không kéo cổ tức lịch sử vô hạn cho năm lỗ)
        td = V.terminal_dividend(eps_n / b_open * b_end, b_end, ke, g) if b_open > 0 else None
        if td is None:
            return None
        d_next, g_eff = td
        lbl = f"+ PV cổ tức cuối kỳ (g = {g_eff * 100:.1f}%, tỷ lệ chi trả {d_next / (eps_n / b_open * b_end):.0%})"
    tv = d_next / (ke - g_eff) / (1 + ke) ** n
    v = pv + tv
    return ({"value": v, "tv_share": tv / v, "rows": rows,
             "bridge": [{"Khoản mục": "Σ PV cổ tức", "KRW/cp": pv},
                        {"Khoản mục": lbl, "KRW/cp": tv},
                        {"Khoản mục": "= Giá trị/cp", "KRW/cp": v}]} if v > 0 else None)


# ============================================================================ peer
def peer_medians(peer_rows: list[dict], exclude: Optional[str] = None) -> dict:
    """Trung vị P/E (0; 60] và P/B > 0 của peer (bỏ chính mã), cần ≥ MIN_PEERS mã hợp lệ."""
    rows = [r for r in peer_rows if r.get("ticker") != exclude and not r.get("_list_error")]
    pes = [_num(r.get("per")) for r in rows]
    pbs = [_num(r.get("pbr")) for r in rows]
    return {"pe": V._robust_median(pes, 0.0, V.PE_CAP), "pb": V._robust_median(pbs, 0.0),
            "n_pe": sum(1 for x in pes if x is not None and 0 < x <= V.PE_CAP),
            "n_pb": sum(1 for x in pbs if x is not None and x > 0), "n": len(rows)}


def peer_anchor(methods: dict, midcycle: bool) -> Optional[float]:
    """Phép neo peer = trung vị các phương pháp tương đối (cùng luật value_stock: cần ≥ 2)."""
    keys = (("EPS chuẩn hóa × P/E ngành", "P/B vs median ngành") if midcycle
            else ("P/E vs median ngành", "Forward P/E vs median ngành", "P/B vs median ngành"))
    rel = [methods[k] for k in keys if methods.get(k)]
    return V._median(rel) if len(rel) >= 2 else None


_IND_API = "https://m.stock.naver.com/api/stocks/industry"
_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def _ind_json(url: str, params: dict) -> dict:
    """GET JSON của API ngành Naver: kiểm mã HTTP (429/503 có thân JSON KHÔNG phải 'ngành không có'), thử lại 1 lần."""
    import requests
    err: Exception = RuntimeError("không có phản hồi")
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers=_H, timeout=10)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict):
                return j
            err = ValueError("thân không phải JSON object")
        except Exception as e:
            err = e
    raise err


def _industry_ranked(industry_kr: str) -> Optional[list[str]]:
    """Mọi cổ phiếu THƯỜNG của ngành KRX (업종 Naver) xếp vốn hóa giảm dần. None nếu lỗi nguồn (mạng, HTTP lỗi, thân
    thiếu 'groups'/'stocks') — khác [] = ngành không có trên Naver. Cổ phiếu ưu đãi (mã không tận cùng 0) bị bỏ ở
    đây để không chiếm chỗ trong top-n (rà soát 18/11/2026: 005935 đẩy 031980 khỏi bộ đối thủ của Samsung)."""
    try:
        groups = _ind_json(_IND_API, {"page": 1, "pageSize": 100})
        if "groups" not in groups:
            return None
        no = next((g["no"] for g in groups.get("groups") or [] if g.get("name") == industry_kr), None)
        if no is None:
            return []
        stocks, page = [], 1
        while page <= 5:
            j = _ind_json(f"{_IND_API}/{no}", {"page": page, "pageSize": 100})
            if "stocks" not in j:
                return None
            s = j.get("stocks") or []
            stocks.extend(s)
            if page * 100 >= j.get("totalCount", 0) or not s:
                break
            page += 1
    except Exception:
        return None
    rows = [(str(s.get("itemCode", "")).zfill(6), float(s.get("marketValueRaw") or 0)) for s in stocks
            if s.get("stockEndType") == "stock"]
    rows = [r for r in rows if r[0].endswith("0")]          # chỉ cổ phiếu thường
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows]


_IND_NAMES: dict = {}             # {"at": time.monotonic(), "map": {industryCode: tên ngành}} — danh mục tĩnh, 1 ngày
_IND_NAMES_TTL = 24 * 3600


def industry_name(code, errors: Optional[list] = None) -> Optional[str]:
    """Tên ngành KRX (업종) từ industryCode của Naver (integration) — cho mã chưa có trong danh mục (mới niêm yết).
    1 lượt gọi danh sách ngành, nhớ 1 ngày; lỗi không được nhớ và được ghi vào errors (nếu truyền)."""
    import time as _time
    if code in (None, ""):
        return None
    m = _IND_NAMES.get("map")
    if not m or _time.monotonic() - _IND_NAMES.get("at", 0) > _IND_NAMES_TTL:
        try:
            j = _ind_json(_IND_API, {"page": 1, "pageSize": 100})
        except Exception:
            j = None
        m = {str(g.get("no")): g.get("name") for g in (j or {}).get("groups") or [] if g.get("no") is not None}
        if not m:
            if errors is not None:
                errors.append("danh mục tên ngành Naver lỗi")
            return None
        _IND_NAMES.update({"at": _time.monotonic(), "map": m})
    return m.get(str(code))


def industry_peers(industry_kr: str, exclude: Optional[str] = None, n: int = 12,
                   cache: Optional[dict] = None, errors: Optional[list] = None) -> list[str]:
    """Top-n mã cùng ngành KRX (업종 Naver) theo vốn hóa, bỏ ETF và chính mã. [] nếu lỗi mạng (ghi vào errors).
    cache: dict của MỘT lần chạy (không giữ qua các lần — thứ hạng vốn hóa là số liệu thị trường) để nhiều mã cùng
    ngành không gọi lại danh sách ngành."""
    if cache is not None and industry_kr in cache:
        ranked = cache[industry_kr]
    else:
        ranked = _industry_ranked(industry_kr)
        if cache is not None and ranked is not None:      # lỗi mạng không được nhớ
            cache[industry_kr] = ranked
    if ranked is None and errors is not None:
        errors.append(f"danh sách ngành {industry_kr} lỗi")
    return [t for t in (ranked or []) if t != exclude][:n]


def peer_failures(peers: Optional[list[dict]], errors: Optional[list] = None) -> Optional[str]:
    """Bộ đối thủ có lấy thiếu do lỗi nguồn không: đối thủ lấy lỗi (fetch_ok=False — trông như mã lỗ vì P/E trống) và
    danh sách đối thủ lỗi (dòng đánh dấu _list_error / errors). None = đầy đủ. Rà soát 18/11/2026: mất đối thủ làm
    mất phép neo → cổng [0,25; 4]× và tam giác hóa bị bỏ, cổng LỎNG ra vì lỗi mạng."""
    rows = [r for r in (peers or []) if not r.get("_list_error")]
    bad = [r for r in rows if r.get("fetch_ok") is False]
    lst = list(errors or []) + [r["_list_error"] for r in (peers or []) if r.get("_list_error")]
    parts = []
    if bad:
        parts.append(f"{len(bad)}/{len(rows)} đối thủ không lấy được")
    if lst:
        parts.append("danh sách đối thủ chưa đầy đủ (" + "; ".join(dict.fromkeys(str(x) for x in lst)) + ")")
    return "; ".join(parts) or None


# ============================================================================ Kết luận
def _gate(value: Optional[float], price: Optional[float], anchor: Optional[float]) -> tuple[bool, str]:
    if value is None:
        return False, "không tính được"
    if not price:
        return False, "thiếu giá"
    r = value / price
    if not V.RELIABLE_BAND[0] <= r <= V.RELIABLE_BAND[1]:
        return False, f"{r:.2f}× giá — ngoài [{V.RELIABLE_BAND[0]}; {V.RELIABLE_BAND[1]}]×"
    if anchor:
        ra = value / anchor
        if not V.IRRECONCILABLE[0] <= ra <= V.IRRECONCILABLE[1]:
            return False, f"{ra:.2f}× phép neo peer — ngoài [{V.IRRECONCILABLE[0]}; {V.IRRECONCILABLE[1]}]×"
    return True, f"{r:.2f}× giá"


def unpublished_reason(rows: list[dict], price: Optional[float]) -> str:
    """Vì sao không công bố — nói rõ CHIỀU khi mọi mô hình chính cùng lệch một phía (số vẫn không công bố).
    Rà soát 200 mã 10/11/2026: 52/66 mã không công bố là do mọi mô hình chính < 0,4× giá."""
    prim = [r for r in rows if r["role"] == "primary"]
    # giá trị bị chặn vì DỰ PHÓNG sai không dùng để nói chiều; lõi KCI tự gắn "ngoài vùng tin cậy" vẫn là số thật
    fc_blocked = [r for r in prim if "cổng dự phóng" in str(r.get("blocked") or "")]
    ratios = [r["value"] / price for r in prim
              if r.get("value") is not None and price and r not in fc_blocked]
    lo, hi = V.RELIABLE_BAND
    if ratios:
        if all(x < lo for x in ratios):
            return (f"mọi mô hình chính < {lo}× giá: thị giá đang vượt xa giá trị tính từ BCTC hiện có "
                    "(kỳ vọng tăng trưởng chưa vào số liệu)")
        if all(x > hi for x in ratios):
            return (f"mọi mô hình chính > {hi}× giá: chênh lệch quá lớn để tin — thường do dữ liệu/ngành đặc thù "
                    "(holding, tài chính), cần kiểm tra số liệu")
    if fc_blocked:
        return "dự phóng bị cổng số chặn và mô hình còn lại ngoài vùng tin cậy"
    if all(r.get("value") is None for r in prim):
        return "không mô hình chính nào tính được (thiếu lợi nhuận/vốn chủ dương hoặc thiếu dữ liệu)"
    return "không mô hình chính nào qua cổng số"


def reconcile(price: Optional[float], candidates: list[dict], anchor: Optional[float],
              caps: list[Optional[str]], anchor_fail: Optional[str] = None) -> dict:
    """candidates: [{name, value, role: 'primary'|'cross', blocked: str|None}].

    Mỗi ứng viên qua cổng số (_gate). Giá trị hợp lý = trung vị các mô hình CHÍNH qua cổng;
    khoảng = min–max của mọi ứng viên qua cổng. Không mô hình chính nào qua → không công bố.
    Độ tin cậy (siết 15/11/2026 — rà soát 200 mã: cả 4 mã High đều KHÔNG có phép neo peer; 46 mã công bố trên một mô
    hình chính trong khi mô hình kia bị chặn, lệch nhau trung vị 2,3×, mà 41 mã vẫn Medium):
      • High   : ≥ 2 mô hình chính qua cổng, cùng trong ±20% trung vị VÀ CÓ phép neo peer, giá trị hợp lý trong
                 [0,67; 1,5]× phép neo (hai mô hình cùng họ sổ sách đồng ý với nhau chưa đủ — cần một phép đo độc lập).
      • Medium : ≥ 2 mô hình chính qua cổng; hoặc chỉ 1 mô hình chính nhưng phép neo peer xác nhận (cùng dải trên).
      • Low    : chỉ 1 mô hình chính qua cổng và không có phép neo peer xác nhận.
    Rồi áp các trần (chất lượng dữ liệu, độ tin cậy lõi KCI).
    anchor_fail: bộ đối thủ lấy thiếu do lỗi nguồn — khi đó KHÔNG có phép neo thì không công bố (mất phép neo vì lỗi
    mạng không được làm cổng lỏng ra; rà soát 18/11/2026: Coway 7/12 đối thủ lỗi → 175.268 Medium thay vì 109.774)."""
    rows = []
    for c in candidates:
        if c.get("blocked"):
            ok, why = False, c["blocked"]
        else:
            ok, why = _gate(c.get("value"), price, anchor)
        rows.append({**c, "pass": ok, "why": why})
    prim = [r["value"] for r in rows if r["pass"] and r["role"] == "primary"]
    allv = [r["value"] for r in rows if r["pass"]]
    if prim and anchor is None and anchor_fail:
        return {"fair": None, "low": None, "high": None, "upside": None, "confidence": "Low",
                "status": f"⚪ không công bố — thiếu phép neo do lỗi nguồn ({anchor_fail}), bấm 🔄", "rows": rows}
    if not prim:
        return {"fair": None, "low": None, "high": None, "upside": None, "confidence": "Low",
                "status": "⚪ không công bố — " + unpublished_reason(rows, price), "rows": rows}
    fair = V._median(prim)
    # phép neo chỉ "xác nhận" khi có ít nhất một mô hình chính qua cổng KHÔNG nằm trong chính phép neo: lõi Holdco là
    # 'P/E vs median ngành' — một thành phần của trung vị phép neo → fair/anchor ≈ 1 luôn, tự xác nhận mình (rà soát
    # cuối 18/11/2026: GS 078930 lên Medium +140% chỉ nhờ lõi tự so với chính nó)
    indep = [r for r in rows if r["pass"] and r["role"] == "primary" and not r.get("in_anchor")]
    near_anchor = (bool(anchor) and bool(indep)
                   and HIGH_ANCHOR_BAND[0] <= fair / anchor <= HIGH_ANCHOR_BAND[1])
    if len(prim) >= 2 and all(abs(v / fair - 1) <= AGREE_BAND for v in prim) and near_anchor:
        conf = "High"
    elif len(prim) >= 2 or near_anchor:
        conf = "Medium"
    else:
        conf = "Low"
    for cap in caps:
        conf = V._cap_conf(conf, cap)
    up = (fair / price - 1) * 100 if price else None
    if up is None:
        verdict = "—"
    elif up >= 20:
        verdict = "Giá thấp hơn giá trị ước tính"
    elif up <= -15:
        verdict = "Giá cao hơn giá trị ước tính"
    else:
        verdict = "Giá gần giá trị ước tính"
    return {"fair": fair, "low": min(allv), "high": max(allv), "upside": up, "confidence": conf,
            "status": f"✓ {len(prim)} mô hình chính qua cổng", "verdict": verdict, "rows": rows}


# ============================================================================ Báo cáo
def _f0(x) -> str:
    return f"{x:,.0f}" if isinstance(x, (int, float)) and math.isfinite(x) else "—"


def report_markdown(ctx: dict) -> str:
    """Báo cáo tóm tắt (Markdown) từ ngữ cảnh trang. Chỉ đọc số đã tính, không tính lại."""
    idn, coc, rec, dq, fg = ctx["identify"], ctx["coc"], ctx["conclusion"], ctx["dq"], ctx["fgates"]
    L = [f"# Báo cáo định giá — {ctx['name']} ({ctx['ticker']})",
         f"_Ngày {ctx['date']} · KCI Pipeline giai đoạn 1 · số liệu Naver/DART, công thức deterministic_", ""]
    if ctx.get("edited"):          # số đã sửa tay ≠ con số chuẩn mà Tổng hợp / Sàng lọc / bot công bố
        L += [f"> ✏️ **Đã sửa tay:** {ctx['edited']} — khác giả định mặc định KCI"
              + (f" (mặc định KCI: {_f0(ctx['default_fair'])} KRW)" if ctx.get("default_fair") else "") + ".", ""]
    L += ["## Kết luận",
         f"- Giá hiện tại: **{_f0(ctx['price'])} KRW**",
         f"- Giá trị hợp lý: **{_f0(rec['fair'])} KRW**" + (
             f" (khoảng {_f0(rec['low'])} – {_f0(rec['high'])})" if rec["fair"] else ""),
         f"- Chênh lệch so với giá: **{rec['upside']:+.1f}%** — {rec.get('verdict', '')}"
         if rec.get("upside") is not None else "- Chênh lệch: —",
         f"- Độ tin cậy: **{rec['confidence']}** · Trạng thái: {rec['status']}", ""]
    ov = ctx.get("overall")
    if ov:
        L += ["## Đánh giá tổng quan", f"**{ov['verdict']}**", "",
              "| Trụ | Điểm |", "|---|---:|"]
        L += [f"| {k} | {'—' if v is None else f'{v:.0f}/100'} |" for k, v in ov["pillars"].items()]
        tot = "—" if ov["total"] is None else f"{ov['total']:.0f}/100"
        L += [f"| **Tổng** | **{tot}** |", ""]
    ps = ctx.get("peer_set")
    if ps:
        L += ["## Đối thủ trong & ngoài nước", "",
              "| Chỉ số | Mã | Trung vị trong nước | Trung vị nước ngoài | Nhận xét |", "|---|---:|---:|---:|---|"]
        for r in ps["position"]:
            def f2(x):
                return "—" if x is None else f"{x:,.2f}"
            L.append(f"| {r['Chỉ số']} | {f2(r['Mã'])} | {f2(r['Trung vị trong nước'])} | "
                     f"{f2(r['Trung vị nước ngoài'])} | {r['Nhận xét']} |")
        L += ["", "Trong nước: " + ", ".join(p["Tên"] or p["Mã"] for p in ps["domestic"]),
              "Nước ngoài: " + (", ".join(p["Tên"] or p["Mã"] for p in ps["foreign"]) or "—"), ""]
    dc = ctx.get("decomp")
    if dc:
        dp = dc["dupont"]

        def f1(x, suf=""):
            return "—" if x is None else f"{x:,.2f}{suf}"
        L += [f"## Bóc tách tài chính (năm {dc['year']})",
              f"- DuPont: biên LN {f1(dp['Biên LN ròng (cổ đông mẹ) %'], '%')} × vòng quay "
              f"{f1(dp['Vòng quay tài sản (lần)'])} × đòn bẩy {f1(dp['Đòn bẩy (TS/VCSH)'])} = ROE "
              f"{f1(dp['ROE = tích ba thành phần %'], '%')}",
              "- Cơ cấu LN trước thuế: " + " · ".join(
                  f"{r['Thành phần']} {f1(r['% LN trước thuế'], '%')}" for r in dc["structure"][:-1]),
              f"- CFO/LN ròng {f1(dc['cash']['CFO / LN ròng'])}× · biên FCF {f1(dc['cash']['Biên FCF %'], '%')} · "
              f"nợ ròng {f1(dc['balance']['Nợ ròng (억)'])}억", ""]
    dq_rows = ctx.get("decomp_q") or []
    if dq_rows:
        def fq(x, fmt):
            return "—" if x is None else fmt.format(x)
        L += [f"## Bóc tách theo quý ({dq_rows[0]['Quý']} → {dq_rows[-1]['Quý']})",
              "| Chỉ tiêu | " + " | ".join(r["Quý"] for r in dq_rows) + " |",
              "|---|" + "---:|" * len(dq_rows)]
        for k, fmt in (("Doanh thu (억)", "{:,.0f}"), ("DT YoY %", "{:+.1f}%"), ("LN HĐ (억)", "{:,.0f}"),
                       ("LN HĐ YoY %", "{:+.1f}%"), ("Biên LN HĐ %", "{:.1f}%"), ("Biên LN ròng %", "{:.1f}%"),
                       ("ROE năm hóa %", "{:.1f}%"), ("Khác / LNTT %", "{:.0f}%"), ("CFO / LN ròng", "{:.2f}×"),
                       ("FCF (억)", "{:,.0f}"), ("Nợ ròng (억)", "{:,.0f}")):
            L.append(f"| {k} | " + " | ".join(fq(r.get(k), fmt) for r in dq_rows) + " |")
        L += [""] + [f"- {x}" for x in (ctx.get("qflags") or [])] + [""]
    qu = ctx.get("quality")
    if qu:
        sc_q = "—" if qu["score"] is None else f"{qu['score']:.0f}"
        L += [f"## Chất lượng tài sản — hạng {qu['grade']} ({sc_q}/100)"]
        L += [f"- {c['Kết quả']} {c['Chỉ tiêu']}: {c['Giá trị']}" for c in qu["checks"]]
        L.append("")
    L += ["## 1. Nhận diện",
         f"- Ngành KRX: {idn.get('industry_kr') or '—'} → nhóm **{idn['bucket']}**"
         + (" (chu kỳ)" if idn["cyclical"] else ""),
         f"- Mô hình chính theo ngành: {idn['primary']} · đối chiếu: {', '.join(idn['cross'])}",
         f"- Ghi chú: {idn['note']}", "",
         f"## 2. Chất lượng dữ liệu — {dq['n_fail']} ❌ · {dq['n_warn']} ⚠️"
         + (f" → trần độ tin cậy **{dq['cap']}**" if dq["cap"] else ""), ""]
    L += [f"- {c['Kết quả']} {c['Kiểm tra']}: {c['Giá trị']}" + (f" — {c['Ghi chú']}" if c["Ghi chú"] else "")
          for c in dq["checks"]]
    L += ["", "## 4. Dự phóng", "",
          "| Năm | DT (억) | Tăng trưởng % | Biên LN ròng % | EPS | DPS | ROE % |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for f in ctx["forecast"]:
        L.append(f"| {f['Năm']} | {_f0(f['Doanh thu (억)'])} | {f['Tăng trưởng DT %']:+.1f} | "
                 f"{f['Biên LN ròng %']:.1f} | {_f0(f['EPS'])} | {_f0(f['DPS'])} | "
                 f"{f['ROE %']:.1f} |" if f.get("ROE %") is not None else
                 f"| {f['Năm']} | {_f0(f['Doanh thu (억)'])} | | | {_f0(f['EPS'])} | | |")
    L += ["", "Cổng dự phóng: " + ("✅ trong dải" if fg["ok"] else
                                   f"❌ {fg['n_fail']} vi phạm — mô hình dựa trên dự phóng bị loại khỏi kết luận")]
    L += ["", "## 5. Chi phí vốn",
          f"- Rf {coc['rf'] * 100:.2f}% · ERP {coc['erp'] * 100:.2f}% · β dùng {coc['beta_used']:.2f}"
          + (f" (hồi quy {coc['beta_raw']:.2f}, Blume)" if coc["beta_raw"] else ""),
          f"- **Ke {coc['ke'] * 100:.2f}%** · Kd {coc['kd'] * 100:.2f}% · g dài hạn {coc['g_term'] * 100:.1f}%"
          + (f" · WACC {coc['wacc'] * 100:.2f}%" if coc["wacc"] else ""), "",
          "## 6. Các mô hình và cổng số", "", "| Mô hình | Vai trò | Giá trị | Qua cổng | Lý do |",
          "|---|---|---:|---|---|"]
    for r in rec["rows"]:
        L.append(f"| {r['name']} | {'chính' if r['role'] == 'primary' else 'đối chiếu'} | {_f0(r.get('value'))} | "
                 f"{'✅' if r['pass'] else '❌'} | {r['why']} |")
    sp = ctx.get("special")
    if sp:
        L += ["", f"## Mô hình chuyên ngành — {sp['name']}", f"- {sp['summary']}" if sp.get("summary") else ""]
        L += [f"- {c['Kết quả']} {c['Kiểm tra']}: {c['Giá trị']}" + (f" — {c['Ghi chú']}" if c["Ghi chú"] else "")
              for c in sp.get("checks", [])]
    sc, se = ctx.get("scen"), ctx.get("sens")
    if sc:
        L += ["", "## 7. Kịch bản", "", "| Kịch bản | Xác suất | Δ tăng trưởng | × biên | Cổng dự phóng | Giá trị hợp lý |",
              "|---|---:|---:|---:|---|---:|"]
        for r in sc["rows"]:
            L.append(f"| {r['Kịch bản']} | {r['Xác suất']:.0%} | {r['Δ tăng trưởng (điểm %)']:+.1f} | "
                     f"{r['× biên LN']:.2f} | {r['Cổng dự phóng']} | {_f0(r['Giá trị hợp lý'])} |")
        L += ["", f"Giá trị kỳ vọng: **{_f0(sc['expected'])} KRW**" if sc["expected"] else
              "Giá trị kỳ vọng: không công bố (có kịch bản không qua cổng số)"]
    if se:
        gs = se["g_grid"]
        L += ["", "## 8. Độ nhạy Ke × g", "", "| Ke lệch | " + " | ".join(f"g {g * 100:.1f}%" for g in gs) + " |",
              "|---|" + "---:|" * len(gs)]
        for r in se["grid"]:
            L.append(f"| {r['ke_shift'] * 100:+.1f} điểm | " + " | ".join(
                _f0(r["values"][g]) if r["values"][g] is not None else "⚪" for g in gs) + " |")
    pm = ctx.get("peers") or {}
    L += ["", f"Phép neo peer: P/E trung vị {pm.get('pe') or '—'} ({pm.get('n_pe', 0)} mã), "
              f"P/B trung vị {pm.get('pb') or '—'} ({pm.get('n_pb', 0)} mã) → neo {_f0(ctx.get('anchor'))} KRW",
          "", "_Đây là ước lượng có kỷ luật cho nghiên cứu, không phải khuyến nghị đầu tư._"]
    return "\n".join(L)


# ============================================================================ GIAI ĐOẠN 2
# Toàn bộ bước 6 + kết luận gom vào evaluate() để kịch bản / độ nhạy dùng CHUNG công thức và cổng số với kết
# luận chính — không có đường tắt nào cho một kịch bản né cổng.
from dataclasses import dataclass as _dc   # noqa: E402
import csv as _csv                          # noqa: E402
import html as _html                        # noqa: E402
import json as _json                        # noqa: E402
import os as _os                            # noqa: E402


@_dc
class ShiftedAssumptions(V.Assumptions):
    """Assumptions với Ke dịch thêm ke_shift (cho bảng độ nhạy). Rf/g cap giữ nguyên."""
    ke_shift: float = 0.0

    def ke(self) -> float:
        return super().ke() + self.ke_shift

    def ke_stock(self, beta_raw: Optional[float]) -> float:
        return super().ke_stock(beta_raw) + self.ke_shift


def evaluate(D: dict, a: V.Assumptions, drivers: list[dict], idn: Optional[dict] = None,
             dq: Optional[dict] = None, evidence: Optional[dict] = None) -> dict:
    """Bước 4–6 + kết luận cho 1 bộ giả định. D = {row (có beta), series, quarters, extras, peers, ticker}."""
    row, series = D["row"], D["series"]
    extras = D.get("extras") or {}
    reals = _reals(series)
    idn = idn or identify(row)
    price, mc = _num(row.get("price")), _num(row.get("market_cap"))
    coc = cost_of_capital(a, _num(row.get("beta")), mc, extras, idn)
    ke, w = coc["ke"], a.persistence
    pm = peer_medians(D.get("peers") or [], exclude=D.get("ticker"))
    if reals and reals[-1].get("_period_months"):
        # kỳ ngắn (I08): EPS của mã đã là 12 tháng nhưng PER Naver của đối thủ (REIT) là PER kỳ 6/3 tháng → nhân
        # chéo ra gấp 2–4 lần. Quy PER đối thủ về 12 tháng (PER kỳ × m/12, coi đối thủ cùng độ dài kỳ) thay vì bỏ P/E:
        # bỏ P/E thì phép neo chỉ còn P/B (< 2 phương pháp) → anchor None → cổng [0,25; 4]× phép neo tắt lặng lẽ cho cả
        # nhóm REIT kỳ ngắn (rà soát cuối 18/11/2026). EPS 12 tháng × PER kỳ × m/12 = EPS kỳ × PER kỳ (cùng kỳ).
        _m = reals[-1]["_period_months"]
        _pe = _num(pm.get("pe"))
        pm = {**pm, "pe": (_pe * _m / 12.0) if (_pe and 0 < _m < 12) else None,
              "pe_note": f"PER đối thủ theo kỳ {_m} tháng — quy về 12 tháng (× {_m}/12)"}
    fc = run_forecast(series, row, drivers, D.get("quarters"))
    fg = forecast_gates(series, fc, financial=idn["financial"], evidence=evidence, quarters=D.get("quarters"))
    per_share = []                 # cổng số của cơ sở mỗi cổ phiếu (data_quality) → chặn mô hình dựa trên dự phóng
    if (dq or {}).get("share_break"):
        _sw = (dq or {}).get("share_break_why")
        per_share.append(_check(_sw, "không có số liệu", FAIL, "EPS/BPS dự phóng không có gốc số mỗi cổ phiếu")
                         if _sw else
                         _check("Số cổ phiếu", "đổi mạnh so với BCTC năm", FAIL,
                                "EPS/BPS dự phóng không cùng gốc số cổ phiếu hiện tại"))
    if (dq or {}).get("parent_break"):
        per_share.append(_check("LN cổ đông mẹ ÷ LN hợp nhất", "không suy được từ số liệu", FAIL,
                                "LN dự phóng (hợp nhất) không đổi được sang EPS"))
    if (dq or {}).get("source_break"):
        per_share.append(_check("Nguồn số liệu", "DART ≠ Naver ở năm gần nhất", FAIL,
                                "Doanh thu/LN của hai nguồn không khớp — dự phóng không có gốc tin cậy"))
    if (dq or {}).get("period_break"):
        per_share.append(_check("Độ dài kỳ kế toán", "không đủ 12 tháng gần nhất", FAIL,
                                "LN/EPS gốc chỉ là một phần năm (kỳ 6/3 tháng) — dự phóng không có gốc 12 tháng"))
    if per_share and fc:
        fg = {**fg, "ok": False, "n_fail": fg["n_fail"] + len(per_share),
              "checks": [c for c in fg["checks"] if c["Kết quả"] != OK] + per_share}
    r_fwd = dict(row)
    if fc and fg["ok"]:          # dự phóng bị cổng chặn không được nuôi P/E forward → phép neo peer (rà soát 10/11)
        r_fwd["fwd_eps"] = fc[0]["EPS"]
        r_fwd["fwd_roe"] = fc[0]["EPS"] / fc[0]["BPS đầu kỳ"] if fc[0]["BPS đầu kỳ"] else None
    base = V.value_stock(r_fwd, a, pm["pe"], pm["pb"], history=reals or None)
    # cùng phép neo mà value_stock dùng để tam giác hóa / chặn >4× (rà soát 18/11/2026: lõi rơi về mid-cycle ở DN
    # không chu kỳ thì hai nơi từng dùng hai phép neo khác nhau)
    anchor = peer_anchor(base.methods, base.anchor_basis == "mid")
    if anchor is None and reals and reals[-1].get("_period_months") and base.methods.get("P/B vs median ngành"):
        # kỳ ngắn mà P/E đối thủ không dùng được: P/B đơn lẻ làm phép neo cho cổng [0,25; 4]× — cổng không được tắt
        anchor = base.methods["P/B vs median ngành"]
    rim = rim_explicit(fc[0]["BPS đầu kỳ"] if fc else None, fc, ke, w)   # B0 cùng gốc với dự phóng (cuối năm tài chính)
    ddm = ddm_explicit(fc, ke, coc["g_term"])
    fc_block = (None if fg["ok"] else f"cổng dự phóng: {fg['n_fail']} vi phạm" if fc
                else no_forecast_reason(series, drivers))
    cands = [
        # tên gắn trạng thái tam giác hóa: lõi đổi sang 50/50 với peer = một mô hình KHÁC (fair_same_set)
        {"name": f"Lõi KCI — {base.auto_method}" + (" · △ 50/50 peer" if base.triangulated else ""),
         "value": base.fair_raw, "role": "primary", "blocked": None if base.reliable else base.status,
         "in_anchor": base.auto_method in V._RELATIVE_METHODS},
        {"name": f"RIM tường minh ({len(fc)} năm dự phóng)", "value": rim["value"] if rim else None,
         "role": "primary", "blocked": fc_block},
        {"name": f"DDM tường minh ({len(fc)} năm)", "value": ddm["value"] if ddm else None,
         "role": "primary" if idn["dividend_model"] else "cross", "blocked": fc_block},
        {"name": "P/E × trung vị peer", "value": base.methods.get("P/E vs median ngành"), "role": "cross"},
        {"name": "P/B × trung vị peer", "value": base.methods.get("P/B vs median ngành"), "role": "cross"},
    ]
    fs = D.get("fs_full")
    # EV → vốn chủ MỖI CỔ PHIẾU: chia cho số cp cơ sở EPS mới nhất (LN cổ đông MẸ ÷ EPS — gồm cổ phiếu ưu đãi, không gồm
    # phần cổ đông thiểu số mà bridge đã trừ). Bản 10/11/2026 chia cho vốn hóa ÷ giá = chỉ cổ phiếu THƯỜNG → giá trị/cp
    # cao hơn thực ở mã có cổ phiếu ưu đãi (Samsung +15%, Hyundai Motor +31%) — rà soát 15/11/2026
    shares = (share_basis(reals, D.get("quarters"), mc, price)["shares"] if reals else None) or (
        (mc / price) if (mc and price) else None)
    fcff_m = evm = None
    if fs and not idn["financial"] and fc and shares and mc:
        import dcf as _dcf
        fcff_m = _dcf.fcff_dcf(fc, fs, ke, a.rf, coc["g_term"], mc, shares)
        evm = _dcf.ev_ebit_ttm(_dcf.ttm_ebit(D.get("quarters")), fs, D.get("peer_ev") or [], shares)
        if fcff_m:
            # ĐỐI CHIẾU, không vào trung vị kết luận: backtest point-in-time 2021–25 (78 mã) — đưa FCFF-DCF làm mô
            # hình chính kéo IC kết luận từ +0,106 (t 3,2) xuống +0,033 (t 0,5); FCFF riêng IC −0,058. Bảng tính vẫn
            # hiện đầy đủ để đọc cấu trúc dòng tiền.
            cands.append({"name": f"FCFF-DCF ({len(fc)} năm dự phóng)", "value": fcff_m["value"],
                          "role": "cross", "blocked": fc_block})
        if evm:
            cands.append({"name": f"EV/EBIT 4 quý × trung vị {evm['n']} đối thủ", "value": evm["value"],
                          "role": "cross"})
    # FCFF dự phòng từ số DART phụ: chỉ khi số đó là KRW, BCTC đầy đủ không bị loại và không phải ngành tài chính
    # (CFO ngân hàng gồm dòng tiền gửi/cho vay — từng ra 1,52× giá, qua cổng thành cận dưới khoảng)
    if (not fcff_m and extras.get("cfo") is not None and mc and price and not idn["financial"]
            and str(extras.get("currency") or "KRW") == "KRW" and not D.get("fs_note")):
        fcff = V.fcff_value(extras.get("cfo"), extras.get("capex"), extras.get("debt"), extras.get("net_debt"),
                            shares or mc / price, ke, coc["kd"], coc["tax"], a.g_high, a.years_high, coc["g_term"],
                            mc / 1e8)
        cands.append({"name": "FCFF-DCF (DART, tăng trưởng phẳng)", "value": fcff, "role": "cross"})
    # mô hình chuyên ngành (sector_models): hàm(coc) → ứng viên, để độ nhạy Ke dịch cả rNPV / EV-GWh
    for fn in D.get("extra") or []:
        c = fn(coc)
        if c:
            cands.append(c)
    caps = [(dq or {}).get("cap"), None if idn["data_ok"] else "Medium"]
    rec = reconcile(price, cands, anchor, caps, anchor_fail=peer_failures(D.get("peers"), D.get("peer_errors")))
    fc_model = ddm if idn["dividend_model"] else rim
    return {"fc": fc, "fg": fg, "base": base, "anchor": anchor, "rim": rim, "ddm": ddm, "rec": rec,
            "fcff": fcff_m, "ev_ebit": evm, "shares": shares,
            "coc": coc, "pm": pm, "fc_model": fc_model["value"] if fc_model else None,
            "fc_model_name": "DDM tường minh" if idn["dividend_model"] else "RIM tường minh"}


# ---------------------------------------------------------------------------- MỘT đường định giá cho mọi trang
# Rà soát 15/11/2026: cùng một mã, cùng lúc, mỗi trang một giá trị hợp lý (Samsung ở giá 209.000: trang 🧮 183.820,
# bảng Định giá tổng hợp 147.796, Sàng lọc/bot 119.277) vì mỗi trang tự dựng bộ đối thủ và dữ liệu phụ riêng. Nay
# mọi trang gọi prepare() → evaluate() với CÙNG bộ đối thủ chuẩn (peers.domestic_candidates: gợi ý Naver ∪ top vốn
# hóa cùng ngành KRX, PEER_N mã) — khác nhau chỉ còn khi người dùng tự sửa giả định ở trang 🧮.
PEER_N = 12          # số đối thủ trong nước của bộ chuẩn
FC_YEARS_MAX = 5     # số năm dự phóng tối đa


def fs_mismatch(fs_full: Optional[dict], series: list[dict]) -> Optional[str]:
    """BCTC đầy đủ DART (dùng cho FCFF-DCF, EV/EBIT) có cùng đơn vị và cùng phạm vi với chuỗi năm không.
    Trả LÝ DO không dùng được, hoặc None nếu dùng được. Kiểm bằng số: đơn vị tiền phải là KRW; doanh thu cùng năm
    phải trong ±10% so với chuỗi (Doosan Bobcat nộp USD: 0,07%; SK Square: 13,5% do Naver tính cả lãi liên kết)."""
    if not fs_full:
        return None
    cur = str(fs_full.get("currency") or "KRW")
    if cur != "KRW":
        return f"BCTC đầy đủ DART lập bằng {cur}, không cùng đơn vị với vốn hóa (KRW)"
    reals = _reals(series)
    src = (reals[-1].get("_src") if reals else None) or {}
    if src.get("dart") in ("currency", "mismatch"):
        return f"DART không khớp chuỗi năm ({src.get('note')})"
    y = str(fs_full.get("year"))
    y_base = str(reals[-1].get("period", ""))[:4] if reals else None
    if y_base and fs_full.get("year") is not None and y != y_base:      # năm cũ hơn (năm mới chưa nộp / DART lỗi) → FCFF, WACC, cầu nối lệch kỳ dự phóng
        return f"BCTC đầy đủ là năm {y}, dự phóng xuất phát từ năm {y_base}"
    c = next((x for x in reals if str(x.get("period", ""))[:4] == y), None)
    r_fs, r_ser = _num((fs_full.get("cur") or {}).get("revenue")), _num(c.get("revenue")) if c else None
    if r_fs and r_ser and r_ser > 0 and not 0.9 <= r_fs / r_ser <= 1.1:
        return f"doanh thu {y} của BCTC đầy đủ {r_fs:,.0f}억 ≠ chuỗi năm {r_ser:,.0f}억"
    return None


def prepare(tk: str, row: dict, fin: Optional[dict], a: V.Assumptions, peers: Optional[list[dict]],
            beta: Optional[float], *, series: Optional[list[dict]] = None, extras: Optional[dict] = None,
            fs_full: Optional[dict] = None, peer_ev: Optional[list] = None, extra: Optional[list] = None,
            years: Optional[int] = None, feeds: Optional[dict] = None, today: Optional[_dt.date] = None,
            peer_errors: Optional[list] = None, dart_quarters: Optional[list[dict]] = None,
            src_info: Optional[dict] = None) -> dict:
    """Dựng đầu vào định giá của 1 mã theo MỘT cách duy nhất.

    row: dòng thị trường mới (giá, vốn hóa, EPS, BPS…); fin: {'annual', 'quarter'} của Naver; peers: dòng thị trường
    của bộ đối thủ chuẩn; beta: beta 52 tuần (None = không lấy được). series: chuỗi năm đã dựng sẵn (không truyền thì
    dựng từ fin + DART). peer_errors: lỗi khi lấy DANH SÁCH đối thủ (trang 🧮). dart_quarters: quý DART đã nộp
    (analysis.quarter_series) — quý mới hơn quý thực cuối của Naver được ghép vào (financials.merge_dart_quarters, I05).
    src_info: {rf_asof, beta_weeks} của trang gọi → ô 'Nguồn và độ mới dữ liệu' (dq['source_line'], C8).
    Trả {row, series, quarters, idn, dq, evidence, drivers (mặc định), basis, D, fs_note, dart_q}."""
    from financials import build_annual_series, merge_dart_quarters
    fin = fin or {}
    row = dict(row)
    row["beta"] = beta
    quarters = fin.get("quarter") or []
    dq_info = {"state": "none", "added": [], "note": ""}
    idn = identify(row)
    # ngành tài chính: 'doanh thu' quý DART (thu lãi) khác định nghĩa Naver → không đối chiếu được, không ghép, không ⚠️
    # (như fs_mismatch); KB금융 Q3.25 DART 71.950 vs Naver 149.661억
    if dart_quarters and not idn["financial"]:
        ann = _reals(series) if series is not None else _reals(fin.get("annual") or [])
        if not ann or str(ann[-1].get("period", ""))[4:6] == "12":     # quý DART theo năm dương lịch
            quarters, dq_info = merge_dart_quarters(quarters, dart_quarters)
    if series is None:
        series = build_annual_series(tk, fin.get("annual") or [], row.get("market_cap"), row.get("price"),
                                     years=6, quarters=quarters)
    elif dq_info["state"] == "merged":           # chuỗi dựng sẵn với quý Naver → cột dự phóng tính lại theo quý đã ghép
        r0 = _reals(series)
        fc0 = project_forecast(r0, _num(row.get("market_cap")), _num(row.get("price")), quarters=quarters)
        series = r0 + ([fc0] if fc0 else [])
    # ngành tài chính không dùng FCFF/EV-EBIT (và 'doanh thu' DART của họ là thu lãi, khác định nghĩa Naver) → không xét
    fs_note = None if idn["financial"] else fs_mismatch(fs_full, series)
    feeds = dict(feeds or {})
    if dq_info["state"] not in ("none", "merged"):
        feeds["Quý DART mới hơn Naver"] = {"ok": False, "level": WARN, "value": dq_info["note"],
                                           "note": "DART đã có quý mới hơn Naver nhưng không ghép được — dự phóng năm 1 "
                                                   "chưa tính quý này"}
    if fs_note:
        feeds["BCTC đầy đủ DART (FCFF, EV/EBIT)"] = {"ok": False, "level": WARN, "no_cap": True,
                                                    "note": fs_note + " → không tính FCFF-DCF / EV-EBIT"}
    # Số DART phụ (nợ vay, tiền ròng, CFO): đọc cùng báo cáo với BCTC đầy đủ → không phải KRW hoặc khác năm gốc dự
    # phóng thì KHÔNG dùng (WACC, FCFF dự phòng, ô tiền ròng rNPV / EV-GWh) — rà soát 18/11/2026
    extras = dict(extras or {})
    reals0 = _reals(series)
    y_base = str(reals0[-1].get("period", ""))[:4] if reals0 else None
    ex_cur = str(extras.get("currency") or "KRW")
    ex_cur_bad = bool(extras) and ex_cur != "KRW"
    ex_year_bad = bool(extras) and extras.get("year") is not None and bool(y_base) and str(extras.get("year")) != y_base
    ex_note = (f"BCTC DART lập bằng {ex_cur}, không quy đổi" if ex_cur_bad else
               f"số DART là năm {extras.get('year')}, dự phóng xuất phát từ năm {y_base}" if ex_year_bad else None)
    if ex_note:
        # cùng MỘT nguyên nhân với BCTC đầy đủ (cùng báo cáo USD / cùng năm cũ) → 1 dòng ⚠️, không đếm 2 lần (≥2 ⚠️ là
        # trần Medium: tháng 1–3 Naver có cột sơ bộ năm mới, DART còn năm cũ → mọi mã bị hạ chỉ vì lịch nộp)
        fs = fs_full or {}
        same = fs_note and ((ex_cur_bad and str(fs.get("currency") or "KRW") == ex_cur) or
                            (not ex_cur_bad and str(fs.get("year")) == str(extras.get("year"))
                             and fs_note.startswith("BCTC đầy đủ là năm")))
        extras = {}
        if same:
            f0 = feeds["BCTC đầy đủ DART (FCFF, EV/EBIT)"]
            f0["note"] = f0["note"] + ", WACC / FCFF dự phòng (số DART phụ cùng báo cáo)"
        else:
            feeds["Số DART phụ (WACC, tiền ròng)"] = {"ok": False, "level": WARN, "no_cap": True,
                                                      "note": ex_note + " → không tính WACC / FCFF dự phòng"}
    # BCTC Naver (EPS, BPS, LN cổ đông mẹ) lỗi mạng hoặc không có năm thực nào có EPS/LN cổ đông mẹ: số mỗi cổ phiếu
    # lùi về vốn hóa ÷ giá (chỉ cổ phiếu thường), tỷ lệ cổ đông mẹ = 1 → ❌, trần Low, chặn mô hình theo mỗi cổ phiếu
    n_err = (fin.get("_error") if isinstance(fin, dict) else None) or {}
    if n_err or not any(c.get("eps") is not None or c.get("ctrl_net") is not None for c in reals0):
        feeds["BCTC Naver (EPS/BPS/LN cổ đông mẹ)"] = {
            "ok": False, "share_break": True,
            "value": ("lỗi " + ", ".join(f"{k}: {v}" for k, v in n_err.items())) if n_err else "không có",
            "note": ("Naver không trả BCTC (lỗi mạng) — " if n_err else "Không năm thực nào có EPS / LN cổ đông mẹ — ")
                    + "số cp và tỷ lệ cổ đông mẹ không suy được; chặn mô hình theo mỗi cổ phiếu. Bấm 🔄 để lấy lại"}
    pfail = peer_failures(peers, peer_errors)
    if pfail:           # bộ đối thủ thiếu do lỗi nguồn → ❌, trần Low (phép neo đang tính trên bộ không đầy đủ)
        feeds["Bộ đối thủ"] = {"ok": False, "note": pfail + " — bấm 🔄 để lấy lại"}
    # Kỳ kế toán ngắn (REIT — I08): EPS / PER của dòng thị trường là của MỘT kỳ 6/3 tháng (PER 43,9 thay vì 24,0) trong
    # khi cổ tức là 12 tháng → thay bằng EPS 12 tháng gần nhất của chuỗi đã năm hóa (financials.annualize_short)
    last_r = (_reals(series) or [{}])[-1]
    if last_r.get("_period_months") and _num(last_r.get("eps")) is not None:
        px = _num(row.get("price"))
        row["_eps_period"], row["_per_period"] = row.get("eps"), row.get("per")
        row["eps"] = last_r["eps"]
        row["per"] = round(px / last_r["eps"], 2) if (px and last_r["eps"] > 0) else None
        _ni = _num(last_r.get("ctrl_net"))
        _ni = _ni if _ni is not None else _num(last_r.get("net_profit"))
        if _ni is not None:            # LN 12 tháng (억 → KRW như market_cap ÷ PER), kể cả khi lỗ (PER None)
            row["net_income"] = _ni * 1e8
        elif row["per"] and _num(row.get("market_cap")):
            row["net_income"] = _num(row["market_cap"]) / row["per"]
        else:
            row["net_income"] = None
    sources = _source_info(fin, series, quarters, dart_quarters, peers, src_info)
    dq = data_quality(row, series, quarters, beta, today=today, feeds=feeds, sources=sources)
    evidence = reported_evidence(series, quarters)
    n = min(years or a.years_high, FC_YEARS_MAX)
    drivers = default_drivers(series, row, a, years=n, quarters=quarters, cyclical=idn["cyclical"],
                              financial=idn["financial"])
    basis = drivers[0].pop("_basis", "") if drivers else ""
    D = {"row": row, "series": series, "quarters": quarters, "extras": extras, "peers": peers or [],
         "ticker": tk, "fs_full": None if fs_note else fs_full, "fs_note": fs_note, "peer_ev": peer_ev or [],
         "extra": extra if extra is not None else [], "peer_errors": list(peer_errors or [])}
    return {"row": row, "series": series, "quarters": quarters, "idn": idn, "dq": dq, "evidence": evidence,
            "drivers": drivers, "basis": basis, "D": D, "fs_note": fs_note, "dart_q": dq_info}


def cached_dart_quarters(tk: str) -> list[dict]:
    """Quý DART ĐÃ LƯU trên đĩa (không gọi DART) cho prepare(dart_quarters=) ở Sàng lọc / bot / Tổng hợp. Lỗi → []."""
    try:
        import analysis as AN
        return AN.quarter_series(AN.fetch_quarter_reports(tk, offline=True))
    except Exception:
        return []


def assess(tk: str, row: dict, fin: Optional[dict], a: V.Assumptions, peers: Optional[list[dict]],
           beta: Optional[float], **kw) -> dict:
    """prepare() + evaluate() với giả định mặc định — con số mà bảng tổng hợp, Sàng lọc và bot công bố."""
    ctx = prepare(tk, row, fin, a, peers, beta, **kw)
    ctx["E"] = evaluate(ctx["D"], a, ctx["drivers"], ctx["idn"], ctx["dq"], ctx["evidence"])
    ctx["rec"] = ctx["E"]["rec"]
    return ctx


def fair_same_set(rec: dict, names: list[str]) -> Optional[float]:
    """Trung vị đúng bộ mô hình chính `names` (bộ đã qua cổng ở trường hợp gốc). Có mô hình nào trong bộ không
    qua cổng ở ô này → None. Giữ nguyên bộ mô hình để kịch bản/độ nhạy đo tác động của GIẢ ĐỊNH, không phải
    tác động của việc một mô hình rơi ra/vào cổng (Ke +0,5 điểm làm lõi tụt dưới 0,4× giá → trung vị đổi
    sang bộ khác và giá trị lại TĂNG — sai chiều)."""
    if not names:
        return None
    by = {r["name"]: r for r in rec["rows"]}
    vals = [by[n]["value"] for n in names if n in by and by[n]["pass"]]
    return V._median(vals) if len(vals) == len(names) else None


def primary_set(rec: dict) -> list[str]:
    return [r["name"] for r in rec["rows"] if r["pass"] and r["role"] == "primary"]


# ---------------------------------------------------------------------------- Bước 7a — kịch bản
SCENARIOS = {   # dịch tăng trưởng (điểm %/năm), nhân biên LN ròng, xác suất
    "Bear": {"dg": -3.0, "mm": 0.85, "p": 0.25},
    "Base": {"dg": 0.0, "mm": 1.00, "p": 0.50},
    "Bull": {"dg": 3.0, "mm": 1.15, "p": 0.25},
}


SCEN_MM_BAND = (0.0, 2.0)       # × biên LN của kịch bản
SCEN_PSUM_TOL = 0.01            # |Σ xác suất − 1| tối đa


def shift_drivers(drivers: list[dict], dg: float, mm: float, keep_year1: bool = False) -> list[dict]:
    """Dịch giả định: tăng trưởng + dg điểm %, biên × mm. keep_year1: năm 1 đã có ≥2 quý thực → giữ nguyên
    (số đã công bố không phải giả định để lạc quan/bi quan)."""
    dg, mm = _num(dg), _num(mm)
    if mm is not None:
        mm = min(max(mm, SCEN_MM_BAND[0]), SCEN_MM_BAND[1])      # > 2 từng lật biên âm thành dương
    out = []
    for i, d in enumerate(drivers):
        d2 = dict(d)
        if not (keep_year1 and i == 0):
            # ô trống (kịch bản hoặc giả định) → None → run_forecast đánh dấu, cổng chặn; không coi là 0 / 1
            g0, m = _num(d.get("Tăng trưởng DT %")), _num(d.get("Biên LN ròng %"))
            d2["Tăng trưởng DT %"] = g0 + dg if (g0 is not None and dg is not None) else None
            # biên âm: kịch bản xấu là lỗ sâu hơn, tốt là lỗ ít hơn
            d2["Biên LN ròng %"] = (None if (m is None or mm is None) else m * mm if m >= 0 else m * (2 - mm))
        out.append(d2)
    return out


def scenarios(D: dict, a: V.Assumptions, drivers: list[dict], idn: dict, dq: dict,
              evidence: Optional[dict], spec: Optional[dict] = None) -> dict:
    """Chạy evaluate() cho Bear/Base/Bull. Giá trị kỳ vọng Σp·fair chỉ công bố khi CẢ BA kịch bản đều có
    giá trị hợp lý qua cổng (một kịch bản bị chặn = phân phối không đầy đủ, không bịa số thay).
    Giá trị mỗi kịch bản = trung vị CÙNG bộ mô hình chính đã qua cổng ở giả định gốc (fair_same_set)."""
    spec = spec or SCENARIOS
    names = primary_set(evaluate(D, a, drivers, idn, dq, evidence)["rec"])
    # không mô hình chính nào dùng dự phóng (RIM/DDM bị cổng chặn) → kịch bản không đổi gì, Σp·fair vô nghĩa
    informative = any(n.startswith(("RIM tường minh", "DDM tường minh")) for n in names)
    rows = []
    for nm, s in spec.items():
        drv = shift_drivers(drivers, s["dg"], s["mm"], keep_year1=bool(evidence))
        ev = evaluate(D, a, drv, idn, dq, evidence)
        rows.append({"Kịch bản": nm, "Xác suất": s["p"], "Δ tăng trưởng (điểm %)": s["dg"],
                     "× biên LN": s["mm"], "Cổng dự phóng": "✅" if ev["fg"]["ok"] else f"❌ {ev['fg']['n_fail']}",
                     ev["fc_model_name"]: ev["fc_model"], "Giá trị hợp lý": fair_same_set(ev["rec"], names),
                     "Độ tin cậy": ev["rec"]["confidence"]})
    fairs = [r["Giá trị hợp lý"] for r in rows]
    ps = [_num(r["Xác suất"]) for r in rows]
    p_note = None
    if any(p is None or p < 0 for p in ps):
        p_note = "có xác suất trống / âm / không phải số"
    elif abs(sum(ps) - 1) > SCEN_PSUM_TOL:
        p_note = f"Σ xác suất = {sum(ps):g} ≠ 1 (nhập 0,25 chứ không phải 25)"
    ev_fair = (sum(p * f for p, f in zip(ps, fairs)) / sum(ps)
               if informative and p_note is None and all(f is not None for f in fairs) else None)
    if not informative:
        fairs = []
    return {"rows": rows, "expected": ev_fair, "informative": informative, "p_note": p_note,
            "low": min((f for f in fairs if f), default=None), "high": max((f for f in fairs if f), default=None)}


# ---------------------------------------------------------------------------- Bước 7b — độ nhạy
KE_SHIFTS = (-0.01, -0.005, 0.0, 0.005, 0.01)
G_GRID = (0.010, 0.015, 0.020, 0.025)


def sensitivity(D: dict, a: V.Assumptions, drivers: list[dict], idn: dict, dq: dict,
                evidence: Optional[dict], ke_shifts=KE_SHIFTS, g_grid=G_GRID) -> dict:
    """Giá trị hợp lý theo lưới Ke ± Δ × g dài hạn, cùng bộ mô hình chính của trường hợp gốc (fair_same_set).
    None = có mô hình trong bộ không qua cổng ở ô đó."""
    from dataclasses import asdict
    base_kw = {k: v for k, v in asdict(a).items() if k in V.Assumptions.__dataclass_fields__}
    names = primary_set(evaluate(D, a, drivers, idn, dq, evidence)["rec"])
    grid = []
    for s in ke_shifts:
        line = {}
        for g in g_grid:
            a2 = ShiftedAssumptions(**{**base_kw, "g_terminal": g}, ke_shift=s)
            line[g] = fair_same_set(evaluate(D, a2, drivers, idn, dq, evidence)["rec"], names)
        grid.append({"ke_shift": s, "values": line})
    vals = [v for r in grid for v in r["values"].values() if v is not None]
    n_all = len(ke_shifts) * len(g_grid)
    return {"grid": grid, "g_grid": list(g_grid), "low": min(vals, default=None),
            "high": max(vals, default=None), "n_ok": len(vals), "n_all": n_all}


# ---------------------------------------------------------------------------- Bước 8 — football field
def football_field(row: dict, rec: dict, scen: Optional[dict], sens: Optional[dict]) -> list[dict]:
    """Các dải để vẽ: [{Nhóm, Dải, low, high}]. Chỉ đưa mô hình ĐÃ qua cổng."""
    out = []
    lo, hi = _num(row.get("low_52w")), _num(row.get("high_52w"))
    if lo and hi:
        out.append({"Nhóm": "Thị trường", "Dải": "Giá 52 tuần", "low": lo, "high": hi})
    for r in rec.get("rows", []):
        if r["pass"] and r.get("value"):
            out.append({"Nhóm": "Mô hình (qua cổng)", "Dải": r["name"], "low": r["value"], "high": r["value"]})
    if scen and scen.get("low") and scen.get("high"):
        out.append({"Nhóm": "Kịch bản", "Dải": "Bear – Bull", "low": scen["low"], "high": scen["high"]})
    if sens and sens.get("low") and sens.get("high"):
        out.append({"Nhóm": "Độ nhạy", "Dải": "Ke ±1 điểm × g 1–2,5%", "low": sens["low"], "high": sens["high"]})
    if rec.get("fair"):
        out.append({"Nhóm": "Kết luận", "Dải": "Khoảng giá trị hợp lý", "low": rec["low"], "high": rec["high"]})
    return out


# ---------------------------------------------------------------------------- Bước 9 — nhật ký định giá
LOG_FIELDS = ["Thời điểm", "Mã", "Tên", "Giá", "Giá trị hợp lý", "Thấp", "Cao", "Chênh %", "Độ tin cậy",
              "Trạng thái", "Kỳ vọng kịch bản", "Ke %", "ERP %", "g %", "Số năm", "Giả định", "Đã sửa tay", "Kịch bản"]
LOG_MAX_ROWS = 5000              # quá trần → đổi tên thành <tên>.1.csv rồi bắt đầu file mới
LOG_MAX_BYTES = 2_000_000


def log_entry(ctx: dict, drivers: list[dict], a: V.Assumptions, scen: Optional[dict]) -> dict:
    rec, coc = ctx["conclusion"], ctx["coc"]

    def r0(x):
        return round(x) if isinstance(x, (int, float)) and math.isfinite(x) else ""
    return {"Thời điểm": _G.now_kst_naive().isoformat(timespec="seconds"), "Mã": ctx["ticker"],
            "Tên": ctx["name"], "Giá": r0(ctx["price"]), "Giá trị hợp lý": r0(rec["fair"]),
            "Thấp": r0(rec["low"]), "Cao": r0(rec["high"]),
            "Chênh %": round(rec["upside"], 1) if rec["upside"] is not None else "",
            "Độ tin cậy": rec["confidence"], "Trạng thái": rec["status"],
            "Kỳ vọng kịch bản": r0((scen or {}).get("expected")), "Ke %": round(coc["ke"] * 100, 2),
            "ERP %": round(a.erp * 100, 2), "g %": round(coc["g_term"] * 100, 2), "Số năm": len(drivers),
            "Giả định": _json.dumps([{k: d[k] for k in ("Năm", "Tăng trưởng DT %", "Biên LN ròng %",
                                                         "Tỷ lệ cổ tức %") if k in d} for d in drivers],
                                    ensure_ascii=False),
            "Đã sửa tay": ctx.get("edited") or "",
            "Kịch bản": _json.dumps([{"Kịch bản": r.get("Kịch bản"), "dg": r.get("Δ tăng trưởng (điểm %)"),
                                      "mm": r.get("× biên LN"), "p": r.get("Xác suất")}
                                     for r in (scen or {}).get("rows", [])], ensure_ascii=False) if scen else ""}


def log_append(path: str, entry: dict) -> None:
    """Ghi thêm 1 dòng. File quá LOG_MAX_ROWS dòng / LOG_MAX_BYTES → đổi tên <tên>.1.csv (ghi đè bản cũ) rồi bắt đầu
    file mới; file cũ thiếu cột (trước 18/11/2026) → viết lại với đủ cột để dòng mới không lệch cột."""
    if _os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            head = next(_csv.reader(f), None)
            n = sum(1 for _ in f)
        if n >= LOG_MAX_ROWS or _os.path.getsize(path) >= LOG_MAX_BYTES:
            root, ext = _os.path.splitext(path)
            _os.replace(path, f"{root}.1{ext}")
        elif head != LOG_FIELDS:
            rows = log_read(path)
            tmp = f"{path}.tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(f, fieldnames=LOG_FIELDS)
                w.writeheader()
                w.writerows({k: r.get(k) or "" for k in LOG_FIELDS} for r in rows)
            _os.replace(tmp, path)
    new = not _os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: entry.get(k, "") for k in LOG_FIELDS})


def log_read(path: str, ticker: Optional[str] = None) -> list[dict]:
    if not _os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    return [r for r in rows if ticker is None or r.get("Mã") == ticker]


def log_track(entries: list[dict], price_now: Optional[float]) -> list[dict]:
    """So các lần định giá cũ với giá hôm nay: giá đã đi bao nhiêu % và đã đi được bao nhiêu % quãng
    từ giá lúc định giá tới giá trị hợp lý (100% = chạm giá trị hợp lý; âm = đi ngược)."""
    out = []
    for e in entries:
        p0, fair = _num(e.get("Giá")), _num(e.get("Giá trị hợp lý"))
        chg = (price_now / p0 - 1) * 100 if (price_now and p0) else None
        prog = ((price_now - p0) / (fair - p0) * 100
                if (price_now and p0 and fair and abs(fair - p0) > 1e-9) else None)
        out.append({"Thời điểm": e.get("Thời điểm"), "Giá lúc định giá": p0, "Giá trị hợp lý": fair,
                    "Độ tin cậy": e.get("Độ tin cậy"), "Giá hôm nay": price_now,
                    "Giá đã đi %": chg, "Đi được về giá trị %": prog})
    return out


# ---------------------------------------------------------------------------- Báo cáo HTML (in ra PDF)
def _inline(s: str) -> str:
    """**đậm** và cả dòng _nghiêng_ (chỉ khi cả dòng bọc trong _…_, để tên như S_RIM không bị hỏng)."""
    s = _html.escape(s)
    italic = len(s) > 2 and s.startswith("_") and s.endswith("_")
    if italic:
        s = s[1:-1]
    parts = s.split("**")
    out = "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))
    return f"<i>{out}</i>" if italic else out


def report_html(md: str, title: str) -> str:
    """Chuyển Markdown của report_markdown (tập con: #, -, bảng |, **, _…_) sang HTML có CSS in ấn.
    Mở file trong trình duyệt → In → Lưu PDF (không cần thêm thư viện PDF)."""
    body, i, lines = [], 0, md.splitlines()
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].startswith("|"):
                tbl.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            head, rows = tbl[0], [r for r in tbl[1:] if not all(set(c) <= set("-:") for c in r)]
            body.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                        + "</tbody></table>")
            continue
        if ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            body.append("<ul>" + "".join(items) + "</ul>")
            continue
        m = len(ln) - len(ln.lstrip("#"))
        if 1 <= m <= 4 and ln[m:m + 1] == " ":
            body.append(f"<h{m}>{_inline(ln[m + 1:])}</h{m}>")
        elif ln.strip():
            body.append(f"<p>{_inline(ln)}</p>")
        i += 1
    css = ("body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:900px;margin:24px auto;"
           "padding:0 16px;color:#1b1f24;line-height:1.5}h1{font-size:22px;border-bottom:2px solid #1b1f24}"
           "h2{font-size:17px;margin-top:22px;border-bottom:1px solid #ccc}table{border-collapse:collapse;"
           "width:100%;font-size:13px;margin:8px 0}th,td{border:1px solid #ccc;padding:4px 6px;text-align:left}"
           "th{background:#f2f4f7}td:nth-child(n+2){font-variant-numeric:tabular-nums}"
           "@media print{body{margin:0}h2{break-after:avoid}table{break-inside:avoid}}")
    return (f"<!doctype html><html lang='vi'><head><meta charset='utf-8'><title>{_html.escape(title)}</title>"
            f"<style>{css}</style></head><body>{''.join(body)}</body></html>")


# ============================================================================ Bước 5 — đánh giá tổng quan
PILLAR_WEIGHTS = {"Định giá": 1.0, "Sức khỏe tài chính": 1.0, "Chất lượng tài sản": 1.0, "Vị thế cạnh tranh": 1.0}


def overall_assessment(rec: dict, fin_health: Optional[dict], quality: Optional[dict],
                       peer_set: Optional[dict]) -> dict:
    """Bảng điểm 4 trụ (0–100) + kết luận bằng QUY TẮC SỐ.

    • Định giá: 50 + chênh lệch % (kẹp 0–100); không công bố giá trị hợp lý → không chấm.
    • Sức khỏe tài chính: financials.assess_financials (điểm/tối đa).
    • Chất lượng tài sản: analysis.quality (✅ 1 · ⚠️ 0,5 · ❌ 0).
    • Vị thế cạnh tranh: hạng phần trăm TRONG NƯỚC trung bình của tăng trưởng DT, biên LN HĐ, ROE.
    Kết luận không thể 'hấp dẫn' khi chưa công bố giá trị, chất lượng tài sản hạng D / ≥ 3 ❌, hoặc chất lượng tài
    sản chưa chấm đầy đủ (ngành tài chính)."""
    pillars = {}
    up = rec.get("upside")
    pillars["Định giá"] = max(0.0, min(100.0, 50 + up)) if (rec.get("fair") and up is not None) else None
    if fin_health and fin_health.get("max"):
        pillars["Sức khỏe tài chính"] = fin_health["score"] / fin_health["max"] * 100
    else:
        pillars["Sức khỏe tài chính"] = None
    pillars["Chất lượng tài sản"] = (quality or {}).get("score")
    ranks = [r.get("Hạng trong nước (%)") for r in (peer_set or {}).get("position", [])
             if r["Chỉ số"] in ("CAGR DT 2 năm %", "Tăng trưởng DT 3 năm %", "Biên LN HĐ %", "ROE %")
             and r.get("Hạng trong nước (%)") is not None]
    pillars["Vị thế cạnh tranh"] = sum(ranks) / len(ranks) if len(ranks) >= 2 else None
    have = {k: v for k, v in pillars.items() if v is not None}
    total = (sum(v * PILLAR_WEIGHTS[k] for k, v in have.items()) / sum(PILLAR_WEIGHTS[k] for k in have)
             if have else None)
    g = (quality or {}).get("grade")
    n_fail = (quality or {}).get("n_fail", 0)
    if not rec.get("fair"):
        verdict = "⚪ Chưa đủ cơ sở định giá — " + (str(rec.get("status") or "").split(" — ", 1)[-1]
                                                   or "không mô hình chính nào qua cổng số")
    elif g == "D" or n_fail >= 3:
        verdict = "🔴 Rủi ro chất lượng tài sản — cần kiểm kỹ trước mọi kết luận về giá"
    elif up is not None and up >= 20 and (quality or {}).get("fin_limited"):
        verdict = ("🟡 Rẻ hơn giá trị ước tính, nhưng chất lượng tài sản ngành tài chính chưa chấm đầy đủ (không có "
                   "nợ xấu) — đọc thuyết minh trước khi kết luận")
    elif up is not None and up >= 20 and g in ("A", "B") and (total or 0) >= 60:
        verdict = "🟢 Rẻ hơn giá trị ước tính và chất lượng tốt — đáng nghiên cứu sâu"
    elif up is not None and up >= 20:
        verdict = "🟡 Rẻ hơn giá trị ước tính nhưng chất lượng/vị thế chưa tốt — cẩn thận bẫy giá trị"
    elif up is not None and up <= -15:
        verdict = "🔴 Giá cao hơn giá trị ước tính"
    else:
        verdict = "⚪ Giá gần giá trị ước tính"
    return {"pillars": pillars, "total": total, "verdict": verdict, "n_pillars": len(have)}
