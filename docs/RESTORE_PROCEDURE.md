# Atheer RADAR (scraplan) — Database Restore Procedure

**Measured:** 2026-08-04 04:43–05:10 UTC (00:43–01:10 EDT) · **Host:**
`gis-syr-ThinkCentre-neo-50t-Gen-5` · **Item:** RESTORE-TEST
**Method:** every command below was executed in the session that wrote this file, against a
real off-host dump, into a real throwaway TimescaleDB instance. Timings are wall-clock from
that run. Nothing here is quoted from vendor documentation without having been run.

---

## 0. Why this file exists

`docs/PROJECT_STATE.md` §6.8 (BACKUP-EXT) proved that the nightly dump **arrives off-host
byte-identical** — sha256 computed by the destination, two full cycles, retention and failure
paths exercised. It explicitly did **not** prove that those bytes **reconstitute a working
database**, and it flagged the reason to doubt it: every dump run emits

```
pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: continuous_agg
pg_dump: hint: You might not be able to restore the dump without using --disable-triggers …
```

**A backup with no tested, written restore procedure is not insurance.** This file is that
procedure.

⛔ **The headline finding: the obvious restore command silently destroys data.** Running
`gunzip -c dump.sql.gz | psql -d opensky` — what an operator types under pressure — exits
**0**, prints nothing alarming to the terminal, and produces a database that is missing
**9,431,499 rows**, missing **all four hypertable primary keys**, and carrying **116 duplicate
indexes**. §3 documents that failure in full. §2 is the procedure that works.

⚠ **`--disable-triggers` was NOT the fix, and is not applicable here.** That flag exists for
`pg_dump --data-only` dumps restored with `pg_restore`. The nightly dump is a **full** dump
(schema + data) in **plain SQL** format, restored with `psql`, where foreign keys are created
*after* the data loads — so circular FKs never block it. pg_dump emits the warning whenever it
*detects* circular FKs, regardless of whether the dump it is producing is affected. The real
requirement is `timescaledb_pre_restore()` / `timescaledb_post_restore()`, which the warning
does not mention at all.

---

## 1. Preconditions

| Requirement | Value used in the tested run | Why it matters |
|---|---|---|
| Server version | **PostgreSQL 17.10** | Must match production. Measured from prod: `select version()` |
| Extension version | **timescaledb 2.28.2** | Must match production. Restoring onto a different version tests something else |
| Image | `timescale/timescaledb:2.28.2-pg17`, `sha256:fbc6fcd7ba7b418c9ff836c4bad89f7de2ff1e2609dc5b4efc9beae0e440b42d` | The **byte-identical image production runs**, so version match is guaranteed rather than approximated |
| Free disk | ≥ 3× the compressed dump, plus the restored DB | Tested run: 2.84 GiB dump + 6.0 GiB restored DB. 80 GB was free; peak use was ~18 GB |
| Free RAM | modest — the restore is I/O-bound | Tested with the target capped at **`--memory=3g --cpus=4`**; it completed without pressure |

⚠ **A plain `postgres:17` image cannot host this dump at all.** The dump's first statement is
`CREATE EXTENSION IF NOT EXISTS timescaledb`, and its body restores the TimescaleDB catalog
directly. Without the extension every hypertable object fails.

⛔ **Never restore onto the production instance** (`scraplan-timescaledb`, `127.0.0.1:5435`).
Restore to a separate target on a separate port with a separate volume.

---

## 2. THE PROCEDURE — executable from zero

Every command below is verbatim from the run that succeeded. Timings are from that run.

### Step 1 — fetch the dump from the off-host destination · **4 m 26 s**

```sh
mkdir -p ~/scraplan-restore-test && cd ~/scraplan-restore-test

# Newest dump at the destination. The key MUST be pinned: see PROJECT_STATE §6.8 — plain
# ssh works interactively (via the GNOME keyring agent) and fails without it.
env -u SSH_AUTH_SOCK ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/serverq \
    serverq@10.10.20.57 'ls -1 ~/scraplan-offsite/db/'

env -u SSH_AUTH_SOCK ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/serverq \
    serverq@10.10.20.57 'cat ~/scraplan-offsite/db/opensky-2026-08-02.sql.gz' \
    > opensky-2026-08-02.sql.gz
```

Measured: 3,048,307,155 B in **4 m 26 s = 11.5 MB/s**, consistent with the 100 Mb/s wire in
`docs/ENVIRONMENT.md` §3.

