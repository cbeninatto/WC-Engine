# Deploy: Supabase + Vercel

The hosted topology (replaces the original local-only design):

```
  Supabase (Postgres)  ── state (all tables) ───────────────┐
        ▲      ▲                                             │
        │      │ writes results                             │ reads
        │      └────────── GitHub Actions (every 30 min, the worker)
        │                                                    │
        └──────────────────────────── Vercel (dashboard UI + Telegram webhook)
```

- **Supabase** owns the data (the old `wc.db`).
- **GitHub Actions** is the worker — it runs the long web-search agent and writes to Supabase.
- **Vercel** serves the dashboard (reads Supabase live; "Run monitor" fires the Action).
- Your computer is **not** in the picture.

The code already supports this: `lib/db.py` uses Postgres whenever `DATABASE_URL` is set,
and falls back to local SQLite otherwise. Nothing else changes.

---

## 1. Supabase — create the database

1. Create a project at <https://supabase.com> → set a strong DB password.
2. **Project Settings → Database → Connection string → "Connection pooling"** (Transaction
   mode, port **6543**). Copy it; append `?sslmode=require`. This is your `DATABASE_URL`:
   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
   ```
   Use the **pooler (6543)**, not the direct connection (5432) — it's the serverless-safe one.

The tables are created for you by the migration script in step 2 (or paste
[`db/schema_postgres.sql`](../db/schema_postgres.sql) into the Supabase **SQL Editor**).

## 2. Migrate your data into Supabase

From your machine (one time), carry the current `wc.db` (teams, the 9 results, ratings…)
into Postgres:

```bash
# add DATABASE_URL to .env, then:
python scripts/migrate_to_postgres.py
```

It creates the schema and copies every table in FK-safe order (idempotent — safe to re-run).
Verify counts in the Supabase Table Editor (48 teams, 72 matches).

**Smoke-test the Postgres backend locally** (same code, hosted DB):

```bash
python scripts/predict.py        # should print the same top-5 as SQLite did
```

To go back to local SQLite at any time, just unset `DATABASE_URL`.

## 3. Vercel — deploy the dashboard

1. Import the GitHub repo at <https://vercel.com/new>. It picks up
   [`vercel.json`](../vercel.json) and [`api/index.py`](../api/index.py) automatically.
2. **Project Settings → Environment Variables:**
   | Var | Value |
   |-----|-------|
   | `DATABASE_URL` | the Supabase pooler URL from step 1 (**required**) |
   | `GITHUB_DISPATCH_TOKEN` | a fine-grained token with **Actions: write** on this repo |
   | `GITHUB_REPO` | `cbeninatto/WC-Engine` |
3. Deploy. Open the URL — standings/ratings/results load from Supabase; **Run monitor**
   fires the GitHub Action (which writes back to Supabase); **Recompute** runs inline.
4. **Protect it.** The dashboard has live, spend-triggering buttons, so it must not be
   public: enable **Vercel → Settings → Deployment Protection** (Vercel Authentication or
   Password Protection). Don't skip this.

## 4. Point the scheduled worker at Supabase

Add one repo secret so the every-30-min Action writes to Supabase instead of committing
`wc.db`: **repo → Settings → Secrets and variables → Actions → New secret**

| Secret | Value |
|--------|-------|
| `DATABASE_URL` | the Supabase pooler URL |

(Plus `ANTHROPIC_API_KEY`, and `TELEGRAM_*` for pings, as before.) The workflow detects
`DATABASE_URL` and switches to Postgres mode automatically — no `wc.db` commits.

## 5. (Optional) Telegram bot as a Vercel webhook

`agents/telegram_bot.py` long-polls and needs an always-on host. To run it serverlessly
instead, port its command handlers into a Vercel function and register a webhook:
```
https://api.telegram.org/bot<token>/setWebhook?url=https://<your-app>.vercel.app/api/telegram
```
This is the one piece not yet scaffolded — say the word and I'll add the webhook function.

---

## Rollback
Everything is gated on `DATABASE_URL`. Unset it (locally, in Vercel, in Actions) and the
whole system reverts to the local SQLite file. No data is destroyed by switching backends —
only by re-running `seed_from_xlsx.py`, which still resets the local `wc.db`.
