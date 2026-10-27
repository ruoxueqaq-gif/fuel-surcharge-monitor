
import sys as _layout_sys
from pathlib import Path as _LayoutPath
_layout_sys.path.insert(0, str(_LayoutPath(__file__).resolve().parents[1]))
_layout_sys.path.insert(0, str(_LayoutPath(__file__).resolve().parents[1] / "src"))
from src.layout import project_path

import csv
from datetime import date
from io import StringIO
from pathlib import Path
import importlib.util
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('focus_views',ROOT/'scripts/update_focus.py')
focus=importlib.util.module_from_spec(spec)
spec.loader.exec_module(focus)
TODAY=date(2026,10,25)


def test_build_is_repeatable_and_does_not_write_source_files():
    files=[p for p in ROOT.rglob('*') if p.is_file() and p.suffix in {'.csv','.json','.yaml'}]
    before={p:p.read_bytes() for p in files}
    first=focus.build(ROOT,TODAY)
    assert first == focus.build(ROOT,TODAY)
    assert first
    assert all(data.startswith(b'\xef\xbb\xbf') for data in first.values())
    assert before == {p:p.read_bytes() for p in files}


def test_effective_boundaries_next_period_and_revision_deduplication():
    def row(start,end,recorded,amount='1'):
        return {'适用开始日期':start,'适用结束日期':end,'记录时间':recorded,'单程燃油附加费':amount}
    rows=[row('2026-09-01','2026-09-30','2026-08-20'),row('2026-10-01','2026-10-25','2026-09-20'),
          row('2026-11-01','2026-12-31','2026-10-20','2'),row('2026-11-01','2026-12-31','2026-10-25','3'),
          row('2027-01-01','2027-02-28','2026-10-25')]
    output=focus.mark_rows(rows,TODAY)
    assert len(output)==4
    assert output[1]['重点标记']=='🟦 下一期'
    assert output[1]['单程燃油附加费']=='3'
    assert output[2]['重点标记']=='🟨 当前生效'
    assert output[0]['重点标记']==output[3]['重点标记']==''
    assert not any(row['重点标记']=='🟨 当前生效' for row in focus.mark_rows(rows,date(2026,10,26)))


def test_monitor_appends_to_archive_and_focus_rebuild_does_not_recreate_duplicates(tmp_path, monkeypatch):
    from src import monitor
    assert all(path.parent.name == '03_不常看' for path in monitor.HISTORY_FILES.values())
    archives = {code: project_path(tmp_path, 'csv/03_不常看') / f'{code}.csv' for code in ('NH','JL')}
    monkeypatch.setattr(monitor,'HISTORY_FILES',archives)

    def record(code, identity, price):
        return {'airline':code,'id':identity,'announced_at':'2026-10-20',
                'effective_start':'2026-11-01','effective_end':'2026-12-31','currency':'CNY',
                'amounts':[{'route':monitor.MONITORED_ROUTE,'one_way_amount_cny':price,'round_trip_amount_cny':price*2}]}

    monitor.append_history([record('NH','first',300),record('JL','jal',400)])
    monitor.append_history([record('NH','revision',350)])
    for _ in range(2):
        for relative, content in focus.build(tmp_path,TODAY).items():
            path=tmp_path/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
    focused=project_path(tmp_path, 'csv/01_重点关注')
    assert sorted(path.name for path in focused.iterdir()) == ['01_NH.csv','02_JL.csv']
    with (focused/'01_NH.csv').open(encoding='utf-8-sig',newline='') as handle:
        rows=list(csv.DictReader(handle))
    assert len(rows)==1 and rows[0]['单程燃油附加费']=='350'
    with archives['NH'].open(encoding='utf-8-sig',newline='') as handle:
        assert len(list(csv.DictReader(handle)))==2


