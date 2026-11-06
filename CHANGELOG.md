# Changelog

## Unreleased — load Arkose MatchKey verification

- Support four specific Arkose API, frame and CDN hosts in both browser egress
  layers, leaving other provider subdomains blocked. Facebook can
  present this provider instead of Google reCAPTCHA during a different session.
- Exercise Google and Arkose synthetic scripts, frames, an API request and image
  dependencies together through the real sandboxed browser and private proxy.
- Retain Facebook-only session confirmation, Google path restrictions,
  private-address rejection and manual verification.

## Unreleased — load Facebook's verification challenge

- Fix the blank Google reCAPTCHA challenge caused by Facebook-only request
  filtering in both Chromium and the host's private proxy.
- Share the host policy and permit the documented reCAPTCHA hosts, with
  `/recaptcha/` path restrictions in Chromium. Confirming a session still
  requires a Facebook URL; general Google pages and other subdomains stay blocked.
- Add policy, private-address and real offline cross-domain frame/script/image
  checks. Verification remains manual; no live CAPTCHA is used in tests.

## Unreleased — smoother remote login browser

- Replace 1.2-second screenshot polling with an authenticated live JPEG stream
  capped at 10 fps, direct compositor capture and lightweight login metadata.
- Batch rapid text/wheel input while preserving click, typing and key order;
  support touch scrolling and avoid replaying ambiguous input requests.
- Pause hidden views, reconnect read-only streams, and provide a decoded snapshot
  fallback without changing session expiry, revocation or browser isolation.
- Add stream authorization/cleanup tests, input queue regressions, browser-flow
  frame-rate/pause checks and an offline real-Chromium capture benchmark.

## Unreleased — self-service setup invitations

- Generate single-use expiring setup links, list active links and recent history,
  and disable access and temporary browsers from the dashboard.
- Consent-based guest onboarding through a real remote Facebook browser,
  encrypted cookie handoff, and an automatic personal Telegram invitation.
- Separate controller and disposable unprivileged sandboxed Chromium containers,
  with a deadline, memory limit, no data/token mounts, and no direct IP network.
- Facebook-only HTTPS egress through an IP-checked Unix-socket proxy; no public
  debugging/VNC ports or recording of inputs/screenshots.
- Atomic claims, independent guest cookies/CSRF, reserved capacity, revocation
  checks at credential commit, and the existing Telegram approval flow.
- Offline browser/container checks and synthetic desktop/mobile guest flows.

## Unreleased — incoming-only capture and combined Telegram downloads

- Exclude the logged-in account's regular/encrypted messages before storage or
  media downloads, and remove quoted copies of its text from incoming records.
- Restrict related actions to known incoming messages; briefly hold unattributed
  deletions in memory for delayed originals. Read-only views hide legacy owner records.
- Send each deletion as one notice: text, an original downloadable attachment with
  a caption, or one ZIP containing multiple files and full message details.
- Keep voice files downloadable, wait for pending media, preserve long text in a
  transcript, and retain duplicate suppression and safe missing-topic repair.

## Unreleased — administrator workspace

- Optional HTTPS admin dashboard for up to six separate Messenger accounts.
- Encrypted cookie/bot configuration, expiring sign-in sessions, and CSRF protection.
- One shared Telegram gateway with destination checks, paced sends, single-use
  invitations, administrator-approved pairing, and per-user disconnect.
- Separate root container manager; web/bot services have no Docker access.
- Mounted runtime worker credentials, independent archives, account lifecycle
  controls, stopped-account retention, and original-service migration/rollback.
- Synthetic isolation/migration tests and a real-browser account setup/mobile check.

## Unreleased — initial open-source edition

- Receive-only regular/encrypted capture with a pinned native bridge.
- Future-events-only admission, per-run counters, resolved names, and content-only logs.
- Observed deletion tracking with original/latest captured text and media.
- Bounded authenticated media downloads and supported view-once wrapper parsing.
- Optional formatted Telegram notices and persistent per-conversation topics.
- Fresh read-only Telegram setup checks and local topic-ID repair.
- Rolling 72-hour retention, recoverable file/journal cleanup, and late-download
  protection; remote Telegram copies remain untouched.
- Ubuntu systemd installation, controlled restarts, and an independent cleanup timer.
- Synthetic tests, Docker/native CI configuration, generic setup guides,
  example configuration, and upstream license/provenance records.
