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

import datetime as _dt
import math
from typing import Optional

import valuation as V
from financials import (_shares_estimate, _trend_value, _ytd_run_rate, conversion_ratio, one_off_core,
                        project_forecast)

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
    sec_eff = V.effective_sector(sector if isinstance(sector, str) else None,
                                 ik if isinstance(ik, str) else None)
    guide = V.recommend_method(sector if isinstance(sector, str) else None,
                               ik if isinstance(ik, str) else None)
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
                 beta: Optional[float] = None, today: Optional[_dt.date] = None) -> dict:
    """Kiểm dữ liệu bằng số. Trả {checks, cap, n_fail, n_warn}.

    cap: trần độ tin cậy — có ❌ → 'Low'; ≥2 ⚠️ → 'Medium'; còn lại None (không chặn)."""
    today = today or _dt.date.today()
    reals = _reals(series)
    checks: list[dict] = []
    price, mc = _num(row.get("price")), _num(row.get("market_cap"))
    eps, bps = _num(row.get("eps")), _num(row.get("bps"))

    n = len(reals)
    checks.append(_check("Số năm BCTC thực", n,
                         OK if n >= 5 else WARN if n >= 3 else FAIL,
                         "≥5 năm mới trọn một chu kỳ" if n < 5 else ""))
    dart_n = sum(1 for c in reals if c.get("_dart"))
    checks.append(_check("Số năm có số gốc DART", dart_n, OK if dart_n >= 3 else WARN,
                         "" if dart_n >= 3 else "Thiếu DART → dùng số Naver (đã làm tròn, 3–4 năm)"))
    checks.append(_check("Giá & vốn hóa", f"{price:,.0f}" if price else "—",
                         OK if (price and mc) else FAIL,
                         "" if (price and mc) else "Không có giá → không kiểm được vùng tin cậy"))
    checks.append(_check("BPS > 0", f"{bps:,.0f}" if bps is not None else "—",
                         OK if (bps and bps > 0) else FAIL,
                         "" if (bps and bps > 0) else "Vốn chủ âm/thiếu → mô hình sổ sách không dùng được"))

    last = reals[-1] if reals else {}
    shares = (mc / price) if (mc and price) else None
    # EPS × số cp so với LN công ty mẹ (hoặc LN thuần) năm gần nhất
    np_ref = _num(last.get("ctrl_net")) if last.get("ctrl_net") is not None else _num(last.get("net_profit"))
    eps_last = _num(last.get("eps")) or eps
    if shares and eps_last and np_ref and np_ref > 0 and eps_last > 0:
        r = eps_last * shares / 1e8 / np_ref
        checks.append(_check("EPS × số cp ÷ LN năm gần nhất", f"{r:.2f}×",
                             OK if 0.7 <= r <= 1.3 else WARN,
                             "" if 0.7 <= r <= 1.3 else "Lệch >30%: số cp thay đổi (phát hành/mua lại) "
                             "hoặc EPS và LN khác kỳ"))
    # BPS × số cp so với VCSH (DART — có cả cổ đông thiểu số nên chỉ cảnh báo)
    eq = _num(last.get("equity"))
    if shares and bps and eq and eq > 0:
        r = bps * shares / 1e8 / eq
        checks.append(_check("BPS × số cp ÷ VCSH", f"{r:.2f}×",
                             OK if 0.6 <= r <= 1.3 else WARN,
                             "" if 0.6 <= r <= 1.3 else "Lệch lớn: cổ đông thiểu số lớn hoặc BPS khác kỳ"))
    # ROE công bố so với EPS/BPS
    roe_rep = _num(last.get("roe"))
    if roe_rep is not None and eps_last and bps:
        d = abs(roe_rep - eps_last / bps * 100)
        checks.append(_check("ROE công bố vs EPS/BPS", f"lệch {d:.1f} điểm %",
                             OK if d <= 5 else WARN,
                             "" if d <= 5 else "ROE công bố tính trên VCSH bình quân / khác kỳ"))
    # Độ mới của BCTC năm
    try:
        y_last = int(str(last.get("period", ""))[:4])
    except ValueError:
        y_last = None
    if y_last:
        age = today.year - y_last
        checks.append(_check("Năm BCTC thực gần nhất", y_last, OK if age <= 1 else FAIL,
                             "" if age <= 1 else "BCTC năm quá cũ"))
    # Quý thực gần nhất
    qs = [c for c in (quarters or []) if not c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()]
    if qs:
        p = str(qs[-1]["period"])
        q_age = (today.year - int(p[:4])) * 12 + today.month - int(p[4:6])
        checks.append(_check("Quý thực gần nhất", f"{p[:4]}.{p[4:6]}", OK if q_age <= 6 else WARN,
                             "" if q_age <= 6 else "Quý mới nhất >6 tháng — dự phóng năm 1 bỏ qua số mới"))
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
    checks.append(_check("Beta riêng mã (52 tuần)", f"{beta:.2f}" if beta else "—",
                         OK if beta else WARN, "" if beta else "Dùng β = 1"))

    n_fail = sum(1 for c in checks if c["Kết quả"] == FAIL)
    n_warn = sum(1 for c in checks if c["Kết quả"] == WARN)
    cap = "Low" if n_fail else ("Medium" if n_warn >= 2 else None)
    return {"checks": checks, "cap": cap, "n_fail": n_fail, "n_warn": n_warn}


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
        out[0]["_basis"] = fc1.get("_basis", "")
    return out


