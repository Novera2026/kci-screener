"""Nhất quán mô hình theo lý thuyết (rà soát 18/11/2026, C5): đẳng thức toán, không cần dữ liệu thị trường, chạy offline.

Dự phóng lợi nhuận sạch (clean surplus): EPS_t = ROE × BPS đầu kỳ, DPS_t = payout × EPS_t, BPS cuối = BPS đầu + EPS − DPS.
Khi đó DDM với payout cuối kỳ 1 − g/ROE (ROE < Ke → g = 0, trả hết) phải bằng RIM có giá trị cuối kỳ Gordon.
"""
import itertools

import pytest

import pipeline as P
import valuation as V

N = 5
ROES = (0.04, 0.08, 0.12, 0.20)
PAYOUTS = (0.0, 0.30, 0.60)
GS = (0.01, 0.02)
KES = (0.065, 0.09)
GRID = list(itertools.product(ROES, PAYOUTS, GS, KES))


def _fc(b0, roe, payout, n=N):
    fc, b = [], b0
    for t in range(n):
        e = roe * b
        d = e * payout
        fc.append({"Năm": str(2026 + t), "EPS": e, "DPS": d, "BPS đầu kỳ": b, "BPS cuối kỳ": b + e - d,
                   "ROE %": roe * 100})
        b += e - d
    return fc


def _rim_gordon(b0, fc, ke, g):
    """RIM: B0 + Σ RI_t/(1+Ke)^t + RI_{N+1}/(Ke − g)/(1+Ke)^N, RI_{N+1} = (ROE − Ke)·B_N; ROE < Ke → g = 0."""
    roe = fc[-1]["EPS"] / fc[-1]["BPS đầu kỳ"]
    g_eff = g if roe >= ke else 0.0
    pv = sum((f["EPS"] - ke * f["BPS đầu kỳ"]) / (1 + ke) ** t for t, f in enumerate(fc, 1))
    bn = fc[-1]["BPS cuối kỳ"]
    return b0 + pv + (roe - ke) * bn / (ke - g_eff) / (1 + ke) ** len(fc)


@pytest.mark.parametrize("roe,payout,g,ke", GRID)
def test_ddm_explicit_equals_rim_gordon(roe, payout, g, ke):
    b0 = 50_000.0
    fc = _fc(b0, roe, payout)
    out = P.ddm_explicit(fc, ke, g)
    if payout == 0:
        assert out is None                       # không trả cổ tức → không có DDM
        return
    assert out["value"] == pytest.approx(_rim_gordon(b0, fc, ke, g), rel=1e-3)


@pytest.mark.parametrize("roe,payout,g,ke", GRID)
def test_steady_state_ddm_equals_dcf_earnings(roe, payout, g, ke):
    """Giai đoạn đầu cũng ở trạng thái dừng (payout 1 − g/ROE): DDM tường minh = DDM 2 giai đoạn = dcf_earnings."""
    g_eff = g if roe >= ke else 0.0
    po = 1 - g_eff / roe
    b0 = 50_000.0
    fc = _fc(b0, roe, po)
    eps0 = fc[0]["EPS"] / (1 + g_eff)            # EPS năm gốc: dcf_earnings tăng EPS g ngay từ năm 1
    bps0 = eps0 / roe
    ddm = P.ddm_explicit(fc, ke, g)["value"]
    dcf = V.dcf_earnings(eps0, ke, g_eff, N, g, roe=roe)
    two = V.ddm_two_stage(eps0 * po, ke, g_eff, N, g, eps=eps0, bps=bps0)
    assert ddm == pytest.approx(_rim_gordon(b0, fc, ke, g), rel=1e-3)
    assert dcf == pytest.approx(ddm, rel=1e-3)
    assert two == pytest.approx(ddm, rel=1e-3)