def test_initial_comparison_uses_earliest_period_latest_revision_and_keeps_inputs():
    rows = [
        {'适用开始日期': '2026-09-01', '适用结束日期': '2026-10-31',
         '记录时间': '2026-09-21', '航线': '中国大陆-日本', '币种': 'CNY', '往返燃油附加费': '882'},
        {'适用开始日期': '2026-01-01', '适用结束日期': '2026-03-31',
         '记录时间': '2026-09-21', '航线': '中国大陆-日本', '币种': 'CNY', '往返燃油附加费': '480'},
        {'适用开始日期': '2026-01-01', '适用结束日期': '2026-03-31',
         '记录时间': '2026-10-20', '航线': '中国大陆-日本', '币种': 'CNY', '往返燃油附加费': '490'},
        {'适用开始日期': '2026-04-01', '适用结束日期': '2026-04-30',
         '记录时间': '2026-09-21', '航线': '中国大陆-日本', '币种': 'CNY', '往返燃油附加费': '450.5'},
    ]
    before = [dict(row) for row in rows]
    output = focus.mark_rows(rows, TODAY)
    assert [row['往返较最初涨跌'] for row in output] == ['+392', '-39.5', '0']
    assert rows == before


def test_initial_comparison_separates_routes_currencies_and_handles_missing_amounts():
    def row(start, amount, route='中国大陆-日本', currency='CNY', end='2026-12-31'):
        return {'适用开始日期': start, '适用结束日期': end,
                '记录时间': '2026-10-20', '航线': route, '币种': currency, '往返燃油附加费': amount}

    rows = [
        row('2026-01-01', '0'), row('2026-04-01', '60'), row('2026-05-01', ''),
        row('2026-01-01', '100', route='其他航线'), row('2026-04-01', '80', route='其他航线'),
        row('2026-01-01', '1000', currency='JPY'), row('2026-04-01', '1200', currency='JPY'),
        row('2026-01-01', '', route='基准缺失'), row('2026-04-01', '300', route='基准缺失'),
        row('2026-01-01', 'NaN', route='基准无效'), row('2026-04-01', '300', route='基准无效'),
        row('2025-01-01', '999', end='2024-12-31'), row('日期未知', '999'),
    ]
    output = focus.mark_rows(rows, TODAY)
    amounts = {(r['航线'], r['币种'], r['适用开始日期']): r['往返较最初涨跌'] for r in output}
    assert amounts[('中国大陆-日本', 'CNY', '2026-01-01')] == '0'
    assert amounts[('中国大陆-日本', 'CNY', '2026-04-01')] == '+60'
    assert amounts[('中国大陆-日本', 'CNY', '2026-05-01')] == ''
    assert amounts[('其他航线', 'CNY', '2026-04-01')] == '-20'
    assert amounts[('中国大陆-日本', 'JPY', '2026-04-01')] == '+200'
    assert amounts[('基准缺失', 'CNY', '2026-04-01')] == ''
    assert amounts[('基准无效', 'CNY', '2026-04-01')] == ''
    assert amounts[('中国大陆-日本', 'CNY', '2025-01-01')] == ''
    assert amounts[('中国大陆-日本', 'CNY', '日期未知')] == ''


def test_build_includes_initial_comparison_for_each_airline(tmp_path):
    expected = {'NH': ('490', '882', '+392'), 'JL': ('578', '1050', '+472')}
    headers = ['记录时间', '适用开始日期', '适用结束日期', '航线', '往返燃油附加费', '币种']
    for airline, (baseline, amount, _) in expected.items():
        path = project_path(tmp_path, f'csv/03_不常看/{airline}.csv')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(focus.table(headers, [
            ['2026-09-21', '2026-01-01', '2026-03-31', '中国大陆-日本', baseline, 'CNY'],
            ['2026-09-21', '2026-09-01', '2026-10-31', '中国大陆-日本', amount, 'CNY'],
        ]))
    output = focus.build(tmp_path, TODAY)
    for airline, (_, amount, difference) in expected.items():
        data = next(content for path, content in output.items() if path.endswith(f'_{airline}.csv'))
        reader = csv.DictReader(StringIO(data.decode('utf-8-sig')))
        assert reader.fieldnames[-1] == '往返较最初涨跌'
        rows = list(reader)
        assert rows[-1]['往返较最初涨跌'] == '0'
        current = [row for row in rows if row['重点标记'] == '🟨 当前生效']
        assert current
        assert all(row['往返燃油附加费'] == amount and row['往返较最初涨跌'] == difference
                   for row in current)
