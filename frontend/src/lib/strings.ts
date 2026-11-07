/**
 * كل مفردات الواجهة في وحدة واحدة — المصدر الوحيد للنصوص.
 * الأرقام والرموز الملاحية (ICAO24، النداءات، Z، الإحداثيات) تبقى لاتينية
 * لأنها بيانات لا مفردات.
 */

/**
 * ⛔ THE RANK WORDS — ONE MAP, because there used to be three.
 *
 * Measured before RANK-NAME: the SAME wire value rendered as «مرتفع» on the alert badge
 * (`dashHome.severity`), «عالٍ» in the vessel panel (`vessel.rvRank`, which had THREE
 * consumers to the badge's one), and «يستدعي الانتباه» on the triage tile — three
 * vocabularies for one axis on one dashboard, with «متوسّط» / «متوسط» differing by a shadda.
 * That divergence is itself part of why the labels read as unclear, and two maps holding
 * matching values would simply drift again.
 *
 * ⚠ The tile keeps its own ACTION wording on purpose (`tileHigh` / `tileMedium`): a tile
 * summarises a queue and may say what to do, while a per-row badge sits on the danger axis
 * and may not — that separation is what `importance` exists to protect.
 *
 * ⛔ `info` lives only on the alert badge: the vessel panel's ranks are `high|medium|low`
 * (a `suspicion_rank` / `composite_rank`), and there is no informational tier there.
 */
const RANK_WORDS = {
  high: "مرتفع",
  medium: "متوسّط",
  low: "منخفض",
} as const;

