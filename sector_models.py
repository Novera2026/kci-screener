"""
KCI — mô hình chuyên ngành (giai đoạn 3): đóng tàu/quốc phòng (backlog), pin (công suất GWh), dược/biotech (rNPV).

Ba ngành này không định giá đúng được bằng ROE/EPS quá khứ, và App KHÔNG có nguồn dữ liệu tự động cho đầu vào
quan trọng nhất (수주잔고, công suất GWh, danh mục thuốc). Như SOTP: người dùng NHẬP đầu vào (từ 사업보고서 DART /
IR), mô hình tính deterministic, và mọi đầu vào phải qua CỔNG BẰNG SỐ — không có ô giải trình nào mở được cổng.

  • backlog_drivers()   : 수주잔고 → doanh thu các năm đầu (đốt backlog) + đơn mới → giả định bước ④
  • capacity_drivers()  : GWh × hiệu suất × giá bán/GWh → doanh thu → giả định bước ④;  ev_per_gwh_value(): đối chiếu
  • rnpv()              : Σ PoS × doanh số × biên / (1+r)^t − chi phí phát triển  +  tiền ròng  → giá trị/cp
"""
from __future__ import annotations

from typing import Optional

import valuation as V

OK, WARN, FAIL = "✅", "⚠️", "❌"
SPECIAL_BUCKETS = {"Shipbuilding": "backlog", "Defense": "backlog", "Battery": "capacity",
                   "Pharma/Biotech": "rnpv"}

# ---------------------------------------------------------------------------- cổng
BACKLOG_MAX_YEARS = 8.0          # backlog / doanh thu năm gần nhất > 8 năm → ❌ (gần như chắc chắn nhập sai đơn vị)
BURN_YEARS = (1, 6)              # số năm đốt backlog cho phép
MARGIN_HEADROOM = 0.10           # biên trên backlog / công suất ≤ max biên LN ròng lịch sử + 10 điểm
UTIL_BAND = (0.0, 1.0)
ASP_BAND = (0.5, 1.5)            # giá bán/GWh nhập ÷ giá bán ngụ ý từ doanh thu năm gần nhất
POS_CAP_MULT = 1.5               # PoS nhập ≤ 1,5× PoS chuẩn của pha (và ≤ 100%)
RNPV_MARGIN_MAX = 0.60
# Xác suất được phê duyệt tính từ đầu pha — BIO/Informa/QLS "Clinical Development Success Rates 2011–2020"
# (Phase I 7,9%, II 15,3%, III 57,8%, NDA/BLA 90,6%). Tiền lâm sàng: giả định nhà 4% (báo cáo không đo).
POS_BY_PHASE = {"Tiền lâm sàng": 0.04, "Pha 1": 0.079, "Pha 2": 0.153, "Pha 3": 0.578,
                "Nộp hồ sơ": 0.906, "Đã phê duyệt": 1.0}


def _num(x) -> Optional[float]:
    return V._num(x)


def _chk(name, value, status, note=""):
    return {"Kiểm tra": name, "Giá trị": value, "Kết quả": status, "Ghi chú": note}


def _hist_margin_max(reals: list[dict]) -> Optional[float]:
    ms = [c["net_profit"] / c["revenue"] for c in reals
          if c.get("net_profit") is not None and c.get("revenue")]
    return max(ms) if ms else None


def _reals(series):
    return [c for c in (series or []) if not c.get("is_consensus")]


def _gate_result(checks: list[dict]) -> dict:
    n_fail = sum(1 for c in checks if c["Kết quả"] == FAIL)
    return {"checks": checks, "ok": n_fail == 0, "n_fail": n_fail}


