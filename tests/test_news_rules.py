"""I36 (rà soát 18/11/2026): hủy / rút / đổ vỡ (해지·철회·취소·무산…) không còn bị chấm là xúc tác tích cực."""
import datetime as dt

import pytest

import growth as GR
import hotnews as H

NOW = dt.datetime(2026, 11, 17, 10, 0)


@pytest.mark.parametrize("name, label_prefix, pol, score", [
    ("단일판매ㆍ공급계약해지", "Hủy: ", -1, 90),
    ("[기재정정]단일판매ㆍ공급계약해지", "Hủy: ", -1, 90),
    ("자기주식취득신탁계약해지결정", "Hủy: ", -1, 70),
    ("기술이전계약해지", "Hủy: ", -1, 85),
    ("공개매수철회", "Hủy: ", -1, 80),
    ("주식소각결정철회", "Hủy: ", -1, 75),
    ("현금ㆍ현물배당결정(취소)", "Hủy: ", -1, 55),
    ("신규시설투자등(철회)", "Hủy: ", -1, 65),
    ("유상증자결정(철회)", "Rút: ", 0, 75),
])
def test_classify_dart_negation(name, label_prefix, pol, score):
    label, p, sc = H.classify_dart(name)
    assert label.startswith(label_prefix) and p == pol and sc == score


def test_classify_dart_regressions():
    assert H.classify_dart("단일판매ㆍ공급계약체결") == ("Hợp đồng bán hàng / cung cấp lớn", 1, 90)
    assert H.classify_dart("주요사항보고서(유상증자결정)")[1] == -1
    # gỡ khỏi diện quản lý / được giao dịch lại là tin TỐT, không phải 'Bị đưa vào diện quản lý'
    assert H.classify_dart("관리종목지정해제")[1] == 1
    assert H.classify_dart("매매거래정지해제")[1] == 1
    assert H.classify_dart("관리종목지정")[1] == -1
    # chiều 0 (trọng yếu) giữ nguyên dù có chữ 철회
    assert H.classify_dart("투자판단관련주요경영사항(임상시험계획철회)")[1] == 0


@pytest.mark.parametrize("title", ["에코프로, 3000억 공급계약 해지", "셀트리온, 美 FDA 허가 신청 철회",
                                   "삼성바이오, 수주 계약 취소", "OO, 합병 무산"])
def test_cancel_titles_are_risk(title):
    r = H.score_news({"title": title, "dt": NOW}, {}, [], {}, NOW)
    assert r["kind"] == "🔴 Rủi ro" and r["pol"] == -1
    assert GR.tag_title(title) == -1


def test_positive_titles_unchanged():
    r = H.score_news({"title": "OO, 3000억 공급계약 체결", "dt": NOW}, {}, [], {}, NOW)
    assert r["kind"] == "🟢 Xúc tác mạnh" and r["pol"] == 1
    assert GR.tag_title("OO, 3000억 공급계약 체결") == 1
    assert GR.tag_title("코스피 공급계약 해지") == 0          # tin thị trường chung vẫn trung tính


def test_news_metrics_counts_cancellation_as_negative():
    items = [{"dt": "20261117090000", "title": "OO, 단일판매 공급계약 해지"}]
    m = GR.news_metrics(items, today=dt.date(2026, 11, 17))
    assert m["pos"] == 0 and m["neg"] == 1


@pytest.mark.parametrize("title", ["대중 수출규제 해제…반도체 수혜", "그린벨트 해제 수혜주 급등",
                                   "공매도 금지 해제 앞두고 외국인 매수", "감산 중단 수혜", "규제 해제 신고가"])
def test_deregulation_titles_stay_positive(title):
    """'해제'/'중단' của một hạn chế là tin TỐT — chỉ phủ định đứng ngay sau danh từ xúc tác mới thành rủi ro."""
    r = H.score_news({"title": title, "dt": NOW}, {}, [], {}, NOW)
    assert r["pol"] == 1 and r["kind"] != "🔴 Rủi ro"
    assert GR.tag_title(title) >= 0          # không còn bị tính là tin xấu (trước sửa: -1)


def test_neutral_halt_title_not_negative():
    assert GR.tag_title("OO 주가 하락 중단") == 0


@pytest.mark.parametrize("title", ["OO, 임상 중단 결정", "OO 기술이전 계약 해제", "OO 상장 철회"])
def test_more_cancel_titles(title):
    assert H.score_news({"title": title, "dt": NOW}, {}, [], {}, NOW)["pol"] == -1
    assert GR.tag_title(title) == -1


def test_cancel_pattern_same_in_both_modules():
    assert H.CANCEL_RE.pattern == GR.CANCEL_RE.pattern
