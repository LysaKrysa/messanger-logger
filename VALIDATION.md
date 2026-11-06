# Validation

## Remote browser responsiveness

- Live-view tests cover binary JPEG streaming, no-cache/same-origin headers,
  guest authorization, active revocation/expiry, and bounded stream concurrency.
- Input tests cover bounded batches, validation before dispatch, Unicode,
  slow-connection ordering, rapid text/wheel coalescing, no ambiguous resend,
  and clearing pending input across invitation changes.
- The HTTPS guest test verifies multiple frames per second, hidden-tab pause
  and resume, and the existing approval and Telegram handoff.
- The real offline browser check verifies batched input, metadata without images,
  and times direct compositor capture while retaining the sandbox/proxy checks.
- All fixtures remain synthetic; no Facebook login or real Telegram send.

## Self-service setup links

- Synthetic tests cover atomic one-time claims, consent, origin/CSRF checks,
  guest/admin separation, capacity reservations, expiry, disabling links,
  credential-commit revocation races, encryption and Telegram pairing isolation.
- Browser-controller tests cover fixed commands, private authentication, one
  active session, hard expiry, prescribed container flags, and public-IP egress.
- The HTTPS browser flow exercises invitation creation, guest setup, remote input
  with a synthetic image, Telegram handoff, administrator approval, mobile layout,
  one-time replay rejection and revocation of an active browser session.
- A separate real Docker smoke check verifies sandboxed Chromium, synthetic input,
  JPEG frames, no IP network, no published ports, unprivileged execution, read-only
  root, bounded memory and the sole read-only proxy-socket mount.
- No tests log into Facebook or send a real Telegram message. Actual Facebook
  remote-login acceptance and device verification must be checked by the owner.

## Incoming-only capture and single-notice downloads

- 187 standalone tests passed with the pinned offline native RPC check, and
  38 dashboard tests passed locally.
- Authenticated sender admission excludes outgoing regular/encrypted messages,
  attachments, owner actions, and quoted owner text before content persistence.
- Synthetic runtime tests verify incoming-only counters, dispatch, out-of-order
  deletion attribution, and legacy owner-record isolation in views and media.
- Telegram tests check exact voice/document bytes, one-message multipart captions,
  pending download completion, safe ZIP members, full long-text transcripts,
  combined upload limits, ambiguous-send suppression, and topic-repair rewinds.
- Tests use synthetic accounts and mocked network requests; no Facebook login
  or real Telegram send is performed. Native transport inputs are unchanged.

## Optional administrator workspace

- 172 standalone tests pass, including the pinned native offline RPC test and
  mounted-credential/gateway configuration checks.
- 34 optional dashboard tests cover encrypted credentials, account identity,
  expiring invitations, approval, destination isolation, sessions, CSRF,
  migration rollback, independent archives, cleanup, and account removal.
- A real HTTPS browser flow verifies sign-in, account creation, hidden saved
  cookies, invitation/approval/start, and mobile layout without horizontal
  overflow. Screenshots use synthetic accounts only.
- Real Docker fixture workers verify separate identities/data, runtime-mounted
  secrets without credential environment metadata, resource limits, graceful
  shutdown, offline cleanup, and scoped removal. The test also exercises a root
  controller using an unprivileged owner's SQLite WAL files.
- ARM64 binary wheels for the pinned cryptography dependency and CFFI were
  verified available for Ubuntu's Python 3.12; native inputs remain unchanged.

These checks make no Facebook login or real Telegram send. Actual Oracle
deployment still requires the operator to configure ingress, verify certificate
trust, and test their own sessions and approved Telegram destinations.

Offline checks completed on Linux amd64, 5 November 2026.

## Current source tree

- All **151 Python unit/integration checks passed** with the compatible native
  binary supplied through `BRIDGE_TEST_BIN`.
- Python syntax, Bash wrapper/installer syntax, SVG XML, workflow/template YAML,
  and relative Markdown document links were checked.
- Native Go source and pinned binary hashes are unchanged. The Python reader
  additionally filters the pinned Signal logger's exact diagnostic prefix and
  makes JSON/encoding failures restartable. The runtime image includes license
  documents; the hotfix also works through the existing full-source bind mount.
