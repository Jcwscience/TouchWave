# Validation and hands-on checklist

## Environment

Implementation and checks run directly on the connected Raspberry Pi Compute
Module 5 Lite with 8 GB RAM, a Debian 13 (Trixie) userland, Python 3.13.5, Kivy
2.3.1, GNU Radio 3.10.12.0 and SoapySDR 0.8.1. Native rendering uses the Broadcom
V3D OpenGL driver. DSI reports a 1080 × 1240, 65 × 75 mm portrait panel; the
`hyn_ts` multitouch input reports matching coordinate ranges.

The connected RTL2838/R820T dongle, serial `00000001`, enumerates and streams through
GNU Radio's Soapy source at 2.56 MS/s. The C-Media USB audio adapter opens at stereo
48 kHz; its hardware speaker mixer was enabled when inspected. HackRF is disconnected.

## Completed checks

- **27 automated tests:** configuration and frequency validation, portable/atomic
  bookmark import, persistence, scanner dwell/hold, exclusive device leases,
  exact capture limits, low-disk behavior, recording recovery, source-stall cleanup,
  worker death/parent-death handling, IQ replay and decoder event routing.
- **Generated RF:** AM/NFM/USB/LSB recover the expected tone; opposite sideband
  rejection exceeds 20 dB at the tested tones; stereo FM channel separation exceeds
  10 dB at the tested tones. Pilot detection distinguishes mono and stereo fixtures.
- **USB audio:** all five modes run through the real ALSA adapter with output
  muted, while recording one second of demodulated audio. This checks device access
  and data flow, not subjective sound quality.
- **17 GUI workflow steps:** numeric entry and selection replacement, mode switching,
  tagged bookmarks, WAV/IQ capture, library replay, live return, scanning, manual
  scan cancellation, synthetic pinch/tap events and worker restart.
- **SigMF metadata:** generated IQ metadata validates against the official core
  JSON schema; the optional TouchWave namespace is declared and documented.
- **Native display:** main receiver and sheets render without application errors.
  Screenshots include the live simulated spectrum/waterfall, frequency keypad,
  receiver controls, plot controls, bookmark entry and library.
- **Live GUI + RTL-SDR:** all five modes switched successfully using the real
  dongle and muted USB audio; bounded WAV and IQ recording completed. The actual
  IQ capture also validates against the official SigMF core schema.
- **Installation:** editable virtual environment, CLI entry point and per-user
  desktop launcher installed. No autostart enabled.

**Passed:** 1800 seconds of headless RTL-SDR reception at 2.56 MS/s, with 35,607 observed spectrum frames and no detected source stall. The final sampled CPU usage was 55.9% of one core, RSS about 96.6 MiB, and sampled temperatures ranged 33.6–40.2°C. This run used a silent DSP sink; the actual USB audio path is checked separately.

The 30-minute RTL-SDR endurance report is written to `artifacts/rtl-soak.json`.
Its `result` is `running` until the entire check finishes, then `passed` or `failed`.
The report records stream frames, memory, CPU time, temperature and load. Native
logs are in `artifacts/rtl-soak.log`. Hardware overflow counts remain unknown when
the driver does not expose them; inspect native logs as well as the JSON result.

Other reports: `artifacts/audio-report.json`, `artifacts/workflow-report.json`,
`artifacts/ui-report.json`, `artifacts/live-ui-report.json`,
`artifacts/sigmf-validation.json`. Re-run commands are in the README.

## When back at the handheld

1. Open **TouchWave** from the application menu. Confirm portrait layout, text
   size, touch alignment and access to the bottom controls. Reception starts stopped.
2. Attach an appropriate antenna, enter a known local FM frequency, tap Start RX,
   and check sound, volume/mute, stereo indication and mono fallback. Check the
   audio output in Controls if necessary.
3. Try tap-to-tune, drag-to-pan, two-finger zoom and double-tap recenter. Hold the
   frequency step controls; enter an exact frequency with both keypad and keyboard.
4. Check AM/NFM/SSB against suitable known signals or recorded fixtures. The R820T
   tuner's reported range starts near 24 MHz; AM demodulation does not add HF coverage.
5. Save/tag bookmarks, scan a group with squelch above the noise floor, and verify
   activity hold and manual scan cancellation.
6. Make a short WAV and IQ recording. Replay them from Library, change IQ decoder
   mode/filter, return to live, and verify the saved files and recording limits.
7. Unplug/reconnect the RTL-SDR during reception. Confirm a visible error and a
   responsive UI, then explicitly start reception again.
8. Connect HackRF and repeat discovery, tuning, gain-stage, recording and endurance
   checks at the default 8 MS/s. This hardware path remains unverified until connected.

GPS/battery integration, maps, ADS-B, transmission and concurrent receiver pipelines
are outside this release. No listening-quality, finger-latency or daylight-readability
claim replaces the pending hands-on checks.
