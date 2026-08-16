# marklogic-tool

CLI to query, verify, deploy and destroy MarkLogic Server configuration via the REST and Management APIs.

## Installation

```bash
pip install marklogic-tool
```

The tool needs Python 3.13 or later.

## Commands

| Command | Function |
|---|---|
| `status` | Reports server reachability, version and health |
| `count` | Counts documents, and reports the identity that counted them |
| `count-unpermissioned` | Counts documents that have an empty permission set |
| `search` | Searches documents |
| `doc` | Reads and writes documents |
| `eval` | Runs code on the server |
| `db` | Operates on databases |
| `server` | Operates on app servers |
| `group` | Operates on cluster groups |
| `host` | Operates on cluster hosts |
| `config` | Manages the tool configuration |
| `deploy FILE` | Applies a configuration declaration |
| `destroy FILE` | Removes the objects in a declaration |
| `test` | Runs diagnostic commands |

Two options apply to all commands:

- `--output` (`-o`) selects `json`, `table` or `raw`.
- `--profile` (`-P`) selects a configuration profile.

## Exit codes

The exit code is a contract. An agent can branch on it. The error message text is
not a contract. Match on the exit code, and never on the wording.

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | The operation is complete |
| 1 | `GENERAL` | An undocumented failure. No documented result gives this code |
| 2 | `USAGE` | The command invocation is wrong |
| 3 | `INPUT` | The configuration, the authentication or the request is wrong |
| 4 | `OUTPUT` | The tool cannot parse the response |
| 5 | `NETWORK` | The server is unreachable, or it returned a 5xx status |
| 6 | `TIMEOUT` | The deadline elapsed, or the server returned `XDMP-EXTIME` |
| 7 | `VERIFICATION_FAILED` | A verification ran correctly and found offending documents |
| 8 | `BLOCKED` | The tool cannot change an object safely, and left it unchanged |

Codes 7 and 8 report a result. Codes 1 to 6 report a failure of the tool. Do not
retry code 7 or code 8.

Code 1 maps to no documented outcome. A caller that reads code 1 as "not found"
reads the wrong code. A missing resource gives code 3.

A refusal gives an error. The tool does not continue with less precision. It does
not use the administrator credential in place of an identity that it cannot
resolve. HTTP 400 and HTTP 409 raise, and both give code 3. Neither status
returns success.

### Per-command exit codes

| Command | 0 | 2 | 3 | 6 | 7 |
|---|---|---|---|---|---|
| `status` | Healthy | — | The profile or the credential is wrong | Deadline or `XDMP-EXTIME` | Reachable, but unhealthy |
| `count` | Counted | `--as-user-secret` without `--as-user` | `rest_port` is unset, or the identity is unresolvable | Deadline or `XDMP-EXTIME` | — |
| `count-unpermissioned` | No offenders, or `--no-gate` | `--as-user` (refused here) | The URI lexicon is off | Deadline or `XDMP-EXTIME` | Offenders found |

`count-unpermissioned` is a gate. By default, one offender gives exit code 7. Use
`--no-gate` to report offenders with exit code 0.

Code 4 applies when a response has no usable `total`. The tool refuses the
response. It does not use 0 in place of the count, because an unpermissioned
corpus also reports 0.

## Verification output

`count-unpermissioned --output json` gives the schema
`marklogic-tool/unpermissioned/1`. Read the `schema` field to identify the
payload.

| Field | Content |
|---|---|
| `unpermissioned` | The number of documents that have an empty permission set |
| `total_scanned` | The number of URIs examined |
| `method` | `exhaustive` or `sampled` |
| `evidence` | `true` for an exhaustive scan only |
| `findings_are_sound` | `true`, because a sampled scan can only report too few offenders |
| `uris` | The offending URIs in the output |
| `uris_requested` | The number given to `--list-uris`, or `0` |
| `uris_listing` | `not_requested`, `truncated` or `complete` |
| `identity`, `identity_source`, `endpoint` | The identity that counted, and the endpoint it used |
| `gate`, `exit_code` | The gate state, and the exit code of the process |

`uris_listing` has three values:

- `not_requested` — the command did not include `--list-uris`. The `uris` field is
  empty because the command asked for no URIs.
- `truncated` — there are more offenders than the requested number. The
  `unpermissioned` field gives the correct total.
- `complete` — the `uris` field contains all the offenders.

