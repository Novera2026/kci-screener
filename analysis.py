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


# ============================================================================ Bước 2b — bóc tách theo QUÝ
# DART báo cáo quý (fnlttSinglAcntAll): KQKD (IS/CIS) thstrm_amount = số RIÊNG quý đó, thstrm_add_amount = lũy kế
# từ đầu năm, frmtrm_q_amount = cùng quý năm trước; LCTT (CF) thstrm_amount = LŨY KẾ từ đầu năm; CĐKT (BS) = số
# cuối kỳ. Báo cáo năm: IS/CF = cả năm → Q4 = cả năm − lũy kế 9 tháng (Q3). Kiểm tra trên 005930 (10/11/2026).
Q_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "dart_q")
_RC_Q = (("11013", 1), ("11012", 2), ("11014", 3), ("11011", 4))      # mã báo cáo → quý cuối kỳ
_FLOW_IS = {"IS", "CIS"}


def parse_report(items: list[dict]) -> dict:
    """1 báo cáo DART → {"pt": số theo kỳ (IS quý / BS cuối kỳ / CF lũy kế), "ytd": IS lũy kế, "yoy": IS cùng kỳ
    năm trước, "sj": loại báo cáo của từng trường}. Đơn vị 억원."""
    pt, ytd, yoy, sj = {}, {}, {}, {}
    for field, sjs, ids in ACCOUNTS:
        add = field.startswith("+")
        name = field.lstrip("+")
        for aid in ids:
            hits = [it for it in items if it.get("sj_div") in sjs and it.get("account_id") == aid]
            if not hits:
                continue
            it = hits[0]
            vals = (_n(it.get("thstrm_amount")), _n(it.get("thstrm_add_amount")),
                    _n(it.get("frmtrm_q_amount")) if it.get("frmtrm_q_amount") not in (None, "") else _n(it.get("frmtrm_amount")))
            sj[name] = "IS" if it.get("sj_div") in _FLOW_IS else it.get("sj_div")
            for d, v in zip((pt, ytd, yoy), vals):
                if v is None:
                    continue
                d[name] = d.get(name, 0.0) + abs(v) if add else v
            if not add:
                break
    for d in (pt, ytd, yoy):
        alt = d.pop("borrowings_alt", None)
        if not d.get("borrowings") and alt:
            d["borrowings"] = alt
    sj.pop("borrowings_alt", None)
    return {"pt": pt, "ytd": ytd, "yoy": yoy, "sj": sj}


def _fetch_report(cc: str, year: int, rc: str, fs_div_pref: Optional[str], cache_dir: str) -> Optional[dict]:
    fp = os.path.join(cache_dir, f"{cc}_{year}_{rc}.json")
    if os.path.exists(fp):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    for fs_div in ([fs_div_pref] if fs_div_pref else []) + [d for d in ("CFS", "OFS") if d != fs_div_pref]:
        try:
            j = requests.get(f"{dart_api._BASE}/fnlttSinglAcntAll.json",
                             params={"crtfc_key": dart_api.api_key(), "corp_code": cc, "bsns_year": str(year),
                                     "reprt_code": rc, "fs_div": fs_div}, timeout=20).json()
        except Exception:
            continue
        if j.get("status") != "000":
            continue
        rep = parse_report(j.get("list", []))
        if not rep["pt"].get("assets"):
            continue
        out = {"year": year, "rc": rc, "fs_div": fs_div, **rep}
        json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False)    # báo cáo đã nộp không đổi
        return out
    return None


def fetch_quarter_reports(ticker: str, years: int = 2, cache_dir: str = Q_CACHE) -> list[dict]:
    """Báo cáo quý + năm của `years` năm gần nhất (+ năm trước đó để tính Q4/so cùng kỳ), cũ → mới.
    Cùng loại hợp nhất/riêng với báo cáo đầu tiên lấy được. Không key DART → []."""
    if not dart_api.available():
        return []
    cc = dart_api.corp_code_of(ticker)
    if not cc:
        return []
    os.makedirs(cache_dir, exist_ok=True)
    today = _dt.date.today()
    out, fs_div = [], None
    for y in range(today.year - years, today.year + 1):
        for rc, _q in _RC_Q:
            rep = _fetch_report(cc, y, rc, fs_div, cache_dir)
            if rep:
                fs_div = fs_div or rep["fs_div"]
                out.append(rep)
    return out


