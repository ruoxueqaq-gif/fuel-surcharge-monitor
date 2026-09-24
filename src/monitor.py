from __future__ import annotations

import hashlib
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILES = {code: ROOT / "data" / f"{code}.csv" for code in ("NH", "JL")}
STATE_FILE = ROOT / "data" / "state.json"
REPORT_FILE = ROOT / "reports" / "latest.md"
CHANGE_FILE = ROOT / "runtime" / "change.md"

SOURCES = {
    "NH": "https://www.ana.co.jp/ja/jp/guide/plan/charge/fuelsurcharge/",
    "JL": "https://www.jal.co.jp/jp/ja/inter/fare/fuel/detail_overseas.html",
}

AIRLINE_NAMES = {"NH": "ANA（全日空）", "JL": "JAL（日本航空）"}
MONITORED_ROUTE = "中国大陆-日本（中国大陆始发）"
HISTORY_FIELDS = (
    "record_id", "announced_at", "effective_start", "effective_end", "route",
    "one_way_amount_cny", "round_trip_amount_cny", "currency", "change_vs_previous_cny",
)
HISTORY_HEADERS = {
    "record_id": "记录编号",
    "announced_at": "公布日期",
    "effective_start": "适用开始日期",
    "effective_end": "适用结束日期",
    "route": "航线",
    "one_way_amount_cny": "单程燃油附加费（人民币）",
    "round_trip_amount_cny": "往返燃油附加费（人民币）",
    "currency": "币种",
    "change_vs_previous_cny": "较上周期涨跌（人民币）",
}
DATE_RANGE_JA = re.compile(
    r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日(?:から|～|〜|-|－)"
    r"\s*(?:(20\d{2})年\s*)?(\d{1,2})月\s*(\d{1,2})日"
)
UPDATED_JA = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日更新")
MARKDOWN_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}\s+(?P<title>[^\r\n]+)")


@dataclass(frozen=True)
class Cycle:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def target_start(self) -> date:
        if self.month == 12:
            return date(self.year + 1, 1, 1)
        return date(self.year, self.month + 1, 1)


def cycle_for_day(day: date) -> Cycle:
    """Return the announcement cycle for a scheduled or manual run."""
    if day.month % 2 == 0:
        return Cycle(day.year, day.month)
    if day.day <= 2:
        if day.month == 1:
            return Cycle(day.year - 1, 12)
        return Cycle(day.year, day.month - 1)
    if day.month == 12:
        return Cycle(day.year + 1, 2)
    return Cycle(day.year, day.month + 1)


def is_candidate_day(day: date) -> bool:
    return (day.month % 2 == 0 and day.day in (20, 25)) or (
        day.month % 2 == 1 and day.day == 2
    )


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def iso_date(groups: Iterable[str]) -> str:
    y, m, d = (int(value) for value in groups)
    return date(y, m, d).isoformat()


def range_dates(match: re.Match[str]) -> tuple[str, str]:
    start_year, start_month, start_day, end_year, end_month, end_day = match.groups()
    resolved_end_year = int(end_year or start_year)
    if end_year is None and int(end_month) < int(start_month):
        resolved_end_year += 1
    return (
        date(int(start_year), int(start_month), int(start_day)).isoformat(),
        date(resolved_end_year, int(end_month), int(end_day)).isoformat(),
    )


def announced_for_period(updated_at: str | None, effective_start: str) -> str | None:
    """Use a page update as the announcement date only when it precedes the period."""
    if updated_at is None or updated_at > effective_start:
        return None
    return updated_at


