"""Build a local status page; never query services or send notifications."""
from __future__ import annotations
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'src'))

def read(path, default=None):
    p = ROOT / path
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else ({} if default is None else default)

def csv_rows(pattern):
    for p in sorted(ROOT.glob(pattern)):
        with p.open(encoding='utf-8-sig', newline='') as handle:
            yield from csv.DictReader(handle)

def build_status(name, now):
    status = {'repository': name, 'page_generated_at': now, 'workflow_outcome': os.getenv('MONITOR_JOB_STATUS', 'not_run_locally'),
        'last_data_check': '', 'last_successful_fetch': '', 'valid_results': None, 'next_check': '参见查询计划', 'notes': []}
    state = read('data/state.json')
    if name == 'flight-price-monitor':
        coverages = [read(str(p.relative_to(ROOT))) for p in ROOT.glob('data/*_coverage.json')]
        rows = [row for c in coverages for row in c.get('routes', [])]
        if rows:
            status['last_data_check'] = max(row.get('checked_at', '') for row in rows)
            status['valid_results'] = sum(row.get('valid', 0) for row in rows)
            status['last_successful_fetch'] = max((row.get('checked_at', '') for row in rows if row.get('status') != 'error'), default='')
            status['source_failures'] = sum(row.get('status') == 'error' for row in rows)
            status['notes'].append('[逐路线报价诊断](akita_2027_coverage.md) · [澳新报价诊断](au_nz_2027_coverage.md)')
        else:
            history = list(csv_rows('csv/03_不常看/run_status.csv'))
            if history:
                last = history[-1]
                status['last_data_check'] = last.get('run_time', '')
                status['valid_results'] = int(last['quotes_valid']) if last.get('quotes_valid','').isdigit() else None
                status['notes'].append('旧记录尚无逐路线诊断；下一次实际查询后生成。')
        from flight_monitor.config import load_profiles
        from flight_monitor.schedule import schedule_slot
        local = datetime.fromisoformat(now).astimezone(ZoneInfo('Asia/Shanghai'))
        plans = []
        for config in load_profiles(str(ROOT/'config/profiles'), 'all'):
            if not config.enabled or config.booked:
                continue
            completed = state.get('profiles', {}).get(config.profile, {}).get('last_successful_slot')
            for day in range(400):
                candidates = [(local + timedelta(days=day)).replace(hour=h, minute=23, second=0, microsecond=0) for h in [15,21]]
                due = next((dt for dt in candidates if dt > local and schedule_slot(config, dt) not in {None, completed}), None)
                if due:
                    plans.append(config.profile + '：' + due.isoformat())
                    break
        status['next_check'] = '；'.join(plans) or '配置期内无后续计划查询'
    elif name == 'airline-campaign-monitor':
        sources = read('data/source_health.json')
        status['last_data_check'] = sources.get('checked_at', '')
        rows = sources.get('sources', {}).values()
        status['last_successful_fetch'] = max((r.get('last_successful_fetch', '') for r in rows), default='')
        status['source_failures'] = sum(r.get('status') != 'ok' for r in rows) if sources.get('sources') else None
        status['valid_results'] = len(read('data/campaigns.json').get('campaigns', {}))
        status['next_check'] = '北京时间每天约 01:20、09:20、17:20'
    elif name == 'fuel-surcharge-monitor':
        sources = state.get('source_health', {})
        status['last_data_check'] = max((r.get('checked_at', '') for r in sources.values()), default='')
        status['last_successful_fetch'] = max((r.get('last_successful_fetch', '') for r in sources.values()), default='')
        status['source_failures'] = sum(r.get('status') == 'error' for r in sources.values()) if sources else None
        status['valid_results'] = sum(1 for _ in csv_rows('csv/01_重点关注/*.csv'))
        status['next_check'] = '偶数月20、25日及下一奇数月2日；已完成航司自动跳过'
    elif name in {'akita-hotel-monitor','akita-omagari-2027-monitor'}:
        status['last_data_check'] = state.get('last_attempt_check') or state.get('last_effective_check', '')
        status['last_successful_fetch'] = state.get('last_effective_check', '')
        statuses = state.get('last_source_statuses', {})
        status['source_failures'] = sum(value not in {'OK','no-match'} for value in statuses.values()) if statuses else None
        import yaml
        cfg = yaml.safe_load((ROOT/'config.yaml').read_text(encoding='utf-8'))
        now_date = datetime.fromisoformat(now).astimezone(ZoneInfo('Asia/Tokyo')).date()
        if name == 'akita-hotel-monitor':
            from src.monitor import schedule_min_hours
            interval = schedule_min_hours(cfg, now_date)
            if state.get('last_check_failed'): interval = min(interval or 6, 6)
            status['valid_results'] = sum(1 for _ in csv_rows('csv/01_重点关注/my_verified_offers.csv')) if (ROOT/'csv/01_重点关注/my_verified_offers.csv').exists() else None
            status['notes'].append('[公告覆盖与人工查询入口](source_coverage.md)；报价为空不等于无房。')
        else:
            from src.monitor import interval_hours
            interval = interval_hours(cfg, now_date)
            if status['source_failures']: interval = min(interval or 3, 3)
            status['valid_results'] = len(state.get('latest_events', {}))
        if now_date.isoformat() > cfg['event']['monitor_end']:
            status['next_check'] = '计划监控已结束'
        elif interval is not None and status['last_data_check']:
            due = datetime.fromisoformat(status['last_data_check']) + timedelta(hours=interval)
            status['next_check'] = due.isoformat() + ' 起的下一次工作流唤醒（计划时间）'
        else:
            status['next_check'] = '下一次工作流唤醒'
    elif name == 'kobe-attendance-monitor':
        profiles = state.get('profiles', {}).values()
        status['last_data_check'] = state.get('last_run', '')
        status['source_failures'] = sum(item.get('parser_status') != 'ok' for item in profiles)
        status['last_successful_fetch'] = max((ob.get('checked_at', '') for item in profiles
            if item.get('parser_status') == 'ok' for ob in item.get('observations', [])), default='')
        status['valid_results'] = sum(len(item.get('observations', [])) for item in profiles if item.get('parser_status') == 'ok')
        rows = list(csv_rows('csv/03_不常看/my_run_log.csv'))
        if rows:
            status['last_run_record'] = rows[-1]
            status['last_data_check'] = status['last_data_check'] or rows[-1].get('检查时间', '')
        status['notes'].append('[每日运行记录](../csv/03_不常看/my_run_log.csv) · [月度运行记录](../csv/04_月度排班/run_log.csv)')
        status['next_check'] = '北京时间每天约07:17；月度扩展查询按周一及每月1日执行'
    elif name == 'av-new-release-monitor':
        seen = read('data/seen.json')
        status['notes'].append('[个人复核待处理清单](review_queue.md)；季度查询间隔内不运行属于正常调度。')
        status['valid_results'] = sum(1 for _ in csv_rows('reports/review_queue.csv'))
        status['next_check'] = '每年1、4、7、10月1日北京时间11:31；新人任务13:17'
        # Preserve the real metadata; do not turn page-generation time into scan time.
        status['last_data_check'] = seen.get('last_completed_at') or seen.get('last_checked_at') or seen.get('last_run_at') or ''
    queue = read('data/notification_outbox.json')
    status['pending_notifications'] = len(queue.get('pending', {})) if queue else None
    return status

def main():
    name = os.getenv('GITHUB_REPOSITORY', ROOT.name).split('/')[-1]
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    status = build_status(name, now)
    target = ROOT/'reports/health.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    def shown(value): return '尚无可靠记录' if value in (None, '') else str(value)
    lines = [f'# {name} 监控状态', '', f'页面生成时间：{now}', '',
        f'- 最近实际查询：{shown(status["last_data_check"])}',
        f'- 最近成功获取来源数据：{shown(status["last_successful_fetch"])}',
        f'- 记录数量：{shown(status["valid_results"])}（机票为本次有效报价；酒店为已核实报价；促销／花火为当前记录；复核为待处理项目）',
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
