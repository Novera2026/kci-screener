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
from financials import _shares_estimate, _trend_value, _ytd_run_rate, project_forecast

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
    for r in rows:
        x, flag = r["ROE %"], ""
        if x is not None and med is not None and med != 0:
            if (x > 0) != (med > 0):
                flag = "ROE khác dấu trung vị"
            elif abs(x) > 2 * abs(med) or abs(x) < 0.5 * abs(med):
                flag = "ROE lệch >2× trung vị"
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
    return {"k": ytd["k"], "growth": ytd["revenue"] / rev_last - 1, "margin": ytd["net_profit"] / ytd["revenue"]}


def default_drivers(series: list[dict], row: dict, a: V.Assumptions, years: int = 5,
                    quarters: Optional[list[dict]] = None, cyclical: bool = False) -> list[dict]:
    """Giả định mặc định cho dự phóng (người dùng sửa trên bảng):

      • Năm 1: theo financials.project_forecast (đã tính các quý thực đã công bố của năm đó).
      • Tăng trưởng DT: năm 1 giữ nguyên nếu đã có ≥ 2 quý thực, không thì kẹp [-20%; +30%]; năm 2+ trượt
        tuyến tính từ g năm 1 đã kẹp về g dài hạn ở năm cuối.
      • Biên LN ròng: ngành chu kỳ / biên năm 1 âm → trượt dần về trung vị lịch sử (mid-cycle);
        còn lại giữ biên xu hướng.
      • Tỷ lệ cổ tức: trung vị lịch sử (kẹp 0–90%), không có → 0.
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
    fade_margin = (cyclical or m1 <= 0) and m_mid is not None
    g_long = a.g_term_capped()
    payout = _payout_hist(reals) or 0.0
    y0 = int(str(reals[-1]["period"])[:4])
    # Năm 2+ là GIẢ ĐỊNH, không phải số công bố: trượt từ g năm 1 đã kẹp [-20%; +30%] về g dài hạn — đợt
    # tăng vọt của năm 1 (bằng chứng quý thực) không được ngoại suy sang các năm sau.
    g_from = min(max(g1, -0.20), 0.30)
    out = []
    for t in range(1, years + 1):
        k = (t - 1) / (years - 1) if years > 1 else 1.0
        g = g1 if t == 1 else g_from + (g_long - g_from) * k
        m = (m1 + (m_mid - m1) * k) if fade_margin else m1
        out.append({"Năm": str(y0 + t), "Tăng trưởng DT %": round(g * 100, 1),
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
    out = []
    for d in drivers:
        g = (_num(d.get("Tăng trưởng DT %")) or 0.0) / 100
        m = (_num(d.get("Biên LN ròng %")) or 0.0) / 100
        po = min(max((_num(d.get("Tỷ lệ cổ tức %")) or 0.0) / 100, 0.0), 1.0)
        bps_open = bps
        rev = rev * (1 + g)
        np_ = rev * m
        eps = np_ * 1e8 / shares
        dps = max(eps * po, 0.0)
        bps = bps + eps - dps
        out.append({"Năm": d.get("Năm"), "Doanh thu (억)": rev, "LN ròng (억)": np_,
                    "Tăng trưởng DT %": g * 100, "Biên LN ròng %": m * 100,
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
        if m_max is not None and m > m_max + FC_MARGIN_HEADROOM:
            checks.append(_check(f"{y}: biên LN ròng", f"{m * 100:.1f}%", FAIL,
                                 f"> max lịch sử/đã công bố {m_max * 100:.1f}% + {FC_MARGIN_HEADROOM * 100:.0f} điểm"))
        if not financial and m > FC_MARGIN_MAX:
            checks.append(_check(f"{y}: biên LN ròng", f"{m * 100:.1f}%", FAIL,
                                 f"> {FC_MARGIN_MAX * 100:.0f}% (doanh nghiệp phi tài chính)"))
        roe = f.get("ROE %")
        if roe is not None and roe / 100 > FC_ROE_MAX:
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
    pv, ri = 0.0, 0.0
    for t, f in enumerate(fc, 1):
        ri = f["EPS"] - ke * f["BPS đầu kỳ"]
        pv += ri / (1 + ke) ** t
    n = len(fc)
    tv = ri * w / (1 + ke - w) / (1 + ke) ** n
    v = bps0 + pv + tv
    if not math.isfinite(v) or v <= 0:
        return None
    return {"value": v, "tv_share": abs(tv) / v}


def ddm_explicit(fc: list[dict], ke: float, g: float) -> Optional[dict]:
    """DDM tường minh: Σ DPS_t/(1+Ke)^t + DPS_N(1+g)/(Ke−g)/(1+Ke)^N. Cần cổ tức > 0 năm cuối."""
    if not fc or ke <= g or fc[-1]["DPS"] <= 0:
        return None
    pv = sum(f["DPS"] / (1 + ke) ** t for t, f in enumerate(fc, 1))
    n = len(fc)
    tv = fc[-1]["DPS"] * (1 + g) / (ke - g) / (1 + ke) ** n
    v = pv + tv
    return {"value": v, "tv_share": tv / v} if v > 0 else None


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
         f"- Độ tin cậy: **{rec['confidence']}** · Trạng thái: {rec['status']}", "",
         "## 1. Nhận diện",
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
    pm = ctx.get("peers") or {}
    L += ["", f"Phép neo peer: P/E trung vị {pm.get('pe') or '—'} ({pm.get('n_pe', 0)} mã), "
              f"P/B trung vị {pm.get('pb') or '—'} ({pm.get('n_pb', 0)} mã) → neo {_f0(ctx.get('anchor'))} KRW",
          "", "_Đây là ước lượng có kỷ luật cho nghiên cứu, không phải khuyến nghị đầu tư._"]
    return "\n".join(L)