def fetch(url: str) -> tuple[str, str]:
    """Fetch an official page, falling back to a text mirror on bot blocking."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; fuel-surcharge-monitor/1.0; +https://github.com/ruoxueqaq-gif/fuel-surcharge-monitor)",
        "Accept-Language": "ja,en;q=0.8",
    }
    errors: list[str] = []
    candidates = [(url, "official"), (f"https://r.jina.ai/http://{url.split('://', 1)[1]}", "mirror")]
    for candidate, mode in candidates:
        try:
            response = requests.get(candidate, headers=headers, timeout=45)
            response.raise_for_status()
            body = response.text
            if "Access Denied" in body or len(body) < 800:
                raise RuntimeError("page returned an access-denied or empty response")
            return body, mode
        except Exception as exc:  # continue to the documented fallback
            errors.append(f"{mode}: {exc}")
    raise RuntimeError("; ".join(errors))


def parse_html_periods(html: str, airline: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = clean(soup.get_text(" ", strip=True))
    updated_match = UPDATED_JA.search(page_text)
    updated_at = iso_date(updated_match.groups()) if updated_match else None
    records: list[dict] = []

    periods: list[tuple[re.Match[str], int, int]] = []
    cursor = 0
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        heading_text = clean(heading.get_text(" ", strip=True))
        period = DATE_RANGE_JA.search(heading_text)
        if period is None:
            continue
        start = page_text.find(heading_text, cursor)
        if start == -1:
            continue
        cursor = start + len(heading_text)
        periods.append((period, start, cursor))

    for index, (period, _, section_start) in enumerate(periods):
        end = periods[index + 1][1] if index + 1 < len(periods) else len(page_text)
        amount = extract_mainland_china_amount(page_text[section_start:end], airline)
        if amount is not None:
            effective_start, effective_end = range_dates(period)
            records.append(
                make_record(
                    airline=airline,
                    announced_at=announced_for_period(updated_at, effective_start),
                    effective_start=effective_start,
                    effective_end=effective_end,
                    amounts=mainland_china_amounts(amount),
                    source_url=source_url,
                )
            )
    return records


def parse_markdown_periods(markdown: str, airline: str, source_url: str) -> list[dict]:
    updated_match = UPDATED_JA.search(markdown)
    updated_at = iso_date(updated_match.groups()) if updated_match else None
    periods: list[tuple[re.Match[str], int, int]] = []
    for heading in MARKDOWN_HEADING.finditer(markdown):
        period = DATE_RANGE_JA.search(heading.group("title"))
        if period is not None:
            periods.append((period, heading.start(), heading.end()))
    records: list[dict] = []
    for index, (period, _, section_start) in enumerate(periods):
        end = periods[index + 1][1] if index + 1 < len(periods) else len(markdown)
        section = markdown[section_start:end]
        amount = extract_mainland_china_amount(section, airline)
        if amount is not None:
            effective_start, effective_end = range_dates(period)
            records.append(
                make_record(
                    airline=airline,
                    announced_at=announced_for_period(updated_at, effective_start),
                    effective_start=effective_start,
                    effective_end=effective_end,
                    amounts=mainland_china_amounts(amount),
                    source_url=source_url,
                )
            )
    return records


def extract_mainland_china_amount(text: str, airline: str) -> int | None:
    """Extract the airline's CNY surcharge for itineraries originating in Mainland China."""
    if airline == "NH":
        route_match = re.search(
            r"(?:中国大陸発日本行き旅程|Mainland China.*?Japan).*?"
            r"(?:CNY\s*([\d,]+)|([\d,]+)\s*(?:中国元|Chinese yuan))",
            text,
            re.I,
        )
    else:
        route_match = re.search(
            r"(?:東アジア|东亚|East Asia).*?発旅程.*?CNY\s*([\d,]+)",
            text,
            re.I,
        )
    if not route_match:
        return None
    value = next(group for group in route_match.groups() if group is not None)
    return int(value.replace(",", ""))


def mainland_china_amounts(one_way: int) -> list[dict]:
    return [
        {
            "route": MONITORED_ROUTE,
            "one_way_amount_cny": one_way,
            "round_trip_amount_cny": one_way * 2,
        }
    ]