# ============================================================================ đóng tàu / quốc phòng — backlog
def backlog_drivers(series: list[dict], backlog: float, burn_years: float, backlog_margin: float,
                    new_order_ratio: float, years: int = 5, payout: float = 0.0,
                    tail_margin: Optional[float] = None) -> dict:
    """수주잔고 (억원) đốt đều trong `burn_years` năm + đơn mới mỗi năm = new_order_ratio × doanh thu năm trước,
    ghi nhận dần trong `burn_years` năm. Biên LN ròng: backlog_margin cho phần backlog hiện có, tail_margin
    (mặc định = backlog_margin) cho đơn mới. Trả {drivers, revenue, checks, ok}.

    new_order_ratio: book-to-bill (đơn mới / doanh thu). 1,0 = giữ quy mô."""
    reals = _reals(series)
    rev0 = _num(reals[-1].get("revenue")) if reals else None
    checks = []
    if not rev0 or rev0 <= 0:
        return {"drivers": [], "revenue": [], "checks": [_chk("Doanh thu năm gần nhất", "—", FAIL)], "ok": False}
    cover = backlog / rev0 if backlog else 0
    checks.append(_chk("Backlog ÷ doanh thu năm gần nhất", f"{cover:.1f} năm",
                       OK if 0 < cover <= BACKLOG_MAX_YEARS else FAIL,
                       "" if 0 < cover <= BACKLOG_MAX_YEARS else
                       f"phải trong (0; {BACKLOG_MAX_YEARS:.0f}] năm — kiểm đơn vị (억원)"))
    by_ok = BURN_YEARS[0] <= burn_years <= BURN_YEARS[1]
    checks.append(_chk("Số năm đốt backlog", f"{burn_years:.1f}", OK if by_ok else FAIL,
                       "" if by_ok else f"phải trong [{BURN_YEARS[0]}; {BURN_YEARS[1]}]"))
    m_max = _hist_margin_max(reals)
    for nm, m in (("Biên LN ròng trên backlog", backlog_margin),
                  ("Biên LN ròng đơn mới", tail_margin if tail_margin is not None else backlog_margin)):
        bad = m_max is not None and m > m_max + MARGIN_HEADROOM
        checks.append(_chk(nm, f"{m * 100:.1f}%", FAIL if bad else OK,
                           f"> max lịch sử {m_max * 100:.1f}% + {MARGIN_HEADROOM * 100:.0f} điểm" if bad else ""))
    btb_ok = 0.0 <= new_order_ratio <= 3.0
    checks.append(_chk("Book-to-bill đơn mới", f"{new_order_ratio:.2f}", OK if btb_ok else FAIL,
                       "" if btb_ok else "phải trong [0; 3]"))
    if not (by_ok and 0 < cover):
        return {"drivers": [], "revenue": [], "checks": checks, "ok": False}

    tm = tail_margin if tail_margin is not None else backlog_margin
    burn = backlog / burn_years
    tranches: list[tuple[float, int]] = []        # (giá trị đơn mới mỗi năm ghi nhận, năm bắt đầu)
    revs, out = [], []
    prev = rev0
    y0 = int(str(reals[-1]["period"])[:4])
    def frac(t: int, start: int) -> float:
        """Phần của năm t nằm trong quãng ghi nhận [start; start + burn_years) — xử lý burn_years lẻ (2,5 năm)."""
        return max(0.0, min(1.0, start + burn_years - t)) if t >= start else 0.0

    for t in range(1, years + 1):
        r_back = burn * frac(t, 1)
        r_new = sum(v * frac(t, s) for v, s in tranches)
        rev = r_back + r_new
        np_ = r_back * backlog_margin + r_new * tm
        tranches.append((new_order_ratio * rev / burn_years, t + 1))      # đơn ký năm t, ghi nhận từ t+1
        out.append({"Năm": str(y0 + t), "Tăng trưởng DT %": round((rev / prev - 1) * 100, 2) if prev else 0.0,
                    "Biên LN ròng %": round(np_ / rev * 100, 2) if rev else 0.0,
                    "Tỷ lệ cổ tức %": round(payout * 100, 1)})
        revs.append(rev)
        prev = rev if rev > 0 else prev
    return {"drivers": out, "revenue": revs, **_gate_result(checks)}


