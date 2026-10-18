-- Normal MULTISET table: duplicates allowed, same semantics as Snowflake.
CREATE MULTISET TABLE sales.orders, NO FALLBACK, NO BEFORE JOURNAL, NO AFTER JOURNAL
(
    order_id      INTEGER NOT NULL,
    customer_id   INTEGER NOT NULL,
    order_date    DATE NOT NULL,
    order_amount  DECIMAL(12,2),
    status_code   CHAR(1) DEFAULT 'N',
    created_ts    TIMESTAMP(0)
)
PRIMARY INDEX (order_id);
