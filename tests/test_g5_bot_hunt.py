"""Bot săn (rà soát 18/11/2026: I31 cổng sức khỏe, I63 trần định giá sâu) — kín, không mạng."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest
from streamlit.testing.v1 import AppTest

import bot_hunt as BH
import guard as GD
import screen as SC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RF = {"value": 0.0325, "source": "test", "asof": "2026-11-18", "live": True, "note": ""}


def _row(tk, price=10000.0):
    """10 mã đầu rẻ (qua tầng 1 bộ KCI), còn lại giá ngang ngành. price=None → dòng lỗi nguồn."""
    if price is None:
        return {"ticker": tk, "name": f"n{tk}", "price": None, "per": None, "pbr": None, "eps": None, "bps": None,
                "div_yield": None, "market_cap": None, "industry_kr": "식품", "fetch_ok": False}
    pe, pb = (3.0, 0.3) if int(tk) <= 10 else (15.0, 1.5)
    return {"ticker": tk, "name": f"n{tk}", "price": price, "per": pe, "pbr": pb, "eps": price / pe,
            "bps": price / pb, "div_yield": 4.0, "market_cap": 5e12, "industry_kr": "식품", "fetch_ok": True}


def _res(tk, up=30.0, verdict="🟢 Rẻ", warn=""):
    return {"Mã": tk, "Tên": f"n{tk}", "Ngành": "식품", "Giá": 1000.0, "Giá trị hợp lý": 1000 * (1 + up / 100),
            "Chênh %": up, "Chất lượng TS": "A", "Kết luận": verdict, "Độ tin cậy": "Medium", "Cảnh báo dữ liệu": warn,
            "F-score": 7, "Thanh toán hiện hành": 2.0, "Nợ/VCSH": 0.3, "Có năm lỗ": False}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Mọi nguồn của hunt() là bản giả; bot_state / export nằm trong tmp_path."""
    import screener
    feed = {"top": [f"{i:06d}" for i in range(1, 201)], "rows": None, "deep": None, "deep_calls": []}

    def fetch_many(tks, *a, **k):
        rows = feed["rows"] if feed["rows"] is not None else [_row(t) for t in tks]
        return [dict(r) for r in rows], []

    def deep_stage(tks, *a, **k):
        feed["deep_calls"].append(list(tks))
        return [dict(feed["deep"].get(t) or _res(t), **{"Mã": t}) for t in tks] if feed["deep"] is not None \
            else [_res(t) for t in tks]
    monkeypatch.setattr(SC, "top_by_cap", lambda market, n, reg: list(feed["top"])[:n])
    monkeypatch.setattr(SC, "deep_stage", deep_stage)
    monkeypatch.setattr(screener, "fetch_many", fetch_many)
    monkeypatch.setattr(screener, "rf_for_valuation", lambda: dict(RF))
    monkeypatch.setattr(BH, "market_open_today", lambda now=None: True)
    state = tmp_path / "bot_state.json"
    prev = {"at": "2026-11-15T16:30", "picks": {f"{i:06d}": {"name": f"old{i}", "upside": 20.0, "verdict": "🟢 x",
                                                             "presets": ["kci"]} for i in range(1, 25)}}
    state.write_text(json.dumps(prev, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BH, "STATE_PATH", str(state))
    monkeypatch.setenv("BOT_PRESETS", "kci")
    for k in ("BOT_UNIVERSE", "BOT_MAX_DEEP", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(BH, "_env", lambda: None)                         # không nạp .env thật
    return feed, state, tmp_path


# ---------------------------------------------------------------------------- I31
def test_network_down_exits_2_without_touching_state_or_export(offline):
    feed, state, tmp = offline
    feed["top"] = []                                                      # Naver lỗi → top_by_cap trả []
    before = state.read_bytes()
    exp = tmp / "bot_export.json"
    exp.write_text('{"cũ": 1}', encoding="utf-8")                          # file của lần trước
    assert BH.main(["--export", str(exp)]) == 2
    assert state.read_bytes() == before                                   # bot_state.json còn nguyên
    assert not exp.exists()                                               # không để số cũ bị đọc nhầm là lần này


def test_rows_without_market_data_fail_the_health_gate(offline):
    feed, state, tmp = offline
    feed["rows"] = [_row(f"{i:06d}", price=None) for i in range(1, 201)]  # Naver trả dòng không giá cho mọi mã
    before = state.read_bytes()
    assert BH.main(["--dry-run"]) == 2
    assert state.read_bytes() == before


def test_mostly_failed_valuations_fail_the_health_gate(offline):
    feed, _, tmp = offline
    feed["deep"] = {f"{i:06d}": {"Kết luận": "⚪ Chưa định giá được (ConnectionError)"} for i in range(1, 201)}
    ok, why = BH._health(BH.hunt(["kci"], 200, 15, progress=lambda *a: None), 200)
    assert not ok and any("định giá sâu bị lỗi" in w for w in why)
    assert BH.main(["--export", str(tmp / "x.json")]) == 2


def test_healthy_run_writes_export_and_state_atomically(offline, monkeypatch):
    feed, state, tmp = offline
    exp = tmp / "out.json"
    assert BH.main(["--export", str(exp)]) == 0
    j = json.loads(exp.read_text(encoding="utf-8"))
    assert j["run_status"] == "ok" and j["run_problems"] == [] and j["rf_live"] is True and "rf_note" in j
    assert j["picks"] and all("Cảnh báo dữ liệu" in p for p in j["picks"])
    st_ = json.loads(state.read_text(encoding="utf-8"))
    assert set(st_["picks"]) >= {p["ticker"] for p in j["picks"]}
    assert not [f for f in os.listdir(tmp) if f.startswith(".tmp_")]    # không sót file tạm


def test_failed_rows_are_not_reported_as_dropped(offline):
    """Mã cũ lần này lỗi dữ liệu → 'chưa kiểm được', không phải 'Rớt'; bot_state giữ mục cũ."""
    feed, state, tmp = offline
    rows = [_row(f"{i:06d}") for i in range(1, 201)]
    rows[0] = _row("000001", price=None)                                    # Naver lỗi riêng mã này
    feed["rows"] = rows
    feed["deep"] = {"000002": {"Kết luận": "⚪ Chưa định giá được (timeout)"}}
    out = BH.hunt(["kci"], 200, 15, progress=lambda *a: None)
    assert {"000001", "000002"} <= set(out["errored"])
    prev = json.loads(state.read_text(encoding="utf-8"))
    d = BH.diff(prev, out["picks"], out["errored"])
    dropped = {x["ticker"] for x in d["dropped"]}
    assert "000001" not in dropped and "000002" not in dropped
    assert {x["ticker"] for x in d["errored"]} >= {"000001", "000002"}
    keep = BH.state_of(out["picks"], out["meta"]["at"], d)
    assert keep["picks"]["000001"]["name"] == "old1"
    txt = "\n".join(BH.format_message(out["picks"], d, out["meta"]))
    assert "Chưa kiểm được" in txt


def test_diff_errored_unit():
    prev = {"picks": {"a": {"name": "na", "verdict": "🟢"}, "b": {"name": "nb", "verdict": "🟢"}}}
    picks = BH.aggregate({"a": _res("a")}, {"kci": ["a"]})
    d = BH.diff(prev, picks, errored=["b"])
    assert d["dropped"] == [] and [x["ticker"] for x in d["errored"]] == ["b"]
    assert BH.diff(prev, picks)["dropped"][0]["ticker"] == "b"              # không truyền errored → như cũ
    assert "b" in BH.state_of(picks, dt.datetime(2026, 11, 18, 16, 30), d)["picks"]


def test_errored_counts_only_real_dart_errors():
    """'Đối chiếu DART ↔ Naver' là lệch nguồn (định giá vẫn chạy bằng số Naver), không phải lỗi lấy dữ liệu."""
    ok = {"Kết luận": "🟢 Hấp dẫn", "Cảnh báo dữ liệu": ""}
    assert not BH._errored(ok)
    assert not BH._errored(dict(ok, **{"Cảnh báo dữ liệu": "Đối chiếu DART ↔ Naver"}))
    assert not BH._errored(dict(ok, **{"Cảnh báo dữ liệu": "Số năm dữ liệu; Đối chiếu DART ↔ Naver"}))
    assert BH._errored(dict(ok, **{"Cảnh báo dữ liệu": "Số năm dữ liệu; Số năm có số gốc DART"}))
    assert BH._errored(dict(ok, **{"Cảnh báo dữ liệu": "BCTC đầy đủ DART: lỗi đọc BCTC đầy đủ"}))
    assert BH._errored({"Kết luận": "⚪ Chưa định giá được", "Cảnh báo dữ liệu": ""})
    assert not BH._errored({"Kết luận": "⚪ Giá gần giá trị ước tính", "Cảnh báo dữ liệu": ""})


def test_market_calendar_unknown_warns(offline, capsys, monkeypatch):
    monkeypatch.setattr(BH, "market_open_today", lambda now=None: None)
    BH.main(["--dry-run"])
    assert "Không kiểm được lịch phiên" in capsys.readouterr().err


# ---------------------------------------------------------------------------- I63
def _seven_presets(monkeypatch, feed):
    keys = list(SC.PRESETS)
    assert len(keys) == 7
    lists = {k: [f"{j}{i:05d}" for i in range(15)] for j, k in enumerate(keys)}          # 7 × 15 mã không trùng
    monkeypatch.setattr(SC, "apply_preset", lambda rows, k, ov=None: [{"Mã": t, "Qua lọc": True} for t in lists[k]])
    feed["top"] = [t for v in lists.values() for t in v]
    return keys, lists


def test_hunt_max_total_caps_deep_valuation_round_robin(offline, monkeypatch):
    feed, _, _ = offline
    keys, lists = _seven_presets(monkeypatch, feed)
    out = BH.hunt(keys, universe=105, max_deep=15, progress=lambda *a: None, max_total=30)
    got = feed["deep_calls"][-1]
    assert len(got) == 30 and out["meta"]["n_deep"] == 30 and out["meta"]["n_cut"] == 75
    for k in keys:
        assert sum(1 for t in got if t in lists[k]) >= 4, k                 # bộ nào cũng có mã
    BH.hunt(keys, universe=105, max_deep=15, progress=lambda *a: None)    # None → như cũ
    assert len(feed["deep_calls"][-1]) == 105


@pytest.mark.parametrize("owner", [False, True])
def test_home_bot_passes_public_cap(owner, monkeypatch, fake_market):
    seen = {}

    def fake_hunt(presets, **k):
        seen.update(k)
        return {"picks": [], "results": {}, "hits": {}, "rows": [], "errored": [],
                "meta": {"at": dt.datetime(2026, 11, 18, 16, 0), "universe": 0, "scope": k.get("scope", ""),
                         "presets": presets, "n_deep": 0, "rf": 0.03, "rf_live": True, "rf_note": ""}}
    monkeypatch.setattr(BH, "hunt", fake_hunt)
    monkeypatch.setenv("KCI_PUBLIC", "1")
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    if owner:
        at.session_state["_owner_ok"] = True
    at.run()
    at = at.multiselect(key="hb_presets").set_value(list(SC.PRESETS)).run()
    at = at.slider(key="hb_deep").set_value(15).run()
    at = at.button(key="hb_run").click().run()
    assert not at.exception, at.exception
    assert seen["max_total"] == (None if owner else GD.MAX_DEEP_PUBLIC)
    assert any("7–14 lượt gọi DART" in c.value for c in at.caption)
