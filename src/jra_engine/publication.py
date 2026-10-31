"""Offline publication planning and LINE text drafts. Never sends messages."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from .store import digest

JST=timezone(timedelta(hours=9))


def instant(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('Timezone required')
    return t


def plan(race,morning='08:30'):
    start=instant(race['start_at']).astimezone(JST)
    hour,minute=map(int,morning.split(':'))
    first=start.replace(hour=hour,minute=minute,second=0,microsecond=0)
    times=[('morning',first),('t60',start-timedelta(hours=1)),('t10',start-timedelta(minutes=10))]
    if not first<times[1][1]:raise ValueError('Morning overlaps prestart updates; configure an earlier time')
    return [dict(edition_id=race['race_id']+':edition:'+stage,stage=stage,
                 scheduled_at=t.isoformat(),race_id=race['race_id'],start_at=start.isoformat()) for stage,t in times]


def draft(race,edition,forecast, pdf, url,at=None):
    """Accept a real edition forecast, not an old canonical forecast relabeled as new."""
    current=instant(at or datetime.now(timezone.utc).isoformat())
    start=instant(race['start_at']);scheduled=instant(edition['scheduled_at'])
    if current>=start:raise ValueError('Race started; prediction delivery suppressed')
    if abs((current-scheduled).total_seconds())>300:raise ValueError('Outside publication window')
    if edition not in plan(race):raise ValueError('Unknown edition plan')
    if forecast['id']!=edition['edition_id'] or forecast['race']['race_id']!=race['race_id']:
        raise ValueError('A distinct edition forecast is required; canonical freezes cannot be relabeled')
    asof,created=instant(forecast['as_of']),instant(forecast['created_at'])
    if not scheduled-timedelta(minutes=5)<=asof<=created<=current:
        raise ValueError('Stale/future edition forecast')
    link=urlparse(url)
    if link.scheme!='https' or not link.hostname or link.hostname in ('localhost','127.0.0.1','::1') or link.username or link.password:
        raise ValueError('External HTTPS PDF URL required')
    raw=Path(pdf).read_bytes()
    if not raw.startswith(b'%PDF-'):raise ValueError('PDF missing/invalid')
    labels={'morning':'朝版','t60':'60分前版','t10':'10分前版'}
    body=f"{race['name']}｜{labels[edition['stage']]}\n情報時点：{forecast['as_of']}\nPDFを見る：{url}\n未校正モデルによる参考情報。"
    return dict(id=digest([edition,digest(forecast),hashlib.sha256(raw).hexdigest(),url]),
        edition=edition,forecast_hash=digest(forecast),pdf_sha256=hashlib.sha256(raw).hexdigest(),
        created_at=current.isoformat(),delivery_status='draft_not_sent',
        access_status='hosting_and_paid_access_not_verified',
        messages=[dict(type='text',text=body)])


def save_draft(packet,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(packet['id']+'.json')
    payload=json.dumps(packet,ensure_ascii=False,sort_keys=True,indent=2)
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf-8'))
        # Repeated preparation time is not a new delivery or a reason to overwrite.
        if {k:v for k,v in saved.items() if k!='created_at'}!={k:v for k,v in packet.items() if k!='created_at'}:
            raise ValueError('Conflicting draft')
        return path
    with path.open('x',encoding='utf-8') as f:f.write(payload)
    return path
