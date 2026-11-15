"""Bóc tách theo quý: tách số quý từ báo cáo DART lũy kế — không cần mạng."""
import pytest

import analysis as A


def _rep(year, rc, pt, ytd=None, yoy=None):
    sj = {k: ("BS" if k in ("assets", "equity", "cash", "borrowings", "receivables", "inventory")
              else "CF" if k in ("cfo", "capex") else "IS") for k in pt}
    return {"year": year, "rc": rc, "fs_div": "CFS", "pt": pt, "ytd": ytd or {}, "yoy": yoy or {}, "sj": sj}


def _year(y, revs, cfo_ytd, base=100.0):
    """4 báo cáo của năm y: doanh thu từng quý `revs`, CFO lũy kế `cfo_ytd` (Q1, H1, 9T, năm)."""
    ytd = [sum(revs[:i + 1]) for i in range(4)]
    out = []
    for i, rc in enumerate(("11013", "11012", "11014", "11011")):
        rev_pt = revs[i] if i < 3 else ytd[3]                       # báo cáo năm: IS = cả năm
        out.append(_rep(y, rc, {"revenue": rev_pt, "op": rev_pt * 0.1, "pretax": rev_pt * 0.1, "ni": rev_pt * 0.08,
                                "cfo": cfo_ytd[i], "capex": 5.0 * (i + 1), "assets": base * 10, "equity": base * 5,
                                "cash": 50.0, "borrowings": 20.0},
                        ytd={"revenue": ytd[i], "op": ytd[i] * 0.1, "pretax": ytd[i] * 0.1, "ni": ytd[i] * 0.08}))
    return out


def test_q4_and_cashflow_derived_from_cumulative():
    reps = _year(2024, [100, 110, 120, 130], [10, 25, 45, 70]) + _year(2025, [120, 130, 150, 200], [12, 30, 50, 90])
    qs = A.quarter_series(reps)
    assert [q["label"] for q in qs] == ["Q1.24", "Q2.24", "Q3.24", "Q4.24", "Q1.25", "Q2.25", "Q3.25", "Q4.25"]
    q4 = qs[-1]
    assert q4["flow"]["revenue"] == 200                    # 600 cả năm − 400 lũy kế 9 tháng
    assert q4["yoy"]["revenue"] == 130                     # Q4.24 = 460 − 330
    assert [q["cf"]["cfo"] for q in qs[4:]] == [12, 18, 20, 40]   # lũy kế → riêng quý
    rows = A.decompose_quarters(qs, n=4)
    assert [r["Quý"] for r in rows] == ["Q1.25", "Q2.25", "Q3.25", "Q4.25"]
    assert round(rows[-1]["DT YoY %"], 1) == round((200 / 130 - 1) * 100, 1)
    assert round(rows[-1]["DT QoQ %"], 1) == round((200 / 150 - 1) * 100, 1)
    assert rows[-1]["Biên LN HĐ %"] == 10.0
    assert rows[-1]["Nợ ròng (억)"] == -30.0


def test_growth_not_computed_from_loss_base():
    assert A._growth_pct(50, -10) is None and A._growth_pct(50, 0) is None
    assert A._growth_pct(None, 10) is None


def test_flags_negative_cfo_and_margin_jump():
    rows = [{"Quý": "Q1.25", "Biên LN HĐ %": 5.0, "CFO (억)": 10, "FCF (억)": -1},
            {"Quý": "Q2.25", "Biên LN HĐ %": 9.0, "CFO (억)": -5, "FCF (억)": -2, "DT YoY %": 12.0}]
    f = A.quarter_flags(rows)
    assert any("Biên LN HĐ" in x and "+4.0" in x for x in f)
    assert any("dòng tiền hoạt động âm" in x for x in f)
    assert any("+12.0%" in x for x in f)


