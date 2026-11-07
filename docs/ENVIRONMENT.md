# Atheer RADAR (scraplan) — Measured Environment

**Measured:** 2026-07-29 09:30 UTC (05:30 EDT) · **Host:** `gis-syr-ThinkCentre-neo-50t-Gen-5`
**Method:** every figure below was produced by a command run on this host in the session that
wrote this file. The commands are cited inline so any value can be re-measured.

---

## 0. Rule — environment figures must be measured, never carried

**Any hardware, network, service or storage figure used in planning must be measured
in-session and cited from this file. Figures must never be carried across projects.**

Concretely:

1. If a plan depends on a number (download time, core count, VRAM, free disk, timer
   cadence), that number comes from this file, or the plan re-measures it and updates this
   file in the same commit.
2. Cite it — "per `ENVIRONMENT.md` §3, median 2.5 MB/s" — not "roughly 28 Mbps".
3. Never reuse a figure because it was true on another machine, in another repo, or in an
   earlier assessment that did not say how it measured.
4. A figure with no recorded method is not a measurement. Record the command.

**Why this file exists.** The **SAR-pre** assessment was written against hardware and
network figures that were never measured for this project — they were carried in from an
unrelated project. Its Q1/Q2 conclusions about download time, detector choice and compute
rest on those figures and are therefore unsound; they must be re-derived against the values
below before any of them is acted on. Three of the carried figures are simply false here:
the CPU is not the one assumed (§1), there is **no usable GPU at all** (§2), and free disk
is **13 GB**, not a working surface for bulk imagery (§5).

---

## 1. Host

| Fact | Measured value | Command |
|---|---|---|
| Hostname | `gis-syr-ThinkCentre-neo-50t-Gen-5` | `hostnamectl` |
| Machine | LENOVO ThinkCentre neo 50t Gen 5 (`12UD005MGP`) | `/sys/class/dmi/id/{sys_vendor,product_name}` |
| OS | Ubuntu **25.10** (Questing Quokka) | `/etc/os-release` |
| Kernel | **6.17.0-41-generic**, x86-64 | `uname -r` |
| CPU | **13th Gen Intel Core i5-13400** | `lscpu` |
| Logical CPUs | **16** | `nproc` → `16`; `lscpu` → `CPU(s): 16` |
| Instruction sets | `avx`, `avx2` — **no AVX-512** | `grep avx /proc/cpuinfo` |
| RAM total | **30.5 GiB** (`MemTotal: 31944652 kB`) | `/proc/meminfo` |
| RAM available | **10.9 GiB** (`MemAvailable: 11427788 kB`) | `/proc/meminfo` |
| Swap | 4.0 GiB total, **3.59 GiB in use** | `free -b` |
| Load / uptime | `1.54 2.18 2.23` · up 2 h 45 min | `/proc/loadavg`, `uptime -p` |

### ⚠ Correction — the CPU is not an i7-12700K

The owner stated an **i7-12700K**. The machine reports a **13th Gen Intel Core i5-13400**.
This is not a reporting quirk; it is a different part (65 W non-K, 10 cores, no AVX-512 —
versus a 125 W 12-core K part). The active display adapter being an Intel **UHD Graphics
730** iGPU (§2) is consistent with the i5-13400 and with an OEM ThinkCentre desktop.

✅ **Reconciled 2026-08-25 (WINSTORE) — the i7-12700K is real, it is just a DIFFERENT MACHINE.**
While measuring the Windows backup destination at `10.0.0.113`, `Get-CimInstance Win32_Processor`
there returned **`12th Gen Intel(R) Core(TM) i7-12700K`**. So the owner's figure was accurate
and was simply attached to the wrong host. Nothing above changes — **this** host is still an
i5-13400 with 16 logical CPUs and no usable GPU, and every planning figure in this file is
measured on it. The correction is recorded because "the owner is misremembering their
hardware" was the wrong conclusion to leave standing: they were describing the other desktop.

### Reconciling `nproc = 16` against "20 threads"

The 20-thread expectation came from the i7-12700K assumption and does not apply. For the
CPU actually present the arithmetic is consistent:

- `lscpu` prints a flat model — `Core(s) per socket: 10`, `Thread(s) per core: 2` — which
  naively multiplies to 20. That product is **an artifact of describing a hybrid CPU with
  one thread-per-core number**; only the P-cores are SMT-capable.
