# sense-rev2 — Reference Arduino Sketch

This directory holds the reference firmware that produces serial lines in the
canonical format expected by `scripts/read_serial.py`.

> **TODO:** Drop the actual `sense-rev2.ino` file here. The structure below
> describes what the sketch should emit and how it should be wired so the
> downstream Python pipeline accepts its output.

## Expected Serial Output

The Python ingest layer (`scripts/read_serial.py`) accepts two line formats.
The canonical format is preferred — it is unambiguous, compact, and easier to
parse:

```
TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z
TEMP=23.4;HUM=51.2;PRESS=1008.7;TS=2026-03-29T11:12:13Z
```

Field rules enforced by the parser:

- `TEMP` — temperature in °C, range -40 to 85
- `HUM` — relative humidity in %, range 0 to 100
- `PRESS` — pressure in hPa, range 300 to 1100 (optional)
- `TS` — UTC ISO-8601 timestamp ending in `Z`. If the Arduino has no RTC, omit
  `TS` and the host will stamp the observation on receipt.
- Duplicate or unknown fields cause the line to be rejected.

Serial settings: **9600 baud, 8-N-1, line-terminated with `\n`**. Emit one
observation per line; do not interleave debug output without a clear prefix
(prefix debug lines with `#` so they are ignored).

## Wiring (placeholder)

Document the actual sensor + microcontroller wiring here. Suggested template:

| Pin (MCU) | Sensor | Function |
|-----------|--------|----------|
| `<pin>`   | `<chip>` | `<signal>` |

Include:

- MCU model (e.g., Arduino Uno R3, Nano, ESP32, etc.).
- Sensor module(s) and breakout boards.
- Pull-ups, decoupling capacitors, and any power-supply notes.
- A photograph or schematic if available (`schematic.png` / `schematic.pdf`).

## Building

Document the Arduino IDE / `arduino-cli` invocation and any required
libraries here once the sketch lands.

## Verifying End-to-End

After flashing:

```bash
# Watch a few lines come through
./.venv/bin/python scripts/read_serial.py --device /dev/ttyACM0 --baud 9600

# Confirm they were validated
tail -n 5 logs/validated-observations.jsonl
```

Anything that failed schema or range checks will appear in
`logs/rejected-lines.jsonl` instead.
