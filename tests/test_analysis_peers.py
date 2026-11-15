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


# ---------------------------------------------------------------- bộ tiêu chuẩn rẻ
def _r(tk, pe, pb, div=2.0, mcap=2e12, ik="식품", price=10000):
    eps = price / pe if pe and pe > 0 else -100
    bps = price / pb
    return {"ticker": tk, "name": tk, "industry_kr": ik, "price": price, "per": pe if pe and pe > 0 else None,
            "pbr": pb, "eps": eps, "bps": bps, "div_yield": div, "market_cap": mcap}


def test_every_preset_runs_and_documents_source():
    import screen as SC
    rows = [_r(f"{i:06d}", pe, pb) for i, (pe, pb) in enumerate([(5, .5), (8, .9), (12, 1.2), (20, 3), (-1, 1)])]
    for k, p in SC.PRESETS.items():
        assert p["source"] and p["desc"] and "stage1" in p and "stage2" in p
        out = SC.apply_preset(rows, k)
        assert len(out) == len(rows)
        assert all(("Qua lọc" in x and "Lý do loại" in x) for x in out)


def test_graham_defensive_rules():
    import screen as SC
    rows = [_r("a", 10, 1.0), _r("b", 14, 1.8), _r("c", 12, 1.4, div=0.0), _r("d", 9, 1.2, mcap=5e11)]
    ok = {x["Mã"]: x["Qua lọc"] for x in SC.apply_preset(rows, "graham")}
    assert ok == {"a": True, "b": False, "c": False, "d": False}     # P/B 1,8 · không cổ tức · vốn hóa nhỏ


def test_graham_number_margin_sorted():
    import screen as SC
    out = SC.apply_preset([_r("a", 10, 1.0), _r("b", 6, 0.8), _r("c", 20, 2.0)], "graham_number")
    assert [x["Mã"] for x in out if x["Qua lọc"]] == ["b", "a"]       # c: 40 > 22,5
    assert out[0]["Biên an toàn Graham %"] > out[1]["Biên an toàn Graham %"]


def test_magic_formula_ranks_and_excludes_financials():
    import screen as SC
    rows = [_r(f"{i:06d}", pe, pb) for i, (pe, pb) in enumerate([(5, 1.5), (6, .6), (9, 2.0), (15, 1.0)])]
    rows.append(_r("bank01", 4, 0.4, ik="은행"))
    out = SC.apply_preset(rows, "magic", {"magic_top": 2})
    kept = [x["Mã"] for x in out if x["Qua lọc"]]
    assert len(kept) == 2 and "bank01" not in kept
    assert "000000" in kept                                            # E/P cao nhất và ROE cao nhất (5×… ROE 30%)


def test_piotroski_bottom_pb_and_stage2():
    import screen as SC
    rows = [_r(f"{i:06d}", 10, pb) for i, pb in enumerate([0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0])]
    passed = [x["Mã"] for x in SC.apply_preset(rows, "piotroski") if x["Qua lọc"]]
    assert passed == ["000000", "000001"]                              # 20% P/B thấp nhất
    assert SC.stage2_check({"F-score": 7}, "piotroski")[0]
    assert not SC.stage2_check({"F-score": 5}, "piotroski")[0]
    assert not SC.stage2_check({"F-score": None}, "piotroski")[0]


def test_stage2_graham_and_valueup():
    import screen as SC
    good = {"Thanh toán hiện hành": 1.8, "Có năm lỗ": False, "Ngành": "식품"}
    assert SC.stage2_check(good, "graham")[0]
    assert not SC.stage2_check({**good, "Có năm lỗ": True}, "graham")[0]
    assert SC.stage2_check({"Thanh toán hiện hành": None, "Có năm lỗ": False, "Ngành": "은행"}, "graham")[0]
    assert not SC.stage2_check({"Chất lượng TS": "B", "Nợ/VCSH": 3.0, "Ngành": "식품"}, "valueup")[0]
    assert SC.stage2_check({"Chất lượng TS": "B", "Nợ/VCSH": 3.0, "Ngành": "은행"}, "valueup")[0]


# ================================================================ rà soát 15/11/2026 — đợt 1
def _bs(nm, cur, prev=0.0, aid="-표준계정코드 미사용-"):
    return {"sj_div": "BS", "account_id": aid, "account_nm": nm, "thstrm_amount": str(int(cur * 1e8)),
            "frmtrm_amount": str(int(prev * 1e8))}


