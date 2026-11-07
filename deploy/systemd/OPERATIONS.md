# دليل التشغيل — Atheer RADAR (scraplan) — بقاء بعد انقطاع الكهرباء

الجهاز (ThinkCentre) معرّض لانقطاع الكهرباء. كل عملية طويلة تعمل كوحدة systemd
للمستخدم مع `Restart=always` و`WantedBy=default.target`، ومع `enable-linger`
تعود **كلها تلقائياً بعد الإقلاع** دون تدخل يدوي.

## الوحدات

| الوحدة | الدور | إعادة التشغيل |
|---|---|---|
| `scraplan-server` | واجهة API + الواجهة | on boot |
| `scraplan-collector` | جامع OpenSky (JSONL + مرآة DB) | always |
| `scraplan-military` | جامع airplanes.live | on-failure |
| `scraplan-maritime` | جامع AIS (AISStream) | always |
| **`adsb-scraper-collector`** | **جامع ADS-B Exchange (مشروع خارجي)** → `state_vectors.jsonl` | **always** |
| **`scraplan-adsb-ingest`** | **إدخال أرشيف adsb إلى القاعدة (idempotent، follow)** | **always** |
| `scraplan-backup.timer` | نسخ `pg_dump` ليلي | timer |
| **`scraplan-offsite.timer`** | **نسخة خارج المضيف إلى جهاز ثانٍ عبر SSH (04:15)** | **timer** |
| **`scraplan-windows.timer`** | **نسخة عميقة إلى متجر ويندوز `F:\scraplan` (04:45، احتفاظ 90)** | **timer** |
| `scraplan-rotate.timer` | تدوير السجلات الخام | timer |
| `scraplan-satellites.timer` | جلب TLE دوري | timer |

## الكواشف السلوكية — ⛔ كلٌّ منها مصنَّف صراحةً

⚠ **لم تكن هذه الجدولة موجودة هنا إطلاقاً قبل GNSS-REVIVE.** جدول الوحدات أعلاه لم يذكر أيّ
كاشف سلوكي، وقائمة التفعيل أدناه لم تُفعّل واحداً منها — فإعادة بناء المضيف من هذا الملف كانت
ستقيم الجامعات وتترك طبقة التحليل كلّها مطفأة وصامتة.

⛔ **القاعدة من اليوم: لا كاشف بلا تصنيف صريح.** إمّا `مؤقَّت` — وعندها طزاجته تُفحص — أو
`عند الطلب` — وعندها **يُوسَم في كل سلسلة زمنية بأن الغياب لا يعني غياب الظاهرة**. لا خانة
ثالثة، ولا استثناء صامت.

| الكاشف | الوحدة | الإيقاع | التصنيف |
|---|---|---|---|
| فجوات AIS (A1) | `scraplan-gaps.timer` | `*:0/15` | **مؤقَّت** |
| جودة الموضع (per-fix) | `scraplan-quality.timer` | `*:3/15` | **مؤقَّت — مُزاح عن :00 (2026-09-01)** |
| تغيّر الهوية (A3) | `scraplan-identity.timer` | `*:9/30` | **مؤقَّت — مُزاح عن :00 (2026-09-01)** |
| الالتقاءات (A2) | `scraplan-rendezvous.timer` | `*:6/20` | **مؤقَّت — مُزاح عن :00 (2026-09-01)** |
| المكوث في المناطق | `scraplan-zone-dwell.timer` | `*:8/20` | **مؤقَّت — مُزاح عن :00 (2026-09-01)** |
| **شذوذ GNSS (G4a)** | **`scraplan-gnss.timer`** | **`*:10/20`** | **مؤقَّت — منذ GNSS-REVIVE** |

## المهامّ التلخيصية

| المهمّة | الوحدة | الإيقاع | التصنيف |
|---|---|---|---|
| **الملخّص الجوّي اليومي (DASH-BOARD-3b)** | **`scraplan-air-daily.timer`** | **`01:37` يومياً** | **مؤقَّت** |

