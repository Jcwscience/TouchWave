import json
from pathlib import Path
import numpy as np
import pytest
from touchwave.model import ReceiverConfig, parse_frequency
from touchwave.plugins import ResourceManager
from touchwave.recording import Recorder, replay_info
from touchwave.scanner import Scanner
from touchwave.storage import Store


def test_frequency_units_and_precision():
    assert parse_frequency('100.123456') == 100123456
    assert parse_frequency('145500', 'kHz') == 145500000
    for value in ('NaN', 'Infinity', '-1', 'hello', '0.0000001'):
        with pytest.raises(ValueError):
            parse_frequency(value)


def test_config_validation():
    with pytest.raises(ValueError):
        ReceiverConfig.from_dict({'volume': float('nan')})
    with pytest.raises(ValueError):
        ReceiverConfig().changed(mode='NFM')  # Wrong inherited WFM filter.
    assert ReceiverConfig.from_dict({'future_field': 1}).mode == 'WFM'


def test_bookmark_roundtrip_and_atomic_import(tmp_path):
    a, b = Store(tmp_path/'a'), Store(tmp_path/'b')
    c = ReceiverConfig(frequency=145500000,mode='NFM',bandwidth=12500,step=12500)
    a.add_bookmark('Local', 'ham, local', c)
    path = tmp_path/'bookmarks.json'
    a.export_bookmarks(path)
    assert b.import_bookmarks(path) == 1
    assert b.bookmarks('HAM')[0]['config'] == c.to_dict()
    doc = json.loads(path.read_text())
    doc['bookmarks'].append({'name':'Broken','config':{'frequency':-1}})
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        b.import_bookmarks(path)
    assert len(b.bookmarks()) == 1
    a.save_config(c)
    assert a.load_config() == c
    a.close(); b.close()


def test_scanner_dwell_hold_and_stop():
    s = Scanner()
    s.start(['a','b'], now=0, dwell=1, hold=2)
    assert s.tick(0,-120,-80) == 'a'
    assert s.tick(0.1,-20,-80) is None  # Settling ignores old channel activity.
    assert s.tick(1.1,-120,-80) == 'b'
    assert s.tick(1.5,-30,-80) is None
    assert s.tick(2.2,-120,-80) is None
    assert s.tick(3.6,-120,-80) == 'a'
    s.stop()
    assert s.tick(10,-120,-80) is None


def test_resource_ownership():
    r = ResourceManager()
    r.acquire('receiver')
    with pytest.raises(RuntimeError):
        r.acquire('adsb')
    r.release('adsb')
    assert r.owner == 'receiver'
    r.release('receiver'); r.acquire('adsb')


def test_iq_exact_limit_and_replay_metadata(tmp_path):
    r = Recorder(tmp_path)
    r.start('iq', ReceiverConfig(), duration=1,max_bytes=1024)
    r.write('iq', np.ones(500,dtype=np.complex64))
    assert not r.active
    meta = r.completed[0]
    assert Path(meta['path']).stat().st_size == 1024
    path, rate, freq = replay_info(meta['path'])
    assert rate == 2400000 and freq == 100100000
    assert len(np.fromfile(path, dtype='<c8')) == 128
    assert meta['reason'] == 'limit reached'
    doc=json.loads(path.with_suffix('.sigmf-meta').read_text())
    assert doc['global']['core:extensions']==[{'name':'touchwave','version':'0.1.0','optional':True}]


def test_wav_limit_and_low_disk(tmp_path, monkeypatch):
    import wave
    from collections import namedtuple
    r = Recorder(tmp_path)
    r.start('audio', ReceiverConfig(), duration=1, max_bytes=1024)
    r.write('audio', np.ones((400,2)))
    meta = r.completed[0]
    with wave.open(meta['path']) as wav:
        assert wav.getnchannels() == 2
        assert wav.getnframes() == 256
        assert wav.getframerate() == 48000
    usage = namedtuple('usage','total used free')
    monkeypatch.setattr('touchwave.recording.shutil.disk_usage', lambda _: usage(100,100,0))
    with pytest.raises(RuntimeError):
        r.start('iq',ReceiverConfig())


def test_capture_stops_when_disk_fills(tmp_path, monkeypatch):
    from collections import namedtuple
    r=Recorder(tmp_path)
    r.start('iq',ReceiverConfig())
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('touchwave.recording.shutil.disk_usage',lambda _:usage(100,100,0))
    r.write('iq',np.ones(100,dtype=np.complex64))
    assert not r.active
    assert r.completed[0]['reason']=='low disk space'


def test_capability_validation():
    from touchwave.devices import SoapyProvider, SIMULATOR, validate_device
    caps=SoapyProvider().capabilities(SIMULATOR)
    with pytest.raises(ValueError):
        validate_device(ReceiverConfig(frequency=7_000_000_000),caps)
    with pytest.raises(ValueError):
        validate_device(ReceiverConfig(gain_stages={'missing':10}),caps)
    with pytest.raises(ValueError):
        validate_device(ReceiverConfig(biastee=True),caps)


def test_fractional_hz_is_rejected():
    with pytest.raises(ValueError,match='whole number'):
        ReceiverConfig.from_dict({'frequency':100100000.5})


def test_restore_preferences_preserves_supported_values_and_disables_bias():
    from touchwave.devices import restore_compatible_config
    caps=dict(frequency_ranges=[[24000000,1764000000]],sample_rates=[1024000,2560000],
              gains={'TUNER':[0,49.6]},gain_range=[0,49.6],ppm=True,antennas=['RX'])
    saved=ReceiverConfig(sample_rate=1024000,gain=35,ppm=12,biastee=True,antenna='RX')
    restored=restore_compatible_config(saved,ReceiverConfig(sample_rate=2560000),caps)
    assert restored.sample_rate==1024000 and restored.gain==35 and restored.ppm==12
    assert restored.antenna=='RX' and not restored.biastee
    wrong=ReceiverConfig(frequency=1000000,sample_rate=2400000,gain=100,gain_stages={'LNA':20})
    restored=restore_compatible_config(wrong,ReceiverConfig(sample_rate=2560000),caps)
    assert restored.sample_rate==2560000 and restored.frequency==100100000
    assert restored.gain==49.6 and restored.gain_stages=={}


def test_boolean_config_does_not_treat_false_string_as_enabled_bias():
    with pytest.raises(ValueError,match='true or false'):
        ReceiverConfig.from_dict({'biastee':'false'})
    with pytest.raises(ValueError,match='object'):
        ReceiverConfig.from_dict([])


def test_library_recovers_capture_finalized_after_ui_closed(tmp_path):
    store=Store(tmp_path)
    recorder=Recorder(store.recordings)
    recorder.start('audio',ReceiverConfig())
    recorder.write('audio',np.zeros((100,2)))
    recorder.stop('worker shutdown')
    assert not store.list_recordings()  # No final status reached the UI.
    store.close()
    reopened=Store(tmp_path)
    recovered=reopened.list_recordings()
    assert len(recovered)==1 and recovered[0]['reason']=='worker shutdown'
    reopened.recover_recordings()
    assert len(reopened.list_recordings())==1
    reopened.close()
