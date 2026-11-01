"""Drive the deployed X checker. OAuth and report state stay on the server."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import time
import urllib.error
import urllib.request

INTERVAL = 5
RUN_SECONDS = 21000  # 5h 50m, below GitHub's six-hour job timeout.
_LAST_ERRORS = {}


def log_health(action, result):
    """Log safe error classifications, never posts, URLs or response bodies."""
    rows = {'response': result, 'poll': result.get('poll') or {}}
    rows.update(result.get('result') or {})
    for stage, row in rows.items():
        if not isinstance(row, dict):
            continue
        key = (action, stage)
        if not row.get('error'):
            _LAST_ERRORS.pop(key, None)
            continue
        def safe(value):
            text = str(value or '')
            return text if re.fullmatch(r'[A-Za-z0-9_]{1,64}', text) else 'redacted'
        state = (safe(row.get('state')), safe(row.get('error')), safe(row.get('error_code')))
        if _LAST_ERRORS.get(key) != state:
            print('X handler:', action, stage, *state, flush=True)
            _LAST_ERRORS[key] = state


def call(url, key, action):
    data = json.dumps({'action': action, 'key': key}).encode()
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=125 if action == 'x_check_work' else 30) as response:
            result = json.load(response)
        log_health(action, result)
        # Do not log report contents, identities, auth headers or response bodies.
        if result.get('poll', {}).get('queued'):
            print('Queued new X checks.', flush=True)
        if result.get('result', {}).get('delivery', {}).get('state') == 'published':
            print('Published an X reply.', flush=True)
        return result
    except urllib.error.HTTPError as exc:
        print('X maintenance HTTP', exc.code, flush=True)
        try:
            log_health(action, json.load(exc))
        except Exception:
            pass
    except Exception as exc:
        print('X maintenance:', type(exc).__name__, flush=True)
    return None


def run(url, key, seconds=RUN_SECONDS):
    deadline = time.monotonic() + seconds if seconds else float('inf')
    # A long analysis must not block the independent five-second mentions loop.
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {'x_check_poll': None, 'x_check_work': None}
        next_at = time.monotonic()
        while time.monotonic() < deadline:
            for action, future in pending.items():
                if future is None or future.done():
                    if future is not None:
                        future.result()
                    pending[action] = pool.submit(call, url, key, action)
            next_at += INTERVAL
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = INTERVAL
            time.sleep(max(0, min(delay, deadline - time.monotonic())))


def continue_workflow(env=None):
    """Queue one successor; workflow concurrency prevents parallel chains."""
    env = os.environ if env is None else env
    token = env.get('GITHUB_TOKEN')
    repo = env.get('GITHUB_REPOSITORY')
    if not token or not repo:
        raise RuntimeError('Continuation requires GitHub Actions credentials.')
    url = 'https://api.github.com/repos/' + repo + '/actions/workflows/x-checks.yml/dispatches'
    data = json.dumps({'ref': env.get('GITHUB_REF_NAME', 'main')}).encode()
    # A failed HTTP response must not expose a credential or response body.
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 204:
                    raise RuntimeError('Unexpected continuation response.')
            print('Queued the next X polling session.', flush=True)
            return
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise RuntimeError('Workflow continuation HTTP ' + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('Workflow continuation connection failed.') from None
        time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=RUN_SECONDS, help='0 runs continuously')
    parser.add_argument('--continue-workflow', action='store_true', help='Queue one successor after a finite run')
    args = parser.parse_args()
    key = os.environ.get('WATCH_CRON_KEY')
    if not key:
        parser.error('Set WATCH_CRON_KEY in the environment.')
    if args.seconds < 0:
        parser.error('--seconds must be nonnegative')
    if args.continue_workflow and (not args.seconds or args.seconds > RUN_SECONDS):
        parser.error('Continuation requires a duration between 1 and 21000 seconds.')
    if args.continue_workflow and not (os.environ.get('GITHUB_TOKEN') and os.environ.get('GITHUB_REPOSITORY')):
        parser.error('Continuation requires GitHub Actions credentials.')
    run(os.environ.get('WATCH_URL', 'https://www.reagents.tech/api/index'), key, args.seconds)
    if args.continue_workflow:
        continue_workflow()


if __name__ == '__main__':
    main()
