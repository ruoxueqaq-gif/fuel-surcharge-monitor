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
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILES = {code: ROOT / "data" / f"{code}.csv" for code in ("NH", "JL")}
STATE_FILE = ROOT / "data" / "state.json"
REPORT_FILE = ROOT / "reports" / "latest.md"
CHANGE_FILE = ROOT / "runtime" / "change.md"

SOURCES = {
    "NH": "https://www.ana.co.jp/ja/jp/guide/plan/charge/fuelsurcharge/",
    "JL": "https://www.jal.co.jp/jp/ja/inter/fare/fuel/detail.html",
}

AIRLINE_NAMES = {"NH": "ANA（全日空）", "JL": "JAL（日本航空）"}
DATE_RANGE_JA = re.compile(
    r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日(?:から|～|〜|-|－)"
    r"\s*(?:(20\d{2})年\s*)?(\d{1,2})月\s*(\d{1,2})日"
)
UPDATED_JA = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日更新")


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
    announced = iso_date(updated_match.groups()) if updated_match else None
    records: list[dict] = []

    for heading in soup.find_all(["h2", "h3"]):
        heading_text = clean(heading.get_text(" ", strip=True))
        period = DATE_RANGE_JA.search(heading_text)
        if not period:
            continue
        table = heading.find_next("table")
        if not isinstance(table, Tag):
            continue
        rows: list[dict] = []
        for tr in table.find_all("tr"):
            cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if len(cells) < 2 or not re.search(r"\d", cells[-1]):
                continue
            amount_match = re.search(r"([\d,]+)", cells[-1])
            if amount_match:
                rows.append({"route": cells[0], "amount_jpy": int(amount_match.group(1).replace(",", ""))})
        if rows:
            effective_start, effective_end = range_dates(period)
            records.append(
                make_record(
                    airline=airline,
                    announced_at=announced,
                    effective_start=effective_start,
                    effective_end=effective_end,
                    amounts=rows,
                    source_url=source_url,
                )
            )
    return records


def parse_markdown_periods(markdown: str, airline: str, source_url: str) -> list[dict]:
    updated_match = UPDATED_JA.search(markdown)
    announced = iso_date(updated_match.groups()) if updated_match else None
    matches = list(DATE_RANGE_JA.finditer(markdown))
    records: list[dict] = []
    for index, period in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        section = markdown[period.end() : end]
        rows: list[dict] = []
        in_route_table = False
        for line in section.splitlines():
            if not line.strip().startswith("|"):
                if in_route_table:
                    break
                continue
            if not in_route_table:
                if not any(word in line for word in ("区間", "路線", "Route")):
                    continue
                in_route_table = True
            cells = [clean(cell) for cell in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set("".join(cells)) <= {"-", ":", " "}:
                continue
            amount_match = re.fullmatch(r"(?:JPY\s*)?([\d,]+)\s*(?:円|Yen)?", cells[-1], re.I)
            if amount_match and any(word in cells[0] for word in ("日本", "Japan")):
                rows.append({"route": cells[0], "amount_jpy": int(amount_match.group(1).replace(",", ""))})
        if rows:
            effective_start, effective_end = range_dates(period)
            records.append(
                make_record(
                    airline=airline,
                    announced_at=announced,
                    effective_start=effective_start,
                    effective_end=effective_end,
                    amounts=rows,
                    source_url=source_url,
                )
            )
    return records


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
        "currency": "JPY",
        "unit": "每位旅客、每航段、单程",
        "amounts": amounts,
        "source_url": source_url,
    }


def load_history() -> list[dict]:
    records: list[dict] = []
    for airline, path in HISTORY_FILES.items():
        if not path.exists() or path.stat().st_size == 0:
            continue
        grouped: dict[str, dict] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                record_id = row["record_id"]
                record = grouped.setdefault(
                    record_id,
                    {
                        "id": record_id,
                        "airline": airline,
                        "airline_name": AIRLINE_NAMES[airline],
                        "announced_at": row["announced_at"] or None,
                        "effective_start": row["effective_start"],
                        "effective_end": row["effective_end"],
                        "currency": row["currency"],
                        "unit": row["unit"],
                        "amounts": [],
                        "source_url": row["source_url"],
                    },
                )
                record["amounts"].append(
                    {"route": row["route"], "amount_jpy": int(row["amount_jpy"])}
                )
        records.extend(grouped.values())
    return records


def append_history(records: list[dict]) -> None:
    if not records:
        return
    fieldnames = [
        "record_id", "announced_at", "effective_start", "effective_end",
        "route", "amount_jpy", "currency", "unit", "source_url",
    ]
    for airline, path in HISTORY_FILES.items():
        selected = [record for record in records if record["airline"] == airline]
        if not selected:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            if needs_header:
                writer.writeheader()
            for record in selected:
                for amount in record["amounts"]:
                    writer.writerow(
                        {
                            "record_id": record["id"],
                            "announced_at": record["announced_at"] or "",
                            "effective_start": record["effective_start"],
                            "effective_end": record["effective_end"],
                            "route": amount["route"],
                            "amount_jpy": amount["amount_jpy"],
                            "currency": record["currency"],
                            "unit": record["unit"],
                            "source_url": record["source_url"],
                        }
                    )


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"schema_version": 1, "cycles": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_report(history: list[dict]) -> str:
    lines = ["# NH / JL 航空燃油附加费", "", "历史记录采用追加方式保存；下表显示每家航司已记录的最近一期。", ""]
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
                "| 航线 | 日元 |",
                "|---|---:|",
            ]
        )
        for row in latest["amounts"]:
            lines.append(f"| {row['route'].replace('|', '/')} | ¥{row['amount_jpy']:,} |")
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
                f"记录数：{len(record['amounts'])} 条航线价格",
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
                raise RuntimeError("未能从页面识别任何日元燃油附加费表格")
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
