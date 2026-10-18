-- Uses PERIOD(DATE), which has no Snowflake equivalent. Typical rewrites
-- (two DATE columns, inclusive vs exclusive end) change semantics, so this
-- must be reviewed by a person rather than auto-converted.
CREATE MULTISET TABLE sales.contract, NO FALLBACK
(
    contract_id     INTEGER NOT NULL,
    customer_id     INTEGER NOT NULL,
    contract_value  DECIMAL(14,2),
    validity        PERIOD(DATE) NOT NULL
)
PRIMARY INDEX (contract_id);
