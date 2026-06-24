"""
KCI Financials — lấy báo cáo tài chính 4 năm / 4 quý gần nhất từ Naver, suy ra
bảng cân đối (nợ, tổng tài sản, nợ/tài sản) và chấm điểm sức khỏe tài chính theo
tiêu chuẩn đầu tư.

Nguồn (ổn định, không cần KRX login):
  GET /api/stock/{code}/finance/annual   -> 3 năm thực + 1 năm dự phóng (consensus)
  GET /api/stock/{code}/finance/quarter  -> các quý gần nhất (+ 1 quý dự phóng)

Đơn vị: 매출액/영업이익/당기순이익... = 억원 (×100 triệu KRW); EPS/BPS/DPS = 원;
ROE/부채비율/당좌비율/유보율/biên LN = %.
"""
from __future__ import annotations
from typing import Optional
import requests

try:
    import dart_api
except Exception:                      # module phụ; thiếu cũng không sao
    dart_api = None

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
_BASE = "https://m.stock.naver.com/api/stock"
_TIMEOUT = 10

# title (Hàn) trong rowList -> field
_ROW_MAP = {
    "매출액": "revenue", "영업이익": "op_profit", "당기순이익": "net_profit",
    "지배주주순이익": "ctrl_net", "영업이익률": "op_margin", "순이익률": "net_margin",
    "ROE": "roe", "부채비율": "debt_ratio", "당좌비율": "quick_ratio",
    "유보율": "retention", "EPS": "eps", "PER": "per", "BPS": "bps",
    "PBR": "pbr", "주당배당금": "dps",
}

# nhãn tiếng Việt để hiển thị (đúng thứ tự ưu tiên)
LABELS_VI = {
    "revenue": "Doanh thu", "op_profit": "LN hoạt động", "net_profit": "LN thuần",
    "ctrl_net": "LN ròng (cổ đông)", "op_margin": "Biên LN HĐ %",
    "net_margin": "Biên LN ròng %", "roe": "ROE %", "debt_ratio": "Nợ/VCSH %",
    "quick_ratio": "Tỷ lệ thanh toán nhanh %", "retention": "Tỷ lệ tích lũy %",
    "eps": "EPS", "per": "PER", "bps": "BPS", "pbr": "PBR", "dps": "Cổ tức/cp",
    "assets": "Tổng tài sản (억)", "liabilities": "Nợ (억)", "equity": "VCSH (억)",
}


