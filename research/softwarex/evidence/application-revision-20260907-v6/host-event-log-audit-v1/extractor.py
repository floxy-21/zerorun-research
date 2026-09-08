"""Extract only timestamp/state/heartbeat events from one retained VBox log."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import argparse,hashlib,json,re

EXPECTED='e8a87fcbed089355f1c14fd6fa76610b80ef1f9b37c0c26ddfa49efd31fbf616'
STAMP=re.compile(r'^(\d+):(\d\d):(\d\d)\.(\d+) ')

def sha(raw):return hashlib.sha256(raw).hexdigest()
def relative(line):
 m=STAMP.match(line)
 if not m:return None
 return Decimal(int(m[1])*3600+int(m[2])*60+int(m[3]))+Decimal('0.'+m[4])
def utc(value):return value.isoformat(timespec='microseconds').replace('+00:00','Z')
def record(path,label):
 raw=path.read_bytes();return {'path':label,'bytes':len(raw),'sha256':sha(raw)}

def audit(raw_path,guest,out):
 raw=raw_path.read_bytes();assert len(raw)==147412 and sha(raw)==EXPECTED
 lines=raw.decode('utf-8').splitlines()
 anchors=[(i,line) for i,line in enumerate(lines,1) if ' Log opened ' in line]
 assert len(anchors)==1
 anchor_lineno,anchor_line=anchors[0]
 wall_text=anchor_line.split(' Log opened ',1)[1]
 wall=datetime.fromisoformat(wall_text.replace('Z','+00:00'))
 anchor_relative=relative(anchor_line);assert anchor_relative is not None
 def mapped(value):return wall+timedelta(microseconds=int((value-anchor_relative)*1000000))
 start_raw=(guest/'main-full.started.json').read_bytes();end_raw=(guest/'main-full.json').read_bytes()
 start=json.loads(start_raw)['started_utc'];end=json.loads(end_raw)['completed_utc']
 lo,hi=datetime.fromisoformat(start),datetime.fromisoformat(end);assert lo<hi
 states=[];during=[];later=[];last=None
 for number,line in enumerate(lines,1):
  value=relative(line)
  if value is None:continue
  last=value
  kind=None
  if "Changing the VM state from '" in line:kind='internal_vm_state'
  elif 'Console: Machine state changed to ' in line:kind='console_machine_state'
  elif 'VMMDev: vmmDevHeartbeatFlatlinedTimer:' in line:kind='heartbeat_lapse'
  elif 'VMMDev: GuestHeartBeat: Guest is alive' in line:kind='heartbeat_resumed'
  elif 'TM: Giving up catch-up attempt' in line:kind='timer_catchup_abandoned'
  if kind is None:continue
  event={'source_line':number,'relative_seconds':str(value),'approximate_host_utc':utc(mapped(value)),
         'kind':kind,'verbatim_line':line,'source_line_utf8_sha256':sha(line.encode('utf-8'))}
  if kind in ('internal_vm_state','console_machine_state'):states.append(event)
  if lo<=mapped(value)<=hi:during.append(event)
  elif mapped(value)>hi:later.append(event)
 assert last is not None
 pause=[row for row in states if any(word in row['verbatim_line'].upper() for word in ['PAUSED','SUSPENDING','SUSPENDED','RESUMING'])]
 first_later=next(row for row in later if row['kind']=='timer_catchup_abandoned')
 selected={row['source_line']:row for row in [*states,*during,first_later]}
 selected[anchor_lineno]={'source_line':anchor_lineno,'relative_seconds':str(anchor_relative),'approximate_host_utc':utc(wall),'kind':'log_open_anchor','verbatim_line':anchor_line,'source_line_utf8_sha256':sha(anchor_line.encode())}
 selected=[selected[n] for n in sorted(selected)]
 assert len(during)==2 and {row['kind'] for row in during}=={'heartbeat_lapse','heartbeat_resumed'}
 assert not pause
 out.mkdir(exist_ok=False)
 summary={'schema':'zerorun.v6-retained-vbox-event-audit.v1','source_log':{'name':raw_path.name,'bytes':len(raw),'sha256':EXPECTED,'total_lines':len(lines),'full_log_published':False},
  'log_open_utc_literal':wall_text,'log_open_relative_seconds':str(anchor_relative),'last_relative_seconds':str(last),
  'approximate_last_host_utc':utc(mapped(last)),'guest_campaign':{'started_utc':start,'completed_utc':end,'source_records':[record(guest/'main-full.started.json','provenance/main-full.started.json'),record(guest/'main-full.json','provenance/main-full.json')]},
  'clock_mapping':'Approximate host UTC = Log opened UTC plus (event relative timestamp minus Log opened relative timestamp); precision truncated to microseconds. Comparing this with guest UTC assumes clock comparability, not independently certified by the missing sampled monitor.',
  'association_basis':'Operator-identified retained VirtualBox log; its nominal wall-clock interval covers the recovered guest campaign. No recovered host-sampling bridge or independently verified guest/host clock offset is available.',
  'all_logged_vm_state_events':states,'explicit_pause_transition_count_in_entire_retained_log':len(pause),
  'events_inside_nominal_guest_campaign_interval':during,'heartbeat_lapse_ns':5528525578,
  'first_later_timer_catchup_event':first_later,'selected_public_lines':selected,
  'original_230_sample_monitor_recovered':False,'sampled_continuity_checks_passed':False,
  'quiet_host_certified':False,'uninterrupted_timing_certified':False,'missing_samples_reconstructed':False,
  'scope':'The retained log records no explicit VM pause transition, but it reports a 5.528525578-second heartbeat lapse within the nominal V6 interval. This is a limited event-log observation, not continuous/sampled performance certification. Later timer catch-up failures are outside that nominal interval. No cause is assigned to workload timing variations.'}
 (out/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n',encoding='utf-8')
 excerpt=['# Public-safe excerpt; full retained log SHA-256 '+EXPECTED,
          '# Only log-open, VM state, in-interval heartbeat, and first later timer-catchup lines selected.',
          '# Approximate UTC mapping is conditional; original line text is retained after the final separator.']
 excerpt += [f"line {row['source_line']} | {row['approximate_host_utc']} | {row['verbatim_line']}" for row in selected]
 (out/'excerpt.log').write_text('\n'.join(excerpt)+'\n',encoding='utf-8')
 (out/'extractor.py').write_bytes(Path(__file__).read_bytes())
 (out/'SOURCE_MANIFEST.json').write_text(json.dumps({'files':[record(path,path.name) for path in sorted(out.iterdir()) if path.is_file()]},indent=2)+'\n',encoding='utf-8')
 print(json.dumps({k:summary[k] for k in ['source_log','explicit_pause_transition_count_in_entire_retained_log','heartbeat_lapse_ns','original_230_sample_monitor_recovered','uninterrupted_timing_certified']},indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,required=True);p.add_argument('--guest-provenance',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();audit(a.log,a.guest_provenance,a.output)
