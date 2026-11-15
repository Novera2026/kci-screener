"""Bảo vệ app công khai (guard.py) + hạn mức DART, dọn hồ sơ, tra mã — rà soát 15/11/2026, đợt 2. Không cần mạng."""
import datetime as dt
import os

import pytest

import guard as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_is_cloud_override(monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    assert G.is_cloud()
    monkeypatch.setenv("KCI_PUBLIC", "0")
    assert not G.is_cloud()
    monkeypatch.delenv("KCI_PUBLIC")
    assert G.is_cloud() == os.path.abspath(G.__file__).startswith("/mount/src/")


def test_password_check():
    assert G.check_password("dung-roi", "dung-roi")
    assert not G.check_password("sai", "dung-roi")
    assert not G.check_password("", "dung-roi")
    assert not G.check_password("bat-ky", None)                   # chưa đặt mật khẩu → không mở được


def test_clip_queries_dedupes_caps_and_drops_junk():
    text = "005930\n005930, 000660\n" + "x" * 500 + "\n" + "\n".join(f"{i:06d}" for i in range(300))
    items, cut = G.clip_queries(text, limit=200)
    assert items[:2] == ["005930", "000660"] and len(items) == 200 and cut > 0
    assert all(len(x) <= G.MAX_QUERY_CHARS for x in items)
    assert G.clip_queries("")[0] == []


def test_clean_codes_blocks_path_tricks():
    assert G.clean_codes(["TSM", "MU.O", "7203.T", "0700.HK", "../../x", "a/b", "?q=1", "TSM"]) == \
        ["TSM", "MU.O", "7203.T", "0700.HK"]
    assert len(G.clean_codes([f"C{i}" for i in range(30)])) == 10


def test_heavy_slot_limits_concurrency():
    with G.heavy_slot() as a, G.heavy_slot() as b:
        assert a and b
        with G.heavy_slot() as c:
            assert not c                                         # máy chủ đang bận đủ HEAVY_SLOTS tác vụ
    with G.heavy_slot() as d:                                    # đã nhả hết
        assert d
    with pytest.raises(RuntimeError):                             # lỗi giữa chừng vẫn nhả suất
        with G.heavy_slot():
            raise RuntimeError("x")
    with G.heavy_slot() as e, G.heavy_slot() as f:
        assert e and f


def test_redact_hides_keys_and_tokens(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "abcdef1234567890")
    err = ("HTTPSConnectionPool(host='opendart.fss.or.kr', port=443): Max retries exceeded with url: "
           "/api/fnlttSinglAcntAll.json?crtfc_key=abcdef1234567890&corp_code=00126380")
    out = G.redact(err)
    assert "abcdef1234567890" not in out and "crtfc_key=***" in out and "corp_code=00126380" in out
    assert "123:AA" not in G.redact("https://api.telegram.org/bot123:AAbbCC_dd-ee/sendMessage")
    assert "s3cr3t" not in G.redact('{"client_secret": "s3cr3t", "x": 1}')
    assert G.redact("không có gì bí mật") == "không có gì bí mật"


def test_markdown_from_outside_is_inert():
    t = G.md_escape("Lãi [kỷ lục](javascript:alert(1)) **x** # tiêu đề")
    assert "](" not in t.replace("\\]\\(", "") and "\\[" in t and "\\*\\*" in t
    assert G.safe_url("https://n.news.naver.com/article/1") == "https://n.news.naver.com/article/1"
    assert G.safe_url("javascript:alert(1)") is None
    assert G.safe_url("https://x.com/a) [b](c") is None


# ---------------------------------------------------------------- hạn mức DART theo ngày
def test_dart_daily_budget_blocks_calls_and_persists(monkeypatch, tmp_path):
    import dart_api as D
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "3")
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(D, "BUDGET_PATH", str(tmp_path / "b.json"))
    monkeypatch.setattr(D, "_BUDGET", {"day": None, "used": 0})
    monkeypatch.setattr(D, "MIN_GAP", 0)
    monkeypatch.setattr(D, "STATS", {"ok": 0, "nodata": 0, "errors": 0, "streak": 0, "open_until": 0.0,
                                     "last_error": ""})
    calls = []

    class R:
        def json(self):
            return {"status": "000", "list": []}
    monkeypatch.setattr(D.requests, "get", lambda *a, **k: calls.append(1) or R())
    for _ in range(3):
        assert D.fetch_statements("1", 2025) == []
    with pytest.raises(D.DartError) as e:
        D.fetch_statements("1", 2025)
    assert "hạn mức" in str(e.value) and len(calls) == 3              # không gọi DART khi đã hết hạn mức
    # khởi động lại tiến trình (bộ đếm trong bộ nhớ trống) vẫn nhớ số đã dùng hôm nay
    monkeypatch.setattr(D, "_BUDGET", {"day": None, "used": 0})
    assert not D.budget_take()
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "0")                  # 0 = không giới hạn
    assert D.budget_take()


