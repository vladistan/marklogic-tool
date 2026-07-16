# marklogic-tool

A read-only command-line tool for querying MarkLogic Server over its REST API.
Invoked as `marklogic-tool`, it is built for exploring databases, servers, and
hosts, retrieving documents, running ad-hoc XQuery/JavaScript, and debugging
deployments — without touching a full deployment toolchain.

marklogic-tool is intended for **exploration, debugging, and lightweight
administration**. It is not a data-loading, deployment, or CI/CD tool.

## Installation

```bash
pip install marklogic-tool
```

This installs the `marklogic-tool` console script. Python 3.13+ is required.

## Quick Start

```bash
marklogic-tool db list                          # list all databases
marklogic-tool server list                      # list all app servers
marklogic-tool host list                        # list cluster hosts
marklogic-tool doc get /path/doc.xml            # retrieve a document
marklogic-tool search -d my-database "term"     # search documents in a database
marklogic-tool eval "xdmp:hosts()"              # run an XQuery expression
marklogic-tool eval -j "xdmp.hosts()"           # run a JavaScript expression
marklogic-tool config list                      # show available connection profiles
```

### Argument ordering

For `search` and `eval`, **options must come before the positional argument**
(the query or code). Options placed after the positional argument are
interpreted as subcommands and will fail:

```bash
marklogic-tool search -d MyDB -n 5 "term"       # correct
marklogic-tool eval -d MyDB "xdmp:hosts()"      # correct

marklogic-tool search "term" -n 5               # wrong — '-n' is read as a command
```

## Commands

### Discovery

```bash
marklogic-tool db list                          # list databases
marklogic-tool db show <name>                   # forests, indices, settings
marklogic-tool server list                      # list app servers
marklogic-tool server show <name>               # port, root, modules, auth type
marklogic-tool host list                        # list hosts
marklogic-tool host show [<name>]               # host details
marklogic-tool group list                       # list groups
marklogic-tool group show <name>                # group details
```

### Documents

```bash
marklogic-tool doc get <uri> [-d database] [-f xml|json|text|binary] [--metadata]
marklogic-tool search [-d database] [-c collection] [-n pageLength] <query>
```

Always pass `-d <database>` to `search` — the default database is typically
empty.

### Code execution

```bash
marklogic-tool eval "<code>"                    # XQuery (default)
marklogic-tool eval -j "<code>"                 # JavaScript
marklogic-tool eval -f script.xqy              # read code from a file
marklogic-tool eval -d my-database "<code>"    # target a specific database
marklogic-tool eval --vars '{"x":1}' "<code>"  # pass external variables
```

`eval` defaults to the App-Services database, so pass `-d <database>` when you
need to run against a specific content database.

## Configuration

Connection settings are resolved from **layered sources**, in order of
increasing precedence:

1. **TOML config profiles** — the base layer
2. **Environment variables** — override individual profile values
3. **CLI flags** — override per invocation (highest precedence)

### Config file and profiles

Profiles live in a TOML file at `~/.config/marklogic-tool/config.toml`. Copy the
bundled `config.example.toml` to that location and edit it:

```toml
default_profile = "local"

[profiles.local]
host = "localhost"
port = 8000
manage_port = 8002
username = "admin"
password = "admin"
auth_method = "digest"
timeout = 30
default_group = "Default"

# [profiles.staging]
# host = "ml-staging.example.com"
# port = 8000
# username = "reader"
# password = "change-me"
# auth_method = "digest"
```

Select a profile per command with `-P` / `--profile` (accepted at any position):

```bash
marklogic-tool -P staging db list
marklogic-tool config list                      # list all configured profiles
marklogic-tool config show                      # show the active profile's details
```

### Environment variables

| Variable           | Overrides                                             |
|--------------------|-------------------------------------------------------|
| `MARKLOGIC_CONFIG` | Path to the config file (overrides the default above) |
| `ML_PROFILE`       | Profile name (overrides `default_profile`)            |
| `ML_HOST`          | Host                                                  |
| `ML_PORT`          | Port                                                  |
| `ML_USERNAME`      | Username                                              |
| `ML_PASSWORD`      | Password                                              |

## Global options

| Flag              | Purpose                                                   |
|-------------------|-----------------------------------------------------------|
| `-P`, `--profile` | Select a connection profile                               |
| `-o`, `--output`  | Output format: `json`, `table`, or `raw`                  |
| `-v`, `--verbose` | Enable debug logging                                      |
| `-q`, `--quiet`   | Suppress warning messages                                 |
| `-V`, `--version` | Print the version and exit                                |

Output auto-detects when `-o` is omitted: `table` for an interactive terminal,
`json` when piped.

## Telemetry

marklogic-tool sends anonymous error and performance data to a hosted Sentry
project by default, which helps surface crashes and regressions. No document
content or credentials are collected.

To opt out, set the `MARKLOGIC_TOOL_DISABLE_TELEMETRY` environment variable to a
truthy value (`1`, `true`, or `yes`):

```bash
export MARKLOGIC_TOOL_DISABLE_TELEMETRY=1
```

## License

MIT — see [LICENSE](LICENSE).
