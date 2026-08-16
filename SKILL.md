# marklogic-tool

CLI to query, verify, deploy and destroy MarkLogic Server configuration via the REST and Management APIs. Invoked as `marklogic-tool`.

## When to Use / When NOT to Use

| Use marklogic-tool for | Do NOT use for |
|------------------------|----------------|
| Exploring databases, servers, hosts | CI/CD pipelines (use ml-gradle) |
| Finding indices and configuration | Data loading (use mlsync, rdf-loader) |
| Debugging deployments | Complex SPARQL queries (use sparql-tool) |
| Performance troubleshooting | Schema/code deployment |
| Simple admin (accounts, backups) | Bulk document operations |
| Checking cluster health | |

## CRITICAL: Argument Ordering

**Options MUST come BEFORE positional arguments** for `search` and `eval`:

```bash
# CORRECT — options before query/code
marklogic-tool search -d MyDB -n 5 "term"
marklogic-tool eval -d MyDB "xdmp:hosts()"

# WRONG — options after positional arg are treated as subcommands
marklogic-tool search "term" -n 5              # ERROR: No such command '-n'
marklogic-tool eval "code" -d MyDB             # ERROR: No such command '-d'
```

## Quick Reference

```bash
marklogic-tool db list                          # list all databases
marklogic-tool server list                      # list all app servers
marklogic-tool doc get /path/doc.xml            # retrieve a document
marklogic-tool search -d my-database "term"     # search documents
marklogic-tool eval "xdmp:hosts()"              # run XQuery
marklogic-tool eval -j "xdmp.hosts()"           # run JavaScript
marklogic-tool config list                      # show available profiles
```

## Commands

### Discovery

```bash
marklogic-tool db list [-P profile]
marklogic-tool db show <name>                   # details: forests, indices, settings
marklogic-tool server list
marklogic-tool server show <name>               # port, root, modules, auth type
marklogic-tool host list
marklogic-tool host show [<name>]
marklogic-tool group list
marklogic-tool group show <name>
```

### Documents

```bash
marklogic-tool doc get <uri> [-d database] [-f xml|json|text|binary] [--metadata]
marklogic-tool search [-d database] [-c collection] [-n pageLength] <query>
```

**Always specify `-d database`** for search — the default database is typically empty.

### Code Execution

```bash
marklogic-tool eval "<code>"                    # XQuery (default)
marklogic-tool eval -j "<code>"                 # JavaScript
marklogic-tool eval -f script.xqy              # from file
marklogic-tool eval -d my-database "<code>"    # target specific database (options BEFORE code)
marklogic-tool eval --vars '{"x":1}' "<code>"  # pass external variables
```

**Always specify `-d database`** when targeting a content database — eval defaults to the App-Services database (usually "Documents").

### Configuration

```bash
marklogic-tool config list                      # list all profiles
marklogic-tool config show                      # show active profile details
```

## Profiles and Connection

- List available profiles: `marklogic-tool config list`
- Select profile per command: `marklogic-tool -P staging db list` (works at any position)
- NEVER read `~/.config/marklogic-tool/config.toml` directly — always use `marklogic-tool config` commands
- Always verify correct profile before any write/destructive operation
- Ask user to confirm profile before destructive operations

## Output Formats

| Flag | Use case |
|------|----------|
| `-o json` | Agent processing — structured, parseable |
| `-o table` | Presenting results to users |
| `-o raw` | Unformatted server response |
| (none) | Auto-detects: table for TTY, json for pipes |

When processing output internally, always use `-o json`. When showing results to the user, use `-o table` or let auto-detection handle it.

## Operations Table

| Operation | Built-in command | Needs eval |
|-----------|-----------------|------------|
| List databases | `marklogic-tool db list` | No |
| Database details/indices | `marklogic-tool db show <name>` | No |
| List servers | `marklogic-tool server list` | No |
| Server config | `marklogic-tool server show <name>` | No |
| Cluster hosts | `marklogic-tool host list` | No |
| Retrieve document | `marklogic-tool doc get <uri>` | No |
| Search documents | `marklogic-tool search <query>` | No |
| Count documents | No | `xdmp:estimate(doc())` |
| List collections | No | `cts:collections()` |
| Check index status | No | `admin:database-get-range-element-indexes(...)` |
| Forest status/stands | No | `xdmp:forest-status(xdmp:forest("name"))` |
| Clear database | No | `xdmp:collection-delete("all")` or admin API |
| Create/delete users | No | `sec:create-user(...)` / `sec:remove-user(...)` |
| Trigger backup | No | `xdmp:database-backup(...)` |
| Check long-running requests | No | `xdmp:server-status(...)` |
| Cancel request | No | `xdmp:request-cancel(...)` |

## SPARQL Endpoint Discovery

Find which servers have REST API (and therefore SPARQL) enabled:

```bash
marklogic-tool eval 'xquery version "1.0-ml";
import module namespace admin = "http://marklogic.com/xdmp/admin" at "/MarkLogic/admin.xqy";
let $config := admin:get-configuration()
for $id in admin:get-appserver-ids($config)
let $port := admin:appserver-get-port($config, $id)
let $rewriter := admin:appserver-get-url-rewriter($config, $id)
where fn:contains($rewriter, "rest-api")
return fn:concat(admin:appserver-get-name($config, $id), " | port:", string($port))'
```

