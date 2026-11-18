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


FETCH_RETRY_WAITS = (0.5, 1.0)     # nghỉ trước mỗi lần thử lại (giây) khi Naver lỗi mạng / trả mã lỗi
FETCH_RETRY_TIMEOUT = 5            # hết giờ của lần thử lại (giây) — Naver đang chậm không kéo dài quét gấp 3


def fetch_financials(ticker: str) -> dict:
    """Trả {'annual': [...], 'quarter': [...]} theo thứ tự thời gian tăng dần.

    Mỗi endpoint thử lại tối đa 2 lần khi lỗi tạm thời (mạng, 429, 5xx; 4xx khác không thử lại). Vẫn lỗi → [] và thêm out['_error'] = {ptype: lý do}: rỗng do LỖI khác rỗng do
    không có dữ liệu (rà soát 18/11/2026 — Naver lỗi mạng từng làm app định giá trên số DART thiếu EPS/LN cổ đông mẹ
    mà vẫn Medium)."""
    import time
    out = {"annual": [], "quarter": []}
    errs: dict = {}
    for ptype in ("annual", "quarter"):
        for i in range(len(FETCH_RETRY_WAITS) + 1):
            try:
                r = requests.get(f"{_BASE}/{ticker}/finance/{ptype}",
                                 headers=_H, timeout=_TIMEOUT if i == 0 else FETCH_RETRY_TIMEOUT)
                r.raise_for_status()
                out[ptype] = _parse(r.json())
                errs.pop(ptype, None)
                break
            except Exception as e:
                out[ptype] = []
                errs[ptype] = type(e).__name__
                # chỉ thử lại lỗi tạm thời (mạng, hết giờ, 429, 5xx); 404/403… thử lại cũng vậy → dừng ngay
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code is not None and code != 429 and 400 <= code < 500:
                    break
                if i < len(FETCH_RETRY_WAITS):
                    time.sleep(FETCH_RETRY_WAITS[i])
    if errs:
        out["_error"] = errs
    return out


def fin_complete(fin) -> bool:
    """BCTC Naver dùng được để NHỚ (cache): không có '_error' và có ít nhất annual hoặc quarter. Kết quả lỗi là dict
    {'annual': [], 'quarter': [], '_error': …} — luôn truthy, nên `if v:` không đủ (rà soát cuối 18/11/2026)."""
    return (isinstance(fin, dict) and not fin.get("_error")
            and bool(fin.get("annual") or fin.get("quarter")))


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


def _trend_value(xs: list[float]) -> Optional[float]:
    """Giá trị đại diện CÓ XÉT XU HƯỚNG (cho biên LN dự phóng).

    - Chuỗi ĐƠN ĐIỆU (tăng dần / giảm dần rõ): trung bình có trọng số nghiêng về
      năm gần đây (1,2,…,n) → bám xu hướng, không bị 'kéo về giữa' như median.
      Tránh được lỗi: ngành chu kỳ đang hồi phục bị dự phóng tụt lợi nhuận.
    - Chuỗi NHIỄU (lên xuống thất thường): dùng median (bền với năm bất thường).
    """
    xs = [v for v in xs if v is not None]
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    inc = all(xs[i] <= xs[i + 1] for i in range(len(xs) - 1))
    dec = all(xs[i] >= xs[i + 1] for i in range(len(xs) - 1))
    if inc or dec:                       # xu hướng rõ → ưu tiên năm gần đây
        w = list(range(1, len(xs) + 1))
        return sum(v * wi for v, wi in zip(xs, w)) / sum(w)
    return _median(xs)                   # nhiễu → median


def _shares_estimate(reals: list[dict], market_cap: Optional[float],
                     price: Optional[float], field: str = "net_profit") -> Optional[float]:
    """Số cổ phiếu: ưu tiên suy từ LN (field) / EPS cùng kỳ, fallback vốn hóa/giá."""
    for c in reversed(reals):
        np_, eps = c.get(field), c.get("eps")
        if np_ and eps and np_ > 0 and eps > 0:   # cùng dấu dương → số cp hợp lệ
            return np_ * 1e8 / eps                 # 억원 -> 원 rồi chia EPS (원/cp)
    if market_cap and price and price > 0:
        return market_cap / price
    return None


# ---------------------------------------------------------------------------
# Cơ sở MỖI CỔ PHIẾU của dự phóng (rà soát 15/11/2026).
# Trước đây số cp = LN HỢP NHẤT năm trước ÷ EPS năm trước, dùng cho mọi năm dự phóng → ngầm giả định (a) số cp
# không đổi từ năm trước và (b) phần của cổ đông mẹ trong LN hợp nhất không đổi. Đo trên 200 mã lớn: (a) sai > 5% ở
# 29 mã (Samsung Biologics sau tách công ty 67,7 → 46,7 triệu cp; KT&G hủy cổ phiếu quỹ 121,5 → 106,5 triệu),
# (b) sai > 10% ở 37 mã (SK Inc 0,45 → 0,32; Doosan 0,30 → 0,62). Nay tách hai việc:
#   • share_basis : số cp quy đổi theo EPS = LN cổ đông mẹ ÷ EPS của QUÝ thực gần nhất (gồm cổ phiếu ưu đãi, đúng cơ
#                   sở EPS/BPS của Naver; giữa hai quý liên tiếp lệch trung vị 0,1%).
#   • parent_ratio: LN cổ đông mẹ ÷ LN hợp nhất — năm 1 theo các quý thực đã công bố, năm 2+ theo số lõi của các quý đó.
# Chuỗi KHÔNG có LN cổ đông mẹ (backtest point-in-time, dữ liệu thử) → số cp và tỷ lệ tính như cũ (LN ÷ EPS của năm có
# lãi gần nhất, tỷ lệ = 1): EPS dự phóng không đổi. (Riêng cổng "số cổ phiếu" của pipeline.data_quality nay kiểm cả khi
# năm gần nhất lỗ — trước đây bỏ qua trường hợp đó dù dự phóng vẫn dùng số cp của một năm cũ hơn.)
# ---------------------------------------------------------------------------
# LN của Naver làm tròn tới 1억, EPS tới 1 KRW → số cp suy ra có sai số tối đa 0,5/|LN| + 0,5/|EPS|. Kỳ nào sai số này
# > 2% thì không dùng (quý LN 3억 hiển thị từ 3,4억: lệch 12%; EPS 3 KRW: lệch 17%).
SHARE_BASIS_MAX_ERR = 0.02
PARENT_RATIO_BAND = (0.05, 1.5)  # tỷ lệ cổ đông mẹ dùng cho năm 2+ phải nằm trong dải này mới coi là suy được từ số liệu


def _q_reals(quarters: Optional[list[dict]]) -> list[dict]:
    """Các quý THỰC (bỏ cột đồng thuận), cũ → mới."""
    return sorted((c for c in (quarters or []) if not c.get("is_consensus")
                   and str(c.get("period", ""))[:6].isdigit()), key=lambda c: str(c["period"]))


def _implied_shares(c: dict, field: str = "ctrl_net", max_err: Optional[float] = None) -> Optional[float]:
    """Số cp ngầm định của 1 kỳ = LN (억) × 1e8 ÷ EPS. Cần LN và EPS khác 0, cùng dấu (kỳ lỗ vẫn dùng được).
    max_err: bỏ kỳ có sai số làm tròn (0,5/|LN| + 0,5/|EPS|) vượt ngưỡng này."""
    v, eps = c.get(field), c.get("eps")
    if not v or not eps or (v > 0) != (eps > 0):
        return None
    if max_err is not None and 0.5 / abs(v) + 0.5 / abs(eps) > max_err:
        return None
    return v * 1e8 / eps


def _has_parent_profit(reals: list[dict], quarters: Optional[list[dict]] = None) -> bool:
    return any(c.get("ctrl_net") is not None for c in list(reals) + list(quarters or []))


