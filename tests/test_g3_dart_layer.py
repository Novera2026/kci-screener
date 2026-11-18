"""Lớp DART và lượt gọi lặp (rà soát 18/11/2026, nhóm G3: I10, I24, I32, I50, I51, cải tiến C7).

Không mạng: requests.get / fetch_statements / fetch_financials đều là bản giả có bộ đếm. Ngày giả qua guard._now_utc.
"""
import datetime as dt
import hashlib
import io
import json
import os
import zipfile

import pytest

import analysis as A
import coverage as COV
import dart_api as D
import guard as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc


@pytest.fixture
def clock(monkeypatch):
    def set_(y, m, d, h=3):          # giờ UTC (03:00 UTC = 12:00 KST cùng ngày)
        monkeypatch.setattr(G, "_now_utc", lambda: dt.datetime(y, m, d, h, tzinfo=UTC))
    set_(2026, 11, 18)
    return set_


class _Resp:
    def __init__(self, payload=None, content=b""):
        self._p, self.content = payload, content

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _Calls(list):
    pass


@pytest.fixture
def dart(monkeypatch):
    """DART giả qua requests.get; trả list các lượt gọi (params)."""
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(D, "RETRY_WAITS", (0, 0))
    monkeypatch.setattr(D, "MIN_GAP", 0)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    calls, box = _Calls(), {"h": lambda p: _Resp({"status": "013", "message": "no data"})}

    def get(url, params=None, timeout=None):
        calls.append(dict(params or {}, _url=url))
        return box["h"](params or {})
    monkeypatch.setattr(D.requests, "get", get)
    calls.handler = box
    return calls


def _items(**amounts):
    """Danh sách tài khoản giả (số tiền theo 억원)."""
    names = {"ebit": ("IS", "영업이익", "dart_OperatingIncomeLoss"), "cash": ("BS", "현금및현금성자산", "x"),
             "cfo": ("CF", "영업활동현금흐름", "x"), "assets": ("BS", "자산총계", "ifrs-full_Assets"),
             "revenue": ("IS", "매출액", "ifrs-full_Revenue")}
    out = []
    for k, v in amounts.items():
        sj, nm, aid = names[k]
        out.append({"sj_div": sj, "account_nm": nm, "account_id": aid, "thstrm_amount": str(int(v * 1e8)),
                    "currency": "KRW"})
    return out


# ================================================================ get_json — hợp đồng dùng chung (G4, G8)
def test_get_json_contract(dart, monkeypatch):
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": [1]})
    j = D.get_json("list.json", corp_code="1", bgn_de="20261118")
    assert j["status"] == "000" and dart[-1]["_url"].endswith("/api/list.json")
    assert dart[-1]["crtfc_key"] == "test-key" and dart[-1]["bgn_de"] == "20261118"
    dart.handler["h"] = lambda p: _Resp({"status": "013"})
    assert D.get_json("/list.json")["status"] == "013"
    assert D.STATS["ok"] == 1 and D.STATS["nodata"] == 1
    dart.handler["h"] = lambda p: _Resp({"status": "020", "message": "limit"})
    n = len(dart)
    with pytest.raises(D.DartError) as e:
        D.get_json("list.json")
    assert "020" in str(e.value) and len(dart) == n + 1          # hạn mức khóa: không thử lại
    assert "test-key" not in str(e.value)


# ================================================================ I32 — nhớ 'không có báo cáo', bỏ kỳ chưa kết thúc
def test_year_013_is_remembered_in_memory_and_on_disk(dart, clock):
    assert D._fetch_year("C1", 2022) is None
    n = len(dart)
    assert n == 2                                                   # CFS + OFS
    assert D._fetch_year("C1", 2022) is None and len(dart) == n     # lần 2: 0 lượt
    D._YEAR_CACHE.clear()                                           # tiến trình mới: đọc .none.json
    assert D._fetch_year("C1", 2022) is None and len(dart) == n
    assert os.path.exists(os.path.join(D.YEAR_CACHE_DIR, "C1_2022.none.json"))