@pytest.mark.parametrize("roe,payout,g,ke", GRID)
def test_book_and_dividend_models_vs_book(roe, payout, g, ke):
    """ROE > Ke ⇒ mô hình sổ sách/cổ tức > BPS; ROE < Ke ⇒ < BPS."""
    b0 = 50_000.0
    fc = _fc(b0, roe, payout)
    g_eff = g if roe >= ke else 0.0
    vals = {"RIM Gordon": _rim_gordon(b0, fc, ke, g),
            "P/B-ROE": V.justified_pb_value(b0, roe, ke, g_eff),
            "RIM nhiều giai đoạn": V.rim_multistage(b0, roe, ke, N, g_eff, payout=payout if roe >= ke else 1.0),
            "dcf_earnings": V.dcf_earnings(roe * b0, ke, roe * (1 - payout), N, g, roe=roe)}
    if payout > 0:
        eps, dps = roe * b0, roe * b0 * payout
        vals["DDM tường minh"] = P.ddm_explicit(fc, ke, g)["value"]
        vals["DDM Gordon"] = V.ddm_gordon(dps, ke, g, eps=eps, bps=b0)
    for name, v in vals.items():
        assert v is not None, name
        assert (v > b0) if roe > ke else (v < b0), (name, v)


@pytest.mark.parametrize("ke", KES)
@pytest.mark.parametrize("g", GS)
def test_ddm_gordon_terminal_payout(ke, g):
    eps, bps = 6_000.0, 100_000.0                # ROE 6% < Ke → g = 0, trả hết: DDM = EPS/Ke
    assert V.ddm_gordon(eps * 0.4, ke, g, eps=eps, bps=bps) == pytest.approx(eps / ke)
    eps = 20_000.0                               # ROE 20% > Ke → EPS(1+g)(1 − g/ROE)/(Ke − g)
    assert V.ddm_gordon(eps * 0.4, ke, g, eps=eps, bps=bps) == pytest.approx(
        eps * (1 + g) * (1 - g / 0.20) / (ke - g))


@pytest.mark.parametrize("g", GS)
@pytest.mark.parametrize("ke", KES)
def test_continuity_at_roe_equals_ke(ke, g):
    """Quy tắc ROE < Ke → g = 0 không tạo bước nhảy: tại ROE = Ke, P/B-ROE và DDM tường minh đều = BPS."""
    b0, eps_ = 50_000.0, 1e-9
    lo = V.justified_pb_value(b0, ke - eps_, ke, 0.0)
    hi = V.justified_pb_value(b0, ke + eps_, ke, g)
    assert lo == pytest.approx(b0) and hi == pytest.approx(b0)
    d_lo = P.ddm_explicit(_fc(b0, ke - eps_, 0.5), ke, g)["value"]
    d_hi = P.ddm_explicit(_fc(b0, ke + eps_, 0.5), ke, g)["value"]
    assert d_lo == pytest.approx(b0, rel=1e-6) and d_hi == pytest.approx(b0, rel=1e-6)


def test_terminal_dividend_rule():
    assert V.terminal_dividend(10.0, 100.0, 0.08, 0.02) == pytest.approx((10.0 * (1 - 0.02 / 0.10), 0.02))
    assert V.terminal_dividend(5.0, 100.0, 0.08, 0.02) == pytest.approx((5.0, 0.0))     # ROE < Ke → trả hết
    assert V.terminal_dividend(-1.0, 100.0, 0.08, 0.02) is None
    assert V.terminal_dividend(10.0, 0.0, 0.08, 0.02) is None


@pytest.mark.parametrize("roe", (0.12, 0.20))
def test_fcff_dcf_no_debt_matches_dcf_earnings(roe):
    """FCFF-DCF không nợ, không tiền, sales-to-capital khớp ROIC ≈ dcf_earnings × số cp.

    Sai số ≤ 0,5%: FCFF-DCF đo ROIC cuối kỳ trên vốn đầu tư CUỐI năm N (= ROIC/(1+g)), dcf_earnings dùng ROE
    trên vốn đầu kỳ — khác quy ước thời điểm, không phải lỗi mô hình."""
    import dcf as D
    ke, g, tax, m, rev0, shares = 0.09, 0.02, 0.22, 0.10, 10_000.0, 1e7
    s2c = roe / (m * (1 - tax) * (1 + g))        # ROIC đầu kỳ = NOPAT_t / IC_{t-1} = roe
    fc = [{"Năm": str(2026 + t), "Doanh thu (억)": rev0 * (1 + g) ** (t + 1),
           "LN HĐ (억)": rev0 * (1 + g) ** (t + 1) * m} for t in range(N)]
    fs = {"cur": {"revenue": rev0, "ppe": rev0 / s2c, "equity": 1.0, "equity_parent": 1.0}}
    mc = 5_000.0 * 1e8
    out = D.fcff_dcf(fc, fs, ke, 0.03, g, mc, shares, tax=tax)
    eps0 = m * (1 - tax) * rev0 * 1e8 / shares
    ref = V.dcf_earnings(eps0, ke, g, N, g, roe=roe)
    assert out["wacc"] == pytest.approx(ke)
    assert out["value"] == pytest.approx(ref, rel=5e-3)