def share_basis(reals: list[dict], quarters: Optional[list[dict]] = None,
                market_cap: Optional[float] = None, price: Optional[float] = None) -> dict:
    """Số cổ phiếu dùng để đổi LN cổ đông mẹ ↔ EPS, MỚI NHẤT có thể.

    Thứ tự (chuỗi có LN cổ đông mẹ): quý thực gần nhất có sai số làm tròn ≤ 2% → năm thực gần nhất (cùng điều kiện) →
    vốn hóa ÷ giá → LN cổ đông mẹ ÷ EPS bất kể sai số. KHÔNG bao giờ lùi về LN HỢP NHẤT ÷ EPS: số cp đó đã phồng theo
    phần cổ đông thiểu số, nhân thêm tỷ lệ cổ đông mẹ (parent_ratio) là trừ thiểu số hai lần.
    Trả {shares, source, prev (cơ sở của năm thực gần nhất), listed (vốn hóa ÷ giá — chỉ cổ phiếu thường)}; prev và
    listed để data_quality so chéo, không dùng để tính."""
    reals = [c for c in reals if not c.get("is_consensus")]
    listed = market_cap / price if (market_cap and price and price > 0) else None
    if not _has_parent_profit(reals, quarters):                 # không tách cổ đông mẹ → cách cũ
        s = _shares_estimate(reals, market_cap, price)
        from_eps = any(c.get("net_profit") and c.get("eps") and c["net_profit"] > 0 and c["eps"] > 0 for c in reals)
        return {"shares": s, "source": "LN ròng ÷ EPS năm" if from_eps else "vốn hóa ÷ giá",
                "prev": s if from_eps else None, "listed": listed}
    prev, prev_y = None, None
    for c in reversed(reals):
        prev = _implied_shares(c, "ctrl_net", SHARE_BASIS_MAX_ERR)
        if prev:
            prev_y = str(c.get("period", ""))[:4]
            break
    qs = _q_reals(quarters)[-4:]
    for q in reversed(qs):
        s = _implied_shares(q, "ctrl_net", SHARE_BASIS_MAX_ERR)
        if s:
            p = str(q["period"])
            return {"shares": s, "source": f"quý {p[:4]}.{p[4:6]}", "prev": prev, "listed": listed}
    if prev:
        return {"shares": prev, "source": f"năm {prev_y}", "prev": prev, "listed": listed}
    if listed:
        return {"shares": listed, "source": "vốn hóa ÷ giá", "prev": None, "listed": listed}
    for c in list(reversed(qs)) + list(reversed(reals)):        # không có giá: chấp nhận sai số làm tròn lớn
        s = _implied_shares(c, "ctrl_net")
        if s:
            return {"shares": s, "source": "LN cổ đông mẹ ÷ EPS (sai số làm tròn lớn)", "prev": None, "listed": None}
    return {"shares": None, "source": "không xác định được", "prev": None, "listed": None}


def parent_ratio(reals: list[dict], ytd: Optional[dict] = None) -> dict:
    """Tỷ lệ LN cổ đông công ty mẹ ÷ LN hợp nhất, để đổi LN ròng dự phóng (hợp nhất) → EPS.

      • y1   — năm dự phóng đầu: theo các quý THỰC đã công bố (ytd = _ytd_run_rate) nếu có. Đây là số công bố nên
               giữ nguyên kể cả khi trái dấu (LN hợp nhất dương nhưng công ty mẹ lỗ).
      • rest — các năm sau: theo số LÕI của chính các quý đó (bỏ lãi/lỗ một lần); không có thì = y1.
      • Không có quý: tỷ lệ của năm thực gần nhất CÓ số cổ đông mẹ (năm mới nhất chỉ thiếu số này — cột sơ bộ của
        Naver, năm chỉ có ở DART — không phải là 'trái dấu'); năm đó trái dấu/ngoài dải → trung vị các năm hợp lệ → 1.
    reliable = False khi tỷ lệ cho năm 2+ KHÔNG suy được từ số liệu (trái dấu hoặc ngoài PARENT_RATIO_BAND) — cổng số
    của pipeline chặn các mô hình dựa trên dự phóng mỗi cổ phiếu. Chuỗi không có LN cổ đông mẹ → 1 (cách cũ)."""
    reals = [c for c in reals if not c.get("is_consensus")]
    lo, hi = PARENT_RATIO_BAND

    def ratio(c_, n_):
        return c_ / n_ if (c_ is not None and n_ and (c_ > 0) == (n_ > 0) and c_ != 0) else None

    def ok(x):
        return x is not None and lo <= x <= hi
    known = [c for c in reals if c.get("ctrl_net") is not None and c.get("net_profit") is not None]
    hist = [r for r in (ratio(c["ctrl_net"], c["net_profit"]) for c in known) if ok(r)]
    last = ratio(known[-1]["ctrl_net"], known[-1]["net_profit"]) if known else None
    fallback = last if ok(last) else (_median(hist) if hist else 1.0)
    if ytd and ytd.get("ctrl_net") is not None and ytd.get("net_profit"):
        # chỉ từ các quý THỰC của năm dự phóng (tổng đã công bố) — không lấy từ run-rate: ở k = 1 run-rate trộn cột Q4
        # năm trước của Naver (cả năm − 9 tháng) nên tỷ lệ từng vọt 1,78 (이수화학 20/08/2026) dù quý thực 1,01
        if ytd.get("ctrl_net_ytd") is not None and ytd.get("net_profit_ytd"):
            y1 = ytd["ctrl_net_ytd"] / ytd["net_profit_ytd"]
            core = ratio(ytd.get("ctrl_net_core_ytd"), ytd.get("net_profit_core_ytd"))
            rest = core if core is not None else ratio(ytd["ctrl_net_ytd"], ytd["net_profit_ytd"])
        else:
            y1 = ytd["ctrl_net"] / ytd["net_profit"]
            core = ratio(ytd.get("ctrl_net_core"), ytd.get("net_profit_core"))
            rest = core if core is not None else ratio(ytd["ctrl_net"], ytd["net_profit"])
        good = ok(rest)
        return {"y1": y1, "rest": rest if good else fallback, "last": last, "reliable": good,
                "raw": rest if rest is not None else y1, "source": f"{ytd.get('k')} quý thực"}
    if not known:
        return {"y1": 1.0, "rest": 1.0, "last": None, "reliable": True, "source": "LN ròng (không tách cổ đông mẹ)"}
    return {"y1": fallback, "rest": fallback, "last": last, "reliable": ok(last), "raw": last,
            "source": "năm thực gần nhất" if ok(last) else "trung vị các năm (năm gần nhất không dùng được)"}


def _next_label(reals: list[dict]) -> str:
    if reals:
        try:
            return f"{int(reals[-1]['period'][:4]) + 1}12"
        except Exception:
            pass
    return "FCST"


# ---------------------------------------------------------------------------
# Lãi/lỗ bất thường một lần (10/11/2026): LN thuần ≠ LN HĐ×(1−thuế) rất phổ biến và phần lớn LẶP LẠI (lãi công
# ty liên kết của Hyundai Mobis ~1,2× LN HĐ, lãi tiền gửi của DN nhiều tiền) → KHÔNG thay LN thuần bằng LN HĐ
# hàng loạt. Chỉ cờ một quý khi tỷ lệ LN thuần/LN HĐ lệch xa CHÍNH LỊCH SỬ của mã và phần lệch đáng kể so với
# doanh thu quý, hoặc LN thuần > doanh thu (Hynix Q2/2026: 93,9조 trên 79,3조).
# ---------------------------------------------------------------------------
ONE_OFF_REL = (2 / 3, 1.5)     # tỷ lệ quý ÷ tỷ lệ trung vị năm của mã ngoài dải này…
ONE_OFF_MIN_SHARE = 0.15       # …VÀ |LN thuần − LN HĐ × tỷ lệ trung vị| > 15% doanh thu quý
DEFAULT_CONV = 0.78            # LN thuần ≈ LN HĐ × (1 − 22% thuế) khi thiếu lịch sử


def conversion_ratio(reals: list[dict]) -> float:
    """Tỷ lệ LN thuần / LN HĐ trung vị của chính mã (năm thực có LN HĐ > 0 và LN thuần > 0), kẹp [0,3; 2].
    < 3 năm → DEFAULT_CONV: trung vị 2 năm mà 1 năm có lãi một lần thì 'tỷ lệ thường' tự nó đã méo (Alteogen
    2024–25: 2,39 / 1,36 → 1,87, làm biên lõi 96% > biên công bố 84%)."""
    xs = [c["net_profit"] / c["op_profit"] for c in reals
          if c.get("op_profit") and c["op_profit"] > 0 and c.get("net_profit") and c["net_profit"] > 0]
    if len(xs) < 3:
        return DEFAULT_CONV
    return min(max(_median(xs), 0.3), 2.0)


def one_off_core(c: dict, ratio: float) -> Optional[float]:
    """LN thuần 'lõi' của 1 kỳ nếu kỳ đó có lãi/lỗ bất thường (= LN HĐ × tỷ lệ của mã), không thì None."""
    np_, op, rev = c.get("net_profit"), c.get("op_profit"), c.get("revenue")
    if np_ is None or op is None or op <= 0 or not rev or rev <= 0:
        return None
    core = op * ratio
    if np_ > rev:
        return core
    rel = (np_ / op) / ratio
    if not ONE_OFF_REL[0] <= rel <= ONE_OFF_REL[1] and abs(np_ - core) > ONE_OFF_MIN_SHARE * rev:
        return core
    return None


QUARTER_BAND = (1 / 3, 3.0)    # doanh thu quý ÷ trung vị các quý lân cận ngoài dải → số quý không dùng được


