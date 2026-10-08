"""Install daily backup + disposable restore verification for the personal Linux user."""
from pathlib import Path
import subprocess  # nosec B404 # fixed systemctl commands without a shell.


def quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    project = Path(__file__).resolve().parents[1]
    if not (project / '.env.backup-test').exists():
        raise SystemExit('Configure .env.backup-test and run a successful backup --verify first; see DATA_STORAGE.md.')
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    (units / 'toms-backup.service').write_text(f'''[Unit]
Description=TOMS database backup and restore verification

[Service]
Type=oneshot
WorkingDirectory={quote(project)}
ExecStart={quote(project / '.venv/bin/python')} -m services.database.backups backup --verify --keep-days 30
TimeoutStartSec=2h
UMask=0077
''')
    (units / 'toms-backup.timer').write_text('''[Unit]
Description=Back up and test TOMS daily at 02:00 Europe/London

[Timer]
OnCalendar=*-*-* 02:00:00 Europe/London
Persistent=true

[Install]
WantedBy=timers.target
''')
    subprocess.run(['/usr/bin/systemctl', '--user', 'daemon-reload'], check=True)  # nosec B603 # fixed arguments.
    subprocess.run(['/usr/bin/systemctl', '--user', 'enable', '--now', 'toms-backup.timer'], check=True)  # nosec B603 # fixed arguments.
    print('Installed daily backup timer. Check systemctl --user list-timers toms-backup.timer.')


if __name__ == '__main__':
    main()