# ================================================================ rà soát 15/11/2026 — Q4 khi báo cáo năm điều chỉnh hồi tố
def test_q4_not_derived_when_annual_report_restated():
    """Samsung Biologics 2025: báo cáo năm lập sau tách công ty (45.570) trong khi 9 tháng đã nộp là trước tách
    (42.484) → 'Q4' = 3.086 là số vô nghĩa. Báo cáo quý 2026 ghi lại cùng quý 2025 theo cơ sở mới."""
    y25 = _year(2025, [130, 129, 166, 0], [10, 20, 30, 40])
    y25[3]["pt"]["revenue"] = 456.0                                   # cả năm theo cơ sở MỚI (nhỏ hơn 9 tháng cũ + Q4)
    y25[3]["pt"]["op"] = 207.0
    y26 = _year(2026, [126, 132, 0, 0], [12, 24, 0, 0])[:2]
    y26[0]["yoy"] = {"revenue": 100.0, "op": 43.0}                    # Q1.25 theo cơ sở mới (đã nộp: 130)
    y26[1]["yoy"] = {"revenue": 101.0, "op": 48.0}                    # Q2.25 theo cơ sở mới (đã nộp: 129)
    qs = {q["label"]: q for q in A.quarter_series(y25 + y26)}
    assert qs["Q1.25"]["flow"]["revenue"] == 100.0 and "điều chỉnh hồi tố" in qs["Q1.25"]["note"]
    assert qs["Q1.25"]["yoy"] == {}                                   # không có 'cùng kỳ' cùng cơ sở → không tính YoY
    assert qs["Q3.25"]["flow"]["revenue"] == 166 and "CHƯA cùng cơ sở" in qs["Q3.25"]["note"]
    assert qs["Q4.25"]["flow"] == {} and qs["Q4.25"]["cf"] == {} and "không tách được Q4" in qs["Q4.25"]["note"]
    rows = A.decompose_quarters(list(A.quarter_series(y25 + y26)), n=6)
    q4 = next(r for r in rows if r["Quý"] == "Q4.25")
    assert q4["Doanh thu (억)"] is None and q4["LN HĐ (억)"] is None
    assert any(f.startswith("⚠️ Q4.25") for f in A.quarter_flags(rows))


def test_q4_rebuilt_once_all_three_restated_quarters_exist():
    y25 = _year(2025, [130, 129, 166, 0], [10, 20, 30, 40])
    y25[3]["pt"]["revenue"] = 456.0
    y26 = _year(2026, [126, 132, 150, 0], [12, 24, 36, 0])[:3]
    for rep, v in zip(y26, (100.0, 101.0, 125.0)):
        rep["yoy"] = {"revenue": v}
    qs = {q["label"]: q for q in A.quarter_series(y25 + y26)}
    assert qs["Q4.25"]["flow"]["revenue"] == pytest.approx(456.0 - 326.0)   # cả năm − ba quý đã điều chỉnh


def test_q4_gate_blocks_absurd_subtraction_without_next_year_reports():
    y25 = _year(2025, [130, 129, 166, 0], [10, 20, 30, 40])
    y25[3]["pt"]["revenue"] = 456.0                                   # 456 − 425 = 31: < 1/3 trung vị Q1–Q3 (130)
    qs = A.quarter_series(y25)
    assert qs[-1]["label"] == "Q4.25" and qs[-1]["flow"] == {} and "phi lý" in qs[-1]["note"]
    # Q4 mùa vụ bình thường (gấp 1,5 lần) không bị chặn
    ok = A.quarter_series(_year(2025, [120, 130, 150, 200], [12, 30, 50, 90]))
    assert ok[-1]["flow"]["revenue"] == 200 and ok[-1]["note"] == ""


def test_quarter_reports_keep_old_cache_when_dart_is_down(monkeypatch, tmp_path):
    """DART đang chặn: báo cáo quý đã lưu (đời cũ) vẫn dùng được và lỗi được báo lên; không có bản lưu thì dừng, báo lỗi."""
    import json
    monkeypatch.setattr(A.dart_api, "available", lambda: True)
    monkeypatch.setattr(A.dart_api, "corp_code_of", lambda t: "00000001")

    def down(*a, **k):
        raise A.dart_api.DartError("DART đang từ chối kết nối")
    monkeypatch.setattr(A.dart_api, "fetch_statements", down)
    import datetime as dt
    y = dt.date.today().year - 2
    old = {"year": y, "rc": "11013", "fs_div": "CFS", "pt": {"assets": 1.0, "revenue": 5.0}, "ytd": {}, "yoy": {},
           "sj": {"assets": "BS", "revenue": "IS"}}
    (tmp_path / f"00000001_{y}_11013.json").write_text(json.dumps(old))
    errs = []
    out = A.fetch_quarter_reports("005930", years=2, cache_dir=str(tmp_path), errors=errs)
    assert [r["rc"] for r in out] == ["11013"] and errs               # dùng bản cũ, có báo lỗi, dừng ở báo cáo kế tiếp
    assert len(list(tmp_path.iterdir())) == 1                         # không ghi gì thêm khi DART lỗi


