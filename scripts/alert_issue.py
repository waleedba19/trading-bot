#!/usr/bin/env python3
"""Failure alert: open (or comment on) one GitHub issue per failing workflow, close it when the workflow is green again.

Called by .github/workflows/alert.yml with the workflow name and its conclusion. Pure decision function `decide` is unit-tested;
`main` only shells out to `gh`.
    python scripts/alert_issue.py "<workflow name>" <conclusion> <run url>
"""
import json
import os
import subprocess
import sys

LABEL = "workflow-failure"
FAILED = ("failure", "timed_out")


def title(name):
    return f"Workflow failed: {name}"


def decide(open_titles_to_numbers, name, conclusion):
    """-> ('create'|'comment'|'close'|'none', issue number or None)."""
    number = open_titles_to_numbers.get(title(name))
    if conclusion in FAILED:   # a timeout is a failure too
        return ("comment", number) if number else ("create", None)
    if conclusion == "success" and number:
        return ("close", number)
    return ("none", None)


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout


def main(argv):
    name, conclusion, url = argv[1], argv[2], argv[3] if len(argv) > 3 else ""
    branch = argv[4] if len(argv) > 4 else "main"
    if branch not in ("main", ""):   # only the default branch opens or closes alerts: a green PR run must not close a red main
        print(f"alert: {name} {conclusion} on branch {branch} -> none")
        return 0
    repo = os.environ.get("GH_REPO") or os.environ["GITHUB_REPOSITORY"]  # a fork's `gh` defaults to the PARENT repo: always name it
    # REST, not `gh issue list --label`: that goes through the search index, which lags and caused duplicate issues
    issues = json.loads(gh("api", f"repos/{repo}/issues?state=open&labels={LABEL}&per_page=100", "--jq", "[.[]|select(.pull_request|not)|{number,title}]"))
    action, number = decide({i["title"]: i["number"] for i in issues}, name, conclusion)
    if action == "create":
        subprocess.run(["gh", "label", "create", LABEL, "-R", repo, "--color", "B60205", "--description", "automatic: a scheduled workflow failed"],
                       capture_output=True, text=True)
        gh("issue", "create", "-R", repo, "--title", title(name), "--label", LABEL,
           "--body", f"`{name}` failed.\n\nRun: {url}\n\nThis issue closes itself when the next run of `{name}` succeeds.")
    elif action == "comment":
        gh("issue", "comment", "-R", repo, str(number), "--body", f"Failed again: {url}")
    elif action == "close":
        gh("issue", "close", "-R", repo, str(number), "--comment", f"Green again: {url}")
    print(f"alert: {name} {conclusion} -> {action} {number or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