- i5-13400 = **6 P-cores × 2 threads (12) + 4 E-cores × 1 thread (4) = 16**.
- Both `nproc` and `lscpu`'s own `CPU(s):` field report **16**.

**Plan against 16 logical CPUs.** `nproc = 16` was correct as stated; nothing is being
under-reported, and there are no 4 missing threads to recover.

---

## 2. GPU — installed driver, no device

| Fact | Measured value | Command |
|---|---|---|
| `nvidia-smi` | **fails** — "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver." | `nvidia-smi` |
| NVIDIA PCI device | **none** — no device with vendor ID `10de` on any bus | `lspci -nnk \| grep 10de:` → no match |
| Display adapter present | `00:02.0 VGA compatible controller: Intel Alder Lake-S GT1 [UHD Graphics 730] [8086:4682]` — the **only** one | `lspci -nn \| grep -Ei 'vga\|3d\|display'` |
| Driver package | **installed** — `nvidia-driver-590` 590.48.01 (+ `nvidia-dkms-590`, `nvidia-utils-590`, `libnvidia-compute-590`) | `dpkg -l \| grep nvidia` |
| DKMS module | **built** for both `6.17.0-40-generic` and `6.17.0-41-generic` | `dkms status` |
| Kernel module loaded | **no** | `lsmod \| grep nvidia` → empty |
| `/proc/driver/nvidia` | absent | `ls /proc/driver/nvidia` |
| Stale device node | `/dev/nvidiactl` exists (195:255) — leftover, no backing device | `ls -l /dev/nvidia*` |
| Secure Boot | **disabled** — so Secure Boot is *not* why the module is unloaded | `mokutil --sb-state` |

### ⚠ Correction — the RTX 5060 is not visible to this machine

The owner stated an **RTX 5060 (8 GB) is installed**. The driver failure is confirmed, but
the stated cause is not what was measured. **`lspci` enumerates no NVIDIA device at all.**
The only display controller on the bus is the CPU's integrated UHD Graphics 730.

So this is not "card present, driver not loaded". The measured picture is the reverse:

- **The driver stack is complete** — packages installed, DKMS modules compiled against the
  running kernel, Secure Boot off. There is nothing wrong with the software side.
- **There is no hardware for it to bind to.** A built NVIDIA module will not load when no
  `10de` device enumerates, which is exactly the state observed.

Therefore the record cannot say the card is present in hardware — **as far as the OS can
see, it is not there**: either not physically installed in this chassis, or installed and
failing to enumerate on the PCI bus. Distinguishing those two requires opening the machine
or checking firmware, which was out of scope here.

**Planning consequence — treat this host as CPU-only.** No CUDA, no VRAM, 16 CPU threads,
no AVX-512. Any SAR-pre conclusion that assumed 8 GB of usable VRAM (detector choice, batch
size, "GPU inference" timings) is invalid and must be re-derived CPU-only.

*Nothing was installed, loaded, or repaired in producing this section.*

---

## 3. Network

| Fact | Measured value | Command |
|---|---|---|
| Interface | `enp2s0`, operstate `up` | `/sys/class/net/*/operstate` |
| **Negotiated link speed** | **100 Mb/s** (≈ 12.5 MB/s hard ceiling) | `cat /sys/class/net/enp2s0/speed` |
| Default route | `via 10.0.0.1 dev enp2s0 proto dhcp src 10.0.0.109` | `ip route show default` |
| Other interfaces | `tailscale0` (100.79.72.78) up, plus docker bridges/veths — **not** on the default route | `ip route`, `tailscale status` |
| Public egress IP | `23.234.89.60` (Cloudflare colo LAX) | `curl api.ipify.org` |

### Downstream throughput — measured

**Method:** seven sequential HTTPS GETs of a 25 MB object from
`https://speed.cloudflare.com/__down?bytes=25000000`, reading curl's own
`%{speed_download}`:

```
curl -o /dev/null -w '%{speed_download}\n' 'https://speed.cloudflare.com/__down?bytes=25000000'
```

| Run | MB/s | Mbps |
|---|---|---|
| 1 | 2.73 | 21.8 |
| 2 | 0.36 | 2.8 |
| 3 | 2.10 | 16.8 |
| 4 | 3.40 | 27.2 |
| 5 | 2.48 | 19.8 |
| 6 | 1.59 | 12.7 |
| 7 | 3.06 | 24.5 |

