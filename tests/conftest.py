"""Môi trường test KÍN (rà soát 18/11/2026, I71).

- Chặn mọi kết nối mạng ra ngoài (chỉ cho localhost). Test cần mạng thật đánh dấu  @pytest.mark.e2e  (và vẫn bị
  skip nếu không có KCI_E2E=1). Mạng treo / bị từ chối không còn làm CI đỏ hay làm test "xanh giả".
- Cô lập DART: bộ đếm hạn mức, thống kê, thư mục lưu báo cáo → tmp_path; bỏ khóa/mật khẩu thật khỏi môi trường.
  Chạy suite không còn cộng lượt vào .cache/dart_budget.json của repo.
- fake_market: dữ liệu thị trường cố định (Rf, chỉ số, chủ đề, tin, công bố) cho test mở trang bằng AppTest.
"""
from __future__ import annotations

import datetime as _dt
import socket

import pytest

def pytest_collection_modifyitems(config, items):
    """Test @pytest.mark.e2e bị skip khi không có KCI_E2E=1 — đúng như docstring trên (rà soát cuối 18/11/2026: trước
    đây chỉ dựa vào skipif viết tay, ai quên thì test gọi Naver/DART thật và cộng vào hạn mức DART của máy)."""
    import os
    if os.environ.get("KCI_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="test e2e cần mạng thật — chạy với KCI_E2E=1")
    for item in items:
        if item.get_closest_marker("e2e") is not None:
            item.add_marker(skip)


_LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
_PROXY_ENV = [k for b in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") for k in (b, b.lower())]
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class NetworkBlocked(ConnectionRefusedError):
    """Test gọi mạng ra ngoài khi không có marker e2e."""


def _is_local(host) -> bool:
    return str(host).strip("[]").lower() in _LOCAL


def _guard_getaddrinfo(host, *a, **k):
    if not _is_local(host):
        raise socket.gaierror(socket.EAI_NONAME, f"mạng bị chặn trong test: {host}")
    return _real_getaddrinfo(host, *a, **k)


def _guard_addr(sock, addr):
    if sock.family == getattr(socket, "AF_UNIX", object()):
        return
    host = addr[0] if isinstance(addr, tuple) else addr
    if not _is_local(host):
        raise NetworkBlocked(f"mạng bị chặn trong test: {host}")


def _guard_connect(self, addr):
    _guard_addr(self, addr)
    return _real_connect(self, addr)


def _guard_connect_ex(self, addr):
    _guard_addr(self, addr)
    return _real_connect_ex(self, addr)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("e2e"):
        yield
        return
    for k in _PROXY_ENV:                  # proxy ở localhost sẽ đi ra mạng thay cho test → bỏ proxy
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(socket, "getaddrinfo", _guard_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guard_connect_ex)
    yield


@pytest.fixture(autouse=True)
def _isolate_dart(request, tmp_path, monkeypatch):
    """Không đọc/ghi bộ đếm hạn mức và thư mục lưu của repo; không dùng khóa/mật khẩu thật.
    Test e2e gọi DART THẬT → giữ bộ đếm hạn mức thật để lượt gọi được tính vào hạn mức trong ngày."""
    import analysis
    import dart_api as D
    e2e = request.node.get_closest_marker("e2e") is not None
    if not e2e:
        monkeypatch.setattr(D, "BUDGET_PATH", str(tmp_path / "dart_budget.json"))
        monkeypatch.setattr(D, "_BUDGET", {"day": None, "used": 0})
    monkeypatch.setattr(D, "STATS", {"ok": 0, "nodata": 0, "errors": 0, "streak": 0, "open_until": 0.0,
                                     "last_error": ""})
    monkeypatch.setattr(D, "_last_call", [0.0])
    monkeypatch.setattr(D, "YEAR_CACHE_DIR", str(tmp_path / "dart_year"))
    monkeypatch.setattr(D, "REPORT_CACHE_DIR", str(tmp_path / "dart_report"))       # G3 (I32, I50)
    monkeypatch.setattr(D, "EXTRAS_CACHE_DIR", str(tmp_path / "dart_extras"))
    monkeypatch.setattr(D, "CORP_DL_PATH", str(tmp_path / "dart_corp.csv"))
    monkeypatch.setattr(D, "CORP_META_PATH", str(tmp_path / "dart_corp.meta.json"))
    monkeypatch.setattr(D, "_corp_retry", {"day": None})
    monkeypatch.setattr(D, "_corp_busy", {"on": False})
    monkeypatch.setattr(D, "_SESSIONS", {})
    monkeypatch.setattr(D, "_WINDOW", D.deque())
    monkeypatch.setattr(analysis, "CACHE", str(tmp_path / "dart_full"))
    monkeypatch.setattr(analysis, "Q_CACHE", str(tmp_path / "dart_q"))
    if not e2e:
        for k in ("DART_API_KEY", "OPENDART_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    for k in ("KCI_DART_DAILY_BUDGET", "KCI_OWNER_PASSWORD", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):      # khóa thật trong .env không lọt vào test
        monkeypatch.delenv(k, raising=False)
    import dossier                                            # hồ sơ do test sinh ra không rơi vào dossiers/ của repo
    _real, _ddir = dossier.DIR, str(tmp_path / "dossiers")
    monkeypatch.setattr(dossier, "DIR", _ddir)
    for _f in list(vars(dossier).values()):                   # tham số mặc định base=DIR đã gắn lúc import
        _d = getattr(_f, "__defaults__", None) if getattr(_f, "__module__", "") == "dossier" else None
        if _d and _real in _d:
            monkeypatch.setattr(_f, "__defaults__", tuple(_ddir if v == _real else v for v in _d))
    monkeypatch.setenv("KCI_PUBLIC", "0")
    yield


# ---------------------------------------------------------------- dữ liệu thị trường giả
def _fake_themes():
    import megatrend as MT
    nos = sorted({n for m in MT.MEGATRENDS for n in m["themes"]})
    out = []
    for i, no in enumerate(nos):
        chg = round(3.0 - i * 0.1, 2)
        total = 10
        rise = 7 if chg > 0 else 3
        out.append({"no": no, "name": f"테마{no}", "chg": chg, "rise": rise, "fall": total - rise,
                    "total": total, "breadth": rise / total * 100})
    return out, "CLOSE"


def _fake_detail(no, n=40):
    stocks = [{"ticker": f"{(int(no) * 10 + k) % 1000000:06d}", "name": f"종목{no}-{k}",
               "price": 10000.0 + k * 100, "chg": 2.0 - k, "tv_eok": 50.0, "cap_eok": 5000.0,
               "reason": "테스트"} for k in range(3)]
    return {"desc": f"테마 {no} 설명", "stocks": stocks, "info": {}}


def _fake_indices(codes=("KOSPI", "KOSDAQ")):
    return [{"code": c, "price": 2500.0 if c == "KOSPI" else 800.0, "chg_pt": 10.0, "chg": 0.4,
             "dir": "RISING", "status": "CLOSE", "at": "2026-11-18T15:30:00+09:00"} for c in codes]


def _fake_news(*a, **k):
    now = _dt.datetime(2026, 11, 18, 10, 0)
    return [{"title": "삼성전자 대규모 공급계약 체결", "summary": "테스트 기사", "dt": now, "office": "테스트",
             "url": "https://n.news.naver.com/mnews/article/001/0000000001", "src": "Tin nhanh"}]


def _fake_stock_news(ticker, n=10, name=""):
    return [{"title": f"{name or ticker} 수주", "summary": "", "dt": _dt.datetime(2026, 11, 18, 9, 0),
             "office": "테스트", "url": f"https://n.news.naver.com/mnews/article/002/{ticker}",
             "src": "Tin theo mã", "ticker_hint": ticker, "hint_name": name}]


def _fake_dart(days=1, max_pages=15):
    return [{"date": "20261118", "rcept_no": "20261118000001", "market": "KOSPI", "ticker": "005930",
             "name": "삼성전자", "report": "단일판매ㆍ공급계약체결", "label": "Hợp đồng cung cấp", "pol": 1,
             "score": 80, "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261118000001"}], None


def _fake_realtime(tickers):
    return {t: {"price": 10000.0, "chg": 1.0, "tv": 1} for t in tickers}


FAKE_RF = {"value": 0.0325, "source": "test", "asof": "2026-11-18", "live": True, "note": ""}


@pytest.fixture
def fake_market(monkeypatch):
    """Thay mọi nguồn thị trường mà các trang gọi khi MỞ bằng dữ liệu cố định (không mạng, kết quả xác định)."""
    import streamlit as st

    import hotnews as H
    import megatrend as MT
    import screener
    monkeypatch.setattr(H, "theme_ranking", lambda max_pages=4: _fake_themes())
    monkeypatch.setattr(H, "theme_detail", _fake_detail)
    monkeypatch.setattr(H, "market_news", _fake_news)
    monkeypatch.setattr(H, "stock_news", _fake_stock_news)
    monkeypatch.setattr(H, "dart_disclosures", _fake_dart)
    monkeypatch.setattr(H, "realtime", _fake_realtime)
    monkeypatch.setattr(MT, "market_indices", _fake_indices)
    monkeypatch.setattr(screener, "rf_for_valuation", lambda: dict(FAKE_RF))
    st.cache_data.clear()                 # trang lưu ngắn kết quả: không để lượt trước (thật/giả) lọt vào
    yield
    st.cache_data.clear()