Servers with `/MarkLogic/rest-api/rewriter.xml` expose SPARQL at `http://host:<port>/v1/graphs/sparql`.

The database must have **triple index enabled** for SPARQL to return results.

Hand off complex SPARQL queries to **sparql-tool** — marklogic-tool is not designed for multi-line SPARQL with prefixes, OPTIONAL blocks, or federated queries.

## Safety Rules

1. **Never delete databases with documents** without explicit user confirmation
2. **Before dropping a database**, check size and document count first:
   ```bash
   marklogic-tool eval "xdmp:estimate(doc())" -d target-database
   ```
3. **Triple-check destructive/irrecoverable operations** — confirm with user each time
4. **On production profiles**, default to read-only behavior — only run queries, never mutations, unless user explicitly requests
5. **Always verify profile** before any write operation: `marklogic-tool config list` then confirm with user

## Per-command exit codes

| Command | 0 | 2 | 3 | 6 | 7 |
|---|---|---|---|---|---|
| `status` | reachable and healthy | — | profile/auth refused | deadline or `XDMP-EXTIME` | reachable but **unhealthy** |
| `count` | counted | `--as-user-secret` without `--as-user` | unset `rest_port`, unresolvable identity | deadline or `XDMP-EXTIME` | — |
| `count-unpermissioned` | zero offenders, or `--no-gate` | `--as-user` (refused here) | URI lexicon off | deadline or `XDMP-EXTIME` | offending documents found |

`count-unpermissioned` is a **gate, not a report**. With the gate on (the
default) any finding exits 7, so a checkpoint fails loudly rather than being
piped into something that ignores it. Exit 4 (`OUTPUT`) reaches any command whose
response carried no usable `total` — a missing count is refused, never defaulted
to 0, because 0 is also what an unpermissioned corpus reports.

## Acceptance procedure

This is **the** acceptance procedure for the permission model. Neither half
proves anything alone.

```bash
# 1. Count as admin and as the application identity, over the IDENTICAL endpoint.
marklogic-tool -o json count -d example-content
marklogic-tool -o json count -d example-content --as-user writer

# 2. Prove no document is invisible to every non-admin identity.
marklogic-tool count-unpermissioned -d example-content; echo "exit=$?"
```

Pass condition: the two counts **agree**, their `endpoint` provenance fields are
**identical**, and `count-unpermissioned` reports 0 and exits 0.

Why both halves and why the same endpoint: an admin-authenticated check reports
health because admin sees everything, and comparing two different app servers is
not evidence — App-Services on 8000 and the REST instance on `rest_port` address
different content databases. `count` therefore traverses the REST instance for
both halves whenever `rest_port` is set. If the counts disagree, the application
cannot see its own data; that is the production defect, observed.

A sampled run (`--sampled N`) is **not** evidence. Sampling can only under-report,
so findings it reports are sound, but a sampled zero proves nothing — sampled
inspection is what concealed the original defect for eleven months.

## Error Handling

| Exit code | Meaning | Agent action |
|-----------|---------|--------------|
| 0 | Success | Proceed |
| 1 | Undocumented failure | Report to user — no documented outcome maps here; treat as a bug |
| 2 | Usage/syntax error (invocation-shaped refusal) | Fix command syntax (likely wrong arg order) |
| 3 | Auth/input/config error (config-shaped refusal), including not-found | Check profile, credentials, profile keys |
| 4 | Output/parse error | Server replied in an unexpected shape — report |
| 5 | Network error, connection refused, or server error | Host reachable but port wrong/closed, or 5xx — check port config |
| 6 | Timeout — client deadline or `XDMP-EXTIME` | Host unreachable or query too slow — retry once then report |
| 7 | Verification failed | The check ran correctly **and found offending documents**. This is a finding, not a malfunction — report the count |
| 8 | Blocked | The run refused to act on an object and left it alone — report what was blocked and why |

**Exit 7 and 8 are contract.** 7 means the verification worked and the corpus is bad;
8 means the tool declined to act. Neither is a tool failure, and neither should be
retried. Distinguishing them from 1–6 is the whole point of the split.

- **Retry**: Only transient errors (exit 5, 6). One retry, then report failure.
- **Do not retry**: Refusals (2, 3), parse errors (4), findings (7), blocks (8).
- **Exit code 2 is almost always argument ordering** — move options before the positional argument.
- **HTTP 500 from search** usually means the database lacks REST search indexes (not a tool bug — a server config issue).

## Known Limitations

- `config list` output is plain text (not affected by `-o json`)
- `-v` (verbose) currently produces no additional output (no debug logging implemented in commands)
- `-q` (quiet) only suppresses structlog warnings, not informational stderr like search result counts
- Search requires REST-configured databases — bare databases without REST search indexes return HTTP 500

## Global Options

| Flag | Purpose |
|------|---------|
| `-P <profile>` | Select connection profile |
| `-o json\|table\|raw` | Output format |
| `-v` | Verbose/debug logging |
| `-q` | Quiet — suppress warnings |
| `-V` | Print version |
