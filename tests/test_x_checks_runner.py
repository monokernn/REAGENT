import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import urllib.error


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
