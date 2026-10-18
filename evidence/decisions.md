# Decisions

## 1. Build the cutover gate, not a full migration tool
**Context:** On a previous Teradata to Snowflake migration for a bank, about
70-80% of dashboards were already on Snowflake by month 10-11, but the final
cutover took several approval rounds and an extra 2-3 months of monitoring.
There was no agreed definition of "ready".
**Decision:** In 3 hours, focus on reconciliation and an objective GO / NO-GO
decision with a saved report per run.
**Trade-off:** DDL conversion covers only common cases.

## 2. Allowlist conversion; anything unknown is NEEDS_REVIEW
**Context:** sqlglot passes Teradata-only syntax (PRIMARY INDEX, PERIOD,
NOT CASESPECIFIC) through to "Snowflake" output unchanged.
**Decision:** Only convert types and attributes known to be safe. Never guess.
NOT CASESPECIFIC goes to review, because Teradata compares those columns
case-insensitively and Snowflake doesn't by default.
**Trade-off:** Real Teradata exports use NOT CASESPECIFIC often, so at scale I
would convert with a warning and an agreed collation/normalisation approach.

## 3. Strict tolerance for amounts
**Decision:** Sum tolerance defaults to 0.005, so a 0.01 rounding difference
fails. For financial amounts I'd rather surface a difference and let data
owners agree a tolerance than hide it. Tolerance is configurable.

## 4. DuckDB locally; no live Snowflake run
**Context:** The brief requires the core path to run with no credentials or
paid accounts. I planned to try a Snowflake trial, but sign-up required a
credit card (see snowflake-trial-card-required.png).
**Decision:** DuckDB stands in for both systems behind an adapter interface.
Converted SQL is checked against the Snowflake dialect in verify.sh.
**Trade-off:** This proves syntax, not Snowflake runtime behaviour. Next step:
run the same suite through a SnowflakeAdapter on a dev account before any
real cutover decision.

## 5. What I deliberately did not build
- Spark ETL for cold data: in a real programme cold data moves via Spark
  jobs; here it is simulated with CSV extracts. The checks don't depend on how
  data was loaded.
- ETL jobs, stored procedures, dashboards, and full Teradata coverage.
- The SnowflakeAdapter and a CI pipeline (described in design.md, not built).
- An LLM agent to investigate failures. In my past migration the checks were
  scheduled in Control-M, but investigating failures was manual and slow.
  That is where I'd add an agent next, with read-only tools, keeping the
  checks themselves deterministic.
- Streaming and data science pipelines: the platform is designed for them
  (see design.md, Future consumers) but they are built when use cases are approved.
