import numpy as np
import pytest
from gnuradio import blocks, gr
from touchwave.dsp import Demodulator, synthetic_iq
from touchwave.model import MODES, ReceiverConfig


def decode(mode, iq):
    graph = gr.top_block()
    source = blocks.vector_source_c(iq.tolist(), False)
    config = ReceiverConfig(mode=mode, bandwidth=MODES[mode]['bandwidth'])
    demod = Demodulator(config)
    sinks = [blocks.vector_sink_f(), blocks.vector_sink_f()]
    graph.connect(source, demod)
    for i, sink in enumerate(sinks):
        graph.connect((demod,i), sink)
    graph.run()
    return [np.array(s.data())[12000:-1000] for s in sinks]


def tone_power(samples, freq):
    window = np.hanning(len(samples))
    fft = abs(np.fft.rfft(samples*window))**2
    bins = np.fft.rfftfreq(len(samples),1/48000)
    return fft[abs(bins-freq)<20].sum()


@pytest.mark.parametrize('mode', ['AM','NFM','USB','LSB'])
def test_demodulation_recovers_tone(mode):
    rate = MODES[mode]['rate']
    left, right = decode(mode, synthetic_iq(mode,rate,0.6))
    assert len(left)>10000 and np.isfinite(left).all()
    assert np.sqrt(np.mean(left**2)) > .01
    assert tone_power(left,1000) > 100*tone_power(left,4000)
    assert np.allclose(left,right)


def test_fm_stereo_separation():
    left, right = decode('WFM',synthetic_iq('WFM',240000,0.8))
    assert tone_power(left,1000) > 10*tone_power(left,2300)
    assert tone_power(right,2300) > 10*tone_power(right,1000)


@pytest.mark.parametrize('mode,sign',[('USB',1),('LSB',-1)])
def test_ssb_rejects_other_sideband(mode,sign):
    t=np.arange(28800)/48000
    iq=(.3*np.exp(sign*2j*np.pi*1000*t)+.3*np.exp(-sign*2j*np.pi*2000*t)).astype(np.complex64)
    left,_=decode(mode,iq)
    assert tone_power(left,1000)>100*tone_power(left,2000)


def test_pilot_detector_rejects_mono_and_detects_stereo():
    from touchwave.dsp import ChannelMeter
    meter=ChannelMeter(240000,wfm=True)
    stereo=synthetic_iq('WFM',240000,.1)[-4096:]
    meter.work([np.array([stereo])],[])
    assert meter.pilot
    mono=synthetic_iq('NFM',240000,.1)[-4096:]
    meter.work([np.array([mono])],[])
    assert not meter.pilot