def _sub(a, b):
    return a - b if (a is not None and b is not None) else None


def quarter_series(reports: list[dict]) -> list[dict]:
    """Báo cáo DART → từng QUÝ riêng: {label, year, q, flow (IS quý), cf (CF quý), bs (cuối quý), yoy (IS cùng quý
    năm trước)}. Q4 = năm − 9 tháng; CF quý = lũy kế kỳ này − lũy kế kỳ trước cùng năm."""
    by = {(r["year"], dict(_RC_Q)[r["rc"]]): r for r in reports}
    out = []
    for (y, q) in sorted(by):
        r = by[(y, q)]
        sj = r.get("sj", {})
        flow, cf, yoy = {}, {}, {}
        prev = by.get((y, q - 1)) if q > 1 else None
        for k, v in r["pt"].items():
            kind = sj.get(k)
            if kind == "IS":
                if q < 4:
                    flow[k] = v
                    if k in r.get("yoy", {}):
                        yoy[k] = r["yoy"][k]
                else:   # năm − lũy kế 9 tháng; cùng kỳ năm trước = năm trước − 9 tháng năm trước
                    p3 = by.get((y, 3))
                    flow[k] = _sub(v, (p3 or {}).get("ytd", {}).get(k))
                    pfy, p3y = by.get((y - 1, 4)), by.get((y - 1, 3))
                    yoy[k] = _sub((pfy or {}).get("pt", {}).get(k), (p3y or {}).get("ytd", {}).get(k))
            elif kind == "CF":
                cf[k] = v if q == 1 else _sub(v, (prev or {}).get("pt", {}).get(k))
        flow = {k: v for k, v in flow.items() if v is not None}
        out.append({"label": f"Q{q}.{str(y)[2:]}", "year": y, "q": q, "fs_div": r["fs_div"],
                    "flow": flow, "cf": {k: v for k, v in cf.items() if v is not None},
                    "bs": {k: v for k, v in r["pt"].items() if sj.get(k) == "BS"},
                    "yoy": {k: v for k, v in yoy.items() if v is not None}})
    return out


def _pct(a, b):
    return a / b * 100 if (a is not None and b not in (None, 0)) else None


def _growth_pct(cur, prev):
    """Tăng trưởng %; kỳ trước ≤ 0 (lỗ) → không có nghĩa → None."""
    return (cur / prev - 1) * 100 if (cur is not None and prev is not None and prev > 0) else None


def decompose_quarters(qs: list[dict], n: int = 6) -> list[dict]:
    """Bóc tách từng quý (n quý gần nhất): tăng trưởng, biên, DuPont năm hóa, cơ cấu LN trước thuế, dòng tiền,
    vốn lưu động, nợ ròng. Bình quân TS/VCSH = (cuối quý này + cuối quý trước)/2."""
    rows = []
    for i, qd in enumerate(qs):
        f, cf, bs, yoy = qd["flow"], qd["cf"], qd["bs"], qd["yoy"]
        pb = qs[i - 1]["bs"] if i > 0 else {}
        prev_q = qs[i - 1]["flow"] if i > 0 else {}
        rev, op, pre = f.get("revenue"), f.get("op"), f.get("pretax")
        ni = f.get("ni_parent") if f.get("ni_parent") is not None else f.get("ni")
        eq_c, eq_p = bs.get("equity_parent") or bs.get("equity"), pb.get("equity_parent") or pb.get("equity")
        avg_a, avg_e = _avg(bs.get("assets"), pb.get("assets")), _avg(eq_c, eq_p)
        margin, turn, lev = _div(ni, rev), _div(rev * 4 if rev is not None else None, avg_a), _div(avg_a, avg_e)
        fin_net = ((f.get("fin_income") or 0.0) - (f.get("fin_cost") or 0.0)
                   if (f.get("fin_income") is not None or f.get("fin_cost") is not None) else None)
        eqi = f.get("equity_income")
        other = (pre - (op or 0) - (fin_net or 0) - (eqi or 0)) if pre is not None else None
        capex, cfo = cf.get("capex"), cf.get("cfo")
        fcf = _sub(cfo, capex)
        liquid = (bs.get("cash") or 0.0) + (bs.get("st_deposits") or 0.0)
        rows.append({
            "Quý": qd["label"],
            "Doanh thu (억)": rev, "DT YoY %": _growth_pct(rev, yoy.get("revenue")),
            "DT QoQ %": _growth_pct(rev, prev_q.get("revenue")),
            "LN HĐ (억)": op, "LN HĐ YoY %": _growth_pct(op, yoy.get("op")),
            "LN ròng CĐ mẹ (억)": ni,
            "Biên gộp %": _pct(f.get("gross"), rev), "Biên LN HĐ %": _pct(op, rev), "Biên LN ròng %": _pct(ni, rev),
            "ROE năm hóa %": margin * turn * lev * 100 if None not in (margin, turn, lev) else None,
            "Vòng quay TS (năm hóa)": turn, "Đòn bẩy (TS/VCSH)": lev,
            "LN HĐ / LNTT %": _pct(op, pre), "Tài chính ròng / LNTT %": _pct(fin_net, pre),
            "Liên kết / LNTT %": _pct(eqi, pre), "Khác / LNTT %": _pct(other, pre),
            "CFO (억)": cfo, "CFO / LN ròng": _div(cfo, f.get("ni")) if (f.get("ni") or 0) > 0 else None,
            "Capex (억)": capex, "FCF (억)": fcf, "Biên FCF %": _pct(fcf, rev),
            "Ngày phải thu": _div(bs.get("receivables"), rev) * 91 if _div(bs.get("receivables"), rev) is not None else None,
            "Ngày tồn kho": _div(bs.get("inventory"), f.get("cogs")) * 91 if _div(bs.get("inventory"), f.get("cogs")) is not None else None,
            "Nợ ròng (억)": (bs.get("borrowings") or 0.0) - liquid if bs else None,
        })
    return rows[-n:]


