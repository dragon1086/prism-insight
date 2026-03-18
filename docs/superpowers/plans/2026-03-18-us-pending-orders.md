# US Pending Orders Queue + Batch Execution Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Queue US reserved orders that fail due to KIS API time restrictions (before 10:00 KST) and execute them via a 10:05 KST cron batch.

**Architecture:** Add `us_pending_orders` SQLite table to existing DB schema. Modify `buy_reserved_order` to INSERT pending record instead of failing. New batch script reads pending orders and executes them via existing `buy_reserved_order`/`sell_reserved_order`.

**Tech Stack:** Python 3.10+, SQLite (existing), KIS API (existing trading module)

---

### Task 1: Add `us_pending_orders` table to DB schema

**Files:**
- Modify: `prism-us/tracking/db_schema.py`

- [ ] **Step 1: Add table definition**

Add `TABLE_US_PENDING_ORDERS` after `TABLE_US_HOLDING_DECISIONS`:

```python
TABLE_US_PENDING_ORDERS = """
CREATE TABLE IF NOT EXISTS us_pending_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    order_type TEXT NOT NULL,          -- 'buy' or 'sell'
    limit_price REAL NOT NULL,
    buy_amount REAL,                   -- USD (buy only)
    exchange TEXT,                     -- NASD, NYSE, AMEX
    trigger_type TEXT,
    trigger_mode TEXT,
    status TEXT DEFAULT 'pending',     -- pending, executed, failed, expired, cancelled
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    order_result TEXT                  -- JSON result from KIS API
)
"""
```

- [ ] **Step 2: Register table in `create_us_tables` and `async_initialize_us_database`**

Add `("us_pending_orders", TABLE_US_PENDING_ORDERS)` to the tables list.
Add index: `CREATE INDEX IF NOT EXISTS idx_us_pending_status ON us_pending_orders(status)`

- [ ] **Step 3: Commit**

```
feat: add us_pending_orders table to DB schema
```

---

### Task 2: Modify `buy_reserved_order` to queue when time-restricted

**Files:**
- Modify: `prism-us/trading/us_stock_trading.py`

- [ ] **Step 1: Add `_queue_pending_order` helper method**

When `is_reserved_order_available()` returns False, save order to `us_pending_orders` table instead of returning failure.

- [ ] **Step 2: Modify `buy_reserved_order` time check block**

Replace the early-return failure with a call to `_queue_pending_order(...)` that returns `success=True, status='queued'`.

- [ ] **Step 3: Commit**

```
feat: queue US reserved orders when outside KIS API time window
```

---

### Task 3: Create `us_pending_order_batch.py`

**Files:**
- Create: `prism-us/us_pending_order_batch.py`

- [ ] **Step 1: Write batch script**

Logic:
1. Initialize DB, check `is_reserved_order_available()`
2. Query `us_pending_orders WHERE status = 'pending' AND date(created_at) = date('now', 'localtime')`
3. For each order: call `buy_reserved_order` or `sell_reserved_order`
4. Update status to `executed` or `failed`
5. Expire old pending orders (created_at < today)
6. Log summary

- [ ] **Step 2: Commit**

```
feat: add US pending order batch processor (10:05 KST cron)
```

---

### Task 4: Update crontab + documentation

**Files:**
- Modify: `docker/crontab`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add cron entry at 10:05 KST**

```
# US Pending orders batch at 10:05 KST (process queued reserved orders)
5 10 * * 2-6 cd /app/prism-insight && python3 prism-us/us_pending_order_batch.py >> /app/prism-insight/logs/us_pending_orders_$(date +\%Y\%m\%d).log 2>&1
```

- [ ] **Step 2: Update CLAUDE.md entry points and troubleshooting**

Add to Key Entry Points table and Quick Troubleshooting.

- [ ] **Step 3: Commit**

```
docs: add US pending order batch to crontab and CLAUDE.md
```