def quarter_ok(q: dict, neighbors: Optional[list] = None) -> bool:
    """Cổng số cho một quý SUY RA (cột Q4 của Naver / Q4 DART = cả năm − 9 tháng): loại khi doanh thu ≤ 0, LN thuần >
    doanh thu, hoặc doanh thu ÷ trung vị doanh thu các quý lân cận ngoài QUARTER_BAND. neighbors: doanh thu các quý
    lân cận (bỏ số ≤ 0 / None; rỗng → không xét tỷ lệ). Dùng chung cho analysis.quarter_series và _ytd_run_rate."""
    rev, np_ = q.get("revenue"), q.get("net_profit")
    if rev is None:
        return True
    if rev <= 0 or (np_ is not None and np_ > rev):
        return False
    nb = sorted(v for v in (neighbors or []) if v is not None and v > 0)
    if nb:
        med = _median(nb)
        if not QUARTER_BAND[0] <= rev / med <= QUARTER_BAND[1]:
            return False
    return True


def _ytd_run_rate(reals: list[dict], quarters: Optional[list[dict]]) -> Optional[dict]:
    """Năm dự phóng ĐÃ CÓ quý thực công bố → ước cả năm = lũy kế các quý đã công bố
    + mỗi quý còn lại bằng trung bình 2 quý thực gần nhất (run-rate, không ngoại suy).

    Trả {revenue, op_profit, net_profit, k} (k = số quý đã công bố) hoặc None nếu chưa
    có quý nào của năm dự phóng / năm tài chính không kết thúc tháng 12 / thiếu số.

    Cột Q4 của Naver là 'cả năm − 9 tháng' (số suy ra): trong 2 quý gần nhất, quý Q4 không qua quarter_ok bị loại khỏi
    run-rate (ghi ở 'dropped'). Run-rate còn dùng quý của năm trước (k = 1): *_ytd = tổng các quý THỰC của năm dự phóng
    để parent_ratio không lấy tỷ lệ cổ đông mẹ từ quý năm trước."""
    if not quarters or not reals or str(reals[-1].get("period", ""))[4:6] != "12":
        return None
    fy = str(int(str(reals[-1]["period"])[:4]) + 1)
    q_real = sorted((c for c in quarters if not c.get("is_consensus")
                     and str(c.get("period", ""))[:6].isdigit()),
                    key=lambda c: str(c["period"]))
    ytd = [c for c in q_real if str(c["period"])[:4] == fy]
    k = len(ytd)
    if not 1 <= k <= 4:
        return None
    recent, dropped = [], []
    for c in q_real[-2:]:
        nb = [x.get("revenue") for x in q_real if x is not c]
        if str(c["period"])[4:6] == "12" and c not in ytd and not quarter_ok(c, nb):
            dropped.append(c["period"])
        else:
            recent.append(c)
    recent = recent or ytd
    ratio = conversion_ratio(reals)

    def core(c: dict, m: str):
        """LN của quý sau khi bỏ phần bất thường (chỉ cho LN thuần / LN cổ đông mẹ)."""
        v = c.get(m)
        if m not in ("net_profit", "ctrl_net") or v is None:
            return v
        cn = one_off_core(c, ratio)
        if cn is None:
            return v
        np_ = c.get("net_profit")
        return cn if m == "net_profit" else (cn * v / np_ if np_ else cn)

    out = {"k": k, "conv_ratio": ratio, "dropped": dropped,
           "one_offs": [{"period": c["period"], "net_profit": c["net_profit"],
                         "core": one_off_core(c, ratio)} for c in q_real
                        if one_off_core(c, ratio) is not None and c in (ytd + recent)]}
    for m in ("revenue", "op_profit", "net_profit", "ctrl_net"):
        vals = [c.get(m) for c in ytd]
        # quý còn lại ngoại suy theo 2 quý gần nhất ĐÃ BỎ lãi/lỗ bất thường — số công bố giữ nguyên (thật),
        # nhưng một lần không được nhân lên cho các quý chưa công bố
        rr = [core(c, m) for c in recent if c.get(m) is not None]
        if any(v is None for v in vals) or not rr:
            out[m] = None
            continue
        out[m] = sum(vals) + (4 - k) * sum(rr) / len(rr)
        if m in ("net_profit", "ctrl_net"):
            out[m + "_core"] = sum(core(c, m) for c in ytd) + (4 - k) * sum(rr) / len(rr)
            if any(c not in ytd for c in recent):     # run-rate có quý của năm trước (k = 1) → tỷ lệ theo quý thực
                out[m + "_ytd"] = sum(vals)
                out[m + "_core_ytd"] = sum(core(c, m) for c in ytd)
    return out if out.get("revenue") else None


def _rev_cagr(revs: list) -> Optional[float]:
    """CAGR doanh thu năm đầu → năm cuối (số thực). None khi < 2 năm hoặc năm đầu / năm cuối ≤ 0 — lũy thừa phân số
    của số âm ra số phức (rà soát 18/11/2026: năm cuối âm làm trang 🧮 sập)."""
    xs = [x for x in (revs or []) if x is not None]
    if len(xs) < 2 or xs[0] <= 0 or xs[-1] <= 0:
        return None
    return (xs[-1] / xs[0]) ** (1 / (len(xs) - 1)) - 1


