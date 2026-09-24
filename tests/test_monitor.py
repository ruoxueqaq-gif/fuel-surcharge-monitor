import csv
from datetime import date

import src.monitor as monitor
from src.monitor import (
    Cycle,
    append_history,
    cycle_for_day,
    is_candidate_day,
    load_history,
    parse_html_periods,
    parse_markdown_periods,
)


def test_candidate_dates_and_cycles():
    assert is_candidate_day(date(2026, 10, 20))
    assert is_candidate_day(date(2026, 10, 25))
    assert is_candidate_day(date(2026, 11, 2))
    assert not is_candidate_day(date(2026, 10, 21))
    assert cycle_for_day(date(2026, 10, 20)) == Cycle(2026, 10)
    assert cycle_for_day(date(2026, 11, 2)) == Cycle(2026, 10)
    assert cycle_for_day(date(2027, 1, 2)) == Cycle(2026, 12)


def test_html_parser_extracts_period_and_amounts():
    html = """
    <p>2026年10月18日更新</p>
    <h3>運賃額 2026年11月1日から2026年12月31日ご購入分まで</h3>
    <h4>旅行開始国が日本以外の場合</h4>
    <p>中国大陸発日本行き旅程の中国大陸線はご購入地点にかかわらず以下のとおりです。</p>
    <p>（2026年11月1日以降発券分）441中国元</p>
    """
    records = parse_html_periods(html, "NH", "https://example.test")
    assert len(records) == 1
    assert records[0]["announced_at"] == "2026-10-18"
    assert records[0]["effective_start"] == "2026-11-01"
    assert records[0]["amounts"] == [
        {
            "route": "中国大陆-日本（中国大陆始发）",
            "one_way_amount_cny": 441,
            "round_trip_amount_cny": 882,
        }
    ]


def test_markdown_parser_extracts_jal_mainland_china_cny_amount():
    markdown = """
    2026年10月18日更新
    ### 2026年11月1日から12月31日発券分まで

    | 区間 | 旅行開始国が日本以外の場合 |
    | --- | --- |
    | 日本－東アジア（韓国を除く） | USD 68*2 |

    *2 東アジア（除くソウル/釜山/済州/台北/高雄/香港/ウランバートル）発旅程はCNY482です。

    ## 航空保険特別料金
    | ご購入場所 | 適用額 |
    | --- | --- |
    | 日本 | 600円 |
    """
    records = parse_markdown_periods(markdown, "JL", "https://example.test")
    assert len(records) == 1
    assert records[0]["effective_end"] == "2026-12-31"
    assert records[0]["amounts"] == [
        {
            "route": "中国大陆-日本（中国大陆始发）",
            "one_way_amount_cny": 482,
            "round_trip_amount_cny": 964,
        }
    ]


def test_parser_ignores_date_ranges_outside_period_headings():
    markdown = """
    2026年9月15日更新
    ### 2026年9月1日から10月31日発券分まで

    東アジア発旅程はCNY525です。
    フィリピン発旅程は2026年9月16日から9月30日発券分までUSD38です。
    """
    records = parse_markdown_periods(markdown, "JL", "https://example.test")
    assert len(records) == 1
    assert records[0]["effective_start"] == "2026-09-01"
    assert records[0]["announced_at"] is None


def test_history_csv_omits_unit_and_source_and_tracks_change(tmp_path, monkeypatch):
    nh = tmp_path / "NH.csv"
    jl = tmp_path / "JL.csv"
    monkeypatch.setattr(monitor, "HISTORY_FILES", {"NH": nh, "JL": jl})

    base = {
        "airline": "NH",
        "airline_name": "ANA（全日空）",
        "announced_at": "2026-01-01",
        "effective_start": "2026-01-01",
        "effective_end": "2026-02-28",
        "currency": "CNY",
        "unit": "每位旅客、每航段、单程",
        "source_url": "https://example.test",
    }
    first = {
        **base,
        "id": "a",
        "amounts": [{"route": "中国大陆-日本（中国大陆始发）", "one_way_amount_cny": 245, "round_trip_amount_cny": 490}],
    }
    same = {
        **base,
        "id": "b",
        "effective_start": "2026-03-01",
        "effective_end": "2026-04-30",
        "amounts": [{"route": "中国大陆-日本（中国大陆始发）", "one_way_amount_cny": 245, "round_trip_amount_cny": 490}],
    }
    higher = {
        **base,
        "id": "c",
        "effective_start": "2026-05-01",
        "effective_end": "2026-06-30",
        "amounts": [{"route": "中国大陆-日本（中国大陆始发）", "one_way_amount_cny": 300, "round_trip_amount_cny": 600}],
    }
    lower = {
        **base,
        "id": "d",
        "effective_start": "2026-07-01",
        "effective_end": "2026-08-31",
        "amounts": [{"route": "中国大陆-日本（中国大陆始发）", "one_way_amount_cny": 280, "round_trip_amount_cny": 560}],
    }

    append_history([first, same, higher, lower])
    with nh.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    assert reader.fieldnames == [
        "记录编号", "公布日期", "适用开始日期", "适用结束日期", "航线",
        "单程燃油附加费（人民币）", "往返燃油附加费（人民币）", "币种", "较上周期涨跌（人民币）",
    ]
    assert [row["较上周期涨跌（人民币）"] for row in rows] == ["", "", "+55", "-20"]
    assert all("_" not in name for name in reader.fieldnames)
    assert "单位" not in reader.fieldnames
    assert "官方来源链接" not in reader.fieldnames

    loaded = load_history()
    assert loaded[0]["unit"] == "每位旅客、每航段、单程"
    assert loaded[0]["source_url"] == monitor.SOURCES["NH"]