def make_record(
    *, airline: str, announced_at: str | None, effective_start: str,
    effective_end: str, amounts: list[dict], source_url: str
) -> dict:
    identity = json.dumps(
        [airline, effective_start, effective_end, amounts],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "airline": airline,
        "airline_name": AIRLINE_NAMES[airline],
        "announced_at": announced_at,
        "effective_start": effective_start,
        "effective_end": effective_end,
        "currency": "CNY",
        "unit": "每位旅客、每航段、单程",
        "amounts": amounts,
        "source_url": source_url,
    }


def _history_value(row: dict[str, str], key: str) -> str:
    return row.get(HISTORY_HEADERS[key], row.get(key, ""))


def load_history() -> list[dict]:
    records: list[dict] = []
    for airline, path in HISTORY_FILES.items():
        if not path.exists() or path.stat().st_size == 0:
            continue
        grouped: dict[str, dict] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                record_id = _history_value(row, "record_id")
                record = grouped.setdefault(
                    record_id,
                    {
                        "id": record_id,
                        "airline": airline,
                        "airline_name": AIRLINE_NAMES[airline],
                        "announced_at": _history_value(row, "announced_at") or None,
                        "effective_start": _history_value(row, "effective_start"),
                        "effective_end": _history_value(row, "effective_end"),
                        "currency": _history_value(row, "currency") or "CNY",
                        "unit": "每位旅客、每航段、单程",
                        "amounts": [],
                        "source_url": SOURCES[airline],
                    },
                )
                one_way = int(_history_value(row, "one_way_amount_cny"))
                record["amounts"].append(
                    {
                        "route": _history_value(row, "route"),
                        "one_way_amount_cny": one_way,
                        "round_trip_amount_cny": int(_history_value(row, "round_trip_amount_cny")),
                    }
                )
        records.extend(grouped.values())
    return records


def append_history(records: list[dict]) -> None:
    if not records:
        return
    fieldnames = [HISTORY_HEADERS[field] for field in HISTORY_FIELDS]
    for airline, path in HISTORY_FILES.items():
        selected = sorted(
            (record for record in records if record["airline"] == airline),
            key=lambda record: (record["effective_start"], record["id"]),
        )
        if not selected:
            continue
        previous_by_route: dict[str, int] = {}
        if path.exists() and path.stat().st_size:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    route = _history_value(row, "route")
                    amount = _history_value(row, "one_way_amount_cny")
                    if route and amount:
                        previous_by_route[route] = int(amount)
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            if needs_header:
                writer.writeheader()
            for record in selected:
                for amount in record["amounts"]:
                    route = amount["route"]
                    current = int(amount["one_way_amount_cny"])
                    previous = previous_by_route.get(route)
                    change = "" if previous is None or current == previous else f"{current - previous:+d}"
                    output = {
                        "record_id": record["id"],
                        "announced_at": record["announced_at"] or "",
                        "effective_start": record["effective_start"],
                        "effective_end": record["effective_end"],
                        "route": route,
                        "one_way_amount_cny": current,
                        "round_trip_amount_cny": amount["round_trip_amount_cny"],
                        "currency": record["currency"],
                        "change_vs_previous_cny": change,
                    }
                    writer.writerow({HISTORY_HEADERS[key]: value for key, value in output.items()})
                    previous_by_route[route] = current

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"schema_version": 1, "cycles": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_report(history: list[dict]) -> str:
    lines = ["# NH / JL 中国大陆始发-日本燃油附加费", "", "历史记录采用追加方式保存；下表显示每家航司已记录的最近一期。", ""]
    for airline in ("NH", "JL"):
        records = [item for item in history if item["airline"] == airline]
        if not records:
            lines.extend([f"## {AIRLINE_NAMES[airline]}", "", "尚无记录。", ""])
            continue
        latest = max(records, key=lambda item: (item["effective_start"], item["id"]))
        lines.extend(
            [
                f"## {AIRLINE_NAMES[airline]}",
                "",
                f"适用期：{latest['effective_start']} 至 {latest['effective_end']}",
                "",
                "| 航线 | 燃油单程价格 | 燃油往返价格 |",
                "|---|---:|---:|",
            ]
        )
        for row in latest["amounts"]:
            lines.append(
                f"| {row['route'].replace('|', '/')} | "
                f"CNY {row['one_way_amount_cny']:,} | CNY {row['round_trip_amount_cny']:,} |"
            )
        lines.extend(["", f"[官方来源]({latest['source_url']})", ""])
    return "\n".join(lines)