def run_forecast(series: list[dict], row: dict, drivers: list[dict]) -> list[dict]:
    """Áp giả định → DT, LN, EPS, DPS, BPS, ROE từng năm. EPS = LN × 1e8 / số cp (suy như financials)."""
    reals = _reals(series)
    if not reals or not drivers:
        return []
    mc, price = _num(row.get("market_cap")), _num(row.get("price"))
    shares = _shares_estimate(reals, mc, price)
    rev = _num(reals[-1].get("revenue"))
    bps = _num(row.get("bps")) or _num(reals[-1].get("bps"))
    if not shares or not rev or bps is None:
        return []
    conv = conversion_ratio(reals)
    out = []
    for d in drivers:
        g = (_num(d.get("Tăng trưởng DT %")) or 0.0) / 100
        m = (_num(d.get("Biên LN ròng %")) or 0.0) / 100
        po = min(max((_num(d.get("Tỷ lệ cổ tức %")) or 0.0) / 100, 0.0), 1.0)
        opm = _num(d.get("Biên LN HĐ %"))
        opm = opm / 100 if opm is not None else m / conv
        bps_open = bps
        rev = rev * (1 + g)
        np_ = rev * m
        eps = np_ * 1e8 / shares
        dps = max(eps * po, 0.0)
        bps = bps + eps - dps
        out.append({"Năm": d.get("Năm"), "Doanh thu (억)": rev, "LN HĐ (억)": rev * opm, "LN ròng (억)": np_,
                    "Tăng trưởng DT %": g * 100, "Biên LN HĐ %": opm * 100, "Biên LN ròng %": m * 100,
                    "EPS": eps, "DPS": dps, "BPS đầu kỳ": bps_open, "BPS cuối kỳ": bps,
                    "ROE %": eps / bps_open * 100 if bps_open and bps_open > 0 else None})
    return out


def forecast_gates(series: list[dict], fc: list[dict], financial: bool = False,
                   evidence: Optional[dict] = None) -> dict:
    """Cổng cứng bằng số cho dự phóng. Có ❌ → mô hình dựa trên dự phóng KHÔNG vào kết luận.

    evidence (reported_evidence): năm 1 đã có ≥2 quý thực → dải tăng trưởng năm 1 nới tới số đã công bố
    (+5 điểm), và biên LN công bố được tính vào 'max lịch sử'. Chỉ số thực mới nới được cổng."""
    reals = _reals(series)
    hm = _hist_net_margins(reals)
    if evidence:
        hm = hm + [evidence["margin"]]
    m_max = max(hm) if hm else None
    checks = []
    for i, f in enumerate(fc):
        y = f["Năm"]
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
    n_fail = sum(1 for c in checks if c["Kết quả"] == FAIL)
    if not checks:
        checks.append(_check("Giả định dự phóng", "trong dải", OK, ""))
    return {"checks": checks, "ok": n_fail == 0 and bool(fc), "n_fail": n_fail}


