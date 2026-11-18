"""Giờ Hàn Quốc dùng chung + lịch công bố (rà soát 18/11/2026: I35, cải tiến C2). Không cần mạng.

Máy chủ Streamlit Cloud chạy UTC: 0–9 giờ sáng KST date.today() / datetime.now() là ngày hôm trước. Đồng hồ giả qua
guard._now_utc nên kết quả không phụ thuộc TZ của máy chạy test."""
import ast
import datetime as dt
import glob
import os

import pytest

import guard as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc


@pytest.fixture
def clock(monkeypatch):
    """clock(datetime UTC) — đặt đồng hồ giả cho guard.now_kst / today_kst / now_kst_naive."""
    def set_(t):
        monkeypatch.setattr(G, "_now_utc", lambda: t)
    return set_


# ---------------------------------------------------------------- guard: giờ Hàn
def test_today_kst_rolls_over_at_kst_midnight(clock):
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))           # 08:10 KST thứ Hai 16/11
    assert G.today_kst() == dt.date(2026, 11, 16)
    assert G.now_kst().strftime("%H:%M %d/%m") == "08:10 16/11"
    assert G.now_kst().utcoffset() == dt.timedelta(hours=9)
    assert G.now_kst_naive() == dt.datetime(2026, 11, 16, 8, 10) and G.now_kst_naive().tzinfo is None
    clock(dt.datetime(2026, 12, 31, 15, 0, tzinfo=UTC))            # 00:00 KST 1/1/2027
    assert G.today_kst() == dt.date(2027, 1, 1)


def test_dart_disclosures_queries_kst_today(clock, monkeypatch):
    import dart_api as D
    import hotnews as H
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))           # 08:10 KST — giờ DN nộp 잠정실적
    monkeypatch.setattr(D, "api_key", lambda: "test-key")
    monkeypatch.setattr(D, "budget_take", lambda *a, **k: True)
    sent = []

    class R:
        def json(self):
            return {"status": "013", "message": "no data"}
    monkeypatch.setattr(H.requests, "get", lambda url, params=None, **k: sent.append(params) or R())
    out, err, _ = H.dart_disclosures(1)                           # (danh sách, lỗi, meta) từ 18/11 (I34)
    assert out == [] and err is None
    assert sent[0]["bgn_de"] == "20261116" and sent[0]["end_de"] == "20261116"
    sent.clear()
    H.dart_disclosures(3)
    assert sent[0]["bgn_de"] == "20261114" and sent[0]["end_de"] == "20261116"


def test_dart_disclosures_error_is_redacted(clock, monkeypatch):
    import dart_api as D
    import hotnews as H
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))
    monkeypatch.setattr(D, "api_key", lambda: "k" * 40)
    monkeypatch.setattr(D, "budget_take", lambda *a, **k: True)

    def boom(url, params=None, **k):
        raise ConnectionError(f"Max retries exceeded with url: /api/list.json?crtfc_key={params['crtfc_key']}&x=1")
    monkeypatch.setattr(H.requests, "get", boom)
    monkeypatch.setattr(D, "RETRY_WAITS", ())
    _, err, _ = H.dart_disclosures(1)
    # từ 18/11 (I34) gọi qua dart_api.get_json: lỗi mạng thành câu tiếng Việt (describe_exception), không URL/khóa
    assert "k" * 40 not in err and "ConnectionError" in err and "crtfc_key" not in err


def test_news_age_uses_kst_day(clock):
    import growth as GR
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))           # 16/11 ở Hàn
    items = [{"dt": "20261116073000", "title": "a"}, {"dt": "20261109090000", "title": "b"}]
    m = GR.news_metrics(items)
    assert m["n7"] == 2                                               # tin 9/11 đúng 7 ngày tuổi
    clock(dt.datetime(2026, 11, 16, 15, 1, tzinfo=UTC))            # 00:01 KST 17/11
    assert GR.news_metrics(items)["n7"] == 1                          # tin 9/11 đã 8 ngày


def test_score_news_age_hours_in_kst(clock):
    import hotnews as H
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))           # 08:10 KST
    item = {"title": "삼성전자 수주", "dt": dt.datetime(2026, 11, 16, 7, 10), "url": "u"}
    r = H.score_news(item, {}, [], {})
    assert abs(r["age_h"] - 1.0) < 1e-6