- **Median 2.48 MB/s ≈ 19.8 Mbps** ← use this for planning
- Mean 2.24 MB/s ≈ 17.9 Mbps · **min 0.36 MB/s (2.8 Mbps)** · max 3.40 MB/s (27.2 Mbps)

**Independent cross-check, different provider and route:** 100 MB from
`https://ash-speed.hetzner.com/100MB.bin` did not finish inside 120 s — 98.2 MB
transferred, **0.82 MB/s ≈ 6.5 Mbps**. Sustained large transfers are materially slower
than the 25 MB samples.

### ⚠ Correction — 3.48 MB/s ≈ 28 Mbps is the best case, not the typical case

The single carried measurement (~3.48 MB/s ≈ 28 Mbps) is close to my **fastest of seven
samples** (3.40 MB/s / 27.2 Mbps). It is not representative:

- it is **~40 % above the median** (2.48 MB/s), and
- **~10× above the worst sample** (0.36 MB/s), and
- **~4× above the sustained 100 MB cross-check** (0.82 MB/s).

**Plan on 2.5 MB/s (20 Mbps) with a floor near 0.4 MB/s, and on ≤ 0.8 MB/s for multi-GB
sustained pulls.** Any SAR-pre download-time estimate built on 28 Mbps is optimistic by at
least 40 %, and by ~4× for the large-file case that SAR imagery actually is. The 100 Mb/s
NIC link is a separate, harder ceiling: this host can never exceed ~12.5 MB/s regardless of
the upstream link.

**Measurement artifact worth recording:** requests for `bytes=100000000` returned
`size_download=1` despite a `Content-Length: 100000000` response header, repeatably. The
25 MB object size was chosen because it transfers intact. Re-measure with 25 MB objects.

### LAN throughput to the backup peer — measured 2026-08-01 (BACKUP-EXT)

**This is a different figure from everything above, and must not be confused with it.** The
numbers above measure the *internet* path (median 2.48 MB/s). The off-host backup never
touches it: the destination is a second machine on the **local** network.

| Fact | Measured value | Command |
|---|---|---|
| Destination | `serverq@10.10.20.57` → `serverq-MS-7E06`, Ubuntu 24.04, 20 threads, 15 GiB RAM | `ssh … 'hostname; uname -a; nproc; free -h'` |
| Path | **`via 10.0.0.1 dev enp2s0`** — the LAN, **not** `tailscale0` | `ip route get 10.10.20.57` |
| **Sustained SSH throughput** | **11.2 MB/s ≈ 90 Mbps — 90 % of wire** | two multi-GB `ssh 'cat >'` transfers, journal-timestamped |
| Destination free space | **712,345,415,680 B (664 GB), 24 % used** | `ssh … 'df -B1 ~'` |
| Local sha256 rate | **512 MB/s** | timed `sha256sum` over 100 MB |
| Local `pg_dump \| gzip` rate | **10.8 MB/s of compressed output** | 3,062,243,980 B in 284 s, under daytime detector load |

**Method — and it is the larger sample that counts.** Two real transfers, sizes and
timestamps taken from the `scraplan-offsite.service` journal, each checksum-verified from
the destination:

| Object | Bytes | Seconds | Rate |
|---|---|---|---|
| `opensky-2026-08-01.sql.gz` | 3,062,243,980 | 273 | **11.22 MB/s (89.7 Mbps)** |
| `state_vectors_pre-narrowing_2026-07-29.jsonl.gz` | 3,475,344,889 | 309 | **11.25 MB/s (90.0 Mbps)** |

Both windows *include* the sha256 read back from the destination, so the transfer alone is
slightly faster still. **Use 11.2 MB/s for off-host transfer sizing:** a ~3.1 GB nightly
dump takes **~4.6 min**, and the one-time 3.48 GB era file took **5 min 9 s**.

### ⚠ Correction — the 100 MB `scp` probe understated the real rate by 43 %

The first measurement taken in BACKUP-EXT was a 100 MB `scp -o Compression=no` file, which
reported **7.87 MB/s (63 Mbps)** and was briefly recorded here as the planning figure. The
two multi-GB transfers above then measured **11.2 MB/s** on the same link, same key, same
hour. **The small probe was wrong, and it was wrong in the same direction as §3's own
lesson** — a short sample is not a rate. Two contributors:

