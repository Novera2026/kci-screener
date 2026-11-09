"""Bóc tách / chất lượng tài sản (analysis.py), đối thủ (peers.py), hồ sơ (dossier.py) — không cần mạng."""
import pytest

import analysis as AN
import dossier as DS
import peers as PR


def _it(sj, aid, cur, prev):
    return {"sj_div": sj, "account_id": aid, "thstrm_amount": str(int(cur * 1e8)), "frmtrm_amount": str(int(prev * 1e8))}


def _items(**over):
    base = dict(assets=(1000, 900), current_assets=(400, 350), current_liab=(200, 200), liabilities=(400, 380),
                equity=(600, 520), revenue=(800, 700), cogs=(500, 450), gross=(300, 250), op=(100, 80),
                ni=(80, 60), cfo=(110, 90), receivables=(100, 90), inventory=(80, 75), retained=(300, 250),
                intangibles=(50, 50), cash=(100, 80), fin_cost=(10, 10), pretax=(100, 80), tax=(20, 20))
    base.update(over)
    ids = {"assets": ("BS", "ifrs-full_Assets"), "current_assets": ("BS", "ifrs-full_CurrentAssets"),
           "current_liab": ("BS", "ifrs-full_CurrentLiabilities"), "liabilities": ("BS", "ifrs-full_Liabilities"),
           "equity": ("BS", "ifrs-full_Equity"), "revenue": ("IS", "ifrs-full_Revenue"),
           "cogs": ("IS", "ifrs-full_CostOfSales"), "gross": ("IS", "ifrs-full_GrossProfit"),
           "op": ("IS", "dart_OperatingIncomeLoss"), "ni": ("IS", "ifrs-full_ProfitLoss"),
           "cfo": ("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities"),
           "receivables": ("BS", "ifrs-full_CurrentTradeReceivables"), "inventory": ("BS", "ifrs-full_Inventories"),
           "retained": ("BS", "ifrs-full_RetainedEarnings"), "intangibles": ("BS", "ifrs-full_IntangibleAssetsAndGoodwill"),
           "cash": ("BS", "ifrs-full_CashAndCashEquivalents"), "fin_cost": ("IS", "ifrs-full_FinanceCosts"),
           "pretax": ("IS", "ifrs-full_ProfitLossBeforeTax"), "tax": ("IS", "ifrs-full_IncomeTaxExpenseContinuingOperations")}
    return [_it(ids[k][0], ids[k][1], *v) for k, v in base.items()]


def _fs(**over):
    cur, prev = AN.parse_statements(_items(**over))
    return {"year": 2025, "fs_div": "CFS", "cur": cur, "prev": prev}


def test_parse_statements_priority_and_borrowing_fallback():
    items = _items() + [_it("BS", "ifrs-full_OtherCurrentFinancialLiabilities", 30, 20),
                        _it("BS", "ifrs-full_OtherNoncurrentFinancialLiabilities", 70, 60)]
    cur, prev = AN.parse_statements(items)
    assert cur["borrowings"] == pytest.approx(100) and prev["borrowings"] == pytest.approx(80)   # dự phòng
    items.append(_it("BS", "ifrs-full_ShorttermBorrowings", 40, 40))
    cur, _ = AN.parse_statements(items)
    assert cur["borrowings"] == pytest.approx(40)                     # có 차입금 → không cộng dự phòng


def test_dupont_multiplies_to_roe():
    d = AN.decompose(_fs())
    dp = d["dupont"]
    assert dp["ROE = tích ba thành phần %"] == pytest.approx(80 / ((600 + 520) / 2) * 100)


def test_quality_clean_company_scores_high():
    q = AN.quality(_fs(), market_cap_eok=1500, bucket="Consumer/Retail")
    assert q["grade"] in ("A", "B") and q["n_fail"] == 0


def test_quality_flags_accruals_and_receivables_and_caps_grade():
    q = AN.quality(_fs(ni=(150, 60), cfo=(10, 90), receivables=(200, 90)), market_cap_eok=1500,
                   bucket="Consumer/Retail")
    res = {c["Chỉ tiêu"]: c["Kết quả"] for c in q["checks"]}
    assert res["Accruals (LN − CFO) / tổng TS"] == "❌"
    assert res["Phải thu tăng nhanh hơn doanh thu"] == "❌"
    assert q["grade"] not in ("A", "B")                               # ≥ 2 ❌ → tối đa C