def test_borrowings_read_by_line_name_when_ids_are_non_standard():
    # KT&G: mọi dòng nợ vay ghi "-표준계정코드 미사용-" hoặc mã dart_ khác → đọc theo mã ra 0 dù trả lãi 732억
    items = _items() + [_bs("단기차입금", 3330, 3000), _bs("유동성 사채", 3297), _bs("유동성장기차입금", 689),
                        _bs("유동성전환사채", 237, aid="dart_CurrentPortionOfConvertibleBonds"), _bs("장기사채", 9074),
                        _bs("장기차입금", 1064), _bs("장기전환사채", 169, aid="dart_ConvertibleBondsNet"),
                        # không phải nợ vay: cho vay, phái sinh, thuê tài chính, nợ tài chính khác
                        _bs("단기대여금", 500), _bs("유동파생상품금융부채", 40), _bs("리스부채", 300),
                        _bs("기타유동금융부채", 900, aid="ifrs-full_OtherCurrentFinancialLiabilities")]
    cur, prev = AN.parse_statements(items)
    assert cur["borrowings"] == pytest.approx(3330 + 3297 + 689 + 237 + 9074 + 1064 + 169)
    assert prev["borrowings"] == pytest.approx(3000)
    assert "borrowings_alt" not in cur
    # không dòng nào mang tên nợ vay → cách cũ: 금융부채 (HD한국조선해양)
    cur2, _ = AN.parse_statements(_items() + [_bs("단기금융부채", 700, aid="ifrs-full_OtherCurrentFinancialLiabilities")])
    assert cur2["borrowings"] == pytest.approx(700)


def test_equity_method_income_from_split_or_named_lines():
    def _is(nm, v, aid="-표준계정코드 미사용-"):
        return {"sj_div": "IS", "account_id": aid, "account_nm": nm, "thstrm_amount": str(int(v * 1e8)), "frmtrm_amount": "0"}
    split = _items() + [_is("지분법이익", 904, "dart_ProfitsOfAssociatesAndJointVenturesAccountedForUsingEquityMethod"),
                        _is("지분법손실", 11, "dart_LossesOfAssociatesAndJointVenturesAccountedForUsingEquityMethod")]
    assert AN.parse_statements(split)[0]["equity_income"] == pytest.approx(893)
    named = _items() + [_is("공동기업 및 관계기업투자손익", 25101), _is("지분법이익잉여금", -3116)]
    cur = AN.parse_statements(named)[0]
    assert cur["equity_income"] == pytest.approx(25101) and "equity_gain" not in cur


def _dart_ok(monkeypatch):
    monkeypatch.setattr(AN.dart_api, "available", lambda: True)
    monkeypatch.setattr(AN.dart_api, "corp_code_of", lambda t: "00000001")


def test_fetch_full_never_caches_or_downgrades_after_dart_error(monkeypatch, tmp_path):
    _dart_ok(monkeypatch)
    seen = []

    def fs(cc, y, rc="11011", fs_div="CFS", timeout=None):
        seen.append(fs_div)
        if fs_div == "CFS":
            raise AN.dart_api.DartError("lỗi kết nối DART (ConnectionError)")
        return _items(revenue=(787, 700))                                        # báo cáo RIÊNG LẺ: số nhỏ hơn hẳn
    monkeypatch.setattr(AN.dart_api, "fetch_statements", fs)
    errs = []
    assert AN.fetch_full("005380", 2025, cache_dir=str(tmp_path), errors=errs) is None
    assert seen == ["CFS"] and errs and not list(tmp_path.iterdir())             # không lùi OFS, không ghi cache
    # DART xác nhận không có bản hợp nhất (None) → mới dùng bản riêng lẻ, và lúc đó mới lưu
    monkeypatch.setattr(AN.dart_api, "fetch_statements",
                        lambda cc, y, rc="11011", fs_div="CFS", timeout=None: None if fs_div == "CFS" else _items())
    out = AN.fetch_full("005380", 2025, cache_dir=str(tmp_path))
    assert out["fs_div"] == "OFS" and out["currency"] == "KRW" and out["_v"] == AN.FULL_V
    assert len(list(tmp_path.iterdir())) == 1


def test_fetch_full_refreshes_old_cache_and_keeps_it_on_error(monkeypatch, tmp_path):
    import json
    _dart_ok(monkeypatch)
    fp = tmp_path / "00000001_2025.json"
    fp.write_text(json.dumps({"year": 2025, "fs_div": "OFS", "cur": {"assets": 1.0, "revenue": 787.0}, "prev": {}}))
    # cache đời cũ (có thể là OFS lưu nhầm) + DART đang lỗi → dùng tạm bản cũ nhưng báo lỗi, không ghi đè
    def boom(*a, **k):
        raise AN.dart_api.DartError("DART đang từ chối kết nối")
    monkeypatch.setattr(AN.dart_api, "fetch_statements", boom)
    errs = []
    old = AN.fetch_full("005380", 2025, cache_dir=str(tmp_path), errors=errs)
    assert old["cur"]["revenue"] == 787.0 and errs
    # DART trả lời → thay bằng bản hợp nhất đúng
    monkeypatch.setattr(AN.dart_api, "fetch_statements",
                        lambda cc, y, rc="11011", fs_div="CFS", timeout=None: _items(revenue=(1862, 1700)))
    new = AN.fetch_full("005380", 2025, cache_dir=str(tmp_path))
    assert new["fs_div"] == "CFS" and new["cur"]["revenue"] == pytest.approx(1862)
    assert json.loads(fp.read_text())["_v"] == AN.FULL_V


