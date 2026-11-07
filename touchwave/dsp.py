"""GNU Radio graphs. Imported only by the receiver worker and DSP tests."""
import math
import threading
import time
import numpy as np
from gnuradio import analog, audio, blocks, filter, gr, soapy
from gnuradio.fft import window
from gnuradio.filter import firdes
from .model import MODES, EXTERNAL_DEMODULATORS

FFT_SIZE = 2048


def synthetic_iq(mode, rate, seconds=0.1, seed=7):
    """Deterministic RF fixture: audio tones, stereo pilot and a noise floor."""
    t = np.arange(round(rate * seconds), dtype=np.float64) / rate
    left = 0.35 * np.sin(2 * np.pi * 1000 * t)
    right = 0.25 * np.sin(2 * np.pi * 2300 * t)
    if mode == "AM":
        iq = 0.5 * (1 + left)
    elif mode in ("NFM", "WFM"):
        composite = left
        deviation = 2500
        if mode == "WFM":
            composite = (left + right) / 2 + (left - right) / 2 * np.cos(2 * np.pi * 38000 * t) + 0.1 * np.cos(2 * np.pi * 19000 * t)
            deviation = 75000
        iq = 0.6 * np.exp(1j * np.cumsum(composite) * 2 * np.pi * deviation / rate)
    elif mode in ("USB", "LSB"):
        iq = 0.4 * np.exp((1j if mode == "USB" else -1j) * 2 * np.pi * 1000 * t)
    else:
        raise ValueError("Demo source has no fixture for this modulation")
    rng = np.random.default_rng(seed)
    return np.asarray(iq + 0.001 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t))), np.complex64)


class LatestSpectrum(gr.sync_block):
    def __init__(self, rate, fps=20):
        gr.sync_block.__init__(self, "Latest spectrum", in_sig=[(np.complex64, FFT_SIZE)], out_sig=None)
        self.rate, self.fps = rate, fps
        self.lock = threading.Lock()
        self.frame = None
        self.sequence = 0
        self.window = np.hanning(FFT_SIZE)

    def work(self, inputs, outputs):
        sample = inputs[0][-1]
        spectrum = np.fft.fftshift(np.fft.fft(sample * self.window))
        db = 20 * np.log10(np.maximum(np.abs(spectrum) / self.window.sum(), 1e-8))
        with self.lock:
            self.sequence += 1
            self.frame = (self.sequence, db.astype(np.float32), time.monotonic())
        return len(inputs[0])

    def latest(self):
        with self.lock:
            return self.frame


class IQCapture(gr.sync_block):
    def __init__(self, recorder):
        gr.sync_block.__init__(self, "Bounded IQ capture", in_sig=[np.complex64], out_sig=None)
        self.recorder = recorder

    def work(self, inputs, outputs):
        self.recorder.write("iq", inputs[0])
        return len(inputs[0])


class AudioCapture(gr.sync_block):
    def __init__(self, recorder):
        gr.sync_block.__init__(self, "Bounded audio capture", in_sig=[np.float32, np.float32], out_sig=None)
        self.recorder = recorder

    def work(self, inputs, outputs):
        if self.recorder.active:
            self.recorder.write("audio", np.column_stack(inputs))
        return len(inputs[0])


class ChannelMeter(gr.sync_block):
    def __init__(self, rate, wfm=False):
        gr.sync_block.__init__(self, "Channel meter", in_sig=[(np.complex64, 4096)], out_sig=None)
        self.rate, self.wfm = rate, wfm
        self.level, self.pilot = -140.0, False

    def work(self, inputs, outputs):
        x = inputs[0][-1]
        self.level = float(10 * np.log10(max(float(np.mean(np.abs(x)**2)), 1e-14)))
        if self.wfm:
            composite = np.angle(x[1:] * x[:-1].conj())
            spec = abs(np.fft.rfft(composite * np.hanning(len(composite))))**2
            freqs = np.fft.rfftfreq(len(composite), 1 / self.rate)
            pilot = spec[(freqs > 18800) & (freqs < 19200)].sum()
            total = spec[(freqs > 200) & (freqs < 60000)].sum()
            self.pilot = bool(pilot / max(total, 1e-20) > 0.015 and self.level > -65)
        return len(inputs[0])


