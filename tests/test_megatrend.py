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


def test_session_summary_text():
    import datetime as dt
    at = dt.datetime(2026, 11, 11, 15, 40)
    idx = [{"code": "KOSPI", "price": 5188.0, "chg_pt": 169.15, "chg": 3.37},
           {"code": "KOSDAQ", "price": 657.81, "chg_pt": -2.5, "chg": -0.38}]
    trends = [{"key": "nuclear", "icon": "☢️", "name": "Hạt nhân", "chg": 4.6, "rise": 122, "total": 144},
              {"key": "ship", "icon": "🚢", "name": "Đóng tàu", "chg": 3.4, "rise": 60, "total": 71},
              {"key": "bio", "icon": "🧬", "name": "Bio", "chg": -0.5, "rise": 10, "total": 50},
              {"key": "k", "icon": "💄", "name": "K-beauty", "chg": -1.2, "rise": 5, "total": 40}]
    movers = {"nuclear": [{"name": "두산에너빌리티", "chg": 12.69}, {"name": "X", "chg": 9.0}, {"name": "Y", "chg": 1.0}]}
    t = MT.session_summary(idx, trends, movers, at, "CLOSE", n_trends=2)
    assert t.startswith("📊 TỔNG KẾT PHIÊN 11/11/2026")
    assert "KOSPI: 5.188,00 điểm (+169,15 điểm, +3,37%)" in t and "🔻 KOSDAQ" in t
    assert "2/4 xu hướng lớn tăng" in t and "Thị trường phân hóa" in t
    assert "↳ 두산에너빌리티 +12,7% · X +9,0%" in t and "Y +1,0%" not in t
    assert "Yếu nhất: 💄 K-beauty (-1,20%)" in t and "không phải khuyến nghị" in t
    assert "(cập nhật 15:40)" in MT.session_summary(idx, trends, movers, at, "OPEN")


def test_session_summary_mentions_each_stock_once():
    import datetime as dt
    tr = [{"key": "a", "icon": "1", "name": "A", "chg": 5.0, "rise": 1, "total": 2},
          {"key": "b", "icon": "2", "name": "B", "chg": 4.0, "rise": 1, "total": 2}]
    mv = {"a": [{"ticker": "089030", "name": "테크윙", "chg": 20.7}, {"ticker": "1", "name": "P", "chg": 9.0}],
          "b": [{"ticker": "089030", "name": "테크윙", "chg": 20.7}, {"ticker": "2", "name": "Q", "chg": 8.0},
                {"ticker": "3", "name": "R", "chg": 7.0}]}
    t = MT.session_summary([], tr, mv, dt.datetime(2026, 11, 11, 16, 0), "CLOSE")
    assert t.count("테크윙") == 1 and "Q +8,0% · R +7,0%" in t
