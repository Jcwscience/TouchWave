"""Soapy device discovery. No hardware object escapes its owning process."""
import gc


SIMULATOR = {"driver": "demo", "label": "Demo · synthetic signals", "serial": "demo"}


class SoapyProvider:
    def discover(self):
        import SoapySDR
        # Enumerate specific families: avoid probing audio/network/UHD drivers.
        return [dict(item) for driver in ("rtlsdr", "hackrf")
                for item in SoapySDR.Device.enumerate(f"driver={driver}")]

    def capabilities(self, selection):
        if selection["driver"] == "demo":
            return dict(frequency_ranges=[[0, 6e9]], sample_rates=[2400000], gains={},
                        gain_range=[0, 50], ppm=False, antennas=[], settings=[])
        import SoapySDR as s
        s.Device.enumerate("driver=" + selection["driver"])
        device = s.Device(",".join(f"{k}={selection[k]}" for k in ("driver", "serial") if selection.get(k)))
        try:
            def rng(r):
                return [r.minimum(), r.maximum()]
            return dict(frequency_ranges=[rng(r) for r in device.getFrequencyRange(s.SOAPY_SDR_RX, 0)],
                        sample_rates=[r for r in device.listSampleRates(s.SOAPY_SDR_RX, 0)
                                      if selection["driver"] != "hackrf" or r >= 8000000],
                        gains={name: rng(device.getGainRange(s.SOAPY_SDR_RX, 0, name))
                               for name in device.listGains(s.SOAPY_SDR_RX, 0)},
                        gain_range=rng(device.getGainRange(s.SOAPY_SDR_RX, 0)),
                        ppm=device.hasFrequencyCorrection(s.SOAPY_SDR_RX, 0),
                        antennas=list(device.listAntennas(s.SOAPY_SDR_RX, 0)),
                        settings=[dict(key=a.key, name=a.name, value=a.value) for a in device.getSettingInfo()])
        finally:
            del device
            gc.collect()


def validate_device(config, caps):
    if not any(lo <= config.frequency <= hi for lo, hi in caps["frequency_ranges"]):
        raise ValueError("Frequency is outside this device's tuning range")
    if caps["sample_rates"] and not any(abs(config.sample_rate - r) < 1 for r in caps["sample_rates"]):
        raise ValueError("Unsupported sample rate for this device")
    lo, hi = caps["gain_range"]
    if not lo <= config.gain <= hi:
        raise ValueError(f"Gain must be between {lo:g} and {hi:g} dB")

    for name, value in config.gain_stages.items():
        if name not in caps['gains'] or not caps['gains'][name][0] <= value <= caps['gains'][name][1]:
            raise ValueError(f"Unsupported gain stage or value: {name}")
    if config.ppm and not caps["ppm"]:
        raise ValueError("Frequency correction is unavailable on this device")
    if config.antenna and config.antenna not in caps['antennas']:
        raise ValueError("Unsupported antenna selection")
    if config.biastee and not any(s['key'] in ('biastee', 'bias_tx') for s in caps['settings']):
        raise ValueError("Antenna bias is unavailable on this device")


def restore_compatible_config(saved, negotiated, caps):
    """Restore listening preferences while respecting the newly selected hardware."""
    values = saved.to_dict()
    if not any(lo <= saved.frequency <= hi for lo, hi in caps['frequency_ranges']):
        values['frequency'] = negotiated.frequency
    if caps['sample_rates'] and saved.sample_rate not in caps['sample_rates']:
        values['sample_rate'] = negotiated.sample_rate
    lo, hi = caps['gain_range']
    values['gain'] = min(max(saved.gain, lo), hi)
    if any(name not in caps['gains'] or not caps['gains'][name][0] <= gain <= caps['gains'][name][1]
           for name, gain in saved.gain_stages.items()):
        values['gain_stages'] = {}
    if saved.antenna not in caps['antennas']:
        values['antenna'] = ''
    values['biastee'] = False
    if not caps['ppm']:
        values['ppm'] = 0
    return saved.from_dict(values)
