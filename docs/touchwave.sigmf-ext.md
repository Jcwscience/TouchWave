# TouchWave SigMF extension

Version: **0.1.0**. Namespace: **touchwave**.

This optional extension preserves receiver settings and capture diagnostics for
TouchWave's recording library. Reading the RF samples requires only core SigMF
metadata; consumers may ignore this extension.

The recording declares this namespace in the global `core:extensions` array:

```json
{"name": "touchwave", "version": "0.1.0", "optional": true}
```

## Global fields

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `touchwave:recording` | No | object | TouchWave's completed-recording metadata |

The recording object contains `kind` (`iq`), `path` (local original path), `rate`
(samples/sec), `count` (complex samples), `started` (Unix seconds), `seconds`
(sample duration), `source` (driver ID), `config` (ReceiverConfig dictionary),
`reason` (completion reason), `discontinuities` (array), and `overrun_reporting`
(text describing reporting limits). An optional `metadata_error` string records
an error encountered finalizing metadata.

A discontinuity object contains `time` (Unix seconds) and `reason` (text). Empty
`discontinuities` does not establish uninterrupted hardware capture; some drivers
do not report dropped samples. The local `path` is advisory: dataset discovery
uses the standard matching `.sigmf-data` filename, allowing files to be moved.

This extension adds no capture-segment or annotation fields and changes no dataset
encoding. IQ remains interleaved little-endian complex float32 (`cf32_le`).

See the [SigMF specification](https://sigmf.org/) for core fields and namespace rules.
