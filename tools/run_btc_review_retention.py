"""Run one verified BTC review-retention batch after a successful backup."""
import argparse
import json
import re

from utils.btc_review_retention import cleanup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup_dir')
    args = parser.parse_args(argv)
    try:
        result = cleanup(args.backup_dir)
    except Exception as exc:
        result = {'status': 'ERROR', 'error_type': type(exc).__name__}
        if isinstance(exc, ValueError) and re.fullmatch(r'retention_[a-z0-9_]{1,80}', str(exc)):
            result['reason'] = str(exc)
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 1 if result.get('status') == 'PARTIAL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