## Acceptance procedure

Use both steps. One step alone is not sufficient.

```bash
# 1. Count as the administrator and as the application identity.
#    Both counts must use the same endpoint.
marklogic-tool -o json count -d example-content
marklogic-tool -o json count -d example-content --as-user writer

# 2. Show that each document is visible to a non-administrator identity.
marklogic-tool count-unpermissioned -d example-content; echo "exit=$?"
```

The procedure passes when these three conditions are true:

- The two counts are equal.
- The `endpoint` field is identical in both counts.
- `count-unpermissioned` reports 0 and gives exit code 0.

The administrator sees all documents. Therefore a count as the administrator
cannot show this defect. Two different app servers can address different content
databases. Therefore both counts must use the same endpoint. When `rest_port` is
set, `count` uses the REST instance for both counts.

A sampled scan (`--sampled N`) is not evidence. A sampled scan can only report
too few offenders. The offenders that it reports are correct, but a result of 0
shows nothing.

## Credentials

Do not write a password in a declaration or in a profile. Write a reference. The
tool resolves the reference when it needs the password. There are three schemes:

| Reference | Source |
|---|---|
| `env:VAR_NAME` | The environment variable `VAR_NAME` |
| `ssm:/path/to/parameter` | AWS SSM Parameter Store, with decryption |
| `profile:NAME` | Another identity in the same profile |

The set is closed. The tool refuses any other value where it needs a reference.

References occur in two places:

- `users[].password` in a declaration that you give to `deploy`.
- `[profiles.<name>.identities]` in your configuration. `--as-user` resolves
  against this map.

```toml
[profiles.<profile-name>.identities]
<app-writer-username> = "env:<ENV_VAR_NAME>"
<app-reader-username> = "ssm:/<org>/<env>/marklogic/<instance>/<identity>-password"
```

### AWS permissions

An SSM parameter that holds a password is a SecureString. The tool requests it
with decryption. Therefore the identity that runs the tool needs two permissions:

- `ssm:GetParameter` reads the parameter.
- `kms:Decrypt` decrypts it, on the KMS key of the parameter.

If `kms:Decrypt` is absent, `GetParameter` is successful and the decryption
fails. The error looks like a wrong parameter path. If the path is correct and
the resolution fails, examine the KMS permission.

Give the permission for a path prefix. Do not use `*`.

```
arn:aws:ssm:<region>:<account-id>:parameter/<org>/<env>/marklogic/*
```

The tool reads only the parameters that the declaration and the profile name. It
does not search the store. Therefore a prefix is sufficient.

### The tool does not use a different credential

If the tool cannot resolve a reference, the command fails. The error names the
identity. The tool does not use the profile credential in place of it.

This behavior is a security property. The administrator sees all documents.
Therefore a check that continues as the administrator reports no offenders, and
hides the defect.

An empty reference is an error. `env:` with no variable name, and a variable that
contains only spaces, both give an error. The tool does not use an empty
password.

### The tool does not print a credential

A resolved password does not occur in the output, in the logs or in a trace. The
module `core/secrets.py` is the only place that resolves a password. The tool
registers each resolved value with one redaction function. The log processor and
the crash-report filter both use that function. Therefore the tool removes the
value from all fields.

For an `ssm:` reference, the tool gives the parameter name as an argument. It
does not use a shell. Therefore the value does not occur in a command line or in
the shell history.

## Telemetry

The tool sends crash reports to a Sentry project. It also sends a sample of
performance traces. The sample rate is 3 percent. The reports show crashes and
performance changes.

Two mechanisms keep credentials out of the reports:

- The tool does not collect the local variables of a stack frame
  (`include_local_variables=False`). The frames that fail are transport frames.
  The variables in those frames hold the credential and the outbound headers.
- The tool passes each outbound string through the redaction function that the
  logs use (`before_send`). This includes messages, breadcrumbs and context.

The tool does not collect document content.

A person reads the crash reports, and uses them to correct defects.

To send nothing, set `MARKLOGIC_TOOL_DISABLE_TELEMETRY` to `1`, `true` or `yes`.
The value is not case-sensitive. The tool then does not start a Sentry client.
Any other value, and no value, keeps telemetry active.

Unit tests cover the redaction function. No live event has contained a
credential, therefore no live event has tested the function. A live test
confirmed that the tool does not collect local variables.