def render_change(records: list[dict], cycle: Cycle, errors: list[str]) -> str:
    lines = [f"# {cycle.key} 轮次发现新的燃油附加费记录", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['airline_name']}",
                "",
                f"适用期：{record['effective_start']} 至 {record['effective_end']}",
                "| 航线 | 燃油单程价格 | 燃油往返价格 |",
                "|---|---:|---:|",
                *[
                    f"| {amount['route']} | CNY {amount['one_way_amount_cny']:,} | "
                    f"CNY {amount['round_trip_amount_cny']:,} |"
                    for amount in record["amounts"]
                ],
                "",
                f"来源：{record['source_url']}",
                "",
            ]
        )
    if errors:
        lines.extend(["## 本轮仍需重试", "", *[f"- {error}" for error in errors], ""])
    return "\n".join(lines)


def main() -> int:
    today = date.fromisoformat(os.environ.get("MONITOR_DATE", date.today().isoformat()))
    force = os.environ.get("FORCE_CHECK", "").lower() in {"1", "true", "yes"}
    if not force and not is_candidate_day(today):
        print(f"{today}: 不是计划检查日，跳过。")
        return 0

    cycle = cycle_for_day(today)
    state = load_state()
    cycle_state = state.setdefault("cycles", {}).setdefault(cycle.key, {"airlines": {}})
    completed = {code for code, value in cycle_state["airlines"].items() if value.get("found")}
    pending = list(SOURCES) if force else [code for code in SOURCES if code not in completed]
    if not pending:
        print(f"{cycle.key}: NH/JL 均已在更早的候选日查到，本次跳过。")
        return 0

    history = load_history()
    known_ids = {item["id"] for item in history}
    new_records: list[dict] = []
    errors: list[str] = []
    for airline in pending:
        try:
            body, fetch_mode = fetch(SOURCES[airline])
            if body.lstrip().startswith(("#", "Title:")):
                found = parse_markdown_periods(body, airline, SOURCES[airline])
            else:
                found = parse_html_periods(body, airline, SOURCES[airline])
            if not found:
                raise RuntimeError("未能从页面识别中国大陆始发行程的人民币燃油附加费")
            found = list({record["id"]: record for record in found}.values())
            unseen = [record for record in found if record["id"] not in known_ids]
            new_records.extend(unseen)
            known_ids.update(record["id"] for record in unseen)
            target = [record for record in found if record["effective_start"] == cycle.target_start.isoformat()]
            if target:
                cycle_state["airlines"][airline] = {
                    "found": True,
                    "found_at": today.isoformat(),
                    "record_id": max(target, key=lambda item: item["effective_end"])["id"],
                    "fetch_mode": fetch_mode,
                }
                print(f"{airline}: 已找到 {cycle.target_start} 起生效的新一期记录。")
            else:
                print(f"{airline}: 官网尚未出现 {cycle.target_start} 起生效的新一期记录。")
        except Exception as exc:
            message = f"{airline}: {exc}"
            errors.append(message)
            print(f"::warning::{message}")

    if new_records:
        new_records.sort(key=lambda item: (item["effective_start"], item["airline"], item["id"]))
        append_history(new_records)
        history.extend(new_records)
    save_state(state)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(render_report(history), encoding="utf-8")

    CHANGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if new_records:
        CHANGE_FILE.write_text(render_change(new_records, cycle, errors), encoding="utf-8")
    elif CHANGE_FILE.exists():
        CHANGE_FILE.unlink()

    print(f"新增 {len(new_records)} 条期次记录；待后续检查：{', '.join(errors) if errors else '无抓取错误'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
