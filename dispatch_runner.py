"""Run an existing Original job on ephemeral public Windows without publishing chart data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

API = "https://api.github.com"
DEFAULT_REPO = "zinevichroman-cpu/lokpa-original-test"


def github_get(path, token):
    req = urllib.request.Request(API + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LOKPA-Public-Runner",
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub API returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def select_runner(issue):
    title = str(issue.get("title", ""))
    if title.startswith("LOKPA: horoscope"):
        return "automation/lokpa_issue_runner.py"
    if title.startswith("LOKPA: event-scan"):
        return "automation/lokpa_event_scan_runner.py"
    raise ValueError("Supported private issue titles: LOKPA: horoscope, LOKPA: event-scan")


def completion_marker(issue, comments):
    title = str(issue.get("title", ""))
    markers = ("LOKPA_EVENT_SCAN_SUMMARY_V1", "LOKPA_STATUS: COMPLETED") if title.startswith("LOKPA: event-scan") else ("LOKPA_STATUS: COMPLETED",)
    return any(any(marker in str(c.get("body") or "") for marker in markers) for c in comments)


def main():
    token = os.environ.get("LOKPA_PRIVATE_REPO_TOKEN", "")
    repo = os.environ.get("LOKPA_PRIVATE_REPO", DEFAULT_REPO)
    source = Path(os.environ.get("PRIVATE_SOURCE_PATH", "private-source")).resolve()
    raw_number = os.environ.get("LOKPA_ISSUE_NUMBER", "")
    force = os.environ.get("LOKPA_FORCE_RERUN", "false").lower() == "true"
    if not token:
        raise RuntimeError("Secret LOKPA_PRIVATE_REPO_TOKEN is missing")
    if repo != DEFAULT_REPO:
        raise RuntimeError("Unexpected private repository; refusing to run")
    if not raw_number.isdigit() or int(raw_number) < 1:
        raise ValueError("issue_number must be a positive integer")

    number = int(raw_number)
    issue = github_get(f"/repos/{repo}/issues/{number}", token)
    if "pull_request" in issue:
        raise ValueError("The supplied number is a pull request, not an issue")
    runner = source / select_runner(issue)
    if len(str(issue.get("body") or "")) > 6000:
        raise ValueError("Issue body exceeds the existing runner's limit")
    if not runner.is_file():
        raise FileNotFoundError("Selected runner missing from private checkout")

    if not force:
        comments = github_get(f"/repos/{repo}/issues/{number}/comments?per_page=100", token)
        if completion_marker(issue, comments):
            raise RuntimeError("Duplicate-run guard: completed result already exists; create a new private issue or explicitly set force_rerun=true")

    event_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", prefix="lokpa-private-", delete=False) as f:
            json.dump({"issue": issue}, f, ensure_ascii=False)
            event_path = Path(f.name)
        child_env = os.environ.copy()
        child_env.update({
            "GITHUB_REPOSITORY": repo,
            "GITHUB_EVENT_PATH": str(event_path),
            "GH_TOKEN": token,
            "PYTHONPATH": str(source / "automation"),
        })
        print(f"Selected private issue #{number}; runner={runner.name}")
        print("Private issue body and raw chart JSON are not printed.")
        result = subprocess.run([sys.executable, str(runner)], cwd=source, env=child_env, check=False)
        if result.returncode:
            print(f"Original runner exited with code {result.returncode}", file=sys.stderr)
            return result.returncode
        print(f"Private issue #{number} processed; result remains in private issue comments.")
        return 0
    finally:
        if event_path:
            event_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PUBLIC_RUNNER_FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        raise SystemExit(1)
