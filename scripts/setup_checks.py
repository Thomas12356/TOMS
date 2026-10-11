"""Install pinned verification tools separately from production dependencies.

--local-linux-libs unpacks Ubuntu 24.04 x86_64 Chromium libraries without sudo.
Other installations can use Playwright's documented system dependencies.
"""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-linux-libs', action='store_true')
    args = parser.parse_args()
    tools = ROOT / '.venv-tools'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', '--target', str(tools),
                    '-r', str(ROOT / 'requirements-dev.txt')], check=True)
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(tools), env.get('PYTHONPATH', ''))))
    subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], env=env, check=True)
    if args.local_linux_libs:
        if sys.platform != 'linux' or platform.machine() != 'x86_64':
            parser.error('--local-linux-libs requires Ubuntu 24.04 x86_64.')
        native = tools / 'native'
        packages = native / 'packages'
        packages.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/apt-get', 'download', 'libnspr4', 'libnss3', 'libasound2t64'], cwd=packages, check=True)
        for package in sorted(packages.glob('*.deb')):
            subprocess.run(['/usr/bin/dpkg-deb', '-x', str(package), str(native / 'root')], check=True)
    print('Tools ready. Run: .venv/bin/python scripts/verify.py --postgres --browser all')


if __name__ == '__main__':
    main()