⚠ **Do not stage the dump under `/tmp`.** `/tmp` on this host is **tmpfs** (`findmnt -no
FSTYPE /tmp` → `tmpfs`, 16 GB), so a 2.84 GiB download there is consumed **from RAM** — on a
host whose swap is already 100 % full (§6.10). Stage on the ext4 volume, as above.

### Step 2 — prove the bytes before trusting them · **51 s**

```sh
# The expected hash comes from the DESTINATION's own index, not from this host.
env -u SSH_AUTH_SOCK ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/serverq \
    serverq@10.10.20.57 'cat ~/scraplan-offsite/CURRENT.sha256'

sha256sum opensky-2026-08-02.sql.gz          # 5.9 s
gzip -t opensky-2026-08-02.sql.gz            # 45.2 s — length alone proves nothing
```

Measured: `1bb276538b5a47ac5cdb87d251c2f9272049ec660e107ce47c1ec3d3009edf83` — matched
`CURRENT.sha256` and `MANIFEST.sha256` exactly; `gzip -t` clean; uncompressed size
**9,226,635,423 B (8.59 GiB)**, ratio 67.0 %.

### Step 3 — start an isolated target · **~25 s to ready**

```sh
docker run -d --name scraplan-restoretest \
  -e POSTGRES_DB=opensky -e POSTGRES_USER=opensky -e POSTGRES_PASSWORD=restoretest_scratch \
  -e TS_TUNE_MEMORY=2GB -e TS_TUNE_NUM_CPUS=4 -e TIMESCALEDB_TELEMETRY=off \
  -p 127.0.0.1:5436:5432 \
  --memory=3g --memory-swap=3g --cpus=4 \
  -v scraplan_restoretest_data:/var/lib/postgresql/data \
  timescale/timescaledb:2.28.2-pg17 \
  postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
           -c wal_level=minimal -c max_wal_senders=0 -c max_wal_size=4GB \
           -c maintenance_work_mem=512MB

until docker exec scraplan-restoretest pg_isready -U opensky -q; do sleep 2; done
```

Why each part:

- **Port 5436** — 5432/5433/5434/5435 are all in use on this host (`ss -ltn | grep 543`).
  5435 is production. ⛔ Never reuse it.
- **`--memory=3g --memory-swap=3g --cpus=4`** — bounds the restore's competition with the live
  collectors *provably* rather than hopefully. `--memory-swap` equal to `--memory` keeps it off
  the host's already-exhausted swap.
- **`TS_TUNE_MEMORY=2GB`** — the image's `001_timescaledb_tune.sh` otherwise reads the cgroup
  limit and tunes itself; pinning it makes the target deterministic.
- **`fsync=off`, `synchronous_commit=off`, `full_page_writes=off`** — safe for a scratch
  restore and materially faster. ⚠ For a restore you intend to **promote to production**, omit
  these three, or restart the server with them back on before the instance takes traffic; a
  crash mid-restore with `fsync=off` leaves an unrecoverable data directory.
- **`TIMESCALEDB_TELEMETRY=off`** — the only reason the restored `policy_telemetry` job (job 1)
  shows `scheduled=f` while production shows `t`. **Not a restore defect**; an artifact of this
  flag. Production reads `timescaledb.telemetry_level = basic`, the target `off`.

### Step 4 — ⛔ THE MANDATORY STEP — enter restoring mode · **< 2 s**

```sh
docker exec scraplan-restoretest psql -U opensky -d postgres -X -c "DROP DATABASE opensky;"
docker exec scraplan-restoretest psql -U opensky -d postgres -X -c "CREATE DATABASE opensky OWNER opensky;"
docker exec scraplan-restoretest psql -U opensky -d opensky   -X -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
docker exec scraplan-restoretest psql -U opensky -d opensky   -X -c "SELECT timescaledb_pre_restore();"

# Prove it took effect before spending 4 minutes on a restore that will silently fail:
docker exec scraplan-restoretest psql -U opensky -d opensky -X -tAc "show timescaledb.restoring;"
# -> on
```

⛔ **Skipping this step is the entire failure mode in §3.** `timescaledb_pre_restore()` sets
`timescaledb.restoring = on` at **database** level and stops the background workers, which
disables the extension's DDL/insert hooks. The dump contains explicit
`CREATE TABLE _timescaledb_internal._hyper_*_chunk` statements and restores the TimescaleDB
catalog directly — it never calls `create_hypertable()`. With the hooks live, those statements
are intercepted and partially rejected.

