"""
KCI Valuation — module định giá deterministic (tính bằng công thức, không bịa số).

Bám theo "KCI Valuation Master Spec":
  - §2/§8: chọn phương pháp theo entity type (recommend_method).
  - §3   : intrinsic — S-RIM (RIM Hàn), P/B–ROE justified, DDM, DCF-earnings (FCFE proxy).
  - §4   : relative — P/E, P/B so với median ngành.
  - §5   : house assumptions Hàn (Rf=KGB10Y, ERP 6.5%, beta clamp 0.5–1.8, g cap 2.5%).

Nguyên tắc (đồng bộ spec):
  - Intrinsic làm base, multiples chỉ cross-check.
  - Thiếu data thì trả None + hạ confidence, KHÔNG bịa.
  - Số học deterministic; phần diễn giải để tầng UI/LLM.

Chỉ phụ thuộc thư viện chuẩn (math). Không thêm dependency.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------------
# §5 — House assumptions (Korea, KRW nominal). Tất cả đều override được từ UI.
# ----------------------------------------------------------------------------
@dataclass
class Assumptions:
    rf: float = 0.032          # KGB 10Y ~ 3.2% (cập nhật theo thời điểm)
    erp: float = 0.065         # Equity Risk Premium house = 6.5%
    beta: float = 1.0          # clamp [0.5, 1.8]
    g_terminal: float = 0.020  # tăng trưởng vĩnh viễn, cap 2.5% & ≤ Rf
    g_high: float = 0.08       # tăng trưởng giai đoạn đầu (DCF/DDM 2 stage)
    years_high: int = 5        # số năm giai đoạn tăng trưởng cao
    persistence: float = 0.90  # hệ số duy trì siêu lợi nhuận S-RIM (w): 1.0/0.9/0.8
    payout: Optional[float] = None  # tỷ lệ chi trả cổ tức; None = suy từ DPS/EPS

    G_CAP = 0.025              # hard cap §5

    def ke(self) -> float:
        """Cost of equity (CAPM): Ke = Rf + β×ERP, β kẹp [0.5, 1.8]."""
        return self.ke_with(self.beta)

    def ke_with(self, beta: Optional[float] = None) -> float:
        """Ke với beta tùy chọn (per-stock). None → dùng beta mặc định. β kẹp [0.5, 1.8]."""
        b = self.beta if beta is None else beta
        b = min(max(b, 0.5), 1.8)
        return self.rf + b * self.erp

    def g_term_capped(self) -> float:
        """g terminal: cap 2.5% và không vượt Rf (§5)."""
        return min(self.g_terminal, self.G_CAP, self.rf)


# ----------------------------------------------------------------------------
# Tiện ích
# ----------------------------------------------------------------------------
def _pos(x) -> Optional[float]:
    """Trả x nếu là số > 0, ngược lại None (để chặn EPS/BPS âm/thiếu)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _num(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# §3.4 — S-RIM (Residual Income Model, biến thể Hàn phổ biến)
# ----------------------------------------------------------------------------
def srim(bps: float, roe: float, ke: float, w: float = 0.90) -> Optional[float]:
    """Fair value/share = BPS + siêu lợi nhuận duy trì với hệ số w.

        RI   = (ROE − Ke) × BPS
        Fair = BPS + RI × w / (1 + Ke − w)

    w = 1.0  → siêu lợi nhuận vĩnh viễn (Fair = BPS + RI/Ke).
    w = 0.9/0.8 → giả định siêu lợi nhuận phai dần (thận trọng hơn).
    """
    bps = _pos(bps)
    roe = _num(roe)
    if bps is None or roe is None or ke is None:
        return None
    ri = (roe - ke) * bps
    denom = 1 + ke - w
    if denom <= 0:
        return None
    v = bps + ri * w / denom
    return v if v > 0 else None   # §6.6: per-share ≤ 0 = model vỡ


# ----------------------------------------------------------------------------
# §3.4 / §4.6 — Justified P/B–ROE (RIM 1 giai đoạn có tăng trưởng)
# ----------------------------------------------------------------------------
def justified_pb_value(bps: float, roe: float, ke: float, g: float) -> Optional[float]:
    """Fair = BPS × (ROE − g)/(Ke − g). Xương sống định giá bank Hàn."""
    bps = _pos(bps)
    roe = _num(roe)
    # Công thức chỉ hợp lệ khi Ke > g VÀ ROE > g (ROE ≤ g ⇒ giá trị âm = model vỡ).
    if bps is None or roe is None or ke is None or ke <= g or roe <= g:
        return None
    return bps * (roe - g) / (ke - g)


def rim_multistage(bps: float, roe: float, ke: float, years: int, g_term: float,
                   payout: Optional[float] = None, g_cap: float = 0.15) -> Optional[float]:
    """RIM NHIỀU GIAI ĐOẠN (tường minh, đúng mô hình sách giáo khoa):

      • Lăn vốn chủ:   B(t) = B(t-1) × (1 + g),  g = ROE×(1−payout) (tăng trưởng bền vững)
      • Thặng dư:      RI(t) = (ROE − Ke) × B(t-1)
      • Giá trị:       Fair = BPS + Σ RI(t)/(1+Ke)^t  +  TV/(1+Ke)^N
                       TV   = RI(N+1)/(Ke − g_term)   (terminal tăng đều g_term)

    Khác S-RIM/justified ở chỗ DÙNG tăng trưởng vốn chủ THỰC trong N năm rồi mới về
    g_term — nên cho kết quả KHÁC (thường cao hơn cho DN ROE cao, giữ lại nhiều LN).
    g (giai đoạn cao) bị kẹp ≤ g_cap (mặc định 15%) để tránh nổ. Trả None nếu model vỡ.
    """
    bps = _pos(bps)
    roe = _num(roe)
    if bps is None or roe is None or ke is None or ke <= g_term:
        return None
    payout = 0.0 if payout is None else min(max(payout, 0.0), 1.0)
    g = min(roe * (1 - payout), g_cap)
    excess = roe - ke                       # có thể âm (ROE < Ke → định giá dưới book)
    B, pv = bps, 0.0
    for t in range(1, years + 1):
        pv += excess * B / (1 + ke) ** t    # RI(t) trên vốn ĐẦU kỳ
        B *= (1 + g)                         # lăn vốn chủ sang năm sau
    tv = excess * B / (ke - g_term)         # RI(N+1) trên vốn cuối, Gordon
    pv += tv / (1 + ke) ** years
    v = bps + pv
    return v if v > 0 else None


# ----------------------------------------------------------------------------
# Normalized / mid-cycle (cho CYCLICAL) — dùng MEDIAN qua chu kỳ, khử đỉnh/đáy (§3.1/§6.6)
# ----------------------------------------------------------------------------
# Ngành cyclical: ROE/biên 1 năm vô nghĩa (đỉnh hoặc đáy chu kỳ) → phải normalize.
CYCLICAL_SECTORS = {"Semiconductor", "Steel/Chemicals", "Shipbuilding",
                    "Auto", "Battery"}


def _median(xs: list) -> Optional[float]:
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def normalized_metrics(history: Optional[list]) -> dict:
    """Từ chuỗi BCTC nhiều năm (history) → MEDIAN ROE/biên qua chu kỳ.

    history: list dict năm THỰC có net_profit, equity, revenue, op_profit
    (đơn vị bất kỳ — ratio là không thứ nguyên). Median khử năm đỉnh/đáy.
    """
    reals = [h for h in (history or []) if not h.get("is_consensus")]
    roes, nms, oms = [], [], []
    for h in reals:
        np_, eq = h.get("net_profit"), h.get("equity")
        rev, op = h.get("revenue"), h.get("op_profit")
        if np_ is not None and eq:
            roes.append(np_ / eq)
        if np_ is not None and rev:
            nms.append(np_ / rev)
        if op is not None and rev:
            oms.append(op / rev)
    return {"median_roe": _median(roes), "median_net_margin": _median(nms),
            "median_op_margin": _median(oms), "n_years": len(reals),
            "mean_roe": (sum(roes) / len(roes)) if roes else None}


def implied_roe_from_price(price: float, bps: float, ke: float, g: float) -> Optional[float]:
    """REVERSE của justified P/B–ROE: thị giá đang NGỤ Ý mức ROE nào?

        price = BPS × (ROE_implied − g)/(Ke − g)
        ⇒ ROE_implied = (price/BPS) × (Ke − g) + g

    Dùng cho boom premium: so ROE thị trường ngụ ý với ROE hiện tại để biết
    thị trường đang kỳ vọng siêu chu kỳ tới mức nào (§6.3 reverse DCF).
    """
    price = _pos(price)
    bps = _pos(bps)
    if price is None or bps is None or ke is None:
        return None
    return (price / bps) * (ke - g) + g


# ----------------------------------------------------------------------------
# §3.3 — DDM (Gordon 1 giai đoạn & 2 giai đoạn)
# ----------------------------------------------------------------------------
def ddm_gordon(dps: float, ke: float, g: float) -> Optional[float]:
    """P = DPS×(1+g)/(Ke − g). Cần cổ tức > 0 và Ke > g."""
    dps = _pos(dps)
    if dps is None or ke is None or ke <= g:
        return None
    return dps * (1 + g) / (ke - g)


def ddm_two_stage(dps: float, ke: float, g_high: float, years: int,
                  g_term: float) -> Optional[float]:
    """DDM 2 giai đoạn: cổ tức tăng g_high trong `years` năm rồi về g_term vĩnh viễn."""
    dps = _pos(dps)
    if dps is None or ke is None or ke <= g_term:
        return None
    pv, d = 0.0, dps
    for t in range(1, years + 1):
        d *= (1 + g_high)
        pv += d / (1 + ke) ** t
    d_term = d * (1 + g_term)
    tv = d_term / (ke - g_term)
    pv += tv / (1 + ke) ** years
    return pv


# ----------------------------------------------------------------------------
# §3.2 — DCF earnings (FCFE proxy). ROUGH: dùng EPS thay FCFE.
# ----------------------------------------------------------------------------
def dcf_earnings(eps: float, ke: float, g_high: float, years: int,
                 g_term: float) -> Optional[float]:
    """Chiết khấu chuỗi EPS (proxy cho dòng tiền cổ đông).

    ⚠️ Đây là proxy thô: EPS ≠ FCFE (bỏ qua reinvestment & nợ). Chỉ dùng
    cross-check / cảm nhận độ lớn, KHÔNG phải full DCF. Cần EPS > 0, Ke > g_term.
    """
    eps = _pos(eps)
    if eps is None or ke is None or ke <= g_term:
        return None
    pv, e = 0.0, eps
    for t in range(1, years + 1):
        e *= (1 + g_high)
        pv += e / (1 + ke) ** t
    e_term = e * (1 + g_term)
    tv = e_term / (ke - g_term)
    pv += tv / (1 + ke) ** years
    return pv


# ----------------------------------------------------------------------------
# §4.1 / §4.6 — Relative valuation (so với median ngành)
# ----------------------------------------------------------------------------
def relative_pe_value(eps: float, sector_median_pe: Optional[float]) -> Optional[float]:
    eps = _pos(eps)
    pe = _num(sector_median_pe)
    if eps is None or pe is None or pe <= 0:
        return None
    return eps * pe


def relative_pb_value(bps: float, sector_median_pb: Optional[float]) -> Optional[float]:
    bps = _pos(bps)
    pb = _num(sector_median_pb)
    if bps is None or pb is None or pb <= 0:
        return None
    return bps * pb


# ----------------------------------------------------------------------------
# EV/EBIT (relative) + FCFF-DCF (số liệu DART). EBIT/EV/CFO/CapEx đơn vị 억원;
# shares = số cp; trả GIÁ/cp (원). EBITDA→dùng EBIT vì DART không tách khấu hao.
# ----------------------------------------------------------------------------
def _wacc(ke: float, kd: float, tax: float, equity_val: float, debt_val: float) -> float:
    """WACC = (E/V)·Ke + (D/V)·Kd·(1−thuế). E=vốn hóa, D=nợ vay (cùng đơn vị)."""
    e = max(equity_val or 0.0, 0.0)
    d = max(debt_val or 0.0, 0.0)
    if e + d <= 0:
        return ke
    return e / (e + d) * ke + d / (e + d) * kd * (1 - tax)


def ev_ebit_value(ebit: float, net_debt: float, shares: float,
                  sector_median_ev_ebit: Optional[float]) -> Optional[float]:
    """Định giá tương đối: Fair EV = median(EV/EBIT ngành)×EBIT; Fair equity = EV − Nợ ròng.

    EBIT, net_debt theo 억원; shares = số cp → trả giá/cp (원). Cần EBIT > 0.
    """
    ebit = _pos(ebit)
    m = _num(sector_median_ev_ebit)
    if ebit is None or m is None or m <= 0 or not shares or shares <= 0:
        return None
    fair_eq = m * ebit - (net_debt or 0.0)        # 억원
    return fair_eq * 1e8 / shares if fair_eq > 0 else None


def fcff_value(cfo: float, capex: float, debt_val: float, net_debt: float,
               shares: float, ke: float, kd: float, tax: float,
               g_high: float, years: int, g_term: float,
               equity_val: float) -> Optional[float]:
    """FCFF-DCF (FCFF tính từ CFO — không cần tách khấu hao):

        FCFF = CFO + Lãi vay×(1−thuế) − CapEx      (Lãi vay ≈ Nợ vay×Kd, ổn định)
        EV   = Σ FCFF·(1+g)^t/(1+WACC)^t + Terminal
        Fair equity = EV − Nợ ròng ;  Giá/cp = equity/số cp

    CFO/CapEx/nợ theo 억원; trả giá/cp (원). None nếu FCFF gốc ≤ 0 hoặc model vỡ.
    """
    if cfo is None or capex is None or not shares or shares <= 0:
        return None
    interest = (debt_val or 0.0) * kd
    fcff0 = cfo + interest * (1 - tax) - capex      # 억원
    if fcff0 <= 0:                                   # đốt tiền → DCF earnings vô nghĩa
        return None
    w = _wacc(ke, kd, tax, equity_val, debt_val)
    if w <= g_term:
        return None
    pv, f = 0.0, fcff0
    for t in range(1, years + 1):
        f *= (1 + g_high)
        pv += f / (1 + w) ** t
    tv = f * (1 + g_term) / (w - g_term)
    pv += tv / (1 + w) ** years
    equity = pv - (net_debt or 0.0)                 # EV − nợ ròng
    return equity * 1e8 / shares if equity > 0 else None


# ----------------------------------------------------------------------------
# §2 / §8 — Engine khuyến nghị method theo entity type (sector)
# ----------------------------------------------------------------------------
# Mỗi sector: primary, cross-check, ghi chú chí mạng (rút gọn từ §7/§8),
# và `auto` = method TỰ ĐỘNG tin cậy nhất trong app này cho sector đó.
_SECTOR_GUIDE: dict[str, dict] = {
    "Bank": {
        "primary": "RIM / P/B–ROE", "cross": ["DDM", "P/E"],
        "auto": "P/B-ROE", "data_ok": True,
        "note": "Book-based. KHÔNG dùng FCFF-DCF. Normalize ROE (khử one-off provisioning). "
                "Theme Value-up (tăng payout/buyback) là catalyst rerating P/B.",
    },
    "Brokerage": {
        "primary": "P/B–ROE / RIM", "cross": ["P/E (normalized)", "DDM"],
        "auto": "P/B-ROE", "data_ok": True,
        "note": "Book-based. Normalize trading gain (one-off cao). Soi rủi ro real-estate PF.",
    },
    "Insurance": {
        "primary": "Embedded Value / Appraisal Value", "cross": ["P/EV", "RIM", "P/B"],
        "auto": "P/B-ROE", "data_ok": False,
        "note": "Chuẩn là Embedded Value (cần data CSM/IFRS17 — app chưa có). "
                "Tạm dùng P/B–ROE/RIM làm proxy, hạ confidence.",
    },
    "Holdco": {
        "primary": "NAV / SOTP (+ holdco discount)", "cross": ["P/B", "look-through P/E"],
        "auto": "P/B-relative", "data_ok": False,
        "note": "Cần định giá từng khoản nắm giữ (SOTP) — app chưa làm tự động. "
                "Korea discount lớn (30–60%). P/B chỉ là tham chiếu thô.",
    },
    "Semiconductor": {
        "primary": "Normalized/mid-cycle DCF", "cross": ["EV/EBITDA mid-cycle", "P/B–ROE"],
        "auto": "P/B-ROE", "data_ok": False,
        "note": "Cyclical: BẮT BUỘC normalized earnings, dùng median D&A/CapEx. "
                "Coi chừng boom premium (HBM/AI). Samsung nên SOTP. P/B–ROE là cross tốt.",
    },
    "Steel/Chemicals": {
        "primary": "Normalized/mid-cycle DCF", "cross": ["EV/EBITDA mid-cycle", "P/B–ROE"],
        "auto": "P/B-ROE", "data_ok": False,
        "note": "Commodity cyclical: dùng normalized, KHÔNG extrapolate đỉnh/đáy. "
                "POSCO/LG Chem có mảng pin → cân nhắc SOTP.",
    },
    "Battery": {
        "primary": "DCF + EV/GWh (capacity)", "cross": ["EV/EBITDA fwd", "EV/Sales"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Capex đi trước doanh thu nhiều năm → FCF âm kéo dài. Phụ thuộc IRA. "
                "DCF-earnings ở đây rất thô (chưa mô hình capex).",
    },
    "Auto": {
        "primary": "DCF (hoặc SOTP tách captive finance)", "cross": ["EV/EBITDA", "P/E"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Tách mảng tài chính (book-based) khỏi ô tô (DCF). P/E Hàn thấp kinh niên "
                "(Korea discount) → reverse-check & catalyst governance.",
    },
    "Shipbuilding": {
        "primary": "DCF dựa backlog", "cross": ["EV/EBITDA", "P/B"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Backlog-driven, ghi nhận theo tiến độ. Nhạy giá thép & FX. "
                "Backlog cao chưa chắc biên cao (rủi ro provisioning).",
    },
    "Utility": {
        "primary": "DDM / RAB (regulated asset base)", "cross": ["P/B", "EV/EBITDA"],
        "auto": "DDM", "data_ok": True,
        "note": "Đặt cược chính sách tariff, không phải vận hành. KEPCO lỗ khi nhiên liệu cao "
                "mà tariff không theo kịp → earnings/book méo.",
    },
    "Telecom": {
        "primary": "DCF + DDM", "cross": ["EV/EBITDA", "EV/subscriber"],
        "auto": "DDM", "data_ok": True,
        "note": "Trưởng thành, cash-generative, cổ tức cao → DDM hợp. Catalyst: AI/datacenter pivot, payout.",
    },
    "Pharma/Biotech": {
        "primary": "DCF (có lãi) hoặc rNPV (pipeline)", "cross": ["EV/EBITDA", "EV/Sales"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Pre-profit → rNPV (cần PoS pipeline, app chưa có). Đừng cộng dồn pipeline "
                "như chắc chắn. SamBio định giá như CDMO capacity.",
    },
    "Consumer/Retail": {
        "primary": "DCF", "cross": ["P/E", "PEG"],
        "auto": "DCF-earnings", "data_ok": True,
        "note": "Brand-driven, dòng tiền ổn định. Cosmetics/duty-free nhạy khách Trung Quốc.",
    },
    "Entertainment": {
        "primary": "DCF + scenario", "cross": ["EV/EBITDA", "P/E"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Doanh thu gắn IP/nghệ sĩ, khó dự báo (concert/album/fandom). "
                "Rủi ro concentration; nghĩa vụ quân sự là biến lịch thật.",
    },
    "Internet/Platform": {
        "primary": "SOTP", "cross": ["DCF hợp nhất", "EV/Sales"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Nhiều mảng + subs niêm yết → SOTP gần như bắt buộc (app chưa tự làm). "
                "Tránh double-count subsidiary; áp holdco discount.",
    },
    "Defense": {
        "primary": "DCF dựa backlog", "cross": ["EV/EBITDA", "P/E"],
        "auto": "DCF-earnings", "data_ok": False,
        "note": "Backlog-driven, hưởng chu kỳ chi tiêu quốc phòng. Đã pricing bao nhiêu cần reverse-check.",
    },
}

# Method "auto" mặc định khi không rõ sector → operating company chuẩn (§8 dòng 1).
_DEFAULT_GUIDE = {
    "primary": "DCF (FCFF)", "cross": ["P/E", "EV/EBITDA"],
    "auto": "DCF-earnings", "data_ok": False,
    "note": "Operating company chuẩn — primary là DCF. App dùng DCF-earnings (thô) + "
            "relative P/E làm cross-check.",
}


# Map ngành KRX (업종, tiếng Hàn từ Naver) -> bucket EN ở trên, để mã ngoài
# registry curated vẫn được gợi ý method hợp lý. Ngành không map -> default.
_KR_INDUSTRY_BUCKET: dict[str, str] = {
    "반도체와반도체장비": "Semiconductor", "디스플레이장비및부품": "Semiconductor",
    "디스플레이패널": "Semiconductor",
    "IT서비스": "Internet/Platform", "소프트웨어": "Internet/Platform",
    "게임엔터테인먼트": "Internet/Platform", "양방향미디어와서비스": "Internet/Platform",
    "인터넷과카탈로그소매": "Internet/Platform",
    "은행": "Bank", "카드": "Bank",
    "증권": "Brokerage", "창업투자": "Brokerage", "기타금융": "Brokerage",
    "손해보험": "Insurance", "생명보험": "Insurance",
    "복합기업": "Holdco",
    "자동차": "Auto", "자동차부품": "Auto",
    "화학": "Steel/Chemicals", "철강": "Steel/Chemicals", "비철금속": "Steel/Chemicals",
    "석유와가스": "Steel/Chemicals", "포장재": "Steel/Chemicals",
    "조선": "Shipbuilding",
    "우주항공과국방": "Defense",
    "가스유틸리티": "Utility", "전기유틸리티": "Utility", "복합유틸리티": "Utility",
    "무선통신서비스": "Telecom", "다각화된통신서비스": "Telecom",
    "제약": "Pharma/Biotech", "생물공학": "Pharma/Biotech",
    "생명과학도구및서비스": "Pharma/Biotech", "건강관리장비와용품": "Pharma/Biotech",
    "건강관리업체및서비스": "Pharma/Biotech", "건강관리기술": "Pharma/Biotech",
    "방송과엔터테인먼트": "Entertainment", "출판": "Entertainment",
    "화장품": "Consumer/Retail", "식품": "Consumer/Retail", "음료": "Consumer/Retail",
    "섬유,의류,신발,호화품": "Consumer/Retail", "백화점과일반상점": "Consumer/Retail",
    "호텔,레스토랑,레저": "Consumer/Retail", "담배": "Consumer/Retail",
    "식품과기본식료품소매": "Consumer/Retail", "전문소매": "Consumer/Retail",
}


def effective_sector(sector: Optional[str], industry_kr: Optional[str] = None) -> Optional[str]:
    """Sector EN curated nếu có; nếu không, suy từ 업종 KRV."""
    if sector:
        return sector
    if industry_kr:
        return _KR_INDUSTRY_BUCKET.get(industry_kr)
    return None


def recommend_method(sector: Optional[str], industry_kr: Optional[str] = None) -> dict:
    """Trả khuyến nghị method cho 1 sector (theo §2/§8). Có thể truyền 업종 KRV."""
    sec = effective_sector(sector, industry_kr)
    g = _SECTOR_GUIDE.get(sec or "", None)
    return dict(g) if g else dict(_DEFAULT_GUIDE)


# ----------------------------------------------------------------------------
# Orchestrator — định giá 1 mã, gom tất cả method + chọn fair value đề xuất
# ----------------------------------------------------------------------------
@dataclass
class ValuationResult:
    ticker: str
    name: Optional[str]
    sector: Optional[str]
    price: Optional[float]
    ke: float
    roe: Optional[float]
    methods: dict = field(default_factory=dict)   # tên method -> fair value
    primary: str = ""
    cross: list = field(default_factory=list)
    auto_method: str = ""
    fair_value: Optional[float] = None            # fair từ method auto đề xuất
    upside_pct: Optional[float] = None
    fwd_fair: Optional[float] = None              # fair theo EPS/ROE dự phóng
    fwd_upside_pct: Optional[float] = None
    implied_roe: Optional[float] = None           # ROE thị giá đang ngụ ý (reverse)
    confidence: str = "Medium"
    note: str = ""
    flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "ticker": self.ticker, "name": self.name, "sector": self.sector,
            "price": self.price, "Ke": round(self.ke, 4),
            "ROE": round(self.roe, 4) if self.roe is not None else None,
            "primary_method": self.primary,
            "auto_method": self.auto_method,
            "fair_value": round(self.fair_value) if self.fair_value else None,
            "upside_%": self.upside_pct,
            "fwd_fair": round(self.fwd_fair) if self.fwd_fair else None,
            "fwd_upside_%": self.fwd_upside_pct,
            "ROE_thị_trường_ngụ_ý": round(self.implied_roe * 100, 1)
                if self.implied_roe is not None else None,
            "confidence": self.confidence,
        }
        for k, v in self.methods.items():
            d[k] = round(v) if v else None
        d["flags"] = "; ".join(self.flags)
        return d


# nhãn hiển thị của các method auto -> key trong methods dict
_AUTO_LABEL = {
    "P/B-ROE": "P/B-ROE (justified)",
    "P/B-relative": "P/E vs median ngành",   # holdco: dùng relative làm proxy thô
    "DDM": "DDM (Gordon)",
    "DCF-earnings": "DCF earnings (thô)",
    "S-RIM": "S-RIM",
}


# Thứ hạng confidence để áp "trần" (cap không cho vượt mức cho phép).
_CONF_RANK = {"High": 3, "Medium": 2, "Low": 1}


def _cap_conf(conf: str, cap: Optional[str]) -> str:
    """Hạ conf xuống `cap` nếu conf đang cao hơn cap (dùng cho mã mới niêm yết)."""
    if not cap:
        return conf
    if _CONF_RANK.get(cap, 99) < _CONF_RANK.get(conf, 0):
        return cap
    return conf


def value_stock(row: dict, a: Assumptions,
                sector_median_pe: Optional[float] = None,
                sector_median_pb: Optional[float] = None,
                coverage_cap: Optional[str] = None,
                history: Optional[list] = None) -> ValuationResult:
    """Định giá 1 mã từ 1 dòng StockRow.to_dict() + giả định + median ngành.

    coverage_cap: trần confidence ('Medium'/'Low') theo độ phủ dữ liệu
    (coverage.assess_coverage(...)['confidence_cap']). Dùng cho mã mới niêm yết /
    thiếu lịch sử — không cho confidence vượt trần dù method có hội tụ."""
    _beta = _num(row.get("beta"))            # beta riêng từng mã (52T) nếu có
    ke = a.ke_with(_beta)
    g_term = a.g_term_capped()
    eps = _num(row.get("eps"))
    bps = _num(row.get("bps"))
    dps = _num(row.get("dps"))
    price = _num(row.get("price"))
    industry_kr = row.get("industry_kr")
    sector = row.get("sector")
    sec_eff = effective_sector(sector, industry_kr)
    sector_disp = sector or industry_kr   # hiển thị: EN curated, else 업종 KRV

    roe = (eps / bps) if (eps and bps and bps != 0) else None
    implied_roe = implied_roe_from_price(price, bps, ke, g_term)

    methods: dict = {}
    methods["S-RIM"] = srim(bps, roe, ke, w=a.persistence) if roe is not None else None
    methods["P/B-ROE (justified)"] = justified_pb_value(bps, roe, ke, g_term) \
        if roe is not None else None
    # RIM nhiều giai đoạn (tường minh): payout suy từ DPS/EPS (mặc định giữ lại hết).
    _payout = (dps / eps) if (eps and dps and eps > 0) else \
        (a.payout if a.payout is not None else 0.0)
    methods["RIM nhiều giai đoạn"] = rim_multistage(
        bps, roe, ke, a.years_high, g_term, payout=_payout) if roe is not None else None
    methods["DDM (Gordon)"] = ddm_gordon(dps, ke, g_term)
    methods["DDM 2 giai đoạn"] = ddm_two_stage(dps, ke, a.g_high, a.years_high, g_term)
    methods["DCF earnings (thô)"] = dcf_earnings(eps, ke, a.g_high, a.years_high, g_term)
    methods["P/E vs median ngành"] = relative_pe_value(eps, sector_median_pe)
    methods["P/B vs median ngành"] = relative_pb_value(bps, sector_median_pb)

    # --- Forward (dùng EPS/ROE dự phóng bằng công thức, truyền qua row) ---
    fwd_eps = _num(row.get("fwd_eps"))
    fwd_roe = _num(row.get("fwd_roe"))      # dạng thập phân (eps_dự phóng / bps)
    if fwd_eps is not None and fwd_eps > 0:
        methods["Forward P/E vs median ngành"] = relative_pe_value(fwd_eps, sector_median_pe)
    if fwd_roe is not None:
        methods["S-RIM (ROE dự phóng)"] = srim(bps, fwd_roe, ke, w=a.persistence)

    # --- Mid-cycle (normalized) cho CYCLICAL: median ROE qua chu kỳ (khử đỉnh/đáy) ---
    norm = normalized_metrics(history) if history else {"median_roe": None, "n_years": 0}
    mid_roe = norm.get("median_roe")
    used_midcycle = False
    if mid_roe is not None and bps is not None:
        methods["S-RIM (mid-cycle)"] = srim(bps, mid_roe, ke, w=a.persistence)
        methods["P/B-ROE (mid-cycle)"] = justified_pb_value(bps, mid_roe, ke, g_term)
        methods["EPS chuẩn hóa × P/E ngành"] = relative_pe_value(mid_roe * bps, sector_median_pe)

    is_cyclical = sec_eff in CYCLICAL_SECTORS

    guide = recommend_method(sector, industry_kr)
    auto_key = guide["auto"]
    auto_label = _AUTO_LABEL.get(auto_key, "S-RIM")
    fair = methods.get(auto_label)

    flags: list = []
    # Cyclical + đủ lịch sử (≥3 năm) → ƯU TIÊN mid-cycle thay cho trailing 1 năm (§3.1)
    if is_cyclical and norm.get("n_years", 0) >= 3:
        for mc in ("P/B-ROE (mid-cycle)", "S-RIM (mid-cycle)"):
            if methods.get(mc) is not None:
                auto_label, fair, used_midcycle = mc, methods[mc], True
                flags.append(f"cyclical → mid-cycle ROE median {mid_roe*100:.0f}% "
                             f"qua {norm['n_years']} năm (khử đỉnh/đáy chu kỳ)")
                break

    # Fallback chuỗi nếu method auto thiếu data
    if fair is None:
        for fb in ("P/B-ROE (mid-cycle)", "S-RIM (mid-cycle)", "S-RIM",
                   "P/B-ROE (justified)", "DCF earnings (thô)", "P/E vs median ngành"):
            if methods.get(fb) is not None:
                auto_label, fair = fb, methods[fb]
                flags.append(f"method chính thiếu data → dùng {fb}")
                break

    # --- Confidence (§9) ---
    conf = "Medium"
    if not used_midcycle and not guide.get("data_ok", False):
        conf = "Low"
        flags.append("entity cần data sâu hơn (DCF/SOTP/EV/rNPV) — đây chỉ là proxy")
    if used_midcycle:
        flags.append("đã chuẩn hóa earnings qua chu kỳ → tin cậy hơn trailing 1 năm")
    # data gap THẬT: không có cả ROE thường lẫn mid-cycle
    if (roe is None and mid_roe is None) or (eps is not None and eps <= 0 and mid_roe is None):
        conf = "Low"
        flags.append("EPS/ROE âm hoặc thiếu → earnings-based kém tin cậy")

    ref_roe = mid_roe if mid_roe is not None else roe   # ROE chuẩn để reverse-check

    upside = None
    if fair and price:
        upside = round((fair / price - 1) * 100, 1)
        # §6.3: gap lớn là TÍN HIỆU (boom premium/value trap) — CẢNH BÁO, KHÔNG tự hạ Low
        if upside <= -40:
            if implied_roe is not None and ref_roe is not None and ref_roe > 0:
                flags.append(
                    f"thị giá ngụ ý ROE ~{implied_roe*100:.0f}% vs chuẩn ~{ref_roe*100:.0f}% "
                    "→ thị trường định giá siêu chu kỳ/tăng trưởng")
            else:
                flags.append("thị giá ≫ fair: khả năng boom premium — reverse-check, đừng ép khớp")
        elif upside >= 60:
            flags.append("fair ≫ thị giá: khả năng value trap/data méo — kiểm lại")

    fwd_fair = (methods.get("Forward P/E vs median ngành")
                or methods.get("S-RIM (ROE dự phóng)"))
    fwd_upside = round((fwd_fair / price - 1) * 100, 1) if (fwd_fair and price) else None

    # §9/§14: trần confidence theo độ phủ dữ liệu (mã mới niêm yết / thiếu lịch sử)
    capped = _cap_conf(conf, coverage_cap)
    if capped != conf:
        flags.append(f"trần confidence theo độ phủ dữ liệu: {coverage_cap}")
        conf = capped

    return ValuationResult(
        ticker=row.get("ticker"), name=row.get("name"), sector=sector_disp,
        price=price, ke=ke, roe=roe, methods=methods,
        primary=guide["primary"], cross=guide["cross"], auto_method=auto_label,
        fair_value=fair, upside_pct=upside, fwd_fair=fwd_fair,
        fwd_upside_pct=fwd_upside, implied_roe=implied_roe, confidence=conf,
        note=guide["note"], flags=flags,
    )


def value_batch(df, a: Assumptions, agg=None):
    """Định giá cả DataFrame. Trả (list[ValuationResult], dict sector->median PE/PB)."""
    import pandas as pd  # local import để module này không buộc pandas khi dùng lẻ
    medians: dict = {}
    if df is not None and not df.empty and "sector" in df.columns:
        for sec, g in df.groupby("sector", dropna=False):
            medians[sec] = {
                "pe": g["per"].median() if "per" in g and g["per"].notna().any() else None,
                "pb": g["pbr"].median() if "pbr" in g and g["pbr"].notna().any() else None,
            }
    results = []
    for _, r in df.iterrows():
        row = r.to_dict()
        med = medians.get(row.get("sector"), {})
        results.append(value_stock(row, a, med.get("pe"), med.get("pb")))
    return results, medians
