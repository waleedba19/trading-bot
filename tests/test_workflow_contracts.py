"""Static contracts for every GitHub workflow.

The failures we actually hit were structural, not logic:
  * a scheduled run passed an empty ``inputs.pairs`` to argparse  -> crash
  * a workflow fixed code but committed without ``contents: write`` -> push denied
  * a step referenced a script that did not exist yet
  * a rebase raced another workflow's commit                      -> lost commit

These tests make that class of mistake fail CI before it can reach main.
"""
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
CRON_FIELDS = (5, 6)  # GitHub accepts 5; 6-field forms are rejected upstream

SCRIPT_RE = re.compile(r"(scripts/[A-Za-z0-9_\-./]+\.(?:py|sh))")
BASH_RE = re.compile(r"bash\s+([A-Za-z0-9_\-./]+\.sh)")
PY_FILE_RE = re.compile(r"python3?\s+([A-Za-z0-9_\-]+\.py)")
PY_MODULE_RE = re.compile(r"python3?\s+-m\s+((?:agents|research|sentiment)\.[A-Za-z0-9_.]+)")
# Scripts that write env vars through --github-env (invisible to `echo` parsing).
GITHUB_ENV_WRITERS = {
    "scripts/session_gate.py": {"RUN_NOW", "RUN_REASON"},
    "scripts/check_staleness.py": {"STALE_WORKFLOWS", "STALE_COUNT"},
}
INPUTS_RE = re.compile(r"\$\{\{\s*inputs\.")
ENV_COND_RE = re.compile(r"env\.([A-Z][A-Z0-9_]+)")
FORCE_PUSH_RE = re.compile(r"push\s+--force|push\s+-f\b")
GUARDS = ("if [ -z", "if [", "|| '")


def _on_block(data):
    return data.get("on", data.get(True))


def _jobs(data):
    return (data.get("jobs") or {}).values()


def _run_blocks(job):
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            yield step


class WorkflowContractTest(unittest.TestCase):
    def test_workflows_exist_and_parse(self):
        self.assertGreaterEqual(len(WORKFLOWS), 8, "expected the full workflow set")
        names = set()
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)
                self.assertIn("name", data)
                self.assertTrue(_jobs(data), "no jobs")
                self.assertIsNotNone(_on_block(data), "no on:")
                self.assertNotIn(data["name"], names, "duplicate workflow name")
                names.add(data["name"])

    def test_schedules_are_valid_crons(self):
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            on = _on_block(data) or {}
            if not isinstance(on, dict):
                continue
            for entry in on.get("schedule") or []:
                cron = (entry or {}).get("cron")
                self.assertIsNotNone(cron, f"{path.name}: schedule without cron")
                fields = len(str(cron).split())
                self.assertIn(fields, CRON_FIELDS, f"{path.name}: bad cron {cron!r}")

    def test_referenced_scripts_exist(self):
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in _jobs(data):
                for step in _run_blocks(job):
                    run = step["run"]
                    targets = []
                    targets += SCRIPT_RE.findall(run)
                    targets += BASH_RE.findall(run)
                    targets += PY_FILE_RE.findall(run)
                    for target in targets:
                        self.assertTrue((ROOT / target).is_file(),
                                        f"{path.name}: missing script {target}")
                    for module in PY_MODULE_RE.findall(run):
                        rel = Path(*module.split(".")).with_suffix(".py")
                        self.assertTrue((ROOT / rel).is_file(),
                                        f"{path.name}: missing module file {rel}")

    def test_actions_are_version_pinned(self):
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in _jobs(data):
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and "uses" in step:
                        self.assertIn("@", str(step["uses"]),
                                      f"{path.name}: unpinned action {step['uses']}")

    def test_committers_declare_contents_write(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            commits = ("git commit" in text) or ("ci_commit_push" in text)
            if not commits:
                continue
            data = yaml.safe_load(text)
            perms = data.get("permissions") or {}
            self.assertEqual(perms.get("contents"), "write",
                             f"{path.name}: commits but lacks permissions.contents=write")

    def test_inputs_are_guarded_in_run_scripts(self):
        """Scheduled events have no inputs, so bare ${{ inputs.x }} is a crash."""
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in _jobs(data):
                for step in _run_blocks(job):
                    run = step["run"]
                    if not INPUTS_RE.search(run):
                        continue
                    self.assertTrue(any(guard in run for guard in GUARDS),
                                    f"{path.name}: unguarded inputs usage in step "
                                    f"{step.get('name', '<unnamed>')}")

    def test_env_conditions_are_written_by_a_previous_step(self):
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in _jobs(data):
                written = set()
                for step in job.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    run = step.get("run") or ""
                    for name in re.findall(r"([A-Z][A-Z0-9_]*)=\S*\n?\s*>>\s*\"?\$GITHUB_ENV", run):
                        written.add(name)
                    for name in re.findall(r"echo \"?([A-Z][A-Z0-9_]*)=[^\"]*\"?\s*>>\s*\"?\$GITHUB_ENV", run):
                        written.add(name)
                    for script, names in GITHUB_ENV_WRITERS.items():
                        if script in run and "--github-env" in run:
                            written.update(names)
                    cond = str(step.get("if") or "")
                    for name in ENV_COND_RE.findall(cond):
                        self.assertIn(name, written,
                                      f"{path.name}: step guards on env.{name} before it is written")

    def test_session_workflows_are_gated(self):
        """Ticks fire every 10 min: an ungated step would run the job at every tick."""
        for name in ("deliberate.yml", "warmup.yml", "sentiment.yml"):
            data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
            for job in _jobs(data):
                steps = [s for s in job["steps"] if isinstance(s, dict)]
                gate = next((i for i, s in enumerate(steps) if "scripts/session_gate.py" in str(s.get("run"))), None)
                self.assertIsNotNone(gate, f"{name}: no session_gate step")
                for step in steps[gate + 1:]:
                    if "run" in step:
                        self.assertIn("RUN_NOW", str(step.get("if")), f"{name}: ungated step {step['name']}")

    def test_jobs_have_timeouts(self):
        for path in WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job_name, job in (data.get("jobs") or {}).items():
                self.assertIn("timeout-minutes", job,
                              f"{path.name}: job {job_name} has no timeout-minutes")

    def test_no_force_pushes(self):
        for path in WORKFLOWS:
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(FORCE_PUSH_RE.search(line),
                                  f"{path.name}:{line_no}: force push is forbidden")


if __name__ == "__main__":
    sys.exit(unittest.main())