⚠ Because the flag is set at database level, it **persists across connections** — which is why
the three steps below can be separate `psql` invocations.

⚠ `CREATE EXTENSION IF NOT EXISTS timescaledb` reports `NOTICE: extension "timescaledb"
already exists, skipping` on this image, because it installs the extension into `template1`.
That notice is expected and harmless.

### Step 5 — restore · **4 m 27 s**

```sh
cd ~/scraplan-restore-test
gunzip -c opensky-2026-08-02.sql.gz \
  | docker exec -i scraplan-restoretest psql -U opensky -d opensky -X -v ON_ERROR_STOP=1
```

Measured: **4 m 27 s, exit 0, zero bytes on stderr.**

- ⛔ **`ON_ERROR_STOP=1` is not optional.** psql's default is to continue past errors, which is
  exactly how the broken restore in §3 reported success. With this flag a bad restore *stops*.
- ⛔ **`docker exec -i`** — without `-i` stdin is not attached and the dump is silently not
  delivered.
- **`-X`** — ignore any `~/.psqlrc` that could alter behaviour mid-restore.
- ⚠ Use the **container's** `psql`, not the host's. The dump is written by pg_dump 17.10 and
  contains `\restrict <token>`; an older host client fails on that metacommand.

### Step 6 — ⛔ leave restoring mode · **< 1 s**

```sh
docker exec scraplan-restoretest psql -U opensky -d opensky -X -c "SELECT timescaledb_post_restore();"
docker exec scraplan-restoretest psql -U opensky -d opensky -X -tAc "show timescaledb.restoring;"
# -> off
```

⛔ **A database left in `restoring = on` has no background workers**: no compression, no
continuous-aggregate refresh, no retention. It looks healthy and quietly stops maintaining
itself.

### Total measured time

| Phase | Time |
|---|---|
| Fetch 3.05 GB from the off-host destination | 4 m 26 s |
| Integrity (sha256 + `gzip -t`) | 51 s |
| Start target, ready to accept connections | ~25 s |
| Prep + `pre_restore` | < 2 s |
| **Restore** | **4 m 27 s** |
| `post_restore` | < 1 s |
| Verification suite (§4) | 3 s |
| **End to end, off-host bytes → verified working database** | **≈ 10 min** |

If the dump is already on the target host, the restore path alone is **≈ 5 minutes**.

---

## 3. ⛔ What the obvious command does instead — measured, not theorised

The naive path, run first on purpose:

```sh
gunzip -c opensky-2026-08-02.sql.gz | docker exec -i scraplan-restoretest psql -U opensky -d opensky
```

**Result: 3 m 13 s, exit code 0, 128 successful `COPY`s — and a silently damaged database.**
Its complete stderr was **five lines**:

```
ERROR:  could not find hypertable with id 4
DETAIL:  Assertion 'ht' failed.
CONTEXT:  COPY _hyper_4_494_chunk, line 1
ERROR:  ONLY option not supported on hypertable operations
ERROR:  ONLY option not supported on hypertable operations
ERROR:  ONLY option not supported on hypertable operations
ERROR:  ONLY option not supported on hypertable operations
```

What those five lines actually cost, measured against production:

| Damage | Measurement |
|---|---|
| **9,431,499 rows destroyed** | `_hyper_4_494_chunk` is the `state_vectors` chunk for **2026-07-26**. Production: 9,431,499 rows. Naive restore: **0**. Proof it was the *only* row loss: production holds **76,236,826** `state_vectors` rows within the dump's coverage (`ts <= 2026-08-02 08:15:16+00`); the naive restore produced **66,805,327**; the difference is **exactly 9,431,499** |
| **All 4 hypertable primary keys missing** | The four `ONLY option not supported` errors are the four `ALTER TABLE ONLY … ADD CONSTRAINT … PRIMARY KEY` statements for the only four hypertables that have one: `state_vectors_pkey`, `vessel_states_pkey`, `satellite_history_pkey`, `mt_vessel_states_pkey`. Indexes on public tables: production **56**, naive restore **52** |
| **116 duplicate indexes** | With the hooks live, every chunk index is created twice — once by the dump, once by the extension — so PostgreSQL renames the second with a `_1` suffix (`_hyper_11_17_chunk_satellite_history_epoch_idx_1`, …). Count of `%_1` indexes: naive **116**, correct restore **0** |