# ============================================================================ Bước 5 — chi phí vốn
def cost_of_capital(a: V.Assumptions, beta_raw: Optional[float], market_cap: Optional[float] = None,
                    extras: Optional[dict] = None) -> dict:
    """Ke (CAPM, β Blume), Kd ≈ Rf + 1,5%, WACC theo nợ vay DART (nếu có)."""
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
    wacc = V._wacc(ke, kd, tax, (market_cap or 0) / 1e8, debt) if (debt is not None and market_cap) else None
    return {"rf": a.rf, "erp": a.erp, "beta_raw": beta_raw, "beta_used": b_used if b_used else a.beta,
            "ke": ke, "kd": kd, "tax": tax, "debt_eok": debt, "wacc": wacc, "g_term": a.g_term_capped()}


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
    """DDM tường minh: Σ DPS_t/(1+Ke)^t + DPS_N(1+g)/(Ke−g)/(1+Ke)^N. Cần cổ tức > 0 năm cuối."""
    if not fc or ke <= g or fc[-1]["DPS"] <= 0:
        return None
    rows = [{"Năm": f.get("Năm", t), "DPS": f["DPS"], "Hệ số chiết khấu": 1 / (1 + ke) ** t,
             "PV DPS": f["DPS"] / (1 + ke) ** t} for t, f in enumerate(fc, 1)]
    pv = sum(r["PV DPS"] for r in rows)
    n = len(fc)
    tv = fc[-1]["DPS"] * (1 + g) / (ke - g) / (1 + ke) ** n
    v = pv + tv
    return ({"value": v, "tv_share": tv / v, "rows": rows,
             "bridge": [{"Khoản mục": "Σ PV cổ tức", "KRW/cp": pv},
                        {"Khoản mục": f"+ PV cổ tức cuối kỳ (g = {g * 100:.1f}%)", "KRW/cp": tv},
                        {"Khoản mục": "= Giá trị/cp", "KRW/cp": v}]} if v > 0 else None)


# ============================================================================ peer
def peer_medians(peer_rows: list[dict], exclude: Optional[str] = None) -> dict:
    """Trung vị P/E (0; 60] và P/B > 0 của peer (bỏ chính mã), cần ≥ MIN_PEERS mã hợp lệ."""
    rows = [r for r in peer_rows if r.get("ticker") != exclude]
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


def industry_peers(industry_kr: str, exclude: Optional[str] = None, n: int = 12) -> list[str]:
    """Top-n mã cùng ngành KRX (업종 Naver) theo vốn hóa, bỏ ETF và chính mã. [] nếu lỗi mạng."""
    import requests
    try:
        groups = requests.get(_IND_API, params={"page": 1, "pageSize": 100}, headers=_H, timeout=10).json()
        no = next((g["no"] for g in groups.get("groups", []) if g.get("name") == industry_kr), None)
        if no is None:
            return []
        stocks, page = [], 1
        while page <= 5:
            j = requests.get(f"{_IND_API}/{no}", params={"page": page, "pageSize": 100},
                             headers=_H, timeout=10).json()
            s = j.get("stocks") or []
            stocks.extend(s)
            if page * 100 >= j.get("totalCount", 0) or not s:
                break
            page += 1
    except Exception:
        return []
    rows = [(str(s.get("itemCode", "")).zfill(6), float(s.get("marketValueRaw") or 0)) for s in stocks
            if s.get("stockEndType") == "stock"]
    rows = [r for r in rows if r[0] != exclude]
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows[:n]]


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


