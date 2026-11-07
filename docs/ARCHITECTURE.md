# Architecture and extension contracts

## Process and data boundaries

`touchwave.ui` owns Kivy widgets, application interactions and the main-thread
SQLite Store. `WorkerClient` starts a local subprocess using the same Python
interpreter, with private IPC sockets inside a mode-0700 temporary directory.
Its two IO threads own separate ZMQ sockets; Kivy drains bounded latest-frame
state and reliable command replies at 30 Hz. No hardware call runs on the UI thread.

On Linux, a parent-death signal also terminates the worker if the UI exits abruptly.
The worker owns Soapy discovery, capability queries, device leases, GNU Radio
flowgraphs and recording. Commands use REQ/REP with a version-1 envelope:

```json
{"version": 1, "op": "configure", "args": {"config": {"frequency": 100100000}}}
```

`configure` accepts a ReceiverConfig dictionary (omitted fields use defaults).
Replies contain `ok`, `result` or `error`, and a current `state`. Commands are
serialized. Timeouts report an error and replace the REQ socket; the user can
restart the worker rather than silently reconnecting hardware.

Operations: `ping`, `discover`, `select`, `configure`, `start`, `stop`, `record`,
`record_stop`, `replay`, `live`, `tool_start`, `shutdown`. The supported Python
interfaces in `model.py` and `plugins.py` are the source of truth for arguments.

PUB/SUB carries disposable spectrum frames and periodic state snapshots. Spectrum
queues are bounded, so a slow display loses old frames without delaying audio.
State contains a monotonic timestamp to reject stale snapshots and the latest
completed recording metadata. The event history holds the most recent 50 decoded
events and sequence numbers; views detect sequence gaps if they need to report
loss. This is a live UI stream, not an archival decoded-event database.

## DSP and recording

The compiled source feeds both a thinned/windowed FFT branch and a channel filter
with early decimation. Rational resampling normalizes each demodulator's input.
The channel meter runs before squelch/AGC. Demodulators output stereo 48 kHz float
audio. FM stereo uses a 19 kHz pilot PLL and 38 kHz difference-channel recovery;
pilot detection selects stereo or a mono sum. Mute/volume apply after the recording
tap. AM/SSB use optional AGC. Default AM/NFM bandwidths and SSB filter shapes are
independent of the display zoom.

Only volume, mute, squelch, force-mono and tuning step update without rebuilding
the graph. Other receiver changes stop/rebuild/restart it; those changes are
rejected during recording. Scan and recording are mutually exclusive in the UI.
IQ replay uses capture rate/frequency and permits demodulator/filter changes.
It does not pretend that retuning a file captures a different RF frequency.

Sample-count limits prevent writing beyond configured recording payload limits.
WAV headers and JSON metadata add a small amount of file overhead. The recorder
holds a lock across open/write/close, checks free space periodically, finalizes
metadata on normal/error shutdown, and annotates detected source stalls. Forced
process termination or power loss can leave an unfinished file; no crash-safe
recording journal is promised. Synchronous recording can overrun slow storage;
use modest capture rates and inspect driver logs. IQ uses 8 bytes per sample. Receiver diagnostics use the declared optional
[TouchWave SigMF extension](touchwave.sigmf-ext.md).

## Plugins

Plugins are installed Python packages loaded through entry points, not sandboxed
code. There is no in-app installation/download mechanism. Failed imports are
isolated and recorded. IDs must be unique. Keep descriptors free of hardware and
GNU Radio imports; import DSP inside `build()`.

A **demodulator** entry point in `touchwave.demodulators` returns an instance with:

- `id`, `label`, `channel_rate`, `default_bandwidth`, `bandwidth_range`, `parameters`.
- `build(config)` returning a GNU Radio block with one complex input at
  `channel_rate` and two float outputs at 48 kHz.

Example packaging:

```toml
[project.entry-points."touchwave.demodulators"]
example = "example_radio:ExampleDemodulator"
```

The model registry makes valid descriptors available to frequency validation,
mode selection and the DSP factory. Custom parameter editors are a plugin-view
responsibility; the core receiver controls cover the shared configuration only.

A **decoder** entry point in `touchwave.decoders` returns an object with `id`,
`label`, and `build(selection, config, emit)`. The build method creates a GNU Radio
`top_block` (or an object supporting `start`, `stop`, `wait`). It owns its own
source/flowgraph and publishes `DecodedEvent` dataclass objects through `emit`.
It must honor selection/capabilities, keep transmit off, and release hardware on
stop. The service stops the current receiver, acquires the exclusive resource
lease and starts this graph. Missing plugins and build failures surface as errors.

A **tool view** entry point in `touchwave.tools` returns an object with `id`,
`label`, `requires_device`, optional `decoder_id`, and `create_view(application)`.
The factory returns a Kivy widget with a defined height or minimum-height binding
for use in a scrolling sheet. Library lists installed tools. A device-backed tool
starts its named decoder through the worker and stops it when its sheet closes. A view may subscribe by adding a
callable to `application.event_handlers` and must remove it in an optional widget `dispose()` method called when the sheet closes; events
are dictionaries with `version`, `source`, `kind`, `timestamp`, `payload`.

A future ADS-B decoder can emit `kind="position"` with geographic fields in the
payload. A map tool can consume this event independently, manage offline tiles,
and incorporate GPS as another event producer. Those field conventions and map
dependencies should be specified when that feature is implemented.

New device families can extend `SoapyProvider` discovery or supply a provider
following `DeviceProvider`; the worker currently registers RTL-SDR and HackRF.
The source factory is receive-only. Remote transport and transmit need explicit
new interfaces rather than exposing raw device handles to widgets.

## Presentation and persistence

Semantic theme tokens live in `ui/theme.py`; spacing and type sizes use Kivy dp/sp.
The initial density targets 480 logical units across the physical portrait panel.
Main frequency and receive actions have approximately 8 mm touch heights;
secondary controls are smaller. Spectrum drawing and texture uploads occur only
on Kivy's thread. The waterfall uses a 512 × 256 RGB circular texture, avoiding
full-history copies for each new row. It keeps about 13 seconds at 20 rows/sec.

Startup reconciles recording sidecars with the library, recovering captures finalized
while the UI was closing.

SQLite schema version 1 stores preferences, bookmarks and recording metadata under
XDG data paths. Schema versions newer than the application are rejected. Frequency
and mode are restored, but receiving and antenna bias are never automatically
started. Future schema changes need transactional migrations and upgrade tests.