⚠ **Nothing above raises an error an operator would notice.** psql exits 0, the row counts are
plausible, the hypertables and continuous aggregate are all present and queryable, and the
`/stats` endpoint query returns **byte-identical results to production** — because the window
it reads does not touch the destroyed chunk. A restore that is 99 % right and silent about the
1 % is the most dangerous outcome available.

---

## 4. Verification — what to check, and what the tested restore returned

⛔ **"psql exited 0" is not verification.** Run all of it. The reusable script is
`verify.sql` as reproduced in §6; run it against production and the restore and diff.

| # | Check | Production | Restored (dump of 2026-08-02 08:15 UTC) | Verdict |
|---|---|---|---|---|
| 1 | Server / extension version | PG 17.10 · timescaledb 2.28.2 | PG 17.10 · timescaledb 2.28.2 | ✅ exact match |
| 2 | **Hypertables — restored as hypertables, not plain tables** | **5** | **5** | ✅ the classic silent failure did **not** occur |
| 3 | Chunks · compressed | 59 · 33 | 54 · 33 | ✅ all 5 missing chunks date **2026-08-03 → 09-04**, i.e. created after the dump; nothing exists in the restore that is absent from production |
| 4 | **Continuous aggregate** | `state_vectors_1m` → `_materialized_hypertable_6`, `materialized_only=f` | identical | ✅ present, real-time, and queryable |
| 5 | Materialized cagg rows | 56,845 | 50,223 | ✅ vintage |
| 6 | **Refresh policies / jobs** | 8 jobs | 8 jobs | ✅ see #12 — they **ran** |
| 7 | Constraints on public tables | **36** | **36** | ✅ **name-for-name identical** (`diff` → no output) |
| 8 | Indexes on public tables | **56** | **56** | ✅ **name-for-name identical** (`diff` → no output) |
| 9 | Duplicate `%_1` indexes | 0 | **0** | ✅ |
| 10 | Migrations applied | 37, `037_maritime_hull_composite_zone_dwell.sql` | identical | ✅ |
| 11 | `state_vectors` rows | 76,236,826 *within the dump's coverage* | **76,236,826** | ✅ **exact** — zero data loss |
| 12 | `vessel_states` / `maritime_events` / `vessel_identity` | 1,707,198 / 50,082 / 5,248 | 1,435,202 / 42,020 / 4,967 | ✅ all lower, all consistent with a 44-hour-old dump |
| 13 | `pg_database_size` | 9831 MB | 6150 MB | ✅ smaller — 5 fewer chunks, **and** see #15 |

### The functional gates — the ones that decide the item

| # | Gate | Result |
|---|---|---|
| 14 | **A real project query touching a hypertable and the cagg together** — `/api/v1/stats` (`backend/api.py:347`) verbatim: reads `state_vectors_1m` (`materialized_only=false`, so it unions the materialization hypertable with live `state_vectors`) LEFT JOINed to the plain `snapshots` table | ✅ **8 buckets, byte-identical to production.** `EXPLAIN` confirms it genuinely touches both: an `Index Only Scan` on `_hyper_6_496_chunk__materialized_hypertable_6_source_bucket_idx` **Appended** to a live `Index Scan Backward` on a `state_vectors` chunk |
| 15 | **Background policies actually execute** | ✅ **7 of 8 jobs ran with 0 failures.** Job 1003 (cagg refresh, 1 min): **6 runs / 6 successes**. Job 1002 (compression on `state_vectors`): 1 success — it **compressed `_hyper_4_570_chunk`**, which production still holds uncompressed at 2959 MB. That single overdue compression is most of why the restored DB measures smaller. Job 1 (telemetry) never ran — disabled by `TIMESCALEDB_TELEMETRY=off`, see §2 Step 3 |
| 16 | **Continuous aggregate is refreshable on demand** | ✅ `CALL refresh_continuous_aggregate('state_vectors_1m','2026-08-01','2026-08-02')` → `NOTICE: … is already up-to-date`, `CALL` |
| 17 | **Writes work and route to a new chunk** | ✅ `INSERT` at `2026-08-09` → `INSERT 0 1`, landed in a newly created `_hyper_4_740_chunk`, chunk count 23 → 24; `ROLLBACK` returned it to 23 with 0 leftover rows |
| 18 | **Restored CHECK constraints are enforcing, not just present** | ✅ An `INSERT` with a deliberately inconsistent epoch was **rejected**: `new row for relation "_hyper_4_739_chunk" violates check constraint "state_vectors_ts_matches_epoch"` |
| 19 | **Compressed data is readable through compression** | ✅ `_hyper_4_494_chunk`: `is_compressed=t`, 9,431,499 rows readable, `after_compression_total_bytes` **462 MB in both** production and the restore |
| 20 | **The primary-key index path works** | ✅ `state_vectors_pkey (icao24, ts, source)` present; a point lookup plans as `Index Only Scan using "730_state_vectors_pkey"` — the path `BACKEND_CHANGELOG` Gate 3 depends on, and the one the naive restore destroys |