def project_forecast(reals: list[dict], market_cap: Optional[float] = None,
                     price: Optional[float] = None,
                     quarters: Optional[list[dict]] = None) -> Optional[dict]:
    """Dự phóng 1 năm tới bằng CÔNG THỨC minh bạch (không dùng consensus Naver):

      • Doanh thu: CAGR các năm thực, kẹp [-20%, +40%]  →  DT(t+1) = DT(t)·(1+g)
      • LN HĐ / LN thuần: biên LN theo XU HƯỚNG (đơn điệu→nghiêng năm gần đây;
        nhiễu→median) × DT dự phóng — tránh under/over-forecast ngành chu kỳ
      • Có `quarters` và năm dự phóng đã có quý thực công bố → DT/LN = lũy kế quý thực
        + quý còn lại theo run-rate 2 quý gần nhất (_ytd_run_rate) thay cho CAGR/biên
        (vd tháng 10 đã có 2-3 quý của năm: không được bỏ qua số thực đó)
      • EPS: LN của CỔ ĐÔNG MẸ dự phóng ÷ số cp cơ sở EPS mới nhất (share_basis / parent_ratio — quý thực gần nhất;
        chuỗi không tách cổ đông mẹ thì LN thuần ÷ số cp suy từ LN/EPS năm gần nhất như trước)
      • BPS: BPS(t) + EPS dự phóng − cổ tức/cp(t)   (lợi nhuận giữ lại)
      • ROE: EPS/BPS dự phóng

    Trả period dict (is_consensus=True, is_forecast=True) hoặc None nếu thiếu dữ liệu.
    """
    reals = [c for c in reals if not c.get("is_consensus")]
    revs = [c.get("revenue") for c in reals if c.get("revenue") is not None]
    if len(revs) < 2:
        return None
    if reals[-1].get("revenue") is None:     # năm gốc thiếu doanh thu: không lấy DT năm trước gắn nhãn năm sau (I14)
        return None
    if revs[-1] <= 0:              # doanh thu năm gần nhất ≤ 0: không có gốc để dự phóng → thiếu dữ liệu
        return None
    g = _rev_cagr(revs) or 0.0
    g = max(-0.20, min(0.40, g))
    rev_fc = revs[-1] * (1 + g)
    # Biên LN dự phóng theo XU HƯỚNG (đơn điệu→nghiêng gần đây; nhiễu→median).
    # Giữ thứ tự thời gian để nhận diện xu hướng.
    opm = _trend_value([c["op_profit"] / c["revenue"] for c in reals
                        if c.get("op_profit") is not None and c.get("revenue")])
    npm = _trend_value([c["net_profit"] / c["revenue"] for c in reals
                        if c.get("net_profit") is not None and c.get("revenue")])
    op_fc = rev_fc * opm if opm is not None else None
    net_fc = rev_fc * npm if npm is not None else None
    basis = f"CAGR doanh thu {g * 100:+.1f}%/năm × biên LN xu hướng"
    ytd = _ytd_run_rate(reals, quarters)
    if ytd:
        rev_fc = ytd["revenue"]
        g = rev_fc / revs[-1] - 1 if revs[-1] and revs[-1] > 0 else g
        op_fc = ytd["op_profit"] if ytd["op_profit"] is not None else op_fc
        net_fc = ytd["net_profit"] if ytd["net_profit"] is not None else net_fc
        opm = op_fc / rev_fc if (op_fc is not None and rev_fc) else opm
        npm = net_fc / rev_fc if (net_fc is not None and rev_fc) else npm
        basis = (f"{ytd['k']} quý thực đã công bố"
                 + (f" + {4 - ytd['k']} quý ước theo TB 2 quý gần nhất" if ytd["k"] < 4 else ""))
    # EPS là của cổ đông công ty mẹ, trên số cp MỚI NHẤT (share_basis / parent_ratio — cùng cơ sở với bảng dự phóng
    # nhiều năm của pipeline.run_forecast, để cả App chỉ có MỘT EPS dự phóng năm 1)
    shares = share_basis(reals, quarters, market_cap, price)["shares"]
    last = reals[-1]
    rho = parent_ratio(reals, ytd)
    eps_fc = net_fc * rho["y1"] * 1e8 / shares if (net_fc is not None and shares) else None
    if ytd and ytd.get("ctrl_net") is not None and shares and ytd.get("ctrl_net_ytd") is None:
        # số công bố: dùng thẳng LN cổ đông mẹ lũy kế quý (k = 1 thì theo tỷ lệ quý thực, như run_forecast)
        eps_fc = ytd["ctrl_net"] * 1e8 / shares
    dps = last.get("dps") or 0
    bps_last = last.get("bps")
    bps_fc = (bps_last + eps_fc - dps) if (bps_last is not None and eps_fc is not None) \
        else bps_last
    roe_fc = eps_fc / bps_fc * 100 if (eps_fc and bps_fc) else None

    # Dự phóng CÂN ĐỐI (억원): VCSH = VCSH cũ + LN giữ lại; Nợ theo 부채비율 gần nhất.
    eq_last = last.get("equity")
    dr_last = last.get("debt_ratio")
    equity_fc = assets_fc = liab_fc = None
    if eq_last is not None and net_fc is not None:
        div_total = (dps * shares / 1e8) if (dps and shares) else 0.0   # 원 -> 억
        equity_fc = eq_last + net_fc - div_total
        if dr_last is not None and equity_fc:
            liab_fc = equity_fc * dr_last / 100
            assets_fc = equity_fc + liab_fc

    return {
        "period": _next_label(reals), "label": _next_label(reals)[:4],
        "is_consensus": True, "is_forecast": True, "_cagr": round(g * 100, 1),
        "_basis": basis,
        "revenue": round(rev_fc) if rev_fc is not None else None,
        "op_profit": round(op_fc) if op_fc is not None else None,
        "net_profit": round(net_fc) if net_fc is not None else None,
        "op_margin": round(opm * 100, 2) if opm is not None else None,
        "net_margin": round(npm * 100, 2) if npm is not None else None,
        "eps": round(eps_fc) if eps_fc is not None else None,
        "bps": round(bps_fc) if bps_fc is not None else None,
        "roe": round(roe_fc, 2) if roe_fc is not None else None,
        "debt_ratio": last.get("debt_ratio"),
        "equity": round(equity_fc) if equity_fc is not None else None,
        "assets": round(assets_fc) if assets_fc is not None else None,
        "liabilities": round(liab_fc) if liab_fc is not None else None,
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


# ---------------------------------------------------------------------------
# Đối chiếu DART ↔ Naver cho CÙNG một năm (rà soát 15/11/2026). Trước đây DART ghi đè Naver vô điều kiện và mọi lỗi
# DART bị nuốt (`except: pass`). Đo 377 cặp (mã, năm) của 147 mã lớn: 97% khớp trong ±2%; phần lệch là
#   • báo cáo bằng USD đọc như KRW  — Doosan Bobcat: DART = 0,07% Naver → tăng trưởng năm 1 ra +150.878%;
#   • định nghĩa doanh thu khác     — SK Square: DART 14.115 vs Naver 104.556 (Naver gồm lãi công ty liên kết);
#   • điều chỉnh hồi tố / đổi phạm vi — Samsung Biologics 2024: DART gốc 45.473 vs Naver đã điều chỉnh 34.971.
# Quy tắc (bằng số, không có đường giải trình):
#   1. Báo cáo DART không phải KRW        → không dùng DART (Naver đã quy KRW), ghi rõ.
#   2. Năm thực GẦN NHẤT lệch ngoài dải   → hai nguồn không cùng nói về một con số: bỏ toàn bộ DART, cổng số chặn mô
#      hình dựa trên dự phóng (pipeline.data_quality → source_break).
#   3. Năm cũ hơn lệch > ±5%              → giữ số Naver của năm đó (đã điều chỉnh hồi tố, cùng cơ sở với năm gần nhất
#      và với EPS/BPS/quý), cảnh báo.
#   4. DART lỗi kết nối / hạn mức         → ghi rõ là KHÔNG ĐẦY ĐỦ (không còn lặng lẽ thành "chỉ có 3 năm Naver").
# Trạng thái gắn vào khóa `_src` của MỌI năm thực (cùng một dict) để data_quality đọc.
# ---------------------------------------------------------------------------
SRC_SAME = 0.05                   # |DART ÷ Naver − 1| ≤ 5%: coi là cùng một số → dùng số gốc DART
SRC_BREAK_REV = (0.9, 1.1)        # năm gần nhất: doanh thu DART ÷ Naver ngoài dải → mâu thuẫn nguồn
SRC_BREAK_NP = (0.8, 1.25)        # năm gần nhất: LN thuần DART ÷ Naver ngoài dải (hoặc trái dấu) → mâu thuẫn nguồn
SRC_NP_MIN_SHARE = 0.01           # chỉ so LN thuần khi |LN| ≥ 1% doanh thu (LN gần 0 thì tỷ lệ vô nghĩa)


def _src_ratio(d, n) -> Optional[float]:
    return d / n if (d is not None and n not in (None, 0) and (d > 0) == (n > 0)) else None


def _source_gaps(base: dict, rows: list[dict]) -> list[dict]:
    """Các năm trùng mà DART lệch Naver > SRC_SAME ở doanh thu / LN HĐ / LN thuần. [{year, field, dart, naver, ratio}]"""
    gaps = []
    for d in rows:
        y = d["period"][:4]
        n = base.get(y)
        if not n:
            continue
        for k in ("revenue", "op_profit", "net_profit"):
            dv, nv = d.get(k), n.get(k)
            if dv is None or nv is None:
                continue
            if k != "revenue" and n.get("revenue") and abs(nv) < SRC_NP_MIN_SHARE * abs(n["revenue"]) \
                    and abs(dv) < SRC_NP_MIN_SHARE * abs(n["revenue"]):
                continue                                   # cả hai đều ~0 so với doanh thu
            if abs(dv - nv) <= 1.0:                        # lệch ≤ 1억: Naver làm tròn tới 억
                continue
            r = _src_ratio(dv, nv)
            if r is None or abs(r - 1) > SRC_SAME:
                gaps.append({"year": y, "field": k, "dart": dv, "naver": nv, "ratio": r})
    return gaps


def _accepts_kw(fn, name: str) -> bool:
    """Hàm `fn` có nhận tham số từ khóa `name` không (kể cả qua **kwargs)."""
    import inspect
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in ps or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in ps.values())


def _dart_parent(ticker: str, year: int, errors: Optional[list] = None) -> Optional[dict]:
    """LN cổ đông mẹ, VCSH cổ đông mẹ, LN thuần (억) và loại báo cáo của BCTC năm `year` (analysis.fetch_full — báo cáo
    đã nộp có cache đĩa). Không có / khác năm / không phải KRW → None.
    errors: list nhận lỗi DART (hết hạn mức, ngắt mạch, mạng) — khi đó None là do LỖI, không phải 'không đủ điều kiện'
    (rà soát cuối 18/11/2026: lỗi bị nuốt → năm chỉ có ở DART âm thầm bị bỏ và chuỗi thiếu được nhớ như 'ok')."""
    try:
        import analysis
        kw = {"errors": errors} if (errors is not None and _accepts_kw(analysis.fetch_full, "errors")) else {}
        fs = analysis.fetch_full(ticker, year=year, **kw)
    except Exception as e:
        if errors is not None:
            errors.append(f"lỗi đọc BCTC đầy đủ DART ({type(e).__name__})")
        return None
    if not fs or str(fs.get("year")) != str(year) or str(fs.get("currency") or "KRW") != "KRW":
        return None
    cur = fs.get("cur") or {}
    return {"ni_parent": cur.get("ni_parent"), "equity_parent": cur.get("equity_parent"), "ni": cur.get("ni"),
            "fs_div": fs.get("fs_div")}


DART_ONLY_RUNRATE = (0.5, 2.0)    # năm chỉ có ở DART ÷ (9 tháng Naver × 4/3) ngoài dải → gãy nguồn (đơn vị / phạm vi)


