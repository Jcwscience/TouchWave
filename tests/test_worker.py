import time
import pytest
from touchwave.client import WorkerClient
from touchwave.model import ReceiverConfig


def wait_for(client, predicate, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        messages,replies=client.drain()
        result=predicate(messages,replies)
        if result:
            return result
        time.sleep(.03)
    raise AssertionError('Worker timed out')


def rpc(client, op, **args):
    result=[]
    client.send(op, lambda reply: result.append(reply), **args)
    def poll(messages,replies):
        for callback,reply in replies:
            if callback:
                callback(reply)
        return result[0] if result else None
    return wait_for(client,poll)


def test_worker_commands_frames_record_and_recovery(tmp_path):
    client=WorkerClient(tmp_path,no_audio=True)
    try:
        assert rpc(client,'ping')['ok']
        assert rpc(client,'start')['ok']
        frame=wait_for(client,lambda messages,_: messages.get('spectrum'))
        assert len(frame['db'])==2048
        assert rpc(client,'record',kind='audio',duration=2,max_bytes=1024*1024)['ok']
        config=ReceiverConfig().changed(frequency=99000000).to_dict()
        rejected=rpc(client,'configure',config=config)
        assert not rejected['ok'] and 'Stop the recording' in rejected['error']
        assert rpc(client,'record_stop')['ok']
        assert rpc(client,'configure',config=config)['ok']
        assert rpc(client,'stop')['state']['phase']=='idle'
        assert not rpc(client,'configure',config={'frequency':-10})['ok']
        assert rpc(client,'start')['ok']
        assert rpc(client,'stop')['ok']
    finally:
        client.close()
    assert client.process.poll() is not None


def test_decoder_resource_lease_and_events(tmp_path):
    from touchwave.worker import Service
    from touchwave.plugins import DecodedEvent
    class Graph:
        started=False
        stopped=False
        def start(self): self.started=True
        def stop(self): self.stopped=True
        def wait(self): pass
    class Decoder:
        def build(self,selection,config,emit):
            emit(DecodedEvent('test','position',time.time(),{'latitude':1,'longitude':2}))
            self.graph=Graph()
            return self.graph
    service=Service(tmp_path,no_audio=True)
    decoder=Decoder()
    service.decoders['test']=decoder
    service.start_tool('test')
    assert service.resources.owner=='test'
    assert service.status()['decoded_events'][0]['event']['payload']['latitude']==1
    service.stop()
    assert decoder.graph.stopped and service.resources.owner is None


def test_worker_death_is_reported(tmp_path):
    client=WorkerClient(tmp_path,no_audio=True)
    try:
        assert rpc(client,'ping')['ok']
        client.process.kill()
        client.process.wait()
        reply=rpc(client,'ping')
        assert not reply['ok'] and 'stopped responding' in reply['error']
    finally:
        client.close()


def test_iq_replay_preserves_capture_tuning(tmp_path):
    from touchwave.worker import Service
    from touchwave.recording import Recorder
    import numpy as np
    r=Recorder(tmp_path)
    r.start('iq',ReceiverConfig(),duration=1,max_bytes=1024)
    r.write('iq',np.ones(128,dtype=np.complex64))
    service=Service(tmp_path,no_audio=True)
    try:
        service.command(dict(version=1,op='replay',args={'path':r.completed[0]['path']}))
        assert service.status()['replay']
        with pytest.raises(ValueError,match='capture'):
            service.configure(service.config.changed(frequency=90000000))
        service.command(dict(version=1,op='live'))
        assert not service.replay and not service.graph
    finally:
        service.stop()


def test_worker_exits_if_ui_parent_dies(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    code='''
import os,time
from touchwave.client import WorkerClient
c=WorkerClient(os.environ['TW_TEST_RECORDINGS'],no_audio=True)
time.sleep(.7)
print(c.process.pid,flush=True)
os._exit(0)
'''
    import os
    env=dict(os.environ,TW_TEST_RECORDINGS=str(tmp_path))
    parent=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,env=env,timeout=5)
    assert parent.returncode==0,parent.stderr
    worker_pid=int(parent.stdout.strip())
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        stat=Path(f'/proc/{worker_pid}/stat')
        if not stat.exists() or stat.read_text().split()[2]=='Z':
            return
        time.sleep(.05)
    raise AssertionError('Worker survived parent termination')


def test_source_stall_stops_capture_and_releases_device(tmp_path,monkeypatch):
    from touchwave.worker import Service
    from touchwave.model import Phase
    from types import SimpleNamespace
    service=Service(tmp_path,no_audio=True)
    disposed=[]
    service.graph=SimpleNamespace(spectrum=SimpleNamespace(latest=lambda:None),
                                 set_audio=lambda config:None,dispose=lambda:disposed.append(True))
    service.resources.acquire('receiver')
    service.recorder.start('iq',service.config)
    service.started=0
    monkeypatch.setattr('touchwave.worker.time.monotonic',lambda:10)
    assert service.frame() is None
    assert service.phase==Phase.ERROR and service.resources.owner is None
    assert disposed and not service.recorder.active
    assert service.recorder.completed[0]['discontinuities']
