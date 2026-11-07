# Atheer RADAR (scraplan) — Project State

**The single document a fresh session reads to know where things stand.**

**Measured:** 2026-07-29 **09:30–10:00 UTC** (§§1–9, backend window) · **11:00–11:10 UTC**
(§10, UI window) · **11:50–12:25 UTC** (BE-MEASURE re-measurement pass — §7 storage in full,
the backup layer, transfer-destination inventory, satellite freshness in §3, the
`mt_vessel_states.ts` proof in §4, and the `:8000` staleness note in §2) ·
**13:18–14:46 UTC** (**DISK-2** — the executed maintenance window: upstream frame narrowed, the
global-era archive cut and verified, backup and rotation fixes, §7 rewritten with before/after,
§6.5 closed and §6.7 added) · **15:09–15:20 UTC** (**TIMERS-1** — zone-dwell timer deployed,
identity timer enabled, IMO axis backfilled, §3 freshness table rebuilt, §6.4 closed and §6.8
opened as the one remaining item), on `gis-syr-ThinkCentre-neo-50t-Gen-5`.
Every figure below was produced by a command run against this host, this DB, or this repo
in the session that wrote it. Commands are cited inline so any value can be
re-measured. Nothing here is restated from a prompt, an assessment, or another project.
**Where a figure was superseded, the old value is kept beside the new one** — a corrected
measurement is itself a finding.

**Companion:** [`ENVIRONMENT.md`](ENVIRONMENT.md) (ENV-1) holds the same host facts at
greater depth. §1 below folds in its scope; where they differ, the later measurement
timestamp wins.

> ✅ **Storage is no longer the binding constraint (DISK-2, 2026-07-29 13:56–14:46 UTC).**
> The upstream sampling frame was narrowed and the global-era archive was cut:
> **98.62 GB free (80 % used), project-attributable growth 14.40 → 0.92 GB/day — a 15.7×
> reduction, and net consumption is now negative.** The archive alone fell **9.04 → 0.404
> GB/day (22.4×)** and the database **5.36 → 0.515 GB/day (10.4×)**. Out-of-AOI records in the
> live archive: **0.00 %**, measured over 10,000 lines. Full before/after in §7.
>
> **The ADS-B archive is now a REGIONAL sampling frame, not a global census** — see the
> ground-truth line in §7 before using any data that spans 2026-07-29 13:55:36 UTC.

---

## 0. The rule that governs this file

**Any environment figure used in planning must be measured in-session and cited from this
file. Figures must never be carried across projects.**

This file exists because that rule was broken: the **SAR-pre** assessment reasoned from
hardware and network numbers imported from an unrelated project, and later turns asserted
service facts from partial evidence (one directory; then an assumed `scraplan-*` name).
Three consequences are recorded as findings below — §1 (the CPU and GPU are not what was
assumed), §3 (the throughput figure was a best case), §7 (free disk is nearly gone).

Corollaries the project already runs on, stated in §8: read from source, never from a
prompt; measure before you build; state the method beside the number.

---

## 1. Environment

### Host

| Fact | Measured value | Command |
|---|---|---|
| Hostname | `gis-syr-ThinkCentre-neo-50t-Gen-5` | `hostnamectl` |
| Machine | LENOVO ThinkCentre neo 50t Gen 5 (`12UD005MGP`) | `/sys/class/dmi/id/*` |
| OS | Ubuntu **25.10** (Questing Quokka) | `/etc/os-release` |
| Kernel | **6.17.0-41-generic**, x86-64 | `uname -r` |
| CPU | **13th Gen Intel Core i5-13400** | `lscpu` |
| Logical CPUs | **16** | `nproc`; `lscpu` → `CPU(s): 16` |
| Instruction sets | `avx`, `avx2` — **no AVX-512** | `/proc/cpuinfo` |
| RAM total | **30.5 GiB** (`MemTotal: 31944652 kB`) | `/proc/meminfo` |
| RAM available | **9.8 GiB** (`MemAvailable: 10256128 kB`) | `/proc/meminfo` |
| Swap | 4.0 GiB total — **fully exhausted, 1.2 MiB free** | `free -h` |
| Load average | `1.90 2.40 2.81` · 3,667 threads | `/proc/loadavg` |

⚠ **The CPU is not an i7-12700K.** It is an **i5-13400** — a different part (65 W non-K,
10 cores, no AVX-512, versus a 125 W 12-core K part). `nproc = 16` is correct and complete:
6 P-cores × 2 threads + 4 E-cores × 1 thread = 16. `lscpu`'s flat `10 cores × 2 threads`
multiplies to 20, but that product is an artifact of describing a hybrid CPU with one
thread-per-core figure — only the P-cores are SMT-capable. **There are no missing threads.**

⚠ **Swap is at 100 %.** It was 3.59 GiB / 4.0 GiB at 09:30 UTC and fully consumed by
10:00 UTC, with 22 containers running (31 defined). This host is under real memory pressure
before any new workload.

### GPU — driver installed, no device present

| Fact | Measured value | Command |
|---|---|---|
| `nvidia-smi` | **fails** — "couldn't communicate with the NVIDIA driver" | `nvidia-smi` |
| NVIDIA PCI device | **none** — no vendor ID `10de` on any bus | `lspci -nnk \| grep 10de:` |
| Only display adapter | `00:02.0 Intel Alder Lake-S GT1 [UHD Graphics 730] [8086:4682]` | `lspci -nn` |
| Driver package | **installed** — `nvidia-driver-590` 590.48.01 + dkms/utils/compute | `dpkg -l \| grep nvidia` |
| DKMS module | **built** for `6.17.0-40` and `6.17.0-41` | `dkms status` |
| Module loaded | **no** | `lsmod \| grep nvidia` |
| `/proc/driver/nvidia` | absent · stale `/dev/nvidiactl` remains | `ls` |
| Secure Boot | **disabled** — not the cause | `mokutil --sb-state` |

⚠ **An RTX 5060 is not visible to this machine.** The driver failure is real, but its
stated cause is not what was measured. The software side is *complete and healthy* —
packages installed, DKMS compiled against the running kernel, Secure Boot off. What is
missing is the hardware: **`lspci` enumerates no NVIDIA device at all**, and the only
display controller is the CPU's integrated UHD 730. A built NVIDIA module cannot load with
no `10de` device to bind to, which is exactly the observed state.

So this cannot be recorded as "card present, driver unloaded". **As far as the OS can see,
there is no discrete GPU** — either not physically installed in this chassis, or installed
and failing to enumerate. Distinguishing those requires opening the machine or checking
firmware; out of scope, and nothing was installed or repaired.

**Plan CPU-only:** no CUDA, no VRAM, 16 threads, no AVX-512. Any conclusion that assumed
8 GB of usable VRAM must be re-derived.

### Network

| Fact | Measured value |
|---|---|
| Interface | `enp2s0` up · **negotiated 100 Mb/s** (≈ 12.5 MB/s hard ceiling) |
| Default route | `via 10.0.0.1 dev enp2s0 proto dhcp src 10.0.0.109` |
| Also present | `tailscale0` (100.79.72.78) — **not** on the default route |
| Public egress | `23.234.89.60` (Cloudflare colo LAX) |

**Method** — seven sequential 25 MB HTTPS GETs, reading curl's own `%{speed_download}`:

```
curl -o /dev/null -w '%{speed_download}\n' 'https://speed.cloudflare.com/__down?bytes=25000000'
```

Samples (MB/s): **2.73 · 0.36 · 2.10 · 3.40 · 2.48 · 1.59 · 3.06**

- **Median 2.48 MB/s ≈ 19.8 Mbps** ← the planning figure
- mean 2.24 MB/s · **min 0.36 MB/s (2.8 Mbps)** · max 3.40 MB/s (27.2 Mbps)

**Cross-check, different provider and route:** 100 MB from `ash-speed.hetzner.com` did not
finish in 120 s — 98.2 MB moved, **0.82 MB/s ≈ 6.5 Mbps**. Sustained large transfers are
materially slower than the 25 MB samples.

⚠ **~3.48 MB/s / 28 Mbps is a best case, not a typical case** — it matches my fastest of
seven samples, sits ~40 % above the median, ~10× above the worst sample, and ~4× above the
sustained 100 MB cross-check. **Plan on 2.5 MB/s, with a floor near 0.4 MB/s and ≤ 0.8 MB/s
for multi-GB pulls.** The 100 Mb/s NIC is a separate, harder ceiling.

*Artifact worth knowing:* requests for `bytes=100000000` returned `size_download=1` despite
a `Content-Length: 100000000` header, repeatably. Use 25 MB objects to re-measure.

---

## 2. Deployed services — full measured inventory

**All scraplan units are user-scope** (`~/.config/systemd/user/`). System scope has
**none** — `systemctl list-unit-files | grep -iE 'scraplan|marine|opensky'` returns nothing,
and `systemctl list-timers` shows only 19 OS-stock timers. **Use `systemctl --user`.**

### Database

| Fact | Measured value |
|---|---|
| Container | `scraplan-timescaledb` — healthy |
| Image | `timescale/timescaledb:2.28.2-pg17` |
| Server | **PostgreSQL 17.10** (Alpine musl) · extension **timescaledb 2.28.2** |
| Port | **`127.0.0.1:5435` → `5432`** (loopback only) |
| Mount | volume `scraplan_timescale_data` → `/var/lib/postgresql/data` |
| Size | `opensky` = **9,831 MB** *(`pg_database_size`, 2026-08-04 05:20Z)*. ⚠ **Superseded the "14 GB" this row carried** — that figure predates the Columnstore policy compressing 33 of 59 chunks. Recorded, not investigated: nothing was lost, the DB got smaller (RESTORE-TEST) |
| Migrations applied | **38**, latest `038_air_country_daily.sql` @ 2026-08-04 (DASH-BOARD-3b — the project's first summary table; rollback executed and re-applied before commit) |

### Summary jobs — ⛔ a new class, listed so a rebuild cannot omit it

| Unit | Role | Cadence | Measured |
|---|---|---|---|
| **`scraplan-air-daily.timer`** | writes `air_country_daily` for the **last COMPLETE UTC day** (DASH-BOARD-3b) | **01:37 daily**, `Persistent=true` | 0.91 s / 29 MB per day; 15.26 s for the 23-day backfill |

⛔ **01:37 is chosen against measurements, not taste**: after 20:00 local so the UTC day it
summarises is complete; clear of 03:00 (`scraplan-backup`) and 04:15 (`scraplan-offsite`), the
two heavy disk jobs; clear of 00:05 (rotate), 00:20/06:20 (satellites) and 05:30 (reentry); and
:37 lands on no detector minute (gaps/quality :00/:15/:30/:45 · rendezvous/zone-dwell
:00/:20/:40 · gnss :10/:30/:50 · identity :00/:30). ⛔ Writes are **upsert**, so a missed night
is made up by the next run without duplicating a row — which matters on a host that is
deliberately switched off (GNSS-REVIVE measured four multi-day clean shutdowns).

### API

| Fact | Measured value |
|---|---|
| Unit | **`scraplan-server.service`** — "Atheer RADAR API server (REST v1 + WS + SPA)" |
| Runs | `.venv/bin/python server.py --host 0.0.0.0 --port 8000` |
| Port | **8000 on `0.0.0.0`** — bound to all interfaces (the DB is loopback-only) |
| State | `enabled` · `active (running)` · **pid 908786** · `Restart=on-failure` · `NRestarts=0` |
| Started | **2026-07-29 08:13:17 EDT = 12:13:17 UTC** (`systemctl --user show -p ExecMainStartTimestamp`, `ps -o lstart`) |

✅ **Resolved — the process now serves the code in the repo.** It was **restarted at
12:13:17 UTC**, replacing pid 7099 (started 02:44:19 EDT), which predated commit `3ec3468`
(MT-FRESH, dated 2026-07-29 05:12:04 EDT) by 2 h 27 min and could not serve the per-source
freshness contract. Verified live against the running instance:

```
curl -s 'http://127.0.0.1:8000/api/v1/maritime?limit=1'
→ sources: [{"source":"aisstream","last_fix":"2026-07-29T12:27:55.895267+00:00",
             "age_seconds":26.6,"rows":273,"stale":false}]
→ data_vintage: 2026-07-29T12:27:55.895267+00:00
```

`sources[]` is present, so §10.7's `:8011` workaround is **no longer required**.
`NRestarts=0` means this was a manual stop/start, not a systemd restart.

⚠ **The contract gap the UI window flagged (§10.3) is confirmed live:** `sources[]` carries
**only `aisstream`** — the requested source — so a client with the MT overlay off cannot read
MT's age. Emitting every known source with `rows: 0` when unrequested remains an open backend
item.

### Always-on collectors — four, all `enabled` + `active (running)`

| Unit | Runs | Verified |
|---|---|---|
| `scraplan-collector.service` | `tracker_opensky.py` — Middle East box, DB mirror | pid 7144 |
| `scraplan-maritime.service` | `tracker_maritime.py` — AIS via AISStream.io | pid 7104 |
| `scraplan-military.service` | `tracker_airplanes.py` — airplanes.live overlay | pid 2682 |
| `scraplan-adsb-ingest.service` | `ingest.py follow` — ADS-B Exchange archive → TimescaleDB (`Restart=always`) | pid 7141 |

### Deployed timers — **nine**, all `active (waiting)`, all `Persistent=true`

| Timer | `OnCalendar` | Interval | Runs |
|---|---|---|---|
| `scraplan-quality.timer` | `*:0/15` | **15 min** | per-fix GNSS quality tagger |
| `scraplan-gaps.timer` | `*:0/15` | **15 min** | A1 AIS-gap detector |
| `scraplan-rendezvous.timer` | `*:0/20` | **20 min** | A2 rendezvous detector |
| **`scraplan-zone-dwell.timer`** | `*:0/20` | **20 min** | zone-dwell (safety-zone intrusions) — *deployed TIMERS-1* |
| **`scraplan-identity.timer`** | `*:0/30` | **30 min** | A3 identity-change detector — *enabled TIMERS-1* |
| `scraplan-satellites.timer` | `00,06,12,18:20:00` | **6-hourly** | CelesTrak elements refresh |
| `scraplan-rotate.timer` | `*-*-* 00:05:00` | **daily 00:05** | archive rotation |
| **`scraplan-reentry.timer`** | `*-*-* 05:30:00` | **daily 05:30** | Space-Track TIP reentry pull — *deployed TIMERS-2* |
| `scraplan-backup.timer` | `*-*-* 03:00:00` | **daily 03:00** | DB backup |

That is the complete deployed set (`systemctl --user list-timers | grep -c scraplan` → **9**).
**Corroborated from the data** — every timer-driven detector is fresh; see §3 for the
`max(computed_at)` cross-check, which is the real liveness test.

### Detectors without a timer — resolved in TIMERS-1, one remains by design

| Detector | Status before TIMERS-1 | Now |
|---|---|---|
| **A3 identity-change** (`detect_identity_change.py`) | ⚠ `scraplan-identity.timer` **`disabled`**, `inactive (dead)`, `Trigger: n/a`, absent from `list-units --all` (`LoadState=loaded` only because `status` loads on demand). **9 d 19:05 stale.** | ✅ **enabled + active**, 30 min |
| **zone_dwell writer** (`detect_zone_dwell.py`) | ⚠ **No unit existed** — none installed, none in `deploy/systemd/`. **3 d 05:43 stale.** | ✅ **deployed**, 20 min |
| **IMO integrity backfill** (`backfill_imo_integrity.py`) | ⚠ No unit; the table had grown since 2026-07-25, leaving **509 identities unstamped**. | ✅ **backfilled** (0 unstamped-with-IMO). Still no unit — a suggestion to fold it into `scraplan-identity.service` is in §3, owner's call. |
| **GNSS episodes** (`detect_gnss_anomaly.py`) | No unit; docs say on demand. | ⛔ **THIS ROW WAS WRONG — corrected by GNSS-REVIVE (2026-08-04), see §3.** It was not on demand by design, it was the one detector that never got a unit, and the classification became the cover under which it died: 13 d 17 h with no run. ✅ **Now timer-driven, 20 min (`scraplan-gnss.timer`, `*:10/20`).** |

⚠ **The lesson, not just the fix:** a `disabled` timer and a **missing unit** both present as
**silence** — no failed unit, no error, nothing in the journal. Neither was found from systemd;
both came from `max(computed_at)` in `maritime_events`. **Treat that query as the liveness check
and the unit list as a claim.** `deploy/systemd/README.md` now carries an explicit
`list-timers | grep scraplan` verification step for exactly this reason.

✅ **`scraplan-reentry.service` / `.timer` — installed and enabled in TIMERS-2.** They had
existed in `deploy/systemd/` since 2026-07-18 but were **never copied to
`~/.config/systemd/user/`**, so `SATELLITE_RESEARCH_BASE.md`'s "daily 05:30" claim described a
repo artifact rather than a running timer for 10 days. Both are now deployed and the claim is
true. Verified before enabling: Space-Track credentials present in `.env`, and a manual run
succeeded (exit 0) — the external source had not changed anything in those 10 days.

⚠ **One latent inconsistency, recorded not fixed:** this unit carries
`After=`/`Wants=network-online.target`, which the sibling user units deliberately avoid — the
`rendezvous` unit's own comment says *"no network-online.target (a SYSTEM target absent from the
user session)"*. Measured: `systemctl --user show network-online.target -p LoadState` returns
**`not-found`**, so the dependency is **inert** — a `Wants=` on a missing unit is non-fatal, and
the service starts and completes cleanly (verified, `Result=success`). The practical
consequence: this collector has **no real network-readiness guard**, which matters only for the
`Persistent=true` catch-up run that fires *on boot* after a missed day. The Space-Track client
owns its own backoff and retries, so it should survive; it was left as-is because it demonstrably
works and changing it was outside the TIMERS-2 instruction.

### MarineTraffic — searched by content, in every scope. No unit exists anywhere.

A previous conclusion here was wrong twice (one directory; then an assumed `scraplan-*`
prefix), so this was re-run without any name assumption. **Every check came back negative:**

| Check | Command | Result |
|---|---|---|
| Content grep, all unit dirs | `grep -rilE 'mtscraper\|marinetraffic\|marine_traffic\|tracker_mt\|mt_scrape'` over `~/.config/systemd/user`, `/etc/systemd/system`, `/etc/systemd/user`, `/usr/lib/systemd/user`, `/usr/lib/systemd/system` | **zero matches** for every pattern |
| System scope | `systemctl list-unit-files \| grep -iE 'scraplan\|mtscrap\|marine\|opensky'` | **none** |
| User scope | `systemctl --user list-unit-files` | no MT unit |
| Filesystem-wide | `find / -name '*.service' -o -name '*.timer' \| grep -iE 'mt\|marine'` | only unrelated OS/snap units (`gvfs-mtp`, `systemd-binfmt`, `kmtpd5`) |
| User cron | `crontab -l` | only two **face-recognition** jobs (a different project) |
| System cron | `/etc/crontab`, `/etc/cron.d/`, `/etc/cron.{hourly,daily,weekly}` | OS stock only |
| Process table | `ps aux \| grep -iE 'mtscraper\|marinetraffic'` | **no process** |
| Containers | `docker ps -a \| grep -iE 'mt\|marine'` | **no container** |
| Repo units | `find . -name '*.service' -o -name '*.timer'` | 22 files in `deploy/systemd/` — **no MT unit** |
| The MT repo itself | `/home/gis-syr/Desktop/mtscraper` — searched for `*.service`, `*.timer`, `docker-compose*`, `crontab*` | **no scheduling of its own** |

⚠ **The MT service does not exist under the name `mtscraper`, or under any other name.**
What exists is: a **standalone repo** at `/home/gis-syr/Desktop/mtscraper` (run by hand),
its **stale output store**, and **committed scraplan-side code** (`tracker_marinetraffic.py`,
`maritime/adapters/marinetraffic*.py`, tests, migrations 032–034). The repo's own docs
already state this correctly — §6 of `MARITIME_INTELLIGENCE_BASE.md` says MT "still has
**no systemd service** (run manually, not continuously deployed)". **The documentation was
right; the assertion that a unit exists is what is wrong.** Store staleness in §4.

### Other units on this host (resource context)

| Unit | Scope | State | What it is |
|---|---|---|---|
| `adsb-scraper-collector.service` | user | `enabled` · `active` | ADS-B Exchange collector → `adsb_scraper` writing `/home/gis-syr/Desktop/plandata/data_p1` |
| `bokteq-bot.service` | user | `enabled` · `active` | BOKTEQ Telegram bot (n8n + ngrok) — genuinely unrelated |

⚠ **`adsb-scraper-collector` is not unrelated to this project.** Its `ExecStart` is
`/home/gis-syr/Desktop/scraplan/deploy/systemd/adsb-collector-run.sh` — a script *inside
this repo* — and it writes the very archive that `scraplan-adsb-ingest.service` follows
(`ARCHIVE=/home/gis-syr/Desktop/plandata/data_p1/state_vectors.jsonl`). It is the **upstream
producer of scraplan's ADS-B substrate and the dominant disk consumer on the host** (§7).
Treat it as part of this system.

⚠ **It runs a full Playwright/Chromium browser tree under `xvfb-run` — the dominant consumer
of disk *and* memory.** Measured 2026-07-29 12:30 UTC (`systemctl --user show`, `ps -eo
pid,ppid,rss,etime,cmd`):

| PID | Parent | RSS | Age | Process |
|---|---|---|---|---|
| **8499** *(MainPID)* | systemd | 1.9 MB | 5 h 45 m | `/bin/sh /usr/bin/xvfb-run -a …/python -m adsb_scraper --no-headless --output …/data_p1` |
| 8509 | 8499 | 48.7 MB | 5 h 45 m | `Xvfb :99 -screen 0 1280x1024x24` |
| 8512 | 8499 | 484.6 MB | 5 h 45 m | `python -m adsb_scraper --no-headless` |
| 904769 | 8512 | 323.6 MB | **17 m** | Playwright `driver/node … cli.js run-driver` |
| 904795 | 904769 | 435.1 MB | **17 m** | **`chrome`** |

- **cgroup memory: `MemoryCurrent` 3.32–3.39 GB · `MemoryPeak` 4.76 GB · `TasksCurrent` 1,176.**
- ⚠ **Not ~5.5 GB.** A figure of ~5.5 GB was expected; the cgroup reports **3.32–3.39 GB
  current and 4.76 GB peak**, and the tree's summed RSS is ≈ 1.29 GB. Both cgroup numbers are
  well below 5.5 GB, and RSS undercounts shared pages — neither method reproduces it.
- ⚠ **The tree relaunches its browser by itself.** Measured **without killing anything**: the
  Playwright driver and its `chrome` child are **17 minutes old** while their parent python is
  **5 h 45 m** old, so the browser was respawned under a long-lived parent. The unit also
  carries **`Restart=always`**, so systemd restarts the whole tree independently. **Killing the
  browser is therefore not a way to stop disk growth** — it comes back on its own.

⚠ **The memory figures in the table above are SUPERSEDED and were an early-session sample, not
the tree's steady state** (MEM-CHECK, 2026-08-01). They were taken **17 minutes into a browser
session** and belong to a *previous unit run* — the current run began Jul 29 10:04 EDT
(14:04 UTC), *after* that 12:30 UTC measurement, and `MemoryPeak` resets per run. Re-measured at
a session age of 4 h 50 m: **`MemoryCurrent` 7,981–8,231 MiB · `MemoryPeak` 8.25 GiB ·
`TasksCurrent` 994** — roughly **2.4× the 3.32–3.39 GB recorded here**. The relaunch mechanism
above is confirmed and now attributed: it is a **health watchdog**, not a memory one (§6.10).

⛔ The `adsb_scraper` repo at `~/Desktop/plandata` is **outside this window's ownership** and
was read only. Nothing there was modified.

Also sharing the machine: **22 running containers** (31 defined) — the `mastodon-*` stack
(7), the `scei-*` stack (12), `face-pg-local`, `thro-postgres`, `matrix_backend`
(restart-looping, exit 255), plus `scraplan-timescaledb`. Four Postgres instances run
concurrently on ports 5432/5433/5434/5435/5544.

---

## 3. Data state

Row counts, `psql -c 'select count(*)'`, 2026-07-29 ~09:44 UTC. **The DB is live** — counts
move between queries; treat every figure as point-in-time.

| Table | Rows |
|---|---|
| `state_vectors` | **71,209,716** |
| `vessel_states` | **1,001,405** |
| `snapshots` | 60,309 |
| `maritime_events` | **27,144** |
| `vessel_snapshots` | 23,161 |
| `aircraft_info` | 22,396 |
| `satellite_history` | 16,598 |
| `vessel_identity` | **4,389** |
| `mt_vessel_states` | **3,455** |
| `satellite_info` | 908 · `satellite_reentry` 808 · `satellite_elements` 801 · `satellite_orbit` 781 · `satellite_strategic` 703 · `satellite_passes` 351 |
| `signal_events` | 146 |
| `vessel_xref` | **128** |
| `flight_routes` | 83 · `vessel_info` 57 |
| `schema_migrations` | 37 |

### Time spans

| Table | Span (UTC) | Duration |
|---|---|---|
| `state_vectors` | 2026-07-08 09:59:16 → 2026-07-29 09:44:45 | **20 d 23:45** |
| `vessel_states` | 2026-07-11 19:10:01 → live | **17 d 14:35** |
| `signal_events` | 2026-07-13 14:48:05 → 2026-07-29 06:44:51 | 15 d 15:57 |
| `mt_vessel_states` | 2026-07-20 11:58:55 → **2026-07-25 10:26:04** | 4 d 22:27 — **FROZEN** |
| `maritime_events` | `start_ts` 2026-07-11 19:17:43 → `end_ts` 2026-07-29 09:29:50 | — |

### Per-source splits

**`vessel_states` — single-sourced.** `aisstream` = **100 %** (the only value). This is the
live-currency SPOF; no second AIS source contributes.

**`state_vectors` — three air sources** (24 h window; a full-table group-by times out on
71 M rows with no index on `source`):

| Source | Rows (24 h) |
|---|---|
| `adsbexchange` | **1,601,097** |
| `opensky` | 57,874 |
| `airplanes_live` | 2,738 |

**`mt_vessel_states`** — `source='marinetraffic'`, and **`count(distinct mmsi) = 0`**: the
MMSI column is entirely NULL. MT positions are `ship_id`-keyed and cannot be
identity-resolved, so their value is bounded to the `vessel_xref` bridge. This is structural,
not a bug — it matches the repo's own §6 note.

**`vessel_xref`** — 128 links over 128 distinct MMSIs, **every row `linked_at`
2026-07-25 08:15:23Z** (one batch, never re-run).

### `maritime_events` by type — and detector freshness

### ✅ DASH-BOARD-3b — `air_country_daily`, the project's FIRST summary table

⛔ **A NEW ARCHITECTURAL PATTERN, ADDED DELIBERATELY AND NARROWLY.** DASH-BOARD-pre measured
that this project has "no summary table anywhere". This is the first, and it exists only
because DASH-BOARD-3 measured the cagg road CLOSED for this unit: `count(DISTINCT icao24)` is
not decomposable and no approximate-distinct extension is available.

**Migration 038** (additive; rollback **executed** — dropped, verified absent, re-applied,
verified back — not asserted):

| column | meaning |
|---|---|
| `day, country, source` | PRIMARY KEY. `source` is part of the key and is **never summed across** |
| `distinct_hulls` | EXACT `count(DISTINCT icao24)` for **that day alone** |
| `rows` | position reports behind the count (an effort/density measure) |
| `computed_at` | refreshed on every upsert |

Two CHECKs carry the honesty at the schema level: **`distinct_hulls > 0`** makes a zero-fill
FAIL rather than pass quietly, and `rows >= distinct_hulls` keeps the pair coherent.

⛔⛔ **THE UNIT CHANGES WHEN ROWS ARE SUMMED.** One row is exact for its day. **Summing days
yields HULL-DAYS (بدن-يوم), not distinct airframes** — an airframe present on ten days
contributes 10, and there is no path back to a multi-day distinct count. ⛔ A hull-day total
must never be labelled "number of airframes" nor placed beside `/air/series/country`'s distinct
counts without the difference visible: **two units, and adding them is the /adsb trap in
another costume.** ⚠ Hull-days are not a degradation — for question (أ) they are arguably the
better measure, weighting PRESENCE OVER TIME rather than mere appearance. Which is why the
contract is a **separate endpoint**, `GET /air/series/country-daily`, and not a flag on the
existing one: two units behind one path is how a reader ends up adding them.

⛔ **ONE AGGREGATE DEFINITION, TWO READERS.** `backend.air_daily.agg_sql` is the only place
that says what "an airframe over a country from one feed in one bucket" means; the live
endpoint and the nightly job both render it, and `country IS NOT NULL` is the ingest-time AOI
classifier's own verdict, never re-derived. **Pinned by an equivalence test on a real day**
(the DASH-4 precedent): the stored rows and a fresh `compute_day` must match exactly.

⛔ **MISSING DAYS STAY MISSING — verified by query.** 2026-07-17 / 07-23 / 07-24 / 07-28 have
**no row**, and there are **0 zero-valued rows** in the table. A zero reads «no aircraft flew»;
an absent day reads «we did not observe».

**Measured:** one day **0.91 s / 29 MB RSS** · full backfill of 23 complete days **15.26 s,
354 rows** · table **128 kB** · year-window query through the contract **~2 ms**. Collector
during the backfill: 3,787 → 3,779 rows/5 min — **unaffected**.

⚠ **Disk moved 86.13 → 83.06 GB during this item, and it was NOT this table.** Attributed:
`scraplan-backup.timer` fired at 03:00 and `scraplan-offsite.timer` at 04:15, both inside the
measurement window. This table's contribution is 128 kB; the DB moved 7,286 → 7,304 MB, which
is ~15 minutes of ordinary ingestion. 83 GB free is far from the DISK-1 precedent (34 h to
exhaustion at 56 GB free and 17 GB/day; the burn has been 0.4 GB/day since DISK-2).

**CONSUMPTION LINE:** read `coverage` first and break the line across every gap window; read
`unit`/`unit_warning` before naming any number; never sum across `source`; treat absent days as
absent. `ratio_cutoff` applies (air) and `gap_basis` is UNAVAILABLE (air) — only `host_gaps`
protect this series.

⚠ **RECORDED, NOT EVALUATED (owner's item (ج)):** an approximate-distinct route would reopen
the cagg road. `postgres_hll` is Apache-2.0 and would satisfy the open-source-only rule,
whereas some `timescaledb_toolkit` features are TSL — ⛔ that is to be MEASURED, not assumed.
Also unmeasured here: availability on the `timescale/timescaledb:2.28.2-pg17` image itself, and
the impact of any upgrade. Not assessed in this item.

---

### ✅ DASH-BOARD-3 — air country × time (`GET /api/v1/air/series/country`) — ⛔ NO STORAGE BUILT

**Phase A said don't build, and Phase A was right — for a reason the item did not anticipate.**

⛔ **A CORRECT CONTINUOUS AGGREGATE IS NOT BUILDABLE FOR THIS UNIT, AT ANY COST.** The unit is
DISTINCT AIRFRAMES, and `count(distinct icao24)` **is not decomposable**: one-minute distincts
cannot be rolled up to a day (an airframe seen in forty minutes would count forty times), and
daily distincts cannot be summed to a week. `state_vectors_1m` works precisely because it
stores only `count(*)`, `sum()` and `max()`. Doing this correctly needs an approximate-distinct
sketch, and **`timescaledb_toolkit` / `hll` are not available** on this instance (installed:
plpgsql, timescaledb 2.28.2). A fixed-granularity cagg would be silently wrong at every other
granularity — and question (أ) asks how the ranking moves over time, i.e. at several.
⇒ **9.2 GB of write amplification was not spent on a 7.3 GB database, and no migration was
added.** Disk before/after: 86.09 → 86.13 GB available. DB 7,288 → 7,286 MB.

**MEASURED COST, and ⚠ my first figures were wrong — corrected here rather than quietly:**

| window | rows | real query |
|---|---|---|
| 24 h | 944 k | **948 ms** |
| 7 d | 8.5 M | **1,431 ms** |
| 14 d | 48 M | **2,388 ms** |
| 30 d (= full history) | 78 M | **3,302 ms** |
| **1 year (projected, 345 M)** | | **≈ 11.5 s** — ⛔ not interactive |

⚠ **The first Phase-A curve reported 506 ms for the full range and was measuring the wrong
thing**: every timing was wrapped in `SELECT count(*) FROM (…)`, which lets the planner skip
the sort and the row materialisation the endpoint actually performs. `EXPLAIN ANALYZE` settled
it — the same plan (Gather Merge, 5 workers) takes ~3.3 s either way. ⚠ A second wrong
attribution is also recorded: a 3,503 ms reading was blamed on `($1 IS NULL OR ts >= $1)`
defeating chunk exclusion; re-measured, that guard is worth ~10 %, not 7×, and the wrapper was
the whole difference. ⚠ And DASH-BOARD-pre's own «27 d ≈ 8 s» was a linear extrapolation from
one uncompressed day — the true figure is 3.3 s, because 18 of 25 chunks are columnstore.

**So the window is bounded by a measured number**: default `hours=720` (30 d), the largest
window under ~3.3 s. Longer windows are **answered, not silently truncated**, and `cost_note`
states the price. A year remains available and remains slow, which the caller is told.

**CONSUMPTION LINE:** read `coverage.series_start` and both gap lists and break the line across
every window. ⛔ Then read `coverage.gap_basis` — for AIR it is **UNAVAILABLE**: `signal_events`
records feed_connect/disconnect for `aisstream` only, so an air outage that did not coincide
with a host shutdown appears in **no gap list at all**. Only `host_gaps` protect this series,
and the contract says so rather than looking complete. ⛔ Never sum across `source` — the
totals are keyed `country|source` and no country-only total exists in the payload, because
opensky / adsbexchange / airplanes_live have measured coverage of 74.46 / 41.19 / 70.88 % and
an airframe seen by two feeds would be counted twice. ⛔ `ratio_cutoff` is carried and **applies
here**, unlike every maritime contract. ⛔ The unit is airframes: no total crosses domains.

⚠ **The storage decision is now the owner's, and it is not "build the cagg".** The options are
(a) live with a bounded window, (b) accept a DIFFERENT unit at coarse granularity (airframe-days
rather than distinct airframes, honestly renamed), or (c) install an approximate-distinct
extension. ⛔ None of the three should be chosen by inference from a dashboard's convenience.

⚠ **A finding worth its own item, measured here:** `PREPARE`/`EXECUTE` and asyncpg both take
~3.3 s where a simple-protocol `psql` run of the same statement took ~0.56 s — until the
wrapper was removed, at which point all three agree at ~3.3 s. `plan_cache_mode =
force_custom_plan` changes nothing. There is no prepared-statement penalty here; the earlier
gap was an artefact of how it was timed. Recorded so nobody re-derives a penalty that does not
exist.

**Gates:** collector unaffected (5-min rolling 3,220–3,586 across the whole read-heavy session
against a 3,367–3,374 baseline) · disk unchanged · no migration · 9 contract tests that follow
their source (`tests/test_air_country_series.py`).

---

### ✅ DASH-BOARD-2 — the four maritime question contracts (ب · ج · د · هـ)

⛔ **ALL FOUR EMBED THEIR DASH-BOARD-0 COVERAGE** through the one shared builder
(`build_series_coverage`, extracted from `/coverage` for exactly this). None recomputes gaps;
a second gap calculation would be free to disagree with `/coverage` about the same stream.

⚠ **BUT TWO OF THE FOUR EMBEDDED THE WRONG STREAM UNTIL UI-0** — the builder was shared, the
argument was not. Corrected below; the sentence above was never the problem, the `STREAMS_BY_KEY`
lookup at each call site was.

| question | endpoint | unit | source | cost now → **1 year** |
|---|---|---|---|---|
| **ب** outage frequency | `GET /maritime/series/ais-gaps` | detected AIS-gap events | `maritime_events` ← ais-gap-detector-v1 over AISStream | 60 ms → **178 ms** |
| **د** multi-pattern hulls | `GET /maritime/series/multi-pattern` | **hulls** (IMO-stitched) | `maritime_hull_events` — the view `/maritime/composite` ranks from | 30 ms → **188 ms** |
| **ج** rendezvous geography | `GET /maritime/series/rendezvous-points` | detected rendezvous events | `maritime_events` ← rendezvous-detector-v1 | ~10 ms → trivial (N≈700/yr) |
| **هـ** flag distribution | `GET /maritime/flags/multi-pattern` | hulls with >1 pattern type | `maritime_hull_composite` × `vessel_identity` | 30 ms → trivial |

**The year figures are MEASURED, not extrapolated**: a TEMP copy of `maritime_events` at
**777,855 rows** (15.2× today = one year at the measured 2,090 rows/day) was built and the
same aggregations run against it. ⛔ Production tables untouched. Both stay interactive.

**CONSUMPTION LINE, per contract — what the frontend reads before drawing:**

- **ب** — read `split_by="classification"` and **never sum across keys**. `outage-excluded`
  (17,059) and `jamming-excluded` (300) are ⛔ **our own downtime**, removed by the detector as
  confounds; a single summed "AIS gaps" line plots our outages as ships going dark, inverting
  the question. Every bucket is keyed; nothing is an unlabelled total.
- **د** — `multi_pattern` and `hulls_with_any_event` are ⛔ **numerator and denominator, not two
  categories to add**. ⚠ And the series is dominated by DETECTOR AVAILABILITY: measured weekly
  7 / 54 / 5 / **159** / 10, where the 159 is the week `zone_dwell` + `identity_change` came
  onto timers (2026-07-29 15:11–15:12Z) with `gnss_anomaly` following on 2026-08-04. Read the
  `detector` gaps in `coverage.gaps[]` before reading any trend — ⛔ **and until UI-0 only ONE of
  the five was there** (see UI-0 below): this sentence was true as an instruction and false as a
  description of the payload.
- **ج** — ⛔ **the data does not support the question as asked, and the contract says so in the
  payload.** Measured: **47 events over 22.81 days ≈ 2.06/day**. `density_refused` carries the
  refusal so a client cannot bin behind the contract's back. Points are returned as they are;
  every one is `candidate` — a lead, never proof. Clustering becomes answerable when N is an
  order of magnitude larger.
- **هـ** — read `denominator` **with** every `pct`. ⛔ `attribution_field` names WHICH derivation
  produced the flags (`vessel_identity.flag_country`); the project's other one (`flag_iso`, from
  the MMSI MID prefix) disagrees by ~6 points and neither is presented as the other. The
  «غير منسوب» slice is **always emitted, even at zero**.

