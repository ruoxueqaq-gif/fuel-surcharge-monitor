from datetime import date

from src.monitor import (
    Cycle,
    cycle_for_day,
    is_candidate_day,
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
    <table>
      <tr><th>路線</th><th>日本円</th></tr>
      <tr><td>日本-韓国</td><td>6,500</td></tr>
      <tr><td>日本-北米</td><td>55,000円</td></tr>
    </table>
    """
    records = parse_html_periods(html, "NH", "https://example.test")
    assert len(records) == 1
    assert records[0]["announced_at"] == "2026-10-18"
    assert records[0]["effective_start"] == "2026-11-01"
    assert records[0]["amounts"][1]["amount_jpy"] == 55000


def test_markdown_parser_stops_before_insurance_table():
    markdown = """
    2026年10月18日更新
    ### 2026年11月1日から12月31日発券分まで

    | 区間 | 旅行開始国が日本の場合 |
    | --- | --- |
    | 日本－韓国 | 6,500円 |
    | 日本－北米 | 55,000円 |

    ## 航空保険特別料金
    | ご購入場所 | 適用額 |
    | --- | --- |
    | 日本 | 600円 |
    """
    records = parse_markdown_periods(markdown, "JL", "https://example.test")
    assert len(records) == 1
    assert records[0]["effective_end"] == "2026-12-31"
    assert [row["amount_jpy"] for row in records[0]["amounts"]] == [6500, 55000]
