# Contributing

Small, focused changes are easier to review, especially for event decoding,
retention, or forwarding. Thanks for helping improve the project.

## Local workflow

1. Fork the repository and create a branch.
2. Use Python 3.11+ on Linux. No third-party Python packages are required.
3. Run `python3 -m unittest discover -s tests -v`.
4. Run `bash -n` on any shell wrapper you changed.
5. Explain the resulting behavior and the checks run in your pull request.

The native smoke test is skipped unless `BRIDGE_TEST_BIN` points to the pinned
binary. See [native development](docs/NATIVE_BUILD.md) for build reproduction.
Never change an expected checksum simply to make verification pass: reproduce
and explain the underlying source/toolchain change.

## Meaningful checks

- Parser changes should preserve valid events and reject unsupported shapes
  without exposing raw provider payloads.
- Retention changes should account for SQLite, exported files, media, restart
  recovery, and late asynchronous work.
- Telegram changes should use mocks and verify conversation/topic routing.
  Tests must not send real notifications.
- Storage changes should cover interrupted writes and schema migration.

Use synthetic fixtures. Do not commit real cookies, tokens, private message
content/IDs, media, device state, or databases. See [SECURITY.md](SECURITY.md).

## Scope

The adapter is receive-only, future-events-only, and self-hosted. Preserve those
properties unless a proposal explicitly discusses changing them. A passing
build is not proof of live Messenger compatibility: distinguish mocked/offline
checks from observations on your own live account.

## License

Contributions to original project files are under AGPL-3.0-or-later. Preserve
upstream attribution and licenses when integrating third-party material.
See [THIRD_PARTY.md](THIRD_PARTY.md).
