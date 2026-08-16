# Changelog

## 0.0.3 — 2026-08-16

### Documentation

- **The README now carries the exit-code facts that 0.0.2 changed.** Exit 1 maps to no
  documented outcome. HTTP 400 and 409 raise. A response with no usable `total` raises.
  The error message text is not a contract.
- The README is the package description, so these facts appear on the package page. The
  wheel does not carry this changelog, so a user who installs the wheel reads the facts
  in the README.

No behaviour changed in this release.

## 0.0.2 — 2026-08-15

### The tool's scope changed

0.0.1 was a read-only query CLI. **0.0.2 can create, change and remove MarkLogic
configuration** through `deploy` and `destroy`. If you installed 0.0.1 on the strength of
"read-only", read this section before upgrading.

`destroy` is guarded — it refuses a host it was not pointed at, refuses objects that hold
data (forests, databases), and refuses anything whose removal would break a declared
dependency — but it is a teardown command and it exists now.

### Breaking

- **Exit codes remapped.** `NotFoundError` 1 → **3**, `ServerError` 1 → **5**,
  `ParseError` 1 → **4**. Exit 1 is now reserved for undocumented failures: no documented
  outcome maps to it. **Scripts branching on exit 1 must be updated.**
- **HTTP 400 and 409 now raise.** Both clients previously judged only 401, 403, 404 and
  5xx; every other non-success status was returned to the caller as though the request had
  succeeded, so a malformed request rendered as an empty result. Affects `db`, `group`,
  `host`, `server`, `doc`, `eval` and `search`.
- **`search` no longer reports a missing `total` as 0.** It raises `ParseError` (exit 4).
  A defaulted 0 is indistinguishable from a genuine count of 0, and that ambiguity hid a
  production defect.
- **Error message wording changed throughout.** Anything matching on the old strings will
  need updating. The 401/403 wording is preserved.

### Added

- **`deploy` and `destroy`** — declarative configuration from a YAML declaration, with
  `--dry-run`, drift classification, and refusals for anything data-affecting.
- **`count`**, **`count-unpermissioned`**, **`status`**.
- **Exit codes 7** (verification failed) and **8** (blocked).
- **Secret references** — `env:`, `ssm:` and `profile:` resolved by indirection. A literal
  password passed where a reference is expected is refused rather than accepted. Resolution
  failure refuses and never falls back to another credential.
- Config keys `rest_port` and `identities`; env overlays `ML_REST_PORT`, `ML_MANAGE_PORT`.
  `rest_port` has **no default** — a REST-backed command refuses by name when it is unset
  rather than borrowing `port`, because the two can address different content databases.
- `forced` on a plan object, true only when the object applied *because* `--force` was
  given, with its `blocked_reason` retained so the override records what it overrode.

### Telemetry — worth reading before you upgrade

- **Crash reports now go to a self-hosted Sentry instance** rather than a hosted service.
  What is collected has not changed; **where it is sent has.**
- **`MARKLOGIC_TOOL_DISABLE_TELEMETRY` is honoured** — set it to `1`, `true` or `yes` and
  the SDK is never initialised at all, rather than initialised and silenced.
- Local variables are not captured, and every outbound event passes through the same
  redaction that backs logging, so a resolved secret is stripped regardless of the field
  name it appears under.

### Fixed

Fourteen defects that only appeared against real MarkLogic servers, none of which a
green offline test suite could see. The ones that affect you:

- Passwords resolved from `ssm:` references reached the server as the **reference string
  itself** rather than the resolved secret, so created users held a guessable password.
- `--force` could not apply anything at all: the tool told you to use it and it did nothing.
- An `ignore_properties` match recorded the suppression correctly and still refused to
  deploy, so the escape hatch could never serve its purpose.
- `applied: true` in a plan meant "sent", not "confirmed".
- A transient MarkLogic restart during teardown was treated as fatal, and a completed
  removal could surface as a transport error.

## 0.0.1 — 2026-07-16

Initial public release. Read-only MarkLogic REST-API CLI. Opt-out telemetry via
`MARKLOGIC_TOOL_DISABLE_TELEMETRY`. MIT licensed.