- The source-only publication set excludes private configuration, cookies,
  tokens, messages, media, device state, databases, personal server addresses,
  SSH material, build caches, and compiled transport binaries.
- Git ignore rules cover common runtime data and private configuration paths.
  A clean source history is prepared independently of the old application.

## Python coverage

The suite uses synthetic events/cookies and mocked Telegram requests:

- Cookie shape/domain validation and account matching before event storage.
- Native response/event interleaving, bounded parsing, cancellation, redacted
  diagnostics, receive-only RPC exposure, and reconnect-state handling.
- Future-only admission, duplicate suppression, per-run counters, typed text,
  attachment metadata, replies, mentions, edits, reactions, and observed unsends.
- Receipt/typing/presence exclusion and no raw device/media keys in persisted logs.
- Durable SQLite journal/export recovery, schema upgrades, account isolation,
  read-only views, names/usernames, and placeholder rejection.
- Authenticated/decrypted media, integrity checks, size limits, sanitized/private
  atomic files, failed/interrupted downloads, and native RPC scheduling.
- Telegram formatting, UTF-16 offsets, multipart bytes, deletion/all modes,
  view-once opt-in, topic mapping, destination scoping, permissions, and repair.
- Threaded-mode checks refreshing after cooldown and immediate recheck on restart.
  The diagnostic calls only getMe/getChat/getChatMember and prints no credentials.
- Rolling local expiry of messages, related actions, quoted copies, journal/log
  records, media, old snapshots, stale labels, and attempt records.
- Interrupted cleanup recovery, late-download rejection, old replay rejection,
  physical database/WAL compaction fixtures, and offline cleanup locking.
- Distinct permanent/transient exit codes and no auth attempt for invalid config.

No test sends a Messenger message or a Telegram notice. Most connection tests
use a local synthetic RPC subprocess. The native test calls only credential-free
local RPC methods.

## Reader recovery regression

A synthetic 122-byte Signal diagnostic reproduced the reported parser signature:
JSON syntax failure at column 2 on a line beginning with a bracket. The new
reader recognizes only the pinned DefaultLogger severity/timestamp/Go-caller
prefix and continues to process subsequent events and RPC responses. Only a
fixed severity notice is emitted, once per level; diagnostic text is not retained.

Checks cover all emitted severities, UTC/offset timestamps, event/response
interleaving, preservation of log-like text inside actual messages, and rejection
of unrecognized bracketed output. Unrecognized JSON/encoding failures stop the
reader with retryable exit 1; protocol shape errors remain terminal. Existing
auth/checkpoint handling still exits 78 and does not repeatedly retry login.

## Native checks already completed for this exact source

- Built `2.3.4-logger.1` for Linux amd64 and arm64 with verified Go 1.26.10.
- Passed the native Go suite, including Consumer/Raven view-once wrappers,
  malformed/nil transports, ephemeral labels, metadata, and contact lookup.
- Repeated source preparation from clean pinned checkouts and independently
  reproduced the amd64 binary SHA-256.
- Executed amd64 hello/isConnected/disconnect and contract/hash checks without login.
- Checked the cross-compiled arm64 ELF target. It was not executed in development.

Exact source pins and both hashes are in [THIRD_PARTY.md](THIRD_PARTY.md).
The native source did not change during open-source preparation.

## Not established by these checks

- A successful build of the current Dockerfile: no Docker daemon was available
  in the development environment. CI is configured to build and test it.
- Actual systemd installation, boot recovery, or service/timer behavior on a
  target VM. Development did not have a running systemd manager; unit verification
  could not initialize in the restricted filesystem.
- Live authentication, checkpoints, encrypted registration, full conversation
  coverage, attachment resolution/availability, or actual Telegram delivery.
- Execution of the arm64 binary in the development environment.
- CI results for your exact revision: inspect its GitHub Actions run; local
  results and earlier successful CI runs are separate evidence.

A user must test their own current session and destination after deployment.
The project deliberately distinguishes local/offline checks from live-service
compatibility.
