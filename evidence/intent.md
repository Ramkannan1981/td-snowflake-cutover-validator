# Intent

## Problem
In a Teradata to Snowflake migration, both systems run in parallel while data
marts and consumers move across. The hardest decision is the cutover: when is
it safe to switch consumers off Teradata? In my experience, cutover gets delayed
when "ready" is not defined by agreed, objective checks.

## What this slice does
A local command-line tool that:
1. Converts Teradata DDL to Snowflake SQL, flagging anything it can't convert
   safely as NEEDS_REVIEW instead of guessing.
2. Loads synthetic source (Teradata) and target (Snowflake) data.
3. Reconciles them (row counts, sums, nulls, duplicates).
4. Produces a GO / NO-GO cutover report with a run ID.

## Intended users
Migration engineers and the cutover decision group (data owners, platform lead).

## Assumptions
- Synthetic data only; DuckDB stands in for both systems locally.
- A small sample of tables. At larger scale the logic stays the same; the table
  list comes from configuration.
- Teradata DDL is available as exported text files.

## Constraints
- 3-hour build timebox.
- Must run locally from a clean checkout, with no credentials, paid accounts
  or Westpac data.
- Synthetic data only.
- Built with an AI coding agent as the main implementation tool.

## Out of scope
- Converting ETL jobs, stored procedures or dashboards
- Full Teradata feature coverage
- Running against real Snowflake in the core path (optional live mode only)

## Success criteria
- Runs locally from a clean checkout using the README
- Unsafe conversions are flagged, never silently converted
- Known data issues (e.g. duplicate rows from SET tables) are detected
- Report clearly states GO or NO-GO, with reasons

## Summary

| Area | In this slice |
|---|---|
| DDL conversion | Common cases + NEEDS_REVIEW for unsupported |
| Data load | Synthetic dual feed (source and target) |
| Reconciliation | Counts, sums, nulls, duplicates |
| Decision | GO / NO-GO with reasons and run ID |
| Snowflake | SQL validated against Snowflake dialect; live run not included (see decisions.md) |
| Out of scope | ETL, stored procedures, dashboards |