- **Connection setup is not amortised over 12.7 s.** Handshake, key exchange and auth are a
  fixed cost paid once; across 273 s they vanish, across 12.7 s they are several percent.
- **`scp` is not `ssh 'cat >'`.** `scp` carries its own file-transfer protocol and
  per-file accounting on top of the SSH channel; the backup path streams raw bytes into a
  remote `cat`, which is what the 11.2 MB/s figure measures.

**Both numbers are recorded deliberately.** The wrong one is kept because the failure mode —
sizing a multi-GB nightly window from a 12-second sample — is the exact mistake §3 already
documents for the internet path, and it recurred here within the same file.

⚠ **Do not substitute the internet figures for either.** The internet median (2.48 MB/s) is
**~4.5× slower** than this LAN path; a backup window sized from it would be wrong by more
than four times, in the direction that hides a problem.

### LAN throughput to the Windows store — measured 2026-08-25 (WINSTORE)

**A third destination, and a third figure.** `serverq` above is a Linux peer reached with
`ssh 'cat >'`. This one is a **Windows** peer reached with `scp`, because `ssh 'cat >'`
does not work there at all (see the trap table below). The numbers are close, but they were
measured separately and neither was assumed from the other.

| Fact | Measured value | Command |
|---|---|---|
| Destination | `scraplanbak@10.0.0.113` → **Windows 11 Pro build 10.0.26200.0**, i7-12700K, 23.8 GB RAM | `Get-CimInstance Win32_OperatingSystem` / `Win32_Processor` |
| SSH server | **`OpenSSH_for_Windows_9.5`** | TCP banner grab on port 22 |
| Path | LAN via `enp2s0` (10.0.0.109 → 10.0.0.113) | `ip -4 addr` |
| ⚠ ICMP | **blocked** — `ping` gets 100 % loss while port 22 is open | `ping -c2` vs `/dev/tcp/10.0.0.113/22` |
| **Sustained `scp` throughput** | **11.0 MB/s ≈ 88 Mbps** over a real 3,825,174,028 B dump | `/proc/<ssh>/io` `wchar` deltas during `backup_windows.sh` |
| Short-probe `scp` throughput | **11.07 / 11.06 MB/s up, 11.04 MB/s down** (300 MB, three runs) | timed `scp`, sha256 verified round-trip |
| Destination volume | **`F:` — 7,452 GB NTFS, 6,964 GB free** (`AvailableFreeSpace` 7,477,827,284,992 B) | `New-Object System.IO.DriveInfo 'F:'` |
| ⚠ Destination disk | **disk 0 = `ST8000DM004-2U9188`, HDD, SATA — a SINGLE disk, no RAID** | `Get-PhysicalDisk`, `Get-Partition -DriveLetter F` |
| Sleep / disk spindown | **never** — `Sleep after` AC index `0x00000000`, `Turn off hard disk after` AC `0x00000000` | `powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` |
| MFT size (≈ object count) | **1.06 GB ≈ ~1.06 M file+directory records** | `fsutil fsinfo ntfsinfo F:` |
| Full-drive ACL propagation | **81 s** for an inheritable ACE across the whole volume | timed `icacls F:\ /deny …` |

**Note the short probe did NOT understate the rate this time,** unlike the serverq
measurement above where a 100 MB `scp` read 43 % low. The 300 MB samples (11.07 MB/s) and
the 3.83 GB production transfer (11.0 MB/s) agree to within 1 %. The difference is sample
size: 27 s is long enough to amortise connection setup, 12.7 s was not. **Use 11.0 MB/s for
Windows-store transfer sizing:** a 3.83 GB nightly dump takes **~5 min 45 s**, plus a
destination-side `Get-FileHash` over the same 3.83 GB from a spinning disk.

⚠ **Both LAN peers sit behind the same 100 Mb/s ceiling, and it is not either machine's
fault.** The local NIC advertises `1000baseT/Full` and the destination's NIC is an **Intel
I226-V**, which is a 2.5 GbE part — yet the link negotiates 100 Mb/s because the link
partner advertises no more than `100baseT/Full` (`ethtool enp2s0`). **The limiting component
is the cable or the switch port between them.** Replacing it is the single highest-leverage
change available to every transfer path in this project: at gigabit the nightly dump would
move in ~35 s instead of ~5 min 45 s.

### ⛔ Four traps specific to a Windows destination — all measured, none guessable

These are recorded because each one produced a failure that looked like something else.

