-- SET table: Teradata silently rejects duplicate rows. Snowflake has no
-- equivalent, so duplicates that Teradata dropped will land in Snowflake.
CREATE SET TABLE sales.customer, NO FALLBACK
(
    customer_id    INTEGER NOT NULL,
    customer_name  VARCHAR(100),
    region_code    CHAR(2),
    signup_date    DATE
)
PRIMARY INDEX (customer_id);