# ================================================================ lượt soát độc lập 15/11/2026
def _row(sj, aid, nm, cur, add=None, prev=None):
    d = {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": str(int(cur * 1e8))}
    if add is not None:
        d["thstrm_add_amount"] = str(int(add * 1e8))
    if prev is not None:
        d["frmtrm_q_amount"] = str(int(prev * 1e8))
    return d


def test_quarter_borrowings_read_by_name_stay_on_balance_sheet():
    """KT&G: mọi dòng nợ vay mang mã '-표준계정코드 미사용-' → nợ ròng quý phải tính cả nợ vay (+250, không phải −50)."""
    items = [_row("BS", "ifrs-full_Assets", "자산총계", 1000), _row("BS", "ifrs-full_CashAndCashEquivalents", "현금", 50),
             _row("BS", "-표준계정코드 미사용-", "단기차입금", 120), _row("BS", "-표준계정코드 미사용-", "장기차입금", 180),
             _row("IS", "ifrs-full_Revenue", "매출액", 300, add=300, prev=250)]
    rep = {"year": 2026, "rc": "11013", "fs_div": "CFS", **A.parse_report(items)}
    q = A.quarter_series([rep])[0]
    assert q["bs"]["borrowings"] == 300
    assert A.decompose_quarters([q])[0]["Nợ ròng (억)"] == 250


def test_q4_equity_income_from_named_line_not_dumped_into_other():
    """Hyundai Motor ghi lãi liên kết bằng tên dòng (không mã chuẩn): Q4 = cả năm − LŨY KẾ 9 tháng phải có phần này."""
    def rep(rc, pt_rev, pt_eq, add_eq=None, pretax=None):
        items = [_row("BS", "ifrs-full_Assets", "자산총계", 1000),
                 _row("IS", "ifrs-full_Revenue", "매출액", pt_rev, add=pt_rev * 3 if rc == "11014" else None),
                 _row("IS", "dart_OperatingIncomeLoss", "영업이익", pt_rev * 0.1,
                      add=pt_rev * 0.3 if rc == "11014" else None),
                 _row("IS", "ifrs-full_ProfitLossBeforeTax", "법인세비용차감전순이익",
                      pretax if pretax is not None else pt_rev * 0.1 + pt_eq, add=(pt_rev * 0.3 + add_eq) if add_eq else None),
                 _row("IS", "-표준계정코드 미사용-", "공동기업및관계기업투자손익", pt_eq, add=add_eq)]
        return {"year": 2025, "rc": rc, "fs_div": "CFS", **A.parse_report(items)}
    q3 = rep("11014", 100, 20, add_eq=60)                      # 9 tháng: lãi liên kết 60
    fy = rep("11011", 400, 90)                                 # cả năm: 90 → Q4 = 30
    assert q3["ytd"]["equity_income"] == 60
    q4 = A.quarter_series([q3, fy])[-1]
    assert q4["flow"]["equity_income"] == 30
    row = A.decompose_quarters([q4])[0]
    assert abs(row["Khác / LNTT %"] or 0) < 1e-6              # không còn "khoản khác" giả


def test_full_statements_fall_back_to_older_cached_year_when_dart_is_down(monkeypatch, tmp_path):
    """Tháng 2: năm vừa kết thúc chưa nộp + DART lỗi → dùng năm trước đã lưu (trước bản sửa: None → mất chất lượng TS)."""
    import datetime as dt
    import json
    monkeypatch.setattr(A.dart_api, "available", lambda: True)
    monkeypatch.setattr(A.dart_api, "corp_code_of", lambda t: "00000001")
    calls = []

    def down(*a, **k):
        calls.append(a)
        raise A.dart_api.DartError("DART đang từ chối kết nối")
    monkeypatch.setattr(A.dart_api, "fetch_statements", down)
    y = dt.date.today().year
    (tmp_path / f"00000001_{y - 2}.json").write_text(json.dumps(
        {"year": y - 2, "fs_div": "CFS", "cur": {"assets": 1.0, "revenue": 9.0}, "prev": {}, "_v": A.FULL_V}))
    errs = []
    out = A.fetch_full("005380", cache_dir=str(tmp_path), errors=errs)
    assert out["year"] == y - 2 and errs and len(calls) == 1    # chỉ gọi DART một lần rồi đọc cache


def test_quarter_reports_continue_from_cache_after_an_error(monkeypatch, tmp_path):
    import datetime as dt
    import json
    monkeypatch.setattr(A.dart_api, "available", lambda: True)
    monkeypatch.setattr(A.dart_api, "corp_code_of", lambda t: "00000001")
    monkeypatch.setattr(A.dart_api, "fetch_statements",
                        lambda *a, **k: (_ for _ in ()).throw(A.dart_api.DartError("DART lỗi")))
    y = dt.date.today().year - 1
    for rc in ("11013", "11012"):                               # hai kỳ năm sau đã lưu — không được bỏ
        (tmp_path / f"00000001_{y}_{rc}.json").write_text(json.dumps(
            {"year": y, "rc": rc, "fs_div": "CFS", "pt": {"assets": 1.0}, "ytd": {}, "yoy": {}, "sj": {"assets": "BS"},
             "_v": A.FULL_V}))
    errs = []
    out = A.fetch_quarter_reports("005930", years=2, cache_dir=str(tmp_path), errors=errs)
    assert [(r["year"], r["rc"]) for r in out] == [(y, "11013"), (y, "11012")] and errs