def _dart_only_check(d: dict, quarters: Optional[list[dict]]) -> tuple[Optional[bool], str]:
    """Đối chiếu năm chỉ có ở DART với quý thực của Naver cùng năm. (True, '') khớp; (False, lý do) lệch (mâu thuẫn
    nguồn); (None, lý do) không đối chiếu được.
      • đủ 4 quý Naver: tổng 4 quý vs số năm DART trong SRC_BREAK_REV / SRC_BREAK_NP (như năm gần nhất);
      • có Q1–Q3: chỉ coi là lệch nguồn khi gãy thô — Q4 suy ra (năm DART − 9 tháng Naver) ≤ 0 hoặc doanh thu năm DART
        ÷ (9 tháng Naver × 4/3) ngoài DART_ONLY_RUNRATE; Q4 chỉ trượt quarter_ok (LN > DT, mùa vụ > 3×) → (None) không
        thêm năm, giữ năm Naver cũ + run-rate (lãi bất thường Q4 / mùa vụ mạnh không chặn mô hình)."""
    y = str(d["period"])[:4]
    nq = {str(c["period"])[4:6]: c for c in _q_reals(quarters) if str(c["period"])[:4] == y}
    rev, np_ = d.get("revenue"), d.get("net_profit")
    q13 = [nq.get(m) for m in ("03", "06", "09")]
    if any(c is None or c.get("revenue") is None or c.get("net_profit") is None for c in q13):
        return None, f"năm {y} chỉ có ở DART, Naver chưa đủ quý {y} để đối chiếu"
    if nq.get("12") and nq["12"].get("revenue") is not None and nq["12"].get("net_profit") is not None:
        s_rev = sum(nq[m]["revenue"] for m in ("03", "06", "09", "12"))
        s_np = sum(nq[m]["net_profit"] for m in ("03", "06", "09", "12"))
        r_rev, r_np = _src_ratio(rev, s_rev), _src_ratio(np_, s_np)
        if not (r_rev and SRC_BREAK_REV[0] <= r_rev <= SRC_BREAK_REV[1]):
            return False, f"năm {y}: doanh thu DART {rev:,.0f} vs tổng 4 quý Naver {s_rev:,.0f}억"
        if abs(s_np) >= SRC_NP_MIN_SHARE * abs(s_rev) and not (r_np and SRC_BREAK_NP[0] <= r_np <= SRC_BREAK_NP[1]):
            return False, f"năm {y}: LN thuần DART {np_:,.0f} vs tổng 4 quý Naver {s_np:,.0f}억"
        return True, ""
    s9 = sum(c["revenue"] for c in q13)
    q4 = {"revenue": rev - s9, "net_profit": np_ - sum(c["net_profit"] for c in q13)}
    r_run = _src_ratio(rev, s9 * 4 / 3)
    if q4["revenue"] <= 0 or not (r_run and DART_ONLY_RUNRATE[0] <= r_run <= DART_ONLY_RUNRATE[1]):
        return False, (f"năm {y}: doanh thu DART {rev:,.0f}억 trừ 9 tháng Naver ra Q4 {q4['revenue']:,.0f}억 — phi lý "
                       "so với các quý")
    if not quarter_ok(q4, [c["revenue"] for c in q13]):
        return None, f"năm {y}: Q4 suy ra từ DART bất thường so với 3 quý Naver — chưa dùng năm này làm gốc"
    return True, ""


def _merge_dart(naver_annual: list[dict], ticker: str, quarters: Optional[list[dict]] = None) -> list[dict]:
    """Gộp số THỰC từ DART (4-6 năm, có 자산총계/부채총계) lên khung Naver (theo năm).

    Năm trùng: DART khớp Naver (±5%) → dùng số gốc DART; lệch → giữ số Naver và ghi nhận. Luôn giữ các tỉ số thị
    trường (EPS/BPS/PER/PBR/ROE) của Naver. Mỗi năm thực mang `_src` = {dart: ok | none | error | currency | mismatch,
    note, gaps} — xem khối chú thích phía trên.

    Năm MỚI HƠN năm thực gần nhất của Naver (DART đã nộp 사업보고서, Naver còn ghi (E) — rà soát 18/11/2026, I14 (A), anh
    duyệt) chỉ thêm khi: có doanh thu và LN thuần (ngân hàng/bảo hiểm thiếu doanh thu → không thêm), khớp các quý Naver
    cùng năm (_dart_only_check; lệch → 'mismatch'), và có LN / VCSH cổ đông mẹ ở BCTC đầy đủ cùng năm để điền
    eps = LN cổ đông mẹ × 1e8 ÷ số cp cơ sở, bps = VCSH cổ đông mẹ × 1e8 ÷ số cp cơ sở (cờ `_dart_only`). Thiếu điều kiện
    → giữ năm Naver cũ + quý thực (run-rate) như trước khi DART nộp. quarters: quý Naver (để đối chiếu và lấy số cp).
    Kỳ kế toán ngắn (REIT 6/3 tháng) → _merge_dart_short: ghép theo kỳ đầy đủ rồi năm hóa (I08)."""
    m_short = short_period_months(naver_annual)
    if m_short:
        return _merge_dart_short(naver_annual, ticker, quarters, m_short)
    base = {c["period"][:4]: dict(c) for c in naver_annual
            if not c.get("is_consensus")}
    src = {"dart": "none", "note": "", "gaps": []}
    if dart_api is not None and dart_api.available():
        errs: list = []
        # bản giả lập / cũ chưa có err_years → không biết năm lỗi (None: không bao giờ coi là 'cached'). Kiểm bằng chữ
        # ký hàm, không bắt TypeError (bắt TypeError sẽ gọi DART 2 lần khi hàm thật lỗi TypeError bên trong)
        err_years: Optional[list] = [] if _accepts_kw(dart_api.annual_financials, "err_years") else None
        try:
            kw = {"err_years": err_years} if err_years is not None else {}
            rows = dart_api.annual_financials(ticker, years=6, errors=errs, **kw)
        except Exception as e:                       # lỗi ngoài dự kiến cũng phải lộ ra, không nuốt
            rows, errs = [], [f"lỗi đọc DART ({type(e).__name__})"]
        got = {r["period"][:4] for r in rows}
        ref = int(max(base or got)) if (base or got) else None
        # err_years rỗng = mọi năm lỗi đều đọc được từ bản lưu (vd bản lưu _YEAR_V cũ) → vẫn 'cached' nếu đủ năm
        if (errs and rows and err_years is not None and all(int(y) > ref for y in err_years)
                and set(base) <= got):
            # DART lỗi CHỈ ở năm MỚI HƠN năm thực gần nhất của Naver (có thể chưa nộp) và các năm đã lưu phủ mọi năm của
            # Naver → chuỗi đủ như lúc DART hoạt động; ghi nhận (⚠️). Lỗi ở một năm ≤ năm gần nhất (chuỗi bị cụt: Hyundai
            # Motor còn 3 năm, +27,5%) vẫn là 'error' (❌, Low) — rà soát 18/11/2026
            errs, cached_note = [], f"DART đang lỗi ({errs[0]}) — dùng BCTC năm đã lưu (đủ tới năm gần nhất)"
        else:
            cached_note = ""
        foreign = sorted({str(r.get("currency")) for r in rows if r.get("currency") and r.get("currency") != "KRW"})
        gaps = [] if foreign else _source_gaps(base, rows)
        last_y = max(base) if base else None
        # năm chỉ có ở DART, mới hơn năm Naver gần nhất → kiểm trước khi gộp (I14)
        only: dict = {}
        only_bad = None
        if not foreign and last_y:
            sh = share_basis([base[k] for k in sorted(base)], quarters)["shares"]
            for d in rows:
                y = d["period"][:4]
                if y <= last_y or y in base:
                    continue
                if d.get("revenue") is None or d.get("net_profit") is None:
                    continue                                   # ngân hàng/bảo hiểm: DART không có dòng doanh thu
                ok, why = _dart_only_check(d, quarters)
                if ok is False:
                    only_bad = only_bad or why
                    continue
                perr: list = []
                par = _dart_parent(ticker, int(y), errors=perr) if ok else None
                if perr:                                   # DART lỗi ≠ năm không đủ điều kiện → 'error', không nhớ
                    errs.append(f"năm {y}: không đọc được LN/VCSH cổ đông mẹ — {perr[0]}")
                    continue
                if not (par and sh and par.get("ni_parent") is not None and par.get("equity_parent")
                        and (not d.get("fs_div") or not par.get("fs_div") or d["fs_div"] == par["fs_div"])
                        and (par.get("ni") is None or _q_close(par["ni"], d["net_profit"], d["revenue"], "net_profit"))):
                    continue
                only[y] = {"ctrl_net": par["ni_parent"], "equity_parent": par["equity_parent"],
                           "eps": round(par["ni_parent"] * 1e8 / sh), "bps": round(par["equity_parent"] * 1e8 / sh),
                           "_dart_only": True}
        last_gap = [g for g in gaps if g["year"] == last_y and (
            (g["field"] == "revenue" and not (g["ratio"] and SRC_BREAK_REV[0] <= g["ratio"] <= SRC_BREAK_REV[1]))
            or (g["field"] == "net_profit" and not (g["ratio"] and SRC_BREAK_NP[0] <= g["ratio"] <= SRC_BREAK_NP[1])))]
        if foreign:
            src = {"dart": "currency", "gaps": [],
                   "note": f"BCTC DART lập bằng {', '.join(foreign)} — không trộn với số KRW, dùng số Naver (đã quy KRW)"}
            rows = []
        elif only_bad:
            src = {"dart": "mismatch", "gaps": gaps, "note": only_bad}
            rows = []
        elif last_gap:
            g = last_gap[0]
            lbl = {"revenue": "doanh thu", "net_profit": "LN thuần"}[g["field"]]
            src = {"dart": "mismatch", "gaps": gaps,
                   "note": f"năm {g['year']}: {lbl} DART {g['dart']:,.0f} vs Naver {g['naver']:,.0f}억 "
                           + (f"({g['ratio']:.2f}×)" if g["ratio"] else "(trái dấu)")}
            rows = []
        else:
            src = {"dart": "error" if errs else ("cached" if cached_note else "ok" if rows else "none"), "gaps": gaps,
                   "note": errs[0] if errs else cached_note}
        off = {(g["year"], g["field"]) for g in gaps}
        for d in rows:
            y = d["period"][:4]
            if last_y and y > last_y and y not in only:
                continue                                       # năm mới chỉ có ở DART nhưng không đủ điều kiện
            tgt = base.get(y) or {"period": d["period"], "label": y,
                                  "is_consensus": False, **only.get(y, {})}
            for k in ("revenue", "op_profit", "net_profit", "assets",
                      "liabilities", "equity", "debt_ratio"):
                if d.get(k) is not None and (y, k) not in off:
                    tgt[k] = d[k]
            if tgt.get("revenue"):
                if tgt.get("net_profit") is not None:
                    tgt["net_margin"] = round(tgt["net_profit"] / tgt["revenue"] * 100, 2)
                if tgt.get("op_profit") is not None:
                    tgt["op_margin"] = round(tgt["op_profit"] / tgt["revenue"] * 100, 2)
            tgt["is_consensus"] = False
            tgt["_dart"] = True
            base[y] = tgt
    out = [base[y] for y in sorted(base.keys())]
    for c in out:
        c["_src"] = src
    return out


