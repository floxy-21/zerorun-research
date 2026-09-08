import base64
import copy
import hashlib

import pytest

from research.softwarex import quickstart_053_metadata_v2 as q


def streams(raw=b'', returncode=0):
    value={'returncode':returncode,'timed_out':False}
    for name, content in (('stdout',raw),('stderr',b'')):
        value.update({name+'_base64':base64.b64encode(content).decode(),name+'_bytes':len(content),
                      name+'_sha256':hashlib.sha256(content).hexdigest(),name+'_truncated':False})
    return value


def fixture():
    root='/tmp/public-source'
    data=[b'a'*40+b'\n',b'',b'',b'',b'i'*(q.LIMIT+1)]
    commands=[]
    for suffix, raw in zip(q.METADATA_SUFFIXES,data):
        commands.append({'command':['/usr/bin/git','-c','core.fsmonitor=false','-c','core.untrackedCache=false','-C',root,*suffix],
                         'streams':streams(raw,1 if suffix[0]=='symbolic-ref' else 0)})
    snap={'head':'a'*40,'branch':None,'relevant_paths':[]}
    snap.update({name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in zip(('status','diff_from_head','index'),data[2:])})
    snap['identity_sha256']=hashlib.sha256(q.canonical(snap)).hexdigest()
    return {'source_root':root,'commands':commands+copy.deepcopy(commands),'source_before':snap,'source_after':copy.deepcopy(snap)}


def test_complete_git_index_above_old_bound_is_recomputed():
    value=fixture()
    q.validate_source_snapshots(value)
    row=value['commands'][4]['streams']
    with pytest.raises(ValueError,match='hash, length'):
        q.validate_streams(row)
    assert len(q.validate_streams(row,limit=q.GIT_METADATA_LIMIT)['stdout'])==q.LIMIT+1


@pytest.mark.parametrize('mode',['changed-hash','truncated','missing','path','snapshot','unclean','oversize'])
def test_metadata_replay_rejects_tampering_and_incomplete_records(mode):
    value=fixture()
    if mode=='changed-hash':value['commands'][4]['streams']['stdout_sha256']='0'*64
    elif mode=='truncated':value['commands'][4]['streams']['stdout_truncated']=True
    elif mode=='missing':value['commands'].pop()
    elif mode=='path':value['commands'][4]['command'][6]='/tmp/another-source'
    elif mode=='snapshot':value['source_after']['index']['bytes']-=1
    elif mode=='unclean':value['commands'][2]['streams']=streams(b'1 M. N... modified')
    else:value['commands'][4]['streams']=streams(b'x'*(q.GIT_METADATA_LIMIT+1))
    with pytest.raises(ValueError):q.validate_source_snapshots(value)


@pytest.mark.parametrize('command',[
    ['/usr/bin/zerorun','mcp-server'],
    ['/usr/bin/git','-C','/tmp/public-source','ls-files','-s','-z'],
    ['/usr/bin/git','-c','core.fsmonitor=false','-c','core.untrackedCache=false','-C','/tmp/other','ls-files','-s','-z'],
    ['/usr/bin/git','-c','core.fsmonitor=false','-c','core.untrackedCache=false','-C','/tmp/public-source','show','HEAD'],
])
def test_larger_bound_cannot_be_applied_to_other_commands(command):
    assert q.metadata_command(command,'/tmp/public-source') is False


def test_no_arbitrary_stream_limit_override():
    with pytest.raises(ValueError,match='unsupported'):
        q.validate_streams(streams(),limit=100*1024*1024)
