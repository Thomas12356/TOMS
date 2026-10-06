"""Install the personal Linux user's 08:00/20:00 Europe/London sync timer.

Run from the repository: .venv/bin/python deployment/install_sync_timer.py
"""
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parents[1]
units = Path.home() / '.config/systemd/user'
units.mkdir(parents=True, exist_ok=True)
# Quote systemd paths; double % so it cannot become a systemd specifier.
def quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

(units / 'toms-sync.service').write_text(f'''[Unit]
Description=TOMS transaction sync

[Service]
Type=oneshot
WorkingDirectory={str(project).replace('%', '%%')}
ExecStart={quote(project / '.venv/bin/python')} -m flask --app app sync-transactions
TimeoutStartSec=30min
UMask=0077
''')
(units / 'toms-sync.timer').write_text('''[Unit]
Description=Sync TOMS at 08:00 and 20:00 Europe/London

[Timer]
OnCalendar=*-*-* 08:00:00 Europe/London
OnCalendar=*-*-* 20:00:00 Europe/London
Persistent=true

[Install]
WantedBy=timers.target
''')
subprocess.run(['/usr/bin/systemctl', '--user', 'daemon-reload'], check=True)
subprocess.run(['/usr/bin/systemctl', '--user', 'enable', '--now', 'toms-sync.timer'], check=True)
subprocess.run(['/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'toms-sync.timer'], check=True)
print('Installed TOMS sync timer. Check: systemctl --user list-timers toms-sync.timer')