def test_fetch_daily_and_screener_dates_in_kst(clock, monkeypatch):
    import growth as GR
    import screener as S
    clock(dt.datetime(2026, 11, 15, 23, 10, tzinfo=UTC))
    assert S._today_kr() == "20261116"
    assert S._last_n_days(10) == ("20261106", "20261116")
    urls = []

    class R:
        text = "[]"
    monkeypatch.setattr(GR.requests, "get", lambda u, **k: urls.append(u) or R())
    GR.fetch_daily("005930", cal_days=10)
    assert "endTime=20261116" in urls[0] and "startTime=20261106" in urls[0]


def test_dossier_name_and_saved_at_in_kst(clock, tmp_path):
    import dossier as DS
    clock(dt.datetime(2026, 11, 17, 15, 54, 41, tzinfo=UTC))       # 00:54 KST 18/11
    p = DS.save_section("005930", "peers", {"x": 1}, base=str(tmp_path))
    assert os.path.basename(p) == "005930_20261118.json"
    assert DS.load("005930", base=str(tmp_path))["sections"]["peers"]["saved_at"] == "2026-11-18T00:54:41"
    assert DS.path_for("005930", base=str(tmp_path)).endswith("005930_20261118.json")
    (tmp_path / "005930_20261018.json").write_text("{}")             # 31 ngày theo ngày Hàn → xóa
    (tmp_path / "005930_20261019.json").write_text("{}")             # 30 ngày → giữ
    assert DS.prune(str(tmp_path), keep_days=30) == 1
    assert not (tmp_path / "005930_20261018.json").exists() and (tmp_path / "005930_20261019.json").exists()


def test_log_entry_time_in_kst(clock):
    import pipeline as P
    import valuation as V
    clock(dt.datetime(2026, 11, 17, 15, 54, 43, tzinfo=UTC))
    ctx = {"conclusion": {"fair": 1, "low": 1, "high": 1, "upside": 0.0, "confidence": "Low", "status": "x"},
           "coc": {"ke": 0.08, "g_term": 0.02}, "ticker": "005930", "name": "x", "price": 1}
    assert P.log_entry(ctx, [], V.Assumptions(), None)["Thời điểm"] == "2026-11-18T00:54:43"


def test_data_quality_default_today_is_kst(clock):
    import pipeline as P
    series = [{"period": f"{y}12", "revenue": 100.0, "net_income": 10.0} for y in range(2021, 2026)]
    quarters = [{"period": "202605", "revenue": 25.0}]
    row = {"price": 100.0, "market_cap": 1000.0, "eps": 10.0, "bps": 100.0}

    def q_check():
        return next(c for c in P.data_quality(row, series, quarters, 1.0)["checks"]
                    if c["Kiểm tra"] == "Quý thực gần nhất")
    clock(dt.datetime(2026, 11, 30, 14, 0, tzinfo=UTC))            # 23:00 KST 30/11 → quý 05 cách 6 tháng
    assert q_check()["Kết quả"] == P.OK
    clock(dt.datetime(2026, 11, 30, 16, 0, tzinfo=UTC))            # 01:00 KST 1/12 → 7 tháng
    assert q_check()["Kết quả"] == P.WARN


# ---------------------------------------------------------------- lịch công bố (reporting.py)
def test_reporting_period_end_and_due_date():
    import reporting as R
    assert R.period_end(2026, "11013") == dt.date(2026, 3, 31)
    assert R.period_end(2026, "11012") == dt.date(2026, 6, 30)
    assert R.period_end(2026, "11014") == dt.date(2026, 9, 30)
    assert R.period_end(2026, "11011") == dt.date(2026, 12, 31)
    assert R.due_date(2026, "11013") == dt.date(2026, 5, 15)
    assert R.due_date(2026, "11012") == dt.date(2026, 8, 14)
    assert R.due_date(2026, "11014") == dt.date(2026, 11, 14)
    assert R.due_date(2026, "11011") == dt.date(2027, 3, 31)


@pytest.mark.parametrize("today, annual_2026, q1_2027, expected", [
    (dt.date(2026, 12, 31), "not_ended", "not_ended", 2025),
    (dt.date(2027, 1, 1), "pending", "not_ended", 2025),
    (dt.date(2027, 2, 15), "pending", "not_ended", 2025),
    (dt.date(2027, 3, 31), "pending", "not_ended", 2025),
    (dt.date(2027, 4, 1), "due", "pending", 2026),
    (dt.date(2027, 5, 15), "due", "pending", 2026),
    (dt.date(2027, 5, 16), "due", "due", 2026),
    (dt.date(2027, 8, 14), "due", "due", 2026),
    (dt.date(2027, 11, 14), "due", "due", 2026),
])
def test_reporting_state_and_expected_fy(today, annual_2026, q1_2027, expected):
    import reporting as R
    assert R.state(2026, "11011", today) == annual_2026
    assert R.state(2027, "11013", today) == q1_2027
    assert R.expected_fy(today) == expected == R.expected_fy(today, 12)