# ============================================================================ pin — công suất
def capacity_drivers(series: list[dict], gwh_now: float, gwh_path: list[float], utilization: float,
                     asp_eok_per_gwh: float, margin: float, payout: float = 0.0) -> dict:
    """Doanh thu năm t = GWh_t × hiệu suất × giá bán (억원/GWh). gwh_path: công suất cuối mỗi năm dự phóng.
    Cổng: hiệu suất ∈ [0; 1]; giá bán nhập ∈ [0,5; 1,5]× giá ngụ ý (doanh thu năm gần nhất ÷ (GWh hiện tại ×
    hiệu suất)); biên ≤ max lịch sử + 10 điểm."""
    reals = _reals(series)
    rev0 = _num(reals[-1].get("revenue")) if reals else None
    checks = []
    u_ok = UTIL_BAND[0] < utilization <= UTIL_BAND[1]
    checks.append(_chk("Hiệu suất sử dụng", f"{utilization * 100:.0f}%", OK if u_ok else FAIL,
                       "" if u_ok else "phải trong (0; 100%]"))
    implied = rev0 / (gwh_now * utilization) if (rev0 and gwh_now and gwh_now > 0 and utilization > 0) else None
    if implied:
        r = asp_eok_per_gwh / implied
        a_ok = ASP_BAND[0] <= r <= ASP_BAND[1]
        checks.append(_chk("Giá bán/GWh ÷ giá ngụ ý từ doanh thu", f"{r:.2f}× (ngụ ý {implied:,.0f}억/GWh)",
                           OK if a_ok else FAIL,
                           "" if a_ok else f"phải trong [{ASP_BAND[0]}; {ASP_BAND[1]}]× — số GWh hoặc giá bán lệch "
                                           "xa doanh thu thực"))
    else:
        checks.append(_chk("Giá bán/GWh", "—", FAIL, "cần doanh thu năm gần nhất và GWh hiện tại > 0"))
    m_max = _hist_margin_max(reals)
    bad = m_max is not None and margin > m_max + MARGIN_HEADROOM
    checks.append(_chk("Biên LN ròng", f"{margin * 100:.1f}%", FAIL if bad else OK,
                       f"> max lịch sử {m_max * 100:.1f}% + {MARGIN_HEADROOM * 100:.0f} điểm" if bad else ""))
    if not (u_ok and rev0 and gwh_path):
        return {"drivers": [], "revenue": [], **_gate_result(checks)}
    y0 = int(str(reals[-1]["period"])[:4])
    out, revs, prev = [], [], rev0
    for t, g in enumerate(gwh_path, 1):
        rev = g * utilization * asp_eok_per_gwh
        out.append({"Năm": str(y0 + t), "Tăng trưởng DT %": round((rev / prev - 1) * 100, 2) if prev else 0.0,
                    "Biên LN ròng %": round(margin * 100, 2), "Tỷ lệ cổ tức %": round(payout * 100, 1)})
        revs.append(rev)
        prev = rev if rev > 0 else prev
    return {"drivers": out, "revenue": revs, **_gate_result(checks)}


def ev_per_gwh_value(gwh: float, ev_per_gwh_eok: float, net_debt_eok: float, shares: float) -> Optional[float]:
    """Đối chiếu tương đối: (GWh × EV/GWh − nợ ròng) / số cp. Đơn vị 억원 → trả KRW/cp."""
    if not (gwh and gwh > 0 and ev_per_gwh_eok and ev_per_gwh_eok > 0 and shares and shares > 0):
        return None
    eq = gwh * ev_per_gwh_eok - (net_debt_eok or 0.0)
    return eq * 1e8 / shares if eq > 0 else None


def implied_ev_per_gwh(market_cap: Optional[float], net_debt_eok: Optional[float], gwh: float) -> Optional[float]:
    """EV/GWh thị trường đang trả cho mã này (억원/GWh) — để người dùng so với bội số mình nhập."""
    if not (market_cap and gwh and gwh > 0):
        return None
    return (market_cap / 1e8 + (net_debt_eok or 0.0)) / gwh