### Warnings observed during the restore — recorded because they were observed

⚠ None of these indicate a problem, and all are listed so the next operator is not surprised:

```
LOG:  skipping vacuum of "_hyper_4_494_chunk" --- lock not available
LOG:  skipping vacuum of "_hyper_4_570_chunk" --- lock not available
ERROR:  canceling autovacuum task
CONTEXT:  while scanning block 113578 of relation "_timescaledb_internal._hyper_4_570_chunk"
NOTICE:  extension "timescaledb" already exists, skipping
WARNING:  no usable system locales were found            (container initdb, cosmetic)
initdb: warning: enabling "trust" authentication for local connections
```

`ERROR: canceling autovacuum task` is autovacuum yielding to the restore's locks — expected
under a bulk load, and it retries. ⚠ **A transient `pg_database_size` of 11.0 GB was measured
seconds after the restore**, before the compression policy and autovacuum settled; the steady
value is **6150 MB**. Measure size after the jobs have run, not immediately.

---

## 5. Promoting a restore to production

⛔ **Not performed or tested in this run** — RESTORE-TEST deliberately never touched
production. What follows is therefore marked as **untested**, and must not be read as a
verified path:

1. Restore and verify into a scratch target exactly as above.
2. Restart the target **without** `fsync=off`, `synchronous_commit=off`,
   `full_page_writes=off`.
3. Stop the writers — `scraplan-collector`, `scraplan-maritime`, `scraplan-military`,
   `scraplan-adsb-ingest`, `scraplan-server` — before any cutover.
4. ⚠ The dump carries **no `CREATE DATABASE`** (`pg_dump -U opensky opensky`, no `-C`), so the
   target database and the `opensky` role must exist first.
5. ⚠ A restored copy is **as old as its dump**. Measured here: the 08-02 dump was **44 hours**
   behind at restore time and was missing 271,996 `vessel_states` rows and 8,062
   `maritime_events`. Data written after the dump is **not recoverable from it** — the local
   `~/scraplan-backups` series and the archive files are the only other sources.

---

## 6. The verification script

Reproduce `verify.sql` and run it against both instances, then diff:

```sh
docker exec -i scraplan-timescaledb  psql -U opensky -d opensky -X -f - < verify.sql > prod.txt
docker exec -i scraplan-restoretest  psql -U opensky -d opensky -X -f - < verify.sql > restored.txt
diff prod.txt restored.txt
```

The checks it must contain, at minimum — each maps to a row in §4:

```sql
-- versions + extensions
select version(); select extname, extversion from pg_extension order by 1;
-- #2/#3 hypertables and chunks: the silent-failure gate
select hypertable_name, num_chunks, compression_enabled from timescaledb_information.hypertables order by 1;
select count(*) from timescaledb_information.chunks;
select count(*) from timescaledb_information.chunks where is_compressed;
-- #4 continuous aggregates
select view_name, materialization_hypertable_name, materialized_only
  from timescaledb_information.continuous_aggregates order by 1;
-- #6/#15 policies, and whether they have actually run
select job_id, proc_name, schedule_interval, hypertable_name, scheduled
  from timescaledb_information.jobs order by job_id;
select job_id, last_run_status, total_runs, total_successes, total_failures
  from timescaledb_information.job_stats order by job_id;   -- NB: no proc_name column here
-- #7/#8 objects, compared BY NAME on public tables (raw totals are confounded by chunk count)
select rel.relname||' | '||con.conname||' | '||con.contype::text
  from pg_constraint con join pg_class rel on rel.oid=con.conrelid
  join pg_namespace n on n.oid=rel.relnamespace where n.nspname='public' order by 1;
select i.relname from pg_index x join pg_class i on i.oid=x.indexrelid
  join pg_class t on t.oid=x.indrelid join pg_namespace n on n.oid=t.relnamespace
  where n.nspname='public' order by 1;
-- #9 duplicate indexes left by a hookless restore
select count(*) from pg_class where relkind='i' and relname like '%\_1';
-- #11/#12 exact row counts for every public table
select c.relname, (xpath('/row/cnt/text()', query_to_xml(
         'select count(*) as cnt from public.'||quote_ident(c.relname), false, true, '')))[1]::text::bigint
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r' order by 1;
-- #14 the real query — /api/v1/stats verbatim, fixed absolute window inside the dump's coverage
```

