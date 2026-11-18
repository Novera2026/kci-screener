"""Mạng giả ở tầng HTTP cho test G9b (rà soát 18/11/2026: I42, I43, C3, C4).

Thay requests.Session.request (requests.get, Session.get… đều đi qua đây) bằng bộ định tuyến trả phản hồi THẬT đã
ghi sẵn trong tests/fixtures/g9b (Naver 18/11/2026, DART fnlttSinglAcntAll năm 2025 — không chứa khóa). Nhờ vậy các
parser gốc (_fetch_naver, financials._parse, fetch_weekly_closes, _industry_ranked, fetch_statements…) chạy thật trong
test, và mỗi lượt gọi được đếm (test ngân sách hiệu năng).

- Mã không có trong fixture (đối thủ trong nước) → dùng bản ghi của mã mẫu cùng ngành, đổi mã/tên (đánh dấu 'synth').
- URL không có tuyến → HTTP 404 và ghi vào `misses`.
- DART: có fixture → status 000; không có → status 013 ('không có báo cáo').
"""
from __future__ import annotations

import json
import os
import re
import threading
from urllib.parse import parse_qs, urlparse

import requests

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "g9b")
NAVER = os.path.join(FIX, "naver")
TICKERS = ["005930", "005935", "241560", "000660", "003490", "030200", "105560", "207940", "330590", "003550",
           "011170", "462870"]


def _load(name):
    with open(os.path.join(NAVER, name), encoding="utf-8") as f:
        return json.load(f)


def _bank_codes() -> set:
    return {str(s.get("itemCode")).zfill(6) for s in _load("industry_301.json").get("stocks") or []}


class FakeNet:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []        # (host, path) theo thứ tự gọi
        self.misses: list[str] = []
        self.dart: list[dict] = []                    # tham số mỗi lượt DART (không có khóa)
        self.lock = threading.Lock()
        self.banks = _bank_codes()

    # ------------------------------------------------------------------ đếm
    def count(self, host_part: str = "") -> int:
        return sum(1 for h, _ in self.calls if host_part in h)

    def reset(self):
        with self.lock:
            self.calls.clear(); self.misses.clear(); self.dart.clear()

    # ------------------------------------------------------------------ định tuyến
    def _template(self, tk: str) -> str:
        if tk in TICKERS:
            return tk
        return "105560" if tk in self.banks else "000660"

    def _stock(self, tk: str, what: str):
        src = self._template(tk)
        if what == "integration":
            j = _load(f"integration_{src}.json")
            if src != tk:
                j = dict(j, itemCode=tk, stockName=f"동종{tk}", industryCompareInfo=[])
            return j
        if what == "basic":
            j = _load(f"integration_{src}.json")
            return {"stockName": j.get("stockName") if src == tk else f"동종{tk}", "itemCode": tk}
        return _load(f"finance_{what}_{src}.json")

    def route(self, method: str, url: str, params: dict):
        u = urlparse(url)
        host, path = u.netloc, u.path
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        q.update({k: str(v) for k, v in (params or {}).items()})
        with self.lock:
            self.calls.append((host, path))
        if host == "opendart.fss.or.kr":
            q.pop("crtfc_key", None)
            with self.lock:
                self.dart.append(dict(q, path=path))
            fn = os.path.join(FIX, "dart", f"fnltt_{q.get('corp_code')}_{q.get('bsns_year')}_{q.get('reprt_code')}_"
                                           f"{q.get('fs_div')}.json")
            if path.endswith("fnlttSinglAcntAll.json") and os.path.exists(fn):
                with open(fn, encoding="utf-8") as f:
                    return 200, f.read()
            return 200, json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."})
        if host == "m.stock.naver.com":
            m = re.match(r"^/api/stock/([0-9A-Z]{6})/(integration|basic|finance/annual|finance/quarter)$", path)
            if m:
                return 200, self._stock(m.group(1), m.group(2).replace("finance/", ""))
            if path == "/api/stocks/industry":
                return 200, _load("industry_groups.json")
            m = re.match(r"^/api/stocks/industry/(\d+)$", path)
            if m:
                fn = f"industry_{m.group(1)}.json"
                if os.path.exists(os.path.join(NAVER, fn)):
                    return 200, _load(fn) if q.get("page", "1") == "1" else {"stocks": [], "totalCount": 0}
                return 200, {"stocks": [], "totalCount": 0, "groupInfo": {}}
            m = re.match(r"^/api/stocks/marketValue/(KOSPI|KOSDAQ)$", path)
            if m:
                if m.group(1) == "KOSPI" and q.get("page", "1") == "1":
                    return 200, _load("market_value_KOSPI.json")
                return 200, {"stocks": [], "totalCount": 0}
            m = re.match(r"^/api/index/(KOSPI|KOSDAQ)/basic$", path)
            if m:
                return 200, dict(_load("index_KOSPI.json"), itemCode=m.group(1))
        if host == "api.finance.naver.com" and path == "/siseJson.naver":
            sym = q.get("symbol", "")
            fn = os.path.join(NAVER, f"sise_week_{sym if sym in TICKERS + ['KOSPI'] else self._template(sym)}.txt")
            with open(fn, encoding="utf-8") as f:
                return 200, f.read()
        if host == "polling.finance.naver.com":
            m = re.match(r"^/api/realtime/domestic/stock/(.+)$", path)
            if m:
                want = m.group(1).split(",")
                datas = {d["itemCode"]: d for d in _load("realtime.json")["datas"]}
                out = []
                for t in want:
                    d = datas.get(t) or (dict(datas[self._template(t)], itemCode=t) if self._template(t) in datas
                                         else None)
                    if d:
                        out.append(d)
                return 200, {"datas": out}
        if host == "api.stock.naver.com":
            if path == "/marketindex/bond/KR10YT=RR":
                return 200, _load("bond_KR10YT.json")
            if path == "/stock/TSM/basic":
                return 200, _load("world_basic_TSM.json")
            if path == "/stock/TSM/finance/annual":
                return 200, _load("world_annual_TSM.json")
        with self.lock:
            self.misses.append(f"{host}{path}")
        return 404, {"code": "NOT_FOUND"}

    # ------------------------------------------------------------------ requests
    def request(self, method, url, params=None, **kw):
        status, body = self.route(method, url, params if isinstance(params, dict) else {})
        r = requests.models.Response()
        r.status_code = status
        r._content = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
        r.encoding = "utf-8"
        r.url = url
        r.headers["Content-Type"] = "application/json; charset=utf-8"
        r.reason = "OK" if status == 200 else "Not Found"
        return r


