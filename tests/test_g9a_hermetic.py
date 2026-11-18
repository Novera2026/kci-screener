"""Bộ test kín (rà soát 18/11/2026, I71): không mạng ra ngoài, không đụng bộ đếm DART / khóa thật của máy."""
import os
import socket

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_outbound_network_is_blocked_fast():
    with pytest.raises(socket.gaierror, match="mạng bị chặn"):
        socket.getaddrinfo("m.stock.naver.com", 443)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="mạng bị chặn"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://m.stock.naver.com/api/index/KOSPI/basic", timeout=5)


def test_localhost_still_allowed():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        cli.connect(srv.getsockname())
    finally:
        cli.close()
        srv.close()


@pytest.mark.e2e
def test_e2e_marker_restores_network():
    assert socket.getaddrinfo.__module__ == "socket"           # hàm thật, không bị thay


def test_e2e_items_skipped_without_flag(request):
    """Không có KCI_E2E=1 → mọi test mang marker e2e bị skip ở bước thu thập (không chạy với mạng mở)."""
    if os.environ.get("KCI_E2E") == "1":
        pytest.skip("đang chạy e2e")
    items = [i for i in request.session.items if i.get_closest_marker("e2e") is not None]
    if not items:
        pytest.skip("không có test e2e nào trong lượt chạy này")
    assert all(any(m.name == "skip" for m in i.iter_markers()) for i in items)


def test_proxy_env_dropped():
    """Proxy ở localhost sẽ ra mạng thay cho test → bị bỏ; requests không lấy proxy nào."""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert k not in os.environ, k
    assert os.environ.get("NO_PROXY") == "*"                      # cả proxy hệ thống (macOS) cũng bị bỏ qua
    assert requests.utils.get_environ_proxies("https://m.stock.naver.com/") == {}


@pytest.mark.e2e
def test_e2e_counts_toward_real_dart_budget():
    """Test e2e gọi DART thật → lượt gọi phải được cộng vào bộ đếm hạn mức thật của máy."""
    import dart_api as D
    assert os.path.realpath(D.BUDGET_PATH) == os.path.realpath(os.path.join(ROOT, ".cache", "dart_budget.json"))


def test_dart_budget_counter_is_isolated(tmp_path):
    import dart_api as D
    repo_budget = os.path.join(ROOT, ".cache", "dart_budget.json")
    before = os.stat(repo_budget).st_mtime_ns if os.path.exists(repo_budget) else None
    assert not os.path.realpath(D.BUDGET_PATH).startswith(os.path.realpath(ROOT) + os.sep)
    assert D.budget_used() == 0                                   # mỗi test bắt đầu từ 0, không đọc số của máy
    for _ in range(40):                                           # ≥ 20 lần → có ghi đĩa
        assert D.budget_take()
    assert os.path.exists(D.BUDGET_PATH)
    after = os.stat(repo_budget).st_mtime_ns if os.path.exists(repo_budget) else None
    assert after == before


def test_dart_caches_point_to_tmp():
    import analysis
    import dart_api as D
    root = os.path.realpath(ROOT) + os.sep
    for p in (D.YEAR_CACHE_DIR, analysis.CACHE, analysis.Q_CACHE):
        assert not os.path.realpath(p).startswith(root), p
    assert D.STATS["errors"] == 0 and D.STATS["open_until"] == 0.0


def test_secrets_and_public_flag_not_inherited():
    for k in ("DART_API_KEY", "OPENDART_API_KEY", "KCI_DART_DAILY_BUDGET", "KCI_OWNER_PASSWORD"):
        assert k not in os.environ, k
    assert os.environ.get("KCI_PUBLIC") == "0"
    import dart_api as D
    assert D.daily_budget() == 0 and not D.available()


def test_fake_market_feeds_pages(fake_market):
    import hotnews as H
    import megatrend as MT
    import screener
    themes, status = H.theme_ranking()
    assert len(MT.trend_table(themes)) == len(MT.MEGATRENDS)
    assert all(r["chg"] is not None for r in MT.trend_table(themes))
    assert [r["code"] for r in MT.market_indices()] == ["KOSPI", "KOSDAQ"]
    assert screener.rf_for_valuation()["live"] is True
