import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import urllib.error

import pytest


spec = importlib.util.spec_from_file_location('x_checks_runner', Path(__file__).resolve().parents[1] / 'tools/x_checks_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_slow_analysis_does_not_block_five_second_polling(monkeypatch):
    calls = []
    mutex = threading.Lock()
    monkeypatch.setattr(runner, 'INTERVAL', .01)

    def call(url, key, action):
        with mutex:
            calls.append(action)
        if action == 'x_check_work':
            time.sleep(.09)

    monkeypatch.setattr(runner, 'call', call)
    runner.run('https://example.test', 'test-key', seconds=.07)
    assert calls.count('x_check_poll') >= 3
    assert calls.count('x_check_work') == 1


def test_authenticated_request_and_logs_exclude_private_response(monkeypatch, capsys):
    class Reply(io.BytesIO):
        pass

    def urlopen(request, timeout):
        assert json.loads(request.data) == {'action': 'x_check_poll', 'key': 'private-key'}
        assert timeout == 30
        return Reply(json.dumps({'poll': {'queued': 1}, 'private': 'never-log-this'}).encode())

    monkeypatch.setattr(runner.urllib.request, 'urlopen', urlopen)
    assert runner.call('https://example.test', 'private-key', 'x_check_poll')['poll']['queued'] == 1
    output = capsys.readouterr().out
    assert 'Queued' in output and 'private-key' not in output and 'never-log-this' not in output


def test_network_errors_are_redacted_and_loop_can_continue(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise urllib.error.URLError('private-key-and-payload')

    monkeypatch.setattr(runner.urllib.request, 'urlopen', fail)
    assert runner.call('https://example.test', 'private-key', 'x_check_work') is None
    assert 'private-key' not in capsys.readouterr().out


def test_continuation_queues_one_normal_session_with_no_duration_override(monkeypatch, capsys):
    calls = []

    class Ack:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def send(request, timeout):
        calls.append(request)
        assert request.full_url == 'https://api.github.com/repos/owner/project/actions/workflows/x-checks.yml/dispatches'
        assert json.loads(request.data) == {'ref': 'main'}
        assert request.get_header('Authorization') == 'Bearer test-token'
        return Ack()

    monkeypatch.setattr(runner.urllib.request, 'urlopen', send)
    runner.continue_workflow({'GITHUB_TOKEN': 'test-token', 'GITHUB_REPOSITORY': 'owner/project'})
    assert len(calls) == 1
    assert 'test-token' not in capsys.readouterr().out


def test_continuation_recovers_transient_failure_with_bounded_retries(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise urllib.error.URLError('private-credential')

    monkeypatch.setattr(runner.urllib.request, 'urlopen', fail)
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='connection failed') as exc:
        runner.continue_workflow({'GITHUB_TOKEN': 'test-token', 'GITHUB_REPOSITORY': 'owner/project'})
    assert len(attempts) == 3
    assert 'private-credential' not in str(exc.value)


def test_continuation_does_not_retry_permission_errors(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise urllib.error.HTTPError('https://api.github.com', 403, 'private-response', {}, None)

    monkeypatch.setattr(runner.urllib.request, 'urlopen', fail)
    with pytest.raises(RuntimeError, match='HTTP 403') as exc:
        runner.continue_workflow({'GITHUB_TOKEN': 'test-token', 'GITHUB_REPOSITORY': 'owner/project'})
    assert len(attempts) == 1
    assert 'private-response' not in str(exc.value)