def quarter_flags(rows: list[dict]) -> list[str]:
    """Tín hiệu số từ quý gần nhất (so quý trước & cùng kỳ) — chỉ nêu, không chấm điểm."""
    if not rows:
        return []
    L, c = [], rows[-1]
    p = rows[-2] if len(rows) > 1 else {}
    q = c["Quý"]
    if c.get("DT YoY %") is not None:
        L.append(f"{'📈' if c['DT YoY %'] >= 0 else '📉'} {q}: doanh thu {c['DT YoY %']:+.1f}% so cùng kỳ")
    if c.get("LN HĐ YoY %") is not None:
        L.append(f"{'📈' if c['LN HĐ YoY %'] >= 0 else '📉'} {q}: LN hoạt động {c['LN HĐ YoY %']:+.1f}% so cùng kỳ")
    if c.get("LN HĐ (억)") is not None and c["LN HĐ (억)"] < 0:
        L.append(f"❌ {q}: lỗ hoạt động {c['LN HĐ (억)']:,.0f}억")
    if c.get("Biên LN HĐ %") is not None and p.get("Biên LN HĐ %") is not None:
        d = c["Biên LN HĐ %"] - p["Biên LN HĐ %"]
        if abs(d) >= 2:
            L.append(f"{'✅' if d > 0 else '⚠️'} Biên LN HĐ {p['Biên LN HĐ %']:.1f}% → {c['Biên LN HĐ %']:.1f}% ({d:+.1f} điểm %)")
    if c.get("Khác / LNTT %") is not None and abs(c["Khác / LNTT %"]) >= 30:
        L.append(f"⚠️ {q}: khoản khác/một lần chiếm {c['Khác / LNTT %']:.0f}% LN trước thuế — LN quý khó lặp lại")
    if c.get("CFO (억)") is not None and c["CFO (억)"] < 0:
        L.append(f"⚠️ {q}: dòng tiền hoạt động âm {c['CFO (억)']:,.0f}억")
    neg = [r for r in rows[-4:] if r.get("FCF (억)") is not None and r["FCF (억)"] < 0]
    if len(neg) >= 3:
        L.append(f"⚠️ FCF âm {len(neg)}/4 quý gần nhất")
    if c.get("Ngày tồn kho") is not None and p.get("Ngày tồn kho"):
        if c["Ngày tồn kho"] > p["Ngày tồn kho"] * 1.2:
            L.append(f"⚠️ Ngày tồn kho {p['Ngày tồn kho']:.0f} → {c['Ngày tồn kho']:.0f} ngày (+>20%)")
    return L


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
            "fin_limited": fin, "fscore": fs_, "fscore_n": n,
            "current_ratio": _div(c.get("current_assets"), c.get("current_liab")),
            "debt_equity": _div(c.get("liabilities"), c.get("equity"))}
