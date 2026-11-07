# ⛔ READ THIS BEFORE RESTORING — the obvious command destroys data

This file is uploaded beside the dumps by `scripts/backup_offsite.sh` on **every** run, so it
is never older than the newest dump next to it. It is deliberately self-sufficient: if the
scraplan repository is the thing you lost, everything you need is below.

`opensky-YYYY-MM-DD.sql.gz` is a **full plain-SQL `pg_dump`** of a **TimescaleDB** database.

---

## ⛔ DO NOT run this

```sh
gunzip -c opensky-YYYY-MM-DD.sql.gz | psql -d opensky        # ⛔ SILENTLY DESTROYS DATA
```

**Measured, 2026-08-04, on the real 2026-08-02 dump.** That command **exits 0**, prints five
lines to stderr, and leaves a database that looks healthy in every ordinary check:

| What it costs | Measured |
|---|---|
| **9,431,499 rows destroyed** | One whole `state_vectors` day-chunk (2026-07-26) restored **empty** |
| **All 4 hypertable primary keys missing** | `state_vectors`, `vessel_states`, `satellite_history`, `mt_vessel_states` |
| **116 duplicate indexes** | Every chunk index built twice, the second renamed with a `_1` suffix |

⚠ The hypertables, the continuous aggregate and the application's own `/stats` query all still
return correct-looking results afterwards. It is 99 % right and silent about the 1 %.

⚠ **`pg_dump -Fc` + `pg_restore` does NOT help** — measured, same damage: `pg_restore` hit the
identical two errors, reported `errors ignored on restore: 2`, and lost the same rows and the
same primary key. `pg_restore` also ignores errors by default. The format is irrelevant.

⚠ **`--disable-triggers` is NOT the fix** and does not apply here. `pg_dump` prints a
`circular foreign-key constraints … continuous_agg` warning on every run; that hint is for
`--data-only` dumps. This is a full dump, where foreign keys are created *after* the data.

---

## ✅ DO run this

The cause of the damage is that TimescaleDB's own DDL/insert hooks intercept the dump's
`CREATE TABLE … _hyper_*_chunk` and `COPY` statements. `timescaledb_pre_restore()` disables
them; `timescaledb_post_restore()` turns the background workers back on.

```sh
# 0. Prove the bytes first. The expected hash is in CURRENT.sha256, beside this file.
sha256sum -c CURRENT.sha256
gzip -t opensky-YYYY-MM-DD.sql.gz

# 1. A target whose versions MATCH the source: PostgreSQL 17.10 + timescaledb 2.28.2.
#    ⛔ A plain `postgres:17` image cannot host this dump at all.
docker run -d --name scraplan-restore \
  -e POSTGRES_DB=opensky -e POSTGRES_USER=opensky -e POSTGRES_PASSWORD=changeme \
  -p 127.0.0.1:5436:5432 -v scraplan_restore_data:/var/lib/postgresql/data \
  timescale/timescaledb:2.28.2-pg17 postgres
until docker exec scraplan-restore pg_isready -U opensky -q; do sleep 2; done

# 2. ⛔ THE MANDATORY STEP. Verify it took effect before spending minutes on the restore.
docker exec scraplan-restore psql -U opensky -d opensky -X -c "SELECT timescaledb_pre_restore();"
docker exec scraplan-restore psql -U opensky -d opensky -X -tAc "show timescaledb.restoring;"   # -> on

# 3. Restore. ⛔ ON_ERROR_STOP=1 is not optional — psql's default is to continue past errors,
#    which is exactly how the broken restore above reports success. -i is required or stdin
#    is silently not delivered. Use the CONTAINER's psql: the dump contains `\restrict`.
gunzip -c opensky-YYYY-MM-DD.sql.gz \
  | docker exec -i scraplan-restore psql -U opensky -d opensky -X -v ON_ERROR_STOP=1

# 4. ⛔ Leave restoring mode, or the database has no background workers at all: no
#    compression, no continuous-aggregate refresh, no retention. It looks fine and quietly
#    stops maintaining itself.
docker exec scraplan-restore psql -U opensky -d opensky -X -c "SELECT timescaledb_post_restore();"
docker exec scraplan-restore psql -U opensky -d opensky -X -tAc "show timescaledb.restoring;"   # -> off
```

Measured cost on this hardware: **4 m 27 s** for the restore itself, ≈10 min end-to-end
including fetching a 3.05 GB dump over the LAN.

---

## Verify it WORKED — "psql exited 0" is not verification

```sql
-- 1. Hypertables must be HYPERTABLES, not plain tables. Expect 5.
select count(*) from timescaledb_information.hypertables;
-- 2. Continuous aggregate must exist and be real-time. Expect state_vectors_1m, materialized_only=f
select view_name, materialized_only from timescaledb_information.continuous_aggregates;
-- 3. Policies must be restored AND running. Expect 8 jobs, 0 failures.
select job_id, last_run_status, total_runs, total_failures from timescaledb_information.job_stats order by 1;
-- 4. ⛔ The gate the naive restore fails silently: 4 primary keys and 0 duplicate indexes.
select count(*) from pg_constraint c join pg_class r on r.oid=c.conrelid
  join pg_namespace n on n.oid=r.relnamespace where n.nspname='public' and c.contype='p';
select count(*) from pg_class where relkind='i' and relname like '%\_1';   -- must be 0
-- 5. Row counts. Compare against the SOURCE restricted to this dump's coverage, never
--    against the source's current totals — production keeps collecting.
select count(*) from state_vectors;
```

⚠ A restored copy is only as old as its dump. `LAST_SUCCESS` beside this file records when the
newest dump was verified here.

The full procedure, with all 20 verification gates and their measured values, is
`docs/RESTORE_PROCEDURE.md` in the scraplan repository.
