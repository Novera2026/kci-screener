"""Mega trend: tính gộp chủ đề Naver đúng, không phụ thuộc mạng."""
import json

import megatrend as MT


def _theme(no, chg, rise, total, name=None):
    return {"no": no, "name": name or f"T{no}", "chg": chg, "rise": rise, "fall": total - rise, "total": total}


def test_catalog_unique_and_nonempty():
    keys = [m["key"] for m in MT.MEGATRENDS]
    assert len(keys) == len(set(keys))
    for m in MT.MEGATRENDS:
        assert m["themes"] and len(m["themes"]) == len(set(m["themes"]))


def test_trend_table_average_breadth_and_sort():
    grid = next(m for m in MT.MEGATRENDS if m["key"] == "grid")
    a, b = grid["themes"][:2]
    themes = [_theme(a, 4.0, 8, 10, "전력설비"), _theme(b, 2.0, 2, 10)]
    rows = MT.trend_table(themes)
    g = next(r for r in rows if r["key"] == "grid")
    assert g["chg"] == 3.0                       # trung bình đều các chủ đề con
    assert g["breadth"] == 50.0                  # (8+2)/(10+10)
    assert g["n_found"] == 2 and g["best"] == "전력설비" and g["best_chg"] == 4.0
    assert rows[0]["key"] == "grid"              # có số → xếp trên các xu hướng thiếu dữ liệu
    assert all(r["chg"] is None for r in rows[1:])


def test_trend_leaders_dedup_and_order():
    ship = next(m for m in MT.MEGATRENDS if m["key"] == "ship")
    a, b = ship["themes"][:2]
    s = lambda tk, chg: {"ticker": tk, "name": tk, "chg": chg}
    det = {a: {"stocks": [s("000001", 1.0), s("000002", 5.0)]},
           b: {"stocks": [s("000002", 5.0), s("000003", None)]}}
    out = MT.trend_leaders("ship", det)
    assert [x["ticker"] for x in out] == ["000002", "000001", "000003"]


def test_load_bot_state(tmp_path):
    p = tmp_path / "bot_state.json"
    assert MT.load_bot_state(str(p)) is None
    p.write_text("{hỏng", encoding="utf-8")
    assert MT.load_bot_state(str(p)) is None
    p.write_text(json.dumps({"at": "2026-11-10T08:00", "picks": {"005930": {"name": "삼성전자"}}}), encoding="utf-8")
    assert MT.load_bot_state(str(p))["picks"]["005930"]["name"] == "삼성전자"


def test_trend_leaders_all_members_with_theme_names():
    ship = next(m for m in MT.MEGATRENDS if m["key"] == "ship")
    a, b = ship["themes"][:2]
    s = lambda tk, chg: {"ticker": tk, "name": tk, "chg": chg}
    det = {a: {"stocks": [s(f"{i:06d}", float(i)) for i in range(15)]}, b: {"stocks": [s("000003", 3.0)]}}
    out = MT.trend_leaders("ship", det, top=None, theme_names={a: "조선", b: "조선기자재"})
    assert len(out) == 15                                  # top=None → đủ, không cắt ở 10
    three = next(x for x in out if x["ticker"] == "000003")
    assert three["themes"] == ["조선", "조선기자재"]