⛔ **No maritime contract carries `ratio_cutoff`.** The 2026-07-29 13:55:36Z boundary is the AIR
sampling frame; no sea stream is sourced from it, so stamping it here would invent a
discontinuity that never happened. Asserted by test, not left to care.

⚠ **TWO MEASURED FIGURES DIFFER FROM DASH-BOARD-pre, both because the right source was used:**
· **(هـ) the denominator is 285 hulls, not 306.** 306 was the MMSI-grouped count; the
  IMO-stitched hull view — the one the existing composite contract uses — gives 285.
· **attribution is 284/285 = 99.65 %, not 100 %.** There is **one genuinely unattributed hull**,
  so the «غير منسوب» slice is not merely structural today: it has a real member.
· ⚠ **(د) weekly counts are 7/54/5/159/10, not 5/35/7** — same cause: hull-stitched, not
  MMSI-grouped.

**Every contract is tested to FOLLOW ITS SOURCE** (`tests/test_maritime_series.py`, 23 passing):
each is driven against a fake pool whose rows the test controls, and changing the rows must
change the response. ⛔ Two lessons force that shape — the GNSS tests that passed for 13 days
*because* the detector was dead, and DASH-BOARD-0's own first output that would have satisfied
any test hard-coding `observed_fraction = 0.0`.

---

### ✅ UI-0 — a composite series carries EVERY contributor's detector window

⛔ **THE DEFECT: A COMMENT THAT DESCRIBED WHAT THE CODE DID NOT DO** — the BE-SUMMARY-2 shape.
The **د** bullet above instructed the reader to break on the `detector` gaps and named
`identity_change` + `gnss_anomaly` as the cause of the 159 spike. The payload proving it carried
**one window of five**: `/maritime/series/multi-pattern` embedded
`STREAMS_BY_KEY["zone_dwell_events"]` — one contributor of six.

And the miss was not only the gaps. **Every field of the embedded coverage described that one
detector instead of the series** (measured live, before → after):

