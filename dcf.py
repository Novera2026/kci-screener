"""
KCI — bảng dự phóng QUÝ/NĂM và các mô hình dòng tiền có BẢNG TÍNH CHI TIẾT (bước ④ của quy trình).

  • quarter_forecast  : chia dự phóng năm (pipeline.run_forecast) thành từng quý theo MÙA VỤ của 4 quý thực gần nhất;
                        năm đang chạy = quý thực đã công bố + phần còn lại chia theo mùa vụ → tổng quý = tổng năm.
  • fcff_dcf          : FCFF = EBIT·(1−t) − tái đầu tư; tái đầu tư = ΔDoanh thu ÷ (Doanh thu/Vốn đầu tư) — phương
                        pháp sales-to-capital của Damodaran, KHÔNG cần khấu hao (DART tóm tắt và Naver không tách
                        khấu hao của DN Hàn). Giá trị cuối kỳ: NOPAT·(1+g)·(1 − g/RONIC) / (WACC − g).
                        EV − nợ ròng − lợi ích cổ đông thiểu số = vốn chủ → giá/cp.
  • ev_ebit_ttm       : EBIT 4 quý gần nhất × trung vị EV/EBIT (4 quý) của đối thủ TRONG NƯỚC (EV = vốn hóa + nợ ròng
                        + thiểu số, DART). EV/EBIT thay EV/EBITDA vì không có khấu hao cho DN Hàn — EBIT so EBIT.

Đơn vị tiền 억원; giá/cp KRW. Mọi hàm trả {"value", "rows", ...} để trang hiện bảng tính từng năm.
"""
from __future__ import annotations

from typing import Optional

import valuation as V

S2C_BAND = (0.3, 5.0)          # doanh thu / vốn đầu tư — kẹp để một năm méo không làm hỏng tái đầu tư
RONIC_CAP = 0.30               # lợi suất vốn đầu tư mới dài hạn tối đa
EV_EBIT_BAND = (0.0, 40.0)     # bội số đối thủ ngoài dải bị loại khỏi trung vị


def _n(x):
    return V._num(x)


# ============================================================================ dự phóng quý
SEASON_MAX_SPREAD = 1.3        # 4 quý gần nhất lệch nhau > 1,3× → đó là xu hướng (tăng/giảm mạnh), không phải mùa vụ


def seasonal_weights(quarters: list[dict]) -> dict:
    """Tỷ trọng doanh thu từng quý dương lịch (03/06/09/12) từ 4 quý THỰC gần nhất — chỉ khi doanh thu ổn định
    (max/min ≤ 1,3). Đang tăng/giảm mạnh (Samsung Q3/25 → Q2/26 gấp 2,3×) thì 'mùa vụ' đo được thực chất là xu
    hướng → chia đều. Thiếu dữ liệu → chia đều. Khóa '_even' = True khi chia đều."""
    q = [c for c in (quarters or []) if not c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()][-4:]
    by = {str(c["period"])[4:6]: _n(c.get("revenue")) for c in q}
    if len(by) == 4 and all(v and v > 0 for v in by.values()) and max(by.values()) / min(by.values()) <= SEASON_MAX_SPREAD:
        tot = sum(by.values())
        return {**{m: v / tot for m, v in by.items()}, "_even": False}
    return {**{m: 0.25 for m in ("03", "06", "09", "12")}, "_even": True}


def quarter_forecast(quarters: list[dict], fc: list[dict], shares: Optional[float]) -> list[dict]:
    """Bảng quý: các quý thực của năm dự phóng đầu + các quý còn lại của 2 năm dự phóng đầu.
    Phần còn lại của năm 1 = (dự phóng năm − lũy kế quý thực) chia theo mùa vụ → tổng quý khớp tổng năm."""
    if not fc or not shares:
        return []
    w = seasonal_weights(quarters)
    real = [c for c in (quarters or []) if not c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()]
    out = []
    for f in fc[:2]:
        y = str(f["Năm"])
        done = [c for c in real if str(c["period"])[:4] == y]
        months_done = {str(c["period"])[4:6] for c in done}
        rest = [m for m in ("03", "06", "09", "12") if m not in months_done]
        for c in done:
            np_ = _n(c.get("net_profit"))
            out.append({"Quý": f"{y}.{str(c['period'])[4:6]}", "Loại": "Thực", "DT (억)": _n(c.get("revenue")),
                        "LN HĐ (억)": _n(c.get("op_profit")), "LN ròng (억)": np_,
                        "EPS": _n(c.get("eps")) if c.get("eps") is not None else (np_ * 1e8 / shares if np_ is not None else None)})
        if not rest:
            continue
        rem = {k: (f.get(k2) or 0) - sum((_n(c.get(k3)) or 0) for c in done)
               for k, k2, k3 in (("rev", "Doanh thu (억)", "revenue"), ("op", "LN HĐ (억)", "op_profit"),
                                 ("np", "LN ròng (억)", "net_profit"))}
        wsum = sum(w[m] for m in rest)
        for m in rest:
            s = w[m] / wsum
            out.append({"Quý": f"{y}.{m}", "Loại": "Dự phóng", "DT (억)": rem["rev"] * s,
                        "LN HĐ (억)": rem["op"] * s if f.get("LN HĐ (억)") is not None else None,
                        "LN ròng (억)": rem["np"] * s, "EPS": rem["np"] * s * 1e8 / shares})
    return out


