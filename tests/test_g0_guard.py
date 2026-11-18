"""Bảo vệ app công khai — rà soát 18/11/2026 (I66 khóa dò mật khẩu theo tiến trình, C1 che khóa, C6 require_owner,
I70 reload_guard nhanh). Không cần mạng; mật khẩu trong test là giá trị giả."""
import os
import sys
import time

import pytest

import guard as G

FAKE_PW = "mat-khau-gia-cho-test-1234"
SECRET_ENVS = ("DART_API_KEY", "OPENDART_API_KEY", "TOSS_CLIENT_SECRET", "TOSS_CLIENT_ID", "TELEGRAM_BOT_TOKEN",
               "KRX_PW", G.OWNER_SECRET)


# ---------------------------------------------------------------- I66: giới hạn dò mật khẩu qua nhiều phiên
@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setenv(G.OWNER_SECRET, FAKE_PW)
    monkeypatch.setattr(G, "WRONG_DELAY", 0, raising=False)
    monkeypatch.setattr(G, "GLOBAL_GAP", 0, raising=False)
    monkeypatch.setattr(G, "_client_ip", lambda: "203.0.113.7", raising=False)
    getattr(G, "_FAILS", {}).clear()
    yield
    getattr(G, "_FAILS", {}).clear()


def _panel_app():
    import streamlit as st

    import guard
    guard.owner_panel()
    st.write("MO" if guard.owner_ok() else "KHOA")


def _session():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_function(_panel_app, default_timeout=30).run()


def _try(at, pw):
    box = next((t for t in at.text_input if t.label == "Mật khẩu"), None)
    if box is None:
        return at, False
    box.set_value(pw).run()
    return next(b for b in at.button if b.label == "Mở khóa").click().run(), True


def _opened(at):
    return any(m.value == "MO" for m in at.markdown)


def test_wrong_passwords_across_sessions_lock_unlock(public):
    for s in range(3):                                       # 3 phiên × 5 lần sai (mỗi phiên bị chặn ở 5)
        at = _session()
        for i in range(G.MAX_TRIES):
            at, ok = _try(at, f"sai-{s}-{i}")
            if not ok:
                break
    at = _session()                                          # phiên mới, nhập ĐÚNG mật khẩu
    assert not any(t.label == "Mật khẩu" for t in at.text_input)
    assert any("Tạm khóa" in e.value for e in at.error)
    at, _ = _try(at, FAKE_PW)
    assert not _opened(at)
    # quá 15 phút: lần sai cũ hết hạn → mở được
    for q in G._FAILS.values():
        for i in range(len(q)):
            q[i] -= G.LOCK_WINDOW + 1
    at, ok = _try(_session(), FAKE_PW)
    assert ok and _opened(at)


def test_lock_counts_per_ip_and_globally(public):
    t0 = 1_000_000.0
    for _ in range(G.LOCK_PER_IP - 1):
        G._note_fail("1.1.1.1", t0)
    assert not G._locked("1.1.1.1", t0)
    G._note_fail("1.1.1.1", t0)
    assert G._locked("1.1.1.1", t0) and not G._locked("2.2.2.2", t0)     # IP khác (chủ app) vẫn mở được
    for i in range(G.LOCK_ALL):
        G._note_fail(f"10.0.0.{i}", t0)
    assert not G._locked("2.2.2.2", t0)                                   # dò rải nhiều IP → KHÔNG khóa chủ app
    assert G._global_slow(t0)                                             # … chỉ giảm tốc cả tiến trình
    assert not G._global_slow(t0 + G.LOCK_WINDOW + 1)                     # hết cửa sổ → hết giảm tốc
    assert G._FAILS == {}                                                 # không giữ dữ liệu cũ


def test_global_fails_from_other_ips_do_not_lock_out_owner(public, monkeypatch):
    """50 lần sai rải từ 10 IP lạ: chủ app (IP khác) vẫn thấy ô mật khẩu và mở được bằng mật khẩu đúng (rà soát cuối
    18/11/2026: khóa chung LOCK_ALL từng cho khách khóa luôn chủ app)."""
    for i in range(G.LOCK_ALL):
        G._note_fail("198.18.0.%d" % (i % 10))
    assert G._global_slow()
    monkeypatch.setattr(G, "_client_ip", lambda: "198.51.100.7")
    paced = []
    real = G._pace_check
    monkeypatch.setattr(G, "_pace_check", lambda: (paced.append(1), real())[1])
    at = _session()
    assert any(t.label == "Mật khẩu" for t in at.text_input)
    at, ok = _try(at, FAKE_PW)
    assert ok and _opened(at) and paced                                   # đã qua hàng chờ giảm tốc


