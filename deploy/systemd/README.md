# التشغيل الدائم — وحدات systemd (مستخدم)

```bash
cp deploy/systemd/scraplan-*.{service,timer} ~/.config/systemd/user/
# بيانات الاعتماد (مرة واحدة): ~/.config/opensky/env بصلاحية 600 يحوي
#   OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET / OPENSKY_DB_URL
# والطبقة البحرية تقرأ AISSTREAM_API_KEY من ملف المشروع .env (مستثنى من git).
systemctl --user daemon-reload
systemctl --user daemon-reload
systemctl --user enable --now \
    scraplan-server scraplan-collector scraplan-military scraplan-maritime \
    scraplan-marinetraffic \
    scraplan-backup.timer scraplan-rotate.timer scraplan-satellites.timer \
    scraplan-reentry.timer scraplan-offsite.timer scraplan-windows.timer
loginctl enable-linger   # لتعمل الخدمات دون جلسة مفتوحة وبعد الإقلاع
```

`scraplan-marinetraffic` (الطبقة البحرية الثانية، مصدر marinetraffic) — يذيّل
`positions.jsonl` الذي يكتبه مشروع `mtscraper` المعزول، ويعكسه في
`mt_vessel_states`. **مُثبَّت ومتحقَّق منه في 2026-08-15**: 348 سجلاً تدفّقت من
الملف إلى القاعدة في أول دورة (3455 → 3803 صفاً).

⚠ **التذييل يبدأ من نهاية الملف ولا يحفظ إزاحته** عبر إعادات التشغيل، فكل ما
يُكتب أثناء توقّف الخدمة يُفوَّت. هذا مقصود (وإلا أعاد ابتلاع أرشيف بحجم
غيغابايتات عند كل إقلاع)، والتغطية عبر الأمر idempotent:

```bash
python tracker_marinetraffic.py backfill --archive /home/gis-syr/Desktop/scraplan/maritime_source_marine/mt_output
```

**شُغِّل `backfill` في 2026-08-15**: 2,939,061 سطراً، صفر سجل غير صالح،
2,887,623 صفاً جديداً في 8.9 دقيقة (ذاكرة ثابتة ~110 MB).

### جسر الهوية — `identities`

تغذية البلاطات **لا تحمل MMSI إطلاقاً** (صفر من 2.94 مليون صف). المصدر الوحيد
للربط الموثوق هو نقطة النهاية التفصيلية التي يستقصيها `mtscraper` ببطء، ويكتب
نتائجها في `vessel_identities.jsonl`. الأمر التالي يحمّلها في `vessel_xref`:

```bash
python tracker_marinetraffic.py identities --archive /home/gis-syr/Desktop/scraplan/maritime_source_marine/mt_output
```

idempotent (حارس WHERE-changed ⇒ إعادة التشغيل = صفر كتابة زائدة). يُشغَّل
دورياً لأن `mtscraper` يحلّ هويات جديدة باستمرار.

**أولوية المصادر** — `link_source` مرتّب: `marinetraffic-detail` و
`marinetraffic-feed` مُغذّيان (لا يُدهسان)، و`ais-correlation` مستنتَج. وُسِّع
حارس `backend/vessel_crossref.py` ليحمي القيمة الجديدة أيضاً؛ بدونه كان الربط
المستنتَج يدهس كل رابط موثوق (لأن `marinetraffic-feed` لا يظهر عملياً أبداً).

**نتيجة 2026-08-15**: 128 → 1,559 رابطاً، و**707 سفينة صارت مرئية في المصدرين
معاً** — أي أن عرض MarineTraffic × AIS صار join مباشراً.

`scraplan-offsite.timer` (نسخة خارج المضيف، BACKUP-EXT) — **يومياً 04:15**، بعد
النسخة المحلية 03:00 وقبل سحب العودة 05:30. ينسخ نسخة `pg_dump` طازجة **وملف
الحقبة 3.48GB مرة واحدة** إلى `serverq@10.10.20.57` عبر SSH، ولا يعتمد أي بايت
إلا بعد قراءة `sha256` **من الوجهة** ومطابقته. **ينسخ ولا ينقل** — لا يُحذف شيء
من هذا المضيف سوى ملفه المؤقت. الاحتفاظ 7 نسخ، والحذف **بعد** نجاح الأحدث فقط.
التفاصيل المقيسة في `docs/PROJECT_STATE.md` §6.8، وفخّ مفتاح SSH في
[`OPERATIONS.md`](OPERATIONS.md).

