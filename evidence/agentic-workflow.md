# Agentic workflow

**Tools:** Claude Code (Opus 5.5, auto permission mode) for implementation;
Claude chat for planning and reviewing agent output.

**Approach:** one step at a time, tests before code. The agent does not run
git; I run the tests myself, review the output, and make every commit.

## Prompt 1 – DDL conversion with safe abstain
Asked the agent to create 3 sample Teradata DDL files (MULTISET, SET, and an
unsupported PERIOD type), write pytest tests first, then implement the
converter with sqlglot, returning a typed result (CONVERTED /
CONVERTED_WITH_WARNING / NEEDS_REVIEW).

### My review / corrections
- The agent found that sqlglot's Teradata-to-Snowflake output passes
  Teradata-only syntax (PRIMARY INDEX, PERIOD, NOT CASESPECIFIC) through
  unchanged, so it looks converted but isn't valid Snowflake. I accepted its
  allowlist approach: only known-safe types and attributes are converted;
  everything else is NEEDS_REVIEW.
- Confirmed the tests check the output for Teradata-only keywords, since
  sqlglot's Snowflake parser alone also accepts PRIMARY INDEX.
- Kept NOT CASESPECIFIC as NEEDS_REVIEW, based on my migration experience:
  Teradata compares these columns case-insensitively and Snowflake doesn't by
  default, which causes silent mismatches in joins and filters.

## Prompt 2 – Synthetic dual-feed data and DuckDB adapter
Asked the agent to create synthetic source (Teradata export) and target
(Snowflake load) CSVs with planted migration issues in the target only, a
config file listing tables, and a DuckDB adapter behind an interface, with
tests first.

### My review / corrections
- Checked the planted issues are really in the target data by diffing source
  and target: duplicate row (SET table), case change, rounding difference
  (100.01 vs 100.00), NULL vs empty string. One table left fully matching.
- Confirmed the table list comes only from config/tables.json, so scaling to
  more tables means adding config, not changing code.
- Snowflake adapter deliberately not implemented; interface leaves a clear
  plug-in point.
