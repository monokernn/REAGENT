"""Drive the deployed X checker. OAuth and report state stay on the server."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
import urllib.error
import urllib.request

INTERVAL = 5


def call(url, key, action):
    data = json.dumps({'action': action, 'key': key}).encode()
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=125 if action == 'x_check_work' else 30) as response:
            result = json.load(response)
        # Do not log report contents, identities, auth headers or response bodies.
        if result.get('poll', {}).get('queued'):
            print('Queued new X checks.', flush=True)
        if result.get('result', {}).get('delivery', {}).get('state') == 'published':
            print('Published an X reply.', flush=True)
        return result
    except urllib.error.HTTPError as exc:
        print('X maintenance HTTP', exc.code, flush=True)
    except Exception as exc:
        print('X maintenance:', type(exc).__name__, flush=True)
    return None


def run(url, key, seconds=330):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=330, help='0 runs continuously')
    args = parser.parse_args()
    key = os.environ.get('WATCH_CRON_KEY')
    if not key:
        parser.error('Set WATCH_CRON_KEY in the environment.')
    if args.seconds < 0:
        parser.error('--seconds must be nonnegative')
    run(os.environ.get('WATCH_URL', 'https://www.reagents.tech/api/index'), key, args.seconds)


if __name__ == '__main__':
    main()
