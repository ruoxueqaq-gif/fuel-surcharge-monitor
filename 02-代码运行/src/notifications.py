"""Durable Issue delivery; state is acknowledged only after GitHub confirms it."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    if not path.exists():
        return {"pending": {}, "delivered": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("pending", {})
    state.setdefault("delivered", {})
    return state


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def enqueue(path: Path, title: str, body: str) -> str:
    key = hashlib.sha256(body.encode("utf-8")).hexdigest()
    state = load(path)
    if key not in state["pending"] and key not in state["delivered"]:
        state["pending"][key] = {"title": title, "body": body,
            "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save(path, state)
    return key


def publish(path: Path, repo: str) -> int:
    state = load(path)
    if not state["pending"]:
        return 0
    # Paginate through closed Issues too: a prior run may have created an Issue
    # and crashed before persisting its acknowledgement.
    result = subprocess.run(["gh", "api", "--paginate", "--slurp",
        f"repos/{repo}/issues?state=all&per_page=100"], text=True, capture_output=True, check=True)
    pages = json.loads(result.stdout)
    known = {f"<!-- delivery:{key} -->": issue.get("html_url", "")
        for page in pages for issue in page if "pull_request" not in issue
        for key in state["pending"] if f"<!-- delivery:{key} -->" in (issue.get("body") or "")}
    count = 0
    for key, item in list(state["pending"].items()):
        marker = f"<!-- delivery:{key} -->"
        url = known.get(marker)
        if url is None:
            result = subprocess.run(["gh", "issue", "create", "--repo", repo,
                "--title", item["title"], "--body-file", "-"],
                input=marker + "\n\n" + item["body"], text=True, capture_output=True, check=True)
            url = result.stdout.strip()
            if not url.startswith("https://github.com/"):
                raise RuntimeError("Issue creation returned no confirmation URL")
            count += 1
        state["delivered"][key] = {"url": url, "delivered_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        del state["pending"][key]
        save(path, state)
    return count


def main() -> int:
    print(f"Issues created: {publish(Path('data/notification_outbox.json'), os.environ['GITHUB_REPOSITORY'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