def test_dart_budget_default_only_on_public_app(monkeypatch):
    import dart_api as D
    monkeypatch.delenv("KCI_DART_DAILY_BUDGET", raising=False)
    monkeypatch.setenv("KCI_PUBLIC", "1")
    assert D.daily_budget() == 10000
    monkeypatch.setenv("KCI_PUBLIC", "0")
    assert D.daily_budget() == 0


# ---------------------------------------------------------------- dọn hồ sơ
def test_dossier_prune_old_and_excess(tmp_path):
    import dossier as DS
    today = dt.date(2026, 11, 15)
    for d in range(0, 40):
        (tmp_path / f"005930_{today - dt.timedelta(days=d):%Y%m%d}.json").write_text("{}")
    (tmp_path / "ghi_chu.txt").write_text("không đụng tới")
    n = DS.prune(str(tmp_path), keep_days=30, max_files=10, today=today)
    left = sorted(p.name for p in tmp_path.glob("*.json"))
    assert n == 30 and len(left) == 10 and left[-1] == "005930_20261115.json"
    assert (tmp_path / "ghi_chu.txt").exists()


# ---------------------------------------------------------------- tra mã
def test_resolve_long_or_repeated_preferred_suffix_does_not_recurse(monkeypatch):
    from screener import TickerRegistry
    reg = TickerRegistry()
    monkeypatch.setattr(reg, "_load_krx_names", lambda: None)
    monkeypatch.setattr(reg, "_krx_name_map", {}, raising=False)
    assert reg.resolve("삼성전자" + "우" * 3000) is None              # trước đây: RecursionError
    reg.resolve("삼성전자" + "우" * 40)                                 # không đệ quy theo số chữ "우"


# ---------------------------------------------------------------- bot: lần chạy trễ qua nửa đêm
def test_bot_session_freshness_handles_late_runs():
    import bot_hunt as BH
    kst = BH.KST
    assert BH.session_is_fresh("2026-11-13", dt.datetime(2026, 11, 13, 16, 30, tzinfo=kst))
    assert BH.session_is_fresh("2026-11-13", dt.datetime(2026, 11, 14, 0, 40, tzinfo=kst))      # trễ qua nửa đêm
    assert not BH.session_is_fresh("2026-11-13", dt.datetime(2026, 11, 14, 16, 30, tzinfo=kst))  # thứ 7: sàn nghỉ
    assert not BH.session_is_fresh("2026-11-12", dt.datetime(2026, 11, 14, 1, 0, tzinfo=kst))


# ---------------------------------------------------------------- trang chạy thật ở chế độ bản công khai
def test_public_app_locks_owner_features(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setenv(G.OWNER_SECRET, "mat-khau-thu")
    at = AppTest.from_file(os.path.join(ROOT, "views", "tong_hop.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    src = next(s for s in at.selectbox if s.label == "Nguồn giá ưu tiên")
    assert list(src.options) == ["naver"]                             # Toss / pykrx dùng khóa riêng → ẩn
    assert any("chỉ mở cho chủ app" in i.value for i in at.info)      # backtest bị khóa
    # nhập sai rồi đúng mật khẩu ở ô 🔒 Chủ app
    pw = next(t for t in at.text_input if t.label == "Mật khẩu")
    pw.set_value("sai").run()
    at = next(b for b in at.button if b.label == "Mở khóa").click().run()
    assert any("Sai mật khẩu" in e.value for e in at.error)
    pw = next(t for t in at.text_input if t.label == "Mật khẩu")
    pw.set_value("mat-khau-thu").run()
    at = next(b for b in at.button if b.label == "Mở khóa").click().run()
    assert not at.exception, at.exception
    src = next(s for s in at.selectbox if s.label == "Nguồn giá ưu tiên")
    assert list(src.options) == ["naver", "toss", "pykrx"]
    assert not any("chỉ mở cho chủ app" in i.value for i in at.info)


def test_public_app_without_password_stays_locked(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.delenv(G.OWNER_SECRET, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "views", "tong_hop.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    assert not any(t.label == "Mật khẩu" for t in at.text_input)      # chưa đặt mật khẩu → không có ô nhập
    assert any("chỉ mở cho chủ app" in i.value for i in at.info)


def test_local_app_is_not_locked(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("KCI_PUBLIC", "0")
    at = AppTest.from_file(os.path.join(ROOT, "views", "tong_hop.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    src = next(s for s in at.selectbox if s.label == "Nguồn giá ưu tiên")
    assert list(src.options) == ["naver", "toss", "pykrx"]
    assert not any("chỉ mở cho chủ app" in i.value for i in at.info)