# ============================================================================ dược / biotech — rNPV
def rnpv(assets: list[dict], r: float, shares: float, net_cash_eok: float = 0.0, this_year: int = 2026,
         ramp_years: int = 5, exclusivity: int = 10, overhead_eok: float = 0.0) -> dict:
    """assets: [{Tên, Pha, Doanh số đỉnh (억), Năm ra mắt, PoS %, Biên LN %, Chi phí PT còn lại (억)}].

    Mỗi tài sản: doanh số tăng tuyến tính tới đỉnh trong ramp_years năm từ năm ra mắt, giữ tới hết exclusivity
    năm rồi về 0. Dòng tiền = PoS × doanh số × biên (biên SAU thuế, hoặc tỷ lệ royalty nếu chỉ thu phí bản quyền).
    Chi phí PT còn lại chi đều tới năm ra mắt, KHÔNG nhân PoS (chi trước khi biết kết quả — thận trọng).
    overhead_eok: chi phí chung hằng năm của công ty (억), trừ như niên kim vĩnh viễn overhead / r.
    Tổng = Σ rNPV + tiền ròng − PV chi phí chung → giá trị/cp. Trả {value, rows, checks, ok}."""
    checks, rows = [], []
    total = 0.0
    for a in assets:
        nm = str(a.get("Tên") or "?")
        phase = a.get("Pha")
        peak = _num(a.get("Doanh số đỉnh (억)")) or 0.0
        launch = int(_num(a.get("Năm ra mắt")) or this_year)
        pos_in = _num(a.get("PoS %"))
        m = (_num(a.get("Biên LN %")) or 0.0) / 100
        cost = _num(a.get("Chi phí PT còn lại (억)")) or 0.0
        base = POS_BY_PHASE.get(phase)
        pos = (pos_in / 100) if pos_in is not None else base
        if base is None:
            checks.append(_chk(f"{nm}: pha", str(phase), FAIL, "chọn pha trong danh sách"))
            continue
        cap = min(1.0, base * POS_CAP_MULT)
        if pos is None or not 0 <= pos <= cap:
            checks.append(_chk(f"{nm}: PoS", f"{(pos or 0) * 100:.1f}%", FAIL,
                               f"> {cap * 100:.1f}% (= {POS_CAP_MULT}× chuẩn pha {phase} {base * 100:.1f}%)"))
            continue
        if not 0 <= m <= RNPV_MARGIN_MAX:
            checks.append(_chk(f"{nm}: biên", f"{m * 100:.0f}%", FAIL, f"phải trong [0; {RNPV_MARGIN_MAX * 100:.0f}%]"))
            continue
        if peak <= 0 or launch < this_year:
            checks.append(_chk(f"{nm}: doanh số/năm ra mắt", f"{peak:,.0f}억 · {launch}", FAIL,
                               "doanh số đỉnh > 0 và năm ra mắt ≥ năm nay"))
            continue
        pv_cf = 0.0
        for y in range(launch, launch + exclusivity):
            t = y - this_year + 1
            sales = peak * min(1.0, (y - launch + 1) / ramp_years)
            pv_cf += pos * sales * m / (1 + r) ** t
        n_dev = max(launch - this_year, 1)
        pv_cost = sum(cost / n_dev / (1 + r) ** t for t in range(1, n_dev + 1))
        v = pv_cf - pv_cost
        total += v
        rows.append({"Tài sản": nm, "Pha": phase, "PoS dùng %": round(pos * 100, 1), "PV dòng tiền (억)": pv_cf,
                     "PV chi phí PT (억)": pv_cost, "rNPV (억)": v})
    oh = overhead_eok / r if (overhead_eok and r > 0) else 0.0
    equity = total + (net_cash_eok or 0.0) - oh
    if not checks:
        checks.append(_chk("Đầu vào danh mục", f"{len(rows)} tài sản", OK if rows else FAIL,
                           "" if rows else "chưa nhập tài sản nào"))
    g = _gate_result(checks)
    value = equity * 1e8 / shares if (g["ok"] and rows and shares and shares > 0 and equity > 0) else None
    return {"value": value, "rows": rows, "sum_assets": total, "net_cash": net_cash_eok, "overhead_pv": oh,
            "equity": equity, **g}