def test_year_negative_cache_ttl_by_age(dart, clock):
    for y in (2023, 2025):
        D._fetch_year("C1", y)
    n = len(dart)
    D._YEAR_CACHE.clear()
    for y in (2023, 2025):                                          # đánh dấu 'không có' 7 giờ trước
        fp = os.path.join(D.YEAR_CACHE_DIR, f"C1_{y}.none.json")
        m = json.load(open(fp))
        m["at"] -= 7 * 3600
        json.dump(m, open(fp, "w"))
    D._fetch_year("C1", 2023)                                       # năm ≤ năm nay − 2: tin 30 ngày
    assert len(dart) == n
    D._fetch_year("C1", 2025)                                       # năm gần: chỉ tin 6 giờ → hỏi lại
    assert len(dart) == n + 2


def test_year_error_is_never_remembered(dart, clock):
    dart.handler["h"] = lambda p: (_ for _ in ()).throw(ConnectionError("x"))
    with pytest.raises(D.DartError):
        D._fetch_year("C1", 2022)
    assert not os.path.exists(D.YEAR_CACHE_DIR) or not os.listdir(D.YEAR_CACHE_DIR)
    assert ("C1", 2022) not in D._YEAR_CACHE


def test_year_not_ended_is_not_asked(dart, clock):
    clock(2026, 12, 20)
    assert D._fetch_year("C1", 2026) is None and dart == []


def test_quarter_reports_skip_unended_periods(dart, clock, monkeypatch, tmp_path):
    """10/01/2026: trước đây 8/10 lượt hỏi kỳ 2026 chưa kết thúc."""
    clock(2026, 1, 10)
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C1")
    A.fetch_quarter_reports("005930", years=1, cache_dir=str(tmp_path))
    assert dart and all(p["bsns_year"] != "2026" for p in dart)
    assert {p["reprt_code"] for p in dart if p["bsns_year"] == "2025"} == {"11013", "11012", "11014", "11011"}
    dart.clear()
    D.quarterly_financials("005930")
    assert all(p["bsns_year"] != "2026" for p in dart)


def test_quarter_reports_013_remembered(dart, clock, monkeypatch, tmp_path):
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C1")
    A.fetch_quarter_reports("005930", years=1, cache_dir=str(tmp_path))
    n = len(dart)
    assert n > 0
    A.fetch_quarter_reports("005930", years=1, cache_dir=str(tmp_path))
    assert len(dart) == n                                           # lần 2: 0 lượt
    D.quarterly_financials("005930")
    m = len(dart)
    D.quarterly_financials("005930")
    assert len(dart) == m


def test_quarter_report_with_data_is_kept(dart, clock, monkeypatch):
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": _items(assets=1000, revenue=300)})
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C1")
    q = D.quarterly_financials("005930", max_reports=2)
    n = len(dart)
    assert len(q) == 2 and q[-1]["revenue"] == 300
    assert D.quarterly_financials("005930", max_reports=2) == q and len(dart) == n


def test_valuation_extras_cached_on_disk(dart, clock, monkeypatch):
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C" + t)
    # có báo cáo nhưng thiếu dòng tiền HĐ (ngân hàng, bảo hiểm…): {} cũng được nhớ
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": _items(ebit=50, cash=10)})
    assert D.valuation_extras("105560") == {}
    n = len(dart)
    assert n == 4                                                   # 2 năm × CFS/OFS
    assert D.valuation_extras("105560") == {} and len(dart) == n
    assert D.extras_cached("105560")
    # mã có số: lần 2 đọc đĩa
    good = _items(ebit=50, cash=10, cfo=60)
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": good})
    e = D.valuation_extras("005930")
    m = len(dart)
    assert e["ebit"] == 50 and e["cfo"] == 60 and m == n + 1
    assert D.valuation_extras("005930") == e and len(dart) == m


def test_valuation_extras_error_not_cached(dart, clock, monkeypatch):
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C" + t)
    dart.handler["h"] = lambda p: (_ for _ in ()).throw(ConnectionError("x"))
    errs = []
    assert D.valuation_extras("005930", errors=errs) == {} and errs
    assert not D.extras_cached("005930")
    assert not os.path.exists(D.EXTRAS_CACHE_DIR) or not os.listdir(D.EXTRAS_CACHE_DIR)


def test_valuation_extras_reads_new_cfo_name_and_standard_id(dart, clock, monkeypatch):
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C" + t)
    rows = _items(ebit=50, cash=10) + [
        {"sj_div": "CF", "account_nm": "영업활동으로부터의 현금흐름", "account_id": "-", "thstrm_amount": "7000000000"}]
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": rows})
    assert D.valuation_extras("000001")["cfo"] == 70
    rows2 = _items(ebit=50, cash=10) + [{"sj_div": "CF", "account_nm": "영업현금", "thstrm_amount": "8000000000",
                                         "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities"}]
    dart.handler["h"] = lambda p: _Resp({"status": "000", "list": rows2})
    assert D.valuation_extras("000002")["cfo"] == 80