def install(monkeypatch) -> FakeNet:
    """Thay requests.Session.request bằng FakeNet (mọi requests.get / Session.get đi qua). Trả FakeNet để đếm lượt."""
    net = FakeNet()
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda self, method, url, params=None, **kw: net.request(method, url, params, **kw))
    return net


def freeze(monkeypatch, when):
    """Giả đồng hồ guard (KST) — `when` là datetime có múi giờ hoặc date (khi đó 15:40 KST)."""
    import datetime as _dt

    import guard as GD
    if not isinstance(when, _dt.datetime):
        when = _dt.datetime(when.year, when.month, when.day, 15, 40, tzinfo=GD.KST)
    monkeypatch.setattr(GD, "_now_utc", lambda: when.astimezone(_dt.timezone.utc))


def isolate_analysis_cache(monkeypatch, full_dir: str, q_dir: str):
    """analysis.fetch_full / fetch_quarter_reports giữ cache_dir mặc định = đường dẫn LÚC IMPORT (tham số mặc định),
    nên thay analysis.CACHE / Q_CACHE (conftest) không đủ — đổi luôn tham số mặc định để test không ghi vào .cache."""
    import analysis as AN
    for fn, new in ((AN.fetch_full, full_dir), (AN.fetch_quarter_reports, q_dir)):
        d = fn.__defaults__
        names = fn.__code__.co_varnames[fn.__code__.co_argcount - len(d):fn.__code__.co_argcount]
        monkeypatch.setattr(fn, "__defaults__", tuple(new if n == "cache_dir" else v for n, v in zip(names, d)))


def dart_on(monkeypatch, tmp_path, year_fixtures: bool = False, q_fixtures: bool = False):
    """DART 'có khóa' (khóa giả, không phải hex) qua FakeNet: danh mục corp_code mẫu, không tải corpCode.xml, không
    giãn nhịp; mọi cache DART trong tmp_path. year_fixtures / q_fixtures: chép BCTC năm / quý DART đã lưu (cache của
    app, đã đọc sẵn — không chứa khóa) của các mã fixture vào cache tmp."""
    import shutil

    import analysis as AN
    import dart_api as D
    monkeypatch.setenv("DART_API_KEY", "test-not-a-key")
    monkeypatch.setattr(D, "_corp_map", None)
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    monkeypatch.setattr(D, "_CORP_CACHE", os.path.join(FIX, "dart", "corp_sample.csv"))
    monkeypatch.setattr(D, "_try_download", lambda: {})
    monkeypatch.setattr(D, "_throttle", lambda: None)
    isolate_analysis_cache(monkeypatch, AN.CACHE, AN.Q_CACHE)          # conftest đã trỏ CACHE/Q_CACHE vào tmp_path
    if year_fixtures:
        shutil.copytree(os.path.join(FIX, "dart_year"), D.YEAR_CACHE_DIR, dirs_exist_ok=True)
    if q_fixtures:
        shutil.copytree(os.path.join(FIX, "dart_q"), AN.Q_CACHE, dirs_exist_ok=True)


def offline_pages(monkeypatch, tmp_path, when=None) -> FakeNet:
    """Môi trường mở trang kín: mạng giả, đồng hồ 18/11/2026 15:40 KST, hồ sơ/nhật ký ghi vào tmp_path."""
    import datetime as _dt

    import analysis as AN
    import dossier as DS
    import guard as GD
    net = install(monkeypatch)
    isolate_analysis_cache(monkeypatch, AN.CACHE, AN.Q_CACHE)
    freeze(monkeypatch, when or _dt.datetime(2026, 11, 18, 15, 40, tzinfo=GD.KST))
    base = str(tmp_path / "dossiers")
    for fn in ("save_section", "load", "path_for", "prune"):        # tham số mặc định base=DS.DIR → tmp_path
        f = getattr(DS, fn)
        monkeypatch.setattr(f, "__defaults__", tuple(base if d == DS.DIR else d for d in f.__defaults__))
    return net
