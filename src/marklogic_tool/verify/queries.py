"""The detection expression, and the only place it lives.

Both queries compose `PERMISSION_FILTER` rather than repeat it, so no second copy can drift.

Sampling applies the same filter. A sample can miss offenders but never invent one.
"""

PERMISSION_FILTER = "[fn:empty(xdmp:document-get-permissions(.))]"

DETECTION_PREDICATE = f"cts:uris(){PERMISSION_FILTER}"

_QUERY = """
declare variable $sample-size as xs:integer external;{extra_declare}
let $scanned := {scan}
let $offenders := $scanned{permission_filter}
return (
  fn:count($offenders),
  fn:count($scanned),
  fn:subsequence($offenders, 1, $sample-size)
)
"""

UNPERMISSIONED_COUNT_AND_SAMPLE = _QUERY.format(
    extra_declare="",
    scan="cts:uris()",
    permission_filter=PERMISSION_FILTER,
).strip()

UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE = _QUERY.format(
    extra_declare="\ndeclare variable $scan-limit as xs:integer external;",
    scan="fn:subsequence(cts:uris(), 1, $scan-limit)",
    permission_filter=PERMISSION_FILTER,
).strip()