# ================================================================ I51 — DART lỗi ở báo cáo quý ≠ 'không có'
def test_quarter_error_gives_coverage_error_not_new_listing(dart, clock, monkeypatch):
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C1")

    def h(p):
        if p.get("reprt_code") == "11011":
            return _Resp({"status": "013"})                         # chưa có báo cáo năm (mã mới niêm yết)
        raise ConnectionError("reset")
    dart.handler["h"] = h
    st = D.data_status("0126Z0")
    q_calls = [p for p in dart if p["reprt_code"] != "11011"]
    assert st["coverage"] == "error" and "báo cáo quý" in st["note"] and st["is_new_listing"] is None
    assert len(q_calls) <= 3                                        # dừng ở lỗi đầu tiên (1 lần + 2 lần thử lại)
    errs = []
    assert D._fetch_report("C1", 2025, "11013", errors=errs) is None and errs
    nav = {"annual": [{"period": "202512", "is_consensus": False, "revenue": 1.0}], "quarter": []}
    rep = COV.assess_coverage("0126Z0", fin=nav)
    assert rep["dart_error"] and rep["confidence_cap"] == "Low" and not rep["is_new_listing"]
    assert "DART đang lỗi" in COV.coverage_badge(rep) and "mới niêm yết" not in COV.coverage_badge(rep)


def test_assess_coverage_uses_given_financials(monkeypatch):
    import financials
    monkeypatch.setattr(financials, "fetch_financials",
                        lambda t: (_ for _ in ()).throw(AssertionError("không được tải lại Naver")))
    nav = {"annual": [{"period": f"{y}12", "is_consensus": False} for y in (2023, 2024, 2025)], "quarter": []}
    rep = COV.assess_coverage("005930", fin=nav)
    assert rep["real_years"] == 3 and rep["valuation_mode"] == "intrinsic_ok"


# ================================================================ I24 — chi phí ghi số âm
def _fs(**cur):
    return {"year": 2025, "fs_div": "CFS", "cur": cur, "prev": {}}


def _interest(fs):
    return next(c for c in A.quality(fs)["checks"] if c["Chỉ tiêu"].startswith("Khả năng trả lãi"))


def test_interest_cover_with_negative_interest_paid():
    loss = _interest(_fs(op=-100.0, interest_paid=-5.0, assets=1000.0))
    assert loss["Kết quả"] == A.FAIL                                 # lỗ HĐ không được ✅
    prof = _interest(_fs(op=100.0, interest_paid=-5.0, assets=1000.0))
    assert prof["Kết quả"] == A.OK and prof["Giá trị"] == "20.0×"   # có lãi không bị ❌ oan
    assert _interest(_fs(op=0.0, fin_cost=3.0, assets=1000.0))["Kết quả"] == A.FAIL


def test_decompose_uses_absolute_outflows():
    fs = _fs(op=100.0, pretax=80.0, fin_income=10.0, fin_cost=-30.0, cfo=200.0, capex=100.0,
             dividends_paid=-50.0, revenue=1000.0, assets=2000.0, equity=1000.0, ni=60.0)
    d = A.decompose(fs)
    fin = next(x for x in d["structure"] if x["Thành phần"] == "Tài chính ròng")
    assert fin["억원"] == -20.0                                      # thu − |chi|
    assert d["cash"]["Cổ tức đã trả / FCF %"] == 50.0               # dương
    q = {"label": "Q1.26", "flow": {"revenue": 100.0, "pretax": 8.0, "op": 10.0, "fin_income": 1.0,
                                    "fin_cost": -3.0}, "cf": {}, "bs": {}, "yoy": {}}
    row = A.decompose_quarters([q])[0]
    assert row["Tài chính ròng / LNTT %"] == pytest.approx(-25.0)


