"""Bóc tách theo quý: tách số quý từ báo cáo DART lũy kế — không cần mạng."""
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
