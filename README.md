# Payment Ledger Reconciliation Engine

A double-entry payment ledger that integrates with Stripe's test-mode API,
processes webhook events idempotently and replay-safely, and runs periodic
reconciliation to detect drift between Stripe's record of truth and the
local ledger.

This is a demonstration of transactional-correctness engineering —
idempotency, concurrency-safe writes, and event-driven consistency — the
same class of problem payment platforms solve internally.

## Core invariants

1. **Double-entry**: every ledger transaction is a set of legs whose debits
   and credits net to exactly zero. This is enforced two ways: the
   application rejects unbalanced leg sets before touching the database
   (`UnbalancedTransactionError`), and every posting happens inside a single
   `BEGIN IMMEDIATE ... COMMIT` SQLite transaction, so a crash or exception
   partway through a multi-leg post rolls back every leg written so far —
   the ledger can never be left half-written.
2. **Idempotency**: Stripe delivers webhooks at-least-once, so the same
   event can (and will) arrive more than once. Every processed event ID is
   recorded in a table with a `PRIMARY KEY` constraint; a duplicate delivery
   fails the `INSERT` and the handler short-circuits without reapplying any
   ledger effects. This holds even under concurrent delivery to multiple
   workers, because the uniqueness guarantee comes from SQLite itself, not
   an in-memory set.
3. **Reconciliation**: `reconciliation.py` periodically pulls recent
   PaymentIntents from Stripe and compares each one against the local
   ledger, flagging `missing_local_entry`, `amount_mismatch`, or
   `status_mismatch`. Output is a structured JSON report, not just log
   lines, so it can be archived, alerted on, or rendered in the dashboard.

## Architecture

```
                          ┌──────────────────┐
                          │   Stripe (test)   │
                          └─────────┬─────────┘
                     webhooks       │       PaymentIntents API
                 ┌───────────────────┴────────────────────┐
                 ▼                                          ▼
        ┌─────────────────┐                        ┌─────────────────┐
        │ webhook_handler  │                        │  reconciliation  │
        │  - verify sig    │                        │  - pull recent   │
        │  - idempotency   │                        │    PaymentIntents│
        │    claim         │                        │  - diff vs local │
        └────────┬─────────┘                        │  - JSON report   │
                 │ post_transaction()                └────────┬─────────┘
                 ▼                                             │
        ┌─────────────────────────────┐                        │
        │      ledger/service.py      │◄───────────────────────┘
        │  BEGIN IMMEDIATE ... COMMIT │
        └────────────┬────────────────┘
                      ▼
              ┌───────────────┐
              │ SQLite (WAL)  │
              │ accounts      │
              │ entries       │
              │ processed_    │
              │  stripe_events│
              └───────┬───────┘
                      │
        ┌─────────────┴──────────────┐
        ▼                             ▼
┌───────────────┐            ┌─────────────────┐
│    api.py      │            │   dashboard.py   │
│ FastAPI: create-│           │ Streamlit: KPIs, │
│ payment, webhook│           │ charts, mismatch │
│ balance, report │           │ drill-down       │
└───────────────┘            └─────────────────┘
```

Accounts used by the webhook handler:

| Account            | Type   | Effect                                             |
|--------------------|--------|-----------------------------------------------------|
| `stripe_receivable`| asset  | debited on `payment_intent.succeeded`, credited on `charge.refunded` |
| `revenue`          | revenue| credited on `payment_intent.succeeded`, debited on `charge.refunded` |
| `payment_failures` | memo   | debited on `payment_intent.payment_failed` (records the attempt; moves no real funds) |
| `suspense`         | memo   | credited on `payment_intent.payment_failed` (memo counterpart) |

## Local setup

```bash
git clone <this repo>
cd payment-ledger-reconciliation-engine
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
cp .env.example .env
# edit .env: set STRIPE_SECRET_KEY (sk_test_...) and STRIPE_WEBHOOK_SECRET (whsec_...)
```

### Running the API

```bash
uvicorn src.api:app --reload
```

### Forwarding Stripe webhooks locally

Install the [Stripe CLI](https://stripe.com/docs/stripe-cli) if you don't
already have it, then:

```bash
stripe login
stripe listen --forward-to localhost:8000/webhook
```

`stripe listen` prints a webhook signing secret (`whsec_...`) the first
time you run it — put that value in `.env` as `STRIPE_WEBHOOK_SECRET`. Then
in another terminal, trigger a test event:

```bash
stripe trigger payment_intent.succeeded
```

### Running the dashboard

```bash
streamlit run src/dashboard.py
```

### Running reconciliation

```bash
python -c "from src.reconciliation import reconcile, write_report; from src.config import get_db; write_report(reconcile(get_db()), 'reconciliation_report.json')"
```

or hit `GET /reconciliation-report` on the running API.

## Running tests

```bash
pytest -q
ruff check src tests
```

The live Stripe integration test
(`tests/test_stripe_integration.py`) is skipped automatically — not
failed — whenever `STRIPE_SECRET_KEY` isn't set in the environment,
including in CI.

## CI

`.github/workflows/ci.yml` installs dependencies, runs `ruff check`, and
runs `pytest -q`. It does not configure a Stripe key as a CI secret, so the
live-Stripe integration test always self-skips there; everything else
(ledger invariant, concurrency, idempotency, webhook signature
verification, API) runs on every push and PR.

## Limitations

- **SQLite** is used for simplicity and zero external dependencies. It's
  adequate for this project's scale and demonstrates the correctness
  patterns clearly, but a production system handling real concurrent load
  would use a database with stronger concurrent-transaction guarantees —
  e.g. Postgres with `SERIALIZABLE` isolation — rather than relying on
  SQLite's single-writer lock.
- The webhook handler and reconciliation job run in a single process with
  a single shared connection; a production deployment would run multiple
  worker processes/containers behind a load balancer, which is exactly the
  scenario the idempotency layer's DB-level uniqueness constraint (rather
  than an in-memory check) is designed to survive.
- `payment_intent.payment_failed` posts a memo-only debit/credit pair to
  dedicated `payment_failures`/`suspense` accounts rather than skipping
  ledger effects entirely, so every handled event type produces an
  auditable, balanced entry — but this is a design choice, not something
  Stripe requires; a real ledger might instead just log failed attempts
  without touching the ledger at all.
- Reconciliation only inspects `PaymentIntent`/`succeeded` state; it
  doesn't yet cross-check partial refunds, disputes, or multi-currency
  conversion fees.