def test_parsers_normalize_negative_outflows(tmp_path, monkeypatch):
    items = [{"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "100000000000",
              "frmtrm_amount": "90000000000"},
             {"sj_div": "CF", "account_id": "ifrs-full_InterestPaidClassifiedAsOperatingActivities",
              "thstrm_amount": "-1430000000", "frmtrm_amount": "-1000000000"},
             {"sj_div": "IS", "account_id": "ifrs-full_FinanceCosts", "thstrm_amount": "-500000000",
              "thstrm_add_amount": "-900000000"},
             {"sj_div": "CF", "account_id": "ifrs-full_DividendsPaidClassifiedAsFinancingActivities",
              "thstrm_amount": "-200000000"}]
    cur, prev = A.parse_statements(items)
    assert cur["interest_paid"] == pytest.approx(14.3) and prev["interest_paid"] == pytest.approx(10.0)
    assert cur["fin_cost"] == 5.0 and cur["dividends_paid"] == 2.0
    rep = A.parse_report(items)
    assert rep["pt"]["fin_cost"] == 5.0 and rep["ytd"]["fin_cost"] == 9.0
    # bản lưu cũ trong .cache/dart_q còn số âm → chuẩn hóa khi nạp, không gọi lại DART
    monkeypatch.setattr(A.dart_api, "fetch_statements",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được gọi DART")))
    (tmp_path / "C1_2025_11013.json").write_text(json.dumps(
        {"year": 2025, "rc": "11013", "fs_div": "CFS", "_v": A.FULL_V, "pt": {"assets": 1.0, "fin_cost": -3.0},
         "ytd": {"fin_cost": -3.0}, "yoy": {}, "sj": {}}))
    got = A._fetch_report("C1", 2025, "11013", None, str(tmp_path))
    assert got["pt"]["fin_cost"] == 3.0 and got["ytd"]["fin_cost"] == 3.0


# ================================================================ I50 — danh mục corp_code
def _corp_zip(rows):
    xml = "<result>" + "".join(f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name>"
                               f"<stock_code>{s}</stock_code></list>" for s, c, n in rows) + "</result>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


@pytest.fixture
def corp(monkeypatch, dart):
    monkeypatch.setattr(D, "_corp_map", None)
    box = {"rows": [("005930", "00126380", "삼성전자"), ("0147B0", "01999999", "래블업")]}
    dart.handler["h"] = lambda p: _Resp(content=_corp_zip(box["rows"]))
    dart.box = box
    return dart


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_corp_map_meta_drives_freshness_and_repo_file_untouched(corp, clock, tmp_path, monkeypatch):
    # bản chép của dart_corp.csv (không đổi mtime file git theo dõi trong repo — test phải kín)
    import shutil
    (tmp_path / "repo").mkdir()
    repo = str(tmp_path / "repo" / "dart_corp.csv")                # khác CORP_DL_PATH (tmp_path/dart_corp.csv)
    shutil.copy2(D._CORP_CACHE, repo)
    monkeypatch.setattr(D, "_CORP_CACHE", repo)
    sha, ino = _sha(repo), os.stat(repo).st_ino
    os.utime(repo, None)                                            # mtime mới (bản clone) không còn được coi là mới
    assert not D._cache_fresh()
    assert D.corp_code_of("0147B0") == "01999999" and len(corp) == 1   # tải lại danh mục
    assert _sha(repo) == sha and os.stat(repo).st_ino == ino        # file của repo không bị ghi đè
    assert D._cache_fresh() and os.path.exists(D.CORP_DL_PATH)
    meta = json.load(open(D.CORP_META_PATH))
    old = dt.datetime.now(UTC) - dt.timedelta(days=31)
    json.dump(dict(meta, downloaded_at=old.isoformat()), open(D.CORP_META_PATH, "w"))
    assert not D._cache_fresh()                                     # meta 31 ngày → cũ


def test_unknown_ticker_reloads_map_once_per_day(corp, clock):
    json.dump({"downloaded_at": dt.datetime.now(UTC).isoformat()}, open(D.CORP_META_PATH, "w"))
    open(D.CORP_DL_PATH, "w").write("stock_code,corp_code,corp_name\n005930,00126380,삼성전자\n")
    assert D.corp_code_of("005930") == "00126380" and corp == []
    assert D.corp_code_of("0147B0") == "01999999" and len(corp) == 1   # mã lạ → tải lại 1 lần
    assert D.corp_code_of("0158S0") is None and D.corp_code_of("0158S0") is None
    assert len(corp) == 1                                           # không tải lại lần nữa trong ngày
    clock(2026, 11, 19)
    D.corp_code_of("0158S0")
    assert len(corp) == 2


def test_no_corp_code_note_names_map_date(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(D, "corp_code_of", lambda t: None)
    assert "danh mục DART ngày" in D.data_status("0158S0")["note"]


# ================================================================ C7 — hạn mức theo phiên cho khách trên Cloud
@pytest.fixture
def guest(monkeypatch, clock):
    import streamlit.runtime.scriptrunner as SR

    class Ctx:
        session_id = "s1"
    box = {"ctx": Ctx(), "owner": False}
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(SR, "get_script_run_ctx", lambda suppress_warning=False: box["ctx"])
    monkeypatch.setattr(G, "owner_ok", lambda: box["owner"])
    monkeypatch.setattr(D, "SESSION_PER_HOUR", 3)
    return box


def test_guest_session_budget(guest, monkeypatch):
    for _ in range(3):
        assert D.budget_take()
    assert D.budget_left() == 0
    assert not D.budget_take()
    ok, why = D.budget_allows(1)
    assert not ok and "phiên" in why
    monkeypatch.setenv("DART_API_KEY", "test-key")
    with pytest.raises(D.DartError) as e:
        D.get_json("list.json")
    assert "phiên" in str(e.value)
    guest["ctx"].session_id = "s2"                                  # phiên khác: thùng riêng
    assert D.budget_take()
    guest["owner"] = True                                           # chủ app: không hạn mức phiên
    for _ in range(10):
        assert D.budget_take()


def test_guest_share_of_daily_budget(guest, monkeypatch):
    monkeypatch.setattr(D, "SESSION_PER_HOUR", 1000)
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "10")
    for _ in range(5):
        assert D.budget_take()
    assert not D.budget_take()                                      # khách chỉ được 50% hạn mức ngày
    guest["owner"] = True
    assert D.budget_take()


def test_guest_share_counts_only_guest_calls(guest, monkeypatch):
    """Phần 50% của khách tính trên số KHÁCH đã dùng — chủ app / bot dùng nhiều không làm khách bị chặn."""
    monkeypatch.setattr(D, "SESSION_PER_HOUR", 1000)
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "10")
    guest["owner"] = True
    for _ in range(6):                                              # chủ app dùng 6/10 (> 50%)
        assert D.budget_take()
    guest["owner"] = False
    assert D.budget_allows(1)[0] and D.budget_take()                # khách vẫn được gọi
    assert D.budget_left() == 3                                     # min(10-7, 5-1, …)
    for _ in range(3):
        assert D.budget_take()
    ok, why = D.budget_allows(1)
    assert not ok                                                   # hết hạn mức ngày (10/10)
    saved = json.load(open(D.BUDGET_PATH, encoding="utf-8"))
    assert saved["used"] == 10 and saved["guest"] == 4              # lưu đĩa cả phần khách