def test_reporting_half_and_q3_states():
    import reporting as R
    assert R.state(2026, "11012", dt.date(2026, 8, 14)) == "pending"
    assert R.state(2026, "11012", dt.date(2026, 8, 15)) == "due"
    assert R.state(2026, "11014", dt.date(2026, 11, 14)) == "pending"
    assert R.state(2026, "11014", dt.date(2026, 11, 15)) == "due"


def test_reporting_expected_fy_non_december():
    import reporting as R
    # FY tháng 3 (kỳ kết thúc 31/3/2026) → hạn 30/6/2026
    assert R.expected_fy(dt.date(2026, 6, 30), 3) == 2025
    assert R.expected_fy(dt.date(2026, 7, 1), 3) == 2026
    # FY tháng 10 → hạn 31/1 năm sau
    assert R.expected_fy(dt.date(2027, 1, 31), 10) == 2025
    assert R.expected_fy(dt.date(2027, 2, 1), 10) == 2026
    assert R.expected_fy(dt.date(2027, 4, 1), None) == 2026        # thiếu tháng → coi là tháng 12


# ---------------------------------------------------------------- quét AST: không còn giờ máy chủ
# Chỗ còn dùng giờ máy chủ, có lý do. (file, hàm chứa lời gọi) → lý do. Nhóm sở hữu sửa thì xóa dòng ở đây.
ALLOW = {
    ("backtest_dart.py", "price_history"): "backtest: tên file cache giá theo ngày — lệch 9 giờ chỉ đổi tên cache",
    ("backtest_dart.py", "fred_rf"): "backtest: tên file cache Rf theo tháng",
    ("analysis.py", "fetch_full"): "năm BCTC mặc định — G1/G3 (I32) chuyển sang reporting",
    ("analysis.py", "fetch_quarter_reports"): "dải năm quý — G3 (I32) chuyển sang reporting.state",
    ("peers.py", "build_peer_set"): "nhãn 'asof' chỉ để hiển thị — G6 sở hữu peers",
}


def _app_files():
    out = []
    for pat in ("*.py", "views/*.py", "pages/*.py"):
        out += glob.glob(os.path.join(ROOT, pat))
    return sorted(out)


def _attr_chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _naive_calls(path):
    """[(hàm chứa, dòng, mô tả)] các lời gọi datetime.now() không múi giờ, datetime.utcnow(), date/datetime.today()."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    found = []

    def visit(node, func):
        for ch in ast.iter_child_nodes(node):
            f = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and func == "<module>" else func
            if isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute):
                chain = _attr_chain(ch.func)
                meth, owner = chain[-1], (chain[-2] if len(chain) > 1 else "")
                tz_none = not ch.args and not any(k.arg == "tz" for k in ch.keywords)
                tz_none = tz_none or (ch.args and isinstance(ch.args[0], ast.Constant) and ch.args[0].value is None)
                if owner == "datetime" and meth == "now" and tz_none:
                    found.append((f, ch.lineno, ".".join(chain)))
                elif owner in ("date", "datetime") and meth in ("today", "utcnow"):
                    found.append((f, ch.lineno, ".".join(chain)))
            visit(ch, f)
    visit(tree, "<module>")
    return found


def test_scanner_catches_naive_clock(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("import datetime as _dt\nfrom datetime import datetime, date\n"
                 "a = _dt.datetime.now()\nb = _dt.date.today()\nc = datetime.now(None)\n"
                 "def f():\n    return date.today(), datetime.utcnow()\n"
                 "ok = _dt.datetime.now(_dt.timezone.utc), datetime.now(tz=None) if 0 else 1\n")
    got = [(f, ln) for f, ln, _ in _naive_calls(str(p))]
    assert got == [("<module>", 3), ("<module>", 4), ("<module>", 5), ("f", 7), ("f", 7)]


def test_no_naive_server_clock_in_app_modules():
    bad = []
    for path in _app_files():
        rel = os.path.relpath(path, ROOT)
        for func, line, what in _naive_calls(path):
            if (rel, func) not in ALLOW:
                bad.append(f"{rel}:{line} ({func}) {what}() — dùng guard.now_kst()/today_kst()/now_kst_naive()")
    assert not bad, "Giờ máy chủ (UTC trên Cloud) thay vì giờ Hàn:\n" + "\n".join(bad)
