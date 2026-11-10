# ADR-005: Native apps

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

## What the PWA cannot do reliably

| Need | PWA today | Covered by an existing Subsonic client? |
|---|---|---|
| Listen: background audio, lock-screen controls | Works (Media Session); iOS can suspend a backgrounded PWA between tracks | Yes (Symfonium, Amperfy, Substreamer, DSub) |
| Listen: large offline library | Cache API, capped per device, HTTPS only | Yes, their core feature |
| CarPlay / Android Auto | Not possible | Yes (Symfonium, Amperfy) |
| Desktop media keys | Works in Chromium/Firefox via Media Session | Yes (Feishin) |
| **Run: tempo lock with the screen off** | `playbackRate` keeps working while audio plays; the refill/cadence logic runs in throttled timers, least reliable on iOS | **No.** No Subsonic client does pitch-preserving tempo lock to a cadence |
| **Run: phone / watch cadence sensors** | No step-sensor API on iOS; `DeviceMotion` needs a foreground page | **No** |

So Listen is solved by third-party clients as soon as the Subsonic API is complete (ADR-003).
The only real gap is Run with the screen off and live cadence from sensors.

## Options

| | (1) No native app; PWA + Subsonic clients | (2) Capacitor wrapper around the existing SPA | (3) Focused native Run client (Kotlin or Swift, or Flutter/React Native) |
|---|---|---|---|
| Closes the Run gap | No (mitigated: "Run - preset" virtual playlists already expose cadence-built queues to Subsonic clients at native speed) | Mostly: native audio session + background mode + step-sensor plugin, same UI code | Fully, including watch |
| Maintenance for a solo maintainer | None | One more build target, store accounts ($99/yr Apple), plugin upkeep | A second codebase and release train per platform |
| Can be verified unattended | n/a | No: needs real devices | No |

## Recommendation

**(1) now, (2) later if Run-with-screen-off proves to be a real daily problem.** Nothing native is
built in this round: it cannot be tested without devices, and the Subsonic work makes every
existing client a first-class Listen app for free. What does get done: PWA hardening that needs no
device (a Run refill that does not depend on foreground timers, where the code allows), and a
written spike for (2) so the decision can be made with real-device data.
