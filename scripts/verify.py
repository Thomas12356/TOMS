"""Run checks using the application interpreter and separate development tools."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--browser', nargs='+', default=[], help='all, or browser script stems')
    parser.add_argument('--skip-backend', action='store_true')
    parser.add_argument('--skip-static', action='store_true')
    args = parser.parse_args()
    env = os.environ.copy()
    env['RUN_POSTGRES_TESTS'] = '1' if args.postgres else '0'
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(ROOT / '.venv-tools'), env.get('PYTHONPATH', ''))))
    native = ROOT / '.venv-tools/native/root/usr/lib/x86_64-linux-gnu'
    if native.is_dir():
        env['LD_LIBRARY_PATH'] = os.pathsep.join(filter(None, (str(native), env.get('LD_LIBRARY_PATH', ''))))
    scripts = sorted((ROOT / 'tests').glob('*browser_checks.py')) if args.browser == ['all'] else []
    for name in args.browser if args.browser != ['all'] else []:
        path = ROOT / 'tests' / f'{name}.py'
        if not name.endswith('browser_checks') or path.parent != ROOT / 'tests' or not path.is_file():
            parser.error(f'Unknown browser check: {name}')
        scripts.append(path)
    if scripts and not args.postgres:
        parser.error('Browser workflows require --postgres for their isolated fixtures.')
    commands = []
    if not args.skip_backend:
        commands.append([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'])
    commands.extend([sys.executable, str(path)] for path in scripts)
    if not args.skip_static:
        commands.extend([
            [sys.executable, '-m', 'ruff', 'check', '--select', 'F', 'app.py', 'models.py', 'routes', 'services', 'scripts'],
            [sys.executable, '-m', 'bandit', '-q', '-r', 'app.py', 'models.py', 'routes', 'services'],
        ])
    commands.append(['git', 'diff', '--check'])
    for command in commands:
        print('Running: ' + ' '.join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