# ---------------------------------------------------------------------------
# Kỳ kế toán NGẮN hơn 12 tháng (rà soát 18/11/2026, I08). REIT báo theo kỳ 6 tháng (롯데리츠 202506/202512/202606) hoặc
# 3 tháng (SK리츠 202512/202603/202606); cột 'annual' của Naver là các kỳ đó và 사업보고서 DART chỉ chứa kỳ cuối năm.
# Gom theo năm (period[:4]) làm 202506 bị 202512 đè, LN/EPS/ROE chỉ còn 1/2–1/4 (PER 43,9 thay vì 24,0). Nay: phát hiện
# kỳ ngắn, ghép DART theo kỳ ĐẦY ĐỦ, rồi dựng mỗi năm một điểm 12 tháng (TTM) lùi 12 tháng từ kỳ thực gần nhất:
#   • đủ 12 tháng từ các kỳ (cột Naver, kỳ DART, quý Naver) → cộng thẳng, _ttm = True;
#   • thiếu → ước = tổng các kỳ có số × 12 ÷ số tháng có số (_ttm = False, _scale) — năm cũ chỉ có kỳ cuối năm của DART.
# Kỳ gần nhất không đủ 12 tháng → pipeline.data_quality ❌ và chặn mô hình dựa trên dự phóng (cổng bằng số).
# ---------------------------------------------------------------------------
_TTM_FLOW = ("revenue", "op_profit", "net_profit", "ctrl_net", "eps", "dps")
_TTM_STOCK = ("assets", "liabilities", "equity", "debt_ratio", "bps", "pbr", "quick_ratio", "retention")
TTM_MAX_POINTS = 7
TTM_MAX_SCALE = 2.0                 # năm cũ ước × 12 ÷ số tháng: tối đa × 2 (≥ 6 tháng có số)


def _pidx(p) -> Optional[int]:
    """'YYYYMM…' → số tháng (năm × 12 + tháng − 1); không phải kỳ hợp lệ → None."""
    s = str(p or "")
    if len(s) < 6 or not s[:6].isdigit() or not 1 <= int(s[4:6]) <= 12:
        return None
    return int(s[:4]) * 12 + int(s[4:6]) - 1


def _pstr(i: int) -> str:
    return f"{i // 12}{i % 12 + 1:02d}"


def short_period_months(annual: Optional[list[dict]]) -> Optional[int]:
    """Độ dài (tháng) của kỳ kế toán khi các cột 'annual' THỰC là kỳ ngắn (6 hoặc 3 tháng), không thì None.
    Ngắn = khoảng cách giữa hai cột gần nhất < 12 tháng (≥ 2 cột cùng năm) và ít nhất một nửa khoảng cách < 12 (một kỳ
    lẻ khi đổi năm tài chính không làm cả chuỗi thành kỳ ngắn)."""
    idx = sorted({i for c in (annual or []) if not c.get("is_consensus")
                  for i in [_pidx(c.get("period"))] if i is not None})
    if len(idx) < 2:
        return None
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    if gaps[-1] not in (3, 6) or sum(g < 12 for g in gaps) * 2 < len(gaps):
        return None
    return gaps[-1]


def _ttm_point(T: int, pieces: dict, m: int, prev_bps: Optional[float]) -> Optional[dict]:
    """Một điểm 12 tháng kết thúc tại tháng T từ các kỳ `pieces` {tháng kết thúc: [kỳ, dài trước]} (mỗi kỳ có '_m')."""
    parts, cur, need = [], T, 12
    while need > 0:
        cand = next((p for p in pieces.get(cur, []) if p["_m"] <= need), None)
        if cand:
            parts.append(cand)
            cur, need = cur - cand["_m"], need - cand["_m"]
        else:
            cur, need = cur - 1, need - 1
    if not parts:
        return None
    out = {"period": _pstr(T), "label": f"{_pstr(T)[:4]}.{_pstr(T)[4:6]} TTM", "is_consensus": False}
    months = {}
    for k in _TTM_FLOW:
        # cổ tức chỉ lấy ở kỳ gốc (cột Naver / kỳ DART): cột quý của REIT 6 tháng lặp lại cổ tức cả kỳ
        src = [p for p in parts if p.get(k) is not None and (k != "dps" or p["_m"] == m)]
        mo = sum(p["_m"] for p in src)
        months[k] = mo
        out[k] = (sum(p[k] for p in src) * 12 / mo) if mo else None
    for k in _TTM_STOCK:                                         # số dư: của kỳ gần nhất có số
        out[k] = next((p[k] for p in parts if p.get(k) is not None), None)
    rev, np_, op = out.get("revenue"), out.get("net_profit"), out.get("op_profit")
    out["net_margin"] = round(np_ / rev * 100, 2) if (rev and np_ is not None) else None
    out["op_margin"] = round(op / rev * 100, 2) if (rev and op is not None) else None
    eps, bps = out.get("eps"), out.get("bps")
    avg_b = (bps + prev_bps) / 2 if (bps and prev_bps) else bps
    if eps is not None and avg_b:
        out["roe"] = round(eps / avg_b * 100, 2)                 # ROE trên VCSH bình quân (BPS đầu–cuối 12 tháng)
    elif np_ is not None and out.get("equity"):
        out["roe"] = round(np_ / out["equity"] * 100, 2)
    else:
        out["roe"] = None
    covered = max(months["revenue"], months["net_profit"])
    out["_ttm"] = months["revenue"] == 12 and months["net_profit"] == 12
    out["_ttm_eps"] = months["eps"] == 12
    lp = parts[0]                                                # kỳ gần nhất: giá ngầm = PER × EPS của kỳ
    out["per"] = (round(lp["per"] * lp["eps"] / eps, 2)
                  if (out["_ttm_eps"] and lp.get("per") and lp.get("eps") and eps and eps > 0 and lp["eps"] > 0)
                  else None)
    out["_period_months"] = m
    out["_scale"] = round(12 / covered, 3) if covered and not out["_ttm"] else None
    out["_ttm_parts"] = [(p["period"], p["_m"]) for p in parts]
    if any(p.get("_dart") for p in parts):
        out["_dart"] = True
        out["_dart_periods"] = [p["period"] for p in parts if p.get("_dart")]
    return out


