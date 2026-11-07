# TouchWave

A native Kivy SDR receiver for a Linux handheld, designed around a 1080 × 1240,
65 × 75 mm portrait multitouch display and Raspberry Pi Compute Module 5.

TouchWave runs GNU Radio in its own process and uses SoapySDR for RTL-SDR and
HackRF receive support. It starts with reception **stopped** and antenna bias power
off. A synthetic RF source lets you explore the complete interface without an SDR.

## Run on Raspberry Pi OS

From this checkout, with the distribution's Python:

```bash
./scripts/install.sh
.venv/bin/touchwave
```

The installer installs native dependencies, creates a virtual environment with
access to system GNU Radio bindings, installs the editable application, and adds a
TouchWave entry to the desktop application menu. It does not enable autostart.
The repository must stay in place while using an editable install.

If dependencies are already installed, launch directly:

```bash
/usr/bin/python3 -m touchwave
/usr/bin/python3 -m touchwave --demo --no-audio
/usr/bin/python3 -m touchwave --demo --windowed
/usr/bin/python3 -m touchwave --devices
```

Run the GUI from the device's desktop session. For an SSH session on this handheld,
`DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 /usr/bin/python3 -m touchwave` uses its
existing Xwayland session. Session paths and user IDs vary on other installations.
Native multitouch is detected from Linux input devices; the desktop user needs
access to the touchscreen's input node. USB access normally comes from the SDR
packages' udev rules. Do not run the GUI as root.

Optional desktop-session autostart:

```bash
.venv/bin/python scripts/install_launcher.py --autostart
```

Remove `~/.config/autostart/touchwave.desktop` to disable it. No system service or
kiosk/compositor configuration is changed.

## Receiver

- AM, narrow FM, pilot-referenced stereo broadcast FM with automatic mono fallback,
  and upper/lower sideband reception. FM de-emphasis is selectable: 50 or 75 µs.
- Large frequency keypad with Hz/kHz/MHz units; step controls repeat while held.
- GPU spectrum and circular-texture waterfall; tap to tune, drag to pan, pinch to
  zoom, double-tap to recenter. Mouse wheel zoom works in desktop previews.
- Adjustable channel bandwidth, squelch, RF gain, PPM correction, volume and mute.
  Hardware-specific antenna selection, gain stages and bias control appear only
  where supported. Bias power is never restored automatically at launch or device change.
- Spectrum smoothing, peak hold, waterfall range/speed and an economy profile.
- Searchable, tagged bookmarks and portable JSON import/export. Scan a bookmark
  group with dwell, signal hold and squelch; manual tuning stops scanning.
- Stereo WAV audio recording before volume/mute; sample-bounded cf32 SigMF IQ
  recording and replay. Recordings require explicit time and size limits, reserve
  128 MiB free space, and stop cleanly when limits or write errors occur.
- A recording library with WAV playback and IQ replay. Live reception stops for
  WAV playback. Return from IQ replay with **Controls → Return to live receiver**.
- Keyboard shortcuts: left/right arrows step, space starts/stops reception, M mutes.
  Text fields support the hardware keyboard and Kivy's docked virtual keyboard;
  frequency entry has its own numeric keypad.

**First use:** open Controls, select the SDR, check the audio output, tap Apply,
then Start RX. Quit from **Controls → Quit TouchWave**. On this handheld, the USB audio output is automatically selected
when the saved output is still `default`. Its ALSA name is
`plughw:CARD=Device,DEV=0`. A radio needs a suitable antenna and an active signal;
selecting AM does not extend the dongle's physical frequency range.

The spectrum uses relative dBFS, not calibrated dBm. Display zoom crops the current
capture; it does not change the hardware sample rate. The VFO is centered on the
hardware tuning frequency; tuning and panning retune the device. This release has
one active receiver/decoder and does not transmit.

## Data and diagnostics

By default, data lives in `$XDG_DATA_HOME/touchwave` or
`~/.local/share/touchwave`: SQLite preferences/bookmarks/recording index,
`recordings/`, and `worker.log`. Use `--data-dir PATH` for an isolated profile.
Bookmark import is additive and atomic; it can create duplicates if repeated.
Recording deletion/renaming is left to the file manager in this release.

The app negotiates supported device sample rates. This RTL-SDR advertises 2.56 MS/s
as the nearest preset to 2.4 MS/s; HackRF defaults to 8 MS/s followed by early
filtering/decimation. HackRF is implemented but awaits physical validation.

A disconnected or stalled source stops reception after about four seconds. The UI
keeps running; reconnect and explicitly start reception. **Controls → Restart
receiver worker** recovers from a crashed worker. Driver/native DSP errors are
written to `worker.log`. Hardware overrun counters are not consistently exposed by
SoapySDR: unknown counts are not presented as zero. Capture metadata includes
known discontinuities and the driver's reporting limitation.

Test commands:

```bash
/usr/bin/python3 -m pytest -q
DISPLAY=:0 /usr/bin/python3 scripts/ui_check.py
DISPLAY=:0 /usr/bin/python3 scripts/workflow_check.py
# Real RTL-SDR GUI/mode/recording check, with muted USB audio
DISPLAY=:0 /usr/bin/python3 scripts/live_ui_check.py
/usr/bin/python3 scripts/hardware_check.py --seconds 1800
# Optional: verify the actual audio device opens, with muted output
/usr/bin/python3 scripts/hardware_check.py --seconds 10 --audio-device plughw:CARD=Device,DEV=0
```

UI screenshots and live hardware reports go to ignored `artifacts/`. The endurance
check owns the SDR exclusively; close TouchWave reception before running it.
See [validation](docs/VALIDATION.md) for tested behavior and remaining hands-on checks.

## Extending TouchWave

Device discovery/capabilities, application state, DSP, storage and presentation are
separate modules. Python entry points load demodulator, decoder and UI tool
extensions. A decoder owns a worker-side device lease and emits versioned events;
a map view can subscribe to position events without depending on the decoder.
See [architecture and extension contracts](docs/ARCHITECTURE.md).

ADS-B, geographic maps, GPS, RDS, remote radios, transmission and concurrent VFOs
are future work. The UI palette, typography and spacing are centralized for future
themes; v0.1 ships one dark palette.
