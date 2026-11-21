"""Restore one verified BTC archive on the backend for historical replay."""
import argparse
import json
from pathlib import Path

from utils.btc_offsite_archive import backend_host, ssh_archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    args = parser.parse_args(argv)
    try:
        host = backend_host(Path('/etc/prism-observability/tunnel.env'))
        if host is None:
            raise ValueError('archive_backend_missing')
        result = json.loads(ssh_archive(host, 0, restore=args.archive).stdout)
        if result.get('status') != 'VERIFIED':
            raise ValueError('archive_restore_unverified')
    except Exception as exc:
        print(json.dumps({'status': 'ERROR', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