# ============================================================================ FCFF-DCF
def invested_capital(c: dict) -> Optional[float]:
    """Vốn đầu tư hoạt động (억) = TSCĐ hữu hình + vô hình + vốn lưu động hoạt động (TS ngắn hạn − tiền − tiền gửi
    ngắn hạn − nợ ngắn hạn, sàn 0)."""
    ppe = _n(c.get("ppe")) or 0.0
    intang = _n(c.get("intangibles")) or 0.0
    nwc = ((_n(c.get("current_assets")) or 0) - (_n(c.get("cash")) or 0) - (_n(c.get("st_deposits")) or 0)
           - (_n(c.get("current_liab")) or 0))
    ic = ppe + intang + max(nwc, 0.0)
    return ic if ic > 0 else None


def bridge(fs: dict) -> dict:
    """Cầu nối EV → vốn chủ từ BCTC DART (억): nợ vay, tiền, nợ ròng, lợi ích cổ đông thiểu số."""
    c = fs["cur"]
    debt = _n(c.get("borrowings")) or 0.0
    cash = (_n(c.get("cash")) or 0.0) + (_n(c.get("st_deposits")) or 0.0)
    eq, eqp = _n(c.get("equity")), _n(c.get("equity_parent"))
    minority = max((eq - eqp), 0.0) if (eq is not None and eqp is not None) else 0.0
    return {"debt": debt, "cash": cash, "net_debt": debt - cash, "minority": minority}


def wacc_of(ke: float, rf: float, market_cap_eok: float, debt_eok: float, tax: float) -> float:
    kd = rf + 0.015
    e, d = max(market_cap_eok, 0.0), max(debt_eok, 0.0)
    return ke if e + d <= 0 else e / (e + d) * ke + d / (e + d) * kd * (1 - tax)


def fcff_dcf(fc: list[dict], fs: dict, ke: float, rf: float, g: float, market_cap: float, shares: float,
             tax: Optional[float] = None) -> Optional[dict]:
    """FCFF-DCF có bảng tính từng năm. fc = pipeline.run_forecast (cần 'LN HĐ (억)'). None nếu thiếu dữ liệu."""
    if not fc or not fs or not shares or not market_cap:
        return None
    c = fs["cur"]
    rev0, ic0 = _n(c.get("revenue")), invested_capital(c)
    if not rev0 or not ic0 or any(f.get("LN HĐ (억)") is None for f in fc):
        return None
    if tax is None:
        pre, tx = _n(c.get("pretax")), _n(c.get("tax"))
        tax = min(max(tx / pre, 0.10), 0.30) if (pre and pre > 0 and tx is not None) else 0.22
    br = bridge(fs)
    mc_eok = market_cap / 1e8
    wacc = wacc_of(ke, rf, mc_eok, br["debt"], tax)
    if wacc <= g + 0.01:
        return None
    s2c = min(max(rev0 / ic0, S2C_BAND[0]), S2C_BAND[1])
    rows, prev_rev, ic, pv_sum = [], rev0, ic0, 0.0
    for t, f in enumerate(fc, 1):
        rev, ebit = f["Doanh thu (억)"], f["LN HĐ (억)"]
        nopat = ebit * (1 - tax)
        reinv = (rev - prev_rev) / s2c
        fcff = nopat - reinv
        df = 1 / (1 + wacc) ** t
        pv = fcff * df
        pv_sum += pv
        ic += reinv
        rows.append({"Năm": f["Năm"], "Doanh thu (억)": rev, "EBIT (억)": ebit, "Thuế %": tax * 100,
                     "NOPAT (억)": nopat, "Tái đầu tư (억)": reinv, "FCFF (억)": fcff,
                     "ROIC %": nopat / ic * 100 if ic > 0 else None, "Hệ số chiết khấu": df, "PV FCFF (억)": pv})
        prev_rev = rev
    n = len(fc)
    nopat_n = rows[-1]["NOPAT (억)"]
    if nopat_n <= 0:
        # vĩnh viễn hóa một khoản lỗ là vô nghĩa — không tính giá trị cuối kỳ, không công bố
        return {"value": None, "rows": rows, "bridge": [], "assumptions": {"WACC %": wacc * 100, "Thuế %": tax * 100},
                "tv_share": None, "wacc": wacc, "ev": None, "equity": None,
                "note": f"EBIT năm cuối dự phóng ≤ 0 ({rows[-1]['EBIT (억)']:,.0f}억) → không tính giá trị cuối kỳ. "
                        "Sửa biên LN HĐ dài hạn ở bảng dự phóng nếu lỗ không phải trạng thái bền."}
    roic_n = (nopat_n / ic) if ic > 0 else wacc
    ronic = min(max(roic_n, wacc), RONIC_CAP)
    reinv_rate = g / ronic if ronic > 0 else 0.0
    fcff_tv = nopat_n * (1 + g) * (1 - reinv_rate)
    tv = fcff_tv / (wacc - g)
    pv_tv = tv / (1 + wacc) ** n
    ev = pv_sum + pv_tv
    equity = ev - br["net_debt"] - br["minority"]
    value = equity * 1e8 / shares if equity > 0 else None
    return {"value": value, "rows": rows,
            "bridge": [{"Khoản mục": k, "억원": v} for k, v in (
                ("Σ PV FCFF", pv_sum), ("PV giá trị cuối kỳ", pv_tv), ("= Giá trị doanh nghiệp (EV)", ev),
                ("− Nợ vay", br["debt"]), ("+ Tiền & tiền gửi ngắn hạn", br["cash"]),
                ("− Lợi ích cổ đông thiểu số", br["minority"]), ("= Giá trị vốn chủ", equity))],
            "assumptions": {"WACC %": wacc * 100, "Ke %": ke * 100, "g dài hạn %": g * 100, "Thuế %": tax * 100,
                            "Doanh thu / vốn đầu tư": s2c, "Vốn đầu tư gốc (억)": ic0,
                            "RONIC dài hạn %": ronic * 100, "Tỷ lệ tái đầu tư cuối kỳ %": reinv_rate * 100,
                            "FCFF năm cuối kỳ +1 (억)": fcff_tv, "Giá trị cuối kỳ (억)": tv},
            "tv_share": abs(pv_tv) / abs(ev) if ev else None, "wacc": wacc, "ev": ev, "equity": equity}


