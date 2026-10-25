"""Pure presentation helpers: no queries, notifications or changes to source tables."""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo


def day(value):
    try:
        text = str(value or '').strip()
        return date.fromisoformat(text[:10]) if len(text) >= 10 else None
    except ValueError:
        return None


def number(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def read_table(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def table(headers, rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(key, '') for key in headers] if isinstance(row, dict) else row)
    return stream.getvalue().encode('utf-8-sig')


def run(build, root):
    parser = argparse.ArgumentParser(description='仅从已有数据更新重点标记，不抓取官网、不发送通知')
    parser.add_argument('--as-of', type=date.fromisoformat)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    today = args.as_of or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    changed = []
    for relative, content in build(root, today).items():
        path = root / relative
        if not path.exists() or path.read_bytes() != content:
            changed.append(relative)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    print(f'Focus views: {len(changed)} changed; as of {today.isoformat()}')
    return 1 if args.check and changed else 0
