"""Install the personal Linux user's 07:55 Europe/London tax-rule verification timer.

Run from the repository: .venv/bin/python deployment/install_tax_rules_timer.py
"""
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parents[1]
units = Path.home() / '.config/systemd/user'
units.mkdir(parents=True, exist_ok=True)
# Quote systemd paths; double % so it cannot become a systemd specifier.
def quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

(units / 'toms-tax-rules.service').write_text(f'''[Unit]
Description=TOMS official tax-rule check

[Service]
Type=oneshot
WorkingDirectory={str(project).replace('%', '%%')}
ExecStart={quote(project / '.venv/bin/python')} -m flask --app app refresh-tax-rules
TimeoutStartSec=2min
UMask=0077
''')
(units / 'toms-tax-rules.timer').write_text('''[Unit]
Description=Check TOMS tax rules daily at 07:55 Europe/London

[Timer]
OnCalendar=*-*-* 07:55:00 Europe/London
Persistent=true

[Install]
WantedBy=timers.target
''')
subprocess.run(['/usr/bin/systemctl', '--user', 'daemon-reload'], check=True)
subprocess.run(['/usr/bin/systemctl', '--user', 'enable', '--now', 'toms-tax-rules.timer'], check=True)
subprocess.run(['/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'toms-tax-rules.timer'], check=True)
print('Installed TOMS tax-rule check timer. Check: systemctl --user list-timers toms-tax-rules.timer')