# ============================================================================ EV/EBIT forward
def ev_ebit_of(market_cap: Optional[float], ebit_eok: Optional[float], fs: Optional[dict]) -> Optional[float]:
    """EV/EBIT của 1 công ty: (vốn hóa + nợ ròng + thiểu số) / EBIT năm gần nhất. None nếu EBIT ≤ 0."""
    if not market_cap or not ebit_eok or ebit_eok <= 0 or not fs:
        return None
    br = bridge(fs)
    ev = market_cap / 1e8 + br["net_debt"] + br["minority"]
    return ev / ebit_eok if ev > 0 else None


def ttm_ebit(quarters: list[dict]) -> Optional[float]:
    """EBIT (LN HĐ) 4 quý THỰC gần nhất (억)."""
    q = [c for c in (quarters or []) if not c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()][-4:]
    if len(q) < 4 or any(c.get("op_profit") is None for c in q):
        return None
    return sum(c["op_profit"] for c in q)


def ev_ebit_ttm(ebit_ttm: Optional[float], fs: dict, peer_multiples: list[dict], shares: float) -> Optional[dict]:
    """EBIT 4 quý gần nhất × trung vị EV/EBIT (4 quý) của đối thủ trong nước (≥ 3 mã) − nợ ròng − thiểu số.
    Cùng cơ sở TTM cho mã và đối thủ: nhân EBIT DỰ PHÓNG với bội số tính trên EBIT QUÁ KHỨ thổi phồng giá trị khi
    lợi nhuận đang tăng vọt (Samsung 2026: EBIT dự phóng gấp 6,7× năm 2025)."""
    if not fs or not shares or ebit_ttm is None:
        return None
    ok = [p for p in peer_multiples if p.get("EV/EBIT") and EV_EBIT_BAND[0] < p["EV/EBIT"] <= EV_EBIT_BAND[1]]
    if len(ok) < V.MIN_PEERS:
        return None
    med = V._median([p["EV/EBIT"] for p in ok])
    ebit1 = ebit_ttm
    if ebit1 <= 0:
        return None
    br = bridge(fs)
    ev = med * ebit1
    equity = ev - br["net_debt"] - br["minority"]
    return {"value": equity * 1e8 / shares if equity > 0 else None, "median": med, "n": len(ok),
            "rows": [{"Khoản mục": k, "Giá trị": v} for k, v in (
                ("EBIT 4 quý gần nhất (억)", ebit1), ("× EV/EBIT (4 quý) trung vị đối thủ", med),
                ("= EV (억)", ev), ("− Nợ ròng (억)", br["net_debt"]), ("− Thiểu số (억)", br["minority"]),
                ("= Vốn chủ (억)", equity))]}