def test_quality_skips_altman_when_market_cap_not_comparable():
    q = AN.quality(_fs(), None, "Consumer/Retail")              # BCTC bằng USD → không truyền vốn hóa KRW
    assert not any(c["Chỉ tiêu"] == "Altman Z" for c in q["checks"])


# ---- một đường định giá cho mọi trang ----
def test_peer_rows_batch_builds_canonical_set_once(monkeypatch):
    import pandas as pd
    import pipeline as P
    import screener

    class Reg:
        df = pd.DataFrame({"ticker": ["000001", "000002", "000003", "000004", "000005"]})
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: {"000001": ["000003", "999999"], "000002": []}[tk])
    ranked_calls = []

    def ranked(ik):
        ranked_calls.append(ik)
        return ["000001", "000002", "000004", "000005"]
    monkeypatch.setattr(P, "_industry_ranked", ranked)
    fetched = []

    def fake_fetch_many(tks, registry=None, prefer=None):
        fetched.extend(tks)
        return [{"ticker": t, "per": 10.0, "pbr": 1.0} for t in tks], []
    monkeypatch.setattr(screener, "fetch_many", fake_fetch_many)
    have = {"000001": {"ticker": "000001", "per": 9.0}, "000002": {"ticker": "000002", "per": 8.0}}
    out = PR.peer_rows_batch([("000001", "식품"), ("000002", "식품")], Reg(), have, n=3)
    # gợi ý Naver trước, rồi top vốn hóa cùng ngành; bỏ chính mã và mã ngoài danh mục (999999)
    assert [r["ticker"] for r in out["000001"]] == ["000003", "000002", "000004"]
    assert [r["ticker"] for r in out["000002"]] == ["000001", "000004", "000005"]
    assert out["000001"][1]["per"] == 8.0                           # dòng đã có trong lần chạy được dùng lại
    assert sorted(fetched) == ["000003", "000004", "000005"]        # chỉ lấy mới các mã còn thiếu, một lượt
    assert ranked_calls == ["식품"]                                  # danh sách ngành gọi đúng một lần


def test_screening_uses_the_shared_valuation_path(monkeypatch):
    """Sàng lọc/bot phải đi qua pipeline.assess với đúng bộ đối thủ được trao — không tự dựng phép neo riêng."""
    import financials
    import pipeline as P
    import screen as SC
    import screener
    import valuation as V
    seen = {}
    real_assess = P.assess

    def spy(tk, row, fin, a, peers, beta, **kw):
        seen.update(tk=tk, peers=peers, beta=beta, kw=kw)
        return real_assess(tk, row, fin, a, peers, beta, **kw)
    monkeypatch.setattr(P, "assess", spy)
    monkeypatch.setattr(financials, "fetch_financials", lambda tk: {"annual": [], "quarter": []})
    monkeypatch.setattr(financials, "dart_api", None)
    monkeypatch.setattr(screener, "fetch_weekly_closes", lambda s: [])
    monkeypatch.setattr(AN, "fetch_full", lambda tk, errors=None: None)
    monkeypatch.setattr(SC.DS, "save_section", lambda *a, **k: None)
    row = {"ticker": "000001", "name": "T", "price": 10_000.0, "market_cap": 1e11, "eps": 1_000.0, "bps": 10_000.0,
           "dps": 0.0, "per": 10.0, "pbr": 1.0, "industry_kr": "식품", "sector": None}
    peers = [{"ticker": t, "per": pe, "pbr": pb} for t, pe, pb in (("a", 10, 1.0), ("b", 12, 1.2), ("c", 14, 1.4))]
    feeds = {"rf": {"ok": False, "note": "Không lấy được Rf — dùng mặc định 3,2%"}}
    out = SC.analyze_one("000001", row, [row], V.Assumptions(rf=0.032), None, [], save=False, peers=peers, feeds=feeds)
    assert seen["tk"] == "000001" and seen["peers"] is peers and seen["kw"]["feeds"] is feeds
    assert seen["beta"] is None
    assert "Rf" in out["Cảnh báo dữ liệu"] and "Beta" in out["Cảnh báo dữ liệu"]      # lỗi nguồn hiện ra ở kết quả
    assert out["Độ tin cậy"] in ("Low", "—")


def test_bond_discount_lines_reduce_borrowings():
    items = _items() + [_bs("사채", 1000), _bs("사채할인발행차금", -30), _bs("장기차입금", 200),
                        _bs("현재가치할인차금", 5)]
    # 현재가치할인차금 không gắn riêng với nợ vay (có thể thuộc khoản phải trả khác) → không tính vào nợ vay
    assert AN.parse_statements(items)[0]["borrowings"] == pytest.approx(1000 - 30 + 200)
