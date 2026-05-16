# Demo Images

Drop captured demo screenshots and the hardware photo into this directory
using the filenames below. The README's "Demo" section already references
them by relative path.

## Expected filenames

| Filename | What it shows | Capture notes |
|----------|---------------|---------------|
| `hardware-pi-arduino.jpg` | Photograph of the Raspberry Pi + Arduino + sensor wiring on a desk. | Landscape orientation works best. JPEG to keep size down. |
| `demo-whatsapp-query.png` | A WhatsApp chat where you send `@ssa what is the current temperature?` and the agent replies with the live reading. | Use a contact named something neutral (e.g. `demo-user`). Crop tight. |
| `demo-whatsapp-alarm.png` | An unsolicited alarm message from the daemon, e.g. "Temperature 32.4 °C is above the configured 30 °C threshold." | Trigger by temporarily lowering the threshold via the admin command path. |
| `demo-whatsapp-admin.png` | The `@ssa <admin-password> set temp 30` exchange and the confirmation reply. | Redact the real password in the screenshot. |
| `demo-api-latest.png` | Terminal output of `curl -s http://127.0.0.1:8080/latest \| jq`. | Plain dark terminal; one screen of JSON. |
| `demo-ssa-health.png` | Terminal output of `ssa health`. | Should show three services up and a recent observation timestamp. |
| `demo-rejected-line.png` | `tail -n 5 logs/rejected-lines.jsonl` after sending an invalid serial line (e.g. a duplicate `TEMP=` field). | Demonstrates that the validation guardrail rejects bad input rather than poisoning the log. |

## Redaction checklist before committing

- [ ] Blur or replace any real WhatsApp phone numbers and display names.
- [ ] Strip or redact the real `SSA_ADMIN_PASSWORD` and `GEMINI_API_KEY`
      from any terminal output you capture.
- [ ] Avoid showing your home directory path in shell prompts; either use a
      generic `$` prompt or `~`-relative paths.

## Suggested image sizes

- Screenshots: PNG, max width ~1200px. Keep file size under ~300 KB each
  (use `oxipng -O2` or `pngquant --quality=70-90` if needed).
- Hardware photo: JPEG, max width ~1600px, quality 80. Aim for < 500 KB.
