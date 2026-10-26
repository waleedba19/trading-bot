#!/usr/bin/env python3
"""
preflight.py - AlphaControl integrity gate (v2).

Checks (all must pass):
  1. Python syntax            (ast.parse - no .pyc side effects)
  2. JSON validity
  3. YAML validity            (needs PyYAML; warns if missing) + workflow structure
  4. Fragile inline Python in GitHub workflows
  5. Merge-conflict markers
  6. Hard-coded secrets       (common key / token patterns)

Usage:
  python preflight.py             check the whole workspace
  python preflight.py --staged    check only files staged in git (pre-commit hook)
"""
import ast
import json
import os
import re
import subprocess
import sys

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None

SKIP_DIRS = {
    ".git", "venv", ".venv", "env", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".vscode", "dist", "build", ".alphacontrol_backup",
}
TEXT_EXT = {
    ".py", ".json", ".yml", ".yaml", ".md", ".toml", ".ini", ".cfg", ".txt",
    ".ps1", ".sh", ".js", ".ts", ".env",
}
MAX_BYTES = 2_000_000

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{32,}"), "API secret key (sk-...)"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key block"),
    (re.compile(r"(?i)(?:api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]"),
     "hard-coded credential"),
]
INLINE_PY = re.compile(r"\bpython3?\s+(?:-c\b|-\s*<<)")
# Step-level keys that are never legitimate environment-variable names.
MISPLACED_KEYS = {"run", "uses", "with", "shell", "working-directory", "if",
                  "continue-on-error", "timeout-minutes"}
CONFLICT = re.compile(r"^(<{7}|>{7})( |$)")

_yaml_missing_reported = False


def is_skipped(path):
    parts = os.path.normpath(path).split(os.sep)
    return any(p in SKIP_DIRS for p in parts)


def staged_files():
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--relative", "--diff-filter=ACM"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return [p for p in out.splitlines() if p and os.path.isfile(p)]


def all_files():
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            yield os.path.normpath(os.path.join(root, name))


def read_text(path):
    if os.path.getsize(path) > MAX_BYTES:
        return None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def check_python(path, errs):
    try:
        with open(path, "rb") as f:
            ast.parse(f.read(), filename=path)
    except SyntaxError as e:
        errs.append(f"{path}:{e.lineno}: Python syntax error: {e.msg}")
    except (OSError, ValueError) as e:
        errs.append(f"{path}: unreadable Python file ({e})")


def check_json(path, text, errs):
    if os.path.basename(path).lower().startswith("tsconfig"):
        return  # tsconfig allows comments
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        errs.append(f"{path}:{e.lineno}: invalid JSON: {e.msg}")


def is_workflow(path):
    norm = path.replace("\\", "/")
    return ".github/workflows/" in norm and norm.lower().endswith((".yml", ".yaml"))


def check_yaml(path, text, errs):
    global _yaml_missing_reported
    if yaml is None:
        # Fail CLOSED: an unvalidated YAML file must never pass the gate silently.
        if not _yaml_missing_reported:
            _yaml_missing_reported = True
            errs.append("PyYAML is not installed, so YAML cannot be validated. "
                        "Fix: python -m pip install pyyaml")
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        errs.append(f"{path}: invalid YAML: {e}")
        return
    if is_workflow(path):
        # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
        has_trigger = isinstance(data, dict) and ("on" in data or True in data)
        has_jobs = isinstance(data, dict) and "jobs" in data
        if not (has_trigger and has_jobs):
            errs.append(f"{path}: workflow must define both 'on:' and 'jobs:'")
        else:
            check_workflow_steps(path, data, errs)


def check_workflow_steps(path, data, errs):
    """Catch indentation bugs that are still *valid YAML*, e.g. `run:` nested under `env:`."""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return
    for jname, job in jobs.items():
        if not isinstance(job, dict):
            continue
        env_blocks = [("job env", job.get("env"))]
        for i, step in enumerate(job.get("steps") or [], 1):
            if not isinstance(step, dict):
                errs.append(f"{path}: job '{jname}', step {i}: step is not a mapping")
                continue
            label = f"job '{jname}', step '{step.get('name', i)}'"
            env_blocks.append((f"{label} env", step.get("env")))
            if "run" in step and "uses" in step:
                errs.append(f"{path}: {label}: has both 'run' and 'uses'")
            elif "run" not in step and "uses" not in step:
                errs.append(f"{path}: {label}: needs either 'run' or 'uses'")
        for where, env in env_blocks:
            if isinstance(env, dict):
                bad = sorted(MISPLACED_KEYS & {str(k) for k in env})
                if bad:
                    errs.append(f"{path}: {where}: {bad} sit under env: (indentation bug - "
                                f"these must be step-level keys)")


def check_workflow_inline_python(path, text, errs):
    for n, line in enumerate(text.splitlines(), 1):
        if INLINE_PY.search(line):
            errs.append(f"{path}:{n}: fragile inline Python in workflow - use a standalone script")


def check_text_hygiene(path, text, errs):
    base = os.path.basename(path)
    for n, line in enumerate(text.splitlines(), 1):
        if CONFLICT.match(line):
            errs.append(f"{path}:{n}: unresolved merge-conflict marker")
        if base == "preflight.py":
            continue
        for pat, label in SECRET_PATTERNS:
            if pat.search(line):
                errs.append(f"{path}:{n}: possible secret ({label})")
                break


def main():
    # Work from the folder this script lives in, so it behaves the same
    # no matter which subdirectory it is launched from.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    staged_mode = "--staged" in sys.argv
    files = staged_files() if staged_mode else None
    if files is None:
        files = list(all_files())
    files = [f for f in files if not is_skipped(f)]

    errs = []
    checked = 0
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        if ext not in TEXT_EXT and os.path.basename(path) not in {".env"}:
            continue
        checked += 1
        if ext == ".py":
            check_python(path, errs)
        try:
            text = read_text(path)
        except OSError as e:
            errs.append(f"{path}: unreadable ({e})")
            continue
        if text is None:
            continue
        if ext == ".json":
            check_json(path, text, errs)
        if ext in {".yml", ".yaml"}:
            check_yaml(path, text, errs)
            if is_workflow(path):
                check_workflow_inline_python(path, text, errs)
        check_text_hygiene(path, text, errs)

    if errs:
        for e in errs:
            print(f"[REASONING DEFECT] {e}")
        print(f"[PRE-FLIGHT FAILED] {len(errs)} problem(s) in {checked} file(s). Self-correction required.")
        sys.exit(1)
    print(f"[PRE-FLIGHT PASSED] {checked} file(s) checked - syntax, schema, and hygiene OK.")
    sys.exit(0)


if __name__ == "__main__":
    main()
