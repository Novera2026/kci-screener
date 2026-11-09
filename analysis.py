"""
KCI — Bước 2 (bóc tách tài chính) và Bước 3 (chất lượng tài sản) của quy trình định giá.

Nguồn: BCTC đầy đủ DART (fnlttSinglAcntAll, 사업보고서) theo MÃ TÀI KHOẢN IFRS — bền hơn khớp tên. Một lần gọi
trả cả năm nay (thstrm) và năm trước (frmtrm) → đủ cho các chỉ tiêu cần biến động (F-score). Cache JSON vĩnh viễn
cho năm đã nộp (.cache/dart_full/).

Bóc tách: DuPont (biên × vòng quay × đòn bẩy), cơ cấu LN trước thuế (HĐ / tài chính ròng / công ty liên kết /
khác), dòng tiền (CFO/LN, FCF, capex), vốn lưu động (ngày phải thu, ngày tồn kho).
Chất lượng tài sản: mỗi chỉ tiêu có NGƯỠNG SỐ cố định (✅/⚠️/❌), không có đường giải trình bằng chữ.
Ngành tài chính: DART không có nợ xấu/dự phòng dạng cấu trúc → chỉ chấm được phần chung, ghi rõ giới hạn.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

import requests

import dart_api

OK, WARN, FAIL, NA = "✅", "⚠️", "❌", "—"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "dart_full")

# (trường, các sj_div hợp lệ, mã tài khoản IFRS/DART theo thứ tự ưu tiên). Trường bắt đầu bằng "+" = cộng dồn.
ACCOUNTS = [
    ("assets", {"BS"}, ["ifrs-full_Assets"]),
    ("current_assets", {"BS"}, ["ifrs-full_CurrentAssets"]),
    ("current_liab", {"BS"}, ["ifrs-full_CurrentLiabilities"]),
    ("liabilities", {"BS"}, ["ifrs-full_Liabilities"]),
    ("equity", {"BS"}, ["ifrs-full_Equity"]),
    ("equity_parent", {"BS"}, ["ifrs-full_EquityAttributableToOwnersOfParent"]),
    ("cash", {"BS"}, ["ifrs-full_CashAndCashEquivalents"]),
    ("st_deposits", {"BS"}, ["ifrs-full_ShorttermDepositsNotClassifiedAsCashEquivalents",
                             "ifrs-full_OtherCurrentFinancialAssets"]),
    ("receivables", {"BS"}, ["ifrs-full_CurrentTradeReceivables", "ifrs-full_TradeAndOtherCurrentReceivables",
                             "dart_ShortTermTradeReceivable"]),
    ("inventory", {"BS"}, ["ifrs-full_Inventories"]),
    ("goodwill", {"BS"}, ["ifrs-full_Goodwill"]),
    ("intangibles", {"BS"}, ["ifrs-full_IntangibleAssetsAndGoodwill", "ifrs-full_IntangibleAssetsOtherThanGoodwill"]),
    ("ppe", {"BS"}, ["ifrs-full_PropertyPlantAndEquipment"]),
    ("retained", {"BS"}, ["ifrs-full_RetainedEarnings"]),
    ("equity_method", {"BS"}, ["ifrs-full_InvestmentAccountedForUsingEquityMethod"]),
    ("+borrowings", {"BS"}, ["ifrs-full_ShorttermBorrowings", "ifrs-full_CurrentPortionOfLongtermBorrowings",
                             "ifrs-full_LongtermBorrowings", "ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued",
                             "ifrs-full_BondsIssued", "dart_ShortTermBorrowings", "dart_LongTermBorrowingsGross",
                             "ifrs-full_CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
                             "ifrs-full_NoncurrentPortionOfNoncurrentLoansReceived", "ifrs-full_OtherBorrowings"]),
    # dự phòng khi DN không tách 차입금 (HD한국조선해양 ghi 단기/장기금융부채) — chỉ dùng nếu "+borrowings" = 0
    ("+borrowings_alt", {"BS"}, ["ifrs-full_OtherCurrentFinancialLiabilities",
                                 "ifrs-full_OtherNoncurrentFinancialLiabilities"]),
    ("revenue", {"IS", "CIS"}, ["ifrs-full_Revenue", "ifrs-full_RevenueFromInterest"]),   # ngân hàng: thu lãi
    ("cogs", {"IS", "CIS"}, ["ifrs-full_CostOfSales"]),
    ("gross", {"IS", "CIS"}, ["ifrs-full_GrossProfit"]),
    ("op", {"IS", "CIS"}, ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"]),
    ("fin_income", {"IS", "CIS"}, ["ifrs-full_FinanceIncome"]),
    ("fin_cost", {"IS", "CIS"}, ["ifrs-full_FinanceCosts"]),
    ("equity_income", {"IS", "CIS"},
     ["ifrs-full_ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod"]),
    ("pretax", {"IS", "CIS"}, ["ifrs-full_ProfitLossBeforeTax"]),
    ("tax", {"IS", "CIS"}, ["ifrs-full_IncomeTaxExpenseContinuingOperations"]),
    ("ni", {"IS", "CIS"}, ["ifrs-full_ProfitLoss"]),
    ("ni_parent", {"IS", "CIS"}, ["ifrs-full_ProfitLossAttributableToOwnersOfParent"]),
    ("cfo", {"CF"}, ["ifrs-full_CashFlowsFromUsedInOperatingActivities"]),
    ("+capex", {"CF"}, ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                        "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"]),
    ("dividends_paid", {"CF"}, ["ifrs-full_DividendsPaidClassifiedAsFinancingActivities"]),
    ("interest_paid", {"CF"}, ["ifrs-full_InterestPaidClassifiedAsOperatingActivities",
                               "ifrs-full_InterestPaidClassifiedAsFinancingActivities"]),
]
FIN_BUCKETS = {"Bank", "Brokerage", "Insurance"}


def _n(s) -> Optional[float]:
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", "")) / 1e8          # 원 → 억원
    except ValueError:
        return None


def parse_statements(items: list[dict]) -> tuple[dict, dict]:
    """Danh sách tài khoản DART → (năm nay, năm trước) theo ACCOUNTS. Đơn vị 억원."""
    cur, prev = {}, {}
    for field, sjs, ids in ACCOUNTS:
        add = field.startswith("+")
        name = field.lstrip("+")
        for rank, aid in enumerate(ids):
            hits = [it for it in items if it.get("sj_div") in sjs and it.get("account_id") == aid]
            if not hits:
                continue
            it = hits[0]
            c, p = _n(it.get("thstrm_amount")), _n(it.get("frmtrm_amount"))
            if add:
                if c is not None:
                    cur[name] = cur.get(name, 0.0) + abs(c)
                if p is not None:
                    prev[name] = prev.get(name, 0.0) + abs(p)
            else:
                if c is not None:
                    cur[name] = c
                if p is not None:
                    prev[name] = p
                break
    for d in (cur, prev):
        alt = d.pop("borrowings_alt", None)
        if not d.get("borrowings") and alt:
            d["borrowings"] = alt
    return cur, prev


def fetch_full(ticker: str, year: Optional[int] = None, cache_dir: str = CACHE) -> Optional[dict]:
    """BCTC đầy đủ năm `year` (mặc định năm tài chính gần nhất đã nộp, lùi tối đa 2 năm).
    Trả {year, fs_div, cur, prev} hoặc None (không key DART / không có báo cáo)."""
    if not dart_api.available():
        return None
    cc = dart_api.corp_code_of(ticker)
    if not cc:
        return None
    today = _dt.date.today()
    years = [year] if year else [today.year - 1, today.year - 2, today.year - 3]
    os.makedirs(cache_dir, exist_ok=True)
    for y in years:
        fp = os.path.join(cache_dir, f"{cc}_{y}.json")
        if os.path.exists(fp):
            try:
                return json.load(open(fp, encoding="utf-8"))
            except Exception:
                pass
        for fs_div in ("CFS", "OFS"):
            try:
                j = requests.get(f"{dart_api._BASE}/fnlttSinglAcntAll.json",
                                 params={"crtfc_key": dart_api.api_key(), "corp_code": cc, "bsns_year": str(y),
                                         "reprt_code": "11011", "fs_div": fs_div}, timeout=20).json()
            except Exception:
                continue
            if j.get("status") != "000":
                continue
            cur, prev = parse_statements(j.get("list", []))
            if not cur.get("assets") or (cur.get("revenue") is None and cur.get("op") is None):
                continue
            out = {"year": y, "fs_div": fs_div, "cur": cur, "prev": prev}
            json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            return out
    return None


# ============================================================================ Bước 2 — bóc tách
def _avg(a, b):
    return (a + b) / 2 if (a is not None and b is not None) else a


def _div(a, b):
    return a / b if (a is not None and b not in (None, 0)) else None


def decompose(fs: dict) -> dict:
    """DuPont, cơ cấu LN trước thuế, dòng tiền, vốn lưu động. fs = fetch_full(...)."""
    c, p = fs["cur"], fs.get("prev") or {}
    ni = c.get("ni_parent") if c.get("ni_parent") is not None else c.get("ni")
    rev = c.get("revenue")
    avg_a = _avg(c.get("assets"), p.get("assets"))
    eq_c = c.get("equity_parent") or c.get("equity")
    eq_p = p.get("equity_parent") or p.get("equity")
    avg_e = _avg(eq_c, eq_p)
    margin, turnover, lev = _div(ni, rev), _div(rev, avg_a), _div(avg_a, avg_e)
    dupont = {"Biên LN ròng (cổ đông mẹ) %": margin * 100 if margin is not None else None,
              "Vòng quay tài sản (lần)": turnover, "Đòn bẩy (TS/VCSH)": lev,
              "ROE = tích ba thành phần %": (margin * turnover * lev * 100)
              if None not in (margin, turnover, lev) else None}
    pre = c.get("pretax")
    fin_net = (c.get("fin_income") or 0.0) - (c.get("fin_cost") or 0.0) \
        if (c.get("fin_income") is not None or c.get("fin_cost") is not None) else None
    eqi = c.get("equity_income")
    other = (pre - (c.get("op") or 0) - (fin_net or 0) - (eqi or 0)) if pre is not None else None
    structure = [{"Thành phần": k, "억원": v, "% LN trước thuế": _div(v, pre) * 100 if _div(v, pre) is not None else None}
                 for k, v in (("LN hoạt động", c.get("op")), ("Tài chính ròng", fin_net),
                              ("Công ty liên kết", eqi), ("Khác (gồm một lần)", other), ("LN trước thuế", pre))]
    capex = c.get("capex")
    fcf = (c["cfo"] - capex) if (c.get("cfo") is not None and capex is not None) else None
    cash = {"CFO (억)": c.get("cfo"), "CFO / LN ròng": _div(c.get("cfo"), c.get("ni")),
            "Capex (억)": capex, "Capex / doanh thu %": _div(capex, rev) * 100 if _div(capex, rev) else None,
            "FCF = CFO − capex (억)": fcf, "Biên FCF %": _div(fcf, rev) * 100 if _div(fcf, rev) is not None else None,
            "Cổ tức đã trả / FCF %": _div(c.get("dividends_paid"), fcf) * 100
            if (fcf and fcf > 0 and c.get("dividends_paid") is not None) else None,
            "Thuế suất thực tế %": _div(c.get("tax"), pre) * 100 if (pre and pre > 0 and c.get("tax") is not None) else None}
    dso = _div(c.get("receivables"), rev)
    dio = _div(c.get("inventory"), c.get("cogs"))
    dso_p = _div(p.get("receivables"), p.get("revenue"))
    dio_p = _div(p.get("inventory"), p.get("cogs"))
    wc = {"Ngày phải thu": dso * 365 if dso is not None else None,
          "Ngày phải thu năm trước": dso_p * 365 if dso_p is not None else None,
          "Ngày tồn kho": dio * 365 if dio is not None else None,
          "Ngày tồn kho năm trước": dio_p * 365 if dio_p is not None else None}
    borrow = c.get("borrowings") or 0.0
    liquid = (c.get("cash") or 0.0) + (c.get("st_deposits") or 0.0)
    balance = {"Nợ vay (억)": borrow, "Tiền + tiền gửi ngắn hạn (억)": liquid, "Nợ ròng (억)": borrow - liquid,
               "Nợ/VCSH %": _div(c.get("liabilities"), c.get("equity")) * 100 if _div(c.get("liabilities"), c.get("equity")) else None,
               "Thanh toán hiện hành (lần)": _div(c.get("current_assets"), c.get("current_liab"))}
    return {"year": fs["year"], "fs_div": fs["fs_div"], "dupont": dupont, "structure": structure,
            "cash": cash, "working_capital": wc, "balance": balance}


# ============================================================================ Bước 3 — chất lượng tài sản
def _chk(name, value, status, rule, note=""):
    return {"Chỉ tiêu": name, "Giá trị": value, "Kết quả": status, "Ngưỡng": rule, "Ghi chú": note}


def _band(v, good, bad, higher_better=True):
    """✅ nếu tốt hơn `good`, ❌ nếu tệ hơn `bad`, ⚠️ ở giữa."""
    if v is None:
        return NA
    if higher_better:
        return OK if v >= good else FAIL if v < bad else WARN
    return OK if v <= good else FAIL if v > bad else WARN


def f_score(c: dict, p: dict) -> tuple[Optional[int], int, list[str]]:
    """Piotroski F-score trên 8 tiêu chí (thiếu tiêu chí 'không phát hành thêm cổ phiếu'). Trả (điểm, số tiêu chí
    tính được, danh sách tiêu chí đạt)."""
    passed, n = [], 0

    def t(name, cond):
        nonlocal n
        if cond is None:
            return
        n += 1
        if cond:
            passed.append(name)
    roa = _div(c.get("ni"), c.get("assets"))
    roa_p = _div(p.get("ni"), p.get("assets"))
    t("ROA > 0", None if roa is None else roa > 0)
    t("CFO > 0", None if c.get("cfo") is None else c["cfo"] > 0)
    t("ROA tăng", None if None in (roa, roa_p) else roa > roa_p)
    t("CFO > LN ròng", None if None in (c.get("cfo"), c.get("ni")) else c["cfo"] > c["ni"])
    lev, lev_p = _div(c.get("borrowings"), c.get("assets")), _div(p.get("borrowings"), p.get("assets"))
    t("Đòn bẩy giảm", None if None in (lev, lev_p) else lev <= lev_p)
    cr, cr_p = _div(c.get("current_assets"), c.get("current_liab")), _div(p.get("current_assets"), p.get("current_liab"))
    t("Thanh toán hiện hành tăng", None if None in (cr, cr_p) else cr > cr_p)
    gm, gm_p = _div(c.get("gross"), c.get("revenue")), _div(p.get("gross"), p.get("revenue"))
    t("Biên gộp tăng", None if None in (gm, gm_p) else gm > gm_p)
    at, at_p = _div(c.get("revenue"), c.get("assets")), _div(p.get("revenue"), p.get("assets"))
    t("Vòng quay tài sản tăng", None if None in (at, at_p) else at > at_p)
    return (len(passed) if n >= 6 else None), n, passed


def altman_z(c: dict, market_cap_eok: Optional[float]) -> Optional[float]:
    """Altman Z (bản gốc, giá trị thị trường vốn chủ). Không dùng cho ngành tài chính."""
    ta, tl = c.get("assets"), c.get("liabilities")
    if not ta or not tl or market_cap_eok is None:
        return None
    wc = (c.get("current_assets") or 0) - (c.get("current_liab") or 0)
    return (1.2 * wc / ta + 1.4 * (c.get("retained") or 0) / ta + 3.3 * (c.get("op") or 0) / ta
            + 0.6 * market_cap_eok / tl + 1.0 * (c.get("revenue") or 0) / ta)


def _growth(a, b):
    return (a / b - 1) * 100 if (a is not None and b and b > 0) else None


def quality(fs: dict, market_cap_eok: Optional[float] = None, bucket: Optional[str] = None) -> dict:
    """Chấm chất lượng tài sản bằng ngưỡng số. Trả {checks, score (0–100), grade A–D, n_fail, fin_limited}."""
    c, p = fs["cur"], fs.get("prev") or {}
    fin = bucket in FIN_BUCKETS
    checks = []
    ni, cfo = c.get("ni"), c.get("cfo")
    avg_a = _avg(c.get("assets"), p.get("assets"))
    if not fin:
        acc = _div((ni or 0) - (cfo or 0), avg_a) if (ni is not None and cfo is not None) else None
        checks.append(_chk("Accruals (LN − CFO) / tổng TS", f"{acc * 100:+.1f}%" if acc is not None else "—",
                           _band(acc, 0.05, 0.10, higher_better=False), "≤ 5% ✅ · > 10% ❌",
                           "Lợi nhuận kế toán vượt xa tiền thu về là dấu hiệu chất lượng LN thấp"))
        if ni is not None and ni > 0 and cfo is not None:
            r = cfo / ni
            checks.append(_chk("CFO / LN ròng", f"{r:.2f}×", _band(r, 0.8, 0.5), "≥ 0,8 ✅ · < 0,5 ❌"))
        elif ni is not None and cfo is not None:
            checks.append(_chk("CFO / LN ròng", "LN ≤ 0", OK if cfo > 0 else FAIL, "LN âm: cần CFO > 0",
                               "lỗ kế toán nhưng vẫn thu tiền" if cfo > 0 else "lỗ và âm tiền hoạt động"))
        gr = _growth(c.get("revenue"), p.get("revenue"))
        for nm, key, base in (("Phải thu tăng nhanh hơn doanh thu", "receivables", "revenue"),
                              ("Tồn kho tăng nhanh hơn giá vốn", "inventory", "cogs")):
            g1, g0 = _growth(c.get(key), p.get(key)), (_growth(c.get(base), p.get(base)) if base == "cogs" else gr)
            gap = (g1 - g0) if (g1 is not None and g0 is not None) else None
            checks.append(_chk(nm, f"{gap:+.1f} điểm %" if gap is not None else "—",
                               _band(gap, 10, 25, higher_better=False), "≤ 10 điểm ✅ · > 25 điểm ❌",
                               f"{key} {g1:+.0f}% vs {base} {g0:+.0f}%" if gap is not None else ""))
    eq = c.get("equity") or 0
    gi = c.get("intangibles") if c.get("intangibles") is not None else c.get("goodwill")
    if gi is not None and eq > 0:
        r = gi / eq
        checks.append(_chk("Lợi thế TM + vô hình / VCSH", f"{r * 100:.0f}%", _band(r, 0.20, 0.50, higher_better=False),
                           "≤ 20% ✅ · > 50% ❌", "tài sản vô hình lớn dễ bị ghi giảm"))
    fs_, n, passed = f_score(c, p)
    if fs_ is not None:
        checks.append(_chk(f"Piotroski F-score ({n} tiêu chí)", f"{fs_}/{n}", _band(fs_ / n, 6 / 8, 4 / 8),
                           "≥ 6/8 ✅ · < 4/8 ❌", "đạt: " + ", ".join(passed) if passed else ""))
    if not fin:
        z = altman_z(c, market_cap_eok)
        if z is not None:
            checks.append(_chk("Altman Z", f"{z:.2f}", _band(z, 2.99, 1.81), "> 2,99 ✅ · < 1,81 ❌",
                               "rủi ro kiệt quệ tài chính"))
        ip = c.get("interest_paid") or c.get("fin_cost")
        op = c.get("op")
        if op is not None and ip:
            cov = op / ip
            checks.append(_chk("Khả năng trả lãi (LN HĐ / lãi)", f"{cov:.1f}×", _band(cov, 5, 2), "≥ 5× ✅ · < 2× ❌"))
        borrow = c.get("borrowings") or 0.0
        nd = borrow - (c.get("cash") or 0) - (c.get("st_deposits") or 0)
        if op is not None and op > 0:
            r = nd / op
            checks.append(_chk("Nợ ròng / LN HĐ", "tiền ròng" if nd <= 0 else f"{r:.1f}×",
                               OK if nd <= 0 else _band(r, 2, 4, higher_better=False), "≤ 2× ✅ · > 4× ❌"))
    else:
        ea = _div(c.get("equity"), c.get("assets"))
        checks.append(_chk("VCSH / tổng tài sản", f"{ea * 100:.1f}%" if ea else "—",
                           _band(ea, 0.07, 0.05) if ea else NA, "≥ 7% ✅ · < 5% ❌", "đệm vốn (thô)"))
    scored = [x for x in checks if x["Kết quả"] in (OK, WARN, FAIL)]
    pts = sum(1.0 if x["Kết quả"] == OK else 0.5 if x["Kết quả"] == WARN else 0.0 for x in scored)
    score = pts / len(scored) * 100 if scored else None
    n_fail = sum(1 for x in scored if x["Kết quả"] == FAIL)
    grade = None if score is None else "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
    # cổng số: ≥ 2 ❌ thì không được hạng trên C, dù trung bình cao
    if grade in ("A", "B") and n_fail >= 2:
        grade = "C"
    # ngành tài chính chỉ chấm được 2 chỉ tiêu (không có nợ xấu/dự phòng) → không được hạng A
    if fin and grade == "A":
        grade = "B"
    return {"year": fs["year"], "checks": checks, "score": score, "grade": grade, "n_fail": n_fail,
            "fin_limited": fin}
