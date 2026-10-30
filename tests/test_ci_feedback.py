"""Exercise report paths without invoking pip, the network or real race data."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('ci_feedback', Path(__file__).resolve().parents[1] / 'scripts/ci_feedback.py')
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)


@pytest.mark.parametrize('exit_code,xml,expected,blocker', [
    (0, '<testsuite><testcase classname="suite" name="ok"/></testsuite>', 'success', None),
    (1, '<testsuite><testcase classname="suite" name="broken"><failure/></testcase></testsuite>', 'failure', 'test_failures_or_errors'),
    (5, '<testsuite/>', 'failure', 'zero_tests'),
    (0, '<testsuite/>', 'failure', 'zero_tests'),
    (2, 'invalid XML', 'failure', 'junit_missing_or_invalid'),
])
def test_test_results(tmp_path, monkeypatch, exit_code, xml, expected, blocker):
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_SHA', 'abc123')
    feedback.initialize(tmp_path)
    commands = []

    def fake_execute(command, log, timeout):
        commands.append(command)
        log.write_text('offline test log', encoding='utf-8')
        if 'pytest' in command:
            (tmp_path / 'pytest.xml').write_text(xml, encoding='utf-8')
            return exit_code
        return 0

    monkeypatch.setattr(feedback, 'execute', fake_execute)
    assert feedback.run(tmp_path) == (0 if expected == 'success' else 1)
    report = json.loads((tmp_path / 'feedback-latest.json').read_text(encoding='utf-8'))
    assert report['status'] == expected
    assert report['run_id'] == '123' and report['commit_sha'] == 'abc123'
    assert report['started_at_utc'] <= report['finished_at_utc']
    assert report['real_race_data_used'] is False
    assert commands[0][-4:] == ['pip', 'install', '-e', '.[test]']
    if blocker:
        assert blocker in report['blockers']
    if report['failed']:
        assert report['failed_tests'] == ['suite::broken']


def test_install_failure_never_runs_pytest(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(feedback, 'execute', lambda command, *_: commands.append(command) or 1)
    assert feedback.run(tmp_path) == 1
    report = json.loads((tmp_path / 'feedback-latest.json').read_text())
    assert len(commands) == 1 and report['blockers'] == ['dependency_install_failed']
    assert report['test_count'] == 0 and report['finished_at_utc']


def test_setup_failure_is_reported(tmp_path):
    initial = feedback.initialize(tmp_path)
    feedback.finalize(tmp_path, 'failure', 'skipped')
    report = json.loads((tmp_path / 'feedback-latest.json').read_text())
    assert report['status'] == 'failure' and 'python_setup_failure' in report['blockers']
    assert report['started_at_utc'] == initial['started_at_utc']


def test_timeout_preserves_log(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(['pytest'], 1, output=b'partial output')
    monkeypatch.setattr(subprocess, 'run', timeout)
    path = tmp_path / 'pytest.log'
    assert feedback.execute(['pytest'], path, 1) == 124
    assert 'partial output' in path.read_text() and 'time limit' in path.read_text()