def annualize_short(cols: list[dict], quarters: Optional[list[dict]], m: int) -> list[dict]:
    """Chuỗi năm 12 tháng (TTM) từ các kỳ ngắn `cols` (cột Naver + kỳ DART, mỗi kỳ dài `m` tháng) và quý thực Naver.
    Điểm gần nhất kết thúc ở kỳ thực gần nhất; mỗi điểm trước lùi 12 tháng. Thứ tự thời gian tăng dần."""
    idx_of = {c["period"]: _pidx(c["period"]) for c in cols if _pidx(c.get("period")) is not None}
    if not idx_of:
        return []
    pieces: dict = {}
    srt = sorted(idx_of.values())
    for c in cols:
        i = idx_of.get(c.get("period"))
        if i is None:
            continue
        prev = max((j for j in srt if j < i), default=None)
        pieces.setdefault(i, []).append({**c, "_m": min(m, i - prev) if prev is not None else m})
    for q in _q_reals(quarters):                                 # quý thực: lấp kỳ còn thiếu (kỳ 6 tháng ưu tiên)
        i = _pidx(q.get("period"))
        if i is not None:
            pieces.setdefault(i, []).append({**q, "_m": 3})
    for v in pieces.values():
        v.sort(key=lambda p: -p["_m"])
    T0 = max(srt)
    out: list = []
    for k in range(TTM_MAX_POINTS - 1, -1, -1):                 # cũ → mới (ROE cần BPS 12 tháng trước)
        T = T0 - 12 * k
        prev = out[-1] if (out and _pidx(out[-1]["period"]) == T - 12) else None
        pt = _ttm_point(T, pieces, m, prev.get("bps") if prev else None)
        if pt and (pt.get("revenue") is not None or pt.get("net_profit") is not None):
            if k and (pt.get("_scale") or 0) > TTM_MAX_SCALE:
                continue                    # năm cũ có < 6 tháng số (vd chỉ quý 4 × 4): mùa vụ méo CAGR → bỏ
            out.append(pt)
    return out


def _merge_dart_short(naver_annual: list[dict], ticker: str, quarters: Optional[list[dict]], m: int) -> list[dict]:
    """_merge_dart cho kỳ kế toán ngắn: ghép DART theo kỳ ĐẦY ĐỦ (DART 'YYYY12' = kỳ cuối năm, dài `m` tháng) rồi năm
    hóa (annualize_short). Cùng quy tắc nguồn: khớp ±5% → số gốc DART; kỳ trùng gần nhất lệch ngoài dải → 'mismatch',
    bỏ DART; DART lỗi → 'error'; khác KRW → 'currency'. Kỳ DART mới hơn kỳ Naver gần nhất không được thêm."""
    base = {c["period"]: dict(c) for c in naver_annual
            if not c.get("is_consensus") and _pidx(c.get("period")) is not None}
    src = {"dart": "none", "note": "", "gaps": []}
    last_i = max(_pidx(p) for p in base) if base else None
    # DART luôn gắn nhãn 'YYYY12' (dart_api.annual_financials): chỉ dùng được khi tháng 12 nằm trên lưới kỳ của mã
    # (kỳ kết thúc tháng 6/12, quý 3/6/9/12). REIT kỳ 3/9, 4/10, 5/11… → kỳ DART không trùng kỳ nào của Naver, ghép
    # vào sẽ cộng trùng tháng (TTM ≈ 18 tháng) → bỏ DART cho mã này (rà soát 18/11/2026)
    on_grid = last_i is not None and (last_i - 11) % m == 0
    if base and not on_grid and dart_api is not None and dart_api.available():
        src = {"dart": "none", "gaps": [],
               "note": f"kỳ DART (tháng 12) không trùng kỳ kế toán {m} tháng của mã (kết thúc tháng "
                       f"{last_i % 12 + 1}) — chỉ dùng số Naver"}
    elif base and dart_api is not None and dart_api.available():
        errs: list = []
        err_years: Optional[list] = [] if _accepts_kw(dart_api.annual_financials, "err_years") else None
        try:
            kw = {"err_years": err_years} if err_years is not None else {}
            rows = dart_api.annual_financials(ticker, years=6, errors=errs, **kw)
        except Exception as e:
            rows, errs = [], [f"lỗi đọc DART ({type(e).__name__})"]
        rows = [d for d in rows if _pidx(d.get("period")) is not None and _pidx(d["period"]) <= last_i
                and (last_i - _pidx(d["period"])) % m == 0]     # kỳ DART mới hơn kỳ Naver gần nhất: không thêm
        got = {d["period"] for d in rows}
        cached_note = ""
        # như nhánh năm: 'cached' chỉ khi lỗi ở năm mới hơn kỳ gần nhất VÀ bản lưu phủ đủ mọi kỳ cuối năm từ kỳ lưu cũ
        # nhất (hoặc kỳ Naver cũ nhất) tới kỳ gần nhất — chuỗi cụt vẫn là 'error' (❌, Low)
        lo = min([_pidx(p) for p in got] + [_pidx(p) for p in base if p[4:6] == "12"], default=None)
        need = {_pstr(i) for i in range(lo, last_i + 1) if i % 12 == 11} if lo is not None else set()
        if (errs and rows and err_years is not None and all(int(y) * 12 + 11 > last_i for y in err_years)
                and need <= got):
            errs, cached_note = [], f"DART đang lỗi ({errs[0]}) — dùng BCTC năm đã lưu (đủ tới kỳ gần nhất)"
        foreign = sorted({str(r.get("currency")) for r in rows if r.get("currency") and r.get("currency") != "KRW"})
        same = [d for d in rows if d.get("period") in base]
        last_dart = max(got, default=None)
        gaps = [] if foreign else _source_gaps({d["period"][:4]: base[d["period"]] for d in same}, same)
        last_same = max((d["period"] for d in same), default=None)
        last_gap = [g for g in gaps if last_same and g["year"] == last_same[:4] and (
            (g["field"] == "revenue" and not (g["ratio"] and SRC_BREAK_REV[0] <= g["ratio"] <= SRC_BREAK_REV[1]))
            or (g["field"] == "net_profit" and not (g["ratio"] and SRC_BREAK_NP[0] <= g["ratio"] <= SRC_BREAK_NP[1])))]
        if foreign:
            src = {"dart": "currency", "gaps": [],
                   "note": f"BCTC DART lập bằng {', '.join(foreign)} — không trộn với số KRW, dùng số Naver (đã quy KRW)"}
            rows = []
        elif last_dart and last_dart not in base:
            # kỳ DART gần nhất không có cột Naver cùng kỳ → không đối chiếu được dải ±5% → không ghép
            src = {"dart": "error" if errs else "none", "gaps": [],
                   "note": errs[0] if errs else
                   f"kỳ DART {last_dart[:4]}.{last_dart[4:6]} không có cột Naver cùng kỳ để đối chiếu — chỉ dùng số Naver"}
            rows = []
        elif last_gap:
            g = last_gap[0]
            lbl = {"revenue": "doanh thu", "net_profit": "LN thuần"}[g["field"]]
            src = {"dart": "mismatch", "gaps": gaps,
                   "note": f"kỳ {last_same[:4]}.{last_same[4:6]}: {lbl} DART {g['dart']:,.0f} vs Naver "
                           f"{g['naver']:,.0f}억 " + (f"({g['ratio']:.2f}×)" if g["ratio"] else "(trái dấu)")}
            rows = []
        else:
            src = {"dart": "error" if errs else ("cached" if cached_note else "ok" if rows else "none"), "gaps": gaps,
                   "note": errs[0] if errs else cached_note}
        off = {(g["year"], g["field"]) for g in gaps}
        for d in rows:
            p = d["period"]
            tgt = base.get(p) or {"period": p, "label": p[:4], "is_consensus": False}
            for k in ("revenue", "op_profit", "net_profit", "assets", "liabilities", "equity", "debt_ratio"):
                if d.get(k) is not None and (p[:4], k) not in off:
                    tgt[k] = d[k]
            tgt["_dart"] = True
            base[p] = tgt
    out = annualize_short(list(base.values()), quarters, m)
    for c in out:
        c["_src"] = src
    return out


def build_annual_series(ticker: str, naver_annual: list[dict],
                        market_cap: Optional[float] = None,
                        price: Optional[float] = None, years: int = 5,
                        quarters: Optional[list[dict]] = None) -> list[dict]:
    """Chuỗi năm để hiển thị: tối đa `years` năm THỰC (DART nếu có, fallback Naver)
    + 1 cột dự phóng bằng công thức (tính cả quý thực của năm dự phóng nếu có
    `quarters`). Thứ tự thời gian tăng dần."""
    reals = _merge_dart(naver_annual, ticker, quarters=quarters)[-years:]
    fc = project_forecast(reals, market_cap, price, quarters=quarters)
    return reals + ([fc] if fc else [])