class StereoFM(gr.hier_block2):
    """Pilot-referenced FM stereo decoder with FIR filters and bounded startup."""
    def __init__(self, rate, tau):
        gr.hier_block2.__init__(self, "FM stereo", gr.io_signature(1, 1, gr.sizeof_gr_complex),
                               gr.io_signature(2, 2, gr.sizeof_float))
        demod = analog.quadrature_demod_cf(rate / (2 * math.pi * 75000))
        pilot_taps = firdes.complex_band_pass(1, rate, 18800, 19200, 1500, window.WIN_HAMMING)
        pilot = filter.fir_filter_fcc(1, pilot_taps)
        pll = analog.pll_refout_cc(0.001, 2*math.pi*19200/rate, 2*math.pi*18800/rate)
        square = blocks.multiply_cc()
        real = blocks.complex_to_real()
        double = blocks.multiply_const_ff(2)
        delay = blocks.delay(gr.sizeof_float, (len(pilot_taps)-1)//2)
        mix = blocks.multiply_ff()
        taps = firdes.low_pass(1, rate, 15000, 4000, window.WIN_HAMMING)
        mono, difference = filter.fir_filter_fff(5, taps), filter.fir_filter_fff(5, taps)
        left, right = blocks.add_ff(), blocks.sub_ff()
        self.connect(self, demod, pilot, pll)
        self.connect(pll, (square, 0))
        self.connect(pll, (square, 1))
        self.connect(square, real, double, (mix, 1))
        self.connect(demod, delay, (mix, 0))
        self.connect(delay, mono)
        self.connect(mix, difference)
        self.connect(mono, (left, 0))
        self.connect(difference, (left, 1))
        self.connect(mono, (right, 0))
        self.connect(difference, (right, 1))
        for i, source in enumerate((left, right)):
            deemph = analog.fm_deemph(48000, tau)
            self.connect(source, deemph, (self, i))


class Demodulator(gr.hier_block2):
    """All modes expose the same complex-in / stereo-float-out contract."""
    def __init__(self, config):
        gr.hier_block2.__init__(self, config.mode, gr.io_signature(1, 1, gr.sizeof_gr_complex),
                               gr.io_signature(2, 2, gr.sizeof_float))
        rate = MODES[config.mode]["rate"]
        self.stereo = config.mode == "WFM"
        if config.mode in EXTERNAL_DEMODULATORS:
            decoder = EXTERNAL_DEMODULATORS[config.mode].build(config)
            self.connect(self, decoder)
            self.connect((decoder, 0), (self, 0))
            self.connect((decoder, 1), (self, 1))
        elif self.stereo:
            decoder = StereoFM(rate, config.deemphasis * 1e-6)
            self.connect(self, decoder)
            self.connect((decoder, 0), (self, 0))
            self.connect((decoder, 1), (self, 1))
        else:
            agc = analog.agc2_cc(0.01, 0.001, 0.5, 1.0, 1000.0) if config.agc else blocks.multiply_const_cc(1)
            self.connect(self, agc)
            if config.mode == "NFM":
                decoder = analog.nbfm_rx(48000, 48000, tau=75e-6, max_dev=2500)
            elif config.mode == "AM":
                cutoff = min(config.bandwidth / 2, 8000)
                decoder = analog.am_demod_cf(48000, 1, cutoff, cutoff + 1500)
            else:
                width = config.bandwidth
                lo, hi = (200, width) if config.mode == "USB" else (-width, -200)
                sideband = filter.fir_filter_ccc(1, firdes.complex_band_pass(1, 48000, lo, hi, 200, window.WIN_HAMMING))
                self.connect(agc, sideband)
                agc = sideband
                decoder = blocks.complex_to_real()
            self.connect(agc, decoder)
            for channel in (0, 1):
                self.connect(decoder, (self, channel))


class RadioGraph(gr.top_block):
    def __init__(self, selection, config, recorder, replay=None, audio_enabled=True):
        gr.top_block.__init__(self, "TouchWave receiver", catch_exceptions=True)
        self.config = config
        self.rate = int(config.sample_rate)
        if replay:
            self.source = blocks.file_source(gr.sizeof_gr_complex, str(replay), False)
            self.throttle = blocks.throttle(gr.sizeof_gr_complex, self.rate, True)
            self.connect(self.source, self.throttle)
            source = self.throttle
        elif selection["driver"] == "demo":
            self.source = blocks.vector_source_c(synthetic_iq(config.mode, self.rate).tolist(), True)
            self.throttle = blocks.throttle(gr.sizeof_gr_complex, self.rate, True)
            self.connect(self.source, self.throttle)
            source = self.throttle
        else:
            args = ",".join(f"{key}={selection[key]}" for key in ("driver", "serial") if selection.get(key))
            # Explicitly disable bias power for both supported drivers.
            self.source = soapy.source(args, "fc32", 1, "", "", [""], [""])
            keys = {item.key for item in self.source.get_setting_info()}
            for key in ("biastee", "bias_tx"):
                if key in keys:
                    self.source.write_setting(key, "true" if config.biastee else "false")
            if config.antenna:
                self.source.set_antenna(0, config.antenna)
            self.source.set_sample_rate(0, self.rate)
            self.source.set_frequency(0, config.frequency)
            self.source.set_gain(0, config.gain)
            for name, value in config.gain_stages.items():
                self.source.set_gain(0, name, value)
            if config.ppm:
                self.source.set_frequency_correction(0, config.ppm)
            actual_rate = int(round(self.source.get_sample_rate(0)))
            self.rate = actual_rate
            self.config = config.changed(sample_rate=actual_rate, frequency=int(round(self.source.get_frequency(0))),
                                         gain=float(self.source.get_gain(0)),
                                         gain_stages={name: float(self.source.get_gain(0, name)) for name in config.gain_stages})
            source = self.source
        fps = 10 if config.economy else 20
        vectors = blocks.stream_to_vector(gr.sizeof_gr_complex, FFT_SIZE)
        thin = blocks.keep_one_in_n(gr.sizeof_gr_complex * FFT_SIZE, max(1, round(self.rate / FFT_SIZE / fps)))
        self.spectrum = LatestSpectrum(self.rate, fps)
        self.connect(source, vectors, thin, self.spectrum)
        self.iq_capture = IQCapture(recorder)
        self.connect(source, self.iq_capture)
        target = MODES[config.mode]["rate"]
        # Filter + integer decimation first; finish unusual device rates with a rational resampler.
        decimation = max(1, self.rate // target)
        cutoff = config.bandwidth / 2 if config.mode not in ("USB", "LSB") else config.bandwidth
        transition = min(target * 0.1, max(1000, cutoff * 0.2),
                         max(100, (min(self.rate / decimation, target) / 2 - cutoff) * 0.8))
        taps = firdes.low_pass(1, self.rate, cutoff, transition, window.WIN_HAMMING)
        self.channel = filter.freq_xlating_fir_filter_ccc(decimation, taps, 0, self.rate)
        self.connect(source, self.channel)
        channel = self.channel
        if self.rate != target * decimation:
            divisor = math.gcd(target * decimation, self.rate)
            resampler = filter.rational_resampler_ccc(interpolation=target * decimation // divisor,
                                                     decimation=self.rate // divisor)
            self.connect(channel, resampler)
            channel = resampler
        meter_vectors = blocks.stream_to_vector(gr.sizeof_gr_complex, 4096)
        meter_thin = blocks.keep_one_in_n(gr.sizeof_gr_complex * 4096, max(1, target // 4096 // 10))
        self.meter = ChannelMeter(target, config.mode == "WFM")
        self.connect(channel, meter_vectors, meter_thin, self.meter)
        self.squelch = analog.pwr_squelch_cc(config.squelch, 0.001, 0, False)
        self.connect(channel, self.squelch)
        self.demod = Demodulator(config)
        self.connect(self.squelch, self.demod)
        self.capture = AudioCapture(recorder)
        self.volumes = [blocks.multiply_const_ff(0), blocks.multiply_const_ff(0)]
        # Stereo matrix: output = own*a + opposite*b. Mono fallback uses a=b=0.5.
        self.matrix = []
        self.sums = []
        for i in (0, 1):
            a, b = blocks.multiply_const_ff(1), blocks.multiply_const_ff(0)
            summation = blocks.add_ff()
            self.connect((self.demod, i), a, (summation, 0))
            self.connect((self.demod, 1-i), b, (summation, 1))
            self.connect(summation, (self.capture, i))
            self.connect(summation, self.volumes[i])
            self.matrix.append((a, b))
            self.sums.append(summation)
        self.output = audio.sink(48000, config.audio_device, True) if audio_enabled else None
        self.nulls = []
        for i in (0, 1):
            if self.output:
                self.connect(self.volumes[i], (self.output, i))
            else:
                sink = blocks.null_sink(gr.sizeof_float)
                self.nulls.append(sink)
                self.connect(self.volumes[i], sink)
        self.set_audio(config)

    def set_audio(self, config):
        for volume in self.volumes:
            volume.set_k(0 if config.muted else config.volume)
        self.squelch.set_threshold(config.squelch)
        mono = config.force_mono or not self.meter.pilot
        for own, other in self.matrix:
            own.set_k(0.5 if mono and config.mode == "WFM" else 1)
            other.set_k(0.5 if mono and config.mode == "WFM" else 0)

    def dispose(self):
        self.stop()
        self.wait()
        self.disconnect_all()
        self.source = None
        self.output = None
        self.throttle = None