def reconcile(price: Optional[float], candidates: list[dict], anchor: Optional[float],
              caps: list[Optional[str]]) -> dict:
    """candidates: [{name, value, role: 'primary'|'cross', blocked: str|None}].

    Mỗi ứng viên qua cổng số (_gate). Giá trị hợp lý = trung vị các mô hình CHÍNH qua cổng;
    khoảng = min–max của mọi ứng viên qua cổng. Không mô hình chính nào qua → không công bố.
    Độ tin cậy: High chỉ khi ≥2 mô hình chính qua cổng, cùng nằm trong ±20% trung vị VÀ giá trị hợp lý
    trong [0,67; 1,5]× phép neo peer (nếu có — hai mô hình cùng họ sổ sách đồng ý với nhau chưa đủ);
    rồi áp các trần (chất lượng dữ liệu, độ tin cậy lõi KCI)."""
    rows = []
    for c in candidates:
        if c.get("blocked"):
            ok, why = False, c["blocked"]
        else:
            ok, why = _gate(c.get("value"), price, anchor)
        rows.append({**c, "pass": ok, "why": why})
    prim = [r["value"] for r in rows if r["pass"] and r["role"] == "primary"]
    allv = [r["value"] for r in rows if r["pass"]]
    if not prim:
        return {"fair": None, "low": None, "high": None, "upside": None, "confidence": "Low",
                "status": "⚪ không công bố — không mô hình chính nào qua cổng số", "rows": rows}
    fair = V._median(prim)
    near_anchor = anchor is None or HIGH_ANCHOR_BAND[0] <= fair / anchor <= HIGH_ANCHOR_BAND[1]
    if len(prim) >= 2 and all(abs(v / fair - 1) <= AGREE_BAND for v in prim) and near_anchor:
        conf = "High"
    elif len(prim) >= 2:
        conf = "Medium"
    else:
        conf = "Medium" if len(allv) >= 2 else "Low"
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
         f"_Ngày {ctx['date']} · KCI Pipeline giai đoạn 1 · số liệu Naver/DART, công thức deterministic_", "",
         "## Kết luận",
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
    coc = cost_of_capital(a, _num(row.get("beta")), mc, extras)
    ke, w = coc["ke"], a.persistence
    pm = peer_medians(D.get("peers") or [], exclude=D.get("ticker"))
    fc = run_forecast(series, row, drivers)
    fg = forecast_gates(series, fc, financial=idn["financial"], evidence=evidence)
    r_fwd = dict(row)
    if fc:
        r_fwd["fwd_eps"] = fc[0]["EPS"]
        r_fwd["fwd_roe"] = fc[0]["EPS"] / fc[0]["BPS đầu kỳ"] if fc[0]["BPS đầu kỳ"] else None
    base = V.value_stock(r_fwd, a, pm["pe"], pm["pb"], history=reals or None)
    anchor = peer_anchor(base.methods, "mid-cycle" in base.auto_method)
    rim = rim_explicit(_num(row.get("bps")), fc, ke, w)
    ddm = ddm_explicit(fc, ke, coc["g_term"])
    fc_block = None if fg["ok"] else f"cổng dự phóng: {fg['n_fail']} vi phạm"
    cands = [
        # tên gắn trạng thái tam giác hóa: lõi đổi sang 50/50 với peer = một mô hình KHÁC (fair_same_set)
        {"name": f"Lõi KCI — {base.auto_method}" + (" · △ 50/50 peer" if base.triangulated else ""),
         "value": base.fair_raw, "role": "primary", "blocked": None if base.reliable else base.status},
        {"name": f"RIM tường minh ({len(fc)} năm dự phóng)", "value": rim["value"] if rim else None,
         "role": "primary", "blocked": fc_block},
        {"name": f"DDM tường minh ({len(fc)} năm)", "value": ddm["value"] if ddm else None,
         "role": "primary" if idn["dividend_model"] else "cross", "blocked": fc_block},
        {"name": "P/E × trung vị peer", "value": base.methods.get("P/E vs median ngành"), "role": "cross"},
        {"name": "P/B × trung vị peer", "value": base.methods.get("P/B vs median ngành"), "role": "cross"},
    ]
    fs = D.get("fs_full")
    shares = _shares_estimate(reals, mc, price) if reals else None
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
    if not fcff_m and extras.get("cfo") is not None and mc and price:
        fcff = V.fcff_value(extras.get("cfo"), extras.get("capex"), extras.get("debt"), extras.get("net_debt"),
                            mc / price, ke, coc["kd"], coc["tax"], a.g_high, a.years_high, coc["g_term"],
                            mc / 1e8)
        cands.append({"name": "FCFF-DCF (DART, tăng trưởng phẳng)", "value": fcff, "role": "cross"})
    # mô hình chuyên ngành (sector_models): hàm(coc) → ứng viên, để độ nhạy Ke dịch cả rNPV / EV-GWh
    for fn in D.get("extra") or []:
        c = fn(coc)
        if c:
            cands.append(c)
    caps = [(dq or {}).get("cap"), None if idn["data_ok"] else "Medium"]
    rec = reconcile(price, cands, anchor, caps)
    fc_model = ddm if idn["dividend_model"] else rim
    return {"fc": fc, "fg": fg, "base": base, "anchor": anchor, "rim": rim, "ddm": ddm, "rec": rec,
            "fcff": fcff_m, "ev_ebit": evm, "shares": shares,
            "coc": coc, "pm": pm, "fc_model": fc_model["value"] if fc_model else None,
            "fc_model_name": "DDM tường minh" if idn["dividend_model"] else "RIM tường minh"}


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