# ---------------------------------------------------------------------------
# Quý DART đã nộp khi Naver còn để cột đồng thuận (rà soát 18/11/2026, I05 — anh duyệt). 17–18/11 Naver vẫn ghi 2026.09
# là đồng thuận ở 19/20 mã lớn dù 분기보고서 Q3 đã nộp: năm 1 tính với 2 quý thay vì 3 (EPS Samsung lệch 12,7%), số cp,
# EBIT 4 quý, bảng quý đều chậm một quý. Ghép quý DART mới hơn quý thực cuối của Naver — CHỈ khi các quý trùng (trong 4
# quý thực gần nhất của Naver) khớp ±2% doanh thu / LN HĐ / LN thuần, cùng KRW, cùng một loại báo cáo (fs_div). Không
# ghép được → giữ nguyên Naver, trả lý do để bảng kiểm tra số liệu ⚠️.
# ---------------------------------------------------------------------------
DART_Q_TOL = 0.02


def _q_close(d, n, rev, field: str) -> bool:
    """Số DART `d` và Naver `n` của cùng một quý có là một số không (±2%, Naver làm tròn tới 1억; LN ~0 so với DT thì bỏ)."""
    if abs(d - n) <= 1.0:
        return True
    if field != "revenue" and rev and abs(d) < SRC_NP_MIN_SHARE * abs(rev) and abs(n) < SRC_NP_MIN_SHARE * abs(rev):
        return True
    r = _src_ratio(d, n)
    return r is not None and abs(r - 1) <= DART_Q_TOL


def merge_dart_quarters(quarters: Optional[list[dict]], dart_q: Optional[list[dict]],
                        shares: Optional[float] = None) -> tuple[list[dict], dict]:
    """Ghép quý DART (analysis.quarter_series) mới hơn quý thực cuối của Naver vào danh sách quý Naver.

    Quý ghép thay cột đồng thuận cùng kỳ (hoặc kỳ Naver chưa có): {period YYYYMM, is_consensus False, revenue, op_profit,
    net_profit, ctrl_net = LN cổ đông mẹ, eps = LN cổ đông mẹ × 1e8 ÷ số cp cơ sở của quý Naver thực cuối, _src 'DART'}.
    shares: số cp cơ sở (không truyền → suy từ quý Naver thực cuối như share_basis).
    Trả (danh sách quý, info) — info = {state: none | merged | mismatch | currency | fs_div | no_overlap | incomplete,
    added: [kỳ], note}; state khác none/merged = DART có quý mới hơn nhưng KHÔNG ghép (giữ nguyên Naver)."""
    quarters = list(quarters or [])
    info = {"state": "none", "added": [], "note": ""}
    nav = _q_reals(quarters)
    by = {}
    for d in dart_q or []:
        try:
            by[f"{int(d['year'])}{int(d['q']) * 3:02d}"] = d
        except (KeyError, TypeError, ValueError):
            continue
    if not by or not nav:
        return quarters, info
    last_p = str(nav[-1]["period"])[:6]
    newer = sorted(p for p in by if p > last_p)
    if not newer:
        return quarters, info

    def fmt(p):
        return f"{p[:4]}.{p[4:6]}"

    def nxt(p):
        y, m = int(p[:4]), int(p[4:6]) + 3
        return f"{y + 1}{m - 12:02d}" if m > 12 else f"{y}{m:02d}"
    add, p = [], last_p
    while nxt(p) in by:                                   # chỉ các quý liền nhau ngay sau quý thực cuối của Naver
        f = by[nxt(p)].get("flow") or {}
        if f.get("revenue") is None or f.get("ni") is None:
            break
        p = nxt(p)
        add.append(p)

    def blocked(state, note):
        return quarters, {"state": state, "added": [], "note": f"DART có quý {fmt(newer[-1])}: {note}"}
    if not add:
        return blocked("incomplete", "thiếu doanh thu / LN thuần (hoặc không liền quý thực cuối của Naver)")
    # quý trùng chỉ đối chiếu khi cả hai bên có doanh thu (DART để trống quý suy ra bị loại — năm điều chỉnh lại)
    overlap = [(c, by[str(c["period"])[:6]]) for c in nav[-4:] if str(c["period"])[:6] in by
               and c.get("revenue") is not None and (by[str(c["period"])[:6]].get("flow") or {}).get("revenue") is not None]
    used = [by[x] for x in add] + [d for _c, d in overlap]
    cur = sorted({str(d.get("currency") or "KRW") for d in used} - {"KRW"})
    if cur:
        return blocked("currency", f"BCTC lập bằng {', '.join(cur)}, không trộn với số KRW của Naver")
    if len({d.get("fs_div") for d in used}) > 1:
        return blocked("fs_div", "các quý không cùng loại báo cáo (hợp nhất / riêng lẻ)")
    if not overlap:
        return blocked("no_overlap", "không có quý trùng (có doanh thu) với Naver để đối chiếu")
    for c, d in overlap:
        f = d.get("flow") or {}
        for k, dk in (("revenue", "revenue"), ("op_profit", "op"), ("net_profit", "ni")):
            dv, nv = f.get(dk), c.get(k)
            if dv is not None and nv is not None and not _q_close(dv, nv, c.get("revenue"), k):
                lbl = {"revenue": "doanh thu", "op_profit": "LN HĐ", "net_profit": "LN thuần"}[k]
                return blocked("mismatch", f"quý {fmt(str(c['period']))} {lbl} DART {dv:,.0f} vs Naver {nv:,.0f}억 "
                                           f"(lệch > {DART_Q_TOL * 100:.0f}%)")
    if shares is None:
        parent = any(c.get("ctrl_net") is not None for c in nav)
        for c in reversed(nav):
            shares = _implied_shares(c, "ctrl_net" if parent else "net_profit", SHARE_BASIS_MAX_ERR)
            if shares:
                break
    rows = []
    for p in add:
        d = by[p]
        f, bs = d.get("flow") or {}, d.get("bs") or {}
        rev, op, ni, nip = f.get("revenue"), f.get("op"), f.get("ni"), f.get("ni_parent")
        eqp = bs.get("equity_parent")
        base_ni = nip if nip is not None else (None if any(c.get("ctrl_net") is not None for c in nav) else ni)
        rows.append({"period": p, "label": fmt(p), "is_consensus": False, "_src": "DART",
                     "revenue": rev, "op_profit": op, "net_profit": ni, "ctrl_net": nip,
                     "op_margin": round(op / rev * 100, 2) if (op is not None and rev) else None,
                     "net_margin": round(ni / rev * 100, 2) if rev else None,
                     "eps": round(base_ni * 1e8 / shares) if (base_ni is not None and shares) else None,
                     "bps": round(eqp * 1e8 / shares) if (eqp is not None and shares) else None})
    out = sorted([c for c in quarters if str(c.get("period", ""))[:6] not in add] + rows,
                 key=lambda c: str(c.get("period", "")))
    return out, {"state": "merged", "added": add,
                 "note": "Naver chưa cập nhật — dùng số đã nộp DART cho quý " + ", ".join(fmt(x) for x in add)}


def consensus_eps(annual: list[dict], period: Optional[str] = None) -> Optional[float]:
    """EPS đồng thuận (Naver, cột isConsensus) — chỉ để ĐỐI CHIẾU với dự phóng công thức.
    period: chỉ lấy khi đúng năm đó (vd '202612'), tránh so lệch năm."""
    for c in reversed(annual):
        if c.get("is_consensus") and c.get("eps") is not None and not c.get("is_forecast"):
            if period is None or str(c.get("period", ""))[:4] == str(period)[:4]:
                return c["eps"]
    return None


def forecast_eps(annual: list[dict], market_cap: Optional[float] = None,
                 price: Optional[float] = None,
                 quarters: Optional[list[dict]] = None) -> Optional[float]:
    """EPS dự tính bằng công thức (ưu tiên), fallback consensus Naver nếu công thức N/A."""
    fc = project_forecast([c for c in annual if not c.get("is_consensus")],
                          market_cap, price, quarters=quarters)
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
        cagr = _rev_cagr(revs)
        add("Doanh thu tăng trưởng", f"{cagr*100:.1f}%/năm" if cagr is not None else "—",
            growth, "Xu hướng tăng." if growth else "Doanh thu đi ngang/giảm.")

    rating = "🟢 An toàn" if maxs and score / maxs >= 0.75 else \
             "🟡 Trung bình" if maxs and score / maxs >= 0.5 else "🔴 Cần thận trọng"
    summary = f"Đạt {score}/{maxs} tiêu chí" + (" (ngành tài chính)" if is_fin else "")
    return {"rating": rating, "score": score, "max": maxs,
            "checks": checks, "summary": summary}