⛔ **`scraplan-air-daily` يحسب اليوم السابق المكتمل لا اليوم الجاري** — يوم جزئي مرسوم بجانب
أيام كاملة يُقرأ هبوطاً. و**الساعة 01:37 مختارة بالقياس**: بعد 20:00 محلياً كي يكون يوم UTC
مكتملاً، وبعيدة عن 03:00 (النسخ المحلي) و04:15 (الخارجي) و00:05 (التدوير) و00:20/06:20 (الأقمار)
و05:30 (العودة)، والدقيقة :37 لا تصادف أي كاشف — وبقيت كذلك بعد إزاحة 2026-09-01
(دقائق الكواشف الآن: 0/15 · 3/15 · 6/20 · 8/20 · 9/30 · 10/20 وعائلة mt على 5–14/15).
⛔ والكتابة **upsert لا insert**، فالتشغيل الفائت يُعوَّض بلا تكرار صفوف.

⛔ **`scraplan-gnss.timer` يعمل عند `:10/:30/:50` والإزاحة مقصودة**: هذا الكاشف يجمّع أحكام
`position_quality` التي يكتبها `scraplan-quality` (عند `:00/:15/:30/:45`)، فلا يجوز أن يسابق
الجولة التي تنتج مدخله؛ والإزاحة تُبعده أيضاً عن `rendezvous`/`zone-dwell` (عند `:00/:20/:40`)
فلا تجتمع أربعة كواشف على دقيقة واحدة من قرص مشترك.

`adsb-scraper-collector` يقود مجمّع ADS-B [Aerial Source (ADS)] في `/home/gis-syr/Desktop/scraplan/aerial_source_ads`
(حزمة `adsb_scraper` ويكتب `state_vectors.jsonl` مع `fsync` بعد كل دفعة لتفادي الفقدان)،
بينما `scraplan-adsb-ingest` يتابع الملف ويحقنه في TimescaleDB فور وصوله.
تم تثبيت الإعدادات في المسارات الموحدة:
```ini
# في ~/.config/opensky/env:
#   ADSB_ARCHIVE=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors.jsonl
#   ADSB_OUTPUT=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1
```

## التفعيل (مرة واحدة)

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
# الاعتمادات في ~/.config/opensky/env (chmod 600):
#   OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET / OPENSKY_DB_URL
#   ADSB_ARCHIVE=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1/state_vectors.jsonl
#   ADSB_OUTPUT=/home/gis-syr/Desktop/scraplan/aerial_source_ads/data_p1
systemctl --user daemon-reload
systemctl --user enable --now \
    scraplan-server scraplan-collector scraplan-military scraplan-maritime \
    adsb-scraper-collector scraplan-adsb-ingest \
    scraplan-backup.timer scraplan-rotate.timer scraplan-satellites.timer \
    scraplan-offsite.timer scraplan-windows.timer scraplan-reentry.timer \
    scraplan-gaps.timer scraplan-quality.timer scraplan-identity.timer \
    scraplan-rendezvous.timer scraplan-zone-dwell.timer scraplan-gnss.timer \
    scraplan-air-daily.timer
