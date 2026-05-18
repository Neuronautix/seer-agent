/*
 * sense-rev2.ino — placeholder
 *
 * The actual reference firmware will be dropped in here. The sketch must emit
 * canonical observation lines over serial at 9600 baud, one per line, in the
 * format:
 *
 *   TEMP=<float>;HUM=<float>;[PRESS=<float>;]TS=<UTC ISO-8601 with trailing Z>
 *
 * Example:
 *   TEMP=23.4;HUM=51.2;TS=2026-03-29T11:12:13Z
 *
 * Lines prefixed with '#' are treated as debug output by the host pipeline
 * and ignored. See arduino/sense-rev2/README.md for the full contract and
 * wiring notes.
 */

void setup() {
  // TODO: initialise sensors (e.g., BME280, DHT22) and Serial.begin(9600).
}

void loop() {
  // TODO: sample sensors, format a canonical line, and Serial.println() it.
  // Recommend ~1 sample / second to start.
}