def test_guest_share_survives_restart(guest, monkeypatch):
    monkeypatch.setattr(D, "SESSION_PER_HOUR", 1000)
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "10")
    with open(D.BUDGET_PATH, "w", encoding="utf-8") as f:
        json.dump({"day": "20261118", "used": 7, "guest": 5}, f)
    ok, why = D.budget_allows(1)
    assert not ok and "khách" in why


def test_budget_used_reads_file_after_restart(monkeypatch, clock):
    with open(D.BUDGET_PATH, "w", encoding="utf-8") as f:
        json.dump({"day": "20261118", "used": 3000}, f)
    monkeypatch.setenv("KCI_DART_DAILY_BUDGET", "10000")
    assert D.budget_used() == 3000                                  # chưa gọi DART lần nào trong tiến trình mới
    assert D.budget_left() == 7000


def test_worker_threads_carry_guest_session(guest, monkeypatch):
    """Luồng phụ (ThreadPool) không có ctx Streamlit: tạo pool bằng pool_kwargs() thì vẫn tính vào hạn mức phiên."""
    import concurrent.futures as cf
    import threading
    import streamlit.runtime.scriptrunner as SR
    main = threading.main_thread()
    monkeypatch.setattr(SR, "get_script_run_ctx",
                        lambda suppress_warning=False: guest["ctx"] if threading.current_thread() is main else None)
    with cf.ThreadPoolExecutor(max_workers=2) as ex:                # pool thường: không thấy phiên
        assert list(ex.map(lambda _: D.is_guest(), range(2))) == [False, False]
    with cf.ThreadPoolExecutor(max_workers=2, **D.pool_kwargs()) as ex:
        got = list(ex.map(lambda _: D.budget_take(), range(5)))
    assert got.count(True) == 3 and got.count(False) == 2           # SESSION_PER_HOUR = 3 trong fixture
    assert not D.budget_take()                                      # luồng chính cùng phiên: đã hết
    guest["owner"] = True                                           # chủ app tạo pool: không hạn mức phiên
    with cf.ThreadPoolExecutor(max_workers=2, **D.pool_kwargs()) as ex:
        assert all(ex.map(lambda _: D.budget_take(), range(5)))


