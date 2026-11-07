# Atheer RADAR — Backend

REST API v1 + WebSocket stream over PostgreSQL/TimescaleDB, fed by the
existing collector's JSONL archive. The React frontend (next phase) is built
against the **API Contract v1** section at the bottom of this file.

## Architecture

```
tracker_opensky.py ──append──▶ opensky_output/states.jsonl   (ground truth, never mutated)
        │  │
        │  └─atomic replace──▶ opensky_output/latest.json    (current snapshot)
        │
        └─(--db-url, optional)──▶ TimescaleDB ──LISTEN/NOTIFY──▶ server.py (REST v1 + WS)
ingest.py backfill ─whole JSONL──▶    │                               │
ingest.py follow (alt. writer) ──▶ hypertable + 1m rollups       React / smoke UI
```

**Roles (the original decoupling, preserved):**

- `tracker_opensky.py` — the collector. Owns all archive writes. With
  `--db-url` (or `$OPENSKY_DB_URL`) it ALSO mirrors each fresh snapshot into
  the DB — flag-gated, off by default, and failure-tolerant: a DB outage is
  logged and skipped, archiving never stops, the collector never crashes.
  The OpenSky polling logic is untouched.
- `ingest.py` — the shared ingestion path (the collector's sink calls it too,
  so live and backfill can never drift).
  - `backfill`: loads the whole archive, idempotently. Re-run any time —
    this is also how a DB-outage gap gets covered.
  - `follow`: optional alternative writer that tails `states.jsonl` for
    setups where the collector should stay fully DB-agnostic. Do not run it
    together with `--db-url` (harmless — idempotency absorbs the overlap —
    but pointless).
  - `--source` selects the feed identity (part of the natural key):
    `opensky` (default), `airplanes_live` (military overlay), and
    `adsbexchange` — the external **adsb_scraper** project, whose collector we
    do not own. It writes `state_vectors.jsonl` already in StateVector shape;
    scraplan consumes it through this SAME path — `ingest.py follow --source
    adsbexchange --archive <path> --state-dir adsb_ingest_state` (durable unit:
    `scraplan-adsb-ingest`), or a one-shot `backfill`. `--state-dir` keeps
    scraplan's tail offset out of the foreign output dir.
- `backend/geo_area.py` — the shared, source-neutral geo classifier (seven
  countries + a true kilometric AEQD border buffer, lifted from adsb_scraper's
  `area.py`; committed asset `backend/data/aoi_countries.geojson`, never fetched
  at runtime, fail-fast if missing). `parse_record` calls it at ingest, so
  EVERY feed's rows get `country`/`near_country`/`position_quality`/
  `distance_km`/`geo_source=scraplan-aoi` from their own lat/lon — geo is a
  property of position, not of the feed. adsbexchange rows are re-classified by
  this same layer, so sources never disagree at a point. `classify()` reads
  only degrees; scraplan's metric altitude/velocity never affect it.
  Configurable via `SCRAPLAN_AOI_BUFFER_KM` (default 50) and `SCRAPLAN_AOI_ASSET`.
- `server.py` / `backend/` — read-only API over the DB, with `latest.json`
  as the primary "current snapshot" source (see contract); also serves the
  React SPA from `frontend/dist`.

**The database is a derived index, not the source of truth.** The JSONL
archive is never deleted or mutated; dropping the whole DB and re-running
`ingest.py backfill` reproduces it exactly.

