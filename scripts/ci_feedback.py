"""Run the repository's offline pytest suite and save a small, dependency-free report."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_report(folder, report):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'feedback-latest.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)


def initialize(folder):
    report = dict(schema_version=1, run_id=os.getenv('GITHUB_RUN_ID', 'local'),
                  run_attempt=os.getenv('GITHUB_RUN_ATTEMPT', '1'),
                  commit_sha=os.getenv('GITHUB_SHA', 'local'), event=os.getenv('GITHUB_EVENT_NAME', 'local'),
                  started_at_utc=utc_now(), finished_at_utc=None, status='in_progress',
                  test_count=0, passed=0, failed=0, errors=0, skipped=0,
                  failed_tests=[], summary='Test execution has not completed.', blockers=[],
                  real_race_data_used=False)
    write_report(folder, report)
    return report


def execute(command, log, timeout):
    """Preserve a bounded log on failure and propagate subprocess timeouts as failures."""
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding='utf-8', errors='replace', timeout=timeout, check=False)
        code, output = result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b''
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        code, output = 124, output + '\nCommand exceeded its time limit.\n'
    except OSError as exc:
        code, output = 127, str(exc)
    # No environment dump, credentials, local data directory or database is uploaded.
    compact = output[-65536:]
    log.write_text(compact, encoding='utf-8')
    print(compact, flush=True)
    return code


def junit_counts(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    failed = [c for c in cases if c.find('failure') is not None]
    errors = [c for c in cases if c.find('error') is not None]
    skipped = [c for c in cases if c.find('skipped') is not None]
    return dict(test_count=len(cases), passed=len(cases)-len(failed)-len(errors)-len(skipped),
                failed=len(failed), errors=len(errors), skipped=len(skipped),
                failed_tests=[f"{c.get('classname', '')}::{c.get('name', '')}"[:300]
                              for c in (failed + errors)[:30]])


def run(folder):
    target = folder / 'feedback-latest.json'
    report = json.loads(target.read_text(encoding='utf-8')) if target.exists() else initialize(folder)
    if report['status'] != 'in_progress':
        report = initialize(folder)
    # A new run must never accidentally read a previous run's JUnit report.
    junit = folder / 'pytest.xml'
    junit.unlink(missing_ok=True)
    install_code = execute([sys.executable, '-m', 'pip', 'install', '-e', '.[test]'],
                           folder / 'install.log', 180)
    report['install_exit_code'] = install_code
    if install_code:
        report['blockers'] = ['dependency_install_failed']
        report['summary'] = 'Dependency installation failed; pytest was not run.'
    else:
        code = execute([sys.executable, '-m', 'pytest', '-q', '--tb=short',
                        f'--junitxml={junit}'], folder / 'pytest.log', 300)
        report['pytest_exit_code'] = code
        try:
            report.update(junit_counts(junit))
        except (OSError, ET.ParseError):
            report['blockers'].append('junit_missing_or_invalid')
        if code:
            report['blockers'].append(f'pytest_exit_{code}')
        if report['test_count'] == 0:
            report['blockers'].append('zero_tests')
        if report['failed'] or report['errors']:
            report['blockers'].append('test_failures_or_errors')
        report['summary'] = (f"{report['test_count']} tests: {report['passed']} passed, "
                             f"{report['failed']} failed, {report['errors']} errors, "
                             f"{report['skipped']} skipped.")
    report['status'] = 'failure' if report['blockers'] else 'success'
    report['finished_at_utc'] = utc_now()
    write_report(folder, report)
    return 0 if report['status'] == 'success' else 1


def finalize(folder, setup_outcome, run_outcome):
    target = folder / 'feedback-latest.json'
    report = json.loads(target.read_text(encoding='utf-8')) if target.exists() else initialize(folder)
    if report['status'] == 'in_progress':
        report['status'] = 'failure'
        report['blockers'] = [f'python_setup_{setup_outcome}', f'test_step_{run_outcome}']
        report['summary'] = 'Execution did not finish; inspect the Actions setup/test steps.'
    report['finished_at_utc'] = report['finished_at_utc'] or utc_now()
    write_report(folder, report)
    summary = (f"### Keiba pytest: {report['status']}\n\n"
               f"{report['summary']}\n\nCommit: `{report['commit_sha']}`\n\n"
               f"Blockers: {', '.join(report['blockers']) or 'none'}\n\n"
               'Scope: repository tests only; no real-race data, paid AI API or automatic code changes.\n')
    print(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['init', 'run', 'finalize'])
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--setup-outcome', default='unknown')
    parser.add_argument('--run-outcome', default='unknown')
    args = parser.parse_args()
    if args.mode == 'init':
        initialize(args.output_dir)
    elif args.mode == 'run':
        return run(args.output_dir)
    else:
        finalize(args.output_dir, args.setup_outcome, args.run_outcome)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
