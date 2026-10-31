"""Work back from each post time in races.json and register one-shot Windows scheduled tasks.

python scheduler.py          show the plan only
python scheduler.py apply    register (replaces tasks of the same name) under \\JRA-AI\\
Tasks run jobs.py with the PC on (or wake it from sleep); a PC that is shut down misses them.
"""
import json, subprocess, sys, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

W = Path(os.environ.get('JRA_JOB_WORKDIR', Path(__file__).resolve().parents[2] / 'data' / 'race-jobs'))
PY = Path(sys.executable)
# Future new forecasts only: existing freezes are never replaced.
LOCK_BEFORE = [timedelta(minutes=m) for m in (120, 90, 60)]
RESULT_AFTER = [timedelta(minutes=m) for m in (30, 60, 120, 240)]


def plan(races, at):
    jobs = []
    for r in races:
        start = datetime.fromisoformat(r['start_at'])
        for cmd, times in (('lock', [start - d for d in LOCK_BEFORE]), ('odds', [start-timedelta(minutes=30)]), ('result', [start + d for d in RESULT_AFTER])):
            for t in times:
                if t > at:
                    jobs.append(dict(name=f"{r['target_key']}-{cmd}-{t:%H%M}", cmd=cmd, key=r['target_key'], at=t))
    return sorted(jobs, key=lambda j: j['at'])


def register(job):
    import re
    if job['cmd'] not in ('lock','result','odds') or not re.fullmatch(r'[A-Za-z0-9-]+', job['key']) or not re.fullmatch(r'[A-Za-z0-9-]+', job['name']):
        raise ValueError('Invalid scheduled job')
    at = job['at'].astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S')
    def quote(s):return str(s).replace("'", "''")
    db = Path(os.environ.get('JRA_DATA_DIR', Path(__file__).resolve().parents[2] / 'data' / 'real'))
    arguments = f'-m jra_engine.race_jobs {job["cmd"]} {job["key"]} --workdir "{W}" --data-dir "{db}"'
    ps = ("$svc=New-Object -ComObject Schedule.Service;$svc.Connect();"
          "try{$null=$svc.GetFolder('\\JRA-AI')}catch{$null=$svc.GetFolder('\\').CreateFolder('JRA-AI')};"
          f"$a=New-ScheduledTaskAction -Execute '{quote(PY)}' -Argument '{quote(arguments)}' -WorkingDirectory '{quote(W)}';"
          f"$t=New-ScheduledTaskTrigger -Once -At ([datetimeoffset]'{at}+09:00').LocalDateTime;"
          "$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 20);"
          f"Register-ScheduledTask -TaskPath '\\JRA-AI\\' -TaskName '{job['name']}' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True)


def main():
    races = json.loads((W / 'races.json').read_text(encoding='utf-8'))
    mode = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if mode not in ('show', 'apply'):
        raise ValueError('Only show/apply are supported; remove individual owned tasks explicitly')
    for j in plan(races, datetime.now(timezone.utc)):
        print(f"{j['at']:%m/%d %H:%M}  {j['cmd']:6}  {j['key']}")
        if mode == 'apply':
            register(j)


if __name__ == '__main__':
    main()