**Geo backfill of pre-P3 rows (separate, not automatic).** P3 classifies only
NEW ingested rows; the ~2.5M opensky + 67k airplanes_live rows written before
P3 (and the ~61k adsbexchange rows ingested in P2 with the feed's own values)
keep their pre-P3 geo columns until a one-off backfill runs. Such a backfill
UPDATEs positioned rows where `geo_source IS DISTINCT FROM 'scraplan-aoi'` using
the same `backend/geo_area` classifier — idempotent and re-runnable. It touches
compressed hypertable chunks, so it must run in time-bounded batches (decompress
→ update → recompress) off-peak; it is a deliberate, separately-approved
operation, not part of P3.

### Phase-2 decision: flag-gated collector mirror (`--db-url`)

Chosen (user decision): **option 1 — extend the collector minimally behind a
flag.** The extension is a single `DatabaseSink` class + one call after the
archive writes; it lazy-imports `ingest.py`, so the collector has zero DB
dependencies unless the flag is used. Because the mirror runs inside the
collector right after a snapshot completes, snapshots arrive in the DB whole
(one transaction, marked `complete` immediately) — no partial-snapshot
window on the live path. Resilience verified: DB stopped mid-collection →
collector logged the failure and kept archiving; DB restarted → mirroring
resumed on the next snapshot; `ingest.py backfill` covered the gap with
zero loss and zero duplicates.

### Idempotency guarantee

Natural key `(icao24, snapshot_time)` is enforced by the **database**
(`PRIMARY KEY (icao24, ts)` — `ts` is `to_timestamp(snapshot_time)`, and a
`CHECK` constraint pins that bijection, so the PK is exactly the natural
key). All inserts are `ON CONFLICT DO NOTHING`. No application-level dedup
exists anywhere, so no race can create duplicates.

## Setup

```bash
# 1. Environment
cp .env.example .env          # set POSTGRES_PASSWORD; DATABASE_URL follows it
pip install -r requirements.txt -c constraints.txt   # -c pins the known-good versions

# 2. Database (dev: docker; production: point DATABASE_URL at a managed instance)
docker compose up -d          # TimescaleDB on 127.0.0.1:5435
python migrate.py             # apply migrations (versioned, checksummed)

# 3. Load the archive
python ingest.py backfill

# 4. Frontend (React SPA — build once, FastAPI serves the output)
cd frontend && npm install && npm run build && cd ..

# 5. Live pipeline (two long-running processes)
# Middle East box (~1185 deg² → 4 OpenSky credits/request; at 100 s cadence
# ≈ 3456 credits/day, inside the 4000/day OAuth2 budget). Smaller boxes cost
# less — see the credit tiers in the OpenSky docs (≤25 deg² = 1 credit).
python tracker_opensky.py --lamin 12.0 --lomin 24.0 --lamax 42.0 --lomax 63.5 \
    --snapshot-delay 100 --db-url "$DATABASE_URL"   # or: export OPENSKY_DB_URL=...
python server.py --host 0.0.0.0 --port 8000
# → React app at /, REST at /api/v1/*, WS at /api/v1/stream

# (alternative, DB-agnostic collector: omit --db-url and run
#  `python ingest.py follow` as a third process instead)
```

### Frontend development

`cd frontend && npm run dev` starts Vite on :5173 with `/api` (HTTP + WS)
proxied to the FastAPI server on :8000 — no CORS setup needed. Production
never runs a second server: `npm run build` writes `frontend/dist/`, which
`server.py` mounts as static files. If `dist/` is missing the server returns
an honest 503 hint page at `/`. Frontend decisions and the dual-theme token
plan live in `FRONTEND_CHANGELOG.md`.

Interactive API docs (generated from the same Pydantic contract): `/api/docs`.

Port 5435 is a dev-machine constraint (5432–5434 already taken by other
Postgres instances) — production uses whatever `DATABASE_URL` says.

### Migrations

Plain SQL files in `migrations/`, applied in lexical order by `migrate.py`,
tracked in `schema_migrations` with sha256 checksums. Editing an applied
migration is a hard error — write a new file. Files containing
`-- migrate:no-transaction` are executed statement-by-statement outside a
transaction (required for continuous aggregates). `python migrate.py --status`
shows applied/pending without changing anything.

## Schema

- **`state_vectors`** — TimescaleDB hypertable, 1-day chunks, partitioned on
  `ts TIMESTAMPTZ`. One row per (aircraft, snapshot). Nulls are preserved as
  nulls (`baro_altitude`, `velocity`, `true_track`, …: null = "not
  reported"). `callsign`/`squawk`/`origin_country` are stored verbatim
  (empty string means the transponder sent none).
- **`snapshots`** — plain table, one row per distinct `snapshot_time`:
  bbox (nullable — only known for snapshots observed live via `latest.json`),
  `count` (always recomputed from actual `state_vectors` rows in the same
  transaction — never trusted from the writer), `generated_at`.
- **`state_vectors_1m`** — continuous aggregate: per-minute `state_count`,
  `ground_count`, `max_baro_altitude`, `max_geo_altitude`, `max_velocity`.
  Real-time aggregation is ON, refresh policy runs every minute over the
  trailing 2h; backfill triggers a full refresh explicitly.

### Query patterns → indexes (verified with EXPLAIN)

| Pattern | Index used |
|---|---|
| history: `icao24 = ? AND ts ∈ range` | PK `(icao24, ts)` — Index Only Scan |
| replay/live: `ts = ?` | hypertable `ts DESC` index |
| stats: time buckets | continuous aggregate (materialized chunks + real-time tail); raw rows never scanned |
| search / bbox filters | applied within one snapshot's rows (~150), after the `ts` index narrowed the scan |

Every time-range endpoint has a hard cap (7 days for history/snapshots,
5000 buckets for stats) — no request can trigger an unbounded hypertable scan.

## التشغيل الدائم والاختبار (Operations)

**Services** (systemd *user* units — no sudo; installed via `deploy/systemd/README.md`):

| unit | job |
|---|---|
| `scraplan-server.service` | API + SPA on :8000, auto-restart |
| `scraplan-collector.service` | Middle East collector + DB mirror, auto-restart |
| `scraplan-military.service` | airplanes.live military overlay, auto-restart |
| `scraplan-maritime.service` | AISStream maritime AIS collector, auto-restart |
| `scraplan-rotate.timer` | daily 00:05 — gzip-rotate `states.jsonl` |
| `scraplan-backup.timer` | daily 03:00 — `pg_dump` → `~/scraplan-backups/` (14 d kept) |

The maritime unit reads `AISSTREAM_API_KEY` from the git-ignored project
`.env` (via `EnvironmentFile=-` + python-dotenv) and writes to
`maritime_output/`. Verified: kill `-9` the collector → systemd restarts it
in ≤15 s (`Restart=on-failure`).

`loginctl enable-linger` keeps everything running without a login session and
after reboot. Logs: `journalctl --user -u scraplan-server -f`.

**Archive layout:** live `opensky_output/states.jsonl` + rotated
`states-YYYY-MM-DD.jsonl.gz`. Full rebuild: `python ingest.py backfill
--archive opensky_output` (directory mode reads rotated .gz too). Chunks
older than 7 days are compressed in-DB (migration 005).

**The one gate:** `./scripts/check.sh` — ruff (F+B) + mypy (backend) + the full
pytest suite + the frontend vitest guards, all adopted at zero findings, so any
output is a regression. Run it before every commit/merge.

**Tests:** `pip install -r requirements-dev.txt && pytest`. Unit tests are
standalone; contract-integration tests run against the live stack and
**skip automatically** when it's down.

**Future — public exposure recipe (deliberately not built; LAN-only today):**
reverse-proxy through the existing Caddy (`reverse_proxy /api/* 127.0.0.1:8000`
+ automatic HTTPS), add per-IP rate limiting at the proxy, and consider an
auth token for `/api/v1/aircraft` (it triggers upstream fetches).
A local ADS-B receiver (~$30 RTL-SDR) remains the biggest data upgrade:
better regional coverage + 8000 OpenSky credits/day as a feeder.

## Operational notes

- **DB down:** the collector logs `DB write failed (archive unaffected...)`
  once per snapshot and keeps archiving — it reconnects automatically on the
  next snapshot after the DB returns. Run `python ingest.py backfill` to
  cover the outage gap (idempotent — safe even if nothing is missing). The
  API keeps serving `/api/v1/live` from `latest.json` and `/api/v1/health`
  reports the truth. (With the alternative `follow` writer: it retries with
  backoff and its offset only advances on commit, so the gap fills itself.)
- **Rebuild from scratch:** `docker compose down -v && docker compose up -d
  && python migrate.py && python ingest.py backfill`.
- **`states.jsonl` growth:** the archive grows unbounded by design (ground
  truth). Rotation/compression is a future operational task; ingestion
  handles file replacement (inode change) by rescanning — idempotency makes
  that safe.

### Exposure (auth, TLS, headers)

The service binds `0.0.0.0:8000` over plain HTTP on purpose — several analysts on
one LAN share the screen — and **the host firewall is NOT armed** (`ufw` the unit is
`active`, but `/etc/ufw/ufw.conf` says `ENABLED=no` — measured 2026-09-01; do not
trust `systemctl is-active` alone). What stands between the live intel picture and
the network, in layers:

1. **`ATHEER_API_KEY`** (see `.env.example`) — opt-in shared key over `/api/*` + WS.
   Unset = open reads, deliberately (the write token stays fail-closed regardless).
2. **Browser-protection headers** are always on: `nosniff`, `X-Frame-Options: DENY`,
   `Referrer-Policy: no-referrer`, CSP `frame-ancestors 'none'`. A **full** CSP is
   not set from the app: it needs an inventory of every origin the map pulls (CARTO
   vector tiles, planespotters/Wikimedia photos) and one wrong entry blanks the
   basemap silently.
3. **TLS**: terminate at a reverse proxy (caddy/nginx) in front of :8000 if the
   LAN is not trusted; that proxy is also the right place for a full CSP and rate
   limiting. Nothing in-tree assumes it exists.

---

## الأرشفة الشاملة — "لا نترك إشارة"

نموذج موحّد عبر الطبقات الثلاث: **سجلّات خام ملحقية (JSONL) = الحقيقة الأرضية**
(تدوير يومي + gz، **لا تُحذف أبداً**)؛ **القاعدة = طبقة فهرسة مشتقة** بمفتاح
يتضمن `source`/`grp`، تُضغط القديمة، **تُحفظ للأبد**؛ كل جدول قابل لإعادة البناء
من سجله الخام عبر `backfill` idempotent؛ ومُصدِر إشارات واحد (`archive.py`).

**ما يُؤرشف (كل شيء):**

| الطبقة | السجل الخام (حقيقة) | القاعدة (مشتق) |
|---|---|---|
| طيران مدني | `opensky_output/states.jsonl` | `state_vectors` |
| عسكري | `opensky_output/military.jsonl` | `state_vectors` (source) |
| بحري — كل إشارة جديدة | `maritime_output/ais-signals.jsonl` | `vessel_states` + `vessel_snapshots` |
| أقمار — كل TLE | `satellite_output/tle.jsonl` | `satellite_history` (+ `satellite_elements` الحالي) |
| إشارات تشغيلية | `*/signals.jsonl` | `signal_events` |

**الإشارات التشغيلية** (`signal_events`, هجرة 010): اتصال/انقطاع التغذية مع مدة
الجلسة (كشف النشاط المظلم)، خنق المصادر (CelesTrak 403)، دورات الجمع، فشل مرآة
القاعدة — بحقل `detail` JSONB فلا تحتاج أنواع جديدة هجرة.

**الاحتفاظ:** لا سياسة حذف على أي جدول بيانات (تحقّقنا) — السياسة الوحيدة هي
تنظيف TimescaleDB الداخلي لسجل تشغيل مهامه (شهر)، لا تمسّ بياناتنا. الضغط
التلقائي (>7 أيام للمواقع، >30 يوماً للأقمار/الإشارات) يبقي الحجم منخفضاً؛
النسخة الليلية `pg_dump` تغطّي كل الجداول. القرص: ~103GB حر، القاعدة ~0.6GB.

**إعادة البناء الكاملة** (القاعدة قابلة للإسقاط، الخام هو الحقيقة):
```bash
python ingest.py backfill --archive opensky_output          # طيران/عسكري
python tracker_maritime.py backfill --archive maritime_output   # بحري
python tracker_satellites.py backfill --archive satellite_output # أقمار
```

## طبقة الرصد البحري (Maritime AIS)

طبقة إضافية معزولة تُحاكي معمارية الرصد الجوي: مصادر تُطبَّع في **نموذج سفينة
موحّد** (`maritime/schema.py`) عبر **محوّلات (adapters) بواجهة واحدة**
(`maritime/adapters/`)، تتجمّع في `Fleet` (لأن AIS تدفق أحداث لا لقطات)، ثم
تُخزَّن بنفس نمط الطيران: `maritime_output/vessels.json` (استبدال ذرّي — العرض
الحي) + `vessels.jsonl` (إلحاق — مصدر الحقيقة). كل الأزمنة UTC بصيغة ISO 8601.

### التشغيل

```bash
export AISSTREAM_API_KEY=...          # مفتاح مجاني من https://aisstream.io
python tracker_maritime.py            # تغطية عالمية، لقطة كل 30 ثانية
python tracker_maritime.py --bbox 20 30 45 65   # صندوق: lamin lomin lamax lomax
```

### المصادر (كلها مجانية، كل مفتاح عبر متغير بيئة — راجع `.env.example`)

| المصدر | الدور | الحالة | متغيرات البيئة |
|---|---|---|---|
| **AISStream.io** | البث الحي الأساسي (WebSocket) | مكتمل | `AISSTREAM_API_KEY` |
| **Global Fishing Watch** | كشف السفن الصامتة (فجوات AIS من **AIS ساتلي**) | **مُفعّل** (`find_ais_gaps`، متحقَّق حياً) | `GFW_API_TOKEN` |
| **Equasis** | إثراء هوية السفن (مالك/مدير/علم) حسب IMO | معزول، **معطّل افتراضياً** | `EQUASIS_EMAIL` + `EQUASIS_PASSWORD` + `EQUASIS_ENABLE=1` |
| **Copernicus (Sentinel-1 SAR)** | تأكيد بصري لسفينة أطفأت AIS | stub (المرحلة 2) | `COPERNICUS_CLIENT_ID/SECRET` |

كل محوّل مستقل: تفعيل/تعطيل أي مصدر بضبط متغيراته دون المساس بالبقية. المحوّلات
غير الحية (GFW/Equasis/Copernicus) من نوع lookup و`run()` فيها لا يفعل شيئاً،
فتندمج بنفس الواجهة دون تظاهر بأنها تبثّ.

### قيود جوهرية (مذكورة في الكود أيضاً)

- **لا AIS ساتلي مجاني.** AISStream **أرضي فقط**: تغطية موثوقة ~40–60 ميلاً
  بحرياً من الساحل — **أعالي البحار مظلمة**. غياب سفينة عن الخريطة ليس دليلاً
  على غيابها الفعلي. لذا `data_origin` دائماً `terrestrial` للبث الحي (بيانات
  GFW التاريخية وحدها قد تحمل `satellite`).
- **AIS قابل للتزوير (spoofing).** الموقع مُبلَّغ ذاتياً من السفينة — لا
  يُعامَل كحقيقة مؤكدة دون تحقق من مصدر ثانٍ (صورة SAR من Copernicus مثلاً،
  وهذا سبب وجود محوّل Copernicus أصلاً).
- **احترام شروط الاستخدام:** Equasis بلا API رسمية — المحوّل يفرض حد طلب أدنى
  6 ثوانٍ، يحترم `robots.txt`، ويحمل User-Agent تعريفياً، ويبقى معطّلاً حتى
  التفعيل الصريح. AISStream/GFW يُحترم rate limit كلٍّ منهما.

### الانقطاعات = إشارة تحليلية

محوّل AISStream يعيد الاتصال بـ backoff أُسّي، **ويسجّل كل انقطاع** (مدة الجلسة
والسبب) في `maritime_output/ais_gaps.jsonl` — لأن الانقطاع قد يخفي "نشاطاً
مظلماً" (سفينة تُطفئ AIS). تسجيلها الآن يمهّد لتمييز انقطاع التغذية (كل السفن
تختفي) عن اختفاء سفينة واحدة لاحقاً.

### التخزين في القاعدة (منفّذ — هجرة 007)

الأرشيف (JSONL/JSON) يبقى مصدر الحقيقة؛ القاعدة طبقة فهرسة مشتقة كما في الطيران.
هجرة `007_vessel_states.sql` تضيف:

- **`vessel_states`** — hypertable بمفتاح طبيعي `(mmsi, ts, source)` — نفس نمط
  هجرة 006 (`source` يمنع تصادم أي مصدرين). `ts` هو زمن تثبيت الموقع لكل سفينة
  (AIS تدفق أحداث لا لقطات مشتركة). السرعة بالعُقَد (native)، لا m/s. ضغط تلقائي
  للمقاطع الأقدم من 7 أيام (`segmentby: mmsi, source`).
- **`vessel_snapshots`** — صف لكل لقطة جامع (عدّ السفن الحية + الصندوق). عدد
  السفن المميزة لا يُحسب في تجميعة مستمرة (`count(DISTINCT)` غير مدعوم — نفس قيد
  الطيران)، فسلسلة العدّ الزمنية تأتي من هذا الجدول الصغير.

المرآة الحية (`--db-url`) بنفس عقد الطيران المتسامح مع الأعطال: الأرشيف يُكتب
أولاً، وأي خطأ قاعدة يُسجَّل ويُبتلع فلا يوقف الأرشفة إطلاقاً. الفجوات يغطّيها:

```bash
# إعادة بناء vessel_states من الأرشيف (idempotent — المفتاح الطبيعي + ON CONFLICT)
python tracker_maritime.py backfill --archive maritime_output
```

خدمة `scraplan-maritime.service` تمرّر `--db-url ${DATABASE_URL}` فتكتب حياً.
مثبَّت حياً: 653+ سفينة في `vessel_states`، `vessel_snapshots` تسجّل ~435
سفينة/لقطة، صفر أخطاء مرآة؛ تشغيل الـbackfill مرتين ⇒ 1303 ثم 0 (تطابق تام).

### مصدر MarineTraffic (مصدر حيّ مستقل — هجرتا 032/033)

مصدر بحري ثانٍ **مستقل** بجانب AISStream، يأتي من مشروع `mtscraper` المنفصل
(كاشط خريطة MarineTraffic الحية عبر اعتراض المتصفح). **مبدأ العزل صارم:** scraplan
لا يستورد أيّ سطر من كود `mtscraper` ولا يُشغّله، ولا يدخل Playwright/Chromium إلى
عمليات جمع scraplan. الرابط الوحيد هو **عقد البيانات**: ملف `positions.jsonl` الذي
يكتبه `mtscraper`، يقرؤه محوّل `maritime/adapters/marinetraffic.py` بمتابعة الملف
(tail) ويطبّعه إلى نموذج `VesselState` الموحّد عبر
`maritime/adapters/marinetraffic_translate.py`.

**فرق الهوية (جوهر التصميم):** مفتاح MarineTraffic هو `ship_id` (معرّفها الداخلي)،
و`mmsi` غائب في الغالبية. لذا لا يُحشر في مفتاح `vessel_states` المجمّد
`(mmsi, ts, source)`؛ بل يُخزَّن في جدول مخصّص:

- **`mt_vessel_states`** (هجرة 032) — hypertable بمفتاح طبيعي `(ship_id, ts, source)`،
  بنفس دلالات `vessel_states` (السرعة بالعُقَد، الأعمدة الجغرافية من نفس مُصنِّف
  scraplan الواحد بحدّ 12 ميلاً بحرياً، ضغط بعد 7 أيام). `mmsi` عمود إثراء اختياري
  يُملأ فقط حين توفّره MarineTraffic فعلاً — لا يُخترع. `flag` عَلَم مُبلَّغ مباشرة
  (إشارة مجانية حتى بلا MMSI). سلسلة العدّ الزمنية تُعيد استخدام `vessel_snapshots`
  بـ `source='marinetraffic'`.
- **`vessel_xref`** (هجرة 033) — جسر هوية `ship_id ↔ mmsi` يُملأ فقط حين يكون الربط
  حقيقياً (السفينة حملت MMSI)، تمهيداً لعرض تقاطُعي AIS×MarineTraffic لاحقاً.

```bash
# حيّ: يتابع مخرجات mtscraper، لقطة كل 30 ثانية، مرآة إلى القاعدة
python tracker_marinetraffic.py --mt-output ../mtscraper/mt_output --db-url ${DATABASE_URL}

# إعادة بناء mt_vessel_states من الأرشيف (idempotent)
python tracker_marinetraffic.py backfill --archive ../mtscraper/mt_output
```

#### تغطية عدة مناطق بحرية (الخليج + البحر الأحمر…)

منظر MarineTraffic واحد (zoom 7) يغطّي رقعة واحدة. لتغطية أكثر، شغّل **نسخة mtscraper
لكل منطقة** (مركز مختلف، مجلّد مختلف، مع `--persist-out-of-area` لأن الخليج/الأحمر خارج
منطقة اهتمام mtscraper الافتراضية للدول الست):

```bash
# شرق المتوسط (الافتراضي) + الخليج العربي + البحر الأحمر الجنوبي — ثلاث نسخ
python -m mtscraper --lat 33.2 --lon 35.2 --zoom 7 --output mt_output            # شرق المتوسط
python -m mtscraper --lat 27   --lon 51   --zoom 7 --persist-out-of-area --output mt_output_gulf  # الخليج
python -m mtscraper --lat 15   --lon 42   --zoom 7 --persist-out-of-area --output mt_output_red   # الأحمر
```

ثم جامع scraplan **واحد** يتابع المخرجات الثلاثة ويدمجها في أسطول/قاعدة واحدة (المفتاح
`ship_id` فلا تصادم بين المناطق):

```bash
python tracker_marinetraffic.py \
  --mt-output ../mtscraper/mt_output ../mtscraper/mt_output_gulf ../mtscraper/mt_output_red \
  --db-url ${DATABASE_URL}
```

**عن AIS (AISStream):** لا يحتاج توسيعاً — خدمة `scraplan-maritime` تشترك أصلاً بصندوق
`--bbox 12 24 42 63.5` الذي **يشمل الخليج والبحر الأحمر**. الفراغ هناك سببه **ندرة
مستقبِلات AIS الأرضية المجانية** في تلك المياه (لا الصندوق) — والحلّ لـ«العمى» هناك هو
**AIS الساتلي** عبر GFW أدناه.

#### GFW — كشف السفن الصامتة عبر AIS ساتلي (مُفعّل)

حيث يعمى AIS الأرضي المجاني (الخليج/البحر الأحمر/أعالي البحار)، يوفّر **Global Fishing
Watch** ما لا يوفّره: **أحداث انقطاع AIS من AIS ساتلي** — سفينة أطفأت بثّها (إشارة
النشاط المظلم الأساسية). `maritime/adapters/gfw.py` (`find_ais_gaps`) يستعلم عبر العميل
الرسمي `gfwapiclient`، مبنيّ على شكل الـ API الحيّ (متحقَّق بتوكن حقيقي، لا تخمين). لكل
حدث: `{start, end, duration_h, lat, lon, mmsi, flag, intentional, distance_km,
data_origin='satellite'}`. **دليل لا إثبات** — الانقطاع قد يكون عطل مستقبِل أو صمتاً
مشروعاً؛ `intentional` ترجيح GFW لا حُكم. مثبَّت حياً: أحداث انقطاع حقيقية في الخليج
(أعلام PLW/BHS/PAN) والبحر الأحمر. التفعيل: `GFW_API_TOKEN` في `.env` (مجاني من
https://globalfishingwatch.org/our-apis/).

مثبَّت حياً: أرشيف 1068 موقعاً ⇒ 258 سفينة مميّزة في `mt_vessel_states`، تصنيف
جغرافي كامل (قبرص/لبنان/سوريا/إسرائيل في المياه الإقليمية)، وتشغيل الـbackfill
مرتين ⇒ 1068 ثم 0 (تطابق تام). **قانونياً:** شروط MarineTraffic تمنع الكشط (موثّق
في README الخاص بـ `mtscraper`)؛ كل صف موسوم `source='marinetraffic'` فالأصل لا
يلتبس أبداً.

### التقاطُع AIS × MarineTraffic (هجرة 034)

بوجود مصدرين بحريين مستقلين، يربط `backend/vessel_crossref.py` بينهما: أيّ
`ship_id` (MarineTraffic) = أيّ `mmsi` (AIS)؟ لا مفتاح مشترك (MarineTraffic بلا
MMSI)، فالربط **مُستنتَج** من الاسم (المرساة) + الموقع عبر الزمن + الأبعاد، بثقة
صادقة (`strong`/`probable`/`weak`) وبيّنة مرفقة — **دليل لا إثبات**. يُخزَّن في
أعمدة `match_*` على `vessel_xref`، ويُعرَض عبر `GET /api/v1/maritime/crossref`
(ملخّص التغطية + الأزواج + التعارضات). نقطة مهمة في التصميم: **التموضع المشترك
يهيمن على تعارض الأبعاد** — سفينتان لا تشغلان نقطة واحدة، فتطابق الاسم عند نفس
النقطة مع اختلاف الأبعاد = نفس السفينة مع **تعارض أبعاد موسوم** (جودة بيانات أو
إشارة تلاعب)، لا رفض. التشغيل: `python tracker_marinetraffic.py correlate`.
مثبَّت حياً: 258 سفينة MarineTraffic ⇒ **128 رابط (122 قوي، 6 مرجّح)، 130 حصرية،
3 تعارضات**.

---

# API Contract v1 (FROZEN)

Everything below is stable. Additive changes only; breaking changes go to
`/api/v2/...`. All endpoints return JSON. Errors follow FastAPI's
`{"detail": "..."}` with 422 (bad params), 404 (replay: nothing at/before
`at`), 503 (DB and archive both unavailable).

**Time parameters** (`from`, `to`, `at`) accept unix epoch seconds
(`1783504756`) or ISO-8601 (`2026-07-08T10:00:00Z`). Responses carry both
`snapshot_time` (epoch int) and `ts` (ISO-8601 UTC).

**Source parameter (additive, migration 006).** The archive is unified across
feeds; `state_vectors`/`snapshots` carry a `source` in the natural key
(`opensky` | `airplanes_live`). `/history`, `/replay`, `/snapshots`, `/stats`
accept an optional `source` (**default `opensky`**), so every pre-006 call
returns byte-identical results — the frozen contract is preserved. Pass
`source=airplanes_live` to query archived military tracks (trails, replay,
stats). `/live` remains OpenSky-only (military lives at `/military`).

**StateVector** (used by several endpoints):

```json
{
  "icao24": "4b1617", "callsign": "SWR27G  ", "origin_country": "Switzerland",
  "time_position": 1783509251, "last_contact": 1783509251,
  "longitude": 8.5492, "latitude": 47.4612,
  "baro_altitude": 6134.1, "geo_altitude": 6484.6,
  "on_ground": false, "velocity": 160.7, "true_track": 267.4,
  "vertical_rate": -4.2, "squawk": "1000", "spi": false,
  "position_source": 0, "category": null,
  "snapshot_time": 1783509251, "fetched_at": "2026-07-08T11:14:15.926535+00:00"
}
```

Units: altitudes m, velocity/vertical_rate m/s, true_track degrees (0 = north).
Nulls mean "not reported" — clients must handle null altitude/velocity/track,
empty callsign, and `bbox: null`.

### GET /api/v1/live

Current snapshot. `source` is `"db"` normally, `"archive"` when falling back
to `latest.json`.

```json
{
  "snapshot_time": 1783509251, "ts": "2026-07-08T11:14:11+00:00",
  "bbox": {"lamin": 45.83, "lomin": 5.99, "lamax": 47.82, "lomax": 10.52},
  "count": 144, "source": "db", "states": [StateVector, ...]
}
```

### GET /api/v1/history?icao24=&from=&to=&limit=&offset=

Track history for one aircraft (trail/replay source). `icao24` required.
Defaults: `to`=now, `from`=`to`−1h. Max range 7 days. `limit` 1..10000
(default 1000). Points ordered by time ASC.

```json
{
  "icao24": "4b1617", "from": 1783504000, "to": 1783510000,
  "count": 5, "limit": 1000, "offset": 0, "has_more": false,
  "points": [StateVector, ...]
}
```

### GET /api/v1/snapshots?from=&to=&limit=&offset=

Snapshot times in a range (replay scrubber source). Defaults: `to`=now,
`from`=`to`−24h. Max range 7 days. `limit` 1..5000 (default 1000).

```json
{
  "from": 1783504000, "to": 1783510000,
  "count": 27, "limit": 1000, "offset": 0, "has_more": false,
  "snapshots": [
    {"snapshot_time": 1783504756, "ts": "2026-07-08T09:59:16+00:00",
     "count": 171, "bbox": null}
  ]
}
```

`bbox` is null for snapshots whose box wasn't observed live (see Schema).

### GET /api/v1/replay?at=

Full state set at a past instant. Resolves to the nearest snapshot **at or
before** `at` (scrub-friendly); 404 if none. Same shape as `/live` plus
`requested_at`, e.g. `"requested_at": 1783508860, "snapshot_time": 1783508851`.

### GET /api/v1/stats?from=&to=&bucket=

`bucket` ∈ `1m | 5m | 15m | 1h | 1d` (default `1m`). Defaults: `to`=now,
`from`=`to`−1h. Max 5000 buckets per request. Served from the continuous
aggregate. Only buckets containing data are returned (no zero-fill).

```json
{
  "from": 1783504000, "to": 1783510000, "bucket": "5m",
  "buckets": [{
    "bucket_start": "2026-07-08T09:55:00+00:00",
    "state_count": 337, "ground_count": 79, "airborne_count": 258,
    "max_baro_altitude": 13716.0, "max_geo_altitude": 14272.3,
    "max_velocity": 278.4, "snapshot_count": 2,
    "avg_aircraft_per_snapshot": 168.5
  }]
}
```

### GET /api/v1/search?q=&min_alt=&max_alt=&on_ground=&limit=

Filters the **current** snapshot. `q` matches substring of `icao24` or
`callsign` (case-insensitive). `min_alt`/`max_alt` in meters against
`baro_altitude` (null altitude never matches an altitude filter).
`limit` 1..1000 (default 200).

```json
{"snapshot_time": 1783509251, "source": "db", "count": 11,
 "states": [StateVector, ...]}
```

### GET /api/v1/military   *(v1 additive, 2026-07-09)*

Live **unfiltered military** overlay from airplanes.live, collected by the
separate `tracker_airplanes.py` process (isolated from OpenSky; no DB). Units
are normalized to the project's metric contract at collection. `available`
is false (empty states) when the collector is off or its snapshot is older
than 120 s — never an error. Extra fields per state beyond StateVector:
`source`, `actype` (ICAO type), `acdesc` (full name), `reg` (registration).

```json
{ "available": true, "snapshot_time": 1783601379, "age_seconds": 10.5,
  "count": 7,
  "states": [{ "icao24": "ae056b", "callsign": "RCH1875 ", "on_ground": false,
    "latitude": 35.2, "longitude": 24.3, "baro_altitude": 10972.8,
    "velocity": 232.1, "true_track": 298.9, "squawk": "5506",
    "snapshot_time": 1783601379, "source": "airplanes_live",
    "actype": "C5M", "acdesc": "Lockheed C-5M Super Galaxy", "reg": "..." }] }
```

The frontend overlays these on the live map (burgundy glyph, `·ع` list tag,
toggle in the top bar) — **display layer only**. Archival integration (the
`source` column in the state_vectors natural key) shipped in migration 006.

### GET /api/v1/maritime   *(v1 additive, 2026-07-11)*

Live **maritime AIS** overlay from the AISStream collector
(`tracker_maritime.py`; file-based like the military overlay, no DB on this
read path). `speed` is **knots** (AIS-native, not metric). `available` is
false (empty states) when the collector is off or its snapshot is older than
180 s — never an error. **LIMITS:** terrestrial AIS only (~40–60 NM off
shore — the open ocean is dark); AIS is self-reported and **spoofable**
(verify against a second source). The frontend renders these as a SEPARATE
vessel layer (cyan ship glyph, click→popup, `بحري` toggle) — not merged into
the icao24-keyed aircraft list.

```json
{ "available": true, "snapshot_time": 1783801411, "age_seconds": 21.8,
  "count": 404,
  "states": [{ "mmsi": "249781000", "name": "MIAMI LIGHT",
    "lat": 40.7417, "lon": 28.2815, "speed": 0.8, "course": 187.5,
    "heading": 189, "nav_status": 0, "timestamp": "2026-07-11T12:00:00+00:00",
    "imo": "9321483", "callsign": "H3RC", "ship_type_code": 80,
    "ship_type": "tanker", "destination": "TUZLA", "draught": 8.4,
    "source": "aisstream", "data_origin": "terrestrial" }] }
```

### GET /api/v1/satellites?group=  ·  /api/v1/satellite-groups   *(v1 additive, 2026-07-13)*

Orbital-elements layer. Architecture (deliberately NOT N2YO): cache TLEs from
**CelesTrak** (free, keyless, refreshed a few times/day by
`scraplan-satellites.timer`) into `satellite_elements` (migration 009); the
**browser propagates positions locally** via satellite.js (SGP4). A satellite
position is deterministic from its TLE, so the backend only serves elements —
no per-position API, no rate cap on live motion. `available=false` when a
group isn't cached yet.

```json
// GET /api/v1/satellites?group=stations
{ "available": true, "group": "stations", "count": 23,
  "fetched_at": "2026-07-13T11:29:18+00:00",
  "elements": [ { "norad_id": 25544, "name": "ISS (ZARYA)",
                  "tle1": "1 25544U ...", "tle2": "2 25544 ...",
                  "epoch": "2026-07-13T02:54:40+00:00" } ] }
// GET /api/v1/satellite-groups → { "groups": [ {"group","count","fetched_at"} ] }
```

LIMITS (surfaced in the UI): TLE accuracy decays from epoch (~1 km near
epoch, km/day after) — a map, not precision; and CelesTrak is the public
unclassified catalogue (no classified/maneuvering objects).

### GET /api/v1/vessel/{mmsi}?imo=&name=   *(v1 additive, 2026-07-12)*

Best-effort **vessel photo** from Wikimedia Commons (searched by IMO then
name), cached server-side (`vessel_info`, migration 008). Unlike aircraft
(Planespotters is dense), there is **no free ship-photo API of comparable
coverage** — so `photo` is null for most vessels; that is expected, not an
error. Attribution (photographer + Commons File: page + license) is a term
of use and is returned when present. The browser never calls Commons.

```json
{ "mmsi": "240521000",
  "photo": { "thumb": "https://upload.wikimedia.org/.../500px-....jpg",
             "page": "https://commons.wikimedia.org/wiki/File:....jpg",
             "photographer": "Bernard Gagnon" },
  "license": "CC BY-SA 3.0" }
```

### GET /api/v1/aircraft/{icao24}?callsign=   *(v1 additive, 2026-07-08)*

Best-effort enrichment from free external sources, **proxied and cached
server-side** (tables `aircraft_info` / `flight_routes`, migration 004):
airframe + fallback photo from **adsbdb.com**; primary photo from
**planespotters.net** (photographer attribution + link back are terms of
use — the UI must keep them). Route is fetched only when `callsign` is
given and is STANDING data (callsign → usual route), not the filed plan.
Unknown airframes and upstream outages yield nulls — never an error.
TTLs: aircraft 30 d, routes 7 d, negative entries 1 d. The
`ENRICH_CONTACT_UA` env var sets the contact User-Agent planespotters
requires.

```json
{ "icao24": "4bc8c9", "registration": "TC-RDE",
  "aircraft_type": "A321-251NX", "operator": "Pegasus",
  "operator_country": "Turkey",
  "photo": {"thumb": "https://t.plnspttrs.net/...jpg",
            "page": "https://www.planespotters.net/photo/...",
            "photographer": "Donato Bolelli"},
  "route": {"callsign": "PGT972", "airline": "Pegasus Airlines",
            "origin":      {"icao": "EDDS", "iata": "STR", "name": "Stuttgart Airport",
                            "municipality": "Stuttgart", "country": "Germany",
                            "lat": 48.69, "lon": 9.22},
            "destination": {"icao": "LTFJ", "iata": "SAW", "name": "Sabiha Gökçen",
                            "municipality": "Pendik, Istanbul", "country": "Turkey",
                            "lat": 40.89, "lon": 29.30}} }
```

### GET /api/v1/health

```json
{
  "ok": true,
  "db": {"connected": true, "latest_snapshot_time": 1783509251,
          "snapshot_age_seconds": 12.3, "stale": false},
  "archive": {"latest_json_present": true, "age_seconds": 9.1},
  "ingestion": {"fresh": true, "lag_seconds": 0.0}
}
```

`ok` = DB connected AND latest snapshot younger than `STALE_AFTER_SECONDS`
(default 120). `ingestion.lag_seconds` = archive latest − DB latest
(0 means the tailer is fully caught up).

### WS /api/v1/stream

Server-push replacement for polling. Every message:

```json
{"type": "snapshot", "data": { ...same payload as GET /api/v1/live... }}
```

- On connect: the current snapshot is sent immediately.
- Then: at least one message per newly ingested snapshot (within one poll
  cycle). A snapshot MAY be re-pushed with the same `snapshot_time` and a
  fuller `states` set (ingestion can land in two batches) — clients must
  replace their state wholesale on every message; the latest message wins.
- Slow consumers never stall the server: each client has a 2-slot queue
  where the oldest payload is dropped — you always get the latest.
- **Fallback:** clients that cannot hold a WebSocket poll
  `GET /api/v1/live` every 30 s (the collector cadence).
- Reconnect policy is client-side (exponential backoff recommended); the
  smoke UI in `server.py` implements the reference behavior.

### Removed

`GET /api/latest` (pre-v1 alias) and the embedded smoke-test UI were removed
when the React frontend shipped (2026-07-08). `/api/v1/*` is the only API
surface; `/` serves the SPA.