⚠ **الاستعادة لم تُختبر بعد** — المُثبَت أن البايتات على الوجهة مطابقة، لا أنها
تُعيد بناء قاعدة عاملة. `pg_dump` يحذّر في كل دورة من قيود مفاتيح دائرية على
`continuous_agg` وأن الاستعادة قد تحتاج `--disable-triggers`. **نسخة غير مُختبرة
الاستعادة ليست نسخة مضمونة** — البند التالي.

`scraplan-windows.timer` (متجر ويندوز — العمق، WINSTORE) — **يومياً 04:45**، بعد
دفعة `serverq` وقبل سحب العودة. الوجهة `scraplanbak@10.0.0.113` على
`F:\scraplan` — قرص NTFS سعته 7,452 GB وفيه 6,964 GB حرّة.

⛔ **هذه الوحدة لا تأخذ `pg_dump` إطلاقاً.** تنسخ الملف الذي أنتجته وحدة 03:00
نفسه، لأن ثلاث عمليات dump في الليلة الواحدة تعني ~4 دقائق حِمل قاعدة و3.83 GB
كتابة زائدة لإنتاج بايتات موجودة أصلاً. والأهم: dump ثالث مستقل يعطي **بصمة
مختلفة**، فتصبح سجلّات الطبقات الثلاث غير قابلة للمقارنة. بنسخ الأثر نفسه، بصمة
`sha256` واحدة تُعرِّف نسخة الليلة **أينما وُجدت**.

⚠ **ولهذا تعتمد على وحدة 03:00**: إن لم تُنتج تلك نسخةً، تفشل هذه صراحةً ولا
تستبدلها بنسخة من عندها. هذا مقصود — التواريخ الأربعة المفقودة في تموز مرّت دون
أن يلاحظها أحد لأن لا شيء كان يعتمد عليها.

الاحتفاظ **90 نسخة** (‎90 × 3.83 GB ≈ 345 GB أي 5٪ من القرص)، مقابل 4 أيام
محلياً و7 على `serverq`. **الطبقات الثلاث لها ثلاث وظائف مختلفة**: المحلية
استعادة سريعة وتموت مع القرص، و`serverq` الناجية من فقد القرص، ومتجر ويندوز
يَنجو من **أسبوع سيّئ** لا من ليلة سيّئة — تلفٌ يُكتشف في اليوم العشرين
يُستعاد من هنا وحده.

⛔ **`F:` قرص ميكانيكي مفرد بلا RAID.** نسخة ثالثة، لا مكان آمن بذاته.

⛔ **فخّ ويندوز الذي يجب ألا يُعاد اكتشافه**: `ssh host 'cat > file'` **لا ينقل**
هناك (135 KB في 300 ثانية — الصدفة الافتراضية `cmd.exe` وstdin نصّي)، و`cmd.exe`
يبتلع الاقتباسات والأنابيب، ونقلٌ مقتول يترك ملفاً **مقفولاً** لا جزئياً فقط.
الجدول الكامل بالأدلة في [`docs/ENVIRONMENT.md`](../../docs/ENVIRONMENT.md) §3.

لإعادة بذر العمق بعد انقطاع: `sh scripts/backfill_windows.sh` — يدفع كل نسخة
محلية لا يملكها المتجر، بالعقد نفسه، ولا يحذف شيئاً محلياً.

`scraplan-reentry.timer` (سحب TIP من Space-Track، S2c) — **يومياً 05:30**. يحتاج
`SPACETRACK_USER`/`SPACETRACK_PASS` في `.env`. كان غائباً عن هذا الأمر، فبقيت
الوحدتان في `deploy/systemd/` **بلا تثبيت 10 أيام** بينما تدّعي الوثيقة الفضائية
أنه يعمل يومياً — الطبقة جُمّدت 10 أيام و21 ساعة. التحقّق بعد التثبيت:

```bash
systemctl --user start scraplan-reentry.service   # يدوياً مرة قبل الاعتماد عليه
psql "$DATABASE_URL" -c "select max(fetched_at) from satellite_reentry;"
```

**ملاحظة على الوحدة:** تحمل `After/Wants=network-online.target` وهو هدف **غير
موجود** في مدير المستخدم (`LoadState=not-found`)، فالتبعية **خامدة** — الخدمة تعمل
وتنتهي بنجاح، لكن لا يوجد ضمان جهوزية شبكة فعلي لتشغيل الإقلاع التعويضي
(`Persistent=true`). عميل Space-Track يملك backoff وretries فيُتوقّع أن ينجو.

## مصدر ADS-B Exchange (adsb_scraper) — مُستهلِك خارجي (اختياري)