| # | Trap | What it looks like | Measured evidence |
|---|---|---|---|
| 1 | **`ssh host 'cat > file'` does not transfer** | the transfer "hangs" | 300 MB through PowerShell's stdin moved **135,168 B in 300 s** before being killed. The default shell is `cmd.exe` and PowerShell's stdin is a *text* stream. **Use `scp`** — same 300 MB in 27.1 s. |
| 2 | **`cmd.exe` eats quotes and pipes** | `'PasswordAuth' is not recognized as an internal or external command` | any `ssh host 'powershell -Command "… \| …"'`. **Use `powershell -EncodedCommand <base64 UTF-16LE>`**, which has no metacharacters left to split. |
| 3 | **A killed transfer leaves the file LOCKED, not merely partial** | later runs fail to overwrite, with an error that never says why | after killing trap 1's transfer, `Get-FileHash` *and* `Remove-Item` both failed with *"because it is being used by another process"*. A `.part` name is not enough here — the script must prove the `.part` is **gone** before writing. |
| 4 | **PowerShell errors return as CLIXML on stderr** | exit status 0, empty stdout, and a guard that "checked" | a failing remote command returns nothing on stdout. Every measured value must pass `is_uint`/`is_sha256` **before** it reaches a comparison. |

⚠ **And one that is not a failure at all, but reads exactly like one:** NTFS does not update
a directory entry's size while the file is open, so **`Get-ChildItem` reports a `.part` as
`0` bytes throughout a transfer that is running perfectly**. Measured mid-flight: the
destination said `0` while `/proc/<ssh>/io` on this host showed 1.65 GB sent at 11 MB/s.
**A zero-byte `.part` at a Windows destination is not evidence of a stall.** Measure
progress from the sending side.

### ⛔ Free space cannot be measured the obvious way as a non-administrator

MEASURED 2026-08-25 as `scraplanbak` (which is deliberately not an administrator):

```
(Get-PSDrive F).Free                        -> ''                (and .Used -> 0)
(Get-CimInstance Win32_LogicalDisk …).Free  -> ''                (CIM needs elevation)
(Get-Volume -DriveLetter F).SizeRemaining   -> ''                (Storage module needs elevation)
New-Object System.IO.DriveInfo 'F:'         -> 7477827284992     ✅
```

**None of the first three is an error.** They return an EMPTY string with exit status 0 —
the precise failed-measurement shape §0 of this file and the fail-open guard in
`backup_offsite.sh` exist to catch. The first end-to-end run of `backup_windows.sh` refused
to transfer anything and said so (*"free space came back non-numeric ('')"*). The guard was
right; the measurement was wrong. A guard that failed *open* would have hidden it.

---

## 4. Services

**All scraplan units are user-level systemd** (`~/.config/systemd/user/`), not system
units. `systemctl list-timers` at system scope shows **zero** scraplan timers — its 19
timers are all OS stock (apt, logrotate, sysstat, man-db …). Use `systemctl --user`.

### Database

| Fact | Measured value |
|---|---|
| Container | `scraplan-timescaledb` — up 3 h (healthy) |
| Image | `timescale/timescaledb:2.28.2-pg17` |
| Server version | **PostgreSQL 17.10** (x86_64-pc-linux-musl, Alpine) |
| Extension | **timescaledb 2.28.2** |
| Port | **`127.0.0.1:5435` → `5432`** — loopback only |
| Mount | named volume `scraplan_timescale_data` → `/var/lib/postgresql/data` (host path `/var/lib/docker/volumes/scraplan_timescale_data/_data`) |
| Database size | `opensky` = **14 GB** |

Commands: `docker ps`, `docker inspect scraplan-timescaledb --format '{{range .Mounts}}…'`,
`psql -h 127.0.0.1 -p 5435 -U opensky -d opensky -tAc 'select version()'`,
`select extversion from pg_extension where extname='timescaledb'`,
`select pg_size_pretty(pg_database_size('opensky'))`.

### API service

| Fact | Measured value |
|---|---|
| Unit | **`scraplan-server.service`** (user scope) — "Atheer RADAR API server (REST v1 + WS + SPA)" |
| ExecStart | `/home/gis-syr/Desktop/scraplan/.venv/bin/python server.py --host 0.0.0.0 --port 8000` |
| Port | **8000**, listening on **`0.0.0.0`** (`ss -ltnp` → pid 7099) |
| State | `enabled`, `active` · `Restart=on-failure` |

