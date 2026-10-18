# Teradata → Snowflake mapping

A reference for the parts of a Teradata estate that change in a migration.
The last column shows what this repo handles today.

## Scripts and utilities

| Teradata | Snowflake | Notes | In this repo |
|---|---|---|---|
| BTEQ scripts | SnowSQL / Snowflake CLI scripts, or SQL run by the orchestrator | BTEQ mixes SQL with control commands; those need rewriting, not copying | No |
| BTEQ control flow (`.IF ERRORCODE`, `.GOTO`, `.LABEL`) | Snowflake Scripting (`BEGIN … END`, `IF`, `EXCEPTION`) or orchestrator steps | Error handling moves into the procedure or the scheduler | No |
| BTEQ `.EXPORT` / FastExport / TPT export | `COPY INTO @stage` (unload to S3) | Used for the Phase 1 data mart export | Simulated (CSV) |
| BTEQ `.IMPORT` / FastLoad / MultiLoad / TPump / TPT load | `COPY INTO <table>`, Snowpipe, Snowpipe Streaming | MultiLoad error tables → `ON_ERROR` / `VALIDATION_MODE` | Simulated (CSV) |
| Stored procedures (SPL) | Snowflake Scripting procedures (or Python/JavaScript) | Usually needs manual rework | No |
| Macros | Stored procedures or views | | No |

## Tables and DDL

| Teradata | Snowflake | Notes | In this repo |
|---|---|---|---|
| `MULTISET` table | Standard table | | Converted |
| `SET` table (rejects duplicate rows) | No equivalent | Dedupe in the pipeline (e.g. `QUALIFY ROW_NUMBER()`), check in reconciliation | Converted with warning + duplicate check |
| `PRIMARY INDEX` / `UNIQUE PRIMARY INDEX` | None: automatic micro-partitions | PK/UNIQUE constraints are not enforced in Snowflake | Dropped, noted |
| Partitioned primary index (PPI) | Clustering key, only for very large tables | | No |
| Secondary / join indexes | None: clustering, search optimisation or materialised views | | No |
| `COLLECT STATISTICS` | Not needed: automatic | | No |
| `FALLBACK`, `JOURNAL` | Not applicable: Time Travel and Fail-safe | | Dropped, noted |
| Volatile tables | `TEMPORARY` tables | | No |
| `NOT CASESPECIFIC` | Case-sensitive by default; use collation or normalise case | Silent join/filter differences if missed | NEEDS_REVIEW |
| `PERIOD(DATE)` | Two `DATE` columns (begin, end) | Changes the data model; needs agreement | NEEDS_REVIEW |
| `FORMAT` clause | Format at query time (`TO_CHAR`) | | NEEDS_REVIEW |
| `CHARACTER SET LATIN / UNICODE` | All strings UTF-8 | | NEEDS_REVIEW |

## Operations

| Teradata | Snowflake |
|---|---|
| Workload management (TASM) | Separate virtual warehouses, resource monitors |
| Viewpoint monitoring | Snowsight, Query History, `ACCOUNT_USAGE` views |
| DBA-managed space and tuning | Platform admin and FinOps: warehouse sizing, cost monitoring, RBAC |

## How code moves

Data is exported and loaded. Code is converted: an automated converter
(for example Snowflake's SnowConvert) handles the bulk of BTEQ, DDL and
procedures; anything it flags is fixed by hand; results are then reconciled
against Teradata before cutover. This repo demonstrates the first and last
parts on DDL: conversion that refuses to guess, and reconciliation.