def test_process_window_cap_on_cloud_waits_instead_of_refusing(guest, monkeypatch):
    """Trần 5 phút đầy → CHỜ tới khi lượt cũ nhất ra khỏi cửa sổ, không từ chối (rà soát cuối 18/11/2026: lượt quét
    của chủ app gãy ở lượt 601 sau ~150 giây với giãn nhịp 0,25 s)."""
    guest["owner"] = True
    clock = {"t": 1_000_000.0}
    slept = []
    monkeypatch.setattr(D.time, "time", lambda: clock["t"])

    def _sleep(s):
        slept.append(s)
        clock["t"] += s
    monkeypatch.setattr(D.time, "sleep", _sleep)
    monkeypatch.setattr(D, "WINDOW_MAX", 4)
    for _ in range(4):
        assert D.budget_take()
        clock["t"] += 0.25
    ok, why = D.budget_allows(1)                     # ước lượng trước tác vụ nặng: cửa sổ đầy chỉ làm chậm
    assert ok and not why
    assert D.budget_take()                           # lượt thứ 5: chờ rồi được gọi
    assert slept and sum(slept) <= D.WINDOW_SECS + 1
    assert len(D._WINDOW) <= 4


def test_window_800_calls_none_refused(guest, monkeypatch):
    guest["owner"] = True
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(D.time, "time", lambda: clock["t"])
    monkeypatch.setattr(D.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(D, "daily_budget", lambda: 0)
    got = []
    for _ in range(800):
        got.append(D.budget_take())
        clock["t"] += D.CLOUD_MIN_GAP
    assert all(got)
    assert clock["t"] - 2_000_000.0 >= D.WINDOW_SECS            # đã chờ thật, không vượt 600 lượt / 5 phút


def test_budget_day_is_kst(monkeypatch):
    import time
    monkeypatch.setattr(G, "_now_utc", lambda: dt.datetime(2026, 11, 17, 23, 30, tzinfo=UTC))   # 08:30 KST 18/11
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"                                        # máy chủ Cloud chạy UTC
    time.tzset()
    try:
        assert D.budget_take()
        assert D._BUDGET["day"] == "20261118"
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_cloud_throttle_gap(monkeypatch):
    waits = []
    monkeypatch.setattr(D.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(D, "MIN_GAP", 0.0)
    monkeypatch.setattr(D, "_last_call", [D.time.time()])
    monkeypatch.setenv("KCI_PUBLIC", "1")
    D._throttle()
    assert waits and 0.2 < waits[0] <= D.CLOUD_MIN_GAP


# ================================================================ I10 — trang Định giá tổng hợp không gọi lại khi chạy lại
VIEW = os.path.join(ROOT, "views", "tong_hop.py")


def _screen(tickers):
    import screener as S
    rows, fins = [], {}
    for i, tk in enumerate(tickers):
        rows.append(S.StockRow(ticker=tk, name=f"Cty {tk}", sector="Tech", industry_kr="반도체", price=10000.0 + i,
                               high_52w=12000.0, low_52w=8000.0, market_cap=1e12, shares=1e8, per=10.0, pbr=1.0,
                               eps=1000.0, bps=10000.0, div_yield=1.0, dps=100.0, net_income=1e11).to_dict())
        fins[tk] = {"annual": [{"period": f"{y}12", "label": f"{y}.12", "revenue": 1000.0, "op_profit": 100.0,
                                "net_profit": 80.0, "ctrl_net": 80.0, "eps": 1000.0, "bps": 10000.0, "roe": 10.0,
                                "debt_ratio": 50.0, "is_consensus": False} for y in (2023, 2024, 2025)],
                    "quarter": []}
    return {"records": rows, "unresolved": [], "resolutions": [], "fin": fins, "beta": {t: 1.0 for t in tickers},
            "pipe_series": {}, "peer_map": {}, "fetched_at": "x"}


@pytest.fixture
def page(monkeypatch, fake_market, clock):
    import financials
    import peers
    import screener
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(D, "MIN_GAP", 0)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    monkeypatch.setattr(D, "corp_code_of", lambda t: "C" + str(t))
    cnt = {"dart": 0, "naver": 0, "extras": 0}

    def fs(*a, **k):
        cnt["dart"] += 1
        return None
    monkeypatch.setattr(D, "fetch_statements", fs)
    real_extras = D.valuation_extras

    def ve(*a, **k):
        cnt["extras"] += 1
        return real_extras(*a, **k)
    monkeypatch.setattr(D, "valuation_extras", ve)

    def ff(t):
        cnt["naver"] += 1
        return _screen([t])["fin"][t]
    monkeypatch.setattr(financials, "fetch_financials", ff)
    monkeypatch.setattr(screener, "fetch_weekly_closes", lambda *a, **k: None)
    monkeypatch.setattr(peers, "peer_rows_batch", lambda *a, **k: {})
    return cnt


def _open(tickers, adv=False):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(VIEW, default_timeout=300)
    at.session_state["screen"] = _screen(tickers)
    if adv:
        at.session_state["adv_dart"] = True
    return at


def test_tong_hop_reruns_make_no_new_calls(page):
    at = _open(["064400", "005930"], adv=True)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    first = dict(page)
    assert first["dart"] > 0                                        # lần đầu có hỏi DART
    for _ in range(2):
        at.run()
        assert not at.exception
    assert page == first                                            # chạy lại: 0 lượt Naver / DART / extras
    sel = next(s for s in at.selectbox if (s.label or "").startswith("Chọn mã"))
    sel.set_value(sel.options[-1]).run()
    n = dict(page)
    at.run()
    assert page == n


def test_tong_hop_adv_on_public_app_once_then_zero(page, monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    at = _open(["064400", "005930", "000660"], adv=True)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    n = page["extras"]
    assert n == 3
    d = page["dart"]
    for _ in range(4):
        at.run()
    assert page["extras"] == n and page["dart"] == d


def test_tong_hop_adv_waits_when_server_busy(page, monkeypatch):
    monkeypatch.setattr(G, "_heavy", G.threading.BoundedSemaphore(1))
    G._heavy.acquire()
    try:
        at = _open(["064400"], adv=True)
        at.run()
        assert not at.exception
        assert page["extras"] == 0
        assert any("tác vụ nặng" in w.value for w in at.warning)
    finally:
        G._heavy.release()


def test_tong_hop_adv_list_too_long_for_guest(page, monkeypatch):
    monkeypatch.setattr(D, "SESSION_PER_HOUR", 3)
    monkeypatch.setattr(D, "is_guest", lambda: True)
    monkeypatch.setattr(D, "budget_allows", lambda n: (False, D.SESSION_MSG.format(n=3)))
    at = _open(["064400", "005930"], adv=True)                      # cần ≥ 4 lượt > 3 lượt/giờ của khách
    at.run()
    assert not at.exception
    assert page["extras"] == 0
    assert any("danh sách quá dài" in w.value for w in at.warning)


def test_prefetch_pipeline_pool_carries_session(monkeypatch):
    src = open(VIEW, encoding="utf-8").read()
    i = src.index("\ndef _prefetch_pipeline")
    body = src[i:src.index("\ndef ", i + 10)]                      # tong_hop là trang Streamlit, không import được
    assert "pool_kwargs()" in body                                  # luồng phụ build_annual_series → DART


def test_dart_extras_drops_foreign_currency(page, monkeypatch):
    monkeypatch.setattr(D, "valuation_extras", lambda *a, **k: {"ebit": 5.0, "cfo": 6.0, "currency": "USD",
                                                                "net_debt": 1})
    at = _open(["064400"], adv=True)
    at.run()
    assert not at.exception
    tbl = [d for d in at.dataframe if "EV/EBIT" in getattr(d.value, "columns", [])]
    assert tbl and tbl[0].value["EV/EBIT"].isna().all()             # số USD không vào EV/EBIT tính bằng KRW


# ================================================================ I32 bước 5 — trang 🧮 nhớ chuỗi năm trong phiên
def test_valuation_page_rerun_does_not_rebuild_series(monkeypatch, clock):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import financials
    import peers as PR
    import pipeline as P
    import screener
    from tests.test_pipeline import _row, _series

    tk, s = "000001", _series()
    row = dict(_row(s), ticker=tk, source="test")

    class Reg:
        def resolve(self, q):
            return tk

        def industry_of(self, t):
            return "식품"

        def sector_of(self, t):
            return None

    class Row(dict):
        def to_dict(self):
            return dict(self)
    built = []
    monkeypatch.setattr(screener, "TickerRegistry", Reg)
    monkeypatch.setattr(screener, "fetch_one", lambda *a, **k: Row(row))
    monkeypatch.setattr(screener, "fetch_many", lambda *a, **k: ([], []))
    monkeypatch.setattr(screener, "fetch_weekly_closes", lambda *a, **k: [])
    monkeypatch.setattr(screener, "beta_vs_market", lambda *a, **k: 1.0)
    monkeypatch.setattr(screener, "rf_for_valuation",
                        lambda: {"value": 0.03, "live": True, "source": "test", "asof": "", "note": ""})
    monkeypatch.setattr(financials, "fetch_financials", lambda t: {"annual": s, "quarter": []})
    monkeypatch.setattr(financials, "build_annual_series", lambda *a, **k: built.append(1) or [dict(c) for c in s])
    monkeypatch.setattr(D, "valuation_extras", lambda *a, **k: {})
    monkeypatch.setattr(D, "available", lambda: False)
    monkeypatch.setattr(A, "fetch_full", lambda *a, **k: None)
    monkeypatch.setattr(A, "fetch_quarter_reports", lambda *a, **k: [])
    monkeypatch.setattr(PR, "domestic_candidates", lambda *a, **k: [])
    monkeypatch.setattr(PR, "fetch_foreign", lambda *a, **k: [])
    monkeypatch.setattr(P, "log_append", lambda *a, **k: None)
    st.cache_data.clear()
    st.cache_resource.clear()
    try:
        at = AppTest.from_file(os.path.join(ROOT, "pages", "3_🧮_Dinh_gia.py"), default_timeout=120)
        for k in ("vp_ticker", "vp_query", "vp_query_box"):
            at.session_state[k] = tk
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        n = len(built)
        assert n >= 1
        at.run()
        at.run()
        assert not at.exception
        assert len(built) == n                                      # chạy lại: không dựng lại chuỗi năm (DART)
    finally:
        st.cache_data.clear()
        st.cache_resource.clear()


# ---------------------------------------------------------------- rà soát cuối 18/11/2026: tải danh mục ngoài khóa
def _fresh_map(rows):
    json.dump({"downloaded_at": dt.datetime.now(UTC).isoformat()}, open(D.CORP_META_PATH, "w"))
    with open(D.CORP_DL_PATH, "w") as f:
        f.write("stock_code,corp_code,corp_name\n" + "".join(f"{s},{c},{n}\n" for s, c, n in rows))


def test_corp_download_does_not_block_other_lookups(corp, clock):
    import threading
    import time as _t
    _fresh_map([("005930", "00126380", "삼성전자")])
    gate, started = threading.Event(), threading.Event()
    zip_ = _corp_zip(corp.box["rows"])

    def slow(p):
        started.set()
        gate.wait(5)
        return _Resp(content=zip_)
    corp.handler["h"] = slow
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("A", D.corp_code_of("0147B0")))
    th.start()
    assert started.wait(5)                                          # luồng A đang tải danh mục
    t0 = _t.monotonic()
    assert D.corp_code_of("005930") == "00126380"                   # luồng khác không đứng chờ lượt tải
    assert _t.monotonic() - t0 < 1.0
    gate.set()
    th.join(5)
    assert out["A"] == "01999999" and len(corp) == 1


def test_corp_download_respects_breaker_and_skips_preferred(corp, clock):
    _fresh_map([("005930", "00126380", "삼성전자")])
    assert D.corp_code_of("005935") is None and corp == []          # cổ phiếu ưu đãi: không tải lại danh mục
    D.STATS["open_until"] = D.time.time() + 600                     # ngắt mạch đang mở
    assert D.corp_code_of("0147B0") is None and corp == []          # không gọi DART khi đang bị chặn


def test_corp_download_failure_is_recorded(corp, clock):
    _fresh_map([("005930", "00126380", "삼성전자")])

    def boom(p):
        raise D.requests.ConnectionError("x")
    corp.handler["h"] = boom
    assert D.corp_code_of("0147B0") is None
    assert D.STATS["errors"] == 1 and "corpCode" in D.STATS["last_error"]
    assert D.corp_code_of("005930") == "00126380"                   # bản đang có vẫn dùng