loginctl enable-linger   # تعمل دون جلسة مفتوحة وتعود بعد الإقلاع
```

> ⚠ **`scraplan-offsite.timer` يحتاج مفتاح SSH على القرص — لا وكيلاً.** الوجهة
> `serverq@10.10.20.57` تقبل `~/.ssh/serverq`، وهو **ليس** اسماً افتراضياً
> (`id_*`) ولا يغطيه مدخل في `~/.ssh/config` لهذا العنوان. لذلك `ssh` لا يعرضه
> تلقائياً: ينجح تفاعلياً **فقط** لأن وكيل GNOME-Keyring يحمله.
>
> ```bash
> env -u SSH_AUTH_SOCK ssh -o BatchMode=yes serverq@10.10.20.57                        # Permission denied
> env -u SSH_AUTH_SOCK ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/serverq …  # OK
> ```
>
> السكربت يثبّت المفتاح صراحةً، والوحدة تحمل `UnsetEnvironment=SSH_AUTH_SOCK` لأن
> `systemctl --user show-environment` **يحمل** `SSH_AUTH_SOCK` فعلاً — أي أن الوحدة
> كانت سترث الوكيل وتنجح في الاختبار، ثم تفشل كل ليلة بعد أول إقلاع بلا دخول
> رسومي. **إن نُقلت الوجهة أو دُوّر المفتاح، عدّل `OFFSITE_HOST`/`OFFSITE_SSH_KEY`
> ثم شغّل دورة يدوية واحدة قبل الاعتماد عليها:**
>
> ```bash
> systemctl --user start scraplan-offsite.service
> ssh -o IdentitiesOnly=yes -i ~/.ssh/serverq serverq@10.10.20.57 \
>     'cat scraplan-offsite/LAST_SUCCESS; cd scraplan-offsite && sha256sum -c CURRENT.sha256'
> ```

> ⛔ **`scraplan-windows.timer` — نفس فخّ المفتاح، وفخّان إضافيان خاصان بويندوز.**
> الوجهة `scraplanbak@10.0.0.113` تقبل `~/.ssh/winstore` فقط، مثبَّتاً صراحةً
> بـ`IdentitiesOnly=yes`، والوحدة تحمل `UnsetEnvironment=SSH_AUTH_SOCK`.
>
> **الفخّ الأول — `Match Group administrators`.** ملف `sshd_config` على الوجهة يحوّل
> مسار المفاتيح لأي حساب مسؤول إلى
> `__PROGRAMDATA__/ssh/administrators_authorized_keys`، فمفتاح في
> `~/.ssh/authorized_keys` **يُتجاهَل بصمت**.
>
> **الفخّ الثاني — الحساب الجديد بلا ملف تعريف.** `scraplanbak` أُنشئ للخدمة ولم
> يُسجَّل دخوله قط، فلم يكن له مُدخل في `ProfileList` ولم يستطع sshd حلّ `~` أصلاً:
> مفتاحٌ صحيحٌ تماماً رُفض بـ`Permission denied (publickey)` بلا سبب ظاهر. الحلّ
> مسار مستقل عن ملف التعريف، مثبَّت في `sshd_config`:
>
> ```
> Match User scraplanbak
>        AuthorizedKeysFile __PROGRAMDATA__/ssh/scraplanbak_authorized_keys
>        PasswordAuthentication no
>        KbdInteractiveAuthentication no
> ```
>
> ⚠ **`PasswordAuthentication no` وحدها تجميلية على ويندوز** — فـ
> `keyboard-interactive` مسار ثانٍ إلى فحص كلمة المرور نفسه. الاثنان معاً هما ما
> يجعل الحساب **مفتاحاً فقط**. التحقّق بالمقارنة، لا بالتأكيد:
>
> ```bash
> ssh -o BatchMode=yes -o PubkeyAuthentication=no scraplanbak@10.0.0.113 true
> #   -> Permission denied (publickey).                       ✅ مفتاح فقط
> ssh -o BatchMode=yes -o PubkeyAuthentication=no A001@10.0.0.113 true
> #   -> Permission denied (publickey,password,keyboard-interactive).   ⛔ للمقارنة
> ```
>
> **ولماذا حساب منفصل أصلاً؟** الحساب المتاح للجهاز هو `A001`، وهو **مسؤول محلي**
> بمصادقة كلمة مرور مفعّلة. تخزين بيانات اعتماده في مؤقّت كان سيضع تحكّماً إدارياً
> كاملاً بجهاز يحوي أرشيفات مشاريع أخرى داخل ملف على هذا المضيف. `scraplanbak`
> **ليس مسؤولاً** (مُثبَت بتعداد المجموعة)، و**ممنوع من الكتابة على كل `F:` عدا
> `F:\scraplan`** (مُثبَت بمحاولة كتابة في كل مجلد على القرص).
>
> ```bash
> systemctl --user start scraplan-windows.service   # يدوياً مرة قبل الاعتماد عليه
> ssh -o IdentitiesOnly=yes -i ~/.ssh/winstore scraplanbak@10.0.0.113 \
>     'powershell -NoProfile -Command "Get-Content F:\scraplan\LAST_SUCCESS"'
> ```

> **ملاحظة headless/Cloudflare (مؤكَّدة 2026-07-18):** `adsb-scraper-collector`
> يعمل headless بنجاح على هذا الجهاز. Cloudflare يردّ 403 على استراتيجية
> `browser_evaluate`، لكن استراتيجية `interception` البديلة في adsb_scraper تنجح
> headless — الدورات تكتمل وتُجمع الطائرات دون سطح مكتب. لا حاجة لوضع headed.
> إن شدّدت Cloudflare مستقبلاً وحجبت الاستراتيجيتين معاً، شغّل الجامع headed عبر:
> (أ) خدمة *مستخدم* تحت جلسة رسومية (`DISPLAY` موروث، تتطلب auto-login للصمود بعد
> الإقلاع)، أو (ب) شاشة وهمية مستقلة عن سطح المكتب: `sudo apt install xvfb` ثم
> `xvfb-run -a … --no-headless` (تصمد بعد الإقلاع بـ enable-linger وحدها).
> التفاصيل داخل ملف الوحدة.

## التشغيل/الإيقاف/الحالة

```bash
systemctl --user status scraplan-adsb-ingest         # الحالة + آخر السجل
systemctl --user restart scraplan-adsb-ingest        # إعادة تشغيل
systemctl --user stop adsb-scraper-collector         # إيقاف
journalctl --user -u scraplan-adsb-ingest -f         # متابعة السجل حياً
systemctl --user list-units 'scraplan-*' 'adsb-*'    # نظرة شاملة
```

## ماذا يحدث عند انقطاع الكهرباء (نموذج التعافي)

1. **الأرشيف هو الحقيقة الأرضية.** الجامعان يكتبان JSONL بـ `fsync` وإصلاح ذاتي
   للسطر الأخير الناقص عند الفتح — الإقلاع يستأنف الكتابة إلى JSONL صالح **دون
   تنظيف يدوي**.
2. **الإدخال idempotent + إزاحة مثابرة.** `scraplan-adsb-ingest` يحفظ إزاحة
   القراءة في `adsb_ingest_state/.ingest_state.json` (تحت scraplan، تنجو من
   الإقلاع). بعد العودة يُكمل من الإزاحة؛ وحتى لو أعاد قراءة أسطر، المفتاح الطبيعي
   `(icao24, ts, source)` مع `ON CONFLICT DO NOTHING` يمنع أي تكرار.
3. **القاعدة مشتقة وقابلة لإعادة البناء.** لو تعطّلت القاعدة، الجامعون يواصلون
   الأرشفة؛ عند عودتها يستأنف follow، و`ingest.py backfill` يعوّض أي فجوة من
   الأرشيف الخام.

## التحقق من التعافي

```bash
# 1) استئناف المُدخِل من الإزاحة دون إعادة إدخال:
BEFORE=$(psql "$OPENSKY_DB_URL" -tAc "SELECT count(*) FROM state_vectors WHERE source='adsbexchange'")
systemctl --user restart scraplan-adsb-ingest
sleep 8
AFTER=$(psql "$OPENSKY_DB_URL" -tAc "SELECT count(*) FROM state_vectors WHERE source='adsbexchange'")
echo "before=$BEFORE after=$AFTER  (يجب أن يتساوَيا إن لم تصل أسطر جديدة)"

# 2) الإزاحة محفوظة وتنجو من إعادة التشغيل:
cat /home/gis-syr/Desktop/scraplan/adsb_ingest_state/.ingest_state.json

# 3) الجامع يستأنف كتابة أرشيف صالح بعد قتل قاسٍ:
systemctl --user kill -s SIGKILL adsb-scraper-collector
systemctl --user start adsb-scraper-collector
tail -c 200 "$ADSB_OUTPUT/state_vectors.jsonl" | python3 -c 'import sys,json;[json.loads(l) for l in sys.stdin if l.strip()];print("آخر الأسطر JSONL صالحة")'
```