`adsb_scraper` مشروع منفصل يكتب `state_vectors.jsonl` بصيغة scraplan
(`source=adsbexchange` + أعمدة التصنيف الجغرافي). scraplan لا يعدّله؛ بل
**يستهلكه** عبر نفس مسار الإدخال الحُقن idempotent (`ingest.py follow`).

وحدتان تشغيليتان (P4): `adsb-scraper-collector` (يقود الجامع الخارجي →
`state_vectors.jsonl`) و`scraplan-adsb-ingest` (يُدخِل الأرشيف إلى القاعدة).
كلاهما `Restart=always` ويعودان بعد انقطاع الكهرباء.

```bash
# في ~/.config/opensky/env:
#   ADSB_OUTPUT=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1
#   ADSB_ARCHIVE=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors.jsonl
systemctl --user enable --now adsb-scraper-collector scraplan-adsb-ingest
# إعادة بناء كاملة من الأرشيف (تشمل الملفات المُدوَّرة إن مررت مجلداً):
#   python ingest.py backfill --source adsbexchange --archive "$ADSB_ARCHIVE"
```

الخدمة تحفظ إزاحة القراءة تحت `scraplan/adsb_ingest_state/` (لا داخل مجلد
adsb_scraper)، وتتحمّل نمو الملف حيّاً وسطراً أخيراً ناقصاً، وإعادة القراءة
لا تُدخل صفوفاً مكرّرة (المفتاح `(icao24, ts, source)`).

## مؤقّتات التحليل البحري + وسم جودة GNSS

مؤقّتات مستخدم دورية فوق `vessel_states` (كلها `Persistent=true` و idempotent
recompute-and-replace):

```bash
systemctl --user enable --now \
    scraplan-gaps.timer scraplan-rendezvous.timer scraplan-identity.timer \
    scraplan-quality.timer scraplan-zone-dwell.timer
```

**تحقّق أنها مفعّلة فعلاً، لا أنها مذكورة هنا فقط:**

```bash
systemctl --user list-timers --all | grep scraplan   # يجب أن تظهر الستة
```

هذا السطر أُضيف لأن الانحراف حدث فعلاً: `scraplan-identity.timer` كان مذكوراً في
الأمر أعلاه لكنه بقي `disabled` على المضيف، فتوقّف كاشف A3 عن العمل **9 أيام و19
ساعة** بلا أي إشارة — لا خطأ ولا وحدة فاشلة، فقط طبقة تشيخ بصمت. اكتشافه جاء من
`max(computed_at)` في `maritime_events`، لا من systemd. **الحالة المرصودة هي
المرجع، لا التوثيق.**

`scraplan-zone-dwell.timer` (اقتحامات مناطق السلامة، ZONES-3) — كل **20 دقيقة**،
نفس نسق `rendezvous`. يكتب **المجموعة القوية فقط** (`safety_zone_intrusion`)؛
`standoff_dwell` يبقى تعليقياً حتى تبلغ منطقةٌ حدودَ الأساس، فلا يمكنه إغراق
`maritime_events`. الكلفة المقيسة: 1.8 ث CPU و39 م.ب ذروة.

`scraplan-quality.timer` (وسم جودة GNSS لكل نقطة، G2) — كل **15 دقيقة** تزايدياً
(`--since-minutes 45`): يسِم الصفوف المُدخَلة حديثاً، ويحلّ `teleport_return` عند
حافة النافذة بعد وصول الخلَف. النافذة 45 د = 3× دورة الـ15 د (تغطّي تأخّر الإدخال +
نقطة تداخل واحدة لكل MMSI) وداخل هامش الضغط 7 أيام، فلا يمسّ التشغيل التزايدي أيّ
قطعة مضغوطة.

إعادة الوسم التاريخية الكاملة (مرّة واحدة، أو بعد انقطاع أطول من نافذة التداخل) —
**يدوية**، تفكّ ضغط القطع المضغوطة ثم تُحدّث ثم تعيد ضغطها (سياسة الضغط 7 أيام لا
تُمَسّ):

```bash
python scripts/backfill_position_quality.py --decompress-chunks
# --dry-run للمعاينة دون كتابة · تشغيل كامل بلا العَلَم يتوقّف بأمان ويرشدك
# (تفادياً لـ DML على قطعة مضغوطة يتجاوز max_tuples_decompressed_per_dml_transaction)
```

الوسم مفهومياً **قبل** كواشف A1/A2/A3، لكن لا ترتيب `After=` مفروض بعد (يُقرَّر في
G3 عند استهلاك الأعمدة).

**دليل التشغيل الكامل (بدء/إيقاف/حالة/تعافي بعد انقطاع الكهرباء + وضع
headless/Cloudflare): [OPERATIONS.md](OPERATIONS.md).**
