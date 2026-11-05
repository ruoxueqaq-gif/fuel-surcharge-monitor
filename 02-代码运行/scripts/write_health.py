"""Build a local status page; never query services or send notifications."""
from __future__ import annotations

import sys as _layout_sys
from pathlib import Path as _LayoutPath
_layout_sys.path.insert(0, str(_LayoutPath(__file__).resolve().parents[1]))
_layout_sys.path.insert(0, str(_LayoutPath(__file__).resolve().parents[1] / "src"))
from src.layout import project_path

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'src'))

def read(path, default=None):
    p = ROOT / path
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else ({} if default is None else default)

def csv_rows(pattern):
    for p in sorted(project_path(ROOT, "csv").glob(pattern.removeprefix("csv/"))):
        with p.open(encoding='utf-8-sig', newline='') as handle:
            yield from csv.DictReader(handle)

def build_status(name, now):
    status = {'repository': name, 'page_generated_at': now, 'workflow_outcome': os.getenv('MONITOR_JOB_STATUS', 'not_run_locally'),
        'last_data_check': '', 'last_successful_fetch': '', 'valid_results': None, 'next_check': '参见查询计划', 'notes': []}
    state = read('data/state.json')
    sources = state.get('source_health', {})
    status['last_data_check'] = max((r.get('checked_at', '') for r in sources.values()), default='')
    status['last_successful_fetch'] = max((r.get('last_successful_fetch', '') for r in sources.values()), default='')
    status['source_failures'] = sum(r.get('status') == 'error' for r in sources.values()) if sources else None
    status['valid_results'] = sum(1 for _ in csv_rows('csv/01_重点关注/*.csv'))
    status['next_check'] = '偶数月20日起及下一奇数月每天北京时间08:20重试；已完成航司自动跳过'
    queue = read('data/notification_outbox.json')
    status['pending_notifications'] = len(queue.get('pending', {})) if queue else None
    return status

def main():
    name = os.getenv('GITHUB_REPOSITORY', ROOT.parent.name if ROOT.name == '02-代码运行' else ROOT.name).split('/')[-1]
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    status = build_status(name, now)
    target = ROOT/'reports/health.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    def shown(value): return '尚无可靠记录' if value in (None, '') else str(value)
    lines = [f'# {name} 监控状态', '', f'页面生成时间：{now}', '',
        f'- 最近实际查询：{shown(status["last_data_check"])}',
        f'- 最近成功获取来源数据：{shown(status["last_successful_fetch"])}',
        f'- 记录数量：{shown(status["valid_results"])}（燃油附加费历史记录）',
        f'- 来源失败数：{shown(status.get("source_failures"))}',
        f'- 待发送通知：{shown(status["pending_notifications"])}',
        f'- 下次查询计划：{status["next_check"]}',
        f'- 本次工作流状态：{dict(success="成功", failure="失败", cancelled="已取消", not_run_locally="根据已有记录生成，未执行查询").get(status["workflow_outcome"], status["workflow_outcome"])}', '',
        '工作流执行、来源请求成功和有效结果是不同状态。页面生成时间不代表完成了实际查询；跳过查询时保留上次来源时间。', '',
        f'[Actions运行记录](https://github.com/ruoxueqaq-gif/{name}/actions) · [查询配置与说明](../README.md)', '']
    lines.extend(status['notes'])
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    data = ROOT/'data/health.json'
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

if __name__ == '__main__': main()