⚠ **Two traps when writing these queries** — both hit during this run:

- `timescaledb_information.continuous_aggregates` on 2.28.2 has **no `finalized` column**.
- `timescaledb_information.job_stats` has **no `proc_name` column** — join to `jobs` for it.
- `pg_constraint.contype` is `"char"`; `||` against it is ambiguous. Cast: `contype::text`.

⚠ **Compare row counts against the dump's coverage, not against production's current
totals.** Production keeps collecting. The honest production-side comparison is
`select count(*) from state_vectors where ts <= '<max ts in the restore>'`.

---

## 7. Cleanup

```sh
docker rm -f scraplan-restoretest
docker volume rm scraplan_restoretest_data
rm -rf ~/scraplan-restore-test          # the fetched dump + logs; NOT the backups
```

⛔ **Never delete anything from `~/scraplan-backups` or from
`serverq:~/scraplan-offsite`.** The fetched copy in `~/scraplan-restore-test` is a third copy
and is the only thing this procedure creates or removes.

Measured for the tested run, `df --output=avail -B1 /`:

| Point | Free |
|---|---|
| Before starting | **80 GB** (85,846,011,904 B) |
| Peak use, during/just after the restore | **62 GB** — ~18 GB consumed (2.9 GB dump + the restored DB) |
| Before cleanup, after the compression policy settled | **68 GB** (72,026,918,912 B) |
| **After cleanup** | **80 GB** (85,846,011,904 B) — **12.87 GiB reclaimed, back to the starting figure** |

Verified after cleanup: `~/scraplan-backups` still holds **13 dumps / 28 GB**, the destination
still holds **3 objects** with `sha256sum -c CURRENT.sha256` passing on all three, and
`scraplan-timescaledb` is still `Up (healthy)` with its **5** hypertables.

---

## 8. What this procedure does and does not prove

**Proven, by measurement, on 2026-08-04:** the off-host dump of 2026-08-02 restores into a
version-matched TimescaleDB instance with **zero data loss** (`state_vectors` exact to the
row), all 5 hypertables **as hypertables**, the continuous aggregate present, real-time and
refreshable, all 8 jobs restored and **executing with 0 failures**, constraints and indexes
**name-for-name identical** to production, a real project query returning **byte-identical**
results, and a working write path with **enforcing** constraints.

**Not proven:**

- ⛔ **Promotion to production has never been performed** (§5).
- ⛔ **Only the 2026-08-02 dump was tested.** The other dumps at the destination and the 13 in
  `~/scraplan-backups` are untested individually; what is proven is that a dump produced by
  `backup_offsite.sh` in its current form restores by this procedure.
- ⚠ **Restore onto a mismatched PostgreSQL/timescaledb version was not tested** and would be a
  different experiment.
- ⚠ **The dump itself carries no restore instructions.** Anyone who finds
  `opensky-YYYY-MM-DD.sql.gz` without this file will very likely run the §3 command. Closing
  that gap is a change to the backup layer, recorded as an open item in
  `docs/PROJECT_STATE.md` §6.8.


## المفسِّر أيضاً — إعادة بناء بيئة بايثون المثبَّتة (مُفجَّر 2026-09-01)

استعادة القاعدة نصف القصة؛ النصف الآخر أن الكود الذي يقرأ الأرشيف يقوم من الصفر
بإصدارات معلومة. مُثبَت بالتفجير على هذا المضيف:

```bash
python3 -m venv /tmp/rebuild-venv
/tmp/rebuild-venv/bin/pip install -r requirements.txt -c constraints.txt -r requirements-dev.txt
/tmp/rebuild-venv/bin/python -m pytest -q
```

| المقياس | المقيس 2026-09-01 |
|---|---|
| التثبيت (65 تثبيتاً من constraints.txt) | **32 ثانية** |
| `import backend.app` | سليم |
| الجناح الكامل على البيئة الجديدة | **1,081 ناجحاً في 65 ثانية** |
| الإجمالي من صفر إلى أخضر | **98 ثانية** |

⚠ يُعاد هذا التفجير بعد كل تجديد لـ`constraints.txt` — ملف تثبيت لم يُجرَّب تنصيبه
النظيف ليس برهاناً بل ادّعاء.
