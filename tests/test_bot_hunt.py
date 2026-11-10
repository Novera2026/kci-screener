"""Bot săn cổ phiếu (bot_hunt.py) — phần logic thuần, không mạng."""
import datetime as dt

import bot_hunt as BH


def _res(tk, up, verdict="🟢 Rẻ…", fair=True):
    return {"Mã": tk, "Tên": f"n{tk}", "Ngành": "식품", "Giá": 1000.0, "Giá trị hợp lý": 1000 * (1 + up / 100) if fair else None,
            "Chênh %": up, "Chất lượng TS": "A", "Kết luận": verdict}


def test_aggregate_ranks_by_presets_then_verdict_then_upside():
    results = {"a": _res("a", 30), "b": _res("b", 60, "🟡 Rẻ…"), "c": _res("c", 20), "d": _res("d", -5),
               "e": _res("e", 40, fair=False)}
    hits = {"kci": ["a", "b", "c", "d", "e"], "valueup": ["c"], "magic": ["c"]}
    picks = BH.aggregate(results, hits)
    assert [p["ticker"] for p in picks] == ["c", "a", "b"]            # c qua 3 bộ; d chênh âm; e không có giá trị
    assert picks[0]["presets"] == ["kci", "valueup", "magic"]


def test_diff_new_dropped_changed():
    prev = {"picks": {"a": {"name": "na", "verdict": "🟡 x"}, "z": {"name": "nz", "verdict": "🟢 x"}}}
    picks = BH.aggregate({"a": _res("a", 30), "b": _res("b", 25)}, {"kci": ["a", "b"]})
    d = BH.diff(prev, picks)
    assert [p["ticker"] for p in d["new"]] == ["b"]
    assert [x["ticker"] for x in d["dropped"]] == ["z"]
    assert [p["ticker"] for p, _ in d["changed"]] == ["a"]           # 🟡 → 🟢
    assert BH.diff({}, picks)["first_run"]


def test_format_message_html_escaped_and_split():
    picks = [{"ticker": f"{i:06d}", "name": "A&B <x>", "industry": "식품", "price": 1000.0, "fair": 1500.0,
              "upside": 50.0, "quality": "A", "verdict": "🟢", "presets": ["kci"]} for i in range(300)]
    meta = {"at": dt.datetime(2026, 11, 11, 16, 30), "universe": 200, "presets": ["kci"], "n_deep": 20, "rf": 0.0387}
    msgs = BH.format_message(picks, BH.diff({}, picks), meta, top=300)
    assert len(msgs) > 1 and all(len(m) <= 4096 for m in msgs)
    assert "A&amp;B &lt;x&gt;" in msgs[0] and "<b>KCI săn cổ phiếu</b>" in msgs[0]
    assert "Rf 3.87%" in msgs[0]


def test_state_roundtrip_feeds_diff():
    picks = BH.aggregate({"a": _res("a", 30)}, {"kci": ["a"]})
    st = BH.state_of(picks, dt.datetime(2026, 11, 11, 16, 30))
    assert BH.diff(st, picks)["new"] == [] and not BH.diff(st, picks)["first_run"]