def _f(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "N/A", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse(period_json: dict) -> list[dict]:
    fi = period_json.get("financeInfo") or {}
    titles = fi.get("trTitleList") or []
    rows = fi.get("rowList") or []
    by_title = {r.get("title"): r.get("columns", {}) for r in rows}
    cols = []
    for t in titles:                       # API trả theo thứ tự thời gian
        key = t.get("key")
        rec = {"period": key, "label": t.get("title"),
               "is_consensus": t.get("isConsensus") == "Y"}
        for kr, field in _ROW_MAP.items():
            cell = by_title.get(kr, {}).get(key)
            rec[field] = _f(cell.get("value") if isinstance(cell, dict) else cell)
        cols.append(rec)
    return cols


def fetch_financials(ticker: str) -> dict:
    """Trả {'annual': [...], 'quarter': [...]} theo thứ tự thời gian tăng dần."""
    out = {"annual": [], "quarter": []}
    for ptype in ("annual", "quarter"):
        try:
            r = requests.get(f"{_BASE}/{ticker}/finance/{ptype}",
                             headers=_H, timeout=_TIMEOUT)
            r.raise_for_status()
            out[ptype] = _parse(r.json())
        except Exception:
            out[ptype] = []
    return out


def last_n(cols: list[dict], n: int = 4) -> list[dict]:
    """n kỳ gần nhất (giữ thứ tự thời gian tăng dần)."""
    return cols[-n:] if cols else []


def latest_actual(cols: list[dict]) -> Optional[dict]:
    """Kỳ THỰC (không phải dự phóng) gần nhất."""
    actual = [c for c in cols if not c["is_consensus"]]
    return actual[-1] if actual else None


def forward_eps(annual: list[dict], quarter: list[dict]) -> Optional[float]:
    """EPS dự tính = EPS của kỳ consensus (ưu tiên năm)."""
    for cols in (annual, quarter):
        for c in reversed(cols):
            if c["is_consensus"] and c.get("eps") is not None:
                return c["eps"]
    return None


def balance_sheet(market_cap: Optional[float], price: Optional[float],
                  bps: Optional[float], debt_ratio: Optional[float]) -> dict:
    """Suy ra nợ / tổng tài sản / nợ-trên-tài-sản từ 부채비율 + BPS + số cp.

        số cp        ≈ market_cap / price
        VCSH (equity) ≈ BPS × số cp
        Nợ           = VCSH × (부채비율/100)
        Tổng tài sản  = VCSH + Nợ = VCSH × (1 + 부채비율/100)
        Nợ/Tài sản   = 부채비율 / (100 + 부채비율)

    Trả các giá trị tuyệt đối theo 억원 (×100 triệu KRW) cho đồng bộ Naver.
    """
    out = {"equity": None, "liabilities": None, "assets": None,
           "debt_to_assets": None, "debt_ratio": debt_ratio}
    if debt_ratio is not None and debt_ratio >= 0:
        out["debt_to_assets"] = round(debt_ratio / (100 + debt_ratio) * 100, 1)
    if market_cap and price and bps and price > 0:
        shares = market_cap / price
        equity_won = bps * shares
        eq = equity_won / 1e8                       # -> 억원
        out["equity"] = round(eq)
        if debt_ratio is not None and debt_ratio >= 0:
            out["liabilities"] = round(eq * debt_ratio / 100)
            out["assets"] = round(eq * (1 + debt_ratio / 100))
    return out


def balance_sheet_of(period: Optional[dict], market_cap: Optional[float],
                     price: Optional[float], bps: Optional[float],
                     debt_ratio: Optional[float]) -> dict:
    """Bảng cân đối: ưu tiên số THỰC từ DART (자산총계/부채총계) nếu period có;
    nếu không thì suy ra từ 부채비율 × VCSH (ước tính). Cờ `source` cho biết nguồn."""
    if period and period.get("assets") is not None and period.get("liabilities") is not None:
        a, l = period["assets"], period["liabilities"]
        return {"equity": period.get("equity"), "liabilities": round(l),
                "assets": round(a), "debt_ratio": period.get("debt_ratio"),
                "debt_to_assets": round(l / a * 100, 1) if a else None,
                "source": "DART"}
    out = balance_sheet(market_cap, price, bps, debt_ratio)
    out["source"] = "Ước tính"
    return out


# ---------------------------------------------------------------------------
# Dự phóng bằng công thức (deterministic) + chuỗi 5 năm (gộp DART nếu có)
# ---------------------------------------------------------------------------
def _median(xs: list[float]) -> Optional[float]:
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _shares_estimate(reals: list[dict], market_cap: Optional[float],
                     price: Optional[float]) -> Optional[float]:
    """Số cổ phiếu: ưu tiên suy từ LN thuần / EPS (cùng kỳ), fallback vốn hóa/giá."""
    for c in reversed(reals):
        np_, eps = c.get("net_profit"), c.get("eps")
        if np_ and eps and np_ > 0 and eps > 0:   # cùng dấu dương → số cp hợp lệ
            return np_ * 1e8 / eps                 # 억원 -> 원 rồi chia EPS (원/cp)
    if market_cap and price and price > 0:
        return market_cap / price
    return None


def _next_label(reals: list[dict]) -> str:
    if reals:
        try:
            return f"{int(reals[-1]['period'][:4]) + 1}12"
        except Exception:
            pass
    return "FCST"


def project_forecast(reals: list[dict], market_cap: Optional[float] = None,
                     price: Optional[float] = None) -> Optional[dict]:
    """Dự phóng 1 năm tới bằng CÔNG THỨC minh bạch (không dùng consensus Naver):

      • Doanh thu: CAGR các năm thực, kẹp [-20%, +40%]  →  DT(t+1) = DT(t)·(1+g)
      • LN HĐ / LN thuần: biên LN median (ổn định, loại năm bất thường) × DT dự phóng
      • EPS: LN thuần dự phóng / số cp (suy từ LN/EPS cùng kỳ)
      • BPS: BPS(t) + EPS dự phóng − cổ tức/cp(t)   (lợi nhuận giữ lại)
      • ROE: EPS/BPS dự phóng

    Trả period dict (is_consensus=True, is_forecast=True) hoặc None nếu thiếu dữ liệu.
    """
    reals = [c for c in reals if not c.get("is_consensus")]
    revs = [c.get("revenue") for c in reals if c.get("revenue") is not None]
    if len(revs) < 2:
        return None
    n = len(revs)
    g = (revs[-1] / revs[0]) ** (1 / (n - 1)) - 1 if revs[0] and revs[0] > 0 else 0.0
    g = max(-0.20, min(0.40, g))
    rev_fc = revs[-1] * (1 + g)
    opm = _median([c["op_profit"] / c["revenue"] for c in reals
                   if c.get("op_profit") is not None and c.get("revenue")])
    npm = _median([c["net_profit"] / c["revenue"] for c in reals
                   if c.get("net_profit") is not None and c.get("revenue")])
    op_fc = rev_fc * opm if opm is not None else None
    net_fc = rev_fc * npm if npm is not None else None
    shares = _shares_estimate(reals, market_cap, price)
    last = reals[-1]
    eps_fc = net_fc * 1e8 / shares if (net_fc is not None and shares) else None
    dps = last.get("dps") or 0
    bps_last = last.get("bps")
    bps_fc = (bps_last + eps_fc - dps) if (bps_last is not None and eps_fc is not None) \
        else bps_last
    roe_fc = eps_fc / bps_fc * 100 if (eps_fc and bps_fc) else None
    return {
        "period": _next_label(reals), "label": _next_label(reals)[:4],
        "is_consensus": True, "is_forecast": True, "_cagr": round(g * 100, 1),
        "revenue": round(rev_fc) if rev_fc is not None else None,
        "op_profit": round(op_fc) if op_fc is not None else None,
        "net_profit": round(net_fc) if net_fc is not None else None,
        "op_margin": round(opm * 100, 2) if opm is not None else None,
        "net_margin": round(npm * 100, 2) if npm is not None else None,
        "eps": round(eps_fc) if eps_fc is not None else None,
        "bps": round(bps_fc) if bps_fc is not None else None,
        "roe": round(roe_fc, 2) if roe_fc is not None else None,
        "debt_ratio": last.get("debt_ratio"),
    }


def _next_q_label(reals: list[dict]) -> str:
    if reals:
        try:
            y, m = int(reals[-1]["period"][:4]), int(reals[-1]["period"][4:6])
            m += 3
            if m > 12:
                m -= 12
                y += 1
            return f"{y}{m:02d}"
        except Exception:
            pass
    return "FCST"


def project_forecast_quarter(quarters: list[dict], market_cap: Optional[float] = None,
                             price: Optional[float] = None) -> Optional[dict]:
    """Dự phóng 1 QUÝ tới bằng công thức, có tính MÙA VỤ:

      • ≥5 quý thực: tăng trưởng YoY của quý gần nhất (so quý cùng kỳ năm trước),
        áp lên quý cùng mùa năm trước  →  DT = DT(quý cùng mùa, năm trước)·(1+YoY)
      • 4 quý: lấy trung bình 4 quý gần nhất (trung hòa mùa vụ)
      • Biên LN median × DT; EPS = LN/số cp; BPS = BPS(t)+EPS dự phóng (giữ lại).
    """
    reals = [c for c in quarters if not c.get("is_consensus")]
    revs = [c.get("revenue") for c in reals if c.get("revenue") is not None]
    if len(revs) < 2:
        return None
    last = reals[-1]
    if len(reals) >= 5 and reals[-4].get("revenue") and reals[-5].get("revenue"):
        yoy = revs[-1] / reals[-5]["revenue"] - 1            # quý gần nhất vs cùng kỳ
        yoy = max(-0.30, min(0.50, yoy))
        rev_fc = reals[-4]["revenue"] * (1 + yoy)            # quý cùng mùa năm trước
    else:
        rev_fc = sum(revs[-4:]) / len(revs[-4:])
    opm = _median([c["op_profit"] / c["revenue"] for c in reals
                   if c.get("op_profit") is not None and c.get("revenue")])
    npm = _median([c["net_profit"] / c["revenue"] for c in reals
                   if c.get("net_profit") is not None and c.get("revenue")])
    op_fc = rev_fc * opm if opm is not None else None
    net_fc = rev_fc * npm if npm is not None else None
    shares = _shares_estimate(reals, market_cap, price)
    eps_fc = net_fc * 1e8 / shares if (net_fc is not None and shares) else None
    bps_last = last.get("bps")
    bps_fc = (bps_last + eps_fc) if (bps_last is not None and eps_fc is not None) else bps_last
    return {
        "period": _next_q_label(reals), "label": _next_q_label(reals),
        "is_consensus": True, "is_forecast": True,
        "revenue": round(rev_fc) if rev_fc is not None else None,
        "op_profit": round(op_fc) if op_fc is not None else None,
        "net_profit": round(net_fc) if net_fc is not None else None,
        "op_margin": round(opm * 100, 2) if opm is not None else None,
        "net_margin": round(npm * 100, 2) if npm is not None else None,
        "eps": round(eps_fc) if eps_fc is not None else None,
        "bps": round(bps_fc) if bps_fc is not None else None,
        "roe": round(eps_fc / bps_fc * 100, 2) if (eps_fc and bps_fc) else None,
        "debt_ratio": last.get("debt_ratio"),
    }


def build_quarter_series(quarters: list[dict], market_cap: Optional[float] = None,
                         price: Optional[float] = None, n: int = 4) -> list[dict]:
    """n quý thực gần nhất + 1 cột dự phóng bằng công thức."""
    reals = [c for c in quarters if not c.get("is_consensus")][-n:]
    fc = project_forecast_quarter(quarters, market_cap, price)
    return reals + ([fc] if fc else [])


def _merge_dart(naver_annual: list[dict], ticker: str) -> list[dict]:
    """Gộp số THỰC từ DART (4-6 năm, có 자산총계/부채총계) lên khung Naver (theo năm).
    DART là số gốc nộp cơ quan QL nên ghi đè doanh thu/LN/cân đối; giữ lại các tỉ số
    thị trường (EPS/BPS/PER/PBR/ROE) của Naver cho năm trùng."""
    base = {c["period"][:4]: dict(c) for c in naver_annual
            if not c.get("is_consensus")}
    if dart_api is not None and dart_api.available():
        try:
            for d in dart_api.annual_financials(ticker, years=6):
                y = d["period"][:4]
                tgt = base.get(y) or {"period": d["period"], "label": y,
                                      "is_consensus": False}
                for k in ("revenue", "op_profit", "net_profit", "assets",
                          "liabilities", "equity", "debt_ratio"):
                    if d.get(k) is not None:
                        tgt[k] = d[k]
                if tgt.get("revenue"):
                    if tgt.get("net_profit") is not None:
                        tgt["net_margin"] = round(tgt["net_profit"] / tgt["revenue"] * 100, 2)
                    if tgt.get("op_profit") is not None:
                        tgt["op_margin"] = round(tgt["op_profit"] / tgt["revenue"] * 100, 2)
                tgt["is_consensus"] = False
                tgt["_dart"] = True
                base[y] = tgt
        except Exception:
            pass
    return [base[y] for y in sorted(base.keys())]


def build_annual_series(ticker: str, naver_annual: list[dict],
                        market_cap: Optional[float] = None,
                        price: Optional[float] = None, years: int = 5) -> list[dict]:
    """Chuỗi năm để hiển thị: tối đa `years` năm THỰC (DART nếu có, fallback Naver)
    + 1 cột dự phóng bằng công thức. Thứ tự thời gian tăng dần."""
    reals = _merge_dart(naver_annual, ticker)[-years:]
    fc = project_forecast(reals, market_cap, price)
    return reals + ([fc] if fc else [])


def forecast_eps(annual: list[dict], market_cap: Optional[float] = None,
                 price: Optional[float] = None) -> Optional[float]:
    """EPS dự tính bằng công thức (ưu tiên), fallback consensus Naver nếu công thức N/A."""
    fc = project_forecast([c for c in annual if not c.get("is_consensus")],
                          market_cap, price)
    if fc and fc.get("eps") is not None:
        return fc["eps"]
    for c in reversed(annual):
        if c.get("is_consensus") and c.get("eps") is not None:
            return c["eps"]
    return None


# ---------------------------------------------------------------------------
# Đánh giá bảng cân đối theo tiêu chuẩn đầu tư
# ---------------------------------------------------------------------------
_FIN_SECTORS = {"Bank", "Insurance", "Brokerage"}
_FIN_INDUSTRIES = {"은행", "증권", "생명보험", "손해보험", "카드", "기타금융", "창업투자"}


def assess_financials(annual: list[dict], sector: Optional[str] = None,
                      industry_kr: Optional[str] = None) -> dict:
    """Chấm sức khỏe tài chính theo tiêu chuẩn đầu tư phổ biến.

    Tiêu chí (phi tài chính): Nợ/VCSH <100% tốt · Thanh toán nhanh >100% · ROE >10% ·
    Biên LN ròng >0 · LN thuần dương & ổn định · Doanh thu tăng trưởng.
    Ngành tài chính (bank/bảo hiểm/chứng khoán): bỏ tiêu chí đòn bẩy (bản chất cao).

    Trả {rating, score, max, checks:[{name,value,verdict,note}], summary}.
    """
    is_fin = (sector in _FIN_SECTORS) or (industry_kr in _FIN_INDUSTRIES)
    actual = [c for c in annual if not c["is_consensus"]]
    last = actual[-1] if actual else None
    checks: list[dict] = []
    score = 0
    maxs = 0

    def add(name, value, good: bool | None, note=""):
        nonlocal score, maxs
        if good is None:
            checks.append({"name": name, "value": value, "verdict": "—", "note": note})
            return
        maxs += 1
        score += 1 if good else 0
        checks.append({"name": name, "value": value,
                       "verdict": "✅" if good else "⚠️", "note": note})

    if last is None:
        return {"rating": "Thiếu dữ liệu", "score": 0, "max": 0,
                "checks": [], "summary": "Không có báo cáo tài chính."}

    # 1) Nợ/VCSH
    dr = last.get("debt_ratio")
    if is_fin:
        add("Nợ/VCSH (đòn bẩy)", dr, None,
            "Ngành tài chính: đòn bẩy cao là bản chất — không áp chuẩn này.")
    elif dr is not None:
        add("Nợ/VCSH < 100%", f"{dr:.0f}%", dr < 100,
            "An toàn <100%, chấp nhận <200%, rủi ro nếu cao." if dr < 200
            else "⚠️ >200%: đòn bẩy cao, rủi ro tài chính.")
    # 2) Thanh toán nhanh
    qr = last.get("quick_ratio")
    if not is_fin and qr is not None:
        add("Thanh toán nhanh > 100%", f"{qr:.0f}%", qr >= 100,
            "Đủ tài sản ngắn hạn trả nợ ngắn hạn.")
    # 3) ROE
    roe = last.get("roe")
    if roe is not None:
        add("ROE > 10%", f"{roe:.1f}%", roe >= 10,
            "Sinh lời trên vốn tốt." if roe >= 15 else "")
    # 4) Biên LN ròng dương
    nm = last.get("net_margin")
    if nm is not None:
        add("Biên LN ròng > 0", f"{nm:.1f}%", nm > 0)
    # 5) LN thuần dương & ổn định (mọi năm thực)
    nets = [c.get("net_profit") for c in actual if c.get("net_profit") is not None]
    if nets:
        all_pos = all(v > 0 for v in nets)
        add(f"LN thuần dương ({len(nets)} năm)", "흑자" if all_pos else "có năm lỗ",
            all_pos, "Lợi nhuận ổn định." if all_pos else "Có năm thua lỗ.")
    # 6) Doanh thu tăng trưởng (năm cuối vs năm đầu trong dữ liệu thực)
    revs = [c.get("revenue") for c in actual if c.get("revenue") is not None]
    if len(revs) >= 2:
        growth = revs[-1] > revs[0]
        cagr = (revs[-1] / revs[0]) ** (1 / (len(revs) - 1)) - 1 if revs[0] > 0 else None
        add("Doanh thu tăng trưởng", f"{cagr*100:.1f}%/năm" if cagr is not None else "—",
            growth, "Xu hướng tăng." if growth else "Doanh thu đi ngang/giảm.")

    rating = "🟢 An toàn" if maxs and score / maxs >= 0.75 else \
             "🟡 Trung bình" if maxs and score / maxs >= 0.5 else "🔴 Cần thận trọng"
    summary = f"Đạt {score}/{maxs} tiêu chí" + (" (ngành tài chính)" if is_fin else "")
    return {"rating": rating, "score": score, "max": maxs,
            "checks": checks, "summary": summary}