def test_pace_check_spaces_checks_when_slow(public, monkeypatch):
    monkeypatch.setattr(G, "GLOBAL_GAP", 2.0)
    clock = {"t": 5_000_000.0}
    monkeypatch.setattr(G.time, "time", lambda: clock["t"])
    monkeypatch.setattr(G.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(G, "_LAST_CHECK", [0.0])
    for i in range(G.LOCK_ALL):
        G._note_fail(f"10.1.0.{i}")
    t0 = clock["t"]
    for _ in range(3):
        G._pace_check()
    assert clock["t"] - t0 >= 2 * 2.0                                     # 3 lần so → cách nhau ≥ 2 giây


def test_short_owner_password_warns_owner_only(public, monkeypatch):
    monkeypatch.setenv(G.OWNER_SECRET, "ngan-1234")
    at = _session()
    assert not any("ngắn hơn" in w.value for w in at.warning)            # khách không được biết mật khẩu ngắn
    assert any(t.label == "Mật khẩu" for t in at.text_input)              # chỉ cảnh báo, không từ chối
    at, ok = _try(at, "ngan-1234")
    assert ok and _opened(at)
    assert any("ngắn hơn" in w.value for w in at.warning)                # chủ app đã mở khóa mới thấy


def test_unknown_ip_counts_only_global(public):
    t0 = 1_000_000.0
    for _ in range(G.LOCK_PER_IP * 2):                                    # IP không rõ: mọi khách chung "?"
        G._note_fail("?", t0)
    assert not G._locked("?", t0) and "?" not in G._FAILS                 # không khóa ở ngưỡng riêng 10
    for _ in range(G.LOCK_ALL - G.LOCK_PER_IP * 2):
        G._note_fail("?", t0)
    assert not G._locked("?", t0) and G._global_slow(t0)                  # ngưỡng chung: giảm tốc, không khóa


def test_unknown_ip_two_guest_sessions_do_not_lock_owner(public, monkeypatch):
    monkeypatch.setattr(G, "_client_ip", lambda: "?")
    for s in range(2):                                                    # 2 phiên khách × 5 lần sai
        at = _session()
        for i in range(G.MAX_TRIES):
            at, _ = _try(at, f"sai-{s}-{i}")
    at, ok = _try(_session(), FAKE_PW)
    assert ok and _opened(at)


# ---------------------------------------------------------------- C1: che khóa khi env trống + err()
@pytest.fixture
def no_env(monkeypatch):
    for n in SECRET_ENVS:
        monkeypatch.delenv(n, raising=False)


_FAKE40 = "abcdef0123456789" * 2 + "abcdef01"          # khóa giả 40 hex, dựng lúc chạy để bộ quét bí mật không báo


@pytest.mark.parametrize("raw, leaked", [
    ("{'crtfc_key': '%s', 'corp_code': '1'}" % _FAKE40, _FAKE40),
    ('params={"crtfc_key":"QWERTY123456"}', "QWERTY123456"),
    ("/api/list.json?crtfc_key=zz99yy88xx77&page_no=1", "zz99yy88xx77"),
    ('{"accessToken": "eyJhbGciOi.payload.sig"}', "eyJhbGciOi.payload.sig"),
    ("access_token=tok_abc123def456&x=1", "tok_abc123def456"),
    ("ACCESS_TOKEN: 'UPPER_tok_9876'", "UPPER_tok_9876"),
    ("headers={'Authorization': 'Bearer eyJ0eXAiOiJKV1Qi.abc-def_ghi'}", "eyJ0eXAiOiJKV1Qi.abc-def_ghi"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ("authorization: bearer eyJhbGciOiJIUzI1NiJ9xx", "eyJhbGciOiJIUzI1NiJ9xx"),
])
def test_redact_patterns_without_env(no_env, raw, leaked):
    out = G.redact(raw)
    assert leaked not in out and "***" in out


def test_redact_keeps_plain_text(no_env):
    for s in ("Basic Materials", "corp_code=00126380", "Bearer market", "không có gì bí mật",
              "Basic Infrastructure fund", "bearer Responsibilities"):
        assert G.redact(s) == s


def test_err_short_redacted_line(no_env):
    e = ConnectionError("Max retries: /api/x.json?crtfc_key=SECRETKEY12345&corp_code=1 " + "x" * 300)
    s = G.err(e)
    assert s.startswith("ConnectionError: ") and "SECRETKEY12345" not in s and len(s) <= 120
    assert len(G.err(e, 40)) <= 40


def test_screener_and_toss_overlay_notes_are_redacted(no_env, monkeypatch):
    import growth as GR
    import screener as S
    import toss_api

    def boom(*a, **k):
        raise RuntimeError("fail /x?crtfc_key=LEAKLEAK1234 accessToken=TOKTOK5678")
    monkeypatch.setattr(S, "_FETCHERS", {"naver": boom})
    note = S.fetch_one("005930", prefer="naver", live=False).note
    assert note.startswith("naver error: RuntimeError") and "LEAKLEAK1234" not in note
    monkeypatch.setattr(toss_api, "TossClient", boom)
    _, _, msg = GR.toss_overlay(["005930"])
    assert msg.startswith("RuntimeError") and "LEAKLEAK1234" not in msg and "TOKTOK5678" not in msg


# ---------------------------------------------------------------- C6: require_owner
def test_require_owner_blocks_guests_on_cloud(monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(G, "owner_ok", lambda: False)
    with pytest.raises(PermissionError, match="Toss"):
        G.require_owner("Nguồn giá Toss")
    monkeypatch.setattr(G, "owner_ok", lambda: True)
    G.require_owner("Nguồn giá Toss")                                     # chủ app đã mở khóa
    monkeypatch.setenv("KCI_PUBLIC", "0")
    monkeypatch.setattr(G, "owner_ok", lambda: False)
    G.require_owner("Nguồn giá Toss")                                     # chạy trên máy của anh


# ---------------------------------------------------------------- I70: reload_guard không realpath mọi module
def test_project_modules_matches_full_scan_and_is_fast():
    import reload_guard as RG
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def full_scan(r):                                                      # cách cũ: realpath mọi module
        rr = os.path.realpath(r) + os.sep
        out = {}
        for name, m in list(sys.modules.items()):
            f = getattr(m, "__file__", None)
            if not f:
                continue
            rf = os.path.realpath(f)
            if rf.startswith(rr) and os.sep + ".venv" + os.sep not in rf and "site-packages" not in rf:
                out[name] = m
        return out
    import pandas  # noqa: F401  — có nhiều module thư viện để quét
    RG._SCAN.clear()
    assert set(RG.project_modules(root)) == set(full_scan(root))
    calls = []
    real = os.path.realpath
    try:
        os.path.realpath = lambda p, *a, **k: calls.append(p) or real(p, *a, **k)
        RG._SCAN.clear()
        RG.project_modules(root)
        n_first = len(calls)
        RG.project_modules(root)
    finally:
        os.path.realpath = real
    lib = sum(1 for m in list(sys.modules.values()) if "site-packages" in (getattr(m, "__file__", None) or ""))
    assert lib > 100 and n_first < lib / 10                                # không realpath module thư viện
    assert len(calls) - n_first <= 1                                       # tập module không đổi → chỉ realpath(root)
    t = time.perf_counter()
    for _ in range(20):
        RG.project_modules(root)
    assert (time.perf_counter() - t) / 20 < 0.005


def test_project_modules_notices_replaced_main_module(tmp_path, monkeypatch):
    """Module cùng tên bị thay (AppTest thay '__main__') → không dùng lại kết quả quét cũ (rà soát cuối 18/11/2026:
    test này từng phụ thuộc thứ tự chạy)."""
    import types
    import reload_guard as RG
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inside = types.ModuleType("__main__")
    inside.__file__ = os.path.join(root, "app.py")
    monkeypatch.setitem(sys.modules, "__main__", inside)
    RG._SCAN.clear()
    assert "__main__" in RG.project_modules(root)
    outside = types.ModuleType("__main__")
    outside.__file__ = str(tmp_path / "x.py")
    monkeypatch.setitem(sys.modules, "__main__", outside)                  # cùng tập tên, khác module
    assert "__main__" not in RG.project_modules(root)


def test_toss_quote_note_is_redacted(no_env, monkeypatch):
    import toss_api
    c = object.__new__(toss_api.TossClient)

    def boom(*a, **k):
        raise RuntimeError("HTTP 401 for url https://x/api?client_secret=FAKESECRET123")
    monkeypatch.setattr(c, "get_price", boom, raising=False)
    monkeypatch.setattr(c, "get_52w_high_low", boom, raising=False)
    note = c.quote("005930")["note"]
    assert "toss price fail: RuntimeError" in note and "FAKESECRET123" not in note and "FAKES" not in note


def test_project_modules_rescans_when_modules_change(tmp_path):
    import importlib
    import reload_guard as RG
    (tmp_path / "kci_g0_tmp_mod.py").write_text("X = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        assert "kci_g0_tmp_mod" not in RG.project_modules(str(tmp_path))
        importlib.import_module("kci_g0_tmp_mod")
        assert "kci_g0_tmp_mod" in RG.project_modules(str(tmp_path))
        del sys.modules["kci_g0_tmp_mod"]
        assert "kci_g0_tmp_mod" not in RG.project_modules(str(tmp_path))
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("kci_g0_tmp_mod", None)