| field | before (`zone_dwell_events`) | after (`hull_multi_pattern`) |
|---|---|---|
| `stream` | `zone_dwell_events` | `hull_multi_pattern` |
| `unit` | `detected zone-dwell sessions` | `hulls (IMO-stitched), not vessels and not events` |
| `series_start` | 2026-07-11T19:55:36Z | **2026-07-11T19:18:40Z** (the series' own first row) |
| `rows` | 60 | **8,854** |
| `detector` gaps | **1** | **5** |
| `source` gaps | 0 | **76** (the view reads AIS; the stream now declares `signal_source`) |
| `observed_fraction` | 0.2419 | **0.5538** |

⛔ **THE CONTRIBUTORS ARE SIX, NOT FIVE — read from migration 037, not from the roadmap.**
`maritime_hull_events` admits `rendezvous` and `identity_change` unconditionally, plus `ais_gap`,
`gnss_anomaly`, `imo_integrity` and `zone_dwell` at `classification='candidate'`. Measured rows
in the view: ais_gap 7,514 · gnss_anomaly 1,143 · rendezvous 96 · zone_dwell 60 ·
identity_change 42 · **imo_integrity 1**.

⚠ **THE SIXTH OWNS NO TIMER AND NO STREAM.** `imo_integrity` is written by
`scripts/backfill_imo_integrity.py` — a hand-run backfill, never on a timer, so it is never
systematically observed. It is stated in the composite's `caveats` rather than emitted as a gap
window, because a window spanning the whole series carries no position information and would make
the break-the-line rule degenerate. Its measured influence is why that is proportionate: **1
event on 1 hull, and 0 hulls reach `n_pattern_types > 1` because of it** (289 multi-pattern hulls
with it, 289 without).

**HOW IT WORKS** — `Stream.contributes_from` names the contributing stream keys, and
`coverage.detector_gaps()` composes `detector_gap()` over them. ⛔ One builder, one primitive:
`build_series_coverage` is still the only place coverage is made, and the single-stream path is
byte-identical (`zone_dwell_events` still reports 0.2419 after the change).

⛔ **AND `observed_fraction` EXCLUDES DETECTOR WINDOWS ON A COMPOSITE — the measurement forced
it.** The five windows union to **23.43 d of a 23.64 d span** (gnss came onto its timer four hours
before the fix), so counting them reports **0.0088** for a period holding 8,657 rows. A detector
window on a composite blinds **one axis of six**; the series kept measuring on the others, so the
count inside it is *depressed by an unknown amount*, which is not the claim «unobserved» and
cannot be folded into one scalar. The fraction therefore counts only the kinds that blind every
axis at once — `host` and `source` — and the new **`observed_fraction_basis`** field says so in
the payload. ⚠ The detector windows stay in `gaps[]`, where they do their real work.
⚠ Note the contrast that validates the split: single-detector `gnss_anomaly_events` legitimately
reports **0.0094**, because there the detector really was the whole series.

⛔ **FAILS CLOSED, PROVEN BY DETONATION.** A contributor key absent from the registry, a
contributor owning no timer, or a timer whose symlink cannot be stat'd yields no window — so
dropping it silently would make an unmeasured detector look like a fully observed one
(BACKUP-HARDEN 1's shape). `detector_gaps` returns them as `unmeasured`, and they reach **both**
`gap_basis` (`⛔ DETECTOR WINDOWS MISSING — … this list is incomplete, not empty`) and
`observed_fraction_basis` (`⛔ UPPER BOUND`). Detonated live by wiring a `ghost_events`
contributor and restarting the server, not asserted.

**THE ITEM-4 AUDIT — every contract asked the same question.** ⛔ One more instance found:

| contract | counts | embedded | verdict |
|---|---|---|---|
| `/coverage` | each stream itself | itself | ✓ |
| `/maritime/series/ais-gaps` | `maritime_events` ← ais_gap | `ais_gap_events` | ✓ exact |
| `/maritime/series/multi-pattern` | 6 detectors via `maritime_hull_events` | ~~`zone_dwell_events`~~ → `hull_multi_pattern` | ⛔ **fixed** |
| `/maritime/series/rendezvous-points` | `maritime_events` ← rendezvous | `rendezvous_events` | ✓ exact |
| `/maritime/flags/multi-pattern` | `maritime_hull_composite` `n_pattern_types>1` — **the same 6** | ~~`identity_change_events`~~ → `hull_multi_pattern` | ⛔ **fixed — the same defect** |
| `/air/series/country` | `state_vectors` | `air_states` | ✓ exact |
| `/air/series/country-daily` | `air_country_daily` ← `state_vectors` | `air_states` | ✓ windows correct — ⚠ see below |

⚠ **`/air/series/country-daily` is sound on gaps but its `coverage.unit` disagrees with its own
`unit`** — «airframe position reports» beside a response whose unit is **hull-days**. The *windows*
are right: the summary is fully backfilled (measured 23 days present, 2026-07-08 → 08-03; the 4
absent days are the proven host-off dates), so it inherits `state_vectors`' coverage exactly, and
a detector window from `scraplan-air-daily.timer` (enabled 2026-08-04 08:15Z) would be a **false**
gap over backfilled data. ⛔ **Left unfixed deliberately, and reported instead**: a `hull_days`
stream is a scope decision for the owner, not a silent addition inside UI-0. `coverage.rows`
likewise reports `state_vectors`' count, not the summary's 354.

**Gates:** 6 new tests in `tests/test_coverage.py` (19 passing there), each proving the window by
moving its source — a timer mtime moved, a contributor added, a symlink unlinked — ⛔ no frozen
value. Full suite **756 passed / 3 failed**, and the 3 are the pre-existing debts, proven by
running the suite at HEAD with only these four files stashed (**750 passed / the identical 3**).
Server restarted; all **10** streams build; both fixed contracts verified by `curl`.

---

### ✅ DASH-BOARD-0 — the coverage contract (`GET /api/v1/coverage`)

⛔ **EVERY TIME SERIES IS READ WITH THIS OR NOT DRAWN.** The rule this phase runs on: *a
contract that can express a year but cannot express «this period was not observed» is the
more dangerous of the two.* So the shape is built for long windows **and** for their holes.

**THE CONSUMPTION LINE — what the frontend reads before plotting anything:**
> Read `series_start`, then `host_gaps[]` **and** `gaps[]`, and **break the line across every
> window in both**. A connected line across a gap asserts a measurement nobody took. If
> `not_a_series` is set, do not draw the stream at all. Read `unit` before combining anything —
> air counts airframe reports, sea counts detector events, space counts passes, and no total
> crosses them. Read `ratio_cutoff` before computing any share.

**THREE GAP KINDS, THREE SOURCES, NONE HAND-MAINTAINED:**

| `kind` | means | derived from | scope |
|---|---|---|---|
| `host` | the machine was off | `journalctl --list-boots -o json` (0.09 s, cached 10 min) | **global** — every stream at once |
| `source` | the upstream feed dropped while the host ran | `signal_events` → **`reconstruct_outage_windows`** — the same machinery DISK-1 used for the 45.46 % figure, ⛔ not a second calculation | per stream |
| `detector` | the phenomenon happened and nothing was looking | mtime of the timer's `timers.target.wants` symlink (the `systemctl enable` instant) | per detector — ⛔ and on a COMPOSITE stream, **once per contributing detector** (UI-0) |

⛔ **The third kind is the one that reads as good news when omitted**: a series that steps up
on its deployment date looks like a rising phenomenon and is a rising *detector*. Measured
windows, live: `ais_gap` 8.00 d · `rendezvous` 8.02 d · `zone_dwell` 17.80 d ·
`identity_change` 17.70 d · **`gnss_anomaly` 23.40 d**.
⚠ Two of these differ from what the DASH-BOARD-0 brief assumed: `identity` and `zone_dwell`
were enabled **2026-07-29 15:11–15:12Z** (TIMERS-1), not around 08-01.

**Measured coverage on the live store** (`observed_fraction`, gaps unioned so overlaps count
once): air_states **0.7465** · sea_states **0.5970** · ais_gap_events 0.3637 ·
rendezvous_events 0.4663 · zone_dwell 0.2316 · identity_change 0.2318 ·
**gnss_anomaly 0.0004** · satellite_tle 0.6862. The air figure independently reproduces
DASH-BOARD-pre's 75.08 % hourly measurement from a different method.
⚠ These drift daily — re-read them from `/coverage`, never from this line (§9). The registry is
**TEN** streams since UI-0 added the `hull_multi_pattern` composite; ⛔ its fraction is not
comparable with the others', because a composite's excludes detector windows by construction
(`observed_fraction_basis` states which inputs each one counted).

⛔ **`ratio_cutoff`** carries 2026-07-29 13:55:36Z on air streams only: absolute counts may
cross the frame boundary, ratios may not (`country IS NULL` 96.05 % → 29.6 % on adsbexchange).
⛔ **`satellite_passes` is declared `not_a_series`** — one recomputed window, 359 rows; drawing
it as a stream with gaps would imply a past that was never stored.

**Two defects found in this item's own first output, and fixed rather than shipped:** gaps
were being summed instead of unioned (the three kinds nest, so five streams reported a false
`observed_fraction` of 0.0), and host gaps reaching back to February were being attached to
July series (a 2,541 h window from before any stream began). Both now clipped and unioned.

⚠ **AND ONE QUESTION THIS ITEM DELIBERATELY DID NOT ANSWER.** Making the GNSS endpoint test
property-based surfaced that an affected-zone cell at **2026-07-16T09:00:00Z** sits inside an
outage window *reconstructed now*. That is not necessarily a defect — the detector subtracts
the windows that existed **when it ran**, and the feed-event history has moved since. Which
of the two is authoritative is GNSS-endpoint work, not coverage work; it is recorded here
instead of being hidden behind a weaker assertion.

---

**Current — after TIMERS-1/2 (measured 2026-07-29 16:05 UTC), corrected by GNSS-REVIVE
(2026-08-04 05:38 UTC).** ⛔ The clause that used to close this sentence — «the one exception is
on-demand **by design**» — was false, and it is struck rather than quietly edited: that
"exception" was a dead detector. **All six stored detectors are now timer-driven and fresh**,
with no exception of any kind:

| `event_type` | `method` | Rows | `max(computed_at)` | Age | Timer |
|---|---|---|---|---|---|
| `ais_gap` | `ais-gap-detector-v1` | **27,291** | 2026-07-29 16:00:26Z | **fresh** | ✅ 15 min |
| `identity_change` | `identity-change-detector-v1` | **29** | 2026-07-29 16:00:25Z | **fresh** | ✅ 30 min *(enabled in TIMERS-1)* |
| **`imo_integrity`** | `imo-integrity-v1` | **1** | 2026-07-29 16:00:26Z | **fresh** | ✅ 30 min *(folded into the identity cycle, TIMERS-2)* |
| `zone_dwell` | `zone-dwell-v1` | **16** | 2026-07-29 16:00:30Z | **fresh** | ✅ 20 min *(deployed in TIMERS-1)* |
| `rendezvous` | `rendezvous-detector-v1` | **31** | 2026-07-29 16:00:26Z | **fresh** | ✅ 20 min |
| `gnss_anomaly` | `gnss-anomaly-detector-v1` | ~~805~~ → **2,091** | ~~2026-07-21 11:35:03Z~~ → **2026-08-04 05:38:34Z** | ~~8.0 d~~ → **fresh** | ✅ **20 min** *(deployed in GNSS-REVIVE)* |

> ### ⛔ GNSS-REVIVE (2026-08-04) — the paragraph below was wrong, and the error had a cost
>
> **`gnss_anomaly` was not an exception by design. It was dead.** Measured before the fix: last
> run **2026-07-21 11:35:03Z (13 d 17 h)**, newest episode **2026-07-19**, while a `--dry-run`
> at deployment derived **2,091** episodes against the **805** stored — 13 days of episodes the
> store never received.
>
> **The stated rationale does not distinguish it.** «Honouring the `--dry-run` discipline» was
> the reason given for leaving it unscheduled; measured, **all five behavioural detectors carry
> `--dry-run`** (rendezvous 3 occurrences, ais_gaps 3, identity 3, zone_dwell 4, gnss 4), and
> three of them have run on timers throughout. `--since` likewise exists on `ais_gaps`, which is
> timer-driven. The detector's own docstring says it **«mirrors detect_rendezvous.py»** and uses
> the same idempotent recompute-and-replace that every sibling unit cites as the reason frequent
> runs are safe. There was no property that made this one manual — only a unit that was never
> written.
>
> **Cost measured before deploying**, exactly as the sibling units document theirs: **2.50 s
> wall · 3.02 s CPU · 308 MB RSS** — at or below the ~2 s rendezvous run it mirrors. There was
> never an affordability reason either.
>
> ✅ **Now `scraplan-gnss.timer`, `*:10/20`**, offset ten minutes so it cannot race
> `scraplan-quality` (`*:0/15`), which writes the per-fix verdicts it aggregates, and so four
> detectors never land on one minute of a shared disk. First run verified: **805 → 2,091 rows,
> newest episode 2026-08-04, `computed_at` 23 s old.**
>
> ⛔ **AND THE GENERAL RULE THIS COST US.** An "on demand by design" label is indistinguishable
> from a dead detector in every observable way — no failed unit, no journal line, only a
> `max(computed_at)` that stops moving. So the label is no longer allowed to stand alone:
> **every detector is either TIMER-DRIVEN AND FRESHNESS-CHECKED, or explicitly marked
> "on-demand: absence does not mean absence of the phenomenon" wherever its output is read** —
> including in any time series. The full classification table now lives in
> `deploy/systemd/OPERATIONS.md`, which before this item listed **no behavioural detector at
> all**, so a host rebuilt from it would have raised every collector and left the entire
> analysis layer dark.

~~**GNSS episodes are the deliberate exception**, not an oversight: the episode detector is a
recompute-and-replace over the whole `position_quality` history and the docs specify it runs on
demand, honouring the `--dry-run` discipline first. The per-fix quality tagger that feeds it
*is* timer-driven (`scraplan-quality.timer`, 15 min).~~ — struck through by GNSS-REVIVE; the
per-fix tagger being timer-driven was true and remains true, and it is precisely why the
episode layer aged invisibly: its INPUT kept updating while its OUTPUT stood still.

**Scraplan timers: 8** (`systemctl --user list-timers | grep -c scraplan`) — gaps, quality,
rendezvous, **zone-dwell**, **identity**, satellites, rotate, backup.

### What the two gaps had cost, measured

| Detector | Before TIMERS-1 | After | Events that had accumulated undetected |
|---|---|---|---|
| `identity_change` | 11 rows, **9 d 19:05** stale, timer `disabled` | 30 rows, 18 s | **+19** — reflag 20 / rename 5 / retype 5 (rank high 25 · medium 1 · low 4) |
| `zone_dwell` | 12 rows, **3 d 05:43** stale, **no unit existed** | 16 rows, 0 s | **+4** safety-zone intrusions |

⚠ **Neither failed loudly.** No unit was in an error state and nothing was logged — a disabled
timer and an absent unit both present as silence. Both were found from `max(computed_at)` in
`maritime_events`, not from systemd. **That query, not the unit list, is the real liveness
check.** `deploy/systemd/README.md` now carries a `list-timers | grep scraplan` verification
step for the same reason.

⚠ The appendix's A3 figure — *"11 (7 re-flag / 2 rename / 2 retype)"* — is a **2026-07-20
snapshot** and is superseded by the 30 above. ⚠ And `rendezvous` moved **32 → 30** earlier in
the day: recompute-and-replace revises counts **downward** as well as up, so a count is only
meaningful beside its `computed_at`.

### IMO-integrity axis — backfilled (TIMERS-1 step 3)

The axis had not been re-stamped since 2026-07-25, so identities added after that date carried
no verdict. Run once manually (`--dry-run` first, per the documented discipline):

| Measure | Before | After |
|---|---|---|
| Identities | 4,434 | 4,434 |
| **Stamped** | **2,921** | **3,430** |
| Unstamped | 1,513 | 1,004 |
| **Unstamped that carry an IMO** | **509** | **0** |
| `valid` | 2,875 | **3,379** |
| `malformed` | 36 | **39** |
| `checksum_fail` | 10 | **12** |
| `max(imo_computed_at)` | 2026-07-25 10:32:35Z | **2026-07-29 15:15:33Z** |

The remaining **1,004 unstamped rows carry no IMO at all** and stay NULL by the documented
invariant — a NULL-IMO row is never stamped. The runner asserts it: *"post-backfill:
imo_method IS NULL = 1004 (must equal NULL-imo = 1004) → OK"*. **0 `contested`**, and the
collision guard stayed latent (0 events).

✅ **The 8 revised rows — investigated, and it is by design, not an idempotency defect
(TIMERS-2 step 3).** The write-guard changed **517** rows while only **509** were newly stamped,
so 8 already-stamped rows were revised. Compared against the pre-run state extracted read-only
from that morning's `pg_dump` (03:03, which still held the 2026-07-25 verdicts):

- In all 8 the **IMO and verdict were unchanged** (`valid` → `valid`); the only change was
  `imo_flags` **NULL → `{reflag_clean}`**.
- `classify_shared` fires only at **`len(members) ≥ 2`**. All 8 were **single-MMSI** at the
  previous stamp, so no flag was correct then.
- Each then **gained a same-IMO sibling MMSI after 2026-07-25 08:27:38** — five siblings absent
  from the spine entirely that morning, four present but last updated 07-26/07-27. IMO `9241346`
  gained two.
- **Idempotency confirmed directly:** two back-to-back runs changed **7** rows then **0**.

The arithmetic closes independently: 471 newly stamped among rows present in the dump + 8 revised
+ 38 newly stamped among the 88 identities that arrived after 03:03 = **517**.

⇒ **New evidence → re-evaluation, by design.** Documented where it belongs, in the axis's own
spec: `MARITIME_INTELLIGENCE_BASE.md` §2.9(a.1). The consequence for studies is recorded there —
a verdict is **as of its `imo_computed_at`**, and an absent `reflag_clean` means "none visible
when stamped", never "no re-flag exists".

✅ **Now timer-driven (TIMERS-2 step 2), no longer manual.** `--dry-run` 0.47 s, real run
**0.61 s / 31.8 MB RSS** — at that cost a dedicated unit would be more moving parts than work,
so it runs as a **second `ExecStart=` in `scraplan-identity.service`**, every **30 minutes**,
after the A3 detector. `Type=oneshot` executes ExecStart lines sequentially and aborts the rest
on a non-zero exit, so the order is guaranteed rather than assumed.

Verified on a real scheduled cycle (12:00:23 EDT), two distinct PIDs in log order: the detector
(`stored 29 identity_change candidates`) then the axis (`3442 imo-bearing … rows CHANGED: 7`,
invariant `imo_method IS NULL = 1009 = NULL-imo → OK`), unit `Result=success`.
`max(imo_computed_at)` advanced **15:15:33Z → 16:00:26Z**; stamped **3,430 → 3,442**.

⚠ **A caveat on judging that step:** `max(imo_computed_at)` only advances when the guard actually
changes a row, so on a cycle where no new identity has arrived it legitimately stays put. Judge
the run by the unit's `Result` and the invariant line, **not by the timestamp alone**.

### ⛔ Bidi isolation for machine-shaped values in Arabic prose (BE-FIX-1, 2026-08-03)

**The rule: any date- or time-shaped value placed inside generated Arabic prose is wrapped in
`U+2068` FIRST STRONG ISOLATE … `U+2069` POP DIRECTIONAL ISOLATE at generation.** Bare
numbers, thousands-separated numbers and Latin identifiers are NOT wrapped.

The defect it fixes was measured in the running UI, not inferred: in «ممرّ متوقَّع يبدأ
2026-08-03 23:49Z» a `Range` over the rendered text put the date at x=1224 and the time at
x=1190 — the date to the RIGHT of the time, read as «23.49Z 03-08-2026».

⚠ **The frontend cannot fix this, which is why it is a generation rule.** The host surface
`.rc-sub` is already `unicode-bidi: plaintext; direction: rtl` and that is correct for the
prose — the paragraph direction and the Latin object name both read properly. What no CSS can
reach is the order of two number runs INSIDE one string; parsing generated prose in the client
to wrap them would be a second definition of the text.

⛔ **Isolation, not formatting.** No Arabic date format is imposed — formatting a date is a
DISPLAY decision and belongs to the renderer. The value's bytes are unchanged; two invisible
marks are placed around it.

⚠ **WHICH values need it — measured in a browser (the authoritative UBA), each placed inside a
real Arabic sentence with its first/last glyph positions compared:**

| value | bare | wrapped |
|---|---|---|
| `2026-08-03 23:49Z` | **REVERSED** | ok |
| `2026-08-01` (TLE epoch) | **REVERSED** | ok |
| `1,234` · `325` · `10,972` · `32.3h` | ok | ok |
| `NAVSTAR 65 (USA 213)` · `Boeing 737` · `AFR1234` | ok | ok |

Latin runs are strong-L and absorb adjacent digits, which is why object names and callsigns
were never affected. ⚠ **The TLE epoch was found by measuring, not reasoning** — a single
hyphenated group looks safe and is not.

**The inventory, empirical over 238 live alerts:** only the military/satellite family emits
date shapes — 14 rows with a date and time in `headline`, 14 with a date in `caveat`.
`implausible` (106 rows) and `maritime` (114) carry **none**, and the emergency templates have
no date slot at all. So **two value expressions** needed wrapping, both in `_satellite_ar`:
the pass start time and the element epoch. Wrapping anything else would scatter invisible
characters for no gain.

⛔ **NEVER on machine fields.** `ts`, `vintage`, `snapshot_time` are parsed by consumers; the
marks belong only in prose a human reads. Verified after the change: `ts` and `vintage` come
back clean and `datetime.fromisoformat` still parses them.

Verified at byte level through the live API (not by appearance): the headline carries
`e2 81 a8 … e2 81 a9` around the timestamp, all 14 satellite rows hold exactly one pair, every
row with an epoch is wrapped, and **0** non-satellite rows contain an isolate.

⚠ **A caution for whoever adds the next family.** These characters are invisible and count
toward `len()`. They are safe today because nothing compares this prose literally and no test
asserts on it (checked). A future test that pins a headline string, or any code that measures
its length, must expect them.

### The Arabic register — `headline` / `why` / `caveat` (BE-SUMMARY-1, 2026-08-02)

Maritime alerts now carry three Arabic fields beside the English `summary`. **Maritime
only** — the other four families are deliberately untouched (see the note at the end).

**The consumption line for the frontend.** Every `family: "maritime"` alert in
`GET /api/v1/alerts` gains:

```
headline: str   # the occurrence AS OBSERVED — what, where, how often. No interpretation.
why:      str   # why it ranks here; carries severity_basis in readable Arabic
caveat:   str   # what this does NOT mean. Non-empty on every maritime row.
```

⚠ **Empty string means "this family has no Arabic register yet", not "no caveat applies".**
Measured on a live call (105 maritime of 165 alerts): all three non-empty on **105/105**
maritime rows, and **0** non-maritime rows populated. A client must therefore not assume the
fields are present family-wide, and must not fall back to `summary` for maritime — `summary`
is the terse English and is still emitted for every family until the frontend consumes these.

The templates live in `backend/summaries_ar.py`, one readable resource, so the wording can be
reviewed **as language** rather than by reading control flow. Nothing composes a sentence at
runtime: fixed constants with slots, filled from fields that already exist on `CompositeHull`.

⚠ **`gnss_anomaly` and `zone_dwell` state PRESENCE, never a count** — `CompositeHull` has no
count column for them (`n_gaps` / `n_rendezvous` / `n_identity_changes` exist; there is no
`n_gnss` on this model and no dwell count anywhere). Measured in real timelines: 164 GNSS and
24 dwell events, so the patterns are real and common — only the counts are absent. They are
not invented, and not softened to "several": a vague quantity reads as a precise one withheld.

⚠ **WHICH patterns a hull has comes from `timeline[].event_type`, not from the `n_*` counts,
and that is not a second derivation.** Measured over 400 real hulls: `n_pattern_types` equals
the number of DISTINCT timeline event types in **400/400** cases, so reading the timeline
reproduces the existing field exactly. It exceeds the number of non-zero `n_*` fields in
**71/400** cases — which is the same gap: GNSS and dwell have no counters.

⚠ **A DEFECT THIS EXPOSED IN THE ENGLISH `summary`.** For those 71 hulls the English line
lists only the counted patterns while `n_pattern_types` counts one more — e.g. hull 9169055
reads `1 gap · Israel waters · Liberia` with `n_pattern_types = 2`, the second pattern (GNSS)
invisible. The Arabic `headline` names every pattern it counts. The English line is left
as-is on purpose: removing it is the frontend-consumption commit's job, not this one's.

⚠ **`involves_grey` fires on 278/400 hulls (70%).** The clause «وعلمها مدرَج على قوائم رقابية»
is therefore common rather than distinguishing. It is accurate — a registry fact about the
flag — but an owner reading it as a rare marker would be misreading it. `imo_quality`, by
contrast, is rare: 368 valid, 30 null, and only 2 of 400 carrying a spine caveat.

⚠ **Arabic country names now exist in TWO layers.** `backend/summaries_ar.AOI_ISO_TO_NAME_AR`
(for generated prose) and the frontend's `T.country.names` (for its own chrome). ISO is the
join key and `country_iso.AOI_NAME_TO_ISO` remains the single source for *which* countries
exist; adding one there without adding it to the Arabic map yields a bare code in the text,
which is visibly incomplete rather than silently wrong. Both Arabic maps must move together.

⚠ **Flags render as reported, in Latin** — «علم التسجيل: Liberia». `flag_country` is any
world registry, not one of the seven, so there is no Arabic map to use and inventing one for
~200 countries is out of scope. Vessel names likewise stay as broadcast.

### The alert contract — `countries` and `importance` (DASH-1, 2026-08-01)

`/alerts` is a **live read over existing signals**, not a stored table, so these are contract
fields over columns that already existed — **no migration**.

| Field | What it is | Why it is shaped that way |
|---|---|---|
| `Alert.countries: list[str]` | The AOI countries the alert **touches**, ISO alpha-2 | A **list**, never a scalar: a hull's events span several AOI waters and a satellite pass crosses several borders. A scalar would silently drop observed facts. |
| `Alert.importance: int` | **Display-ordering** rank, 0 = first | A **second axis**, orthogonal to `severity` — see §6.9. ⚠ Not a threat score. |
| `AlertEntityRef.live_key` | The key that opens the **live** payload | `id` stays the durable IMO; `live_key` is the MMSI **currently** transmitting it. |
| `AlertEntityRef.alt_keys` | The hull's other MMSIs, most-recent first | IMO→MMSI is **one-to-many** across a re-flag; earlier identities are history, not noise. |
| `AlertsResponse.countries_filtered` / `sorted_by` | Echo of the filter and the ordering applied | A filtered view must never be mistaken for the whole picture. |

⚠ **`countries` is WHERE, not WHOSE.** It must never be confused with `flag_country`,
`origin_country` or `operator_country`, which are registrations. Live proof: a maritime alert
reads `countries=['IL']` while its summary says *"Israel waters · United Kingdom"* — the UK is
the flag and correctly absent from the geography.

**Empty `countries` means "not attributable to the seven", never "unknown".** Verified live and
distinguished from a missing position: two military aircraft carry `[]` because the shared
classifier places them 1,128 km and 1,079 km out over the Aegean — positioned, and genuinely
outside the AOI plus its 50 km buffer.

**Where each family's country comes from** — all five were already computed; the envelope simply
had nowhere to put them:

| Family | Source | Note |
|---|---|---|
| `emergency` | `/live` `country` / `near_country` | classifier-stamped at ingest — a read |
| `military` (air) | ⚠ **classified from lat/lon at read time** | the airplanes.live overlay bypasses the classifier entirely (measured: **0** of its states carry `country`), so this family re-classifies through the **same** shared classifier |
| `military` (satellite) | `satellite_passes.countries` + `near_countries` | `near_countries` was **not being selected at all**; adding it is why a pass reading *"over Iraq"* now carries `[IQ, TR]` |
| `maritime` | `CompositeHull.aoi_waters` (M1) | |
| `implausible` | **new** `array_agg(DISTINCT country/near_country)` in the query | a drifting spoofed track can cross waters |

**Live coverage at first measurement:** military 13/15, maritime 99/100, implausible 109/109.
The empties are honest — the Aegean pair, and one hull whose only event touched no AOI waters.
`emergency` had **no live instance** (0 squawks), so it is covered by unit tests, not a curl.

### The single-vessel live contract — `/maritime/vessel` (DASH-4, 2026-08-02)

`GET /api/v1/maritime/vessel?mmsi=<MMSI>` → **`VesselLiveResponse`**. A live read over the
existing snapshot and store — **no migration, and no new vessel shape.**

**Why it exists.** Measured in DASH-2f: aircraft and satellites had a single-entity route to
their **live** contract (`/search?q=<icao24>` → `StateVector[]`; `/search/satellite?q=<norad>`
→ the TLE element). **Vessels did not** — the live `Vessel` lived only inside `/maritime`'s
full list, so the home's left panel rendered the **summary** contract and honestly named the
four fields it could not reach: **heading · destination · nav_status · draught**. All four now
arrive.

| Field | What it is |
|---|---|
| `mmsi` | the MMSI queried, echoed |
| `state` | **`live`** or **`last_known`** — never blended |
| `vessel` | **the `Vessel` contract, unchanged** — the same 19-field object `/maritime` serves |
| `age_seconds` | now − `vessel.timestamp`; what stops an old fix reading as current |
| `snapshot_time` / `snapshot_age_seconds` | **live only** — the snapshot's stamp and the file's age |
| `identity_as_of` | **`last_known` only** — vintage of the *static* identity, a **second** vintage |
| `not_reported` | contract fields NULL because **nothing reported them** |
| `n_fixes` | `last_known` only — stored fixes behind the claim |

**⛔ There is exactly one definition of a live vessel.** `_read_snapshot()` is now the single
place a live maritime snapshot is read, and **`/maritime` calls it too**. For `state=live` this
endpoint returns the **same dict** from the **same snapshot** through the **same model** — not a
re-query and not a re-shape. Verified literally, not asserted: the single payload and
`/maritime`'s row for the same hull, from the same snapshot generation, are **byte-identical**
(`tests/test_vessel_live.py::test_single_vessel_is_byte_identical_to_maritime`, which fails on
any divergence). Same argument as DASH-1/3's *"do not invent a second definition of inside
Syria"*.

**Three outcomes, and the third is an error on purpose:**

| Outcome | Meaning | Shape |
|---|---|---|
| `state="live"` | in the snapshot, inside the **180 s** window (same rule as `/maritime`) | 200 |
| `state="last_known"` | MMSI **known**, not transmitting now — last stored fix **+ its age** | 200 |
| — | MMSI **never seen** | **404** `unknown MMSI: <x>` |

**"Not observed" ≠ "does not exist"** (the 2c′/2e rule). An unknown MMSI is never an object of
nulls, which a client would render as a real hull.

⚠ **`last_known` carries TWO vintages and names both rather than blending them.** AIS position
reports (types 1/2/3) carry **no static data**, so the latest stored row has NULL statics for
**~79 % of vessels** (measured: **665 / 3,223** latest rows carry a name). Dynamics + voyage
come from **one** fix, stamped `timestamp`; static identity comes from the spine, stamped
`identity_as_of`. Measured, the spine **strictly dominates** — `coalesce(fix, spine)` resolves
**2,835** names, *identical* to the spine alone — so statics are single-sourced and **no
per-field vintage mixing was needed**. That is also what the live snapshot does internally: the
collector merges statics onto the current position, which is why the live payload is complete
while a raw `vessel_states` row is not.

⚠ **`not_reported` separates device silence from source shape.** `ship_id` and `flag` are NULL
for **every** AIS vessel (measured **0/556** in the live snapshot — `ship_id` is MarineTraffic's
key, `flag` its field), so they are **structural** and deliberately **excluded**; listing them
would misdescribe them. `draught` is the standing example of a genuine absence: **~77 %** of
vessels have none on their latest stored fix.

⚠ **Nothing is classified or enriched here.** `source` / `data_origin` are carried through **as
stored**, never fabricated — the panel currently synthesises `source="vessel_states"`,
`data_origin="search/vessel"`, and that stops. **`position_quality` is deliberately absent:** it
is not part of the `Vessel` contract, and adding it would be the second shape this endpoint
exists to prevent — `/search/vessel` already serves it, **read, never recomputed**.

⚠ **AIS-keyed by construction.** MMSI addresses the **aisstream** feed. The MarineTraffic fleet
is `ship_id`-keyed and usually carries **no** MMSI, so it is not searched here —
`/maritime?source=marinetraffic` remains the route to it. Source shape, stated not hidden.

**Measured cost per call** (n=25 each, median): **live 2.6 ms / 564 B** · `last_known` 6.9 ms ·
404 5.0 ms. For scale: `/maritime` **5.8 ms / 193,930 B**, `/search/vessel` **22.3 ms**. The
single path is **2.2× faster than the fleet** and **8.6× faster than the summary fallback** the
panel uses today, at **1/344th** the bytes — because it parses the same file but constructs
**one** `Vessel` instead of ~550. The `last_known` path is **one index-backed row** (0.4 ms
execution against **1.45 M** rows). ⛔ **Neither path loads a fleet to return one hull.**

### The Arabic register, second family — implausible + `flag_iso` (BE-SUMMARY-2, 2026-08-02)

**Implausible alerts now carry the same three fields**, and every alert gains `flag_iso`.
Measured on a live call after restarting the API: **200/200** implausible rows carry all
three, **106/106** maritime unchanged, **0/17** military — untouched, as intended.

```
headline / why / caveat : str   # now populated for family ∈ {maritime, implausible}
flag_iso                : str | None   # ISO 3166-1 alpha-2, or null. NEVER guessed.
```

#### ⛔ Why this family's tone is calibrated separately, and could not reuse maritime's

A maritime composite reports **behaviour** of a hull. An implausible position is not
behaviour at all — it is a near-certain **data defect**, a physically impossible fix. So the
subject of every sentence is the **observation**, never the vessel:

| Raw fact | The sentence emitted | Does it describe the observation, or attribute an act to the vessel? |
|---|---|---|
| `on_land` × 941 | «سُجّل موضع داخل اليابسة 941 مرة قرب مياه إسرائيل» | **Observation.** «سُجّل» — *a position was recorded*. Passive; the record is the subject. |
| `impossible_speed` × 1 | «ورد موضع يستحيل بلوغه من الرصدة السابقة مرة قرب مياه تركيا» | **Observation.** The impossibility is a property of the geometry *between two fixes*, not an act. |
| `teleport_return` × 2 | «ورد موضع شاذّ منفرد يعود المسار بعده مباشرة إلى موضعه مرتين» | **Observation.** Describes the out-and-back *shape* of the record. |
| 3 kinds together | «رصدات موضع غير معقولة في سجلّ هذا البدن…: موضع داخل اليابسة، موضع يستحيل بلوغه، قفزة وعودة» | **Observation.** «رصدات» (observations) — never «مخالفات» or «حوادث». |
| rank = LOW | «الرتبة منخفضة: هذه رصدات يُرجَّح أنها عيب في البيانات لا سلوك من السفينة، والسفينة في الغالب طرف متضرّر لا فاعل.» | **Restates** `SEVERITY_BASIS["implausible_position"]`; forms no new verdict. |
| — | caveat: «…خطأ في فكّ ترميز رسالة AIS، أو تشويش على إشارة GNSS، أو موضع منتحَل، أو خلل في المستشعر. ⛔ لا يدلّ هذا على سلوك متعمّد…» | **Names the technical causes outright** and states the victim/actor ambiguity rather than implying intent. |

⛔ **Nothing here implies deliberate deception.** The existing G rules separate the victim
from the actor and a single alert decides neither — jamming and spoofing happen *to* vessels
as much as *from* them. The caveat says so in as many words.

⚠ **The three flags are distinct observations and get distinct sentences.** Measured over
7,006 implausible fixes: `on_land` **4,249** · `impossible_speed` **3,530** ·
`teleport_return` **1,190**. Their meanings are read from `gnss_anomaly.py` R1/R2 and
described, not re-derived; the per-kind counts are aggregated by the alerts query, so this
layer counts nothing itself.

#### ✅ `flag_iso` coverage: 10.8 % → **93.7 %** (MID-ISO-1, 2026-08-03)

BE-SUMMARY-2 shipped `flag_iso` resolving only the seven AOI names — 35 of 323 alerts —
because the project owned no MID→ISO source. It now owns one.

| | before | after |
|---|---|---|
| alerts carrying a code | 35 / 323 (**10.8 %**) | **311 / 332 (93.7 %)** |
| maritime | 11 / 106 | **114 / 114 (100 %)** |
| implausible | 24 / 200 | **197 / 200** |
| distinct codes | 4 | **42** |

The four dominant registry flags now resolve, which was the point:
**Panama → PA · Liberia → LR · Malta → MT · Marshall Islands → MH.**

**The asset — [`backend/data/mid_iso.json`](../backend/data/mid_iso.json)**

| | |
|---|---|
| Source | `github.com/michaeljfazio/MIDs`, `mids.json` — the SAME upstream `mmsi_mid.json` was consolidated from |
| Licence | **Apache-2.0** (LICENSE at repo root; SPDX confirmed via the GitHub API). Redistribution of this derived file is permitted with attribution. ⚠ Upstream ships the Apache template with an **unfilled copyright line**, so attribution is to the repository and its author, not to a named holder |
| Retrieved | **2026-08-03** (upstream last pushed 2025-09-09) |
| Method | `curl` of `raw.githubusercontent.com/.../master/mids.json`, then `value[0]` per MID. Upstream values are `[alpha2, alpha3, subdivision, name]`; **only alpha-2 is copied** — names already live in `mmsi_mid.json` and duplicating them would create two spellings of one fact |
| Rows | **292 MIDs, 292 with a non-empty alpha-2 (no blanks), 228 distinct codes** |

⛔ **The join is MID → ISO DIRECTLY; the country name is never an intermediary.** Name
matching fails silently on exactly the rows that matter — measured, **8 of 292** names differ
in form alone between the two assets (`Syria`/`Syrian Arab Republic`,
`United States`/`United States of America`, `Qatar`/`Qatar (State of)`, `Timor-Leste`/…).
Verified at retrieval: the two assets have **identical 292-MID key sets** — zero MIDs with a
name but no code, zero with a code but no name.

**Manual review, 20 rows** (fixed seed `20260803`, reproducible): all 20 correct, no
ambiguity found. Includes the dependent-territory cases that vindicate the direct join —
`204 Azores → PT`, `331 Greenland → GL`, `347 Martinique → MQ`.

⚠ **The 21 remaining nulls are honest.** 17–18 are `military` (aircraft and satellites have
no MMSI at all). The other 3 are ship MMSIs on **unallocated MIDs** (200, 600, 342 —
`ELSEC_TEST` among them, a synthetic transmitter). Both assets agree they are unresolvable,
so `mmsi_to_flag` and `mmsi_to_iso` can never disagree about what a ship station is.
**A null means "this registry cannot be resolved to a code", never "no flag".**

#### ⛔ Two country layers, deliberately NOT merged

| | `backend/country_iso.py` | `backend/data/mid_iso.json` |
|---|---|---|
| Scope | the **seven** AOI countries | **global** registries (228 codes) |
| Role | **governs** scoping, filtering, `?country=` | **descriptive only** — display |
| Answers | "is this one of the seven?" | "which registry is this hull flagged to?" |

Merging them would let a **Panamanian registration read as a country of interest**. They are
joined by nothing; `mmsi_to_iso` lives in `mmsi_flag.py` (which already owns MMSI semantics)
and never calls `country_iso`.

⚠ **`flag_country`/`flag_iso` is an ADMINISTRATIVE attribute — whose, never where.** §3
already warns against confusing it with `countries` (the geographic attribution), and adding
a **code raises that risk rather than lowering it**: a client will render it as a flag glyph,
and a flag glyph reads as a *location* far faster than a country name does. It is a
point-in-time fact too — an MMSI changes on re-flagging. Any surface showing it must keep
saying «علم التسجيل», which is why BE-SUMMARY-1 made those words mandatory.

### The Arabic register, third family — emergency squawks (BE-SUMMARY-3, 2026-08-03)

**Emergency alerts now carry the same three fields.** Maritime and implausible are
unchanged (verified after restart: 114/114 and 200/200). Military is still untouched (0/20).

```
headline / why / caveat : str   # now populated for family ∈ {maritime, implausible, emergency}
```

#### ⛔ This family's calibration is the INVERSE of the other two

Maritime patterns and implausible fixes are **inferred** — from behaviour, or from data
quality. A squawk is neither: **7500/7600/7700 is a code the aircraft itself set and
broadcast.** The occurrence is *reported*, not deduced.

⛔ **So the «X alone does not indicate Y» pattern is wrong here.** It protected the other
two families; here it would **weaken a true statement**. «أعلنت الطائرة» is correct in a way
«أخفت السفينة» never was — the difference is that the aircraft is the **reporter**.

✅ **The reservation moves; it does not disappear.** It shifts from *"did it happen?"*
(settled — the code was set and transmitted) to *"how much does it mean?"*, on two grounds
that are mandatory in every caveat:
1. the code names a **category, not a severity** — a 7700 covers a trivial technical fault
   and a critical one alike, and does not distinguish them;
2. **mis-setting is common** — crew oversight, a keying error, a transient code during a
   frequency change.

| Raw fact | The sentence emitted | Does it convey what was broadcast, or interpret its cause / rate its severity? |
|---|---|---|
| `squawk=7700`, callsign `IGO083` | «أعلنت طارئاً عامّاً (7700) — الطائرة IGO083» | **What was broadcast.** «أعلنت» — the aircraft declared. Active voice is correct because the aircraft is the reporter. No cause named. |
| `squawk=7600`, callsign `IGO1449` | «أبلغت فقدان الاتصال اللاسلكي (7600) — الطائرة IGO1449» | **What was broadcast.** Reports the declared condition, not a diagnosis of the radio. |
| `squawk=7500`, no callsign, `country=LB` | «بُثّ رمز الاستيلاء غير المشروع (7500) — الطائرة ذات المُعرّف 728673 في أجواء لبنان» | **What was broadcast.** ⚠ **Passive on purpose** — a code *was transmitted*. «أعلنت اختطافاً» would put a claim in the crew's mouth. |
| `severity_basis` = *"active emergency squawk — high by definition"* | «الرتبة مرتفعة **بحكم التعريف**: رمز طوارئ نشط، **لا تقديراً لحالة هذه الرحلة**.» | **Restates** the existing basis. «بحكم التعريف» is what stops the HIGH badge reading as a verdict on this flight. |
| — (caveat, all codes) | «…**يحدّد فئة الحالة لا خطورتها**… **والضبط الخاطئ وارد وشائع**… **ولا يحمل هذا التنبيه مدّة الرمز ولا استمراريته**» | **Bounds the meaning.** Never questions the occurrence. |
| — (caveat, 7500 only) | «7500 هو رمز الاستيلاء غير المشروع **كما تعرّفه ICAO**… **لا يصف ما يجري على متنها، ولا يؤكّد وقوع استيلاء، ولا يُقرأ ترجيحاً لأيّ رواية**» | **Definition and bounds only.** No narrative, no likelihood, no dramatising verb. |

⛔ **No cause is ever implied.** The aircraft declared; we do not know why. «أعلنت طارئاً» is
what happened — «تعرّضت لمشكلة فنّية» would be invention.

⚠ **7500 gets the LEAST prose, deliberately** — it is the most serious code in the system, so
it carries the fact, ICAO's definition, and the bounds, and stops.

⚠ **Duration is not in the contract, and the caveat says so rather than hinting.**
`build_emergency_alerts` reads squawk / icao24 / callsign / position; `ts` is the
**snapshot's vintage** — not when the code was set and not how long it has been held. Nothing
says or implies "briefly" or "sustained".

⚠ **All three codes have a template, and drift fails loudly.** An import-time assertion ties
`SQUAWK_MEANING_AR` to `EMERGENCY_SQUAWKS`; a new code reaching the family without a sentence
raises at import instead of shipping an alert with silently empty Arabic. **Verified by
removing a template and confirming the assertion fires.**

#### ⚠ Rendered from INJECTED facts, not from the database — stated plainly

**There were zero live emergency squawks** when this shipped: 390 of 518 aircraft carried a
squawk, **none** of them 7500/7600/7700 (BE-SUMMARY-1 found the same). So the three sentences
above were produced by `build_emergency_alerts` from **real aircraft** on the live feed —
real `icao24`, real callsigns, real positions — with **only the squawk value substituted**.
Nothing was fabricated, and **no emergency alert has yet been rendered from the database.**
The blank-callsign case is real too, which is why the ICAO24 fallback path is exercised above.

### The Arabic register, fourth family — military aircraft (BE-SUMMARY-4, 2026-08-03)

**Military AIRCRAFT alerts now carry the three fields.** Verified after restart: 5/5
`military_aircraft` populated; maritime 114/114 and implausible 200/200 unchanged.

⚠ **SCOPE — `military_aircraft` only, and the split is bigger than it looks.** The military
family carries **two** signal types, and the live 18 split **13 satellite / 5 aircraft**. So
this item covers **5 of 18** military alerts. `military_satellite` is deliberately deferred:
a pass is a **forward SGP4 prediction**, so it needs future tense («يمرّ», never «رُصد») and
a third kind of caveat (SGP4 accuracy decays with TLE age). Its own item.

#### ⛔ The commitment this could most easily have broken

The project has written down three times that **military presence is INFORMATION, NEVER a
threat**; `SEVERITY_ORDER` puts the family at INFO *below* LOW on purpose, and DASH-2b proved
with 19 gates that its badges do not move. The risk here is a sentence contradicting a badge
— and a reader resolves that in favour of the sentence.

**Verified, not asserted:** after this change `severity` is still `info` for **both** military
signal types and `severity_basis` is byte-identical. A word audit over every military template
found **zero** intent verbs; «تهديد» and «خطر» appear **only in explicitly negated form**
(«لا حكم تهديد» · «ليست منخفضة لضعف الخطر — هذه الفئة خارج محور الخطر أصلاً»).

**⛔ Verbs refused, and why** — each attributes intent, and would read as a finding although
the fact beneath is a bare coordinate:

| Refused | Why |
|---|---|
| «توغّلت» (penetrated) | asserts trespass; the airspace may be its own or international |
| «اقتربت من» (approached) | asserts a target — we hold a coordinate, not an intention |
| «حلّقت قرب» (flew near) | same, **plus «قرب» is a proximity judgement we never computed** |
| «راقبت» · «تجسّست» · «استطلعت» | assign a **mission**; the source carries no mission field at all |
| «تمركزت» (positioned itself) | asserts deliberate placement, from a single snapshot |
| «رصدنا» (we detected) | makes *us* the actor of an interception; we read a public feed |

✅ **The safe form is a recorded observation with the RECORD as subject and no motion verb** —
«سُجّل وجود طائرة عسكرية …» — the same construction as «سُجّل مكوثها» (maritime) and
«سُجّل موضع» (implausible).

| Raw fact | The sentence emitted | Does it describe a recorded presence, or attribute an act / intent / proximity? |
|---|---|---|
| `callsign=UN263`, `acdesc=AGUSTA AW-169`, `baro=472 m`, `country=LB` | «**سُجّل وجود** طائرة عسكرية **في أجواء لبنان** — نداؤها UN263، من طراز AGUSTA AW-169، **على ارتفاع 472 متراً**» | **Recorded presence.** Passive, no motion verb. Altitude is the bare figure — «منخفضاً» would be an assessment. |
| `callsign` blank, `acdesc=BOEING KC-135R/T`, no country | «سُجّل وجود طائرة عسكرية **دون إسناد جغرافي في هذه اللقطة** — **مُعرّفها** ae0427، من طراز …» | **Recorded presence.** The missing geography is **declared**, not silently omitted; the ICAO24 is a labelled fallback. |
| `severity_basis` = *"military aircraft presence — informational, not a threat"* | «**معلوماتي**: الوجود مُسجَّل، لا حكم تهديد. **الرتبة ليست منخفضة لضعف الخطر — هذه الفئة خارج محور الخطر أصلاً.**» | **Restates** the basis. ⛔ The second clause exists so INFO is never read as "mildly dangerous": it is a different *kind* of statement, not a small one. |
| — (caveat, element 1) | «التعريف «عسكرية» **ليس حكماً لنا**: يأتي كما هو من تغذية airplanes.live عبر مسارها المخصّص للطيران العسكري (**/v2/mil**)، ونحن نقصره على النطاق الجغرافي فحسب…» | **Names the actual source.** See the ⚠ below. |
| — (caveat, element 2) | «والوجود في المجال الجوّي الدولي أو داخل حدود الدولة نفسها **أمر مشروع بطبعه**، والتنبيه لا يقول غير ذلك.» | **States lawfulness outright**, rather than leaving it to inference. |
| — (caveat, element 3) | «**ولا يُستنتج من هذا مهمّة ولا غرض ولا جهة آمرة**: المصدر يحمل موضعاً ومُعرّفاً فقط.» | **Bounds the record** to what it contains. |

⚠ **The identification basis was READ, and it is not what a generic phrasing would have
claimed.** Measured from `tracker_airplanes.py`: the collector polls **airplanes.live's
`/v2/mil`** endpoint — *that endpoint is itself the military filter* — and then narrows to the
bbox locally. **This project runs no hull list, no callsign pattern and no identifier rule of
its own.** Writing "derived from airframe lists and callsign patterns" would have described a
mechanism that does not exist here, so element (1) names the feed and its path exactly.

⚠ **Altitude is in METRES, and is transferred as a bare number.** `tracker_airplanes`
normalises airplanes.live's feet to the project's metric contract at ingestion, so converting
back to feet in prose would re-derive a number we already hold. Rounding to the metre is a
format, not a derivation.

⚠ **`countries` is empty on 3 of 5** — the overlay bypasses the geo classifier (measured:
`country` is `None` on every row), so the alert falls back to classifying its position and,
when that yields nothing, the headline **says so**: «دون إسناد جغرافي في هذه اللقطة».

⚠ **No time appears in the prose.** `ts` is the SNAPSHOT's vintage, not an onset — the
BE-SUMMARY-3 lesson applied, so nothing reads as "present since".

⚠ **Drift gate, and it is real this time.** `_MIL_SIGNAL_TYPES` is read from `SEVERITY_BASIS`
rather than hand-listed, and an assertion requires every military signal type to be either
covered or **explicitly deferred**. Verified by injecting a phantom `military_drone` into
`SEVERITY_BASIS` and confirming the import fails. (A first attempt asserted a statically-true
subset relation and would never have fired — caught and replaced.)

### The Arabic register, fifth and LAST family — satellite passes (BE-SUMMARY-5, 2026-08-03)

**Every alert family now carries Arabic.** Verified after restart across all 331 live alerts:
**0 with no Arabic headline.**

| signal type | n | headline / why / caveat |
|---|---|---|
| `implausible_position` | 200 | 200 / 200 / 200 |
| `maritime_composite` | 114 | 114 / 114 / 114 |
| `military_satellite` | 13 | **13 / 13 / 13** ← this item |
| `military_aircraft` | 4 | 4 / 4 / 4 |
| `emergency_squawk` | 0 live | templates verified by injection (BE-SUMMARY-3) |

⚠ **The space family is ONE signal type, measured — not an assumption.** `military_satellite`
is the only one the alert layer emits in the space domain. **`satellite_reentry` has a timer
and a table but NO alert builder at all**, so it never reaches this layer and has nothing to
translate. That is a measured fact, not a deferral.

#### ⛔ The only family carrying TWO constraints at once — why it was left until last

**Constraint 1 — the tense is FUTURE.** The pass **has not happened** (measured: `ts` is the
pass start and sits ~a day ahead of `vintage`). «رُصد» and «سُجّل» — true of all four earlier
families — are **false** here.
⚠ **This also inverts the method that governed the other four.** There, "the observation is
the subject". Here **there is no observation at all — there is a COMPUTATION**, so the subject
is the calculation, never a sensor and never the object.

**Constraint 2 — a third kind of caveat, absent everywhere else.** The pass is a **model
output** (SGP4) run on an **orbital element** (a TLE) whose accuracy **decays with the
element's age**.

**And it inherits the military commitment whole** (BE-SUMMARY-4): severity stays INFO and
that item's refused-verb list applies verbatim, plus the orbital variants «يراقب» ·
«يستطلع» · «يتجسّس» · «يمسح» · and «يحلّق فوق» used to mean purpose.

| Raw fact | The sentence emitted | Conveys a future computation, or claims an observation / assigns a mission? | All verbs future? |
|---|---|---|---|
| `object_name=NAVSTAR 56 (USA 180)`, `countries=[CY,TR]`, `start=2026-08-04T08:10`, `closest=338` | «**يمرّ** جسم عسكري **مُصنَّف** (NAVSTAR 56 (USA 180)) فوق قبرص وتركيا — **ممرّ متوقَّع يبدأ** 2026-08-04 08:10Z، **أقرب مسافة محسوبة** 338 كم» | **Future computation.** «مُصنَّف» = the *catalogue's* tag, not our judgement. The distance is the bare computed figure — «قريب» would be an assessment. ⛔ The object name is **never translated**. | ✅ «يمرّ» · «متوقَّع» · «يبدأ» |
| `severity_basis` (INFO) | «**معلوماتي**: **الممرّ محسوب**، لا حكم تهديد. الرتبة ليست منخفضة لضعف الخطر — هذه الفئة خارج محور الخطر أصلاً.» | **Restates** the basis. «محسوب» replaces BE-SUMMARY-4's «مُسجَّل» because what is ranked is a computation, not a record. | ✅ (stative) |
| — caveat (1) | «هذا الممرّ **محسوب لا مرصود**: **لم يره مستشعر**، وإنما نتج عن تشغيل نموذج SGP4 على عنصر مداري (TLE).» | **States the nature of the claim** first, because it reframes everything after it. | ✅ |
| `tle_epoch=2026-08-02`, `start=2026-08-04` | «والعنصر المستعمل **مؤرَّخ 2026-08-02**، أي أن النموذج **يمتدّ به يوماً واحداً** حتى بداية الممرّ.» | **Carried fact + arithmetic on carried fields.** The span is `start_time − tle_epoch`, both on the row — pure, no `now`. ⛔ If the epoch were absent the template **declares** that instead. | ✅ |
| — caveat (3) | «**ودقّة هذا النوع من الحساب تتحلّل كلّما تقادم العنصر**، والهامش يتّسع للأجسام المنخفضة سريعة الاضطراب. **لا يحمل التنبيه رقم خطأ، فلا يُفترض له واحد.**» | **Bounds the accuracy** without inventing an error figure. | ✅ |
| — caveat (4) | «**والكتالوج المداري بلا دولة**: «فوق قبرص» وصف **لهندسة الممرّ المحسوب**، وليس صفة للجسم ولا إسناداً له إلى تلك الدولة.» | **Bounds the geography** — the DASH-3 warning ("objects with a pass over the country", never "satellites tracked") carried into prose. | ✅ |
| — caveat (tag + bound) | «التصنيف «عسكري» **ليس حكماً لنا**: هو **حقل الاستخدام (users) في قاعدة UCS**، ولقطتها **مُجمَّدة عند 2023**… **والمدار من CelesTrak**… ولا يُستنتج **مهمّة ولا حمولة ولا قدرة استشعار ولا غرض**.» | **Names both sources and both vintages**, then bounds what the record contains. | ✅ |

⚠ **The classification source was READ, per the BE-SUMMARY-4 lesson.** It is the **UCS
`users` field** (joined from `satellite_strategic`, snapshot **frozen 2023** — already in
`severity_basis`), while the orbit is a **CelesTrak TLE**. Two sources, two vintages, both
named rather than blurred into "our data".

**Commitment verified:** severity is still `info` for both military signal types with
`severity_basis` unchanged, and a word audit over every space template found **zero** intent
verbs — «تهديد» · «خطر» · «مهمّة» · «مرصود» appear **only negated or bounded**.

⚠ **Drift gate proven again:** `_MIL_AR_DEFERRED` is now **empty**, and injecting a phantom
`military_laser` into `SEVERITY_BASIS` still fails the import.

⚠ **Two defects caught before shipping, both silent-at-import:** `datetime` was used in the
new helper but **never imported** in `alerts.py` (a runtime `NameError` on every satellite
alert, invisible to `import backend.alerts`); and the caveat carried `**markdown**` markers
that would have rendered literally, since this is plain prose over the wire.

### ⚠ Geo stamping is on the DB path only — the archive artifacts carry no country

**Diagnosed 2026-08-01 (DASH-3 step 2). Verdict: the stamping is NOT broken — a comment was.**

A comment added in DASH-1 claimed *"/live states are classifier-stamped at ingest, so the
country is a read, not a computation"*. Measured, `/live` returns `source=archive` with
`country` **0/567**, `near_country` 0/567, `distance_km` 0/567 and `geo_source` 0/567. The
trace explains it completely, and exonerates the pipeline:

1. **`/live` is ARCHIVE-FIRST by design** (`build_live_payload`, documented in place:
   *"latest.json is the artifact the collector atomically replaces … the DB path is the
   fallback"*).
2. **`latest.json` is written by the collector, which does not classify** — its 19 keys contain
   no `country` / `near_country` / `distance_km` / `geo_source`.
3. **The classifier runs in `ingest.py`, on the way into `state_vectors`, and it works.**
   Over the last 30 minutes `geo_source` is non-null on **100 %** of rows —
   `adsbexchange` 11,532/11,532 · `opensky` 10,186/10,186 · `airplanes_live` 245/245 — with
   `country` set wherever the aircraft is genuinely inside one.

So `/live` showing no country is the archive path behaving exactly as specified. **No defect,
nothing to fix in the stamping pipeline, and this is not a §6 item.**

⚠ **But the false comment was hiding a real gap it had talked me out of checking.** The
emergency family read `s.get("country")` from states that never carry it, so **every emergency
alert had `countries=[]`**. It escaped DASH-1's verification because there were **0 live
emergency squawks** to curl. Proven by placing a real `/live` state over Damascus:
`countries=[]` before, `['SY']` after. Fixed with the same `_classify_position` fallback already
proven for military-air — not a new mechanism, and not a change to the stamping path.

**The generalisation, now in `alerts.py`'s module docstring because it has bitten twice:**

> **All three live JSON artifacts** — `latest.json`, `military.json`, `vessels.json` — carry
> positions but **no** `country` / `near_country` / `geo_source`. The shared classifier lives on
> the **DB path only**. Any consumer reading a live artifact must classify the position itself.

Measured, all three: `latest.json` country 0/567 (lat/lon present) · `military.json` 0/2 (lat/lon
present) · `vessels.json` 0/497 with **lat/lon 497/497**. A stamped country is still preferred
when present, so the DB-backed shape keeps working for the day `/live` is served from it.

### `/alerts` — the country filter

`?country=` takes an ISO code **or** a name, any case (`SY` · `sy` · `Syria`), and matches
**membership**: a hull in Israeli *and* Cypriot waters answers to both. It filters the
**assembled** list rather than five heterogeneous per-family queries, so one predicate over one
already-computed field cannot drift the way five WHERE clauses would.

⚠ **A country outside the seven is a `400`, not an empty list.** An empty list would read as
*"nothing is happening in Britain"* — a claim this project cannot make, having no boundaries
there.

⚠ **`counts` and `domains[]` are SCOPED to the country too (DASH-3, 2026-08-01) — this
reverses the DASH-1 rule** that kept them global. DASH-1 argued a filtered feed must not make
the world look smaller; that was right while nothing said what the numbers were scoped to. It
is wrong now, because `countries_filtered` echoes the scope — and a card reading *"103 maritime
leads"* above a list of 3 is the worse lie. **The echo is what makes the reversal safe.**

`counts` remain **uncapped within the scope**: each family is filtered *before* it is counted
and only then sliced to `ALERT_PER_FAMILY_CAP` (200). Filtering the assembled list afterwards
would have quietly turned every country count into "up to the cap".

**Domain counts are scoped by CLASSIFYING POSITIONS** through `geo_area.classify_columns` on the
committed polygons — the same engine the alert filter uses, so the count and the list can never
disagree about where a border is. Measured **0.048 ms/point** over 1,056 live points; paid only
when a country is requested.

⚠ **The `/adsb` dual-source trap, handled by not mixing.** The air domain has always been
**opensky/`latest.json` only**, and the scoped count classifies **the same states** that produced
the global figure — one population, so the country number and the total are comparable.
`adsbexchange` is a different, larger feed and is never reached into. Every `source` string now
names its population so a country figure cannot be read against a total from another feed:

| Domain | Scoped `source` | Note |
|---|---|---|
| air | `opensky (latest.json) — positions in SY` | one population, never mixed with `/adsb` |
| sea | `aisstream + vessel_states — positions in SY` | positions classified, no country field exists |
| space | `satellite_passes — objects with a pass over SY in the window` | ⚠ a **different question** |

⚠ **Space is deliberately a different question.** The global space number is the size of the
tracked **TLE catalogue** — a catalogue has no country, and a satellite is over many. Forcing it
would be a fiction, so the scoped figure is *objects with a pass over this country in the
window* (`satellite_passes.countries`/`near_countries`), and the `source` says so rather than
letting a reader assume the catalogue was filtered.

**Measured live before/after** (window 24 h, 2026-08-01):

| Request | alerts | `counts.total` | air | sea | space |
|---|---|---|---|---|---|
| no filter | 208 | 208 | 549 | 498 | 803 |
| `country=SY` | 14 | **14** | **9** | **32** | **57** |
| `country=TR` | 45 | **45** | **223** | **128** | **124** |
| `country=IL` | 127 | **127** | **12** | **44** | **13** |

**Before DASH-3 every filtered row read `counts.total=210`, `air=574 sea=489 space=803`** —
global no matter which country was asked for. `?country=GB` → `400`; unfiltered is byte-identical
to before.

### Satellite layers — freshness (measured, previously undocumented)

`max()` of each table's newest ingest/compute stamp *and* its newest data epoch, with rows
compared against the §3 count table above. Measured 2026-07-29 11:57 UTC.

| Table | Rows (§3) | Rows now | Newest ingest/compute stamp | Age | Newest data epoch | Refreshed by |
|---|---|---|---|---|---|---|
| `satellite_elements` | 801 | **803** | `fetched_at` 2026-07-29 10:20:33Z | **1.6 h — fresh** | `epoch` 2026-07-30 16:34:55Z | ✅ `scraplan-satellites.timer` |
| `satellite_history` | 16,598 | **17,370** | `fetched_at` 2026-07-29 10:20:33Z | **1.6 h — fresh** | `epoch` 2026-07-30 16:34:55Z | ✅ same timer |
| `satellite_orbit` | 781 | **782** | `computed_at` 2026-07-29 10:20:37Z | **1.6 h — fresh** | `tle_epoch` 2026-07-30 16:34:55Z | ✅ same timer |
| `satellite_passes` | 351 | **363** | `computed_at` 2026-07-29 10:20:38Z | **1.6 h — fresh** | `window_end` 2026-07-30 10:20:37Z | ✅ same timer |
| `satellite_reentry` | 808 | **926** | `fetched_at` **2026-07-29 15:32:52Z** | ✅ **fresh** | `decay_epoch` **2026-08-02 10:14:00Z** | ✅ `scraplan-reentry.timer` *(deployed TIMERS-2)* |
| `satellite_info` | 908 | 908 | `fetched_at` 2026-07-18 18:57:22Z | ⚪ **by design** | `satcat_snapshot_date` 2026-07-17 | ⚪ **snapshot backfill, no timer wanted** |
| `satellite_strategic` | 703 | 703 | `fetched_at` 2026-07-18 19:39:58Z | ⚪ **by design** | `ucs_snapshot_date` **2023-05-01** | ⚪ **frozen dated snapshot, deliberately** |

**The split is clean and matches §2 exactly.** The 6-hourly `scraplan-satellites.timer`
refreshes **four** tables — elements, history, orbit, passes — and all four are fresh, with
their last run at **10:20 UTC = 06:20 EDT**, confirming the `00,06,12,18:20` schedule is
local-time.

✅ **`satellite_reentry` is now timer-driven (TIMERS-2).** It was the one genuine gap: a
Space-Track **TIP** prediction feed that refreshes daily, frozen **10 d 20:58** because
`scraplan-reentry.{service,timer}` existed in `deploy/systemd/` but had **never been installed**.
Deployed and verified — rows **808 → 926 (+118)**, and the layer now carries **forward**
predictions (`max(decay_epoch)` 2026-07-22 → **2026-08-02**), which is the whole point of a
reentry watch. Next scheduled run Thu 2026-07-30 05:30 EDT, so
`SATELLITE_RESEARCH_BASE.md`'s "daily 05:30" claim is **finally true**.

⚠ **Correction to a TIMERS-1 note.** That note grouped all three tables as "refreshed by
nothing … and ageing". Only `satellite_reentry` was a defect. Read from the scripts that write
them:

- **`satellite_info` (SATCAT)** — *"This is a LAYER (a fill), not a per-object API: one offline
  snapshot covers the whole catalog … with ZERO per-object network calls"*
  (`scripts/backfill_from_satcat.py`). A bulk backfill, re-run when the catalogue snapshot is
  refreshed. **No timer is wanted.**
- **`satellite_strategic` (UCS)** — *"UCS is a DATED RESEARCH SNAPSHOT (frozen 2023-05-01; the
  project paused updates) … NOT a current-state source"* (`scripts/backfill_from_ucs.py`).
  Its age is the **asset's** vintage, upstream-frozen, and every row records
  `ucs_snapshot_date` so a study can cite *"strategic classification as of 2023-05-01"*.

So "10.7 days stale" was the wrong frame for those two: **a dated snapshot is not a stale
feed.** Their vintage must still be cited in any claim — `satcat_snapshot_date` 2026-07-17 and
`ucs_snapshot_date` **2023-05-01** (3.2 years).

### Identity quality (`vessel_identity.imo_quality`)

| Verdict | Rows |
|---|---|
| `valid` | **2,875** |
| *(NULL — no IMO reported)* | **1,471** |
| `malformed` | 36 |
| `checksum_fail` | 10 |

⚠ **The IMO backfill has not run since 2026-07-25.** The doc appendix records that run as
"of **3,776** identities: 2,875 valid · 10 checksum_fail · 36 malformed · **854 NULL-IMO**".
Live, the table holds **4,389** rows with the **same three stamped counts** (2,875 / 10 / 36)
and **1,471 NULL**. So ~600 identities added since have **never been stamped** — consistent
with the documented invariant (a NULL-IMO row is never stamped) plus growth, but it means the
axis covers a shrinking share of the spine until the backfill re-runs. *(Stamped + NULL
exceeds the row count by 3 — the two queries ran seconds apart against a live table.)*

### Feed outage — measured with the project's own reconstruction

Computed by calling `backend.gap_detect.reconstruct_outage_windows` on the real
`signal_events` stream, so the figure matches what the detectors actually exclude
(Finding A open windows + Finding B inferred silent deaths).

| Fact | Measured |
|---|---|
| Source | `aisstream` · 106 `feed_connect`/`feed_disconnect` events |
| Window | **2026-07-13 15:13:35Z → 2026-07-29 09:51:10Z** = **15.78 d** |
| Outage windows | **46** (46 recorded · 0 inferred) |
| Total outage | **7.17 d** |
| **OUTAGE** | **45.46 %** |
| **UPTIME** | **54.54 %** |

Three outages dominate: **2.86 d** (07-22 → 07-25), **2.20 d** (07-20 → 07-22), **1.86 d**
(07-16 → 07-18) — 6.92 of the 7.17 days.

*Cross-check and why it differs:* summing `detail->>'session_seconds'` gives only 5.13 d of
uptime (32.5 %). That method **undercounts** — `session_seconds` exists only on
`feed_disconnect` rows (47 of them), so the currently-open session and any silently-died
session contribute nothing. **The window reconstruction is authoritative.** The event log
does contain a `feed_connect` at 2026-07-29 06:44:51Z with no preceding disconnect (a silent
session death); it correctly produced **no** inferred window because positions flowed
across it — a seamless reconnect, per the Finding-B rule.

### Dated snapshots that will drift

The `MARITIME_INTELLIGENCE_BASE.md` appendix is **frozen at 2026-07-20 00:03:36 UTC** and is
labelled as such. Treat every number in it as historical. Measured drift so far:

| Appendix (frozen 07-20) | Live now | Drift |
|---|---|---|
| `vessel_states` **690,685** | **1,001,405** | **+45 %** |
| `vessel_identity` **3,525** | **4,389** | +24 % |
| A2 rendezvous **35** | **32** | recompute-and-replace moved it **down** |
| IMO integrity over **3,776** identities | **4,389** | stamped counts unchanged (§ above) |
| Outage **52.4 %** over 328 h (recomputed 07-25) | **45.46 %** over 378.7 h | same ~172 h outage, larger denominator |

The last row is the model case: the outage *duration* barely moved, but the *percentage*
fell 7 points purely because the window grew. **Never quote an outage percentage without
its window.**

Layers whose published figures are dated snapshots and **will** drift: the composite hull
counts, A1 candidate funnel, GNSS quality/episode distributions, zone-dwell session counts,
port-visit four-state distribution, and every "N of M" in the appendix.

### ⚠ The ADS-B substrate changed sampling frame mid-history (2026-07-29 13:55:36 UTC)

Every `state_vectors` figure above is from the **global era**. As of
**`2026-07-29 13:55:36+00`** the writer keeps only records INSIDE the seven AOI countries or
NEAR their borders, so the inflow fell **~20×** (measured: 3,351,326 rows in the 24 h before,
against a post-narrowing archive growth of 0.404 GB/day — see §7 for the full before/after and
the preserved global-era file).

Consequences a study must respect:

- **Rows/day and any absolute `state_vectors` count are era-dependent.** A rate computed across
  the boundary is meaningless.
- **`country IS NULL` no longer means "outside the AOI"** — post-narrowing it means NEAR a
  border (in-frame, `near_country` set). Pre-narrowing it meant genuinely outside. Same column,
  two meanings, split at that instant.
- **Absence after the boundary is a frame fact, not an observation.** A global aircraft missing
  after 13:55:36 was never sampled; it is not evidence it was absent.
- The global era is preserved whole and checksum-verified (§7) — nothing was deleted, so any
  pre-boundary analysis remains reproducible from the era file.

---

## 4. MarineTraffic store staleness

| Fact | Measured |
|---|---|
| Upstream store | `/home/gis-syr/Desktop/mtscraper/mt_output/positions.jsonl` — 411,084 B, **last written 2026-07-21 11:13:22Z** |
| Companion files | `vessels.jsonl` 99,346 B · `mtscraper.log` 4,172 B · `diagnostics/last_tile.json` 62,566 B — all same timestamp |
| DB rows | **3,455**, `ts` 2026-07-20 11:58:55Z → **2026-07-25 10:26:04Z** |
| DB ingest window | `ingested_at` **2026-07-25 07:36:59Z → 10:29:16Z** — a single ~3 h manual run |
| MMSI coverage | **0 non-null** |
| `scraplan/marinetraffic_output/` | empty / absent |

**Store idle ~8.0 days; DB rows static ~4.0 days.**

### ⚠ `mt_vessel_states.ts` — resolved from the code, and the earlier hypothesis was wrong

This section previously guessed that `ts` might be the periodic *fleet-snapshot* time. **That
was wrong.** Traced through the write path (read-only; the scraper was not run):

1. [`marinetraffic_translate.py:74-89`](../maritime/adapters/marinetraffic_translate.py#L74) —
   `_fix_timestamp(fetched_at, elapsed_min)` returns **the vessel's own position-fix time =
   `fetched_at − elapsed_min`**, documented at
   [lines 31-34](../maritime/adapters/marinetraffic_translate.py#L31): *"`fetched_at` is the
   COLLECTION time; `elapsed_min` is the age of the vessel's last report at that instant. The
   true position-fix time (what `vessel_states.ts` means, project-wide) is therefore
   `fetched_at - elapsed_min`."* Assigned to `"timestamp"` at
   [line 169](../maritime/adapters/marinetraffic_translate.py#L169).
2. [`maritime/db.py:317`](../maritime/db.py#L317) — `mt_vessel_row`:
   `ts = _parse_ts(rec.get("timestamp"))`, i.e. **that fix time becomes `mt_vessel_states.ts`**
   (`$1` of `MT_INSERT_SQL`).
3. [`maritime/fleet.py:110-127`](../maritime/fleet.py#L110) — `Fleet.snapshot()` **does not
   re-stamp**: it reads `v.timestamp`, evicts fixes older than `max_age_s`, and returns
   `v.to_dict()` unchanged.
4. [`tracker_marinetraffic.py:105`](../tracker_marinetraffic.py#L105) —
   `snapshot_time = int(datetime.now(timezone.utc).timestamp())` flows **only** into the
   `vessel_snapshots` metadata row (`UPSERT_SNAPSHOT_SQL`, `db.py:438-441`), **never** into
   `mt_vessel_states.ts`.

**Answer: `ts` is the raw observation (position-fix) time, not a snapshot time.** It is
directly comparable with `vessel_states.ts`.

### So the 07-25-vs-07-21 gap is a missing-ground-truth problem, not a timestamp one

Measured inside the store (`python3 -c` over `positions.jsonl`, read-only):

| Fact | Measured |
|---|---|
| Lines | **1,068** |
| Line shape | **raw** — `{ship_id, fetched_at, source, in_area, data{…}, distance_km}`. **No `timestamp` key exists**; it is derived downstream, which is why a naive scan for `timestamp` finds 0. |
| Distinct `fetched_at` | **7 instants, all within 2026-07-21 11:07:54Z → 11:13:22Z** — a ~6-minute collection burst |
| `elapsed_min` | n=1,068, **min 1.0 · max 1,389.0** |

Derived bounds: oldest fix ≈ `11:07:54Z − 1389 min` = **2026-07-20 11:58:54Z**; newest fix
≈ `11:13:22Z − 1.0 min` = **2026-07-21 11:12Z**.

- The oldest bound **matches the DB's `min(ts)` = 2026-07-20 11:58:55Z to the second** — an
  empirical confirmation of the `fetched_at − elapsed_min` rule above.
- But the DB's **`max(ts)` = 2026-07-25 10:26:04Z is ~4 days beyond anything this store can
  yield**, and 1,068 lines cannot produce **3,455** rows.

⚠ **The current store is not the ground truth the DB was built from.** The adapter documents
that it re-reads from the start when the file shrinks (mtscraper restart / rotation), so the
store was replaced or truncated after the 07-25 ingest. **Consequence: the MarineTraffic layer
is the one layer that cannot be rebuilt from its own ground truth** — every other layer
rebuilds from an append-only archive or a committed dated asset (`MARITIME_INTELLIGENCE_BASE.md`
§5). This strengthens the §6.1 decision: an accepted source needs a durable archive; an
experiment does not, but then it should not be carried as a source.

---

## 5. Roadmap state

⚠ **The roadmap numbering is not committed anywhere.** The docs cite "Roadmap priority 4"
(zones × dwell), "Roadmap priority 5" (ports) and "priority 6's ~90-day target", but **no
committed file enumerates priorities 1–3**. That ordering has lived only in session memory —
exactly the drift this document exists to stop. Below, each closed priority is identified by
**what shipped**, which is verifiable from the commits.

### Five closed priorities — what shipped vs what was shrunk or killed

**① MarineTraffic parallel source** — migrations 032–034 + adapter/`vessel_xref` (`f7be9d7`).
*Shipped:* own table (`mt_vessel_states`, `ship_id`-keyed), identity bridge (`vessel_xref`),
AIS×MT correlation with confidence/evidence columns, 128 links, tests.
*Shrunk on measured grounds:* **0 non-null MMSI** → MT positions are un-investigable and
unreachable from the search surface; analytical value **bounded to the `vessel_xref` product**.
Core stays AIS-only (**0 MT facts** in `vessel_states`/`vessel_identity`). Never deployed
(§2); recommended **opt-in**. It does **not** close the second-source SPOF.

**② IMO integrity** — IMO-1/2/3, migrations 035/036 (`e88893b · e726fab · e1aed82 · 4cb2813`
· `4ff5da2 · 8725614 · 78d86f5 · 96da069` · `c9957bf`).
*Shipped:* identity-quality axis on `vessel_identity`, malformed hull-key fallback +
collision disjunct in the composite, latent `imo_integrity` event, surface annotation.
*Killed on measured grounds:* the layer **cannot verify ownership** — a fabricated IMO with a
valid check digit passes, and closed registries (IHS Markit, paid Equasis) are excluded by
policy. It verifies internal consistency only.
*A prior guess was corrected by measurement:* the 36 malformed IMOs are **not**
"MMSI-in-the-IMO-field" — **0 of 36** equal the transmitter's own MMSI or any known MMSI;
they are 2 bit-pattern artifacts, 1 sequential placeholder, 33 wrong-length values, and they
**arrive malformed from upstream** (confirmed in the JSONL before the parser).

**③ Draught corroboration** — DRAUGHT-1 (`583c01e · da6a355 · dbed0cf`).
*Shipped:* pure draught-delta module, A2 rendezvous annotation, honest surfacing.
*Shrunk to a latent guard on measured grounds:* 3-state with `insufficient_data`, **no
migration**, ~**1/35** yield — never published alone.

**④ Zones × dwell** — ZONES-1…4, migration 037 (`c7a7780 · c605b65 · 45ca321` · `5db6bd4 ·
e45ac04 · bad4b6d` · `dbd7141 · 68b8b78` · `d759a98` · `d1b09cf`).
*Shipped:* dated 36-zone asset (7 gas_field GEM GOGET + 29 OSM platform), loader, pure dwell
module with two signals + gating + cloaking exposure, on-query endpoint, per-zone familiarity
baseline, event writer, composite admission.
*Shrunk on measured grounds:* of **308** sessions over 30 days → 64 leads / 28 demoted / 216
excluded, and **only 12 admitted** (all `safety_zone_intrusion`). `standoff_dwell` stays
annotation-only because it is type-gated on self-reported `ship_type` — a **structural
cloaking vector**, measured exposure **2 of 28** demoted sessions.
*Scope deferrals, not coverage gaps:* Gulf/Iran theater (deferred with the AOI expansion,
though the collector observes to 59.29°E); submarine cables (**no redistributable open
geometry** — TeleGeography's CC grant covers the map image only); listed ports (see ⑤).

**⑤ Port visits** — PORT-1/2/4 (`ea8ce4c · 9faa71a · a322bfa` · `311ef35 · cdd9f6f` ·
`ec2c11a · d14c617`, plus the gating red-test fix `da80538`).
*Shipped:* 190-feature dated gazetteer (180 WPI public-domain + 10 OSM), loader, pure
sessionization, on-query `/maritime/port-visits`, `flank_pad_km`. **No migration, no table,
no events** — deliberately annotation-only.
*Killed on measured grounds, three times over:*
- the **gap-toward-a-port signal is DEAD and anti-correlated** — gaps start within 15 km of a
  port **8.2 %** vs a **19.14 %** null baseline (~2.3× *depleted*);
- a **listed-ports asset is unbuildable** — OFAC designates **hulls, not places**, and the
  legal state moved twice in seven months (EO revoked 2025-06-30, Caesar Act repealed
  2025-12-18);
- **composite admission was refused** — the tightest bar would flip **96 hulls** from 1→2
  pattern types and grow the composite **9 %**, so a guard test asserts the endpoint writes
  **nothing**.
Honest limits recorded with it: `radius_basis='assumed'` on **all 190** features, **108 at the
1.5 km floor**, **85.6 %** of visits overlap an A1 gap for the same vessel (one silence seen
twice), and **34 of 190** ports produced any visit.

### Deferred items — and the specific bar each waits on

| Deferred | Bar | Measured position |
|---|---|---|
| **`standoff_dwell`** composite admission | a zone sound at **≥ 30 days AND ≥ 10 vessels AND ≥ 20 sessions** | `baseline_ready` **false** · **0 of 17** trafficked zones sound · binding bar is **days**: best span **13.6 d** → **gap 16.4 days**. Zones meeting each bar: **0 days / 1 vessels / 2 sessions** |
| **Live-currency second source** (the SPOF) | **AISHub or self-reception** | **Open.** `vessel_states` is 100 % `aisstream` at **45.46 %** outage. GFW explicitly does **not** satisfy it: CC-BY-NC non-redistributable, zero-storage, **≥ 72 h latency**, 0 core facts derive from it |
| **Class B AIS** (types 18/19/24) | — | Deferred; spine is Class A only (1/2/3/5) |
| **AOI expansion** (Egypt/Iran + Gulf) | a single deliberate future change | Not started. AOI is seven countries; a vessel off Alexandria reads *outside* |
| **Submarine-cable zones** | a licensed/redistributable geometry source | **1 NM buffer spec recorded ready**; EMODnet/OSM near-empty in the AOI |
| **`zone_dwell` timer** | owner decision (§6) | No unit exists; events 3.0 d stale |
| **A3 timer** | owner decision (§6) | Unit exists but `disabled`; events 9.6 d stale |
| **GNSS episode timer** | on-demand by design | 8.0 d stale |
| **GFW `get_track`** | documented deferral | Dark-vessel gap signal is the value taken |
| **~90-day baseline target** ("priority 6") | ~90 days of runtime | Best zone span **13.6 d** |

Suite state: **631 tests collected** (`pytest --collect-only`).

---

## 6. Open decisions — awaiting the owner

These are **decisions, not tasks.** Each is measured; none should be resolved unilaterally.

### 6.0 `--sev-low` is below the text-contrast bar (2026-08-24)

**Measured** after THEME-3 moved the ground to neutral `#0b0b0a`:

| token | on `--panel` before | after | bar |
|---|---|---|---|
| `--sev-high` | **4.48:1** ⛔ | 5.46:1 ✅ | 4.5:1 |
| `--sev-low` | 3.02:1 ⛔ | **3.68:1** ⛔ | 4.5:1 |

THEME-3 discharged `--sev-high` — which was failing on the most consequential badge in the
app and nobody had recorded it. `--sev-low` improved and **still fails**, on a badge that
appears on every low-severity row.

⛔ **WHY IT IS A DECISION AND NOT A FIX.** The severity axis is FROZEN by prior gates: its
hues carry the honest-ranking commitment and were validated for pairwise separation. Lifting
`--sev-low` changes a colour the project deliberately pinned. Raising its lightness is the
obvious move and it narrows the gap to `--sev-info`; the alternative is to stop using it as
TEXT (border/dot only), which keeps the hue and moves the 4.5:1 obligation off it.

**Recorded, not silently left and not silently changed.**

### 6.1 MarineTraffic's policy status — experiment or accepted source?

**Measured:** committed code (migrations 032–034, `tracker_marinetraffic.py`, two adapters,
two test modules), **3,455** rows frozen **4.0 days**, upstream store idle **8.0 days**,
**0 non-null MMSI**, **128** `vessel_xref` links from a single batch, **no deployed unit in
any scope** (§2), and §6 of the maritime base names MarineTraffic a scraped
commercial-aggregator source taken *"by scraping"* against a base that otherwise uses only
the live AIS feed + committed public assets.

**The unresolved question:** is this an **experiment** (then quarantine or remove the code,
and say so) or an **accepted source** (then deploy a unit, keep it current, and say so)?
Right now it is neither — committed code and a stale store for a source the documentation
treats as excluded. **The documentation must match whichever is chosen.**

### 6.2 ✅ RESOLVED — the replay view was deleted (UI-3); its contracts remain

**Measured** (read-only; `frontend/` belongs to another window):
- `"replay"` is in the `View` union at [App.tsx:53](../frontend/src/App.tsx#L53) **and**
  [NavBar.tsx:13](../frontend/src/components/NavBar.tsx#L13)
- `ReplayBar` is imported at [App.tsx:36](../frontend/src/App.tsx#L36) and rendered at
  [App.tsx:1447](../frontend/src/App.tsx#L1447)
- **6 dedicated state hooks** at [App.tsx:218-223](../frontend/src/App.tsx#L218-L223) —
  `replaySnaps`, `replayIndex`, `replayPayload`, `replayPlaying`, `replayLoading`,
  `replaySpeed`
- **59** `replay`/`Replay` references in `App.tsx`
- **NavBar offers only four entries** — `monitor` (لوحة), `live` (جوّي), `maritime` (بحري),
  `satellites` (فضائي). **No replay entry.**
- **`grep -rn "setView(\"replay\")" frontend/src` → no match.** Nothing can ever set the view.

**The decision:** add a nav entry (the feature is built and reachable in one line) or delete
the component and its state. Either is defensible; carrying dead reachable-by-nobody state is
not. *This file records the finding; acting on `frontend/` is the other window's call.*

⚠ **Resolved 2026-08-01 — the owner chose DELETE, and UI-3 executed it** (`c8bc2e3`,
*"delete the replay view and all its dead state"*). `frontend/src/components/ReplayBar.tsx` is
gone and no view can reach it.

DASH-pre had recommended *keep + wire* on the grounds that it was the only UI consumer of two
working endpoints. That recommendation was not taken, which is the owner's call to make.

**What survives, and matters for the DASH UI phase:** the backend contracts are **untouched and
still served** — `/snapshots` (the snapshot index) and `/replay?at=` (the whole fleet's state at
an instant), plus the per-entity tracks `/history?icao24=` and `/vessel/{mmsi}/track`. Satellites
still have **no** track endpoint. So the project retains time-travel and per-entity history as
*capabilities with no UI*. **The decision about how the historical track resurfaces is deferred
to the DASH frontend phase** — it is a design question for that window, not a backend gap.

### 6.3 ✅ The Arabic ERD is now GENERATED from the live DB (closed 2026-09-01 — it was behind by ~48 migrations, worse than this item recorded; scripts/generate_erd.py is the fix-of-the-class, new artifact URL in the erd memory) — originally: behind migrations 035–037

**Measured** against `scraplan-db-erd.md`: `mt_vessel_states` appears **8×** and
`vessel_xref` **10×** (so 032–034 **are** reflected), the applied-migrations list **ends at
`034`**, and **`imo_quality` appears 0 times** — migration 035's four columns
(`imo_quality`/`imo_flags`/`imo_method`/`imo_computed_at`) are absent. Migrations 036/037
edit **views**, and the ERD diagrams tables, so those are a migration-list note, not a
diagram change.

**The edit spec is already recorded** (DOCS-1): add the four `imo_integrity` columns to the
`vessel_identity` entity and its column table, and extend the migration list with 035/036/037.
The document is **hand-maintained Mermaid — there is no generator script.**

**The decision:** this is an **outward-facing Arabic publish**, so the redeploy is the
owner's. Prepared and reported only.

### 6.4 ✅ CLOSED — detector continuity (TIMERS-1, 2026-07-29)

Both gaps are closed and verified; measurements in §3.

- **`zone_dwell` timer deployed** — `scraplan-zone-dwell.{service,timer}`, `OnCalendar=*:0/20`,
  `Persistent=true`, modelled on `rendezvous`. Was 3 d 05:43 stale; now current.
- **`scraplan-identity.timer` enabled** — it existed and was already listed in
  `deploy/systemd/README.md`, but sat `disabled` on the host. Was 9 d 19:05 stale; now current.
  **19 identity changes had accumulated undetected.**
- **IMO-integrity axis backfilled** — 509 identities stamped, `unstamped-with-IMO` now 0.

**Scraplan timers: 8.** Every stored detector is timer-driven; only the GNSS **episode**
detector remains on demand, which is its documented design.

**The lesson recorded in `deploy/systemd/README.md`:** a disabled timer and an absent unit both
present as **silence** — no failed unit, no log line. Both were caught by
`max(computed_at)` in `maritime_events`, so **that query is the liveness check**, not the unit
list.

### 6.5 ✅ CLOSED — disk exhaustion, decided and executed (DISK-2, 2026-07-29)

The owner signed off on narrowing upstream plus cutting the historical archive; both were
executed in one maintenance window. **Project-attributable growth fell 14.40 → 0.92 GB/day
(15.7×), free space 56.29 → 98.62 GB, and there is no exhaustion at the current rate.** Full
before/after and the verification sequence are in §7.

What the decision rejected, and why — all measured:

- **Relocation was not available** — `/media/gis-syr/EXTERNAL1` does not exist, no `sd*` device
  is attached, and the only Tailscale peer is an offline Android. The off-volume path is built
  and guarded (§7) and will run when media returns.
- **Moving the rotated `.gz` was never the rescue** — 0.36 GB, ≈ 34 minutes of the old burn.
- **Deleting the global archive was rejected outright** — it is ground truth. It was preserved
  whole, checksum-verified, as a 3.48 GB compressed era file.

### 6.6 Reclaim candidates — owner decision, nothing deleted

Measured `du` / `docker system df`. **This session deleted nothing.** Ownership is stated so a
decision can be made per row, not in bulk.

| Path / pool | Size | Belongs to | Movable / deletable? |
|---|---|---|---|
| `.cache/pip` | **17 GB** | nothing — pure download cache | **Regenerable.** Safe to clear; no project reads it. |
| Docker **reclaimable images** (50 dangling of 89) | **14.98 GB** | mixed (gitlab-ee 3.72 GB, litellm, editimg, scei-*) | Dangling images are unreferenced. |
| Docker **build cache** (314 entries, 0 active) | **4.44 GB** | mixed | **Regenerable.** |
| `.npm` | **9.1 GB** | nothing — package cache | **Regenerable.** |
| Journals (2.4 GB system + 508 MB user) | **2.9 GB** | system | `journalctl --vacuum-size` bounds it. |
| `snap` (old revisions) | **25 GB** | system/desktop apps | Old snap revisions are purgeable; current ones are not. |
| `wheels` | 7.5 GB | build artefacts | Likely regenerable — owner confirms. |
| `~/scraplan-backups` | **14 GB** | **scraplan** | Retention works (14 d); the issue is it shares the DB's volume. Relocate, don't delete. |
| `Desktop/plandata` | **43 GB** | scraplan upstream | ⛔ **Keep as history.** Narrow the *frame* upstream; do not delete the accumulated archive. |
| `Documents/face1` 22 GB · `Documents/3ddes` 17 GB · `location-recognition-stable` 33 GB · `bokteq_voice_agent` 13 GB · `Music` 10 GB · `models` 7.9 GB | — | **other projects / personal** | Outside this project — owner only. |
| ~~`Qwen3.5-4B` 16 GB~~ | — | other | **Already deleted externally at ~12:47** (§7). |

**Regenerable caches alone (pip + npm + docker dangling + build cache + journals) come to
≈ 48 GB** — more than the current free space, and none of it is project data. That is the
cheapest headroom available, and it is still the owner's call.

Also present: **22 running containers of 31 defined**, four concurrent Postgres instances
(ports 5432/5433/5434/5435/5544), and `matrix_backend` **restart-looping** (exit 255) — a
running cost on the same host, listed as context rather than a recommendation.

➡ **Their memory cost is now measured per container, with idleness evidence and an isolation
proof, in §6.10** (MEM-CHECK). `matrix_backend`'s loop is attributed there: a broken upstream
image entrypoint, **4,709 restarts**, and **0 B of RSS** — churn, not memory.

---

### 6.7 ⚠ `adsb_scraper` has no version control — the patch is the only record

The upstream collector at `~/Desktop/plandata` — which produces the ADS-B substrate this
project depends on — is **not under version control**: no `.git`, `.hg`, `.svn` or `.bzr`, and
it is not nested inside another repository (`git rev-parse` fails from both the directory and
its parent).

So the DISK-2 sampling-frame change **could not be committed where it lives.** It is applied
and tested on disk, and its only version-controlled record is:

> **[`deploy/adsb_scraper-aoi-gate.patch`](../deploy/adsb_scraper-aoi-gate.patch)** — a
> 3-file unified diff (`statevector.py`, `main.py`, `tests/test_statevector.py`), verified to
> reverse cleanly with `patch -p1 --dry-run -R` from `~/Desktop/plandata`, and to refuse
> re-application because it is already applied. Apply/revert instructions and the effective
> date are in its header.

**`git init` was deliberately not run.** It was outside the authorised scope for this task
("one commit limited to the scope gate, nothing else in it"), and `data_p1` alone is **43 GB**,
so initialising a repository there without carefully authored ignore rules is a trap, not a
convenience.

**The decision awaiting the owner:** bring `adsb_scraper` under git — with a `.gitignore` that
excludes `data_p1/`, `adsb_output/`, `data_final/`, `data_neighborhood/` and `.venv/` before the
first `add` — or accept that its history lives only as patches in this repo. Until then, **any
change to that collector must ship as a patch under `deploy/`**, or it is unrecoverable.

### 6.8 ✅ CLOSED — a verified copy off this machine (BACKUP-EXT), and a **tested restore** (RESTORE-TEST)

⚠ **Both halves are now proven, and the defects that proving them exposed are fixed.**
BACKUP-EXT proved the bytes arrive intact; **RESTORE-TEST (2026-08-04) proved they rebuild a
working database** — procedure in [`docs/RESTORE_PROCEDURE.md`](RESTORE_PROCEDURE.md);
**BACKUP-HARDEN (2026-08-04) closed the three data-safety defects RESTORE-TEST found** — a
fail-open space gate, dumps that could not describe themselves, and a missed night nobody
noticed for 44 hours. Three items are recorded at the end of this section as deliberately
**not** fixed, one of which (`rotate_archive.sh`) is the most dangerous defect found.

**Implemented and running.** Option 2 of the three below (network upload) was taken, to a
second physical machine on the LAN. First end-to-end run **2026-08-01 06:43:58 → 06:58:36
EDT**, `Result=success`, both objects checksum-verified from the destination.

| Fact | Measured |
|---|---|
| Destination | **`serverq@10.10.20.57`** → `serverq-MS-7E06`, Ubuntu 24.04 — a **second physical host**, its own disk |
| Path | **LAN via `enp2s0`** (`ip route get` → `via 10.0.0.1`). **Not** `tailscale0` — the only Tailscale peer is still the offline Android |
| Measured rate | **11.2 MB/s ≈ 90 Mbps**, 90 % of the 100 Mb/s wire (`docs/ENVIRONMENT.md` §3) |
| Nightly volume / duration | 3,062,243,980 B dump — **4 min 44 s to dump, 4 min 33 s to send+verify** |
| One-time era file | `state_vectors_pre-narrowing_2026-07-29.jsonl.gz`, 3,475,344,889 B, **5 min 9 s** |
| Retention | **7 dumps at the destination** (`OFFSITE_KEEP`), pruned **only after** tonight's copy verifies |
| MANIFEST | **`~/scraplan-offsite/MANIFEST.sha256`** on the destination — permanent, append-only |
| Checkable index | **`~/scraplan-offsite/CURRENT.sha256`** — rewritten each run; `sha256sum -c` passes at the destination, verified |
| Success marker | **`~/scraplan-offsite/LAST_SUCCESS`** — UTC stamp, written last |
| Timer | `scraplan-offsite.timer`, **04:15 daily**, `Persistent=true` — the quiet gap after the 03:00 local dump, before the 05:30 reentry pull |
| Script | [`scripts/backup_offsite.sh`](../scripts/backup_offsite.sh) |

**It copies, it does not move.** `rotate_archive.sh` relocates and deletes the local original
because its job is to free the volume; this script's job is redundancy, so the source must
survive. Verified after the live run: both sources still present, byte-for-byte. The only
thing it deletes on this host is its own staging dump.

#### ⚠ The trap this nearly walked into — an SSH key that works interactively and not from a timer

`ssh -o BatchMode=yes serverq@10.10.20.57` **succeeds from a shell and fails from systemd.**
The accepted key is `~/.ssh/serverq` — not a default `id_*` name, and no `~/.ssh/config`
entry covers this address — so ssh never offers it unprompted. Interactively it works only
because the **GNOME-Keyring agent** (`SSH_AUTH_SOCK=/run/user/1000/gcr/ssh`) holds and offers
it. Measured, both directions:

```
env -u SSH_AUTH_SOCK ssh -o BatchMode=yes …                          -> Permission denied
env -u SSH_AUTH_SOCK ssh -o IdentitiesOnly=yes -i ~/.ssh/serverq …   -> OK
```

⚠ **And `systemctl --user show-environment` *does* carry `SSH_AUTH_SOCK`**, so the unit would
have inherited the agent and **passed its own acceptance test** — then failed every night
after the first reboot without a graphical login, or whenever the keyring was locked. That is
precisely the silent-failure shape that hid the identity timer for 9 days (§6.4), and it would
have hidden here behind a green first run.

**Two defences, both deliberate:** the script pins `-i ~/.ssh/serverq` with
`IdentitiesOnly=yes`, and the unit sets **`UnsetEnvironment=SSH_AUTH_SOCK`** so its
independence from the agent is structural rather than incidental — the successful run above
is therefore actual proof the on-disk key carried it.

**No key was generated or published by this work.** The existing, already-authorised key was
used. ⚠ It is passphraseless on disk — which is what makes unattended backup possible, and
also means read access to that file is access to the destination. Stated, not hidden.

#### What was verified, and how

| Property | How it was proven |
|---|---|
| Correct bytes arrive | sha256 computed **by the destination** and compared; `sha256sum -c CURRENT.sha256` passes there |
| A truncated transfer is rejected | Injected a 10-byte-truncated stream → mismatch detected, **nothing promoted**, partial left as `.part`, absent from `CURRENT.sha256`, prior good copies untouched |
| Retention keeps the newest N | 5 fabricated dated dumps with `KEEP=3` → oldest 2 removed, newest 3 kept, `MANIFEST` retained all 5 as history |
| Nothing is deleted before its replacement verifies | Retention runs only after `push_verified` returns success |
| The timer path works without an agent | Run through `systemctl --user start` with `UnsetEnvironment=SSH_AUTH_SOCK` |
| **The era file is not re-sent nightly** | **Second full run 07:06:23–07:15:58: `era file already present and byte-identical … (skipping)`.** The whole era step cost **9 s** (hash here, hash there, compare) instead of a 5-minute re-upload |
| The two indexes stay honest under re-run | After the second run `MANIFEST.sha256` holds **3** lines (both same-day dumps + the era file — append-only history) while `CURRENT.sha256` holds **2** (the live set only), and `sha256sum -c` passes at the destination |
| A same-day re-run leaves no gap | During the second transfer the previously verified dump stayed in place; it is replaced only by `mv` after the new copy verifies, so the destination is never empty |

**Steady-state nightly cost, measured on the second run: 9 min 35 s** — 4 min 41 s to dump,
4 min 40 s to send and verify, 9 s for the era check. The first run's 14 min 38 s included
the one-time 3.48 GB era upload and will not recur.

⚠ **Retention has not yet fired in production** — it was proven in the controlled test above,
not by live rotation. ⚠ **Re-measured 2026-08-04: still 2 dumps** (08-01, 08-02) against
`OFFSITE_KEEP=7`, because the 08-03 night failed to transfer at all — see the RESTORE-TEST
currency note at the end of this section. Retention cannot fire until 8 nights have actually
*succeeded*, which is further away than the calendar suggests.

#### ✅ NOW PROVEN — a dump was restored and verified working (RESTORE-TEST, 2026-08-04)

**The untested half of this section is closed.** The off-host dump `opensky-2026-08-02.sql.gz`
was fetched from the destination, integrity-checked, and restored into a **version-matched
throwaway TimescaleDB instance** — PostgreSQL 17.10 + timescaledb 2.28.2, from
`sha256:fbc6fcd7ba7b`, the **byte-identical image production runs**, on `127.0.0.1:5436` with
its own volume, capped at `--memory=3g --cpus=4`. ⛔ Production was never touched: no restore
over it, no drop, no rename, no structural change, and no service stopped.

**The full procedure, every command verbatim, is [`docs/RESTORE_PROCEDURE.md`](RESTORE_PROCEDURE.md).**

| Gate | Result |
|---|---|
| Restore itself | **4 m 27 s, exit 0, zero bytes on stderr** |
| End to end (fetch → verified working) | **≈ 10 min**; ≈ 5 min if the dump is already local |
| `state_vectors` rows | **76,236,826 = production's count within the dump's coverage, exactly.** Zero data loss |
| Hypertables restored **as hypertables** | **5 of 5** — the classic TimescaleDB silent failure did not occur |
| Chunks | 54 vs production's 59; **all 5 absent chunks date 2026-08-03 → 09-04**, i.e. created after the dump. Nothing in the restore is absent from production |
| Continuous aggregate | `state_vectors_1m` present, `materialized_only=f`, queryable, and `refresh_continuous_aggregate` succeeds |
| **Policies restored *and running*** | **7 of 8 jobs executed, 0 failures.** Job 1003 (cagg refresh) **6 runs / 6 successes**; job 1002 compressed an overdue chunk on its own |
| Constraints / indexes on public tables | **36 and 56 — name-for-name identical to production** (`diff` → no output) |
| Real project query | `/api/v1/stats` (`backend/api.py:347` at test time; since the 2026-09-01 split: `backend/routes/air.py`) verbatim → **byte-identical to production**; `EXPLAIN` confirms it Appends the materialization hypertable to a live `state_vectors` scan |
| Write path | `INSERT` routed into a newly created chunk (23 → 24), `ROLLBACK` clean; a bad row was **rejected** by the restored `state_vectors_ts_matches_epoch` CHECK |

#### ⛔ The circular-FK warning was a red herring — and the real trap is worse

**`--disable-triggers` was never needed and does not apply.** That flag is for
`pg_dump --data-only` dumps restored with `pg_restore`. The nightly dump is a **full plain-SQL**
dump restored with `psql`, where foreign keys are created *after* the data loads, so circular
FKs never block it. pg_dump emits that warning whenever it *detects* circular FKs, whether or
not the dump it is producing is affected.

⛔ **The actual requirement is `timescaledb_pre_restore()` / `timescaledb_post_restore()`,
which the warning never mentions.** Measured: the obvious command —
`gunzip -c dump.sql.gz | psql -d opensky` — **exits 0** and produces a silently damaged
database. Its entire stderr was five lines, and they cost:

| Damage from the naive restore | Measured |
|---|---|
| **9,431,499 rows destroyed** | `_hyper_4_494_chunk`, the `state_vectors` chunk for 2026-07-26: 9,431,499 rows in production, **0** in the naive restore. Proof it was the only loss: production holds 76,236,826 rows within the dump's coverage, the naive restore produced 66,805,327 — a difference of **exactly 9,431,499** |
| **All 4 hypertable primary keys missing** | The four `ERROR: ONLY option not supported on hypertable operations` lines are the PKs of the only four hypertables that have one — `state_vectors_pkey`, `vessel_states_pkey`, `satellite_history_pkey`, `mt_vessel_states_pkey`. This also removes the `Index Only Scan on PK` path `BACKEND_CHANGELOG` Gate 3 relies on |
| **116 duplicate chunk indexes** | Every chunk index created twice — once by the dump, once by the still-live extension hooks — renamed with a `_1` suffix. Correct restore: **0** |

⚠ **The hypertables, the continuous aggregate and the `/stats` query all still *looked* fine in
that damaged database.** It is 99 % right and silent about the 1 %, which is the most dangerous
outcome available and the reason `ON_ERROR_STOP=1` is mandatory in the procedure.

#### ✅ CLOSED — the three data-safety defects RESTORE-TEST found (BACKUP-HARDEN, 2026-08-04)

**All three are fixed, each in its own commit, each proven by making it fail on purpose.**

**1. The free-space gate failed open — `backup_offsite.sh`.** Measured in the 08-03 journal:
with the peer unreachable, `remote_free` returned an empty string and the shell reported
`backup_offsite.sh: 143: [: Illegal number:`. `[ "" -lt N ]` is not false, it is an **error
with status 2**, and inside an `if` any non-zero status reads as **false** — so the branch was
skipped and the run continued past the check that exists to stop it. ⛔ **A guard that stops
guarding when its measurement fails is worse than no guard, because it also reports that it
checked.**

Two mechanisms, both fixed:

- **`remote_free` was one remote pipeline.** A pipeline's exit status is its *last* command's,
  so `tr` returning 0 **masked ssh dying** — the function reported success while printing
  nothing. ⚠ This script's own header documents that exact lesson for `pg_dump | gzip`; it had
  simply never been applied here. ⚠ And pipefail cannot fix it: `/bin/sh` is **dash**, which
  has none. The pipeline is now split so ssh's status is checked alone.
- **Every measured input now passes `is_uint` / `is_sha256` before any comparison,** and a
  failed measurement is an explicit logged failure — never a skipped branch.

**The full audit — every comparison in the script whose input can come back empty:**

| Site | Finding | State |
|---|---|---|
| `:141` `avail` | ⛔ the reported defect — gate skipped silently | **fixed** (status *and* shape checked) |
| `:129` `remote_free` | ⛔ the pipeline that made it invisible | **fixed** (split) |
| `:136` `size=$(stat -c %s …)` | ⚠ unvalidated — empty made `$((size + 1GiB))` evaluate to just the margin, so the gate asked for 1 GiB regardless of the real file size | **fixed** |
| `:147` `src_sum` | ⚠ unvalidated; fails closed today *only* because `dst_sum` is checked non-empty first | **hardened anyway** — two failed hashes must never compare equal |
| `:220` `era_sum` | ⚠ empty turns the test into `grep -qx ''`, which **matches an empty line** — a local hashing failure could report "already present and identical" about bytes never compared, and skip the upload permanently | **fixed** |
| `:239` `KEEP_FROM=$((OFFSITE_KEEP + 1))` | ⚠ **shell-dependent, and the first reading of this was wrong.** Measured both: under **bash** `OFFSITE_KEEP=abc` gives `KEEP_FROM=1` → `tail -n +1` → **every dump selected for `rm`**; under **dash** it aborts with `Illegal number: abc`. `/bin/sh`→dash and the unit runs `/bin/sh <script>`, so the deployed path **fails closed** — but `bash scripts/backup_offsite.sh` is a plausible thing to type | **fixed** (validated, closed in both shells) |

⚠ `${VAR:-default}` covers unset and empty but **never garbage** — that is why validation, not
defaulting, was the fix.

**⛔ Detonated, not asserted** (the BE-SUMMARY-4 rule). A harness runs the **real shipped
script** with a fake `ssh` injecting the measurement failure and a fake `docker` so production
is never touched. ⚠ The verdict is **not the exit code** — both versions exit 1 — it is
**whether a transfer was attempted while free space was unmeasurable**:

| Case | Transfers attempted | Verdict |
|---|---|---|
| **pre-fix**, empty `df` output (the exact 08-03 shape) | **1** | ⛔ **fail-open** — stderr shows `143: [: Illegal number:`, then a *checksum mismatch*: the run was stopped by something else entirely and the gate contributed nothing |
| post-fix, empty `df` output | **0** | ✅ refused, failed loudly |
| post-fix, ssh dies on `df` | **0** | ✅ refused, failed loudly |
| post-fix, non-numeric `df` output | **0** | ✅ refused, failed loudly |
| post-fix, `OFFSITE_KEEP=abc` | — | ✅ dies at config validation; retention never reached |

⚠ The gate now **warns and returns non-zero** instead of calling `die` directly. Deliberate:
`die` inside `push_verified` would abort from a helper and skip the `LAST_SUCCESS` write, so a
stalled one-time era upload would cost the nightly dump its freshness marker — the behaviour
this section documents as intentional. The caller decides the blast radius.

**2. The dumps now carry their own restore instructions.**
`docs/OFFSITE_RESTORE_README.md` is uploaded as **`READ_THIS_BEFORE_RESTORING.md`** to the
**root** of the destination, beside `CURRENT.sha256` and `LAST_SUCCESS` — through the same
checksum-verified path as the dumps, **included in `CURRENT.sha256`** (so `sha256sum -c` proves
the instructions are intact too), **before** the dump (a run that later fails still refreshes
them), and **on every run** — a file written once drifts out of date and silently stops
describing the dumps beside it.

⛔ **`pg_dump -Fc` was tested and rejected on evidence.** The premise was that `-Fc` prevents
the mistake structurally because it forces `pg_restore`. **Measured 2026-08-04** on a
purpose-built reproduction (hypertable with a PK and a CHECK, 8 chunks, 3 compressed, a
real-time cagg with a refresh policy, 108,001 rows), restored three ways:

| Path | Rows | Public constraints | Duplicate indexes |
|---|---|---|---|
| source of truth | 108,001 | 2 | 0 |
| `-Fc` + `pg_restore`, **no** `pre_restore` | **86,401** | **1** (PK lost) | **10** |
| plain + `psql`, **no** `pre_restore` | **86,401** | **1** (PK lost) | **10** |
| `-Fc` + `pg_restore`, **with** `pre_restore` | 108,001 | 2 | 0 |

**`-Fc` is damaged identically** — same two errors, and ⚠ **`pg_restore` ignores errors by
default too** (`errors ignored on restore: 2`). The format is irrelevant; the damage comes from
TimescaleDB's live hooks and only `timescaledb_pre_restore()` prevents it. Changing the format
would also have invalidated the tested procedure, broken the `gzip -t` gate and the
`opensky-*.sql.gz` retention glob, and bought nothing. ⚠ A `restore_db.sh` wrapper was the
third option and shares the status quo's fatal weakness: it lives **here**, not **there**.

**3. A missed night is now measured, not discovered 44 hours later.**
[`scripts/check_offsite_freshness.sh`](../scripts/check_offsite_freshness.sh) reads
`LAST_SUCCESS` from **the destination's own evidence** and compares its age to §9's **26 h**
threshold, exiting non-zero with an explicit verdict. It also reports dumps held, the newest
dump, stray `.part` files, and whether the restore README is present. It runs standalone and is
called by `backup_offsite.sh` **before** the run can overwrite `LAST_SUCCESS`, so the journal
records how long the copy had actually been stale.

⛔ **It follows §9's pattern: the measurement is the check, a unit listing is a claim.**
`systemctl list-timers` showed this timer healthy and `active (waiting)` the whole time — it
fires nightly and its last firing *failed*, which looks identical to a timer with nothing to
do. So the script never asks systemd anything.

⚠ **What it cannot do, stated plainly: if the timer never fires at all, nothing here runs
either.** In-band checking catches a *failing* run, not an *absent* one. Nothing yet schedules
the standalone check independently — wiring it to its own timer needs `deploy/systemd/`, which
was not this window's to touch.

Proven both ways: it flagged the real state (`45h 22m`, exit 1) and, with a generous threshold,
returns `✅ FRESH` — so it is not a check that always fails. Fail-closed paths verified:
unreachable destination, missing `LAST_SUCCESS`, and a non-numeric threshold each report
**UNMEASURABLE** and exit 1, because an unknown age is not the same as a healthy one.

⚠ **One thing did not behave as expected, and the cause is NOT established — stated rather than
guessed.** On the end-to-end verification run the check **ran and returned non-zero** (its
warning is in the journal, and the run went on to succeed), but **not one of its own output
lines was recorded in that unit's journal** — while the identical script run under
`systemd-run --user` journals all seven lines. So on that run the operator would have seen
*"STALE or unmeasurable"* without the numbers behind it, which is most of the signal lost.
**Rather than guess at the mechanism, the signal was made independent of it:**
`backup_offsite.sh` now **captures the check's combined output and prints it itself**, so
whether the numbers reach the journal no longer depends on how a child process's streams are
inherited. ⚠ The underlying journald/stream behaviour remains unexplained and is worth a
separate look — it could equally be swallowing output from other child processes in these units.

#### ⚠ Off-host currency at RESTORE-TEST time — one night is missing

⚠ **`LAST_SUCCESS` was `2026-08-02T08:25:03Z`, i.e. 44 h stale** against §9's ~26 h rule. The
2026-08-03 04:15 run **failed**: `ssh: connect to host 10.10.20.57 port 22: No route to host`,
`Result=exit-code`, `ActiveState=failed`. The destination holds **2** dumps (08-01, 08-02),
not 7.

- ✅ The failure was **clean**: nothing was promoted, no `.part` was left in `db/`, the staging
  dump was removed by the script's `EXIT` trap, and the prior good copies were untouched. The
  design held.
- ⚠ The peer was **up** at that time (`uptime` on 10.10.20.57 gives a boot of 2026-08-02
  ≈ 11:16 UTC) and is reachable now, so this was a **network-path** failure, not a dead host.
  Cause not determined — stated rather than guessed.
- ⚠ **08-03's data does have a local dump** (`~/scraplan-backups/opensky-2026-08-03.sql.gz`,
  3,031,933,374 B, from the 03:00 local timer) but **no off-host copy**. ⚠ Note the local and
  offsite series are **independent dumps taken an hour apart**, not copies of each other, so an
  offsite hash can never be checked against a local file.
- ✅ **The 08-03 gap is closed.** BACKUP-HARDEN backfilled it off-host **by hand from the local
  series**, after verifying that copy first: `gzip -t` clean, a valid `pg_dump` 17.10 header,
  3,031,933,374 B → 7,881,948,099 B uncompressed, sha256
  `b5743ee2c9a542eea5df3c12bfd113e57a6d5aa94aa996cf931732ee81e947ec`, then streamed with the
  script's own discipline — hash here, hash **at the destination**, compare, promote, record in
  `MANIFEST`. ⛔ **`LAST_SUCCESS` was deliberately NOT touched**: it means "the nightly run's
  dump arrived and verified", and overwriting it for a manual backfill would have erased the
  staleness signal item 3 exists to raise.
- ⚠ serverq is no longer a viable restore target: measured **805 MB available RAM with swap
  4084/4095 MiB (99.7 %) full** (it runs GitLab CE), and it has **no timescaledb image** —
  only `postgres:17` and `pgvector/pgvector:pg17`, neither of which can host this dump.

#### ⚠ Recorded by BACKUP-HARDEN, deliberately NOT fixed — three separate items

1. **Retention cannot fire until 8 nights have actually *succeeded*, not 8 nights on the
   calendar.** `OFFSITE_KEEP=7` and the destination holds **3** dumps (08-01, 08-02, and the
   08-03 backfill) — so the pruning path is still unexercised in production, and every failed
   night pushes that further out. It was proven in a controlled test (§6.8 above), never by live
   rotation. ⚠ Do not read a growing dump count as retention working.
2. **`/tmp` on this host is `tmpfs`** — 16 GB, RAM-backed (`findmnt -no FSTYPE /tmp` → `tmpfs`).
   ⛔ **Never stage a multi-GB dump or restore artefact there**: it is charged to memory on a
   host whose swap is already 100 % full (§6.10). RESTORE-TEST staged on the ext4 volume for
   exactly this reason. Nothing in the backup scripts currently uses `/tmp`; this is recorded so
   that stays deliberate.
3. ✅ **CLOSED by ROTATE-HARDEN (2026-08-04) — see §6.15.** This was the highest remaining data
   risk in the project: `rotate_archive.sh` **moves** rather than copies, so its fail-open
   deleted the only copy. Fixed, and the deletion was **reproduced before being fixed**.

### 6.15 ✅ CLOSED — the path that deleted the only copy (ROTATE-HARDEN, 2026-08-04)

`rotate_archive.sh` is the one script in this project that **moves ground truth rather than
copying it**: `backup_offsite.sh` adds redundancy, this one frees the volume, so its `rm -f
"$src"` removes the **only** copy. Its verification was correct in intent and fail-open in
implementation.

⛔ **The deletion was reproduced before it was fixed.** Running the pre-fix script against a
sandbox tree with `sha256sum` injected to fail:

| Measured, pre-fix | Result |
|---|---|
| **Source deleted?** | ⛔ **YES** — the only copy, with **both** hashes unmeasured |
| Exit code | ⚠ **0** — so `systemd` would have shown the unit **green** |
| `MANIFEST.sha256` entry written | ⛔ `‹empty›  opensky_output/states-2026-07-01.jsonl.gz` — an index line whose **hash field is empty**, which `sha256sum -c` can never verify, describing a local file that had already been deleted |

Two mechanisms compounded, both now fixed: `sha256sum "$f" | cut -d' ' -f1` is a **pipeline**,
whose status is its *last* command's, so `cut` returning 0 **masked the hash failing** (⚠ and
dash has no `pipefail`); and a failed hash is an **empty string**, so `[ "" != "" ]` is **false**
— two failed measurements "matched", the verification reported success, and the `rm` ran.

**The governing rule now: no source is deleted except after a proven match of two hashes that
were both read successfully and are both well-formed 64-hex values.** An unmeasurable hash is
neither a match nor a mismatch — it is a failed measurement, which stops the step, keeps **both**
copies, and is **recorded**. Tag, never delete.

#### The full audit — the named four, plus two the audit found

| Site | Finding |
|---|---|
| `:132` `src_sum` | ⛔ masked pipeline **and** unvalidated |
| `:136` `dst_sum` | ⛔ masked pipeline **and** unvalidated |
| `:138` `[ "$src_sum" != "$dst_sum" ]` | ⛔ two empties compare **equal** → authorised the `rm` |
| `:180` `-mmin +$((ARCHIVE_AGE_HOURS * 60))` | ⚠ non-numeric → `0` under bash → `-mmin +0` relocates **and deletes** a file rotated seconds ago, destroying the local copy same-day backfills need. Under dash it aborts, so the deployed path fails closed; validated for both |
| **`:97` `[ "$dest_src" = "$root_src" ]`** | ⛔ **FIFTH — and it guards this script's documented safety property.** Only *one* of the two measured values was checked for emptiness. An empty `dest_src` against a non-empty `root_src` is **unequal**, so the check **passes** and the script writes into a path it has not proven is a mounted volume — exactly the failure that function exists to prevent |
| **`:146` → `:147`** | ⛔ **SIXTH — ordering.** The `MANIFEST.sha256` append was **unchecked**, and `rm -f "$src"` came next regardless. A destination gone full or read-only would fail to record the file and the original would still be deleted, leaving ground truth indexed **nowhere**. Now: if it cannot be recorded, it is not deleted |

⚠ Also hardened: `cp`/`mv` in `archive_one` were unchecked. They failed *closed* only by luck
(an absent destination yields an unverifiable hash), and `archive_one` is invoked as
`archive_one "$f" || …`, **which disables `set -e` for the whole function body** — the reason
unchecked failures there fall through to the next line instead of aborting.

#### Detonated — and the verdict is not the exit code

The criterion is **"was the source deleted while the hashes were unmeasured?"**, because a run
can exit non-zero *after* destroying the only copy. Every measurement-failure mode:

| Case | Exit | Source deleted | Manifest | Tagged |
|---|---|---|---|---|
| **pre-fix**, `sha256sum` fails | **0** | ⛔ **YES** | 1 line, empty hash | 0 |
| post-fix, `sha256sum` fails | 1 | ✅ **NO** (bytes unchanged) | 0 | 1 |
| post-fix, **destination** hash unverifiable, source fine | 1 | ✅ **NO** | 0 | 1 |
| post-fix, `MANIFEST` unwritable | 1 | ✅ **NO** | 0 | 1 |
| post-fix, `ARCHIVE_AGE_HOURS=abc` | 1 | ✅ **NO** — dies before any work | 0 | 0 |
| post-fix, destination on the **root** volume | 0 | ✅ **NO** — skips, as designed | 0 | 0 |
| post-fix, **healthy** (regression check) | 0 | ✅ yes — copied, verified, **indexed**, then deleted | 1 line, real hash | 0 |

**A full rotation round passes end-to-end** on the shipped script: all six live logs rotated,
`state_vectors` rotation correctly still disabled, both aged archives relocated with real 64-hex
hashes, and `sha256sum -c MANIFEST.sha256` **passing at the destination**.

⛔ **Zero real archives were touched.** The external volume is **not mounted** (`findmnt -T
/media/gis-syr/EXTERNAL1` resolves nothing), so the real archival path skips and deletes nothing
anyway; and every test ran against a sandbox via `SCRAPLAN_BASE` + `ARCHIVE_DEST`. Proven by
inventory before and after: **47 + 33 + 38 = 118** real `.jsonl.gz` totalling 445,504,210 +
89,799,557 + 3,464,378 B, with an identical `(mtime, path)` fingerprint
`04356ae7fe010ec4e63e79b42cd9e2d6` on both sides, plus 1 era file, 13 local DB dumps and 4
off-host dumps unchanged.

⚠ **One deliberate change beyond the defect:** `BASE` is now `${SCRAPLAN_BASE:-…}`. The
fail-open survived this long partly because **nothing could execute this file without touching
real ground truth** — `BASE` was hardcoded, so there was no way to exercise the delete path
safely. The default is unchanged, and the variable is deliberately *not* named `BASE` so an
unrelated exported value cannot silently redirect the whole rotation.

### 6.8b The original problem statement, kept for the record (measured 2026-07-29)

⚠ **Everything in this subsection is the 2026-07-29 snapshot that motivated the work, kept
because the reasoning is what justified BACKUP-EXT — not the current state.** Its figures are
superseded; the corrections are at the end of it and in §6.8 above. Read it as history.

**Measured:** `~/scraplan-backups` holds **14 GB in 11 files** (`du -sb`), on
**`/dev/nvme0n1p2`** — the same single ext4 volume as the TimescaleDB Docker volume it dumps
(`findmnt -T`). Retention works (`find … -mtime +14 -delete`), so this is **not** an
accumulation problem. Per-dump size is **2.84 GB and rising**. Last successful run
2026-07-29 03:03:43 EDT.

**The risk is co-location, and it is total:** one volume failure, one accidental `mkfs`, or one
filesystem corruption takes the database **and every backup of it** in the same event. A backup
that cannot survive the loss of the thing it backs up is not a backup — it is a second copy.

**Why it was open:** there was nowhere to put it. Measured 2026-07-29 — no second block device
(`/proc/partitions`, `/sys/block`), no mounted removable disk (the USB `sda` was pulled
2026-07-29 06:28:52), `/media/gis-syr/EXTERNAL1` **does not exist**, and the only Tailscale peer
is an offline Android.

**The mechanism was already built, guarded and verified — it was waiting on a destination.**
`rotate_archive.sh` performs sha256-verified relocation (source hash → copy → hash read back
from the destination → delete only on an exact match, permanent `MANIFEST.sha256`), refuses any
destination that is not a real mount off the root volume — both refusal paths tested — and
already carries the 3.48 GB era file via `STATE_VECTORS_HISTORY` (§7).

**The decision was the owner's, and it was a choice between three, not a chore:**

1. **External media** — reattach a disk; the guarded path then runs unchanged, backups included.
2. ✅ **Network upload** — **TAKEN.** An off-site target. The owner supplied one
   (`serverq@10.10.20.57`) and it measured out: a second physical host, reachable over the LAN,
   664 GB free, already trusting an existing key. See §6.8 above.
3. **Documented acceptance** — decide the risk is tolerable and record it as accepted, with the
   reasoning, so it is a stated position rather than an unnoticed gap.

⚠ **The encryption-at-rest decision named in option 2 was NOT made and remains open.** The
dumps sit unencrypted on the destination's filesystem, readable by `serverq` (uid 1000, in
`sudo` and `docker`). That is a deliberate non-decision by this window, not an oversight:
it is an owner call about who may read the data, and nothing about it blocks the copy from
existing. **Raise it before the destination is treated as anything other than a second
trusted machine on the same LAN.**

**Doing nothing silently is the only wrong answer** — that is how the identity timer went 9 days
unnoticed (§6.4).

⚠ **Two figures in the paragraph above this section are stale and were corrected on
2026-08-01**: `~/scraplan-backups` measured **14 GB in 11 files** on 2026-07-29 and now holds
**23 GB in 13 files**; per-dump size was **2.84 GB** and is now **3.06 GB**
(`opensky-2026-08-01.sql.gz` = 3,055,261,823 B from the 03:00 run). Growth is ~50 MB/night.
The retention window (14 d) has not changed, so the directory is bounded — but bounded at a
rising ceiling, now ~43 GB rather than the ~40 GB estimated in §7.

⚠ **Three nights are missing from the local series entirely — 2026-07-23, 07-24 and 07-28** —
with **no file and no `.failed` artifact** for any of them. `backup_db.sh`'s flag-never-delete
rule produces a `.failed` stub when a dump *runs* and fails, so its absence means those runs
**never started** (host off, or the user manager not running) rather than failed silently.
`Persistent=true` on `scraplan-backup.timer` should have caught up a missed run at next boot,
so this is unexplained and worth a look. Recorded in §9.

### 6.9 ✅ CLOSED — the severity-ordering conflict, decided and implemented (DASH-1)

The country-events card wanted **military first**; measured, that was the exact inverse of a
documented commitment (`backend/alerts.py`: military is **INFO**, deliberately sorted *below*
LOW, *"informational, NEVER a threat"*).

**Decided: a separate axis, not a re-ranking. `SEVERITY_ORDER` is untouched.**

`severity` still answers *"how dangerous is this claim?"*. A new `importance` answers *"how much
does this deserve the analyst's eye in a country window?"*. Both are true of one alert at once —
a military alert is `severity="info"` **and** `importance=1` — so the card can lead with military
presence while every card keeps its honest INFO badge and its `severity_basis`. Same discipline
as `position_quality` / `classification` / `imo_quality`: three axes, never conflated.

⚠ **`importance` is not a threat score** — never colour by it, sum it, or present it as risk.
`/alerts?sort=importance` selects the order only; `sort=severity` remains the default and is
byte-for-byte the pre-DASH-1 behaviour. Verified live on `?country=SY`: under `importance`,
military(info, 1) leads; under `severity`, military sinks last exactly as before.

### 6.11 ✅ DASH backend — country numbers are ready for the card (DASH-3, 2026-08-01)

The blocker on card element **2e** is cleared: `?country=` now scopes **the alert list, `counts`
and `domains[]`** together, all from the committed polygons. A country card can be built from
**one** `/alerts?country=XX&window_hours=N&sort=importance` call — the list, the per-family
counts and the three domain stats all agree, because each family is filtered before it is
counted and every number derives from the same classifier. Numbers and shapes in §3.

**What a UI consumer must carry with them:**

- **`counts` and `domains[]` are SCOPED when `country` is set** and global when it is not. Read
  `countries_filtered` before presenting any number — that echo is what makes a scoped count
  unambiguous, and it is why the DASH-1 "always global" rule could safely be reversed.
- ⚠ **The space stat answers a different question when scoped** — *objects with a pass over this
  country in the window*, not the tracked catalogue. Do not label it "satellites tracked".
- ⚠ **Never place the scoped air count beside an `/adsb` total.** Air is opensky-only; `/adsb`
  is a separate, larger feed. Each `source` string names its population — show it.
- **`importance` is not a threat score** (§6.9). `severity` + `severity_basis` remain the only
  honest register; `importance` orders a list.

**Still not built, and deliberately out of scope** — the flags (an ISO map exists,
`backend/country_iso.py`; the seven glyphs are a frontend choice, and Unicode regional-indicator
emoji need no asset at all), and a composed `/entity/{kind}/{id}` contract, which `DASH_PRE.md`
§3d sized as a convenience rather than a prerequisite: the three payload strips already compose
list + detail today.

### 6.12 ✅ DASH-4 — the left panel can show a vessel WHOLE, not a summary (2026-08-02)

**This completes card element 2f.** 2f wired the home's left panel to fetch one entity's live
payload by `live_key`, and it shipped with a measured gap stated in its own header: aircraft and
satellites had a single-entity route to their **live** contract, **vessels did not**. The panel
therefore rendered the **summary** contract for a vessel and named the four fields it could not
reach — **heading · destination · nav_status · draught**. That was honest, and it was temporary.

**`/maritime/vessel?mmsi=` closes it** — the same `Vessel` contract, for one MMSI, in **2.6 ms**
and **564 bytes**. Contract, states and measured costs are in §3. Three things a consumer must
carry:

- **`state` is load-bearing, not decoration.** `live` and `last_known` are different claims. A
  `last_known` payload is a real position from a real fix — just **not now** — and `age_seconds`
  is what keeps that legible. Rendering the two identically re-creates the exact confusion 2f
  was built to avoid.
- ⚠ **`not_reported` is the honest replacement for the panel's hard-coded absence list.** It is
  computed per vessel, so a hull that *does* broadcast draught no longer reads as missing it.
  `ship_id` and `flag` are excluded by design (structural nulls, §3) — do not add them back.
- ⚠ **The panel's synthesised provenance must go.** It currently builds a `Vessel` by hand with
  `source="vessel_states"`, `data_origin="search/vessel"` — values no collector ever emitted.
  The endpoint carries the **stored** `source`/`data_origin` through untouched; fabricated
  provenance in a contract field is the thing §3's "nothing is classified or enriched" forbids.

**What did NOT change, deliberately:** `/search/vessel` keeps serving the summary contract — it
answers a different question (identity + behavioural event counts across a hull's MMSIs) and is
still the right call for the search list and for `position_quality`. DASH-4 adds a route to the
live contract; it does not replace the summary one.

### 6.10 Memory floor — measured, nothing stopped (MEM-CHECK, 2026-08-01)

Storage was resolved at source (§6.5); **memory is now the tightest resource on this host.**
Measured 2026-08-01 13:37–13:45 UTC. **This session stopped nothing and killed nothing** — it is
an inventory so the stop decision can be made per row.

**Host:** 30 GiB total · 28 GiB used · **`shared` 9.3 GiB** · `available` **1.7–2.2 GiB** ·
**swap 4.0 GiB, 100 % full** (188–724 KiB free). Window: 7 samples over 180 s.

⚠ **Swap being 100 % full is not currently causing stalls.** `/proc/pressure/memory` reports
`some` **and** `full` at **avg10 = avg60 = avg300 = 0.00`** — the swap is holding *cold* pages,
static, not thrashing. Cumulative since boot: `pswpout` 3,837,033 pages (≈ 14.6 GiB written),
`pswpin` 2,164,964 (≈ 8.3 GiB read back), `pgmajfault` 9,962,853. **The risk is headroom, not
present stalling:** there is no room left to absorb a spike.

✅ **No OOM kill has occurred.** Verified rather than assumed: `journalctl -k` **is** readable as
this user and returns **0** lines matching `out of memory|oom-kill|killed process` since
2026-07-29; `/var/log/kern.log` is readable and also **0**. Nothing on this host has been
OOM-killed — including nothing of scraplan's.

#### Where the memory actually is

| Consumer | Footprint | Project | Role |
|---|---|---|---|
| **`adsb-scraper-collector`** (Playwright/Chromium, 50–52 procs) | **7,981–8,231 MiB** cgroup · peak **8.25 GiB** · tree RSS 8,369–8,791 MiB · **PSS 6,949 MiB** | **scraplan upstream** | **live — ground truth. ⛔ NOT TO BE STOPPED.** |
| `scraplan-timescaledb` | `docker stats` 7.72 GiB — but **anon just 14 MiB**, `shmem` **7.62 GiB**, file 7,828 MiB, swap 197 MiB | **scraplan** | live. ⛔ **Untouchable.** |
| VS Code (`/snap/code/250`) | **3,780 MiB** RSS, 32 procs | desktop | the owner's IDE (incl. this window) |
| Firefox | **2,894 MiB** RSS, 17 procs | desktop | live |
| Claude Code CLIs | **1,468 MiB** RSS, 8 procs (4 sessions) | desktop | live |
| **`mastodon-*` (7 containers)** | **728 MiB anon** + 250 MiB swap | other project | **idle** — stop candidate |
| **`scei-*` (12 containers)** | **548 MiB anon** + 775 MiB swap | other project | **idle** — stop candidate |
| OnlyOffice | 518 MiB RSS, 8 procs | desktop | live |
| `adsb_scraper` python + Xvfb | 543 MiB RSS, 3 procs | scraplan upstream | live — part of the collector tree |
| scraplan's own units | server 105 MiB · adsb-ingest 33 MiB · collector 33 MiB · military 28 MiB · maritime 26 MiB | scraplan | live — **225 MiB for all five**, negligible |
| **`face-pg-local`** | 0 anon + 17 MiB swap (+36 MiB file) | other project | **idle & broken** — stop candidate |
| **`thro-postgres`** | 0 anon + 12 MiB swap (+5 MiB file) | other project | **idle 3 days** — stop candidate |
| **`matrix_backend`** | **0 B — no RSS at all** | dead project | **restart-looping** — stop candidate (frees no memory) |

⚠ **`scraplan-timescaledb`'s 7.72 GiB is not private memory.** Its cgroup reports `anon`
**14,958,592 B (14 MiB)** against `shmem` **8,184,573,952 B** — i.e. **≈ 99.8 % is
`shared_buffers` / page cache**, and it is exactly what `free` reports as `shared 9.3Gi`. Reading
`docker stats` as "the database is eating 7.7 GB of RAM" would be wrong.

#### The collector: a ceiling, not a runaway leak

**Answer to the question that matters for a future scheduled restart: it plateaus.** Evidence:

- **Process count is stable, not growing:** 50 → 51 → 51 → 50 → 52 → 51 → 51 across 180 s.
  `TasksCurrent` **994** against `TasksMax` 37,216.
- **Memory oscillates without trend** over the same window: cgroup 8,115 → 8,039 → 8,155 →
  7,981 → 8,231 → 8,024 → 8,203 MiB. One renderer (PID 61789) holds **4,958–5,174 MiB alone** —
  the single largest process on the host — and sawtooths rather than climbs.
- **The decisive figure:** `MemoryPeak` over the unit's **entire 3-day run** (active since
  Jul 29 10:04 EDT, `NRestarts=0`) is **8.25 GiB**, only **4 % above** the current reading — while
  the *current browser session is just 4 h 50 m old*. The previous session ran **2 d 8 h**
  (Jul 29 20:54 → Aug 1 04:53) and did not push the run peak higher. So the tree **climbs to
  ~8 GiB within hours and then oscillates there**; it does not grow without bound.
  `MemorySwapPeak` 813 MiB.
- **Therefore a scheduled periodic restart is not justified on memory grounds** — it would
  reclaim ~7 GiB for a few hours and the next session would refill to the same ceiling. Kept as a
  future item only, and **not acted on here.**

**The recycle mechanism already exists, and it is health-triggered, not memory-triggered.** The
journal shows `browser unhealthy at cycle start; restarting` at **Jul 29 15:27:46**,
**Jul 29 20:54:09** and **Aug 01 04:53:03** — the last one carrying a `TargetClosedError` with
`SharedImageManager::ProduceSkia` GPU errors. Irregular (5 h 27 m, then 2 d 8 h), driven by
health, under a python parent that has lived **2 d 23 h**. If a memory trigger is ever wanted,
it should hook this existing watchdog rather than add a systemd timer.

#### Safe-stop candidates — owner's decision, per row

✅ **Isolation verified, not assumed.** `scraplan-timescaledb` is alone on network
`scraplan_default`, published only on **127.0.0.1:5435**. The candidates are on unrelated
networks (`mastodon_internal/external_network`, `scei_default`, default `bridge`,
`matrix-server_default`) — **no shared network with scraplan**. And a repo-wide grep for
`mastodon|scei|face-pg|thro-postgres|matrix` outside `docs/` returns **no hit**: every scraplan
unit references only `127.0.0.1 5435`. **Stopping any row below cannot reach scraplan.**

| Candidate | Frees (anon + swap) | Idle? — evidence | Touches scraplan? |
|---|---|---|---|
| **`scei-*` (12 containers)** | **≈ 1,323 MiB** (548 anon + 775 swap) | **Yes.** **0** non-`/healthz` HTTP lines in the last 2,000 log lines; all 12 show identical net I/O (46.3 MB / 126 B) — healthcheck traffic only; `scei-postgres` has 1 internal conn, 0 anon | **No** |
| **`mastodon-*` (7 containers)** | **≈ 978 MiB** (728 anon + 250 swap) | **Yes.** `LOCAL_DOMAIN=mastodon.local`, caddy published to **127.0.0.1** only, so unreachable from outside; the *only* requests are `GET /health` every 30 s from its own healthcheck; **sidekiq queue depth 0**; the only other activity is renewing its own `.local` cert | **No** |
| **`face-pg-local`** | ≈ 17 MiB (+36 MiB file) | **Yes, and broken** — logging `FATAL: database "face_admin" does not exist`. ⚠ Something still probes it; **the client was not attributed** and is not guessed here | **No** |
| **`thro-postgres`** | ≈ 12 MiB (+5 MiB file) | **Yes.** Last log line **2026-07-29 06:49** — silent 3 days; 1 conn, 0 anon | **No** |
| **`matrix_backend`** | **0 MiB** — it holds no memory | **Dead project.** `restartCount` **4,709**, exit **255**, `unless-stopped`; `matrixdotorg/synapse:latest` now fails `exec /start.py: no such file or directory` — the upstream image moved its entrypoint, so **this container can never start again**. An older `/synapse` container also sits `exited` | **No** |

**Total if every row is stopped: ≈ 2,330 MiB (1,276 MiB anon + 1,054 MiB swap)** — which would
roughly double `available` and, more usefully, **return ~26 % of the full 4 GiB swap**.

⚠ **This is smaller than the framing expected, and the shape of the problem is different.**
Other projects' containers are **not** where the memory is. All of them together come to ≈ 2.3 GiB,
while the **two untouchables hold ≈ 16 GiB of the 30** (collector ~8.2 GiB + the database's
7.6 GiB of `shared_buffers`), and the **desktop applications the owner is actively using total
≈ 8.7 GiB** (VS Code 3.8 + Firefox 2.9 + four Claude CLIs 1.5 + OnlyOffice 0.5) — more than every
other project combined. **Closing a browser window or an idle IDE window frees more than stopping
all 21 foreign containers.** Stated so the decision is made against the real distribution.

⚠ **`matrix_backend` is the one row that is free of judgement but frees no memory.** Its cost is
4,709-and-counting container restarts, the CPU/IO churn of each, and unbounded log growth — not
RSS. Worth stopping for hygiene; **it will not move `available` by a single MiB.**

**Nothing was stopped, disabled, or deleted by MEM-CHECK.** `matrix_backend` should be
**stopped, not removed**, if the owner agrees — `docker stop` respects `unless-stopped`, so it
stays down across reboots without discarding the container or its config.

### 6.13 ✅ CLOSED — all five families carry the Arabic register (BE-SUMMARY-1…5)

`headline` / `why` / `caveat` are populated for **all five**: maritime (BE-SUMMARY-1),
implausible (-2), emergency (-3), military aircraft (-4) and satellite passes (-5). **No
family shows the user English any more** — measured across 331 live alerts, **0** without an
Arabic headline (`emergency` has no live rows; its templates were verified by injection).

**The sequencing was the point, and each prediction below held.** One wording pass across all
five would have got at least two of them wrong, because the calibrations turned out to be
genuinely different — three of them mutually contradictory:

- ✅ **emergency — DONE (BE-SUMMARY-3).** Exactly as predicted here: a squawk is a REPORTED
  FACT, so the «X alone does not indicate Y» pattern was NOT reused — it would have weakened a
  true statement. The reservation moved instead to what the code means (a category, not a
  severity) and to how common mis-setting is. Tone table in §3.
- ✅ **implausible — DONE (BE-SUMMARY-2).** Exactly as predicted here: it is a DATA fault,
  not behaviour, so its sentences make the OBSERVATION the subject and its caveat points at
  the sensor chain and names the vessel as most likely a victim. The tone table is in §3.
- ✅ **military-air — DONE (BE-SUMMARY-4).** Exactly the risk predicted here: every natural
  Arabic verb implied intent, so all of them were refused and the sentence was built around a
  recorded presence instead («سُجّل وجود…»). Severity verified still INFO, basis untouched.
  Refused-verb table in §3.
- ✅ **military-satellite — DONE (BE-SUMMARY-5), the last one.** Exactly as predicted: a
  FORWARD PREDICTION, so «رُصد» is false for it and every verb is future («يمرّ»), and its
  caveat is a third kind again — SGP4 accuracy decays with TLE age (stated from the carried
  epoch), the orbital catalogue has no country, and the tag is UCS's `users` field frozen
  2023. It also INVERTED the method the other four shared: there is no observation to make
  the subject, only a computation.

⚠ **What is still English, deliberately:** the terse `summary` field, on every family. It was
never removed — it is the machine-facing line and the frontend already stopped rendering it
for the families it has consumed. Removing it is a separate item with its own consumer check.

**Owner decision needed on order and tone**, not on whether. Recommended sequence: implausible
(closest in shape to maritime), then emergency, then the two military types last since they
carry the strictest no-inference commitment.

⚠ Also owed, and cheap: `CompositeTimelineEntry.event_type`'s comment in `backend/models.py`
still reads `ais_gap | rendezvous | identity_change`, but real timelines carry
`gnss_anomaly` and `zone_dwell` too (measured: 164 and 24 events). The comment predates
GNSS-1 and ZONES-3 and is now a stale contract claim.

### 6.14 `satellite_reentry` has a timer, a table, and no consumer — RECOMMENDED: declare it reference, not alertable (BE-FIX-1 item 2, 2026-08-03)

BE-SUMMARY-5 found this while giving the satellite family its Arabic register: the table is
written by `scraplan-reentry.timer` (deployed in TIMERS-2) and reaches the alert layer nowhere.
A layer running without a consumer is either an oversight or an undocumented decision, and it
should not stay ambiguous. **No builder was written here** — the brief asked for the numbers
and a decision.

**What the table actually holds** (measured 2026-08-03):

| | |
|---|---|
| rows · distinct objects | **961 · 145** |
| collection span | 2026-07-18 → 2026-08-03 (fresh, not stale) |
| rows with `country` | **3** (Syria ×2, Turkey ×1) |
| rows with `near_country` | **2** (Turkey ×2) |
| rows with NO geo at all | **956 of 961 — 99.5 %** |
| `decay_epoch` in the future | **6** · in the past | **955** |
| `high_interest` | **0** |

**⛔ The decisive measurement: a builder would essentially never fire.** All **6** future rows
carry no country and no near-country, at **5,396–16,163 km** from the nearest AOI country. And
across the last six collection days — 153 rows — **zero** AOI-attributed rows appeared. The
five that exist in the whole table all predate 2026-07-29, i.e. they came from the initial
backfill, not from steady-state collection.

**⚠ And a second reason, structural rather than statistical: the prediction cannot honestly
carry a place.** `window_min` on the future rows runs from 1 to **1,200 minutes** — a decay
window up to twenty hours wide. An object in low orbit covers most of the planet in that span,
so "reentry near Syria" is not a claim this data can support even when the point estimate lands
nearby. Attaching a country to it would be exactly the kind of false precision the alert layer
exists to refuse.

**The two options, as asked:**

**(a) Build an alert family.** Arabic prose with the same forward-prediction calibration the
satellite pass family uses (computed, not observed; the window stated; no verb implying
observation). ⛔ Cost is not the problem — the problem is that it would emit ~0 alerts in
steady state, and a family that never fires trains the analyst to ignore the surface. Worse, the
only way to make it fire is to widen the geo predicate, which is precisely the false precision
the `window_min` measurement forbids.

**(b) ✅ RECOMMENDED — declare it reference, not alertable, and document that.** The feed keeps
running and keeps its timer; it is queryable for context ("what is coming down, globally") and
`/reentry` already serves it. It simply is not an alert source, and §3 should say so beside the
detector inventory so a future reader does not log it as a missing builder for the third time.

**Owner decision needed on (a) vs (b).** If (b), the follow-up is one documentation line, not
code. ⚠ If the AOI-attributed rate ever rises — the number to watch is `aoi_rows` per
collection day, **0 for six straight days** — the decision is worth revisiting, because the
argument here is empirical and not permanent.

### 6.16 The airport approach band is one-sided, and it reports a different number than it gates on (2026-08-24)

**Measured** on `/api/v1/search/airport?q=OJAI` (Queen Alia, elevation **2,395 ft = 730 m**),
9 approach candidates: `min_altitude_m` = **1173, 838, 290, 221, 145, 107, 46, 38, 30**.

Two separate things, both in [`backend/airports.py`](../backend/airports.py):

1. **The band has no floor.** `landing_candidate` admits an approach when
   `net_descent >= MIN_NET_DESCENT_M and terminal_alt <= elev_m + TERMINAL_M`
   ([airports.py:145](../backend/airports.py#L145)). There is no lower bound, so a track that
   ends *below* the runway is as admissible as one that ends on it.
2. **It gates on `terminal_alt` but REPORTS `min_alt`** ([airports.py:145-147](../backend/airports.py#L145-L147)).
   Those are different numbers. One bad low fix anywhere in an otherwise valid approach
   becomes the published `min_altitude_m`, while the gate never saw it — which is the more
   likely reading of the 30–46 m values at a 730 m field, since the vicinity box (±0.05°,
   ~5.5 km) is applied correctly against the field's own coordinates.

⛔ **WHY IT IS A DECISION.** Adding a floor changes which arrivals the fallback reports at
every high-elevation field, and exposing `terminal_alt` changes a client-visible payload.
Neither is a silent fix. **Nothing was changed.**

⚠ What WAS changed is a test that asserted `all(min_altitude_m > TERMINAL_M)` — a claim about
the receiver estate, not about the detector: the elevation-relative band is a strict SUPERSET
of the absolute one, so a candidate inside the absolute band violates nothing. It now asserts
`any(...)`, which is the only claim that distinguishes the fallback from an absolute ceiling.

### 6.17 ⛔ The GNSS broadcast zone was querying a literal `{source}` — FIXED, and the recompute is the owner's call (2026-08-24)

**A one-token defect that silently removed two of three GNSS classes.**
`IMPL_POS_SQL` carries a `{source}` placeholder, and
[`detect_gnss_anomaly.py:155`](../scripts/detect_gnss_anomaly.py#L155) fetched it **without
`.format(source=source)`** — unlike `MMSI_STATS_SQL` on line 174, which formats correctly.
So the SQL that reached Postgres filtered `source = '{source}'` **literally**.

| Measured 2026-08-24 | Value |
|---|---|
| Rows the query as-sent matched | **0** |
| Rows `source = 'aisstream'` matches | **10,712** |
| ⇒ broadcast jamming zone | **empty, always** |

**The consequence is structural, not statistical.** `classify_episode` reaches
`area-jamming` only when `zone.is_area(cw)` is true, and `decoy_match=True` is produced
**only inside that branch** ([gnss_anomaly_events.py:180-189](../backend/gnss_anomaly_events.py#L180-L189)).
An empty zone therefore makes **both** unreachable — every episode falls through to
`candidate`/`individual`.

**The store shows the whole cost.** Across ALL history (2026-07-06 → 2026-08-24):
**0 `area-jamming`** and **0 `decoy_match=true`** rows — where the same detector reported
**661/326/13** after GNSS-REVIVE (2026-08-04). Because the detector is DELETE-then-INSERT,
each broken run also **overwrote** the correct classifications it had previously written.

**Fixed, and proven by dry run** (`--dry-run` writes nothing):

```
broadcast jamming zone: 58 area cell-windows (>= 10 vessels)   ← was 0
GNSS-ANOMALY EPISODES: 2713
  classification candidate     1344      (store currently holds 2,700)
  classification area-jamming  1356      (store currently holds     0)
  classification glitch          13
  affected-zone cells: 1462 (33 area), outage-subtracted
```

✅ **RECOMPUTED AND RE-ARMED, 16:52 (owner-authorised).** `wrote 2716 gnss_anomaly events
(full replace)` — the store now holds **1,356 area-jamming (1,009 of them `decoy_match`)** ·
1,347 candidate · 13 glitch, against **0 area and 0 decoy** before. `scraplan-gnss.timer` is
`active` again. The suite went green with **no test change**: `1058 passed, 1 skipped`.

⚠ **The recompute was held for authorisation rather than run, because it is outward-facing:**

* `scraplan-gnss.timer` (user scope, ~every 20 min) runs this exact script with **no
  `--dry-run`**. It was **stopped at 16:26:57**, ~3 minutes before its 16:30 firing, so the
  rewrite could not land unreviewed — then restarted at 16:52 once authorised.
* `scraplan-notify.timer` runs `backend.notify.runner` **every 2 minutes** (`OnCalendar=*:0/2`)
  over **3 live subscribers**, and `gnss_anomaly` is a rendered family
  ([newsroom.py:384](../backend/notify/newsroom.py#L384)). ~1,356 newly-classified events
  would reach that path within minutes.
* ⚠ The blast is **bounded by design, not by luck**: `INSTANT_BURST_CAP = 10` per tick with
  the overflow **spilled into the digest, never dropped**
  ([policy.py:84-94](../backend/notify/policy.py#L84-L94)) — whose own comment anticipates
  precisely this case ("one backfill can make a rare family sudden").

⚠ **A "seed without sending" path does not exist** after the first baseline
([runner.py:180-186](../backend/notify/runner.py#L180-L186) fires only when `baselined_at is
None`), so stopping the bot across the recompute would have **deferred** the messages, not
avoided them. The owner chose to let them go out under the valve.

### 6.18 ⛔ Four spoofing markers at once, and zero events — the detection gap a dead test sentinel exposed (2026-08-24)

Two tests hard-coded `999999999` as an MMSI that "cannot exist in the AIS feed: MID 999 is
unassigned". **The feed contains it**, and what it carries is the finding:

| Marker | Value |
|---|---|
| MMSI | `999999999` — **MID 999 is not ITU-assigned** |
| Speed | **100.8 kn** — impossible for a vessel |
| `nav_status` | **15** — "undefined" |
| Identity | name / IMO / callsign **all null** |
| Position | 34.673 N, 33.094 E (off Cyprus), 2026-08-18 |
| **Events raised for it** | **0** |

⇒ Four independent markers coincide on one transmitter and **neither `implausible_position`
nor `imo_integrity` raised anything.** The MMSI is well-formed as a *number*, its single fix
is self-consistent (no implausible jump to detect from one point), and `imo_integrity` keys on
IMO — which is null here — so each detector's own logic declines it individually. The hull
that trips four separate red flags is exactly the one the composite is for.

⚠ **This is a gap, not a bug**: no guard is malfunctioning. Closing it means a new rule
(un-assigned MID, or "impossible speed on a single fix with a null identity"), which is a
detection-policy decision and is **not** taken here.

⚠ The tests no longer assume any MMSI is impossible. `tests/conftest.py::unobserved_mmsi`
establishes absence from `vessel_states` + `vessel_identity` + the live snapshot — the same
three reads whose empty result *is* the 404 branch — so the sentinel is measured, not
regulated into existence.

### 6.19 The domain tabs' «قيد المراجعة» tile counts medium severity, and correcting it is a domain-tab change (2026-08-24)

On جوّي · بحري · فضائي the middle triage tile is labelled **«قيد المراجعة»** — a workflow
state — and shows **`sev.medium`**, the count of medium-SEVERITY alerts. A label promising one
axis, delivering a bucket of another. It reads `0` on a live screen carrying 21 alerts whenever
none of them happens to be medium.

**History, because the shape of the fix moved twice:**
* DASH-4a corrected it to the real triage count (`triage === "reviewing"`, migration 051) —
  on **every** surface, because `DomainOverlay` renders `CountryEvents` on all three domain
  tabs. That silently changed them.
* `96b7f29` scoped the correction behind the `hideList` prop the dashboard already passed, so
  the domain tabs kept their old value and the mislabel with it.
* **DASH-6a removed the prop, not the decision.** The dashboard owns `MonitorControls.tsx`
  now and counts the real state there; `CountryEvents` is the domain tabs' file again, so the
  branch had nothing left to choose between and the unconditional `sev.medium` **is exactly
  what those three tabs already displayed**. Zero change to them — G10 confirms it.

⛔ **WHY IT IS STILL OPEN.** Correcting the label or the value on those tabs **is** a
domain-tab change, which is the one thing DASH-6 exists to prevent, and it would fail G10 by
design. It is a real defect on three live surfaces and it is recorded rather than carried.

**The two honest fixes, both the owner's call:** relabel the tile on the domain tabs to what it
counts (e.g. «متوسّطة الخطورة»), or give those tabs the triage count too. The first is a string
change; the second means the domain tabs read migration 051. Either is a deliberate domain-tab
item with its own G10 bless — not something to slip into a dashboard commit.

### 6.21 ⛔ airplanes.live refuses `/v2/mil` with 403 — the military layer has been dark since ~2026-08-12 (found 2026-09-01)

Surfaced the moment `/military` gained its DB fallback (finding-11 fix): the ladder
reported `available=false, source=archive, age≈19.6 d` instead of rendering blank.
Measured: `journalctl --user -u scraplan-military` shows `403 Forbidden` on
`https://api.airplanes.live/v2/mil` on every retry; the collector is HEALTHY and
backing off correctly (30→300 s) — **the upstream is refusing, a restart fixes
nothing**. `opensky_output/military.json` mtime = 2026-08-12 16:05.

**The owner's call, two honest options:** request API access from airplanes.live
(they moved `/v2/mil` behind approval), or retire the layer and say so in the UI.
⛔ Not an option: disguising the client to slip past the block.

### 6.20 ✅ WINSTORE — a third physical copy, 90 days deep, and what building it exposed (2026-08-25)

A **Windows** machine on the LAN now holds the deep-retention tier. This is the third
destination and the first that is not Linux, so almost none of the serverq path transferred.

**The destination, measured** (full detail and commands in [`docs/ENVIRONMENT.md`](ENVIRONMENT.md) §3):
`scraplanbak@10.0.0.113`, Windows 11 Pro build 26200, `OpenSSH_for_Windows_9.5`. `F:` is a
**7,452 GB NTFS volume with 6,964 GB free** — but it is **disk 0, a single `ST8000DM004` SATA
HDD with no RAID**. It is a third *copy*; it is not a safe place by itself. It never sleeps
(`Sleep after` AC = `0x00000000`), so the nightly window is not a gamble.

**Sustained throughput 11.0 MB/s** over the real 3.83 GB dump — the same 100 Mb/s ceiling
serverq sits behind. ⚠ Both NICs are faster than the link: this host advertises
`1000baseT/Full` and the destination has an **Intel I226-V (2.5 GbE)**, yet the link partner
advertises no more than `100baseT/Full`. **The cable or the switch port is the limit, not
either machine** — the single highest-leverage change available to every transfer in this
project.

**What was built:** [`scripts/backup_windows.sh`](../scripts/backup_windows.sh) (nightly,
04:45, keep 90), [`scripts/backfill_windows.sh`](../scripts/backfill_windows.sh) (re-seed
depth), [`scripts/archive_to_windows.sh`](../scripts/archive_to_windows.sh) (one-shot cold
archive relocation). All three carry the `backup_offsite.sh` contract verbatim: sha256 →
transfer → sha256 **read back from the destination** → compare → only then promote, and
retention runs last.

**⛔ The nightly unit takes NO pg_dump.** It copies the artifact the 03:00 unit already wrote.
Three dumps a night would cost ~4 min of DB load and 3.83 GB of writes to produce bytes that
already exist — and, more importantly, three independent dumps have three different
checksums, so the three tiers' manifests could never be compared. One artifact means **one
sha256 identifies tonight's backup wherever it lives**. The cost is a real dependency: if the
03:00 unit produced nothing, this unit fails loudly rather than substituting a dump of its own.

**Local retention cut 14 days → 4** ([`scripts/backup_db.sh`](../scripts/backup_db.sh)).
That tier is the *fast* restore and shares a volume with the database, so depth there is disk
pressure bought for a capability another tier provides better. Recoverable history did not
shrink, it grew: **14 days on one dying volume became 4 local + 7 on serverq + 90 here.**

---

#### ⛔ What the tier-by-tier comparison exposed — three dates had ONE copy in the world

Before cutting local retention, every date was compared across all three tiers. The result was
not what the retention depths implied:

| date | local | serverq | windows (after backfill) |
|---|---|---|---|
| 08-14 | yes | **NO** | ✅ pushed |
| 08-15 | yes | **NO** | ✅ pushed |
| 08-16 | yes | yes | ✅ pushed |
| 08-17 | yes | yes | ✅ pushed |
| 08-18 | yes | **ONLY AN UNPROMOTED `.part`** | ✅ pushed |
| 08-19 | yes | yes | ✅ pushed |
| 08-20 | yes | yes | ✅ pushed |

**`opensky-2026-08-18.sql.gz.part` is still sitting on serverq.** The `.part` discipline
worked exactly as designed — a partial transfer was never promoted to a name that looks
restorable — but **nothing ever went back for it**, so 08-18 quietly had a single copy for a
week. 08-14 and 08-15 simply predate serverq's 7-day window. ⚠ **Had local retention been cut
to 4 days before the backfill, those three dates would have been destroyed**, by a change whose
stated purpose was to make history safer. The cut was held until the backfill completed.

✅ **The backfill ran before the cut, and completed clean: 10 pushed, 1 already present,
0 failed**, in 71 min (11:43 → 12:54 UTC). The store now holds **11 dumps + the cold archive
= 43.46 GB** against 6,923.8 GB still free, with no `.part` left behind. The three
single-copy dates now have two copies each.

⚠ **08-24 is missing everywhere** — `opensky-2026-08-24.sql.gz.101802.failed` (20 bytes) is
the local marker. Same shape as the four July dates in §7 defect 3: the gates make a *future*
silent failure impossible, they do not explain a past one. Unexplained, not guessed.

---

#### ⛔ Four Windows traps, and one that reads like a failure but is not

Recorded in full with evidence in [`docs/ENVIRONMENT.md`](ENVIRONMENT.md) §3. In brief:
`ssh host 'cat > file'` **does not transfer** there (135 KB in 300 s — `cmd.exe` is the
default shell and PowerShell's stdin is a text stream; `scp` moves the same 300 MB in 27.1 s);
`cmd.exe` splits commands at quotes and pipes, so every remote command is
`-EncodedCommand` base64 UTF-16LE; a killed transfer leaves the file **locked**, not merely
partial; and PowerShell errors return as **CLIXML on stderr** with an empty stdout and exit 0.

⚠ **And the one that is not a failure:** NTFS does not update a directory entry's size while
a file is open, so a `.part` reads **0 bytes throughout a transfer that is running perfectly**.
Measured mid-flight: the destination said `0` while this host's `/proc/<ssh>/io` showed 1.65 GB
sent at 11 MB/s. **A zero-byte `.part` at a Windows destination is not evidence of a stall.**

---

#### The free-space gate caught its own bad measurement — which is the point

The gate was first written with `(Get-PSDrive F).Free`. As a non-administrator that returns an
**empty string with exit status 0** — as do `Get-CimInstance Win32_LogicalDisk` and
`Get-Volume`, both of which need elevation. The very first end-to-end run transferred **nothing**
and said why: *"free space at scraplanbak@10.0.0.113 came back non-numeric ('') — refusing to
transfer"*. `New-Object System.IO.DriveInfo 'F:'` works and is now what the gate uses.

**The guard was right and the measurement was wrong, and that is the correct order.** A guard
that failed *open* would have transferred happily and reported that it had checked — the exact
shape §6.8 and the `backup_offsite.sh` free-space bug already cost this project twice.

---

#### The nightly index does not re-hash the store, and that was measured, not assumed

`CURRENT.sha256` was first written the obvious way — `Get-FileHash` over every file at the
destination. ⛔ **At this tier's target depth that is ~345 GB read off a spinning disk every
night**, roughly 40 minutes of re-deriving numbers each file's own promote step had already
computed *and verified*. `backup_offsite.sh` can afford it at 7 dumps (~25 GB); at 90 it stops
being affordable. Both scripts now build the index from `MANIFEST.sha256` instead.

✅ **And the equivalence was proven, not argued.** The backfill's final pass happened to run
the old re-hashing version over the freshly filled store, so there is an independent
recomputation of ~40 GB to compare against the recorded hashes: **12 of 12 agree, 0 differ.**

⚠ It also exposed a real bug in the naive version worth naming: its filter was
`-like '*.gz' -or -like '*.md'`, which **silently omitted `archive/csv_exports.zip`** — the
index under-reported what the store held. Keying off the manifest removes the filename pattern
that has to be kept in sync with reality.

⚠ **Be precise about what the index therefore does NOT prove.** Recorded hashes prove a file
is still *present*; they do not re-prove its bytes are intact on disk. That is a **scrub**, it
is a different job on a different schedule, and it does not exist yet. Until it does,
`sha256sum -c CURRENT.sha256` at the destination is the scrub and it is on demand.

---

#### Security — the account this runs as, and the one it deliberately does not

⛔ **The login offered for this machine was `A001`, which is a LOCAL ADMINISTRATOR
(`IsInRole(544) = True`) with password authentication enabled and a weak password.** Storing
that credential for a timer would have put full administrative control of a machine holding
several unrelated archives (`DAANES-Archive`, `PYD-Archive`, `RojavaTV-Archive`, face-recognition
and panos datasets) into a file on this host.

So the timer authenticates as **`scraplanbak`**, created for it:

* **Not an administrator** — proven by enumerating the Administrators group, not by assuming.
* **Key-only** — `PasswordAuthentication no` *and* `KbdInteractiveAuthentication no`, because
  on Windows keyboard-interactive is a second route to the same password check and the first
  line alone is cosmetic. Proven by contrast: `scraplanbak` offers `publickey`, `A001` offers
  `publickey,password,keyboard-interactive`.
* **Confined to `F:\scraplan`** — `F:` granted `Authenticated Users: Modify` at the root and
  inherited it everywhere, so the new account could initially write to every project on the
  disk. An inherit-only Deny ACE now blocks it, with `F:\scraplan`'s ACL protected from
  inheritance so it keeps access. **Proven by attempting a write in every directory on the
  volume** — all blocked, `F:\scraplan` allowed.

⚠ **`A001` itself is unchanged and still reachable with its password.** That is the owner's
machine access, and disabling it is the owner's call, not a side effect of a backup change —
but it is the largest single exposure on that LAN and it is recorded here rather than left
unsaid.

---

#### ⚠ What is proven, and what is NOT

**Proven, each by detonation rather than assertion:**

* **The bytes arrive intact** — destination-computed sha256 matches the source on every one
  of 12 transfers.
* **The bytes COME BACK** — `db/opensky-2026-08-18.sql.gz` (one of the three dates that had a
  single copy in the world) was pulled back off `F:` in 5 min 16 s: **3,683,580,763 B,
  sha256 `cec99ada…` matching the manifest, `gzip -t` clean, and it decompresses to a real
  `PostgreSQL database dump … Dumped by pg_dump version 17.10`.** A store you have never read
  from is a store you have never tested.
* **The account is confined** — a write was attempted in every directory on the volume;
  all blocked, `F:\scraplan` allowed.
* **The unit works as systemd runs it, not just as a shell runs it** — `systemctl --user start
  scraplan-windows.service` → `Result=success`, `ExecMainStatus=0`, full journal trace, 6 min
  38 s, with no ssh-agent available. This is the §6.4 lesson applied rather than re-learned.
* **The guards fail closed** — the first run refused to transfer on a bad free-space
  measurement instead of proceeding.

⚠ **The unit peaks at 3.3 GB RSS** (`Consumed 1min 14.068s CPU time, 3.3G memory peak`),
almost all of it page cache from reading the 3.83 GB dump. It runs at 04:45, and memory is
this host's tightest resource (§6.10) — worth knowing before anything else is scheduled near
that slot.

⛔ **STILL NOT proven: a database rebuilt from THIS tier.** The retrieval proof above stops at
"the artifact is intact and readable". It does not run
[`docs/RESTORE_PROCEDURE.md`](RESTORE_PROCEDURE.md) end to end. The gap is narrow — the file is
byte-identical to the class RESTORE-TEST already validated, and
`READ_THIS_BEFORE_RESTORING.md` travels with the dumps — but ⚠ the naive restore path silently
destroys data (§6.8), so the distinction is not a formality and is not being blurred here.

## 7. Storage — resolved at source (DISK-2), with the history preserved

**One ext4 filesystem holds everything** — root, `/home`, the Docker volume with the
database, and every JSONL archive. There is no separate data volume.

### ⚠ GROUND TRUTH — six rotated archives are labelled 07-28 but contain 07-29 data

`rotate_archive.sh` stamps a rotated log with **yesterday's** date, because it is designed to
run at 00:05. It was executed **mid-day on 2026-07-29** while verifying the step-9 change
(reported there), so these six files carry a `2026-07-28` label while their contents run from
07-28 up to their actual rotation time on **07-29**:

| File | Rotated at (mtime) | Size |
|---|---|---|
| `opensky_output/states-2026-07-28-101940.jsonl.gz` | 2026-07-29 10:18 | 8,306,107 B |
| `opensky_output/military-2026-07-28-101941.jsonl.gz` | 2026-07-29 10:19 | 290,046 B |
| `maritime_output/ais-signals-2026-07-28-101941.jsonl.gz` | 2026-07-29 10:19 | 1,442,372 B |
| `maritime_output/signals-2026-07-28-101941.jsonl.gz` | 2026-07-29 02:44 | 152 B |
| `satellite_output/tle-2026-07-28-101941.jsonl.gz` | 2026-07-29 06:20 | 58,963 B |
| `satellite_output/signals-2026-07-28-101941.jsonl.gz` | 2026-07-29 06:20 | 183 B |

The `-101940`/`-101941` suffix is the script's own collision guard (a `2026-07-28` file already
existed), so **the rotation time is recoverable from the filename** and each file's `mtime` is
authoritative. **Nothing is lost or corrupted** — every one passes `gzip -t`, and per-record
timestamps inside are unaffected. **The filename date is the only unreliable field; use `mtime`
or the record timestamps.**

⚠ **This note could not be added to the plandata-side `MANIFEST.sha256` or its
`.provenance.txt`,** as those live in `~/Desktop/plandata`, which is outside this window's
ownership for this task. It is recorded here instead — and the affected files are scraplan's own
archives, not the plandata era file, so this is their correct home. The era file's own manifest
and provenance are accurate and unaffected.

### ✅ GROUND TRUTH — the ADS-B frame narrowed at 2026-07-29 13:55:36 UTC

> **The upstream sampling frame is narrowed as of `2026-07-29 13:55:36+00`. Everything
> archived BEFORE that instant is GLOBAL; everything after is REGIONAL — records INSIDE the
> seven AOI countries or NEAR their borders (`--area-buffer-km`, default 50 km) only.**
>
> A study that spans that instant spans **two different sampling frames** and must say so.
> This is a **frame change, not a deletion**: the global era is preserved whole and
> checksum-verified as
> `plandata/data_p1/state_vectors_pre-narrowing_2026-07-29.jsonl.gz` — **67,891,995 lines,
> 45,928,149,501 bytes uncompressed, 3,475,344,889 compressed**, with
> `MANIFEST.sha256` and a `.provenance.txt` beside it. The same principle the satellite and
> maritime layers already state: *sampling frame, not a census.*

### The maintenance window — before / after

| Measure | Before (13:18–13:49) | After (14:15–14:46) | Change |
|---|---|---|---|
| Free space | **56.29 GB** (89 % used) | **98.62 GB** (80 % used) | **+42.33 GB** |
| ADS-B archive growth | **9.04 GB/day** | **0.404 GB/day** | **22.4× less** |
| `opensky` DB growth | **5.36 GB/day** | **0.515 GB/day** | **10.4× less** |
| **Project-attributable growth** | **14.40 GB/day** | **0.92 GB/day** | **15.7× less** |
| Net consumption (`avail`) | −17.53 GB/day | **+2.56 GB/day (net freeing)** | no exhaustion at this rate |
| Runway | 3.21 days (77 h) | **none at this rate** | — |
| Live archive size | 45.89 GB | **13.4 MB** | truncated after verification |
| Out-of-AOI in the live archive | 96.65 % `country IS NULL` | **0.00 % out-of-frame** | inverted |

**Sequence, as executed** (each step verified before the next):

1. **13:56:24 / 13:56:25** — stopped `scraplan-adsb-ingest`, then `adsb-scraper-collector`.
   No `adsb_scraper` or `ingest.py` process remained.
2. **Re-measured at the stop** (not reused from DISK-1): `offset == size == 45,928,149,501`
   → **lag 0 bytes**, inode 945938 matching, and the file's tail `ts`
   **2026-07-29T13:55:36+00:00** equal to the DB's `max(ts)` for `source='adsbexchange'`
   to the second. So the cut point was the end of the file, and the whole archive was
   already committed to the database.
3. **Compressed and verified before removing anything** — `pigz -6`, 244 s to write plus
   80 s to verify:
   - `sha256` of the source bytes = `sha256` of the **decompressed stream** =
     `0090e60095bdcb5651b705cf342289ec92a346ef0ea687c85cc87657fe76e2c6`
   - line counts equal: **67,891,995 = 67,891,995**
   - `.gz` recorded in `MANIFEST.sha256` as
     `77a8d21547e2f53014f99d5e6a61638e99367df77d3edb3e794fa3d91a726ac5`
4. **Only then removed** — truncated in place (`: >`), which is safe precisely because the
   writer was stopped so no `O_APPEND` handle existed. **inode 945938 preserved**, so the
   tailer's own truncation detection (`ingest.py:477-486`) handled the reset.
   `avail` 52.70 → 98.63 GB.
5. **14:04:08 / 14:04:11** — restarted collector then ingest (reverse order). Collection gap:
   **~7 min 45 s**, which upstream records as an observational gap without reacting
   (`orchestrator.py:163`).
6. **Verified within 10 minutes** — records flowing, `country` tagged, lag back to 0,
   `max(ts)` advancing past the cut.

### ✅ The frame holds — measured on both sides, no leakage

| Check | Result |
|---|---|
| Live archive, last 10,000 lines | **INSIDE 7,003 (70.03 %) + NEAR 2,997 (29.97 %) = 100.00 % in-frame**; **OUTSIDE = 0** |
| Database, 20,205 rows since restart | `country IS NULL` 6,189 — **all 6,189 carry `near_country`**; **NEITHER = 0** |
| Countries seen INSIDE | Turkey 5,148 · Israel 944 · Jordan 313 · Cyprus 312 · Syria 237 · Iraq 27 · Lebanon 22 |

⚠ **A DISK-1 proxy is corrected here.** DISK-1 used `distance_km > 1000` (94.53 %) as an
out-of-AOI proxy. `distance_km` is measured **from the fetch-envelope centre**
(`validation.py:197-199`), **not** from the AOI border — so 181 of the post-narrowing records
are >1000 km yet fully **INSIDE Turkey**, which is ~1,600 km wide. The correct membership test
is `country` (INSIDE) / `near_country` (NEAR) / both NULL (OUTSIDE), and by that test the frame
is exact. Directionally DISK-1 was right; as a membership metric it was not.

### ✅ The non-archive consumer, identified (DISK-1 left it unattributed)

DISK-1 measured ~6.7 GB/day it could not attribute. Measured over 30 minutes before the
window, per-consumer:

| Consumer | Rate | Command |
|---|---|---|
| ADS-B archive | **+9.04 GB/day** | `stat -c %s` |
| **`opensky` database (logical)** | **+5.36 GB/day** | `pg_database_size()` |
| `snap` (a transient refresh) | +2.66 GB/day | `du -sb` |
| `/var/log` | +0.04 GB/day | `du -sb` |
| `~/scraplan-backups` · `.cache/pip` · `.npm` · docker images · journals | **0.00 — flat** | `du -sb`, `docker system df` |
| **Net measured** | **−17.53 GB/day** | `df -B1` |

Attributed sum 17.10 vs 17.53 measured — closes to **2.5 %**. **It was the database**, growing
in step with the archive it mirrors (Docker volume 17.62 → 17.73 GB tracks the same bytes —
do not count it twice). Both fell together after narrowing, which confirms the attribution.

| Fact | Measured |
|---|---|
| Device | `/dev/nvme0n1p2` on `/`, **ext4** (`findmnt --real`) |
| Size / used / **available** | 501,811,015,680 B / 419,479,441,408 B / **56,765,706,240 B (~56.77 GB) — 89 % full** *(`df -B1 /`, 2026-07-29 13:05Z)* |
| Same FS | `df / /home /var/lib/docker` all resolve to `/dev/nvme0n1p2` |
| Only real block device | `nvme0n1` — `p1` 1 GB vfat `/boot/efi` + `p2` 467.3 G ext4 `/`. All 50+ `loopN` are read-only squashfs snaps. `findmnt --real` shows no other mount. |
| Superseded readings | **12,699,459,584 B / 98 %** at 10:00Z, then **40,148,586,496 B / 92 %** at 12:20Z — both raised by external deletions, see the attribution below |

### Measured growth — three samples over 30 minutes (supersedes the 120 s sample)

**Method** — `df -B1 --output=avail /` plus `stat -c %s` and `du -B1` on
`/home/gis-syr/Desktop/plandata/data_p1/state_vectors.jsonl`, at t = 0 / 15 / 30 min:

```
t=11:50:47Z  avail 40,463,364,096   archive 45,157,908,744
t=12:05:47Z  avail 40,334,553,088   archive 45,242,129,230
t=12:20:48Z  avail 40,148,586,496   archive 45,351,688,185
```

| Window | Archive growth | Net `avail` change | Usable? |
|---|---|---|---|
| 0 → 15 min (900 s) | 8.09 GB/day | −12.37 GB/day | ✔ |
| **0 → 30 min (1801 s)** | **9.30 GB/day** | **−15.10 GB/day** | ✔ |
| 15 → 30 min (901 s) | 10.51 GB/day | −17.83 GB/day | ✔ |

⚠ **The previously recorded 8.94 GB/day was low by 1.7×.** A 120-second sample is not long
enough — sub-windows disagree by 44 % among themselves, so **never derive a runway from one
short sample.**

### ⚠ A second 30-minute pass, and why `avail` is not a consumption metric here

Repeated at **12:32 / 12:47 / 13:02 UTC**, same method:

```
t=12:32:43Z  avail 40,019,496,960   archive 45,426,030,909
t=12:47:44Z  avail 56,944,349,184   archive 45,515,067,276     <-- avail ROSE 16.92 GB
t=13:02:44Z  avail 56,767,328,256   archive 45,621,967,461
```

| Window | Archive growth *(monotonic — reliable)* | `avail` change *(contaminated)* |
|---|---|---|
| 12:32 → 12:47 | **8.54 GB/day** | **+1,623 GB/day** ← an external deletion, not a rate |
| 12:47 → 13:02 | **10.26 GB/day** | −16.99 GB/day |
| **Full window** | **9.40 GB/day** | net **+16.75 GB** — *not a consumption rate* |

⚠ **Methodological finding: while another agent is deleting files, an `avail`-derived rate is
invalid.** Use the two independent series separately:

- **Archive growth is monotonic and trustworthy: 9.30–9.40 GB/day** across both passes
  (8.09 / 8.54 / 9.30 / 9.40 / 10.26 / 10.51 across all sub-windows).
- **Consumption** is only readable from a window with no external free. The cleanest such
  window is **12:47 → 13:02: −16.99 GB/day**, of which the archive is 10.26 — so
  **~6.7 GB/day comes from elsewhere** (the DB grew 14 → 15 GB in ~2 h, plus WAL, the nightly
  2.8 GB dump, 2.8 GB of `/var/log`).

**State at 13:02 UTC — the pre-DISK-2 baseline: 56.77 GB free, 89 % used, ~17 GB/day, ≈ 3.3
days.** ⚠ That headroom was **not engineered** (see the attribution below), which is why it was
treated as borrowed and the fix was made at source instead. Superseded by the DISK-2 result at
the top of this section; kept as the before-figure.

### ✅ Anomaly attributed by measurement — 27.75 GB then a further 16.9 GB were freed externally

Two separate external frees happened during the session, and **nothing in this session deleted
a byte**:

| When | `avail` before → after | Freed | Attribution |
|---|---|---|---|
| 10:00 → 11:50 UTC | 12.70 → 40.46 GB | **+27.76 GB** | ✅ **Manual deletion by the owner** — confirmed by the owner 2026-07-29. Measurement alone could not attribute it (no before/after `du` existed for the candidates, and `docker system df` showed no prune), so it was recorded as unresolved until confirmed. |
| 12:32 → 12:47 UTC | 40.02 → 56.94 GB | **+16.92 GB** | ✅ **Manual deletion by the owner, including `~/Qwen3.5-4B`** — confirmed by the owner 2026-07-29, and independently measured: the directory was **16 GB** at 12:10 UTC, `ls` now returns *No such file or directory*, and `/home/gis-syr` fell **308 → 292 GB (−16 GB)**, matching the 16.92 GB gain. |

**Both frees were the owner clearing space by hand — not a process, not a scheduled job, and
not this session.** That closes the anomaly, and it does not change the engineering conclusion:
manual deletion is a one-off that leaves the underlying growth rate untouched, which is why the
fix was made at source (below) rather than treated as headroom.

For the second free, every other candidate was re-measured and **none changed**: `docker
system df` identical (23.37 GB images, 14.98 GB reclaimable, 4.443 GB build cache, 50 dangling),
`.cache/pip` 17 GB, `.npm` 9.1 GB, `snap` 25 GB, `scraplan-backups` 14 GB. That isolation is
what makes the attribution a measurement rather than a guess.

**Consequence for planning:** the current 56.77 GB came from deleting an unrelated 16 GB model
directory and an unexplained 27.75 GB. Neither is a structural fix, both are one-off, and the
underlying **9.4 GB/day archive growth is untouched**. The rescue is upstream narrowing
(§7 "Upstream — where the growth actually comes from"), not headroom that appeared.

### What occupies it

Measured `du -sh` / `du -sb`, 2026-07-29 ~11:55 UTC:

| Item | Size | Trend |
|---|---|---|
| `/home/gis-syr/Desktop/plandata` — incl. `data_p1/state_vectors.jsonl` at **45.35 GB** | **43 GB** | ↑ 9.30 GB/day |
| **`~/scraplan-backups` — the nightly DB dumps** | **14 GB** (14,748,233,492 B) | ↑ ~2.8 GB/night |
| `opensky` database (logical, in the Docker volume) | 15 GB | ↑ (was 14 GB at 10:00) |
| `/var/log` | 2.8 GB | — |
| `opensky_output/` — rotated `.gz`, never pruned | 351 MB | ↑ |
| `maritime_output/` — incl. AIS ground truth `ais-signals.jsonl` | 132 MB | ↑ |
| `enrichment_data/` 100 MB · `satellite_output/` 2.7 MB | — | — |

⚠ **`~/scraplan-backups` was missing from this table and is the second-largest consumer at
14 GB** — larger than the database it backs up.

### ⚠ The backup layer, measured (it was an unmeasured gap)

`scraplan-backup.service` is `Type=oneshot` running `scripts/backup_db.sh`:

| Fact | Measured |
|---|---|
| Output path | **`$HOME/scraplan-backups/opensky-<date>.sql.gz`** — `pg_dump \| gzip`, written `.tmp` then `mv` |
| Same volume as the DB? | **Yes — `/home/gis-syr/…` is on `/dev/nvme0n1p2`**, the same filesystem as the Docker volume it dumps. A volume-loss event takes the DB *and* every backup with it. |
| Retention | **`find … -name 'opensky-*.sql.gz' -mtime +14 -delete`** — 14 days, so retention **exists** but bounds the directory at ~14 nightly dumps |
| Files present | **11** |
| Total size | **14,748,233,492 B (14 GB)** |
| Per-dump size | growing fast: **169 MB** (07-16) → 495 MB (07-19) → 1.34 GB (07-21) → 2.37 GB (07-26) → **2.84 GB** (07-29) |
| Last successful run | **2026-07-29 03:03:43 EDT** — journal: `backup written: …opensky-2026-07-29.sql.gz (2.7G)`, unit `Finished`, 3 min 44 s CPU, 2.7 GB memory peak |

**Four measured defects — two now fixed (DISK-2 step 8), two open:**

1. ✅ **FIXED (BACKUP-EXT, 2026-08-01) — the backups no longer live only on the volume they
   protect.** They still *also* live there, which is correct — the local series is the fast
   restore path. What changed is that a **checksum-verified copy now exists on a second
   physical machine** (`serverq@10.10.20.57`), written nightly at 04:15 by
   [`scripts/backup_offsite.sh`](../scripts/backup_offsite.sh). A volume-loss event on this
   host no longer takes every copy with it. **See §6.8** for the measured destination, rate,
   retention and manifest layout — and for the one thing still unproven, the restore.
2. ✅ **FIXED — `opensky-2026-07-15.sql.gz` was 20 bytes**, a failed dump left in place looking
   valid. Root cause: `pg_dump | gzip` loses `pg_dump`'s exit status to the pipe, and gzip
   succeeds on an empty stream, so `set -eu` and the `.tmp`→`mv` guard could not catch it.
   `backup_db.sh` now sets **`pipefail`** (verified supported by this host's `/bin/sh`, dash),
   enforces a **minimum output size** (`BACKUP_MIN_BYTES`, default 100 MB against real dumps of
   ~2.84 GB) for the case where a dump exits zero yet produces nothing, and runs **`gzip -t`**
   so a valid-length but corrupt member is rejected too. On failure the artifact is renamed
   **`*.failed`** — flag, never delete: deleting erases the evidence a night was missed, and
   keeping the real name makes a broken file look restorable. The 07-15 artifact was
   retroactively renamed to `.sql.gz.failed` (20 bytes preserved). All four paths tested with a
   stubbed `docker`/`gzip` against a temporary `HOME`.
3. ⚠ **OPEN — four dates are missing** (07-17, 07-23, 07-24, 07-28), so **10 usable dumps cover
   15 days**. Those are missed runs, not deletions. The new gates make a *future* silent failure
   impossible, but they do not explain the historical gaps — unexplained, not guessed.
   *(Note: retention matches only `*.sql.gz`, so `.failed` markers persist rather than being
   reaped. Intended, and they are tiny.)*
4. ⚠ **OPEN — the 07-29 dump ran twice** — 02:44:07 (boot catch-up, `Persistent=true`) wrote
   2.8 GB, then 03:00:08 wrote 2.7 GB over the *same filename*. ~8 min of CPU and ~2.8 GB
   written twice for one retained artifact.

### Transfer destinations — inventory only (the move decision is the owner's)

Measured with `lsblk`, `/proc/partitions`, `/sys/block`, `findmnt --real`, `journalctl -k`,
`tailscale status`:

| Candidate | Measured state |
|---|---|
| Block devices | **Only `nvme0n1`** — `nvme0n1p1` (1 GB vfat `/boot/efi`) + `nvme0n1p2` (467.3 G ext4 `/`). `/proc/partitions` and `/sys/block` show no other real disk. |
| Loop devices | 50+ `/dev/loopN`, all **read-only squashfs** snap mounts, **0 available** — not writable targets. |
| Other mount points | **None.** `findmnt --real` lists only `/` and `/boot/efi` (1 G) besides the snap mounts. |
| Removable storage | ⚠ **A USB disk `sda` was attached `02:44:01` and removed `06:28:52`** (`Synchronizing SCSI cache` → `Synchronize Cache(10) failed`, an unclean pull). **It is not present now**: no `/dev/sd*`, 0 `sd*` in `/proc/partitions`, `/sys/block/sda` absent. So a removable disk **exists in the owner's possession** but is not connected. |
| The 05:13 USB event | **Not storage** — `idVendor=258a idProduct=003a`, "Gaming KB" (SINO WEALTH keyboard). |
| Tailscale | Interface up (`100.79.72.78`), **1 peer only: `aws`, OS `android`, `Online: false`, `ExitNodeOption: false`.** No reachable second host. |

⚠ **There is currently no transfer destination** — no second volume, no mounted removable
disk, no reachable peer. **Nothing was moved, mounted, or copied; this is an inventory.**

### ✅ Cutting the live 45.9 GB archive — mechanism chosen and executed (DISK-1 step 7 → DISK-2 step 5)

Traced read-only on both sides of the file:

| Side | Mechanism | Reference |
|---|---|---|
| **Writer** (`adsb_scraper`) | Holds **one handle open in append mode** — `open(self._path, "a")` — for the process lifetime, closed only in `aclose()` | `adsb_scraper/persistence.py:109` |
| **Reader** (`scraplan-adsb-ingest`) | Tails by **(inode, byte offset)**, persisted in `<state-dir>/.ingest_state.json` under **scraplan's own tree**, explicitly so the foreign dir's rotation "must not be able to drop our offset" | `ingest.py:31, 444-464` |
| **Truncation handling** | Detects **both** inode change **and** `st_size < offset`, logs *"Archive replaced or truncated — rescanning from start (idempotent, no duplicates possible)"*, resets offset to 0 | `ingest.py:477-486` |

**Consumer is exactly caught up right now** — verified before proposing anything:

```
offset = 45,477,698,550   size = 45,477,698,550   lag = 0 bytes   inode 945938 (matches)
DB max(ts) for adsbexchange = 2026-07-29 12:39:48+00
archive tail ts             = 2026-07-29T12:39:48+00:00   (identical)
```

**Why a bare `mv` is wrong:** the writer's handle follows the **inode**, so after a rename new
observations continue landing in the *rotated* file while the live path stays empty. This is why
`rotate()` is not simply pointed at the archive.

| Option | Works because | Risk |
|---|---|---|
| **`copytruncate`** | The handle is `O_APPEND`, so after truncation the next write lands at offset 0 — no sparse hole. The reader detects `size < offset` and rescans the (now tiny) file. | ⚠ **Lossy race**: bytes appended between the copy finishing and the truncate are destroyed. Bounded but non-zero at ~110 KB/s. |
| **Coordinated stop (recommended)** | Stop the writer → wait for `offset == size` → `mv` + `gzip` → start the writer, which creates a fresh file with a **new inode** → reader resets to 0 and continues. | **Lossless.** Costs a short collection gap — and upstream already *records* gaps as observational facts without reacting (`orchestrator.py:163`, `self._gap_detector.observe`). |

**What would be cut vs kept:** the rotated `.gz` keeps the entire global history as ground
truth (the backfill paths read `.gz` transparently); the live file restarts empty. Nothing is
deleted, and the DB is unaffected because every row is already committed at `lag = 0`.

**Verification steps for the run:** (1) `lag == 0` before stopping; (2) `sha256sum` the rotated
file before and after `gzip -t`; (3) after restart, confirm a new inode in
`.ingest_state.json` and `offset` advancing; (4) confirm `max(ts)` in the DB continues past the
rotation instant.

✅ **The coordinated stop was chosen and executed** (DISK-2 step 5, sequence and checksums at
the top of this section). It was lossless: `lag` was **0 bytes** at the stop, so the cut point
was the end of the file and nothing un-ingested existed to lose. Truncating in place preserved
inode 945938, and the tailer's own truncation detection handled the reset — no state file was
hand-edited.

⚠ **`ROTATE_STATE_VECTORS` still defaults to 0, deliberately.** The one-off historical cut is
done; what remains disabled is *recurring nightly rotation* of the live ADS-B file. It is no
longer needed — at **0.404 GB/day** the file takes months to reach a size worth rotating — and
enabling it inside the nightly script would perform a bare `mv` while the writer holds an
`O_APPEND` handle, which is exactly the failure the deferral exists to prevent. Turn it on only
with a coordinated-stop wrapper, not on its own.

### ⚠ Two structural gaps in rotation

`scripts/rotate_archive.sh` (daily 00:05) rotates **six** logs, all under the scraplan tree:
`opensky_output/{states,military}.jsonl`, `maritime_output/{ais-signals,signals}.jsonl`,
`satellite_output/{tle,signals}.jsonl`.

1. **It does not cover `state_vectors.jsonl`** — the 45.9 GB file that was growing at **9.04
   GB/day**, i.e. **~63 % of all consumption**, written by `adsb-scraper-collector` into
   `/home/gis-syr/Desktop/plandata/` which is **outside `$BASE`**. Nothing rotated or pruned it.
   ✅ **Addressed at source instead** (DISK-2): the growth is now 0.404 GB/day, so rotating it
   is no longer the lever. The historical cut is done, and the resulting era file **is** now in
   the off-volume migration list (`STATE_VECTORS_HISTORY`, DISK-2 step 9).
2. **Rotation gzips but never prunes.** Rotated `.gz` files are ground truth by design and
   accumulate indefinitely. Rotation lowers the *growth rate* of covered logs; it **frees no
   space**. ✅ **Addressed** by the off-volume archival layer below — which is built, guarded
   and verified, but idle until external media returns.

So the nightly rotation timer never rescued the volume, and the backup timer's 14-day retention
does not either: it bounds `~/scraplan-backups` at ~14 dumps, but at **2.84 GB and rising per
dump** that ceiling is itself ~40 GB. Both are now second-order — the growth was cut at source.

### The off-volume archival layer (`rotate_archive.sh`, DISK-1 step 8)

Rotation now ends by relocating `.gz` older than `ARCHIVE_AGE_HOURS` (default **48**) off this
volume, then deleting the local copy **only after the destination copy is read back and proves
byte-identical**:

> `sha256sum` source → `cp` → `sha256sum` the **destination file** → compare → delete source
> on exact match only. A mismatch keeps **both** copies and exits non-zero so systemd flags the
> unit. A permanent append-only `MANIFEST.sha256` at the destination makes the relocated
> archive self-verifying with `sha256sum -c`, without this machine.

**The destination must be a real mount that is not the root volume.** Both refusal paths were
tested against this host:

| Condition | Behaviour | Tested |
|---|---|---|
| Path resolves to no filesystem | warn, skip, **delete nothing** | ✔ refused |
| Path resolves to `/dev/nvme0n1p2` (a stale mount-point dir on root) | warn, skip, **delete nothing** | ✔ refused |

⚠ The second guard is the one that matters: `/media/gis-syr/` contains two **empty root-owned
`EXTERNAL` / `External` directories**, left when the USB disk was pulled (dir mtime
**06:28:52**, the exact second of the unclean removal). Without the check, "copying to the
external disk" would have written **into the very volume being relieved** — and then deleted
the local original.

⚠ **Live behaviour today is skip-and-warn**, because no external volume is mounted.

⚠ **And even when mounted, the rotated `.gz` were never the rescue.** They total **0.36 GB
across 77 files** — about **34 minutes** of the pre-DISK-2 burn rate. This is a correctness fix
to an archival path that previously freed nothing; the rescue was upstream (below).

✅ **What it will actually move, once media returns (DISK-2 step 9):** the pre-narrowing era
file — **3.48 GB in one object**, ten times the entire aged-`.gz` set — via
`STATE_VECTORS_HISTORY`, a glob so a future cut needs no script edit. It lives outside `$BASE`,
which exposed a real bug now fixed: `archive_one` derived its destination by stripping `$BASE`,
yielding an **absolute** path for external files and a broken
`$ARCHIVE_DEST//home/gis-syr/…` destination. External files now map to `adsb_history/`, keeping
manifest paths relative so `sha256sum -c` still works at the destination. Verified live: the
glob resolves to the real 3.48 GB file, and with no volume mounted the run still ends
*"off-volume archival SKIPPED … no .gz deleted"* with `data_p1` unchanged.

### ⚠ Upstream — where the growth actually comes from (the real lever)

Measured on the last **100,000** records of `state_vectors.jsonl` (`tail -n 100000 | python3`,
read-only) and on the DB:

| Measure | Archive tail (100 k records) | DB, last 24 h (3,351,326 rows) |
|---|---|---|
| Outside the seven-country AOI (`country IS NULL`) | **96,654 — 96.65 %** | **3,208,207 — 95.73 %** |
| `distance_km > 1000 km` | **94,535 — 94.53 %** | **3,101,955 — 92.56 %** |
| `distance_km ≤ 500 km` | 1,277 — 1.28 % | 57,870 — 1.73 % |
| `distance_km ≤ 100 km` | **23 — 0.02 %** | — |
| Max `distance_km` | **8,586 km** | — |

⚠ **There is no `outside_area` field.** The archive's keys are `country`, `near_country`,
`distance_km`, `geo_source`, … — the outside-AOI marker is `country IS NULL` (96.65 %), and
`distance_km > 1000` is 94.53 %. A prior figure of "95,321 of 100,000 tagged `outside_area`"
matches the **substance** (~95–97 % outside) but the tag itself does not exist; cite the field
that does.

**So ~95 % of both the 45.6 GB archive and the 14 GB `state_vectors` hypertable is global
traffic outside the area of interest.** `state_vectors` is **14 GB of the 15 GB database**
(`hypertable_detailed_size`); everything else is small (`vessel_states` 203 MB,
`maritime_events` 30 MB, `snapshots` 19 MB).

**Where the filter is, and why it does not apply** — traced read-only, with line references:

1. The scraper **already has** a working AOI: `AOI_COUNTRIES` is exactly the seven countries
   ([`adsb_scraper/constants.py:89`](file:///home/gis-syr/Desktop/plandata/adsb_scraper/constants.py)),
   `buffer_km` defaults to **50.0** (`config.py:63`), with CLI flags `--area-asset` and
   `--area-buffer-km` (`config.py:299-302`). `AreaOfInterest.classify()` returns
   INSIDE / NEAR / outside (`area.py:59, 99-107`), and `bounding_box()` is "the fetch
   envelope" (`area.py:113-114`) passed to the API strategies (`main.py:61, 65, 76`).
2. ⚠ **But the time-series archive is written before the filter runs.** In
   `orchestrator.py:186-196` the order is:

   ```
   # SECOND archive: the full per-cycle time-series (no dedup).
   await self._state_vector_sink.write_snapshot(validated, snapshot_time)   # <- writes EVERYTHING
   result = await self._accumulator.process(validated)                      # <- filter lives here
   out_of_area += result.skipped_out_of_area
   ```

   The `OUTSIDE_AREA` gate is in the **accumulator** (`accumulator.py:87-93`, which keeps NEAR
   records and counts `skipped_out_of_area`) — and the accumulator feeds the **ICAO-dedup
   discovery catalogue**, not the time-series. `distance_km` is carried on the record
   (`models.py:132`) and written unconditionally (`statevector.py:109`).
3. **`ingest.py` does not filter either.** It re-classifies every row with the shared
   classifier and writes `country`/`near_country`/`distance_km` (`ingest.py:187-195`) with no
   distance or area predicate — so the database swallows the global traffic too.

⇒ **The classification happens *after* the archive write, which is exactly why a scraper with a
correct AOI produces a 95 %-global archive.**

**Narrowing design, presented for sign-off (DISK-2) — not implemented:**

- **Gate the time-series sink** on the same `AreaClass` the accumulator already computes
  (INSIDE ∪ NEAR, i.e. the seven countries + the existing 50 km buffer), applied **before**
  `write_snapshot`. Expected effect from the measurements above: archive growth
  **9.4 → ~0.4 GB/day**, and the DB's dominant table shrinks by the same ~95 % going forward.
- **A documented effective date.** This is a change of **sampling frame, not a deletion** —
  the same principle the satellite and maritime layers already state ("*sampling frame, not a
  census*"). The accumulated global archive **stays untouched as history**; every study must
  cite the date from which the frame narrowed.
- **The buffer is already the right knob** (`--area-buffer-km`, default 50 km) — narrowing needs
  no new configuration surface, only the gate placement.

✅ **IMPLEMENTED in DISK-2** (owner-signed), exactly as designed above — and the predicted
effect was measured: archive growth **9.04 → 0.404 GB/day**, against a design estimate of
"~0.4 GB/day". The gate is the record's own `ValidationFlag.OUTSIDE_AREA` under the same
`cfg.area.persist_out_of_area` override the catalogue has always used, applied at the sink's
existing eligibility loop *before* the line is written — one policy moved, not a second policy
added. A test asserts the sink and the accumulator withhold the **same** record set, so they
cannot drift apart.

⚠ **Placement differs from the DISK-2 brief**, which named `orchestrator.py`. It is in
`statevector.py`'s `StateVectorSink` instead, because the orchestrator's own contract states
*"The orchestrator holds NO validation logic — it only delegates"*, the sink already owns an
eligibility loop with two gates and documents that ineligible observations are "skipped, never
written", and the accumulator still needs the **unfiltered** list to count
`skipped_out_of_area` — filtering in the orchestrator would have required maintaining two
lists. Effect is identical; the record is
[`deploy/adsb_scraper-aoi-gate.patch`](../deploy/adsb_scraper-aoi-gate.patch) (§6.7).

**The ICAO catalogue needed no change: it was already regional.** `accumulator.py:89` has always
dropped `OUTSIDE_AREA` records, with `persist_out_of_area` defaulting to `False`
(`config.py:64`, flag help: *"Persist records flagged OUTSIDE_AREA (default: flag but skip)"*).
It never served a global purpose — only the time-series did, by writing before that gate ran.

**Verification:** 6 new tests driven through the **real committed AOI asset** and the real
validator, using the repo's own three reference points — Damascus (INSIDE), a sea point ~34 km
off Lebanon (NEAR), Cairo (OUTSIDE but inside the fetch envelope). Suite **200 passed**, `ruff`
and `mypy` clean.

### ✅ An off-host copy is active since 2026-08-01 06:58:36 EDT (10:58:36 UTC)

**That timestamp is the first verified arrival**, not the deploy time: it is when
`sha256sum` read back from `serverq@10.10.20.57` matched the source for both objects. Before
it, every byte this project held was on `/dev/nvme0n1p2` and nowhere else.

| | |
|---|---|
| What is off-host now | the nightly DB dump (3,062,243,980 B) **and** the 3.48 GB pre-narrowing era file |
| Where | `serverq@10.10.20.57:~/scraplan-offsite/` — `db/` and `adsb_history/` |
| Cadence | `scraplan-offsite.timer`, 04:15 daily, `Persistent=true` |
| Proof at the destination | `sha256sum -c CURRENT.sha256` passes there, without this machine |
| Freshness marker | `~/scraplan-offsite/LAST_SUCCESS` (UTC) |

**This closes the last co-location risk in §6.8** — with one honest qualifier carried
forward from there: **the restore has never been tested.** The copy is proven to be
byte-identical, not proven to rebuild a database. Until that test is run, §7's storage story
is "the bytes survive the machine", which is not yet the same as "the database survives the
machine".

⚠ **The era file's off-volume story is now split between two mechanisms, and they are not
the same thing.** `rotate_archive.sh` would *relocate* it (delete the local original to free
space) and is still idle awaiting external media. `backup_offsite.sh` *copied* it and left
the local original in place. **The 3.48 GB is still occupying this volume** — the off-host
copy bought redundancy, not headroom. Do not read §6.8's closure as disk relief.

---

## 8. Working method — the rules this project runs on

Inherit these rather than rediscovering them. Each exists because departing from it produced
a measured error.

**Assessment before build.** Read and report, measure, *then* decide. A `-pre` assessment
precedes a build phase and is allowed to conclude "don't build this". SAR-pre is the
counter-example: it planned from unmeasured figures and its conclusions had to be voided.

**Shrink or defer on measured grounds rather than building what was asked.** Every closed
priority in §5 shipped smaller than proposed, each time citing a number: draught became a
latent guard at ~1/35 yield; `standoff_dwell` stayed annotation-only on a 2-of-28 cloaking
exposure; the ports layer refused composite admission because it would flip 96 hulls. A
shrink with a measurement behind it is a result, not a failure.

**Every figure read from source, never from a prompt.** Row counts from `psql`, intervals
from unit files, throughput from `curl`. When a prompt and a measurement disagree, the
measurement wins and the disagreement gets written down (this file flags eight).

**Flag, never delete; filter at consumption.** Malformed IMOs are recorded then flagged —
nulling at parse would erase "no IMO reported" vs "reported corrupt" and destroy the very
verdict that classifies them. Rows are never dropped to make a layer look clean.

**Blindness is distinguished from absence.** `insufficient_data` as a third state; "not
evidenced" rather than "not present"; durations published as **lower bounds** under a 45 %
outage; a silent port is coverage, never "no commerce"; A2's silence beyond ~40–60 NM is
outside the sampling frame, not evidence of no meeting.

**One concern per commit.** Documented exceptions are named as exceptions (`a1dde61` bundled
the GFW adapter with UI work, and that was recorded at GFW-pre rather than glossed).

**DB-level guarantees over application logic.** CHECK constraints, unique indexes,
checksum-verified run-once migrations, `Persistent=true` on timers — the invariant lives where
it cannot be bypassed.

**No temporary solutions.** A stub is a *documented* stub with a stated deferral (GFW's
`get_track`), not a silent placeholder.

**Per-fact provenance and per-source vintage.** Assets are dated and licensed at retrieval
(GOGET CC-BY-4.0, OSM ODbL, WPI public domain, ITU MIDs 2026-07-19); `data_vintage` is the
**oldest** contributing source so a blend never over-claims freshness (MT-FRESH); a
non-redistributable licence forces zero-storage (GFW).

**One window owns the working tree per task.** Ownership is declared **per task, not per
directory** — it has already been `backend/ + migrations/ + docs/` in one task and
`scripts/ + docs/` in another, with `backend/` explicitly another window's. Read the current
task's declaration; do not infer it from a previous one. Uncommitted changes you did not write
are reported, never committed.

**The displayed name is `Atheer RADAR`; the technical names are not the brand.** Owner decision,
2026-08-04 (REBRAND-DOCS) — recorded here so the scope is not reopened:

| Layer | Value | Rule |
|---|---|---|
| **Displayed platform name** | **Atheer RADAR** | ✅ changes — doc **titles** read `Atheer RADAR (scraplan)`, and systemd `Description=` lines read `Atheer RADAR …` |
| **Internal codename** | `scraplan` | ⛔ stays — unit names `scraplan-*`, paths `~/Desktop/scraplan`, `~/scraplan-backups`, `~/scraplan-offsite`, container `scraplan-timescaledb`, volume `scraplan_timescale_data`, env `SCRAPLAN_BASE`, stored value `geo_source='scraplan-aoi'` |
| **Data source** | `opensky` | ⛔ stays — OpenSky Network attribution and its licence credit, the DB **name and role** (`-U opensky -d opensky`), env `OPENSKY_*`, the `'opensky'` row-level `source` identifier, and the `opensky-YYYY-MM-DD.sql.gz` dump prefix |

⛔ **Titles carry both names on purpose.** `Atheer RADAR (scraplan)` keeps the codename
discoverable for a reader who arrives at a document from its heading and then runs `systemctl`
or greps the tree. ⛔ **And narrative prose is deliberately NOT rebranded:** sentences like "All
scraplan units are user-scope" or "every scraplan timer" sit directly above commands that read
`grep scraplan` / `list-timers 'scraplan-*'`, so renaming the prose would make each such passage
contradict the command beneath it. These are operational documents read **during an outage**,
when a reader can least afford that contradiction. ⚠ A second reason, specific to this repo: the
Arabic docs would embed a Latin wordmark inside RTL prose, which carries the bidi hazard §3
documents (BE-FIX-1) — a cost with no operational benefit.

⛔ **Nothing executable moved.** The restore and backup command blocks in
[`docs/RESTORE_PROCEDURE.md`](RESTORE_PROCEDURE.md) and the off-host
`READ_THIS_BEFORE_RESTORING.md` are **byte-identical** across this rename: a single changed
character there would invalidate a procedure that was actually tested (§6.8) and break a real
restore.

#### ⛔ The shared-file rule — `docs/PROJECT_STATE.md` has more than one writer

This file is the one place several windows write at once. Measured during BACKUP-HARDEN: another
window landed two commits **in between** this window's commits and left `backend/*` and
`tests/*` modified in the tree throughout. That is normal and must not require coordination —
so the rule is mechanical:

1. **Write only in your own named section.** Your item owns its `§x.y`; leave every other
   section untouched even when you can see it is stale. If a fact you need lives in someone
   else's section, add yours and cross-reference — never edit theirs.
2. **The documentation commit is the LAST thing in the item, with an explicit pathspec.**
   `git add docs/PROJECT_STATE.md scripts/…` — ⛔ never `git add -A`, never `git add .`, never
   `git commit -a`. Committing last keeps the window in which someone else's work can be
   swept up as short as possible.
3. **Re-run `git status` immediately before that commit.** The tree you began with is not the
   tree you are committing into.
4. **If you find the file modified by someone else: commit your own files alone and report it.**
   Do not stash, revert, or "tidy" their hunks, and do not wait silently. ⚠ If their edits are
   in the same file you must also write, say so in the report and let the owner decide — that is
   a coordination call, not a merge to be improvised.
5. **State it in the commit message when it happened.** A commit that landed between two of
   someone else's is worth one sentence, because the log is the only record that the interleave
   was noticed rather than missed.

---

## 9. Re-measuring, and what drifts fastest

### 9.-1 ⚠ OPEN OBSERVABILITY RISK — a script called from a unit may lose its output silently

⛔ **Recorded, deliberately NOT investigated** (BACKUP-HARDEN, restated by ROTATE-HARDEN as an
open item). **Measured:** on the BACKUP-HARDEN verification run, `backup_offsite.sh` called
`check_offsite_freshness.sh`, the child **ran and returned non-zero** — its warning is in the
journal and the run then succeeded — but **not one of the child's seven output lines was
recorded in that unit's journal**. The identical script under `systemd-run --user` journals all
seven. Cause **not established**; no theory is offered here.

⚠ **Why this is a live risk and not a closed curiosity: if it can swallow one child's output it
can swallow another's.** Every scraplan timer runs a shell script, and several of those call
further scripts or binaries (`rotate_archive.sh` → `sha256sum`/`gzip`, `backup_offsite.sh` →
`pg_dump`/`ssh`, the detectors → Python). A guard that logs its refusal correctly and has that
line dropped is indistinguishable, in the journal, from a guard that never ran — which is the
exact failure shape this project keeps paying for (§6.4, and the 44-hour gap in §6.8).

**The mitigation already applied, twice, is structural rather than diagnostic:** a parent that
needs a child's words **captures and re-emits them itself** (`backup_offsite.sh` does this for
the freshness check) so visibility never depends on stream inheritance. ⚠ **Until the cause is
known, treat "no output in the journal" as inconclusive, never as "it did not run"** — confirm
against the effect (a file, a row count, a `LAST_SUCCESS`), which is the same rule as item 2
below: the measurement is the check, and here the *absence* of a log line is not a measurement.

### 9.0 ✅ SOLVED — the four missing dates, and they are a usage pattern, not a fault (GNSS-REVIVE item 2)

DASH-BOARD-pre measured four whole days absent from the air series — **2026-07-17, 07-23,
07-24, 07-28** — and DISK-1 had recorded the same four dates missing from the local backups
without ever explaining them. **The common cause is proven, from `journalctl --list-boots`:
the host was powered off.**

Boot-gap windows converted to UTC, and the whole UTC days each one consumes:

| host off (UTC) | duration | full UTC day(s) lost |
|---|---|---|
| 2026-07-14 12:59 → 07-15 07:13 | 18.22 h | — (partial only) |
| **2026-07-16 13:23 → 07-18 09:52** | **44.49 h** | **2026-07-17** |
| **2026-07-22 13:36 → 07-25 05:32** | **63.93 h** | **2026-07-23, 2026-07-24** |
| **2026-07-27 19:19 → 07-29 06:44** | **35.41 h** | **2026-07-28** |

⇒ **Exactly the four dates, with no extras and none missing.** (A fifth full-day loss,
2026-06-19, predates the data range.) This is a proven common cause, not an inference from
correlation: the windows are read from the boot table and the day arithmetic is closed.

**Verified across every domain on those dates** — air, sea and satellite TLE all report **0**:

| date | air rows | sea rows | TLE rows |
|---|---|---|---|
| 2026-07-16 | 146,119 | 34,207 | 1,100 |
| **2026-07-17** | **0** | **0** | **0** |
| 2026-07-18 | 5,399,454 | 89,884 | 1,982 |
| **2026-07-23** | **0** | **0** | **0** |
| **2026-07-24** | **0** | **0** | **0** |
| 2026-07-25 | 8,574,779 | 49,556 | 1,480 |
| **2026-07-28** | **0** | **0** | **0** |
| 2026-07-29 | 4,449,982 | 65,137 | 1,392 |

⚠ **AND THEY WERE CLEAN SHUTDOWNS, NOT POWER LOSS.** The last journal lines of all three boots
are `systemd-shutdown: Syncing filesystems and block devices` — an orderly stop. The host is
documented as power-vulnerable and every unit carries `Persistent=true` for that reason, but
the measured cause of every multi-day gap is **someone turning a desktop off**. That matters
for the design: these gaps are a **standing usage pattern that will recur**, not a fault to be
repaired, so the coverage contract that describes them is permanent, not a stopgap.

#### ⚠ Impact on the coverage contract — it simplifies PARTLY, not wholly

DASH-BOARD-0 hoped a proven host outage would collapse per-source gap lists into one global
list. Measured, it collapses **half** of the problem:

- **Host-off gaps ARE global.** One list of `(start, end)` windows, derived from the boot
  table, applies to every domain at once. No per-source duplication needed for these.
- ⛔ **But per-source gaps exist independently and are NOT covered by it.** On **2026-07-21**
  the host was up and air recorded **10,093,424** rows while sea recorded **0**; 2026-07-20 has
  sea at **507** against air's 9.7 M. Those are AISStream outages with the machine running.

⇒ The contract needs **both**: a global `host_gaps[]` (cheap, one source of truth, from the
boot table) **and** a per-stream `gaps[]`. A design that carried only the global list would
silently draw the AIS outages of 07-20/07-21 as real quiet days.

---

Re-run the cited commands and update this file in the same commit as any plan depending on
new numbers. Fastest movers, in order:

1. **Swap / available RAM** (§1, §6.10) — swap went 90 % → 100 % within one 30-minute session,
   and **is still 100 % full** (re-measured 2026-08-01, `available` 1.7–2.2 GiB). This is now the
   tightest resource on the host, storage having been resolved. Three method rules, each learned
   by a wrong reading in MEM-CHECK:
   ⚠ **`free`'s `shared` is not consumption** — 9.3 GiB of it is the database's `shared_buffers`.
   Read **`anon` from the cgroup** (`memory.stat`), not `docker stats`, or a 7.72 GiB container
   with **14 MiB** of private memory looks like the problem.
   ⚠ **100 % swap ≠ thrashing.** Check `/proc/pressure/memory` before concluding: `some` and
   `full` were **0.00** at every horizon with swap completely full. Full-but-static swap is a
   *headroom* problem, not a *stalling* one.
   ⚠ **Do not assume OOM kills.** `journalctl -k` **is** readable as this user — verify (it
   returned **0** matches), and say "verified 0", never "probably none".
   ⚠ **Sample a leak against session age, not wall clock** — the collector looked like a runaway
   at one instant; `MemoryPeak` over a 3-day run being only 4 % above a 4 h 50 m session proved a
   **ceiling** instead (§6.10).
2. **Detector staleness** (§3) — **closed.** Maritime: TIMERS-1 (8 timers). Satellite:
   TIMERS-2 deployed `scraplan-reentry.timer`, the last genuine gap. **10 scraplan timers**
   since BACKUP-EXT added `scraplan-offsite.timer` (2026-08-01); it was 9 before that.
   Everything that *should* refresh now does; what does not refresh is snapshot-by-design
   (`satellite_info`, `satellite_strategic`) or on-demand-by-design (GNSS episodes).
   ⚠ **Still check with `max(computed_at)` / `max(fetched_at)`, never with `list-timers`** — a
   disabled timer and a missing unit both look like silence, which is how the A3 detector went
   9 d 19 h and the reentry feed 10 d 21 h unnoticed. And **distinguish a stale feed from a
   dated snapshot** before calling anything a defect (§3).
3. **Off-host backup validity** (§6.8) — **did the last copy actually succeed?** This moves
   nightly and fails silently by nature: the destination is another machine that can be off,
   full, re-addressed, or have its `authorized_keys` rotated, and none of that raises anything
   here. ✅ **This is now one command** (BACKUP-HARDEN item 3) — it reads **the destination's own
   evidence**, not this host's optimism, and exits non-zero with an explicit verdict:
   ```
   sh scripts/check_offsite_freshness.sh        # 0 = fresh · 1 = STALE or UNMEASURABLE
   ```
   It reports `LAST_SUCCESS` and its age against the 26 h threshold, dumps held, the newest
   dump, stray `.part` files, and whether the restore README is present. `backup_offsite.sh`
   also calls it **before** each run can overwrite `LAST_SUCCESS`, so the journal records how
   long the copy had really been stale.
   ⛔ **Do not substitute `systemctl list-timers` for it.** Throughout the 44-hour gap that
   timer read healthy and `active (waiting)`: it fires nightly and its last firing *failed*,
   which is indistinguishable from a timer with nothing to do. **The measurement is the check;
   a unit listing is a claim** — the same rule as item 2.
   ⚠ **An unknown age is not a healthy age.** An unreachable destination, a missing
   `LAST_SUCCESS` or a non-numeric threshold all report **UNMEASURABLE** and exit 1 by design.
   ⚠ **What it cannot catch: a timer that never fires at all** — in-band checking sees a
   *failing* run, not an *absent* one, and nothing yet schedules the standalone check
   independently.
   ⚠ **And `sha256sum -c` passing is not a restore test** — it proves the bytes match what was
   sent, nothing more. The restore itself **is** now proven for the current dump format
   (§6.8, RESTORE-TEST); re-run [`docs/RESTORE_PROCEDURE.md`](RESTORE_PROCEDURE.md) if the dump
   format or either version ever changes.
   ⚠ **This rule caught a real miss on 2026-08-04:** `LAST_SUCCESS` was 45 h 22 m stale because
   the 08-03 run died with `No route to host`, and it was found only because an unrelated item
   happened to read the file. The 08-03 dump was afterwards backfilled off-host by hand from the
   local series. The check works — run it.
   ⚠ **A `.part` file left in `db/` is the signature of an interrupted transfer** — harmless by
   design (never promoted, never indexed), but a recurring one means the link or the peer is
   dropping mid-copy and should be investigated rather than tolerated.
4. **The local backup series has unexplained holes** (§6.8) — **2026-07-23, 07-24 and 07-28**
   produced neither a dump nor a `.failed` stub, which means those runs never started rather
   than failed. `Persistent=true` should have caught them up at next boot and did not. Re-check
   with `ls ~/scraplan-backups/` against a calendar, **not** with `list-timers` — the same rule
   as item 2: a timer that never fired looks identical to one with nothing to do.
5. **Free disk** (§7) — no longer urgent: **98.62 GB, net freeing** after DISK-2. ⚠ **Re-measured
   2026-08-01: 82 GB free / 82 % used** (87,949,987,840 B) — so this figure moved ~17 GB in
   three days, in the consuming direction. `docs/ENVIRONMENT.md` §5 carries a *third* value
   (13 GB, 2026-07-29). **Three recorded numbers for one quantity: re-measure, never pick.**
   Still re-check
   before anything bulk. **Sample over ≥ 30 minutes** — a 120-second sample under-measured the
   burn rate by **1.7×**. ⚠ **And do not derive a rate from `avail` alone**: free space rose
   twice from external deletions (+27.75 GB unattributed, +16.92 GB from `~/Qwen3.5-4B` being
   removed), which makes an `avail` delta meaningless as a consumption figure. **Trust the
   monotonic per-consumer series** — the archive's own size and `pg_database_size()`. Those two
   are what attributed the growth and then proved the fix.
6. **Throughput** (§1) — ~10× spread across seven samples; one sample is not a measurement.
   ⚠ **The LAN path to the backup peer is a separate figure and must not be conflated** with
   the internet one: **11.2 MB/s** measured over two multi-GB transfers
   (`docs/ENVIRONMENT.md` §3), against an internet median of 2.48 MB/s. **And the lesson
   recurred there** — a 100 MB `scp` probe reported 7.87 MB/s and understated the real
   sustained rate by 43 %. One short sample is still not a measurement.
7. **Row counts** (§3) — `state_vectors` inflow dropped **~20×** at the frame change
   (2026-07-29 13:55:36 UTC), so any rows/day figure predating it is wrong for the present.
   `rendezvous` can move **down** on recompute, so always pair a count with its `computed_at`.
8. **Frame-spanning queries** (§7) — the ADS-B substrate changed sampling frame mid-history.
   Ranges crossing 2026-07-29 13:55:36 UTC mix a global era with a regional one.

**Measurement lesson this file keeps re-learning: the window matters as much as the command.**
Three figures here were wrong or misleading because the sampling window was too short (disk
rate), because a single instant was taken as a rate (the 28 GB jump), or because an ingest
timestamp was inferred rather than traced (§4). State the window beside the number.

**At the time of writing,** the other window had **10 uncommitted changes under
`frontend/`** in flight (`App.tsx`, `api/types.ts`, `components/{MapView,MaritimeSidebar,
VesselList,VesselStrip}.tsx`, `lib/{format,strings}.ts`, `styles/app.css`, plus a new
`vite.verify.config.ts`) — the set grew while this file was being written. They were left
untouched and nothing under `frontend/` was staged. The §6.2 line references were re-verified
against the live file after those edits, but **re-grep before relying on them** rather than
trusting the line numbers.

---

## 10. UI track — layout grammar, what shipped, what remains

**Measured:** 2026-07-29, 11:00–11:10 UTC, by the UI window (`frontend/` owner), against a
**fresh** API instance (see §10.7 — the long-running one predates the contract it serves).
Region orders and heights below are DOM measurements at 1440×900, not readings of the code.

**Appended, not merged.** §§1–9 belong to the backend window and were not edited.

### 10.1 The finding: four views, four layout grammars

Each view invented its own region order, so the platform read as four applications. Measured
region order per view — **the four reachable views** (`replay` is unreachable; see §6.2):

| # | monitor (لوحة) | live (جوّي) | maritime (بحري) | satellites (فضائي) |
|---|---|---|---|---|
| 1 | brand 40 | brand 40 | brand 40 | brand 40 |
| 2 | mode seg 30 | **tiles 139** (4) | **tiles 156** (4) | **tiles 66** (2) |
| 3 | search 128 | feed 95 | sources 95 | groups 93 |
| 4 | `dash-home` 626 | **filter 134** | **search 73** | feed 51 |
| 5 | — | **list 241** | **list 140** | **list 140** |
| 6 | — | **detail 149** | **detail 307** | **detail 149** |
| 7 | — | — | analysis 308 | reentry 281 |
| **scroll** | **1.30 screens** | **1.00** | **1.37** | **1.04** |

Components: `DashboardPanel`+`DashboardHome` (monitor) · `Sidebar`+`SearchBar`+`ContactList`+
`FlightStrip` (live) · `MaritimeSidebar`+`VesselList`+`VesselStrip`+5 analysis panels
(maritime) · `SatelliteSidebar`+`SatelliteList`+`SatelliteStrip`+`ReentryWatch` (satellites).
The maritime analysis stack and the search/area/alert regions are composed **inline in
`App.tsx`**, not in their own shells.

**The grammar being adopted** — the one the live tab already proved:

> **(1) stat tiles, ≤3 decision-relevant numbers → (2) search/filter → (3) the list
> (compact table for homogeneous sets, cards only for heterogeneous) → (4) selected-item
> detail, rendered on selection → (5) analysis products last.**

**Who follows it today:** live ✔ (reference) · satellites ✔ (grammar + one appended table) ·
maritime ✔ since UI-2 (was list-and-detail **last**) · monitor **partially** — regions 1–3
conform, but region 3 is still *cards* for a homogeneous set (see §10.5, UI-4). Only the
monitor still breaks rule (1): 3 tiles now, but the tile row sits under a 128 px search
surface it does not own.

### 10.2 UI-1 (monitor triage) and UI-2 (maritime) — measured outcomes

Paired measurements: pre- and post-change served side by side from two dev servers against
one live window, so absolute counts are comparable despite the alert feed drifting.

**UI-1** — `13944ce` (caveat flip) · `a99eac3` (sort/cap/hoist) · `e097b52` (tiles 7→3):

| Proxy | Before | After |
|---|---:|---:|
| monitor scroll depth | 5.12 screens | **1.30** |
| alert cards in default view | 97 | **15** (cap, with «عرض الكل (97)») |
| alerts above the fold | 10 | 10 *(unchanged — see §10.5)* |
| repeated per-card note blocks | 97 | **15** |
| distinct note strings on cards | 7 | 3 |
| untranslated Latin chars, monitor | 8,233 | **927** |
| stat tiles | 7 | **3** |

**UI-2** — `ff905f8` (reorder) · `8999392` (collapse) · `02a7cc8` (AIS-only tiles):

| Proxy | Before | After |
|---|---:|---:|
| maritime scroll depth | 2.68 screens | **1.19** |
| `.analysis-group` height | 1,672 px | **308 px** |
| untranslated Latin chars, maritime | 1,337 | **21** |
| analysis products in 1st screen | 2 | 1 *(designed: products now follow list+detail)* |

**The fold results — UI-2's primary target, with a vessel selected:**

| | Before | After UI-2 | Now (post-UI-FRESH) |
|---|---:|---:|---:|
| `VesselList` top | 2,134 px ✘ | **465 px ✔** | **436 px ✔** |
| `VesselStrip` top | 2,282 px ✘ | **613 px ✔** | **590 px ✔** |

Selecting a vessel used to push its own detail card off-screen — the sharpest usability
failure found. Both now clear the fold.

⚠ **This is tight, and it is a real constraint on future work.** The maritime detail card is
**307 px** and sits at **590 px**, so it clears a 900 px viewport by **3 px**. Any region added
above it pushes it under. UI-FRESH hit exactly this and had to make the per-source strip
*replace* the old feed row (not sit beside it) plus tighten `.stat` by 10 px to stay inside
the budget. **Measure the fold before adding anything to `MaritimeSidebar`.**

**Collapse defaults:** all five analysis panels collapsed, persisted independently as
`scope-collapse-<key>` (`localStorage`, same convention as the map-layer toggles). Each
collapsed header keeps its title **and count**, so it still answers "is there anything here?"
Collapsed cost is **23 px** against 248–487 px expanded.

### 10.3 UI-FRESH — the per-source vintage contract is consumed (not pending)

`20deb9f` · `f3bdaa9` · `fe1a8d9`, landed after §§1–9 were written.

- **`sources[]` is read; `data_vintage` is treated as the worst case it now is** and shown
  only for a blend. The per-source strip **replaced** the single-source feed row — that row
  asserted one liveness verdict for the whole payload, the exact defect MT-FRESH found.
- **MarineTraffic is opt-in**, off by default, persisted as `scope-mt`. The client used to
  request `source=all` explicitly; it now requests `aisstream`. **Measured consequence: the
  default maritime map went 2,862 → 246 vessels** — the honest count of what is tracked live.
- **Stale positions are marked** in list, detail and map (hollow dashed glyph — shape, not
  colour alone). Threshold is **900 s, deliberately not the backend's 180 s**: measured
  `aisstream` median 2.8 m / p90 8.0 m / max 12 m vs `marinetraffic` median ~4.0 d, so 180 s
  per row marked **46 % of the live feed**. At 900 s: 0 of 246 AIS rows marked, exactly 2,616
  of 2,865 marked with the overlay on.

**Contract gap for the backend window:** `sources[]` is built `for s in picked`, so an
un-requested source has no entry and MT's age is unknown to the client while the layer is off.
Emitting all known sources with `rows: 0` when not requested would let the off-state toggle
show the age.

### 10.4 The caveat mechanism (UI-1 Part A)

`lib/caveats.ts` + `components/Caveat.tsx`. The Arabic paraphrases in `strings.ts` were **dead
code**: every panel rendered `data?.caveat?.trim() || T.*`, and the API populates `caveat` on
every path, so English always won.

**Precedence is flipped:** Arabic is the display text; the response caveat is the authoritative
methodology, reachable behind a «المنهجية (EN)» disclosure (LTR-isolated, data font).

**Two-layer drift guard:**

1. **Completeness — compile-time.** `CAVEATS` is `Record<CaveatKey, CaveatEntry>`, so a key
   with no entry, or a typo at a call site, is a `tsc -b` error. Rides an existing gate; no new
   infrastructure.
2. **Staleness — runtime.** Each entry records `en`: the exact backend text its Arabic
   paraphrases. `resolveCaveat` compares the live response against that baseline (whitespace-
   normalised); a mismatch sets `drifted`, renders a `⚠` marker, and warns once per key.

**The rule on drift: the paraphrase is NOT trusted — the live English is shown instead of a
possibly-stale Arabic translation.** Honesty beats localisation. (`caveats.ts` `resolve()`.)

**Coverage, verified:** **8** `CaveatKey` entries — `gaps`, `rendezvous`, `identity`,
`composite`, `crossref`, `dark`, `alerts`, `area` — with **8** `<Caveat>` call sites
(GapCandidates, Rendezvous, IdentityChanges, HullComposite, CrossSourcePanel, DarkVessels,
DashboardHome, AreaPanel), plus **4** `SIGNAL_NOTES` entries for the constant per-signal
`severity_basis` strings. **0 drift markers** at last measurement.

### 10.5 Remaining phases, and what justifies each

**UI-3 — extract the primitives, delete the dead code.** Verified this session:

- **`<FieldGrid>`**: the `.strip` contract (`.strip .strip-head .cs .icao .strip-grid
  .strip-cell .k .v .strip-foot .empty`) is already shared by **6** detail panels, and the
  `cells: Array<[string,string]>` → k/v map idiom is **hand-rolled 8×**. Lowest-churn
  extraction in the codebase: a CSS contract already exists, only the React component is
  missing.
- **`<DataTable>`**: **12** components render `.contact-list`; `scrollIntoView({block:
  "nearest"})` is duplicated in **8** files, alongside the same Enter/Space handler, sort-copy
  and `colSpan` empty row.
- **Delete the orphans — re-verified now: exactly 4 files, 0 importers each, 309 lines total**
  (`VesselSearchList` 83, `AircraftSearchList` 80, `SatelliteSearchList` 77,
  `AirportSearchList` 69). A codebase-wide sweep found **no other** zero-importer component.
  They were replaced by `ResultCards` in the redesign and never removed.
- ⚠ **Correction to the UI-pre assessment, which under-counted bidi isolation.** UI-pre
  claimed "exactly one isolated rule". There is in fact an **RTL data islands** block at
  `app.css:555-559` isolating `.contact-list td`, `.feed-meta b`, `.strip-cell .v`,
  `.strip .icao`, `.replay-bar .t`, `.chart-tip`, `.stat .n`, plus `.strip-route .ap .code`,
  `.vessel-pop td.v`, `.detail-line .v.mono` and `.caveat-en`. **All table-based lists are
  already covered.** The real gap is narrower and specific: the **card** surfaces
  (`.rc-title`, `.rc-sub`, `.rc-note`) and the **div-row** panels — `HullComposite`,
  `CrossSourcePanel`, `DarkVessels` use `<div>` rows, not `<td>`, so `.hull-name`,
  `.tl-summary`, `.identities`, `.xref-name`, `.dark-name`, `.ag-note` carry English with no
  isolation. UI-3 should target those nine classes, not a blanket sweep.
- **Raise the maritime vessel-list height** (approved). Measured now: the wrapper is
  `minHeight: 140` ([App.tsx:1178](../frontend/src/App.tsx#L1178)) and the list renders
  **103 px ≈ 2 rows** on maritime, against **204 px** on live — maritime is squeezed because
  `.contact-list` is `flex: 1 1 120px` and loses the contest to the regions above it. ~260 px
  gives ~7 rows. **Budget check first:** the detail card has only 3 px of fold slack (§10.2),
  so +120 px puts it ~117 px under. Either accept that, or pair the change with reducing the
  307 px detail card.

**UI-4 — alert cards → table.** Justified by measurement, not taste: cards are **189 px** tall
in a **5-column** grid, and the header above them is ~492 px, leaving ~408 px of a 900 px
viewport. Three rows need 3×189 + 2×12 = **591 px**, so only **10 of 15** clear the fold —
which is why "alerts above the fold" did not improve in UI-1 despite scroll depth falling 75 %.
Fitting all 15 needs a **~128 px** row: `(900 − 492 − 24) / 3`. That is the table form. UI-1
deliberately shipped sort+cap+hoist first so this would be measured rather than assumed.

**Per-source vintage consumption — DONE, not pending** (§10.3). Recorded here because the
task that requested this section listed it as outstanding; it landed in UI-FRESH.

**Still open, owner's call:** §6.2 (replay — the UI window's finding, awaiting add-nav-or-delete);
and whether the monitor's 128 px search surface should yield height to its tile row.

### 10.6 What is NOT done

- The monitor is the only view still using cards for a homogeneous result set (UI-4).
- `App.tsx` remains the integration point by design: ~75 `useState`, 8 `useRef`, 24
  `useEffect`. Every UI phase so far kept state there; the primitives in UI-3 are
  presentational and must not relocate it.
- Nothing has been done for viewports other than 1440×900. All figures here are that viewport.

### 10.7 Verification method — how to reproduce this

⚠ **THIS PARAGRAPH IS SUPERSEDED — kept because the correction is itself the finding.** It
read *"There are no frontend tests. `package.json` has only `dev`/`build`/`preview`."* True when
written; false since the dashboard redesign. `package.json` now carries **six verify gates** —
`verify:layout`, `verify:layout-system`, `verify:scope`, `verify:dataviz`, `verify:contracts`,
`verify:series-rules` — and §11.2 is the authoritative gate set. There is still no
vitest/jest/testing-library dependency and no `*.test.*`/`*.spec.*` file: the gates are
constraint detonators run through `node`, not a unit-test framework. The original UI-track
gates below are kept for reproducing THIS section's measurements:

```bash
cd frontend
npx tsc -b          # gate 1 — also enforces the caveat-registry completeness check
npm run build        # gate 2 — vite build
```

**Gate 3 is a repeatable headless DOM measurement**, not a test. Playwright + Chromium are
already installed in the repo `.venv` (a backend dependency, absent from `package.json`):

```bash
cd frontend && npm run dev                     # proxies /api → 127.0.0.1:8000
/home/gis-syr/Desktop/scraplan/.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width":1440,"height":900})
    pg.goto("http://localhost:5173/", wait_until="domcontentloaded")  # NOT networkidle:
    pg.wait_for_timeout(7000)                                          # the WS feed never idles
    pg.get_by_role("tab", name="بحري").first.click(); pg.wait_for_timeout(9000)
    print(pg.evaluate("""() => {
      const s = document.querySelector('.side'); s.scrollTop = 0;   // "without scrolling"
      const st = s.getBoundingClientRect().top, VH = window.innerHeight;
      const inFold = e => e.getBoundingClientRect().top >= st-1
                       && e.getBoundingClientRect().bottom <= VH;
      const list = [...s.querySelectorAll('.contact-list')]
                     .find(e => !e.closest('.analysis-group'));      // see pitfalls
      return {screens: +(s.scrollHeight/s.clientHeight).toFixed(2),
              listFold: inFold(list), stripFold: inFold(s.querySelector('.strip'))};
    }"""))
    b.close()
PY
```

**Pitfalls that produced wrong numbers in this track — all three cost a re-measurement:**

1. **`wait_until="networkidle"` never fires** — the live WS feed keeps the page busy. Use
   `domcontentloaded` + an explicit wait.
2. **A fold test must require `top >= 0` too.** Checking only `bottom <= VH` reports a region
   scrolled *above* the viewport as "clearing the fold". Reset `scrollTop = 0` before measuring:
   the question is what is visible *without* scrolling.
3. **`.contact-list` is not unique** — the analysis panels use it too, and `HullComposite`'s
   `aria-label` also contains «السفن». Selecting the real vessel list requires
   `!el.closest('.analysis-group')`.

**Nav labels for `get_by_role("tab", …)`** are `لوحة` / `جوّي` / `بحري` / `فضائي` — *not* the
`T.views` strings. Stats is a sub-mode button (`إحصاءات`), and replay is unreachable (§6.2).

⚠ **The API on `:8000` may not serve the contract in the repo.** It was started
**02:44:19**; MT-FRESH (`3ec3468`) landed **05:12:04**, so it returns **no `sources[]`** and the
pre-fix `data_vintage`. UI-FRESH and §10.1 were therefore measured against a second read-only
instance (`.venv/bin/python server.py --host 127.0.0.1 --port 8011`; `server.py` performs no
startup writes) with an untracked `frontend/vite.verify.config.ts` pointing the proxy at it —
created and deleted per run, never committed, so `vite.config.ts` stays untouched. **Restart
the `:8000` process** and this workaround is unnecessary.

---

## 11. Handoff — state at 2026-08-24, and what is open

**Working tree CLEAN.** Migrations applied through **052**. Eleven commits landed the
five-item dashboard plan: `9efa560` → `ae09c5f`. The design record — including why three
visual proposals were rejected — is §DASH-3, §DASH-4a and §DASH-4b/5 of `docs/DASH_PRE.md`.

### 11.1 ⚠ Operational state a new session must know BEFORE touching anything

* **Database: `127.0.0.1:5435`** (container `scraplan-timescaledb`). The owner's other
  project `thro-pay` contended for that port and was moved to **5437** (2026-08-24).
  ⛔ If a connection fails with `InvalidPasswordError`, run `docker ps` **before** blaming the
  credential — the request is reaching a *different* database and the symptom is misleading.
  A second symptom hides the first: `/live` is served ARCHIVE-FIRST from `latest.json`, so the
  API keeps answering 200 with hundreds of aircraft while every DB-backed family returns 0.
* **API: `.venv/bin/python server.py --host 0.0.0.0 --port 8000`.** Exposed on all interfaces
  **deliberately** — several analysts on different machines share one screen. The default in
  `server.py` is `127.0.0.1`; the flag is an operator choice.
  ⛔ **IT DOES NOT AUTO-RELOAD.** Any change under `backend/` needs a restart or you are
  testing stale code. This cost two misleading test runs on 2026-08-24.
* ⛔ **A `server.py` RESTART LEAVES THE OLD PROCESS ALIVE.** Measured 2026-08-24: **three**
  `server.py` processes were running, only one holding the listening socket, the other two
  holding **107 MB and 122 MB RSS plus a DB connection each** and serving nothing — on a host
  with **276 MB free and swap fully exhausted** (§6.10 names memory the tightest resource).
  Both **ignored SIGTERM** and needed `SIGKILL`. That is how they were born: uvicorn's
  graceful shutdown releases the listening socket *first* and then waits for in-flight
  connections that the live feed never closes, so the next start binds the port happily while
  the old process hangs forever. ⇒ After any restart, check `pgrep -af server.py` and kill
  what no longer holds `:8000`.
  ⚠ **And start it detached.** A server launched in a foreground shell dies with that shell —
  one started that way was lost mid-session. Use
  `setsid nohup .venv/bin/python server.py --host 0.0.0.0 --port 8000 > api.log 2>&1 &`,
  which leaves it under `systemd --user` (verified: ppid 1/2329, survives the session).
* **`DASHBOARD_WRITE_TOKEN` in `.env`** guards the only two mutating endpoints
  (`POST /alerts/triage`, `POST /alerts/case`). It **fails closed** — unset refuses every
  write with 503. Each browser needs it entered once (stored in `localStorage`).
  ⛔ It is NOT authentication and must never be described as such: a browser bundle cannot
  hold a secret from whoever opens it. It stops what reaches the port *without* opening the
  dashboard.

### 11.2 The gate set — run ALL of it before any commit

```bash
cd frontend && npm run build \
  && npm run verify:layout && npm run verify:layout-system && npm run verify:scope \
  && npm run verify:dataviz && npm run verify:contracts && npm run verify:series-rules
cd .. && .venv/bin/python -m pytest tests/ -q
```

Reference state: **layout-system 11 passed / 0 failed over 18 surface runs**, and
**1058 passed / 0 failed / 1 skipped** backend (2026-08-24, after TEST-DETERMINISM + the §6.17
recompute). ⚠ Between the code fix and the recompute the suite sat at 1057/1/1 — the one red
being `test_gnss_anomalies_endpoint_shape_split_and_filters`, red because the STORE held the
broken detector's output. It went green with **no test change** once the detector ran, which is
what a test pointing at data rather than at itself should do.
⚠ Every run now prints a `db-path:` line naming whether the DB path was exercised — a run that
skipped it is no longer indistinguishable from one that passed it. `verify:layout-system`
takes ~90 s.
⚠ It can fail transiently with `could not measure … no content region (fail closed)` after an
API restart with a cold cache — a load race, not a layout defect. Re-run once; if it repeats,
it is real. ⚠ `verify:scope` has its own transient with the same shape — `⛔ Chrome did not
open a debugging port` — when it starts while `verify:layout-system`'s Chrome is still
releasing its port. Both fail CLOSED, which is why they look alarming; re-run the one gate.

⛔ **The gates themselves moved in DASH-6, and each change was detonated before being trusted:**
* **G10 was BLIND TO COLOUR** and reported 6/6 green through THEME-3's repaint of every tab.
  `96b7f29` claimed it had been extended to carry the ground and the tile text; it had not —
  that commit touched two files, neither of them the gate. It now fingerprints the resolved
  tokens AND what they paint on `body` / `.home-panel.lead` / the tile / the card /
  `.dash-secondary`, plus the tile LABELS. Re-detonated: the same injection that passed green
  now fails 6/6, exit 1. ⚠ Tile NUMBERS are deliberately not frozen — they come off the live
  feed, and freezing them is the cry-wolf baseline the gate's own header refuses.
* **G9 now skips a closed `<details>`.** Chrome collapses it via `content-visibility: hidden`
  on `::details-content`, so `Range.getClientRects()` still reports laid-out geometry for
  content that is never painted — `checkVisibility()` returns **false** for those rows. The
  phantom exists on the domain tabs too and predates DASH-6. `--detonate` still catches G9.
* **G1b's `seatIsSeated` now requires the seat to be RENDERED**, not merely present. A
  `display: none` map answers `getBoundingClientRect()` with zeros, which G1b read as "seat
  clears the bar by −55".

### 11.3 Open items, in priority order

1. ✅ **CLOSED — and it was NEVER test isolation** (TEST-DETERMINISM, 2026-08-24).
   The item read *"`app.state.db` is shared … the failing tests skip or pass in isolation, so
   the defect is cross-file state sharing, not the product."* Measured, it was **three
   unrelated things**, and the diagnosis was wrong on all three counts. Suite went
   **7 failed / 1051 passed → 1 failed / 1057 passed**, the remaining failure being a real
   product defect (§6.17) that is deliberately left red until its recompute is authorised.
   * **The "skip in isolation" was not isolation — it was a SILENT SKIP.** 24 test files
     decide DB reachability with a module-level `os.environ.get("DATABASE_URL")`, and nothing
     in the repo exports it: it arrives as a side effect of importing `backend.config`
     (`load_dotenv()` at config.py:11). **13 of the 24 have no top-level `backend` import**
     (they defer it into `_client()`), so in a full run they are rescued by accident —
     `test_air_daily.py` imports `backend.app` on line 18 above its own DSN read on line 21 —
     and run ALONE they print `1 skipped`: a green line meaning nothing executed. They never
     "passed in isolation". ⇒ `tests/conftest.py` now loads the env once before collection,
     prints the DB-path decision on every run (in the terminal summary too, because
     `pytest_report_header` is suppressed under the gate's own `-q`), and **fails closed** —
     exit 1 if `.env` declares a DSN while anything skipped for want of one. **Detonated:**
     loader disabled ⇒ `6 skipped`, exit **1**, not green.
   * **The 6↔7 oscillation was a RACE AGAINST A LIVE CLOCK, in one assertion.**
     `test_maritime_per_source_vintage` asserted `marinetraffic.stale is True` on the premise
     of an "un-serviced MT store". MT is serviced now — `max(ts)` in `mt_vessel_states`
     measured **205 s** old against **11 s** for `vessel_states` — and `stale` is
     `age > MARITIME_STALE_S` with the threshold at **180 s** (api.py:1041, 1141 at the time; since the 2026-09-01 split: `backend/routes/maritime.py`, threshold now `Settings.maritime_stale_s`). A
     timer-refreshed store crosses 180 s repeatedly, so the flag flipped with nothing in the
     test changing. Run order only ever decided WHEN the clock was read. Now asserts that the
     published `stale` agrees with the published `age_seconds` — same defect caught, no race.
   * **Four failures were pinned data on DELETE-then-INSERT tables, and two were LIVE BUGS.**
     The capstone's `(2,2,1,6)` and `n_pattern_types == 4` were verified against the store
     before being touched (gaps 2→4 is the candidate tier growing; rendezvous 2→0 is real —
     zero rows name that hull, while the detector itself is alive with 67 rendezvous over 27
     hulls) and now assert agreement with an independent count. But `999999999` "cannot
     exist" was **false** (§6.18), and the GNSS `area`-class emptiness was a **one-token SQL
     defect** that had silently killed two of three classes (§6.17) — that assertion was the
     only thing still pointing at it, and softening it was drafted and thrown away.
2. **`--sev-low` at 3.68:1 on `--panel`** (4.35:1 on `--bg` — see §6.0 for both surfaces).
   An owner decision, not a task.
3. **Three surfaces not rebuilt — «بحث كيان» now has the WIDTH, not the SHAPE** (DASH-6b).
   «بحث منطقة» and «تفصيلي» still carry their old shape inside the new frame, and so does
   «بحث كيان» internally. What changed is that the entity surface no longer yields 40 % to a
   map it does not show: content column **804 → 1392 px at 1440** and **920 → 1852 at 1900**,
   lane track **636 → 1224** and **752 → 1684**. ⚠ Releasing a surface is not rebuilding it —
   recorded so the remaining two, and this one's interior, do not read as finished.
4. **The case file has no per-item note editor.** `case_items.note` is written on add and by
   the API, but the UI offers no way to edit it afterwards — and the note is where a human
   records who and why, since there is no `owner` column by decision.

### 11.4 ⛔ Rules this project runs on that were broken repeatedly on 2026-08-24

* **Measure, do not assume — and test the REAL path, not the assumed one.** Three times in one
  day I verified what I *expected* the code to do: tested the `upsert` path while the detectors
  DELETE-then-INSERT (migrations 047/048 were invalidated by it, fixed in 050); wrote "safe
  while bound to localhost" while the server runs `--host 0.0.0.0`; and ran an end-to-end test
  against an API serving pre-guard code. Each check passed while the defect stood.
* **Prove a gate by detonation.** A check that cannot fail is not a check. Every gate added
  here is detonated against the defect it exists to catch.
* **Fail closed** — a guard that cannot measure must refuse, not allow.
  ⚠ **But a fail-open path can HIDE a broken call.** `still_live` returned `false` for every
  case item — including one collected from the live feed a second earlier — because an
  in-process call passed FastAPI's `Query(...)` default *objects* into the query. The response
  code was 200 throughout. **Read the values, not the status.**
* **A comment that lies is worse than no comment.** Every ratio names the surface it was
  measured on; `G8c` enforces that a `px` number in a token's own comment equals the token.
* **Never restyle a component another surface owns** — gate it with a PROP, not a selector.
  That is what leaked four rules onto the three domain tabs; `verify:scope` (G10) guards it now.
* **The `caveat` never drops** — not from a message, not from a card, not from a report.


## 12. 2026-09-01 — the review-hardening pass (structure, security, gates)

**Measured/verified 2026-09-01** on this host, delivered on
`refactor/structure-and-hardening` (merged to `main` the same day). The full
finding-by-finding ledger — 22 review findings, each either fixed-with-commit or
closed by a written decision — is [`REVIEW_DECISIONS.md`](REVIEW_DECISIONS.md);
the mechanics are in `BACKEND_CHANGELOG.md` / `FRONTEND_CHANGELOG.md` under this
date. What a fresh session must know:

* **The REST surface moved:** `backend/api.py` (4,261 lines) → `backend/routes/`
  (ten domain modules); `api.py` is now a re-export shim. ⛔ Patch the DOMAIN
  module (`backend.routes.maritime._DARK_CACHE`), not the shim. The frozen v1
  contract was proven, not asserted: **the OpenAPI schema was byte-identical
  before/after the split** (44 operations); `/military`'s `source` field is the
  one v1-ADDITIVE change since, shipped with its own five-rung tests.
* **The gates are at zero and must stay there:** `./scripts/check.sh` = ruff
  (F+B) + mypy (backend, pydantic plugin, lenient tier) + 1,075 pytest +
  4 vitest — every family adopted at zero findings, so any output is a
  regression. Style families (E501/E7/I) are documented house style
  (`pyproject.toml`), not an oversight.
* **The event loop no longer blocks:** `journalctl --list-boots` measured
  **5.62 s cold** and every sync read on an async path now rides
  `asyncio.to_thread`. ⛔ Standing rule: no `open()`/`json.load`/
  `subprocess.run`/`.stat()` directly in an `async def`.
* **Detector timers are staggered off `:00`** (quality 3/15 · rendezvous 6/20 ·
  zone-dwell 8/20 · identity 9/30 — deployed to `~/.config/systemd/user`,
  verified via `list-timers`; air-daily's `:37` stays clear). OPERATIONS.md
  table updated.
* **Security posture:** popup XSS closed at every sink (`esc()`, unit-proven);
  identifier charsets pinned; window params capped; browser-protection headers
  on every response; `ATHEER_API_KEY` is an OPT-IN read gate (unset = open
  reads, the documented inverse of the fail-closed write token). ⚠ Measured
  trap: `systemctl is-active ufw` = active while `ufw.conf ENABLED=no` — no
  firewall is armed; arming it is a console decision (§6.21-adjacent, README
  §Exposure).
* **Recovery is now pinned:** `constraints.txt` (65 pins) rides
  `pip install -r requirements.txt -c constraints.txt`; SSH host-key checking
  is `yes` everywhere (both keys pre-verified in known_hosts).
* **New open decision:** §6.21 — airplanes.live 403; the military layer is dark
  upstream, honestly reported by the new fallback ladder.
