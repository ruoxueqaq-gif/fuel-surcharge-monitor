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
        return {'适用开始日期':start,'适用结束日期':end,'记录时间（北京时间）':recorded,'单程燃油附加费（人民币）':amount}
    rows=[row('2026-09-01','2026-09-30','2026-08-20'),row('2026-10-01','2026-10-25','2026-09-20'),
          row('2026-11-01','2026-12-31','2026-10-20','2'),row('2026-11-01','2026-12-31','2026-10-25','3'),
          row('2027-01-01','2027-02-28','2026-10-25')]
    output=focus.mark_rows(rows,TODAY)
    assert len(output)==4
    assert output[1]['重点标记']=='🟦 下一期'
    assert output[1]['单程燃油附加费（人民币）']=='3'
    assert output[2]['重点标记']=='🟨 当前生效'
    assert output[0]['重点标记']==output[3]['重点标记']==''
    assert not any(row['重点标记']=='🟨 当前生效' for row in focus.mark_rows(rows,date(2026,10,26)))


def test_monitor_appends_to_archive_and_focus_rebuild_does_not_recreate_duplicates(tmp_path, monkeypatch):
    from src import monitor
    assert all(path.parent.name == '03_不常看' for path in monitor.HISTORY_FILES.values())
    archives = {code: tmp_path / 'csv/03_不常看' / f'{code}.csv' for code in ('NH','JL')}
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
    focused=tmp_path/'csv/01_重点关注'
    assert sorted(path.name for path in focused.iterdir()) == ['01_NH.csv','02_JL.csv']
    with (focused/'01_NH.csv').open(encoding='utf-8-sig',newline='') as handle:
        rows=list(csv.DictReader(handle))
    assert len(rows)==1 and rows[0]['单程燃油附加费（人民币）']=='350'
    with archives['NH'].open(encoding='utf-8-sig',newline='') as handle:
        assert len(list(csv.DictReader(handle)))==2