def shift_drivers(drivers: list[dict], dg: float, mm: float, keep_year1: bool = False) -> list[dict]:
    """Dịch giả định: tăng trưởng + dg điểm %, biên × mm. keep_year1: năm 1 đã có ≥2 quý thực → giữ nguyên
    (số đã công bố không phải giả định để lạc quan/bi quan)."""
    out = []
    for i, d in enumerate(drivers):
        d2 = dict(d)
        if not (keep_year1 and i == 0):
            d2["Tăng trưởng DT %"] = (_num(d.get("Tăng trưởng DT %")) or 0.0) + dg
            m = _num(d.get("Biên LN ròng %")) or 0.0
            # biên âm: kịch bản xấu là lỗ sâu hơn, tốt là lỗ ít hơn
            d2["Biên LN ròng %"] = m * mm if m >= 0 else m * (2 - mm)
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
    psum = sum(r["Xác suất"] for r in rows)
    ev_fair = (sum(r["Xác suất"] * r["Giá trị hợp lý"] for r in rows) / psum
               if informative and psum > 0 and all(f is not None for f in fairs) else None)
    if not informative:
        fairs = []
    return {"rows": rows, "expected": ev_fair, "informative": informative,
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
              "Trạng thái", "Kỳ vọng kịch bản", "Ke %", "ERP %", "g %", "Số năm", "Giả định"]


def log_entry(ctx: dict, drivers: list[dict], a: V.Assumptions, scen: Optional[dict]) -> dict:
    rec, coc = ctx["conclusion"], ctx["coc"]

    def r0(x):
        return round(x) if isinstance(x, (int, float)) and math.isfinite(x) else ""
    return {"Thời điểm": _dt.datetime.now().isoformat(timespec="seconds"), "Mã": ctx["ticker"],
            "Tên": ctx["name"], "Giá": r0(ctx["price"]), "Giá trị hợp lý": r0(rec["fair"]),
            "Thấp": r0(rec["low"]), "Cao": r0(rec["high"]),
            "Chênh %": round(rec["upside"], 1) if rec["upside"] is not None else "",
            "Độ tin cậy": rec["confidence"], "Trạng thái": rec["status"],
            "Kỳ vọng kịch bản": r0((scen or {}).get("expected")), "Ke %": round(coc["ke"] * 100, 2),
            "ERP %": round(a.erp * 100, 2), "g %": round(coc["g_term"] * 100, 2), "Số năm": len(drivers),
            "Giả định": _json.dumps([{k: d[k] for k in ("Năm", "Tăng trưởng DT %", "Biên LN ròng %",
                                                         "Tỷ lệ cổ tức %") if k in d} for d in drivers],
                                    ensure_ascii=False)}


def log_append(path: str, entry: dict) -> None:
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
             if r["Chỉ số"] in ("Tăng trưởng DT 3 năm %", "Biên LN HĐ %", "ROE %")
             and r.get("Hạng trong nước (%)") is not None]
    pillars["Vị thế cạnh tranh"] = sum(ranks) / len(ranks) if len(ranks) >= 2 else None
    have = {k: v for k, v in pillars.items() if v is not None}
    total = (sum(v * PILLAR_WEIGHTS[k] for k, v in have.items()) / sum(PILLAR_WEIGHTS[k] for k in have)
             if have else None)
    g = (quality or {}).get("grade")
    n_fail = (quality or {}).get("n_fail", 0)
    if not rec.get("fair"):
        verdict = "⚪ Chưa đủ cơ sở định giá — không mô hình chính nào qua cổng số"
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