@pytest.mark.parametrize("roe", (0.04, 0.20))
def test_value_stock_ddm_uses_terminal_payout(roe):
    """Ngành Telecom (auto DDM): DDM Gordon/2 giai đoạn của lõi KCI theo payout cuối kỳ, không kéo payout lịch sử."""
    a = V.Assumptions()
    ke, g = a.ke_stock(None), a.g_term_capped()
    bps = 50_000.0
    eps, dps = roe * bps, roe * bps * 0.4
    row = {"ticker": "999990", "name": "SYN", "price": bps, "eps": eps, "bps": bps, "dps": dps,
           "sector": "Telecom", "industry_kr": None, "beta": None, "market_cap": 1e12}
    m = V.value_stock(row, a).methods
    if roe < ke:
        assert m["DDM (Gordon)"] == pytest.approx(eps / ke)
        assert m["DDM (Gordon)"] < bps
    else:
        assert m["DDM (Gordon)"] == pytest.approx(eps * (1 + g) * (1 - g / roe) / (ke - g))
        assert m["DDM (Gordon)"] > bps and m["DDM 2 giai đoạn"] > bps


def test_ddm_loss_year_gets_no_ddm():
    """Rà soát D2: có EPS/BPS mà EPS ≤ 0 (năm lỗ) hoặc BPS ≤ 0 → không có DDM, không quay về payout lịch sử kéo vô hạn
    (trước đó mã lỗ ra ~40k và được công bố, mã lãi mỏng ra ~12 và bị chặn). Thiếu hẳn EPS/BPS → giữ cách cũ."""
    ke, g = 0.08, 0.02
    for eps, bps in ((-50.0, 1000.0), (0.0, 1000.0), (50.0, -10.0), (50.0, 0.0)):
        assert V.ddm_gordon(100.0, ke, g, eps=eps, bps=bps) is None
        assert V.ddm_two_stage(100.0, ke, 0.05, 5, g, eps=eps, bps=bps) is None
    old = 100.0 * 1.02 / 0.06
    assert V.ddm_gordon(100.0, ke, g) == pytest.approx(old)
    assert V.ddm_gordon(100.0, ke, g, eps=None, bps=1000.0) == pytest.approx(old)
    assert V.ddm_gordon(100.0, ke, g, eps=float("nan"), bps=1000.0) == pytest.approx(old)
    assert V.ddm_two_stage(100.0, ke, 0.05, 5, g, eps=None, bps=None) == pytest.approx(
        V.ddm_two_stage(100.0, ke, 0.05, 5, g))
    # ddm_explicit: EPS năm cuối âm hoặc BPS đầu kỳ âm dù DPS > 0 → None
    fc = [{"EPS": -50.0, "DPS": 40.0, "BPS đầu kỳ": 1000.0, "BPS cuối kỳ": 910.0}]
    assert P.ddm_explicit(fc, ke, g) is None
    fc = [{"EPS": 50.0, "DPS": 40.0, "BPS đầu kỳ": -5.0, "BPS cuối kỳ": 5.0}]
    assert P.ddm_explicit(fc, ke, g) is None


@pytest.mark.parametrize("eps", (-100.0, -1.0))
def test_value_stock_telecom_loss_no_ddm(eps):
    """Telecom lỗ, vẫn trả cổ tức: DDM Gordon/2 giai đoạn = None, không cao hơn mã lãi mỏng (= EPS/Ke)."""
    a = V.Assumptions()
    row = {"ticker": "999990", "name": "SYN", "price": 40_000, "eps": eps, "bps": 77_912, "dps": 2_400,
           "sector": "Telecom", "industry_kr": None, "beta": None, "market_cap": 1e13}
    m = V.value_stock(row, a).methods
    assert m["DDM (Gordon)"] is None and m["DDM 2 giai đoạn"] is None
    row["eps"] = 100.0
    thin = V.value_stock(row, a)
    assert thin.methods["DDM (Gordon)"] == pytest.approx(100.0 / thin.ke)
