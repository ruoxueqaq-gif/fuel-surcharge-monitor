from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from focus_common import day, number, read_table, table, run


def mark_rows(rows, today):
    # Same period revisions are retained in the source, latest revision shown here.
    latest = {}
    for row in rows:
        key = (row.get('适用开始日期'), row.get('适用结束日期'), row.get('航线'), row.get('币种'))
        previous = latest.get(key)
        if previous is None or row.get('记录时间（北京时间）', '') >= previous.get('记录时间（北京时间）', ''):
            latest[key] = dict(row)
    rows = list(latest.values())
    future = [day(row.get('适用开始日期')) for row in rows
              if day(row.get('适用开始日期')) and day(row.get('适用结束日期'))
              and day(row['适用开始日期']) > today
              and day(row['适用结束日期']) >= day(row['适用开始日期'])]
    next_start = min(future) if future else None
    for row in rows:
        start, end = day(row.get('适用开始日期')), day(row.get('适用结束日期'))
        label = ''
        if start and end and start <= end:
            if start <= today <= end:
                label = '🟨 当前生效'
            elif start == next_start:
                label = '🟦 下一期'
        row['重点标记'] = label
    return sorted(rows, key=lambda row: (row.get('适用开始日期', ''), row.get('记录时间（北京时间）', '')), reverse=True)


def build(root, today):
    result = {}
    for rank, airline in enumerate(('NH', 'JL'), 1):
        headers, rows = read_table(root / f'csv/03_不常看/{airline}.csv')
        result[f'csv/01_重点关注/0{rank}_{airline}.csv'] = table(['重点标记'] + headers, mark_rows(rows, today))
    return result


if __name__ == '__main__':
    raise SystemExit(run(build, ROOT))