Note the asymmetry: the DB is bound to loopback, the **API is bound to all interfaces**.

### Long-running collectors (`enabled` + `active`, no timer — always on)

| Unit | Description |
|---|---|
| `scraplan-collector.service` | OpenSky collector (Middle East box, DB mirror) — `tracker_opensky.py` |
| `scraplan-maritime.service` | Maritime AIS collector (AISStream.io) — `tracker_maritime.py` |
| `scraplan-military.service` | Military overlay collector (airplanes.live) — `tracker_airplanes.py` |
| `scraplan-adsb-ingest.service` | ADS-B Exchange ingester → TimescaleDB — `ingest.py follow` (`Restart=always`) |

Verified with `systemctl --user is-active` → all `active`.

### Deployed timers — every one, with its measured interval

From `systemctl --user list-timers --all`, intervals read from the unit files
(`grep OnCalendar ~/.config/systemd/user/scraplan-*.timer`). All are `Persistent=true`.

| Timer | `OnCalendar` | Interval |
|---|---|---|
| `scraplan-quality.timer` | `*:0/15` | **15 min** |
| `scraplan-gaps.timer` | `*:0/15` | **15 min** |
| `scraplan-rendezvous.timer` | `*:0/20` | **20 min** |
| `scraplan-satellites.timer` | `*-*-* 00,06,12,18:20:00` | **6-hourly** at :20 |
| `scraplan-rotate.timer` | `*-*-* 00:05:00` | **daily 00:05** |
| `scraplan-backup.timer` | `*-*-* 03:00:00` | **daily 03:00** |

That is the complete set — **six deployed timers.**

⚠ **Superseded 2026-08-01 — there are now ten.** The table above is the 2026-07-29 snapshot.
Four timers were deployed after it: `scraplan-identity`, `scraplan-zone-dwell` and
`scraplan-reentry` (TIMERS-1/2), and **`scraplan-offsite.timer` (`*-*-* 04:15:00`, daily
04:15)** for the off-host backup (BACKUP-EXT, `docs/PROJECT_STATE.md` §6.8). Re-read the set
with `systemctl --user list-timers 'scraplan-*' --all` rather than trusting this table.

### ⚠ Claimed but not actually deployed

| Claim | Where claimed | Measured reality |
|---|---|---|
| `scraplan-identity.timer` — 30 min, listed under "**Runtime timers** (production currency)" | `MARITIME_INTELLIGENCE_BASE.md` §5 and §2.6 ("A3 refresh … every 30 min") | **Unit file exists but is `disabled` and `inactive`**; absent from `systemctl --user list-timers`. Its `OnCalendar=*:0/30` matches the stated interval, so the *interval* claim is right and the *deployment* claim is wrong. **A3 identity-change is not being refreshed on any timer** — it is on-demand only. |
| `scraplan-reentry.timer` / `.service` — daily 05:30, driving `tracker_reentry` | `SATELLITE_RESEARCH_BASE.md` §"Refresh" and §"Runtime services" | **No such unit file exists at all** — neither timer nor service is present in `~/.config/systemd/user/`. |

Verified: `systemctl --user is-enabled scraplan-identity.timer` → `disabled`;
`is-active` → `inactive`; `ls ~/.config/systemd/user/ | grep -i reentry` → no match.

### Claims that check out

- **No GFW timer or service exists**, and no doc claims one — GFW collection is not
  timer-driven. GFW-pre did not over-claim a timer.
- **No zone-dwell timer** — `MARITIME_INTELLIGENCE_BASE.md` explicitly says
  "no deployed timer yet". Accurate.
- **No GNSS-episode-detector timer** — docs say "on demand … no deployed timer". Accurate.
- No `zone`/`dwell`/`draught`/`crossref` units of any kind exist.

---

## 5. Storage

**One filesystem holds everything** — root, `/home`, the Docker volume with the database,
and the JSONL archives. There is no separate data volume.

| Fact | Measured value | Command |
|---|---|---|
| Device / type | `/dev/nvme0n1p2` on `/`, **ext4** | `findmnt -no SOURCE,TARGET,FSTYPE /` |
| Size | 468 GB | `df -h` |
| Used | 432 GB | `df -h` |
| **Available** | **13 GB — 98 % full** | `df -h` |

Confirmed with `df -h / /home /var/lib/docker` — all three resolve to the same
`/dev/nvme0n1p2`.