def test_financial_sector_limited_and_capped():
    q = AN.quality(_fs(), market_cap_eok=1500, bucket="Bank")
    assert q["fin_limited"] and q["grade"] != "A"
    assert not any("Altman" in c["Chỉ tiêu"] for c in q["checks"])


def test_altman_and_fscore_ranges():
    fs = _fs()
    z = AN.altman_z(fs["cur"], 1500)
    assert 2 < z < 10
    score, n, passed = AN.f_score(fs["cur"], fs["prev"])
    assert n >= 6 and 0 <= score <= n


# ---------------------------------------------------------------- peers
def _p(code, pe, pb, g=10, om=10, roe=10):
    return {"Mã": code, "P/E": pe, "P/B": pb, "Tăng trưởng DT 3 năm %": g, "Biên LN HĐ %": om,
            "Biên LN ròng %": om * 0.8, "ROE %": roe, "Tỷ suất cổ tức %": 1.0}


def test_peer_medians_drop_absurd_pe_and_position():
    dom = [_p("a", 10, 1), _p("b", 12, 1.2), _p("c", 900, 5)]          # P/E 900 bị loại khỏi trung vị
    med = PR.peer_medians(dom)
    assert med["P/E"] == pytest.approx(11)
    pos = {r["Chỉ số"]: r for r in PR.position(_p("x", 8, 0.9), dom, med, {"P/E": 20})}
    assert pos["P/E"]["Nhận xét"].startswith("rẻ hơn")
    assert PR.foreign_discount(_p("x", 8, 0.9), {"P/E": 20, "P/B": 2})["P/E"] == pytest.approx(-60)


def test_foreign_peers_excluded_from_nothing_but_listed():
    assert all(isinstance(v, list) and v for v in PR.FOREIGN_PEERS.values())


# ---------------------------------------------------------------- dossier
def test_dossier_roundtrip(tmp_path):
    import datetime as dt
    d = dt.date(2026, 11, 10)
    DS.save_section("005930", "peers", {"x": float("nan"), "y": (1, 2)}, d, str(tmp_path), meta={"name": "S"})
    DS.save_section("005930", "quality", {"grade": "A"}, d, str(tmp_path))
    assert DS.section("005930", "peers", d, str(tmp_path)) == {"x": None, "y": [1, 2]}
    lst = DS.list_dossiers(str(tmp_path))
    assert lst[0]["sections"] == ["peers", "quality"] and lst[0]["name"] == "S"


# ---------------------------------------------------------------- sàng lọc
def test_cheap_stage_relative_to_industry_and_filters():
    import screen as SC
    rows = [{"ticker": f"{i:06d}", "name": f"n{i}", "industry_kr": "식품", "price": 100, "per": pe, "pbr": pb,
             "div_yield": 2.0} for i, (pe, pb) in enumerate([(5, 0.5), (10, 1.0), (12, 1.2), (14, 1.4), (-3, 1.0)])]
    out = SC.cheap_stage(rows, max_pe=15, max_rel=0.8)
    first = out[0]
    assert first["Mã"] == "000000" and first["Qua lọc"]                  # rẻ nhất ngành, đứng đầu
    loss = next(r for r in out if r["Mã"] == "000004")
    assert not loss["Qua lọc"] and "lỗ" in loss["Lý do loại"]
    assert not next(r for r in out if r["Mã"] == "000003")["Qua lọc"]    # đắt hơn ngành


def test_rank_orders_by_verdict_then_upside():
    import screen as SC
    res = [{"Mã": "a", "Kết luận": "⚪ Giá gần giá trị ước tính", "Chênh %": 50},
           {"Mã": "b", "Kết luận": "🟢 Rẻ hơn …", "Chênh %": 20},
           {"Mã": "c", "Kết luận": "🟢 Rẻ hơn …", "Chênh %": 40},
           {"Mã": "d", "Kết luận": "⚪ Chưa đủ cơ sở định giá", "Chênh %": None}]
    assert [r["Mã"] for r in SC.rank(res)] == ["c", "b", "a", "d"]