export const T = {
  brandSub: "مراقب المجال الجوي الحي",

  kpi: {
    contacts: "الأهداف",
    airborne: "في الجو",
    ground: "على الأرض",
    maxAlt: "أقصى ارتفاع (قدم)",
  },

  /* ---------- DASH-BAND: the freshness bar under the map seat ----------
   * ⛔ WHY THIS EXISTS. The square seat (--map-ratio-max: 1) leaves a band under the map —
   * measured 245px at 1440×900 and 111px at 1900×950 before this item — which the token
   * comment explicitly reserved as «content first, height second»: --below-map-h stays 0
   * until real content claims it. This bar is that content, built ONLY from freshness the
   * App already holds (air feed status + snapshot ts, maritime per-source vintages,
   * satellite fetch time). The word carries the verdict; the dot is a secondary channel.
   * ⚠ Compact words on purpose — the bar is one line tall. */
  seatFresh: {
    aria: "حداثة مصادر المجالات الثلاثة",
    live: "حيّ",
    polling: "استطلاع",
    error: "خطأ",
    connecting: "اتصال…",
    // sea/space rows read the ALERT feed's per-domain vintage — their own feeds are gated
    // off on this surface (see SeatFreshness's header), so a liveness word is not earned.
    alertAge: (a: string) => `آخر تنبيه منذ ${a}`,
    none: "بلا بيانات",
  },

  feed: {
    section: "البث",
    ws: "بث مباشر (WebSocket)",
    polling: "استطلاع كل 30 ثانية",
    error: "خطأ في التغذية — إعادة المحاولة",
    connecting: "جارٍ الاتصال…",
    snapshot: "اللقطة",
    source: "المصدر",
    sourceDb: "قاعدة البيانات",
    sourceArchive: "الأرشيف",
    ceiling: "السقف",
  },

  search: {
    section: "بحث / ترشيح",
    placeholder: "نداء أو ICAO24…",
    any: "الكل",
    air: "جوي",
    ground: "أرضي",
    minFt: "أدنى (قدم)",
    maxFt: "أقصى (قدم)",
    showing: (x: number, y: number) => `عرض ${x} من ${y} هدفاً`,
    total: (n: number) => `الأهداف: ${n}`,
    clear: "مسح",
    ariaQuery: "بحث بالنداء أو ICAO24",
    ariaMin: "أدنى ارتفاع بالقدم",
    ariaMax: "أقصى ارتفاع بالقدم",
    ariaGroundGroup: "ترشيح أرضي/جوي",
  },

  list: {
    section: "الأهداف",
    callsign: "النداء",
    icao: "ICAO",
    alt: "الارتفاع",
    spd: "السرعة",
    empty: "لا أهداف مطابقة",
    groundTag: "أ", // وسم مضغوط: على الأرض
    aria: "قائمة الأهداف",
  },

  strip: {
    section: "الهدف المختار",
    empty: "لا هدف مختاراً — اضغط طائرة على الخريطة أو صفاً في القائمة",
    baroAlt: "الارتفاع الضغطي",
    speed: "السرعة",
    track: "المسار",
    vRate: "معدل الصعود",
    squawk: "سكواك",
    geoAlt: "الارتفاع الهندسي",
    position: "الموقع",
    origin: "بلد التسجيل",
    lastSeen: "آخر ظهور",
    gnd: "أرضي",
    stripWord: "شريط",
    clear: "مسح",
    // enrichment
    routeSection: "خط السير",
    registration: "التسجيل",
    aircraftType: "الطراز",
    operator: "المشغّل",
    photoBy: "تصوير",
    loadingEnrich: "جارٍ جلب بيانات الرحلة…",
    routeNote: "خط سير معتاد لهذا النداء — استدلالي",
    copyLink: "نسخ الرابط",
    copied: "نُسخ ✓",
    implausible: "قراءة غير موثوقة من الترانسبوندر",
    // geo classification (P3) — shown when the row carries it
    country: "داخل دولة",
    nearBorder: "قرب حدود",
    posQuality: "جودة الموقع",
    /* ---------- PAYLOAD-PROV: the attribution the panel was withholding ----------
     * ⛔ These are not cosmetic. The project's rule is «a source and a date for every fact»,
     * and this surface printed a classified country and a position-quality verdict WITHOUT
     * saying who classified it. A verdict without its author is the failure the rule exists
     * to prevent — so these are a principle fix, ahead of any feature. */
    geoSource: "مُصنِّف الموقع",
    posSource: "مصدر الإحداثيات",
    fetchedAt: "زمن الجلب",
    /* ⚠ `position_source` arrives as an OpenSky integer code, not a name. Mapped here rather
     * than printed raw: «0» tells the analyst nothing and looks like a measurement. An
     * unmapped code falls through as `#<n>` — visibly a code, never a silent blank. */
    posSourceNames: {
      "0": "ADS-B", "1": "ASTERIX", "2": "MLAT", "3": "FLARM",
    } as Record<string, string>,
    quality: {
      confirmed: "مؤكد", suspect: "مشكوك", no_position: "بلا موقع",
    } as Record<string, string>,
  },

  views: { live: "مباشر", maritime: "بحري", satellites: "أقمار", monitor: "مراقبة", stats: "إحصاءات" } as Record<string, string>,

  // Monitoring dashboard (D2): per-domain freshness strip + layer-visibility control.
  dashboard: {
    freshnessSection: "حداثة البيانات",
    layersSection: "الطبقات",
    air: "جوي",
    sea: "بحري",
    space: "فضائي",
    // air liveness (reuses the feed transport)
    airWs: "مباشر (WebSocket)",
    airPolling: "استطلاع",
    airError: "خطأ في التغذية",
    airConnecting: "اتصال…",
    // sea liveness — the honesty case
    seaLive: (n: number) => `بث حي · ${n} سفينة`,
    seaFrozen: (d: string) => `مجمّد · آخر بيانات ${d}`,
    seaDark: "AIS متوقّف — لا بيانات",
    // space is always computed
    spaceComputed: "محسوب محلياً (SGP4)",
    tleAge: (a: string) => `عناصر TLE: ${a}`,
    age: (a: string) => `منذ ${a}`,
    // layer toggles (sub-overlays nested under their domain)
    mil: "عسكري",
    adsb: "ADS-B",
    // ⚠ RANK-NAME: «GNSS» removed from the LABEL — the abbreviation is jargon to an operator.
    // ⛔ The KEY stays `gnss`: it is the wire/prop name (GnssEvent, gnssAnomalies, the events
    // endpoint), and only the displayed value changes. `adsb` above keeps its abbreviation
    // because ADS-B has no plain-Arabic equivalent that names the same thing.
    gnss: "شذوذ إشارة الملاحة",
    layerHint: "الطبقات المفعّلة تظهر على الخريطة؛ نتيجة البحث تظهر دائماً",
    frozenTag: "مجمّد",
    // D4 area-search — mode toggle + box + window + domain filter
    modeEntity: "بحث كيان",
    modeArea: "بحث منطقة",
    /* DASH-2g′ — the third workspace mode: the three domains' own detail, moved here off
     * the map tabs. Named «تفصيلي» and not «بحث …» because it BROWSES what is already
     * streaming instead of asking a question. */
    modeDetail: "تفصيلي",
    // DASH-4b — the map as its own destination, not a permanent tenant of every surface.
    modeMap: "الخريطة",
    // DASH-5 — the shared case file as a destination.
    modeCase: "الملفّ",
    /* the domain picker inside the detail mode */
    domainAria: "مجال التفصيلي",
    domainAir: "جوّي",
    domainSea: "بحري",
    domainSpace: "فضائي",
    /* ⚠ The four tiles count the AIRCRAFT feed, whichever domain is selected — their source
     * is the ungated LiveFeed. Naming the population is the §6.11 rule; an unlabelled count
     * beside a vessel list would read as a vessel count. */
    tilesPop: "تعدّ التغذية الجوية (OpenSky) — لا تتغيّر بتغيّر المجال المختار",
    // the toggle's group label. ⚠ It used to be `modeArea`, which announced the whole
    // toggle as "area search" to a screen reader (corrected in DASH-2c′).
    modeAria: "وضع اللوحة",
    drawBox: "ارسم صندوقاً",
    drawing: "ارسم على الخريطة…",
    redraw: "أعد الرسم",
    areaPrompt: "ارسم صندوقاً على الخريطة لعرض ما عبَره",
    windowLabel: "النافذة",
    windows: { "1": "ساعة", "6": "6 ساعات", "24": "24 ساعة", "168": "7 أيام" } as Record<string, string>,
    domainsLabel: "المجالات",
    areaAir: "جوي", areaSea: "بحري", areaSpace: "فضائي",
    resultsAir: (n: number) => `جوي (${n})`,
    resultsSea: (n: number) => `بحري (${n})`,
    resultsSpace: (n: number) => `فضائي (${n})`,
    areaEmpty: "لا شيء عبَر الصندوق في النافذة",
    areaSpaceCost: "المجال الفضائي يُعاد حسابه (SGP4) — النوافذ الطويلة أبطأ؛ ألغِ تحديده لبحث جوي/بحري سريع",
    // UI-1 — Arabic counterpart of api.AREA_CAVEAT (was rendered in English)
    areaCaveat:
      "كيانات عبَر مسارها الصندوق خلال النافذة. الحداثة مختلطة: الجو حي، والبحر مجمّد (آخر موضع AIS حقيقي — انظر شريط الحداثة). قائمة الفضاء تعتمد أخذ عيّنات SGP4 — قد يُفوَّت عبورٌ قصير لصندوق صغير جداً بين عيّنتين. كل صف نتيجة استكشاف؛ افتحه لتفصيل الكيان الكامل.",
    /* ⛔ UI-2 — the draw state's guidance and its way out. Before this, the map in draw mode
     * carried NO instruction and no cancel: `MonitorDetail` is entity-mode only, so an analyst who
     * pressed «ارسم صندوقاً» got a bare map. ⚠ Both strings state only what the interaction does —
     * no promise about what will be found, and no invented capability. */
    drawHint: "اسحب على الخريطة لرسم صندوق",
    drawCancel: "إلغاء",
    /* ⛔ `backToResults` DELETED by UI-2 with the `.map-back` button it labelled. The map is no
     * longer summoned over the results, so there is no state to return from. */
  },

  /* ---------- DASH-2e — the country card (the home's lead panel) ---------- */
  country: {
    section: "بطاقة الدولة",
    pickerAria: "اختيار الدولة",
    names: {
      SY: "سوريا", TR: "تركيا", IQ: "العراق", JO: "الأردن",
      LB: "لبنان", IL: "إسرائيل", CY: "قبرص",
    } as Record<string, string>,
    // ⚠ §6.11 warning 1: never print a scoped number without saying whose scope it is.
    scopedFor: (name: string) => `الأرقام أدناه مُنطَّقة على ${name} وحدها`,
    unscopedWarn: "⚠ الأرقام غير مُنطَّقة — لم تُطبَّق دولة",
    numbersAria: "أرقام الدولة",

    /* ⚠ §6.11 warnings 2 and 3 live in these labels. Each number names its own
     * population, because two of the three would otherwise be read as something they
     * are not. */
    airLabel: "طائرات",
    // warning 3: this is the OpenSky population, a different community from /adsb's.
    // The label says so, and no /adsb total is placed beside it — adding the two would
    // manufacture a number that does not add up.
    airPop: "مجتمع OpenSky",
    airTitle:
      "الطائرات المرصودة في مجال هذه الدولة من مجتمع OpenSky. ⚠ مجتمع مختلف عن /adsb (adsbexchange) — الرقمان لا يُجمعان ولا يُقارنان مباشرةً.",
    seaLabel: "سفن",
    seaPop: "AIS + vessel_states",
    seaTitle: "السفن التي سجّلت مواقع في مياه هذه الدولة داخل النافذة.",
    // warning 2: NOT "tracked satellites" — the catalogue has no country.
    spaceLabel: "أجسام لها ممرّ",
    spacePop: "ممرّ فوق الدولة في النافذة",
    spaceTitle:
      "أجسام فضائية لها ممرّ محسوب فوق هذه الدولة داخل النافذة. ⚠ ليست «أقمار متتبَّعة» — كتالوج العناصر لا يحمل دولة، والعدّ هنا ناتج حساب ممرّات لا حصر كتالوج.",

    /* ⚠ the lesson from 2c′: "not observed" and "does not exist" are different states. */
    emptyForCountry: (name: string) => `لا أحداث مسجَّلة لـ${name} في هذه النافذة`,
    emptyNote:
      "هذا يعني أن الكواشف لم ترصد شيئاً في هذه المدّة — لا أنه لا يوجد شيء. وسّع النافذة أو راجع حداثة كل مجال.",
    eventsSection: "الأحداث",
    // the axis note — importance is a display order, never a threat score
    orderedByImportance: "مرتّبة بالأهمية للعرض (ليست درجة تهديد)",
  },

  /* ---------- DASH-2f — the entity payload panel (the home's LEFT panel) ---------- */
  entityPayload: {
    kinds: { vessel: "سفينة", aircraft: "طائرة", satellite: "قمر" } as Record<string, string>,
    close: "إغلاق",
    loading: "جارٍ جلب الحمولة…",
    /* ⛔ PAYLOAD-PROV — the evidence behind a «live» claim. `state: "live"` means the fix is
     * inside the 180 s window; the snapshot's own age is what makes that checkable, and it was
     * arriving unrendered (0 references, measured). A freshness claim with no date is exactly
     * the omission this commit exists to close. */
    snapshotAge: (a: string) => `اللقطة المستعملة عمرها ${a}`,
    /* PAYLOAD-ENRICH — the enrichment layer's own two states, never a silent blank. */
    enrichLoading: "جارٍ جلب بيانات الإثراء…",
    enrichUnavailable: "لا بيانات إثراء متاحة لهذا الكيان — المزوّد لم يُعِد شيئاً.",
    /* DASH-2g″ — the map became a second source of the SAME panel, so the invitation names
     * both. ⛔ It must not promise more than one of them delivers: the panel is identical
     * whichever way the entity was chosen, which is exactly why one sentence covers both. */
    noSelection: "اضغط كياناً — في بطاقة الدولة أو على الخريطة — لتظهر حمولته الحية هنا.",
    /* ⛔ honesty state 1 */
    noLiveKey: "لا مفتاح حمولة لهذا الكيان",
    noLiveKeyWhy:
      "العقد يسمح بغياب المفتاح: لم يُسمع شيء من أي من معرّفات هذا البدن، فلا توجد حمولة حية تُفتح. هذا غياب صادق لا خطأ.",
    /* ⛔ honesty state 2 */
    notTransmitting: "لا حمولة حية الآن",
    notTransmittingWhy: (k: string) =>
      `المفتاح ${k} صحيح لكنه غير موجود في اللقطة الحية الحالية. «لم يُرصد» ليس «لا يوجد» — وسّع النافذة أو راجع حداثة المجال.`,
    /* ⛔ honesty state 3 */
    unavailTitle: "غير متاح لكل كيان على حدة:",
    unavailTrack:
      "المسار الأرضي — يُحسب محلياً بـSGP4 ولا يخدمه العقد، فلا يُحسب هنا لتعبئة اللوح.",
    /* ⛔ DASH-2f′ — `unavailVesselLive` was DELETED, not reworded.
     * It named four fields (الاتجاه · الوجهة · حالة الملاحة · الغاطس) as "not reachable
     * per entity". That was true of 2f and is now FALSE: /maritime/vessel serves all four.
     * Keeping it would have turned an honest temporary caveat into a standing lie — and a
     * hard-coded list cannot tell a vessel that withholds its draught from one that
     * broadcasts it. `notReported*` below replaces it with the backend's PER-VESSEL
     * `not_reported`, so the claim is measured for each hull instead of asserted for all. */
    /* ⛔ DASH-2f′ — the two states of the single-vessel contract */
    stateLastKnown: "آخر معلوم — لا يبثّ الآن",
    stateLastKnownWhy: (age: string, fixes: number | null) =>
      `هذا موضع حقيقي مسجَّل منذ ${age}، وليس موضعاً حالياً. المفتاح معروف لكن السفينة خارج نافذة الحياة${
        fixes !== null ? ` (${fixes} رصدة مخزَّنة)` : ""
      }.`,
    identityAsOf: (ts: string) =>
      `الهوية الثابتة (الاسم·IMO·النداء·النوع) من طابع آخر: ${ts} — رسائل الموضع في AIS لا تحمل البيانات الثابتة، فالطابعان مختلفان ولم يُدمجا.`,
    /* ⛔ per-vessel absence, computed by the backend — replaces the hand-written list */
    notReportedTitle: "لم يبثّها الجهاز:",
    notReportedWhy:
      "حقول فارغة لأن السفينة لم ترسلها — لا لأنها مفقودة من القاعدة. «لم يُرصد» ليس «لا يوجد».",
    /* Field names arrive from the backend as wire keys. Translated here, with the RAW KEY
     * kept as the fallback: if the backend adds a reportable field this map does not know,
     * it must still appear — an untranslated name is a small blemish, a silently dropped
     * absence is a lie. */
    notReportedNames: {
      lat: "خط العرض", lon: "خط الطول", speed: "السرعة", course: "المسار",
      heading: "الاتجاه", nav_status: "حالة الملاحة", timestamp: "طابع الرصد",
      imo: "IMO", name: "الاسم", callsign: "النداء", ship_type: "النوع",
      ship_type_code: "رمز النوع", destination: "الوجهة", draught: "الغاطس",
    } as Record<string, string>,
    /* ⛔ DASH-2f′ — 404 is an outcome with a name, never a bare error */
    unknownKey: "مفتاح غير معروف",
    unknownKeyWhy: (k: string) =>
      `لم يُسمع بـ${k} قط في هذا المخزن — لا رصدة واحدة. هذه حالة صريحة لا خطأ في الجلب، ولا تعني أن السفينة غير موجودة في العالم.`,
    altTitle: "مفاتيح سابقة لهذا البدن:",
    altWhy: "سياق تاريخي (إعادة تسجيل/تغيير علم) — ليست بديلاً عن المفتاح الحالي ولا تُجلب.",
  },

  /* ---------- DASH-2d″ — the home destination: map as the hero, two floating panels ---------- */
  home: {
    // the right-hand (RTL) lead panel — carries DashboardHome until 2e's country card
    leadAria: "لوح الرصد الرئيسي",
    // the left-hand (RTL) entity panel — a STRUCTURAL placeholder for 2f
    entityAria: "لوح حمولة الكيان",
    entityTitle: "حمولة الكيان",
    // ⚠ Says what is true TODAY, not what is planned. The click-through is not wired yet,
    // so promising «اضغط كياناً لعرض حمولته» would be a false affordance — the panel would
    // invite an interaction that does nothing. No placeholder data either: an empty shell
    // that admits it is empty beats a convincing fake.
    entityNotWired: "لم يُوصَل بعد — سيحمل هذا اللوح حمولة الكيان المُختار على الخريطة.",
  },

  // UI-1 — the shared caveat control (Arabic shown; the authoritative backend
  // methodology stays one disclosure away, and drift is marked).
  caveat: {
    showSource: "المنهجية (EN)",
    hideSource: "إخفاء المنهجية",
    sourceAria: "النص المنهجي الأصلي من الخادم",
    drift: "⚠",
    driftTitle: "تغيّر النص المنهجي في الخادم — قد تكون الترجمة العربية غير محدَّثة؛ اقرأ النص الأصلي.",
  },

  /* DASH-7 — the console: one command line, one status line, three surfaces.
   * ⛔ THE SURFACE NAMES ARE THE PROPOSAL'S OWN, not translations of the old mode labels:
   * «بحث كيان» named the ACT of searching, and the fourth proposal names the SURFACE the
   * analyst is on. One field searches from anywhere now, so a surface called "search" would
   * describe a control that is no longer anywhere else. */
  console: {
    surfMon: "المراقبة",
    surfEnt: "الكيان",
    surfMap: "الخريطة",
    surfAria: "السطح",
    // ⚠ names the four identifier spaces the field routes on, in the order routeQuery tests
    queryHint: "MMSI · IMO · اسم · نداء — يُوجَّه حسب صيغة المُعرّف",
    windowLabel: (h: number) =>
      h === 1 ? "ساعة" : h === 6 ? "٦ ساعات" : h === 24 ? "٢٤ ساعة" : h === 168 ? "٧ أيام" : `${h} ساعة`,
    caseTitle: "الملفّ المشترك — ما حُفظ للتقرير",
    statAlerts: "تنبيه",
    // the status line's domain words: the POPULATION, never "tracked"
    domain: { air: "طائرة", sea: "سفينة", space: "جسم" } as Record<string, string>,
    // the routing verdict, shown beside the field so the routing is stated not silent
    routed: { vessel: "سفينة", aircraft: "طائرة", satellite: "قمر", airport: "مطار" } as Record<string, string>,
    caseEmpty: "الملفّ فارغ. افتح كياناً واحفظه للتقرير.",
  },

  // ENHANCE — the ONE shared time-window control (presets + custom), used across all
  // entity searches + area-search. Reuses dashboard.windows for the preset labels.
  timeWindow: {
    label: "النافذة الزمنية",
    custom: "مخصّص",
    customAria: "نافذة مخصّصة بالساعات",
    // per-entity honesty notes
    airportNote: (mx: number) => `يوسّع اكتشاف الهبوط حتى ${mx} ساعة`,
    trackNote: "يحدّد امتداد المسار المعروض",
    satelliteNote: "لا مواضع مخزّنة — المسار الأرضي يُحسب لمدار واحد (SGP4)",
  },

  // IMO-2 — the spine-integrity badge (migration 035): a caveat on a lead's IMO,
  // shown wherever a hull/vessel is presented. Never hides the lead. 'valid' shows no
  // badge. Compact "IMO: <issue>" reusing the existing badge idiom.
  spine: {
    checksum_fail: "IMO: تحقّق خاطئ",
    malformed: "IMO: مشوّه",
    contested: "IMO: متنازع",
  } as Record<string, string>,

  // D5 — the monitor's RESTING state: a live dashboard (domain stats + alert cards)
  // that fills the workspace when no search/area is active. Severity is per-family
  // and honest; each card shows its basis + freshness (mixed liveness).
  /* DASH-5 — the shared case file. ⛔ Every CLAIM in it is the alert's own snapshotted text;
   * these are labels and the report's own framing. */
  caseFile: {
    aria: "الملفّ المشترك",
    loading: "…",
    empty: "الملفّ فارغ. افتح كياناً واحفظه للتقرير.",
    count: (n: number) => `${n} عنصراً محفوظاً`,
    export: "تصدير مسوّدة التقرير",
    remove: "إزالة",
    note: "ملاحظة",
    collected: "حُفظ في",
    signalAt: "زمن الإشارة",
    /* ⚠ NEUTRAL WORDING. Leaving the feed's window is not a verdict — the item was not
     * disproven, and the label must not imply it was. */
    live: "ما زال في التغذية",
    notLive: "خارج النافذة الحالية",
    reportTitle: "مسوّدة تقرير — أثير رادار",
    reportAt: "حُرّرت في",
    reportCount: "عدد العناصر",
    /* ⛔ THE STANDING RESERVATION, restated once at the foot. The per-item caveats say what each
     * signal does not mean; this says what the SET does not mean. */
    reportFoot: "⚠ كل عنصر أعلاه لقطة وقت حفظه، لا قراءة حيّة. التحفّظات جزء من الدليل لا حاشية "
      + "عليه. واجتماع العناصر في ملفّ واحد اختيار المحلّل، لا استنتاجاً من النظام.",
  },

  /* DASH-4b-3 — the entity surface's chrome. ⛔ Every CLAIM on that surface is the alert's
   * own `headline` / `why` / `caveat`, rendered verbatim; these are labels only. */
  entityDetail: {
    /* ⚠ THE HEADING TELLS THE READER HOW TO READ THE PICTURE, which is the proposal's own
     * wording. A lane stack without it is a decoration; with it, the vertical alignment is
     * the evidence for the «متزامنة» claim stated above it in prose. */
    lanesHead: "الأنماط عبر النافذة — التزامن يُقرأ حيث تصطفّ الخلايا",
    aria: "تفصيل الكيان",
    back: "← الكيانات",
    none: "اختر كياناً من قائمة المراقبة.",
    observed: "ما رُصد",
    why: "لِمَ هذه الرتبة",
    /* ⚠ The caveat's label states its job — «what this does NOT mean» — because a heading
     * that just said «تحفّظ» invites the reader to skip a sentence models.py calls mandatory. */
    caveat: "⚠ ما لا يعنيه هذا",
    /* ⚠ An empty register is NOT "no information": models.py defines an empty `headline` as
     * "this family has no Arabic register yet". */
    noRegister: "لا سجلّ عربي لهذه العائلة بعد",
    predicted: "متوقَّع — لم يقع بعد",
    source: "المصدر",
    vintage: "حداثة البيانات",
    firstSeen: "أوّل مشاهدة",
    window: "نافذة الحدث",
    derived: "اشتُقّ في",
    events: "عدد الأحداث",
    counterpart: "اقتربت مباشرةً من",
    /* ⛔ DASH-4b-4 — the shared write passphrase. The wording deliberately says «مشتركة» and
     * never «كلمة مرورك»: it is a door key for the team, not an identity, and calling it a
     * password would imply the system knows who used it. It does not — the record of who did
     * what is the note left on the item. */
    keep: "احفظ للتقرير",
    /* ⚠ Says what was stored, not "done": the row is a SNAPSHOT of the claim, and a reader who
     * thinks a live reference was kept will expect it to update. It will not, by design. */
    kept: "✓ حُفظت لقطته",
    setToken: "أدخل عبارة الكتابة",
    tokenLabel: "عبارة الكتابة المشتركة",
    tokenHint: "من ملفّ الإعداد",
    tokenSave: "حفظ",
    tokenNote: "عبارة واحدة للفريق، تُحفظ في هذا المتصفّح وحده. لا تُعرّف بمن كتب — سجّل ذلك في الملاحظة.",
  },

  /* DASH-4b-2 — the monitoring surface's own chrome. ⚠ Nothing here characterises a signal;
   * every claim on that surface comes from the alert's own `headline` / `why` / `caveat`. */
  entityRows: {
    aria: "الكيانات المرصودة",
    open: "ما زال مفتوحاً",
    triage: { reviewing: "قيد المراجعة", closed: "مُغلق" } as Record<string, string>,
    /* ⛔ A CLOSED ITEM WHOSE SIGNATURE MOVED IS NOT STILL CLOSED — it is a new statement about
     * a known object. The badge says so rather than letting a stale verdict hide an update. */
    /* ⛔ A SUFFIX, NOT A WHOLE LABEL. It was a hard-coded «مُغلق · تغيّر», so an item someone
     * was REVIEWING rendered as closed. "Changed" qualifies whatever the state is. */
    staleSuffix: " · تغيّر",
    /* ⚠ A cap that does not say so reads as "this is everything". */
    capped: (shown: number, total: number) => `عُرضت ${shown} نافذة من ${total}`,
    /* ⚠ «متزامنة» IS THE CLAIM AND IT IS A MEASURED ONE: the lanes share one axis, so the word
     * describes what the cells show. It is NOT a severity word — three co-occurring patterns
     * raise the priority, never the certainty (the composite's own caveat). */
    coPatterns: "أنماط متزامنة",
  },

  dashHome: {
    statsSection: "الإحصاءات المباشرة",
    alertsSection: "التنبيهات",
    // domain stat tiles
    airLabel: "طائرات",
    seaLabel: "سفن",
    spaceLabel: "أقمار متتبَّعة",
    live: "حي",
    stale: "متأخّر",
    frozen: "مجمّد",
    computed: "محسوب",
    ago: (a: string) => `منذ ${a}`,
    /* severity labels (shown on each alert card).
     *
     * ⚠ RANK-NAME: `info` reads «لا تهديد» (was «معلوماتي»). The three degrees are UNCHANGED
     * deliberately — they are already the plainest Arabic magnitude triple and no simpler one
     * exists; the confusion end users reported was «معلوماتي», which is not a degree at all
     * but a CATEGORY, rendered as the fourth rung of a three-rung ladder and sorting BELOW
     * the lowest. «لا تهديد» says the category as a category, and it is the project's own
     * pledge verbatim («الوجود العسكري معلومة لا تهديد»).
     *
     * ⛔ THE VALUE IS THE WIRE, THE TEXT IS NOT. Keyed by `Alert.severity`, and every colour,
     * stripe and sort still reads that value (`SEV_TONE`, `.result-card.sev-*`,
     * `SEVERITY_ORDER`). So this map is display only: the ΔE gate and the four-silhouette
     * second channel are untouched, and SEVERITY_ORDER is not reached from here.
     *
     * ⚠ Width was checked, not assumed: «لا تهديد» is 8 glyphs against «معلوماتي»'s 8, so the
     * badge (49×21 at font-size:10px) does not grow and the fold slack is unaffected.
     *
     * ⛔ `families` and `familyBadge` were DELETED here, not renamed. Both were measured at
     * ZERO consumers outside this file — residue from `DashboardHome`, which DASH-2e replaced
     * with `CountryEvents`. They were also the four-name map a rename LOOKS like it should
     * edit, so leaving them would have left two stale vocabularies contradicting the live one
     * (RANK_PRE §2, §11.4 row 12). The rendered family surface is `signals` below.
     */
    severity: {
      ...RANK_WORDS,
      info: "لا تهديد",
    } as Record<string, string>,
    domainBadge: { air: "جوي", sea: "بحري", space: "فضائي" } as Record<string, string>,
    empty: "لا تنبيهات في هذه النافذة",
    loading: "جارٍ تحميل لوحة المراقبة…",
    windowNote: (h: number) => `نافذة ${h} ساعة`,
    // per-card freshness foot line
    liveTag: "حي",
    predicted: "تنبؤ SGP4",
    vintageAgo: (a: string) => `آخر بيانات منذ ${a}`,
    /* ---------- DASH-CARD: the two-layer alert card ---------- */
    // the disclosure that opens the second layer
    whyLook: "لماذا يستدعي النظر؟",
    // ⛔ «علم التسجيل» is MANDATORY wording, never just a country name: a flag is an
    // administrative fact about the registry, NOT a position. §3 warns explicitly against
    // confusing `flag_country` with `countries` (WHERE), and a bare name invites exactly that.
    flagOfRegistry: (c: string) => `علم التسجيل: ${c}`,
    lastSeen: (a: string) => `آخر رصد منذ ${a}`,
    /* ⛔ DASH-CARD-2 — a FORWARD-dated alert gets no "last observed" line at all, and no
     * invented substitute. Measured on the contract: a satellite pass carries `ts` = the pass
     * START (tomorrow) and `vintage` = the TLE EPOCH (two days ago), so «آخر رصد منذ» would
     * label an orbital element's age as an observation of the object — past tense over a future
     * event. Nothing replaces it because nothing needs to: `alertFoot` already prints
     * «تنبؤ SGP4», and the backend's own caveat states the element's date and how far the model
     * is being stretched. A third line would repeat what two lines already say. */
    forwardPass: "ممرّ متوقَّع — لم يحدث بعد",
    /* the ⓘ on the severity badge — ordering, not a threat verdict (DASH-1/§6.9).
     *
     * ⛔ `sevHintHigh` WAS DELETED, and RANK-S2 is why. It read «مرتفع = تزامن نمطين أو أكثر»,
     * and DASH-CARD-2 had already recorded that this lies to four of the five families (an
     * emergency squawk is high BY DEFINITION; military is INFO off the danger axis). S2 then
     * measured that it is false for the fifth too — the one it was written for: two pattern
     * types ALONE rank MEDIUM, so «مرتفع = تزامن نمطين» names a condition that cannot produce
     * HIGH (172 of 200 live maritime alerts asserted exactly this before the fix).
     *
     * ✅ A string that is false for every family has no honest fallback role, so the fallback
     * is now `sevHint` alone — which claims nothing about the cause. Where a register exists
     * the alert's own `why` still replaces it and states that alert's ACTUAL basis. */
    sevHint: "ترتيب نظر لا حكم تهديد",
    // dashboard sub-mode switch (stats folded into the لوحة, not a top tab)
    statsMonitor: "رصد",
    statsCharts: "إحصاءات",
    // ⛔ DASH-NAV — the switch's own group label. It lived hardcoded in App.tsx's navbar
    // children («وضع اللوحة») until the switch moved into the page; nothing inline remains.
    viewAria: "وضع اللوحة",

    /* ---------- UI-1: triage (sort · cap · hoist) ---------- */

    /* Arabic counterpart of alerts.CAVEAT (was rendered in English, 595 chars).
     *
     * ⚠ RANK-NAME: the rank and family words are brought in line with the labels actually on
     * screen — «لا تهديد» for the military rank (and «معلومة لا حكم خطر» in place of the old
     * trailing «لا تهديد», which would otherwise have said it twice), plus the adopted family
     * words. ⛔ NOT SHORTENED: every fact it carried is still here. Trimming its length is a
     * separate queued item (RANK_PRE §10) and the caveat is shortened there, never deleted. */
    caveat:
      "تُقرأ التنبيهات مباشرةً من الإشارات القائمة — لا جدول تنبيهات مخزّن ولا كاشف جديد. الخطورة لكل عائلة وصادقة: إشارات الطوارئ «مرتفع»؛ والوجود العسكري (طائرات + مرور مداري فوق النطاق) «لا تهديد» — معلومة لا حكم خطر؛ والسفن ذات الملاحظات المتعددة تحمل ترتيبها الخاص (دليل أقوى لا إثبات)؛ وشذوذ الرصد «منخفض» (السفينة المخدوعة/المشوَّشة غالباً ضحية). الحداثة مختلطة وتُبيَّن لكل تنبيه: تغذية الجو/البحر قد تتأخّر، ومرور الأقمار تنبؤ SGP4 مستقبلي، وكل سفينة تحمل زمن آخر حدث فيها. AIS و ADS-B قابلان للتزوير.",

    // the three decision tiles — the triage axis (urgency), not inventory
    tileHigh: "يستدعي الانتباه",
    tileMedium: "قيد المراجعة",
    tileTotal: "إجمالي التنبيهات",
    tileAria: "أولويات التنبيهات",

    /* ---------- RANK-NAME: the badge legend ----------
     *
     * ⛔ WHY THIS EXISTS, and it is not decoration. Measured on the live feed: of the four
     * rank words, only TWO ever render — «مرتفع» ×200 and «لا تهديد» ×21; «متوسّط» and
     * «منخفض» are both 0. The tiles cover only two tiers as well. So a reader is shown the
     * top of a scale and its odd one out, and has no way to learn the scale from the screen —
     * which is the part no rename could fix.
     *
     * ⚠ It is COLLAPSED by default because the panel has no length budget: DASH-2d″'s
     * ~3,042px gate was already exceeded in six of seven countries, which is why PANEL_CAP is
     * 10 and the headline folds. Closed, this costs one line; open, it is one interaction
     * away — the same discipline as `showAll`.
     */
    legendToggle: "ما معنى الشارات؟",
    legendAria: "معاني درجات الخطورة",
    legendGloss: {
      high: "أعلى درجة في مقياس الخطورة.",
      medium: "درجة وسطى.",
      low: "أدنى درجة — لكنها رصدة حقيقية.",
      // ⛔ the teaching line: this is WHY it sits below «منخفض», and the sentence is the
      // project's standing commitment, not a UI gloss.
      info: "ليست درجة خطورة أصلاً: معلومة مسجَّلة، لا حكم تهديد — ولذلك تقع أسفل «منخفض».",
    } as Record<string, string>,
    // ⚠ names the SECOND channel outright, so the four are distinguishable with every hue
    // stripped out (THEME-2: 5px متّصل / متقطّع / منقّط, و2px رفيع).
    legendChannel: "ولكل درجة شكل شريط مختلف على حافة البطاقة، فتُقرأ بلا اعتماد على اللون.",

    /* per-signal group headers — the hoisted constant note attaches here.
     * Grouped by signal_type (NOT family): the military family carries two signal
     * types with DIFFERENT constant bases, so family-grouping would merge them.
     *
     * ⚠ RANK-NAME — the adopted family words, and each replaces an INTERNAL term:
     *   «إشارة طوارئ»          the system captures the SIGNAL (the code), never a confirmed
     *                          emergency — matches the deliberately passive «بُثّ رمز…» and
     *                          the caveat «الرمز مُبلَّغ من الطائرة نفسها». «رمز إرسال» was jargon.
     *   «وجود جوي (عسكري)»     ⛔ «نشاط» was refused: BE-SUMMARY-4's refused-verb list exists
     *                          because the source holds «موضعاً ومُعرّفاً فقط». «وجود» is the
     *                          committed safe form («سُجّل وجود طائرة عسكرية»).
     *   «مرور مداري (عسكري)»   tenseless and geometric — correct for a pass that HAS NOT
     *                          HAPPENED YET (see `forwardPass`).
     *   «عدّة ملاحظات»         ⛔ «صورة مركّبة» / «مركّب» is `composite` — the most internal
     *                          term in the system (composite.py, maritime_hull_composite).
     *   «شذوذ في الرصد»        describes the observation's deviation WITHOUT naming a cause,
     *                          so IMPL_CAVEAT's four causes stay open. ⚠ «خطأ رصد» would have
     *                          closed them — two of the four are not sensing errors at all.
     *
     * ⛔ Five keys, not four: «عسكري» appears twice and each keeps its signal-type qualifier,
     * because the two military signal types carry different constant bases.
     */
    signals: {
      emergency_squawk: "إشارة طوارئ",
      military_aircraft: "وجود جوي (عسكري)",
      military_satellite: "مرور مداري (عسكري)",
      maritime_composite: "عدّة ملاحظات",
      implausible_position: "شذوذ في الرصد",
    } as Record<string, string>,

    // the cap control — the full set is always one interaction away, and named
    showAll: (n: number) => `عرض الكل (${n})`,
    showTop: (n: number) => `أعلى ${n} فقط`,
    capNote: (shown: number, total: number) => `تُعرض ${shown} من ${total} — الأعلى خطورةً أولاً`,

    /* ---------- DASH-2b: the switchable sort axis ---------- */

    // ⚠ The cap note must name the ACTIVE axis. The severity wording above is
    // byte-identical to its pre-DASH-2b self; a separate line carries importance,
    // because printing "الأعلى خطورةً أولاً" over an importance-ordered list would
    // be a false claim about what the order means.
    capNoteImportance: (shown: number, total: number) =>
      `تُعرض ${shown} من ${total} — الأعلى أهميةً للعرض أولاً (ترتيب عرض، ليس درجة تهديد)`,
    // the toggle names the ACTION, like showAll/showTop above it
    sortByImportance: "رتّب بالأهمية",
    sortBySeverity: "رتّب بالخطورة",
    // ⚠ the title carries the documented commitment onto the surface itself
    sortImportanceTitle:
      "ترتيب عرض فقط: يرفع الوجود العسكري إلى الأعلى لأنه الحقيقة البارزة في مراقبة دولة. ليس درجة تهديد — كل بطاقة تُبقي شارة خطورتها وسببها كما هي.",
    // ⚠ RANK-NAME: names the label the badge prints, «لا تهديد» — the whole point of this
    // sentence is that the reader can match it to what is on the card.
    sortSeverityTitle: "الترتيب الصادق للخطورة: الأعلى خطورةً أولاً، و«لا تهديد» أسفل المنخفض.",
    sortAria: "محور فرز التنبيهات",
    // the hoisted maritime clause (Arabic), shown once per group instead of per card
    hoistedLead: "دليل أقوى — لا إثبات",
  },

  // Search & monitoring tab (E1 — vessel first; the shell E2–E4 extend). The
  // analyst interrogates ONE entity: search → summary → expand to full track.
  entitySearch: {
    section: "بحث ومراقبة",
    tabAria: "بحث ومراقبة عبر المجالات",
    entityAria: "نوع الكيان",
    // segment labels — only 'vessel' is built in E1; E2–E4 add طائرة/قمر/مطار
    entities: { vessel: "سفينة", aircraft: "طائرة", satellite: "قمر", airport: "مطار" } as Record<string, string>,
    // generic across entities (the segment shows which entity is active), so the
    // shell (SearchPanel) needs no per-entity change to add aircraft/sat/airport
    placeholder: "معرّف أو اسم أو تسجيل أو نداء…",
    ariaQuery: "حقل البحث عن كيان",
    prompt: "اكتب معرّفاً (MMSI / ICAO24) أو اسماً أو تسجيلاً أو نداءً للبحث",
    loading: "جارٍ البحث…",
    count: (n: number) => `${n} نتيجة`,
    empty: "لا نتائج مطابقة",
    // result list
    listAria: "نتائج البحث",
    colName: "الاسم",
    colFlag: "العَلَم",
    colPatterns: "أنماط",
    resultsSection: "النتائج",
    // summary / detail card
    detailSection: "الملخّص",
    detailEmpty: "اختر نتيجة لعرض ملخّصها",
    identity: "الهوية",
    dimensions: "الأبعاد",
    lastFix: "آخر ظهور",
    position: "الموقع",
    vintage: "زمن البيانات",
    frozen: "بيانات مجمّدة",
    patternsLabel: "أنماط السلوك — دلائل تحقيق",
    patternsNone: "لا أنماط سلوكية مرصودة",
    /* ⚠ RANK-NAME: `gnss` reads «شذوذ إشارة الملاحة» — the frontend mirror of the backend's
     * `PATTERN_AR["gnss_anomaly"]`, kept in step with it. ⛔ «GPS» was rejected: it names ONE
     * constellation, and the detector does not distinguish it from GLONASS/Galileo/BeiDou.
     * ⛔ Scope: this is the maritime behaviour-PATTERN label. The map-layer toggle
     * (`map.layers.gnss`) and the standalone overlay section (`T.gnss.*`) are a DIFFERENT
     * feature with its own endpoint and stay «شذوذ GNSS» — renaming those would rename a map
     * layer, not a pattern. ⚠ Recorded as an open consistency question in RANK_PRE §12.3. */
    pat: { gaps: "فجوات", rendezvous: "لقاءات", identity_changes: "تغيّر هوية", gnss: "شذوذ إشارة الملاحة" },
    caveat:
      "أعداد الأحداث تجميعٌ لدلائل التحقيق (فجوات/لقاءات/تغيّر هوية/شذوذ إشارة الملاحة) — ليست إثباتاً؛ AIS أرضي وقابل للتزوير. البيانات مجمّدة؛ آخر ظهور يبيّن زمنها.",
    fixesLabel: (n: number) => `${n} موضعاً مسجّلاً`,
    // track expansion
    showTrack: "عرض المسار الكامل",
    hideTrack: "إخفاء المسار",
    trackLoading: "جارٍ جلب المسار…",
    trackSection: "المسار",
    trackSpan: "المدى الزمني",
    trackQuality: "جودة المواضع",
    trackCapped: "المسار مقتطع عند الحد — ضيّق النافذة",
    trackEmpty: "لا مواضع في المسار",
    quality: { plausible: "معقول", suspect: "مشكوك", implausible: "غير معقول" } as Record<string, string>,
    clear: "مسح",
    soon: "قريباً",
    // E2 — aircraft (presence, no behavioural events). Field labels reuse T.strip.*
    air: {
      colId: "النداء",
      colType: "الطراز",
      colWhere: "الموقع",
      presenceNote: "ملخّص حضور — المجال الجوي بلا طبقة أحداث سلوكية (بخلاف البحري): هوية + آخر ظهور + الموقع من المنطقة",
      trackedSince: "يُتابَع منذ",
      live: "حي",
      altitude: "الارتفاع",
      onGround: "على الأرض",
      airborne: "في الجو",
      route: "خط السير (معتاد)",
      noRoute: "لا خط سير معروف",
      trackNote: "المسار: آخر 24 ساعة حتى آخر ظهور (عبر /history)",
    },
    // E3 — satellite (no stored fixes; layers with DISTINCT vintages)
    sat: {
      colName: "الاسم",
      colType: "النوع",
      colOrbit: "المدار",
      identitySection: "الهوية (SATCAT)",
      orbitSection: "المدار (SGP4 مشتق)",
      strategicSection: "بيانات استراتيجية (UCS)",
      norad: "NORAD",
      intlDes: "المعرّف الدولي",
      objType: "النوع",
      owner: "الجهة",
      launch: "الإطلاق",
      orbitClass: "صنف المدار",
      inclination: "الميل",
      period: "زمن الدورة",
      apogee: "الأوج",
      perigee: "الحضيض",
      users: "المُستخدِم",
      purpose: "الغرض",
      operatorCountry: "دولة المُشغّل",
      // vintage badges — the strategic one is FROZEN 2023, visibly distinct
      vintages: "زمن البيانات",
      vIdentity: (d: string) => `هوية ${d}`,
      vOrbit: (d: string) => `مدار ${d}`,
      vOrbitLive: "مدار مباشر",
      vStrategic: (d: string) => `استراتيجي ${d} (مجمّد)`,
      // AOI passes (forward prediction)
      passes: (n: number) => `${n} عبور متوقّع فوق المنطقة`,
      passWindow: (a: string, b: string) => `نافذة التنبؤ: ${a} — ${b}`,
      persistent: "مستقرّ فوق المنطقة (GEO)",
      noPasses: "لا عبورات متوقّعة فوق المنطقة",
      // ground-track expansion
      showTrack: "عرض المسار الأرضي",
      hideTrack: "إخفاء المسار",
      trackLeo: (p: number) => `مسار أرضي — دورة مدارية كاملة (~${p} د)`,
      trackGeo: (lon: string) => `GEO — مستقرّ فوق نطاق الطول ~${lon}° (ليس مساراً متحركاً)`,
      noTle: "لا عناصر TLE — يتعذّر حساب المسار",
      live: "الموقع الآن",
      altitude: "الارتفاع",
      km: "كم",
      warn: "الموقع والمسار محسوبان محلياً (SGP4) من TLE؛ الدقّة تتناقص من لحظة العنصر — للعرض. كتالوج CelesTrak عام.",
    },
    // E4 — airport → landings (one-to-many; candidates, coverage-bounded)
    air4: {
      colName: "المطار",
      colCode: "الرمز",
      colWhere: "المدينة",
      airportSection: "المطار",
      landingsSection: (n: number) => `مرشّحو الهبوط (${n})`,
      elevation: "ارتفاع المدرج",
      ft: "قدم",
      landingsEmpty: "لا مرشّحين في النافذة — تغطية ADS-B محدودة عند هذا المطار (نتيجة صحيحة لا خطأ)",
      windowNote: (h: number) => `النافذة: آخر ${h} ساعة`,
      signalGrounded: "على الأرض",
      signalApproach: "اقتراب",
      signalGroundedHint: "رصد على سطح المطار (وصول/مغادرة/رابض)",
      signalApproachHint: "هبوط تنازلي انتهى قرب ارتفاع المدرج",
      minAlt: "أدنى ارتفاع",
      m: "م",
      landedAt: "الوقت",
      caveat:
        "مرشّحو هبوط لا هبوط مؤكّد — 'على الأرض' قد يكون وصولاً أو مغادرة أو رابضاً؛ 'اقتراب' مستدَلّ من تسلسل الارتفاع. الجودة محكومة بالتغطية: مطار جيد التغطية كثيف، وضعيف التغطية قليل أو فارغ.",
      trackNote: "المسار: نافذة الهبوط عبر /history",
    },
  },

  satellite: {
    brandSub: "مراقب الأقمار الصناعية",
    kpiAria: "إحصاءات الأقمار",
    kpiCount: "الأقمار",
    kpiGroups: "المجموعات",
    group: "المجموعة",
    groupAria: "اختيار مجموعة CelesTrak",
    feedFetched: (age: string) => `آخر تحديث TLE: ${age}`,
    feedNone: "لا عناصر — شغّل جامع الأقمار (CelesTrak)",
    listSection: "الأقمار",
    listAria: "قائمة الأقمار",
    listEmpty: "لا أقمار",
    colName: "الاسم",
    colNorad: "NORAD",
    colIncl: "الميل",
    stripSection: "القمر المختار",
    stripEmpty: "لا قمر مختاراً — اضغط قمراً على الخريطة أو صفاً في القائمة",
    norad: "NORAD",
    altitude: "الارتفاع",
    velocity: "السرعة",
    position: "الموقع",
    inclination: "الميل",
    period: "زمن الدورة",
    epoch: "عمر عناصر TLE",
    group_: "المجموعة",
    km: "كم",
    kms: "كم/ث",
    min: "دقيقة",
    computing: "جارٍ الحساب…",
    // SATCAT identity (S1 — CelesTrak SATCAT, on-select enrichment)
    country: "الدولة/الجهة",
    objectType: "النوع",
    launchDate: "تاريخ الإطلاق",
    launchSite: "موقع الإطلاق",
    intlDes: "المعرّف الدولي",
    status: "الحالة",
    decayed: (d: string) => `تحلّل في ${d}`,
    /* ⚠ PAYLOAD-ENRICH surfaced this. `ops_status` is a SATCAT single-character code, and
     * wiring the enrichment made a bare «+» visible in the status cell. PAYLOAD-PROV refused to
     * print raw `category` integers for exactly this reason — a code is noise dressed as data —
     * so leaving a raw «+» here would contradict the rule one commit later. An unmapped code
     * falls through as `#<c>`: visibly a code, never a silent blank. */
    opsStatus: {
      "+": "نشط", "-": "غير نشط", "P": "جزئياً نشط", "B": "احتياطي",
      "S": "احتياطي فائق", "X": "ممتدّ", "D": "متحلّل", "?": "غير معروف",
    } as Record<string, string>,
    typeLabels: {
      payload: "حمولة", rocket_body: "جسم صاروخي", debris: "حطام", unknown: "غير معروف",
    } as Record<string, string>,
    identityUnknown: "لا بيانات هوية SATCAT",
    satcatProvenance: (d: string) => `SATCAT · ${d}`,
    warn: "الموقع محسوب محلياً (SGP4) من عناصر TLE؛ دقّتها تتناقص من لحظة الرصد — للعرض لا للملاحة الدقيقة. كتالوج CelesTrak عام (بلا أجرام سرّية أو مناورة).",
    groupNames: {
      stations: "محطات فضائية", "gps-ops": "GPS", galileo: "Galileo",
      "glo-ops": "GLONASS", beidou: "BeiDou", weather: "طقس", noaa: "NOAA",
      goes: "GOES", science: "علمية", military: "عسكرية", geo: "ثابتة أرضياً",
      starlink: "Starlink", "active": "نشطة", resource: "موارد أرضية",
      "cubesat": "كيوبسات",
    } as Record<string, string>,
  },
  reentry: {
    section: "عودة الأجسام إلى الغلاف الجوي (تنبؤ)",
    aoiWatch: "متوقّعة فوق/قرب المنطقة",
    none: "لا عمليات عودة متوقّعة داخل/قرب المنطقة",
    inside: "داخل",
    near: "قرب",
    colObject: "الجسم",
    colTime: "الوقت المتوقّع (UTC)",
    colWhere: "المكان المتوقّع",
    window: (m: number) => `±${m} د`,
    imprecise: "نقطة اسمية بعدم يقين كبير (SGP4/TIP) — للإنذار لا للدقة",
    source: "المصدر: Space-Track TIP",
  },
  military: {
    toggle: "عسكري",
    ariaOn: "إخفاء التراكب العسكري",
    ariaOff: "إظهار التراكب العسكري",
    tag: "ع",
    counter: (n: number) => `عسكري: ${n}`,
    source: "المصدر: airplanes.live — غير مفلتر",
  },
  adsb: {
    toggle: "ADS-B",
    ariaOn: "إخفاء تراكب ADS-B Exchange",
    ariaOff: "إظهار تراكب ADS-B Exchange",
    tag: "خ",
    source: "المصدر: ADS-B Exchange — ضمن/قرب المنطقة (تصنيف scraplan)",
  },

  // GFW satellite-AIS dark-vessel analysis layer
  dark: {
    region: "المنطقة",
    regions: { all: "الكل", gulf: "الخليج", redsea: "الأحمر" } as Record<string, string>,
    intentional: "متعمّد",
    loading: "جارٍ الجلب من GFW…",
    empty: "لا سفن صامتة في هذه المنطقة/النافذة",
    disabled: "GFW غير مفعّل — أضف GFW_API_TOKEN",
    caveatFallback:
      "انقطاع AIS من مصدر ساتلي (GFW) — دليل لا إثبات: قد يكون عطل مستقبِل أو صمتاً مشروعاً؛ «متعمّد» ترجيح GFW لا حُكم.",
    caveatShort: "دليل لا إثبات",
    // SUSPECT-1: on-land (spoofed/erroneous) dark positions surfaced as their own signal
    atSea: "في البحر",
    suspect: "مشبوهة/مُزوّرة",
    onLandTag: "موقع بري",
    onLandPop: "موقع بري — إحداثيات مشبوهة/مُزوّرة",
    suspectNote:
      "منها {n} بموقع على اليابسة — إحداثيات AIS مشبوهة/مُزوّرة (تزوير إشارة الملاحة أو خطأ)، تُعرض كإشارة لا كموقع.",
  },
  /* ⚠ RANK-NAME: «GNSS» removed from every LABEL and aria string here — the abbreviation is
   * jargon to an operator, and «إشارة الملاحة» names the same thing in plain Arabic without
   * claiming a single constellation the way «GPS» would.
   *
   * ⚠ IT SURVIVES IN ONE PLACE ONLY: `source`, as a parenthetical. That line is PROVENANCE,
   * and naming a source exactly is this project's standing discipline — MIL_CAVEAT_ID names
   * `/v2/mil`, SAT_CAVEAT_TAG names UCS and CelesTrak. The plain term leads and the technical
   * one is a gloss behind it. ⛔ One word to drop if the owner wants it gone. */
  gnss: {
    toggle: "شذوذ إشارة الملاحة",
    ariaOn: "إخفاء تراكب شذوذ إشارة الملاحة",
    ariaOff: "إظهار تراكب شذوذ إشارة الملاحة",
    source: "شذوذ إشارة الملاحة (GNSS): مواضع غير معقولة (خداع/تشويش) — دليل تحقيق لا إثبات",
    caveat: "دليل تحقيق لا إثبات — قد يكون خداعًا أو تشويشًا أو خطأ مستقبِل؛ السفينة غالبًا ضحية",
    variant: { broadcast: "نقطة الهبوط", affected: "الموقع الحقيقي" },
    zoneVessels: "سفن متأثّرة",
    window: "النافذة",
    popup: {
      type: "النوع",
      types: {
        individual: "فردي — سفينة مخدوعة/مشوَّشة",
        area: "تشويش منطقة (ضحية)",
        glitch: "خلل مصدر",
      } as Record<string, string>,
      fixes: "مواضع غير معقولة",
      decoy: "مطابقة نقطة خداع (مطار)",
      speed: "أقصى سرعة ضمنية",
      kn: "عقدة",
      inland: "أقصى توغّل داخلي",
      km: "كم",
    },
  },
  vessel: {
    tab: "بحري",
    tabAria: "الملاحة البحرية",
    brandSub: "مراقب الملاحة البحرية",
    source: "المصدر: AISStream — AIS أرضي فقط",
    sourceMt: "MarineTraffic — كشط مباشر (شروطهم تمنع الكشط؛ موسوم بالمصدر)",
    sourceAis: "AISStream — AIS أرضي",
    flag: "العلم",
/* ---------- PAYLOAD-PROV (vessel) ----------
     * ⛔ THE WORST OF THE SIX, and worse than a plain omission. `source` was rendered ONLY as
     * an «MT» badge when it equalled "marinetraffic"; for every AIS vessel — the majority —
     * the card showed a position, a speed and a draught with NO source at all. A surface that
     * attributes some rows and silently not others reads as attributed everywhere, which is
     * how an unsourced number gets quoted as a sourced one. Now every vessel names its feed. */
    sourceLabel: "المصدر",
    sourceNames: {
      aisstream: "AISStream (AIS أرضي)",
      marinetraffic: "MarineTraffic",
      vessel_states: "vessel_states (مخزَّن)",
    } as Record<string, string>,
    dataOrigin: "أصل البيانة",
    position: "الموقع",
    snapshotAge: "عمر اللقطة",
        shipId: "معرّف MarineTraffic",
    noName: "سفينة بلا اسم",
    crossref: {
      title: "التقاطُع AIS × MarineTraffic",
      corroborated: "من أسطول MarineTraffic مؤكَّد عبر AIS",
      confidence: "توزيع الثقة",
      matched: "مطابَقة",
      mtOnly: "MarineTraffic فقط",
      discrepancies: "تعارض أبعاد",
      discrepancyBanner: "تعارض أبعاد عند نفس الموقع — إشارة تلاعب/جودة بيانات محتملة",
      strong: "قوي",
      probable: "مرجّح",
      weak: "ضعيف",
      ambiguousTag: "ملتبس",
      discrepancyTag: "تعارض أبعاد",
      loading: "جارٍ حساب التقاطُع…",
      empty: "لا تقاطُع محسوب بعد — شغّل «correlate»",
      caveatFallback:
        "روابط مشتقة — دليل لا إثبات: MarineTraffic بلا MMSI، فالربط يُستنتج بالاسم (المرساة) + الموقع عبر الزمن + الأبعاد.",
    },
    speed: "السرعة",
    course: "المسار",
    heading: "الاتجاه",
    draught: "الغاطس",
    type: "النوع",
    callsign: "النداء",
    status: "الحالة",
    lastFix: "آخر ظهور",
    kn: "عقدة",
    warn: "AIS أرضي (~40–60 ميلاً بحرياً) وقابل للتزوير — لا يُعامَل كحقيقة مؤكدة دون مصدر ثانٍ.",
    // KPIs
    kpiAria: "إحصاءات السفن",
    kpiTotal: "السفن",
    kpiMoving: "متحركة",
    kpiStationary: "راسية",
    kpiTypes: "الأنواع",
    // UI-2 step 3 — provenance on the headline number: the tiles count AIS only, and
    // the MarineTraffic contribution is a separate, amber-marked figure.
    kpiSrcAis: "AIS",
    kpiSrcMt: (n: string) => `+${n} MT`,

    /* ---------- UI-FRESH: per-source freshness + the MT opt-in overlay ---------- */
    // the freshness strip — one row per CONTRIBUTING source, each with its own age
    srcSection: "حداثة المصادر",
    srcAria: "حداثة كل مصدر بحري",
    srcNames: { aisstream: "AISStream", marinetraffic: "MarineTraffic" } as Record<string, string>,
    srcLive: "حي",
    srcStale: "غير حي",
    srcRows: (n: string) => `${n} سفينة`,
    srcAge: (a: string) => `منذ ${a}`,
    srcNone: "لا مصدر مساهم",
    // data_vintage is now a worst case, not "the" vintage — label it as such
    srcWorstCase: (a: string) => `أقدم مصدر مساهم: منذ ${a}`,
    srcWorstCaseTitle:
      "data_vintage صار أقدمَ مصدر مساهم (أسوأ حالة) — لا يصف الحزمة كلها؛ اقرأ حداثة كل مصدر أعلاه.",
    // the MarineTraffic opt-in overlay (off by default, like GNSS + dark vessels)
    mtLayer: "طبقة MarineTraffic",
    // ⛔ CORRECTED 2026-08-15. This read «مخزون ثابت لا يحدّثه أي مُجمِّع» — a static store no
    // collector updates. That was true when written (mt_vessel_states was frozen at
    // 2026-07-25) and is now FALSE: mtscraper is running and the store took 3,965 fixes in
    // the last hour, newest 352 s old. A stale caveat is worse than none — it tells the
    // operator to distrust live data.
    // ⚠ The MMSI half is STILL TRUE and kept: measured 20,161 of 20,161 recent rows carry
    // mmsi = NULL. MT keys on its own `ship_id`, so these vessels cannot be joined to the
    // AIS identity layer or investigated by MMSI.
    mtLayerOffHint:
      "مصدر ثانٍ مستقل يجمعه كاشط MarineTraffic. سفنه بلا MMSI (مُعرَّفة بـship_id) فلا تُربط بطبقة هوية AIS ولا يُحقَّق فيها بالـMMSI. مطفأ افتراضياً؛ شغّله بوعي.",
    // ⚠ Shown ONLY when the primary feed is unavailable and this layer is off — an empty
    // tab that does not say why reads as a broken app. It states what to TRY, never that
    // the other source has data: with the layer off the client only asked for aisstream,
    // so it genuinely does not know.
    // ⛔ THE MAP'S OWN DATELINE. Without it a visitor sees ships on a map and reads them as
    // live — the per-marker hollow "stale" glyph is an analyst's cue, not something a
    // first-time reader decodes. Measured state at the time of writing: the AIS feed had
    // been frozen for 11 days while MarineTraffic was ~200 s old, so the map could show
    // hundreds of vessels that were not current at all.
    mapAgeLive: (a: string) => `مواضع حيّة — آخر تحديث منذ ${a}`,
    mapAgeStale: (a: string) => `⚠️ ليست مواضع حيّة — آخر تحديث منذ ${a}`,
    mapAgeNone: "لا مواضع معروضة",
    mapAgeSrc: (s: string) => `المصدر: ${s}`,
    mtPrimaryDown:
      "المصدر الأساسي (AIS) لا يبثّ حالياً. جرّب تفعيل طبقة MarineTraffic أدناه — مصدر مستقل قد يحمل مواضع حديثة.",
    mtLayerOnHint: (a: string) =>
      `مضاف: مواضع MarineTraffic أقدمها منذ ${a} — مُعلَّمة كغير حيّة في القائمة وعلى الخريطة.`,
    mtAgeUnknown: "العمر يظهر عند التشغيل",
    // per-row staleness (flag, never drop)
    staleTag: "غير حي",
    staleTitle: (a: string) => `آخر موضع منذ ${a} — ليس موضعاً حياً`,
    kpiAisOnly:
      "الأرقام أعلاه من AIS (المصدر الأساسي) فقط. مساهمة MarineTraffic تُعرض منفصلةً بلونها — غير مضمومة إلى أي رقم رئيسي.",
    // feed
    feedLive: "بث AIS مباشر",
    feedOff: "لا بث بحري — شغّل الجامع",
    // search / list / strip
    searchSection: "بحث بحري",
    searchPlaceholder: "اسم أو MMSI…",
    searchAria: "بحث باسم السفينة أو MMSI",
    showing: (x: number, y: number) => `عرض ${x} من ${y} سفينة`,
    listSection: "السفن",
    analysisSection: "التحليل",
    listAria: "قائمة السفن",
    // GFW dark-vessels analysis layer
    darkLayer: "سفن صامتة",
    listEmptyDark: "لا سفن صامتة",
    listEmpty: "لا سفن مطابقة",
    colName: "الاسم",
    colType: "النوع",
    colSpeed: "السرعة",
    stripSection: "السفينة المختارة",
    stripEmpty: "لا سفينة مختارة — اضغط سفينة على الخريطة أو صفاً في القائمة",
    photoLoading: "جارٍ البحث عن صورة (ويكيميديا كومنز)…",
    // A1 — AIS gaps (سلوكية): سفينة كانت تسير ثم توقّف إرسالها ضمن تغطية جيدة
    gapsSection: "الفجوات — قيد التحقيق",
    gapsAria: "فجوات إرسال AIS المرشّحة للتحقيق",
    gapsCaveat: "دلائل للتحقيق لا إثبات — انقطاع الإرسال قد يكون لأسباب تقنية، لا بالضرورة تعتيماً متعمّداً.",
    gapsLoading: "جارٍ فحص الفجوات…",
    gapsEmpty: "لا فجوات مرشّحة",
    gapsError: "لا توجد بيانات",
    gapsCount: (n: number) => `${n} مرشّحة`,
    gapsWaters: "المياه",
    gapsHours: (h: string) => `${h} س`,
    gapsStillDark: "ما زالت صامتة",
    // A2 — rendezvous (سلوكية): سفينتان تلاقتا على مسافة قريبة وبطء ≥ ساعة في عرض
    // البحر بعيداً عن الموانئ — نقل محتمل بين سفينتين.
    rvSection: "لقاءات بحرية — قيد التحقيق",
    rvAria: "لقاءات السفن المرشّحة للتحقيق",
    rvCaveat: "دلائل للتحقيق لا إثبات — قد يكون التلاقي مشروعاً (إمداد أو خدمة أو انتظار). الترتيب يحدّد الأولوية للمراجعة، لا اتهاماً؛ ومعظم الحالات مشروعة.",
    rvLoading: "جارٍ فحص اللقاءات…",
    rvEmpty: "لا لقاءات مرشّحة",
    rvError: "لا توجد بيانات",
    rvCount: (n: number) => `${n} مرشّح`,
    rvWaters: "المياه",
    rvDistance: "أقرب مسافة",
    rvDuration: "المدة",
    rvOffshore: "بُعد عن الساحل",
    rvMeters: (m: string) => `${m} م`,
    rvMinutes: (m: string) => `${m} د`,
    rvHours: (h: string) => `${h} س`,
    rvKm: (k: string) => `${k} كم`,
    rvStillTogether: "ما زالتا متلاقيتين",
    rvWith: "مع",
    // ⚠ RANK-NAME: was its own vocabulary («عالٍ» / «متوسط»). Now the shared `RANK_WORDS`, so
    // one wire value cannot read as two different Arabic words on two surfaces of one app.
    rvRank: { ...RANK_WORDS } as Record<string, string>,
    // A3 — identity change (سلوكية): سفينة تبثّ اسماً/عَلَماً/MMSI مختلفاً عبر الزمن
    // (تمويه محتمل). إعادة العَلْمنة = نفس IMO (المرجع الثابت) تحت MMSI وعَلَم جديدين
    // — بصمة أسطول الظل. تغيير الاسم/النوع تحت MMSI واحد.
    idSection: "تغيّر الهوية — قيد التحقيق",
    idAria: "تغيّرات هوية السفن المرشّحة للتحقيق",
    idCaveat: "تغيير الاسم أو العَلَم أو النوع غالباً إجراء مشروع وموثّق — دلائل للتحقيق لا إثبات تهرّب. الترتيب يحدّد أولوية المراجعة، لا اتهاماً.",
    idLoading: "جارٍ فحص تغيّرات الهوية…",
    idEmpty: "لا تغيّرات هوية مرشّحة",
    idError: "لا توجد بيانات",
    idCount: (n: number) => `${n} حدث`,
    idColIdentity: "الهوية (قديم ← جديد)",
    idColAnchor: "المرجع IMO",
    idArrow: "←",
    idMmsi: (o: string, n: string) => `MMSI ${o} ← ${n}`,
    idCorrelated: "مرتبط بأحداث",
    idTypes: {
      reflag: "إعادة عَلْمنة", rename: "تغيير اسم", retype: "تغيير نوع",
    } as Record<string, string>,
    // CAPSTONE — الصورة المركّبة (cross-pattern): تجميع الأنماط الثلاثة (فجوة/لقاء/
    // تغيّر هوية) لكل بدن، مربوطةً بـ IMO عبر هويّات السفينة. تجميع الدلائل دليلٌ
    // أقوى لكنه ليس إثباتاً. الترتيب يحدّد الأولوية لا الاتهام.
    cxSection: "الصورة المركّبة — أولويات التحقيق",
    cxAria: "الصورة المركّبة لأنماط سلوك السفن — أولويات التحقيق",
    cxCaveat:
      "تجميعٌ لدلائل التحقيق — دليلٌ أقوى لكنه ليس إثباتاً: تراكب عدة أنماط سلوكية على بدنٍ واحد يرفع الأولوية، ولا يثبت مخالفة. الترتيب للأولوية لا للاتهام.",
    cxLoading: "جارٍ تجميع الأنماط…",
    cxEmpty: "لا أبدان بأنماط متعددة",
    cxError: "لا توجد بيانات",
    cxCount: (n: number) => `${n} بدن`,
    cxPatterns: (n: number) => `أنماط: ${n}`,
    cxReflag: (n: number) => `⚑ ${n} هويات`, // hint: multiple identities → re-flagged
    cxGrey: "قائمة رمادية/عقوبات",
    cxWaters: "المياه",
    cxIdentities: "الهويات",
    cxNoImo: "بلا IMO — MMSI",
    cxGapWord: "فجوات",
    cxRvWord: "لقاءات",
    cxIdWord: "هوية",
    cxEvents: {
      ais_gap: "فجوة", rendezvous: "لقاء", identity_change: "تغيّر هوية",
    } as Record<string, string>,
    types: {
      cargo: "شحن", tanker: "ناقلة", passenger: "ركّاب", fishing: "صيد",
      tug: "قاطرة", pilot: "إرشاد", "high_speed_craft": "عالية السرعة",
      sailing: "شراعية", "pleasure_craft": "نزهة", "search_and_rescue": "بحث وإنقاذ",
      "law_enforcement": "إنفاذ قانون", military_ops: "عمليات عسكرية",
      dredging: "تجريف", towing: "قطر", "port_tender": "زورق ميناء",
      "anti_pollution": "مكافحة تلوث", "wing_in_ground": "فوق سطحية",
      "diving_ops": "غوص", "medical_transport": "نقل طبي", other: "أخرى",
      unknown: "غير محدد",
    } as Record<string, string>,
  },
  theme: {
    toLight: "خريطة نهارية",
    toDark: "خريطة ليلية",
    lightLabel: "☀ خريطة نهارية",
    darkLabel: "☾ خريطة ليلية",
  },

  stats: {
    aria: "إحصاءات الحركة الجوية",
    range: "النطاق",
    rangeAria: "النطاق الزمني",
    bucket: "الفاصل",
    loading: "جارٍ التحميل…",
    unavailable: "الإحصاءات غير متاحة: ",
    presets: { "1h": "ساعة", "6h": "6 ساعات", "24h": "24 ساعة", "7d": "7 أيام" } as Record<string, string>,
    buckets: { "1m": "دقيقة", "5m": "5 دقائق", "15m": "15 دقيقة", "1h": "ساعة", "1d": "يوم" } as Record<string, string>,
    trafficTitle: "الحركة الجوية",
    trafficSub: (b: string) =>
      `متجهات الحالة لكل فاصل ${b} — من التجميعة المستمرة الدقيقية؛ الفجوات فترات بلا جمع`,
    altTitle: "أقصى ارتفاع ضغطي (قدم)",
    altSub: "أعلى ارتفاع مبلَّغ في كل فاصل؛ الخط ينقطع حيث لا بيانات",
    legendAir: "في الجو",
    legendGround: "على الأرض",
    tipAir: "جو",
    tipGround: "أرض",
    tipSnaps: "لقطات",
    emptyTraffic: "لا بيانات في هذا النطاق",
    emptyAlt: "لا بيانات ارتفاع في هذا النطاق",
    trafficAria: "أعداد الطائرات الجوية والأرضية عبر الزمن",
    altAria: "أقصى ارتفاع ضغطي عبر الزمن",
  },

  alerts: {
    title: "حالة طوارئ في المجال",
    aria: "تنبيهات الطوارئ",
    hijack: "اختطاف محتمل",
    radioFail: "عطل اتصالات",
    emergency: "طوارئ عامة",
  },

  map: {
    aria: "خريطة الحركة الجوية",
    tileWarning: "تعذّر الوصول لبلاطات الخريطة — الطائرات والإحصاءات مستمرة.",
    unavailable: "الخريطة غير متاحة",
    unavailableSub: "الإحصاءات وقائمة الأهداف تعملان.",
  },

  units: {
    ft: "قدم",
    kt: "عقدة",
    fpm: "قدم/د",
    noCallsign: "بدون نداء",
  },
} as const;