### ⚠ Re-measured 2026-08-01 — the 13 GB figure above is stale, and by a lot

DISK-2 changed this materially. Same command, same device, 2026-08-01:

| Fact | 2026-07-29 | **2026-08-01 (current)** |
|---|---|---|
| Used | 432 GB | **362 GB** (388,295,159,808 B) |
| **Available** | 13 GB — **98 %** full | **82 GB — 82 % full** (87,949,987,840 B) |

**Use 82 GB, not 13 GB.** The 13 GB row is kept above rather than overwritten because the
⚠ planning consequence written against it — that free space, not bandwidth, was the binding
constraint — was true when measured and is what motivated DISK-2. It is no longer the
current state.

⚠ **`docs/PROJECT_STATE.md` §9 states 98.62 GB for this same quantity.** That figure is
also stale; it and this one were taken days apart against a volume whose free space moves in
both directions (§9 records +27.75 GB and +16.92 GB arriving from external deletions).
**Re-measure before sizing anything; do not pick whichever of the three numbers is
convenient.**

### ⚠ Re-measured again 2026-08-25 (WINSTORE) — and the 82 GB figure is stale in the OTHER direction

| Fact | 2026-07-29 | 2026-08-01 | **2026-08-25** |
|---|---|---|---|
| Used | 432 GB | 362 GB | **393 GB** → **376 GB** after the archive relocation |
| **Available** | 13 GB (98 %) | 82 GB (82 %) | **51 GB (89 %)** → **68 GB (85 %)** |

**The volume had been refilling.** Between 08-01 and 08-25 free space went from 82 GB back
down to 51 GB, and the largest single contributor was not the database: **`~/scraplan-backups`
had reached 38 GB** — twelve nightly dumps that had grown from 2.84 GB each to 3.83 GB —
**larger than the 15 GB database it backs up.**

Two changes on 2026-08-25 moved it the other way, and the second is not yet visible in the
number above:

1. **Done:** `csv_exports/` (17,957,224,991 B) was relocated to the Windows store and
   deleted locally, after three gates proved `csv_exports.zip` beside it is a complete and
   intact copy. **51 GB → 68 GB free.** The 3.25 GB zip is retained here on purpose so the
   archive still exists on two physical machines.
2. **Takes effect on the next 03:00 run:** local dump retention cut 14 days → 4
   ([`scripts/backup_db.sh`](../scripts/backup_db.sh)), which will release a further
   **~23 GB** (seven dumps) — to roughly **91 GB free, ~80 % used**.

⚠ **Do not read the retention cut as history being thrown away.** Recoverable depth went
*up*: 14 days on this one volume became 4 here + 7 on serverq + **90 on the Windows store**.
See `docs/PROJECT_STATE.md` §6.20, including the file-by-file tier comparison that found
three dates with only ONE copy in existence before the backfill ran.

### What occupies it

| Item | Size |
|---|---|
| `ARCHIVE=/home/gis-syr/Desktop/plandata/data_p1/state_vectors.jsonl` | **41 GiB** (44,242,567,036 B); dir total 42 GiB |
| `opensky` database (logical, inside the Docker volume) | 14 GB |
| `opensky_output/` (rotated `states-*.jsonl.gz`, `military-*.jsonl.gz`) | 328 MiB |
| `maritime_output/` — incl. AIS ground truth `ais-signals.jsonl` (4.2 MiB) | 129 MiB |
| `enrichment_data/` | 100 MiB |
| `satellite_output/` | 2.7 MiB |

### ⚠ Planning consequence

**13 GB is the entire free-space budget**, and it is shared by the growing DB, the
append-only JSONL archives, and the daily `scraplan-backup` dump. Any plan that downloads
bulk data — SAR scenes above all — must state where the bytes land and what is evicted
first; at 98 % full there is no headroom to discover mid-download. Combined with §3
(≤ 0.8 MB/s sustained), a multi-scene SAR pull is constrained by **disk before bandwidth**.

---

## 6. Re-measuring

Re-run the cited commands and update this file in the same commit as whatever plan depends
on the new numbers. Values that drift on their own and are worth re-checking before any
sizing decision: **available RAM and swap** (§1 — swap was already 90 % consumed),
**throughput** (§3 — variance across samples was ~10×), and **free disk** (§5 — 98 % full,
and the archives are append-only).
