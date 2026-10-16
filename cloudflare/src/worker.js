// Kept self-contained so Wrangler can upload it with --no-bundle on restricted hosts.
const TOPIC_WORDS = [
  "gasolina", "ecopais", "extra", "super", "diesel", "combustible",
  "petroleo", "crudo", "brent", "wti", "opep", "opec", "refineria",
  "subsidio", "hidrocarburo", "derivados", "petroecuador", "arch",
  "escasez", "abastecimiento", "desabastecimiento", "gasolinera",
  "expendedor", "octanaje", "petrolero", "oleoducto", "hidrocarburos",
  "gasoline", "diesel fuel", "crude oil", "refinery", "refining", "inventory",
  "inventories", "shortage", "outage", "volatility", "rbob", "ulsd", "oil",
  "pipeline", "tanker", "vessel", "shipping", "maritime", "hormuz", "bab al-mandab",
  "نفط", "الخام", "بنزين", "ديزل", "مصفاة", "هرمز", "ناقلة", "أوبك"
];
const TRACKING_KEYS = new Set([
  "fbclid", "gclid", "mc_cid", "mc_eid", "utm_campaign", "utm_content",
  "utm_medium", "utm_source", "utm_term"
]);

function cleanText(value = "") {
  return value.replace(/[\u0300-\u036f]/g, "")
    .replace(/<[^>]*>/g, " ").replace(/&amp;/gi, "&").replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'").replace(/\s+/g, " ").trim();
}

function normalizeText(value = "") {
  return cleanText(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function isRelevant(title = "", summary = "") {
  const text = normalizeText(`${title} ${summary}`);
  return TOPIC_WORDS.some((word) => ["extra", "super", "arch", "oil"].includes(word)
    ? new RegExp(`(^|[^a-z0-9])${word}([^a-z0-9]|$)`).test(text)
    : text.includes(word));
}

function freshness(publishedAt, detectedAt, hours = 2) {
  if (!publishedAt) return "unknown";
  const age = Date.parse(detectedAt) - Date.parse(publishedAt);
  if (!Number.isFinite(age)) return "unknown";
  if (age < -10 * 60_000) return "future";
  return age > hours * 3600_000 ? "stale" : "fresh";
}

function isBlockedError(value = "") {
  // A blocked or dropped connection is not a code failure, so it must not
  // raise the operator's technical alarm.
  return /bloqueó|no respondió|HTTP (401|403|429|451)/i.test(String(value || ""));
}

// Ecuadorian official sites routinely refuse, throttle or return challenge
// pages to datacenter traffic. The Worker keeps them in /health, but they never
// page the operator: the GitHub runner (different network) is the authority
// that confirms real price or decree changes.
const DATACENTER_BLOCKED = new Set([
  "arch-precios", "arch-biblioteca", "presidencia-decretos",
  "petroecuador-precios", "petroecuador-historico", "bce-petroleo",
]);

function isMutedSource(sourceId, lastError) {
  return DATACENTER_BLOCKED.has(sourceId) || isBlockedError(lastError);
}

function publishedMilliseconds(publishedAt, detectedAt) {
  const parsed = Date.parse(publishedAt || "");
  return Number.isFinite(parsed) ? parsed : Date.parse(detectedAt);
}

function nextPollSeconds(source, state) {
  const errors = Math.min(6, Math.max(0, Number(state?.error_count || 0)));
  // Government sites back off up to an hour to stay polite, but a flaky news
  // feed or aggregator must recover quickly instead of staying silent for
  // hours after a single transient block.
  const ceiling = source.critical ? 3600 : 600;
  return Math.min(ceiling, (source.pollSeconds || 60) * (2 ** errors));
}

function isRelevantCandidate(source, raw) {
  if (source.id === "petroecuador-historico") {
    return /\/ESTRUCTURA-DE-PRECIOS[^/]*\.pdf(?:$|\?)/i.test(raw.url);
  }
  return isRelevant(raw.title, raw.summary);
}

function isDomesticPriceSignal(source, raw) {
  const domestic = new Set([
    "eluniverso-economia-rss", "primicias-economia", "ecuavisa-economia", "elcomercio-rss",
    "expreso-economia", "lahora-economia", "teleamazonas-economia", "eltelegrafo-economia",
    "google-news-ecuador-combustibles"
    ,"x-daniel-noboa", "x-petroecuador"
  ]);
  if (!domestic.has(source.id)) return false;
  const text = normalizeText(`${raw.title} ${raw.summary}`);
  // Ecuador-focused energy news, not only a confirmed price change: a supply
  // scare, an import, a refinery outage or a decree all matter to the operator.
  const hasEnergy = [
    "gasolina", "ecopais", "extra", "super", "diesel", "combustible",
    "hidrocarburo", "petroleo", "crudo", "gasolinera", "refineria", "subsidio",
  ].some((word) => text.includes(word));
  const hasEvent = [
    "precio", "sube", "baja", "aumento", "incremento", "reduccion", "vigencia",
    "escasez", "abastecimiento", "desabastecimiento", "importacion", "paralizacion",
    "alza", "rebaja", "decreto", "resolucion", "subsidio",
  ].some((word) => text.includes(word));
  return hasEnergy && hasEvent;
}

function isMarketSquawkSignal(source, raw) {
  const fastSources = new Set([
    "cnbc-energy-rss", "cnbc-finance-rss", "oilprice-main-rss",
    "telegram-aja-news", "telegram-walter-bloomberg", "ukmto-maritime-security",
    "x-bloomberg"
  ]);
  if (!fastSources.has(source.id)) return false;
  const text = normalizeText(`${raw.title} ${raw.summary}`);
  const market = ["oil", "crude", "brent", "wti", "gasoline", "diesel", "refinery", "rbob", "ulsd",
    "pipeline", "tanker", "vessel", "shipping", "maritime", "hormuz", "bab al-mandab",
    "نفط", "الخام", "بنزين", "ديزل", "مصفاة", "هرمز", "ناقلة"]
    .some((word) => text.includes(word));
  const event = ["surge", "jump", "rise", "fall", "drop", "attack", "outage", "shutdown", "pipeline",
    "sanction", "inventory", "shortage", "opec", "supply", "disruption",
    "ارتفاع", "انخفاض", "هبوط", "هجوم", "تعطل", "إغلاق", "عقوبات", "إمدادات"]
    .some((word) => text.includes(word));
  return market && event;
}

function isOfficialFuelSource(source) {
  return new Set([
    "arch-precios", "arch-biblioteca", "presidencia-decretos",
    "petroecuador-precios", "petroecuador-historico"
  ]).has(source.id);
}

function escapeHtml(value = "") {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function sendFastTelegramAlarm(env, candidate, alarmKind) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
    return { ok: false, waiting: true, error: "Telegram directo aún no configurado" };
  }
  const qualification = alarmKind === "domestic"
    ? "Reporte preliminar; aún no es precio oficial."
    : alarmKind === "market"
      ? "Impacto en Ecuador aún no confirmado."
      : "Publicación oficial detectada; cifras en verificación.";
  const suffix = ` ${qualification} Fuente: ${candidate.source.name}.`;
  const maximumTitle = Math.max(40, 300 - suffix.length);
  const clippedTitle = candidate.title.length <= maximumTitle
    ? candidate.title
    : `${candidate.title.slice(0, maximumTitle - 1).trimEnd()}…`;
  const message = `${clippedTitle}${suffix}`.slice(0, 300);
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    signal: AbortSignal.timeout(8000),
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text: message,
      disable_web_page_preview: true
    })
  });
  if (response.ok && (await response.json()).ok === true) return { ok: true, waiting: false, error: "" };
  return { ok: false, waiting: false, error: `Telegram respondió HTTP ${response.status}` };
}

function canonicalUrl(value, base) {
  try {
    const url = new URL(value, base);
    url.hash = "";
    for (const key of [...url.searchParams.keys()]) {
      if (TRACKING_KEYS.has(key.toLowerCase()) || key.toLowerCase().startsWith("utm_")) url.searchParams.delete(key);
    }
    if (url.pathname !== "/") url.pathname = url.pathname.replace(/\/+$/, "");
    return url.toString();
  } catch { return ""; }
}

function tagValue(block, names) {
  for (const name of names) {
    const match = block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) return cleanText(match[1].replace(/^<!\[CDATA\[|\]\]>$/g, ""));
  }
  return "";
}

function rawTagValue(block, names) {
  for (const name of names) {
    const match = block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) {
      return match[1].replace(/^<!\[CDATA\[|\]\]>$/g, "").replace(/<[^>]*>/g, " ")
        .replace(/&amp;/gi, "&").replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
        .replace(/\s+/g, " ").trim();
    }
  }
  return "";
}

function extractRssCandidates(xml, baseUrl) {
  const blocks = xml.match(/<(?:item|entry)\b[\s\S]*?<\/(?:item|entry)>/gi) || [];
  const results = [];
  for (const block of blocks.slice(0, 50)) {
    const title = tagValue(block, ["title"]);
    const textLink = rawTagValue(block, ["link", "guid"]);
    const hrefMatch = block.match(/<link[^>]+href=["']([^"']+)["']/i);
    const url = canonicalUrl(hrefMatch?.[1] || textLink, baseUrl);
    const summary = tagValue(block, ["description", "summary", "content"]);
    const publishedAt = rawTagValue(block, ["pubDate", "published", "updated", "dc:date"]);
    if (title && url) results.push({ title, url, summary, publishedAt });
  }
  return results;
}

function extractLinkCandidates(html, baseUrl) {
  const results = [];
  const seen = new Set();
  const anchor = /<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = anchor.exec(html)) && results.length < 2000) {
    const title = cleanText(match[2]);
    const url = canonicalUrl(match[1], baseUrl);
    if (!title || !url || seen.has(url) || !/^https?:/i.test(url)) continue;
    seen.add(url);
    results.push({ title, url, summary: "" });
  }
  return results;
}

function extractTelegramCandidates(html) {
  const results = [];
  const blocks = html.match(/<div class="tgme_widget_message_wrap[^"]*"[\s\S]*?(?=<div class="tgme_widget_message_wrap|<\/section>|$)/gi) || [];
  for (const block of blocks.slice(-50)) {
    const post = block.match(/data-post=["']([^"']+)["']/i)?.[1];
    const text = block.match(/<div class="tgme_widget_message_text[^>]*>([\s\S]*?)<\/div>/i)?.[1] || "";
    const title = cleanText(text);
    const publishedAt = block.match(/<time[^>]+datetime=["']([^"']+)["']/i)?.[1] || "";
    if (post && title) results.push({ title: title.slice(0, 500), url: `https://t.me/${post}`, summary: title, publishedAt });
  }
  return results;
}

function titleOverlap(first, second) {
  const ignored = new Set(["para", "como", "desde", "hasta", "sobre", "esta", "este", "estas", "estos", "ecuador"]);
  const tokens = (value) => new Set(normalizeText(value).split(/[^a-z0-9]+/).filter((word) => word.length >= 4 && !ignored.has(word)));
  const a = tokens(first); const b = tokens(second);
  if (!a.size || !b.size) return 0;
  let common = 0;
  for (const word of a) if (b.has(word)) common += 1;
  return common / Math.min(a.size, b.size);
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const SOURCES = [
  { id: "arch-precios", name: "ARCH - Precios de derivados", url: "https://controlhidrocarburos.gob.ec/precios-gasolinas-extra-y-ecopais/", kind: "html", trust: 100, pollSeconds: 60, critical: true },
  { id: "arch-biblioteca", name: "ARCH - Biblioteca y resoluciones", url: "https://controlhidrocarburos.gob.ec/biblioteca/", kind: "links", trust: 100, pollSeconds: 120, critical: true },
  { id: "presidencia-decretos", name: "Presidencia - Decretos", url: "https://minka.presidencia.gob.ec/portal/usuarios_externos.jsf?wpmobileexternal=true", kind: "html", trust: 100, pollSeconds: 120, critical: false },
  { id: "bce-petroleo", name: "BCE - Estadísticas petroleras", url: "https://contenido.bce.fin.ec/?bcedata_grid_app=1", kind: "html", trust: 100, pollSeconds: 1800 },
  { id: "petroecuador-precios", name: "EP Petroecuador - Estructuras de precios", url: "https://www.eppetroecuador.ec/?s=estructura+de+precios", kind: "links", trust: 100, pollSeconds: 60, critical: true },
  { id: "petroecuador-historico", name: "EP Petroecuador - Histórico mensual de precios en terminal", url: "https://www.eppetroecuador.ec/?p=20421", kind: "links", trust: 100, pollSeconds: 60, critical: true },
  { id: "eluniverso-economia-rss", name: "El Universo - Economía", url: "https://www.eluniverso.com/arc/outboundfeeds/rss-subsection/noticias/economia?outputType=xml", kind: "rss", trust: 75, pollSeconds: 60 },
  { id: "primicias-economia", name: "Primicias - Economía", url: "https://www.primicias.ec/economia/", kind: "links", trust: 75, pollSeconds: 120 },
  { id: "ecuavisa-economia", name: "Ecuavisa - Economía", url: "https://www.ecuavisa.com/economia", kind: "links", trust: 75, pollSeconds: 60 },
  { id: "opec-press", name: "OPEP - Comunicados", url: "https://www.opec.org/", kind: "links", trust: 95, pollSeconds: 300 },
  { id: "cnbc-finance-rss", name: "CNBC - Finance RSS", url: "https://www.cnbc.com/id/10000664/device/rss/rss.html", kind: "rss", trust: 82, pollSeconds: 60 },
  { id: "cnbc-energy-rss", name: "CNBC - Energy RSS", url: "https://www.cnbc.com/id/19836768/device/rss/rss.html", kind: "rss", trust: 82, pollSeconds: 60 },
  { id: "oilprice-main-rss", name: "OilPrice.com - Energy RSS", url: "https://oilprice.com/rss/main", kind: "rss", trust: 78, pollSeconds: 60 },
  { id: "cbs-moneywatch-rss", name: "CBS News - MoneyWatch RSS", url: "https://www.cbsnews.com/latest/rss/moneywatch", kind: "rss", trust: 82, pollSeconds: 60 },
  { id: "nyt-energy-rss", name: "The New York Times - Energy and Environment RSS", url: "https://rss.nytimes.com/services/xml/rss/nyt/EnergyEnvironment.xml", kind: "rss", trust: 82, pollSeconds: 120 },
  { id: "yahoo-finance-energy-rss", name: "Google News - Yahoo Finance energía (agregador)", url: "https://news.google.com/rss/search?q=%28oil%20OR%20crude%20OR%20gasoline%20OR%20diesel%20OR%20refinery%20OR%20OPEC%29%20site%3Afinance.yahoo.com&hl=en-US&gl=US&ceid=US%3Aen", kind: "rss", trust: 72, pollSeconds: 60 },
  { id: "doe-energy-news", name: "U.S. Department of Energy - Energy News", url: "https://www.energy.gov/listings/energy-news?view=rss", kind: "links", trust: 100, pollSeconds: 300 },
  { id: "eia-today-energy-rss", name: "EIA - Today in Energy RSS", url: "https://www.eia.gov/rss/todayinenergy.xml", kind: "rss", trust: 100, pollSeconds: 300 },
  { id: "eia-gasoline-diesel-rss", name: "EIA - Gasoline and Diesel Fuel Update RSS", url: "https://www.eia.gov/petroleum/gasdiesel/includes/gas_diesel_rss.xml", kind: "rss", trust: 100, pollSeconds: 300 },
  { id: "eia-weekly-petroleum", name: "EIA - Weekly Petroleum Status Report", url: "https://www.eia.gov/petroleum/supply/weekly/", kind: "html", trust: 100, pollSeconds: 300 }
  ,{ id: "telegram-aja-news", name: "Al Jazeera Arabic — Telegram oficial", url: "https://t.me/s/AjaNews", kind: "telegram", trust: 85, pollSeconds: 60 }
  ,{ id: "telegram-walter-bloomberg", name: "Walter Bloomberg — Telegram (agregador)", url: "https://t.me/s/WalterBloomberg", kind: "telegram", trust: 82, pollSeconds: 60 }
  ,{ id: "google-news-ecuador-combustibles", name: "Google News — combustibles Ecuador (agregador)", url: "https://news.google.com/rss/search?q=%28gasolina%20OR%20diesel%20OR%20combustible%20OR%20Petroecuador%29%20Ecuador&hl=es-419&gl=EC&ceid=EC:es-419", kind: "rss", trust: 72, pollSeconds: 60 }
  ,{ id: "elcomercio-rss", name: "El Comercio — RSS general", url: "https://www.elcomercio.com/feed/", kind: "rss", trust: 75, pollSeconds: 60 }
  ,{ id: "expreso-economia", name: "Expreso — Economía y negocios", url: "https://www.expreso.ec/economia-y-negocios/", kind: "links", trust: 75, pollSeconds: 60 }
  ,{ id: "lahora-economia", name: "Google News — La Hora combustibles", url: "https://news.google.com/rss/search?q=%28gasolina%20OR%20diesel%20OR%20combustible%20OR%20petroleo%29%20site%3Alahora.com.ec&hl=es-419&gl=EC&ceid=EC:es-419", kind: "rss", trust: 72, pollSeconds: 60 }
  ,{ id: "teleamazonas-economia", name: "Teleamazonas — Economía", url: "https://imagenes.teleamazonas.com/actualidad/noticias/economia/", kind: "links", trust: 72, pollSeconds: 60 }
  ,{ id: "eltelegrafo-economia", name: "Google News — El Telégrafo combustibles", url: "https://news.google.com/rss/search?q=%28gasolina%20OR%20diesel%20OR%20combustible%20OR%20petroleo%29%20site%3Aeltelegrafo.com.ec&hl=es-419&gl=EC&ceid=EC:es-419", kind: "rss", trust: 72, pollSeconds: 60 }
  ,{ id: "ukmto-maritime-security", name: "UKMTO — avisos oficiales de seguridad marítima", url: "https://www.ukmto.org/ukmto-products", kind: "links", trust: 95, pollSeconds: 300 }
];

const USER_AGENT = "FuelIntelligenceEcuador/0.2 (+verified private monitor)";

const X_ACCOUNTS = [
  { id: "x-daniel-noboa", username: "DanielNoboaOk", name: "X · @DanielNoboaOk", trust: 95 },
  { id: "x-petroecuador", username: "EPPetroecuador", name: "X · @EPPetroecuador", trust: 95 },
  { id: "x-bloomberg", username: "business", name: "X · @business", trust: 85 }
].map(item => ({...item, url: `https://x.com/${item.username}`, kind: "x"}));

function xPostRelevant(username, text) {
  const value = normalizeText(text);
  if (username.toLowerCase() === "business") {
    return /\b(oil|crude|brent|wti|diesel|gasoline|refinery|tanker|hormuz|ecuador)\b/.test(value);
  }
  return /\b(gasolina|ecopais|diesel|combustibles?|petroleo|crudo|petroecuador|refineria|hidrocarburos|oleoducto)\b/.test(value);
}

async function pollX(env, detectedAt) {
  // X charges for data access. Activation is explicit; never query by default.
  if (env.FUELINTEL_X_ENABLED !== "true" || !env.X_BEARER_TOKEN) return;
  const key = "__x_recent";
  const state = await env.DB.prepare("SELECT * FROM source_state WHERE source_id = ?").bind(key).first();
  if (state && Date.parse(detectedAt) - Date.parse(state.checked_at) < 600_000) return;
  // Reserve the interval before the request, including on failures/rate limits.
  await env.DB.prepare(`INSERT INTO source_state(source_id, body_hash, checked_at) VALUES (?, '', ?)
    ON CONFLICT(source_id) DO UPDATE SET checked_at=excluded.checked_at`).bind(key, detectedAt).run();
  try {
    const url = new URL("https://api.x.com/2/tweets/search/recent");
    url.searchParams.set("query", "(from:DanielNoboaOk OR from:EPPetroecuador OR from:business) (gasolina OR diesel OR combustible OR petróleo OR oil OR crude OR refinery OR tanker OR Ecuador) -is:retweet");
    url.searchParams.set("max_results", "10");
    url.searchParams.set("tweet.fields", "created_at,author_id");
    url.searchParams.set("expansions", "author_id");
    url.searchParams.set("user.fields", "username");
    if (state?.etag) url.searchParams.set("since_id", state.etag);
    const response = await fetch(url, {signal: AbortSignal.timeout(12000), headers: {Authorization: `Bearer ${env.X_BEARER_TOKEN}`}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (result.errors?.length) throw new Error("X devolvió resultados incompletos");
    const users = new Map((result.includes?.users || []).map(user => [user.id, user.username]));
    for (const post of [...(result.data || [])].reverse()) {
      const username = users.get(post.author_id);
      const account = X_ACCOUNTS.find(item => item.username.toLowerCase() === username?.toLowerCase());
      if (!account || !xPostRelevant(username, post.text)) continue;
      await saveCandidate(env, account, {
        title: post.text.slice(0, 500), summary: post.text, url: `https://x.com/${username}/status/${post.id}`,
        publishedAt: post.created_at
      }, !state?.body_hash, detectedAt);
    }
    // Do not skip unseen pages: hold the cursor and flag saturation for review.
    if (result.meta?.next_token) throw new Error("X: hay más resultados que el límite; revisar cobertura");
    await env.DB.prepare("UPDATE source_state SET etag = ?, body_hash = 'initialized', error_count = 0, last_error = NULL WHERE source_id = ?")
      .bind(result.meta?.newest_id || state?.etag || null, key).run();
  } catch (error) {
    await env.DB.prepare("UPDATE source_state SET error_count = error_count + 1, last_error = ? WHERE source_id = ?")
      .bind(`X: ${error?.message?.startsWith("HTTP") ? error.message : "lectura incompleta o conexión fallida"}`, key).run();
  }
}

function discover(source, body, finalUrl) {
  if (source.kind === "rss") return extractRssCandidates(body, finalUrl);
  if (source.kind === "telegram") return extractTelegramCandidates(body);
  if (source.kind === "links") return extractLinkCandidates(body, finalUrl);
  const title = cleanText(body.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || source.name);
  return [{ title, url: canonicalUrl(finalUrl, source.url), summary: normalizeText(body).slice(0, 2000) }];
}

async function dispatchGithub(env, candidate, corroboratedBy = "") {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPOSITORY) return { ok: false, waiting: true, error: "GitHub aún no configurado" };
  const workflow = env.GITHUB_WORKFLOW || "cloud-job.yml";
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/actions/workflows/${workflow}/dispatches`, {
    signal: AbortSignal.timeout(8000),
    method: "POST",
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": USER_AGENT,
      "X-GitHub-Api-Version": "2022-11-28"
    },
    body: JSON.stringify({
      ref: env.GITHUB_REF || "main",
      inputs: {
        source_id: candidate.source.id,
        source_name: candidate.source.name,
        source_url: candidate.source.url,
        event_url: candidate.url,
        event_title: candidate.title.slice(0, 500),
        detected_at: candidate.detectedAt,
        corroborated_by: corroboratedBy
        ,event_summary: candidate.summary?.slice(0, 5000) || ""
        ,event_published_at: candidate.publishedAt || ""
      }
    })
  });
  if (response.status === 204) return { ok: true, waiting: false, error: "" };
  return { ok: false, waiting: false, error: `GitHub respondió HTTP ${response.status}` };
}

async function saveCandidate(env, source, raw, bootstrap, detectedAt) {
  if (!isRelevantCandidate(source, raw)) return;
  // Urgent news must actually be recent. Official price documents are handled
  // separately because a current effective period may outlive publication.
  const current = freshness(raw.publishedAt, detectedAt, isOfficialFuelSource(source) ? 24 : 2);
  if (current === "stale" || current === "future") return;
  const eventKey = await sha256(`${source.id}\n${raw.url}\n${normalizeText(raw.title)}`);
  const status = bootstrap ? "baseline" : (source.trust >= 90 ? "ready" : "pending_corroboration");
  const inserted = await env.DB.prepare(
    `INSERT OR IGNORE INTO event_candidates
     (event_key, source_id, source_name, title, url, detected_at, status)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  ).bind(eventKey, source.id, source.name, raw.title.slice(0, 500), raw.url, detectedAt, status).run();
  if (!inserted.meta?.changes || bootstrap) return;

  let corroboratedBy = "";
  if (source.trust < 90) {
    const recent = await env.DB.prepare(
      `SELECT source_id, source_name, title FROM event_candidates
       WHERE source_id <> ? AND julianday(detected_at) >= julianday('now', '-12 hours')
         AND status NOT IN ('baseline', 'stale', 'rejected')
       ORDER BY detected_at DESC LIMIT 30`
    ).bind(source.id).all();
    const matches = (recent.results || []).filter((item) => titleOverlap(raw.title, item.title) >= 0.5);
    const match = matches[0];
    if (match) corroboratedBy = match.source_name;
    // Dispatch domestic price reports immediately as unconfirmed warnings.
    // Python still blocks publication until ARCH or Petroecuador agrees.
    if (!match && !isDomesticPriceSignal(source, raw) && !isMarketSquawkSignal(source, raw)) return;
    // First source raises the alarm; a second source corroborates it. Further
    // copies are stored for audit but do not start more runners or messages.
    // Different numbers or dates can be corrections, even with similar titles.
    const numbers = (text) => (text.match(/\d+(?:[.,]\d+)?/g) || []).join("|");
    if (matches.length >= 2 && matches.every(item => numbers(item.title) === numbers(raw.title))) return;
  }

  const candidate = { source, ...raw, detectedAt };
  const alarmKind = isDomesticPriceSignal(source, raw)
    ? "domestic"
    : isOfficialFuelSource(source)
      ? "official"
      : isMarketSquawkSignal(source, raw)
        ? "market"
        : "";
  // This alarm is deliberately simple and source-attributed. It reaches the
  // operator before GitHub starts Python/Chromium; the detailed validation and
  // chart arrive later from the cloud job.
  const fastAlarm = alarmKind && current === "fresh"
    ? await sendFastTelegramAlarm(env, candidate, alarmKind).catch(() => ({ ok: false, error: "Telegram: fallo de conexión" }))
    : { ok: false, waiting: true, error: "" };
  const dispatched = await dispatchGithub(env, candidate, corroboratedBy)
    .catch(() => ({ ok: false, error: "GitHub: fallo de conexión" }));
  const nextStatus = dispatched.ok ? "dispatched" : (dispatched.waiting ? "waiting_configuration" : "dispatch_failed");
  await env.DB.prepare(
    `UPDATE event_candidates SET status = ?, dispatch_attempts = dispatch_attempts + 1,
     dispatched_at = CASE WHEN ? = 'dispatched' THEN ? ELSE dispatched_at END, last_error = ?
     WHERE event_key = ?`
  ).bind(
    nextStatus,
    nextStatus,
    detectedAt,
    [fastAlarm.waiting ? "" : fastAlarm.error, dispatched.error].filter(Boolean).join("; ") || null,
    eventKey
  ).run();
}

async function pollSource(env, source, state, detectedAt) {
  const headers = { "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/html, application/pdf;q=0.5, */*;q=0.1" };
  if (state?.etag) headers["If-None-Match"] = state.etag;
  if (state?.last_modified) headers["If-Modified-Since"] = state.last_modified;
  try {
    const response = await fetch(source.url, { headers, redirect: "follow", signal: AbortSignal.timeout(12000) });
    if (response.status === 304) {
      await env.DB.prepare("UPDATE source_state SET checked_at = ?, error_count = 0, last_error = NULL WHERE source_id = ?").bind(detectedAt, source.id).run();
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.text();
    const bodyHash = await sha256(body);
    // A failed first request creates a state row with an empty hash. Keep the
    // next successful request in baseline mode so old articles are never sent.
    const bootstrap = !state?.body_hash;
    if (state?.body_hash !== bodyHash) {
      const candidates = discover(source, body, response.url || source.url);
      if (source.kind !== "html" && candidates.length === 0) {
        // A response that is not a feed at all (HTML challenge page, consent
        // wall, aggregator block) is a blocked read, not a code failure. It is
        // reported in /health and never pages the operator. A valid but empty
        // feed is normal and must not count as an error either.
        if (!/<(?:rss|feed|channel|item|entry)\b/i.test(body.slice(0, 4000))) {
          throw new Error("el servidor bloqueó la lectura del feed (la respuesta no es un feed; reintento programado)");
        }
      }
      // Navigation links often occupy the first half of a news page. Filter
      // before limiting, otherwise a relevant new article can be silently lost.
      const relevantCandidates = candidates.filter((candidate) => isRelevantCandidate(source, candidate));
      // Deliver the newest publications first so a fresh item is never delayed
      // behind an older one that changed in the same poll.
      const ordered = [...relevantCandidates].sort(
        (a, b) => publishedMilliseconds(b.publishedAt, detectedAt) - publishedMilliseconds(a.publishedAt, detectedAt)
      );
      for (const candidate of ordered.slice(0, 50)) {
        await saveCandidate(env, source, candidate, bootstrap, detectedAt);
      }
    }
    await env.DB.prepare(
      `INSERT INTO source_state(source_id, etag, last_modified, body_hash, checked_at, error_count, last_error)
       VALUES (?, ?, ?, ?, ?, 0, NULL)
       ON CONFLICT(source_id) DO UPDATE SET etag=excluded.etag, last_modified=excluded.last_modified,
       body_hash=excluded.body_hash, checked_at=excluded.checked_at, error_count=0, last_error=NULL`
    ).bind(source.id, response.headers.get("etag"), response.headers.get("last-modified"), bodyHash, detectedAt).run();
  } catch (error) {
    const status = Number(String(error?.message || "").match(/HTTP (\d+)/)?.[1]);
    const detail = String(error?.message || "").slice(0, 120);
    const refused = [401, 403, 429, 451].includes(status);
    const timedOut = /timeout|abort/i.test(`${error?.name || ""} ${detail}`);
    const message = refused
      ? `HTTP ${status}: el servidor bloqueó la lectura automática (reintento programado)`
      : timedOut
        ? "Sin respuesta: el servidor no respondió a la lectura automática desde el centro de datos (reintento programado)"
        : `${error?.name || "Error"}: ${detail || "fallo de lectura o procesamiento"}`;
    await env.DB.prepare(
      `INSERT INTO source_state(source_id, body_hash, checked_at, error_count, last_error)
       VALUES (?, '', ?, 1, ?)
       ON CONFLICT(source_id) DO UPDATE SET checked_at=excluded.checked_at,
       error_count=source_state.error_count+1, last_error=excluded.last_error`
    ).bind(source.id, detectedAt, message).run();
  }
}

async function pollAll(env) {
  const detectedAt = new Date().toISOString();
  const nowMs = Date.parse(detectedAt);
  const states = await env.DB.prepare("SELECT * FROM source_state").all();
  const byId = new Map((states.results || []).map((state) => [state.source_id, state]));
  const due = SOURCES.filter((source) => {
    const checkedAt = Date.parse(byId.get(source.id)?.checked_at || "");
    return !Number.isFinite(checkedAt) || nowMs - checkedAt >= nextPollSeconds(source, byId.get(source.id)) * 1000;
  });
  // Bound connections and failures so a broken site cannot stall all sources.
  for (let offset = 0; offset < due.length; offset += 4) {
    await Promise.allSettled(due.slice(offset, offset + 4).map((source) => pollSource(env, source, byId.get(source.id), detectedAt)));
  }
  await retryDispatches(env, detectedAt);
  await pollX(env, detectedAt);
  await reportOperationalHealth(env, detectedAt);
}

async function reportOperationalHealth(env, detectedAt) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  const failed = await env.DB.prepare(
    "SELECT source_id, last_error FROM source_state WHERE error_count >= 3 AND source_id NOT LIKE '__monitor%'").all();
  const backlog = await env.DB.prepare(
    "SELECT COUNT(*) AS total FROM event_candidates WHERE status = 'dispatch_failed' AND dispatch_attempts >= 4 AND julianday(detected_at) >= julianday('now', '-24 hours')").first();
  // A blocked or timed-out source is reported in /health but never spams the
  // operator: the technical alarm only covers sources that truly break.
  const failedIds = (failed.results || [])
    .filter(row => !isMutedSource(row.source_id, row.last_error))
    .map(row => row.source_id)
    .sort();
  const criticalSet = new Set(SOURCES.filter(source => source.critical).map(source => source.id));
  const criticalIds = failedIds.filter(id => criticalSet.has(id));
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPOSITORY) criticalIds.push("github-sin-configurar");
  if (Number(backlog?.total || 0)) criticalIds.push(`envíos-agotados:${backlog.total}`);
  // Only genuinely critical (non-muted) sources page the operator. A flaky news
  // feed stays visible in /health and must never look like a coverage failure.
  const fingerprint = criticalIds.map(id => `critical:${id}`).join(",") || "healthy";
  const previous = await env.DB.prepare("SELECT * FROM source_state WHERE source_id = '__monitor_health'").first();
  if (fingerprint === "healthy" && (!previous || previous.body_hash === "healthy")) return;
  const repeatAfter = criticalIds.length ? 6 * 3600_000 : 24 * 3600_000;
  if (previous?.body_hash === fingerprint && Date.parse(detectedAt) - Date.parse(previous.checked_at) < repeatAfter) return;
  const message = criticalIds.length
    ? `Aviso técnico de combustibleEc\nCobertura crítica: ${criticalIds.join(", ")}.\nLas demás fuentes continúan activas.`
    : "combustibleEc: las fuentes y los envíos vigilados se han recuperado.";
  try {
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST", signal: AbortSignal.timeout(8000), headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: message })
    });
    if (!response.ok || !(await response.json()).ok) return;
    await env.DB.prepare(
      `INSERT INTO source_state(source_id, body_hash, checked_at) VALUES ('__monitor_health', ?, ?)
       ON CONFLICT(source_id) DO UPDATE SET body_hash=excluded.body_hash, checked_at=excluded.checked_at`
    ).bind(fingerprint, detectedAt).run();
  } catch { /* Do not disclose tokens in fetch exceptions. Retry next tick. */ }
}

async function retryDispatches(env, detectedAt) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPOSITORY) return;
  const rows = await env.DB.prepare(
    `SELECT * FROM event_candidates
     WHERE status IN ('dispatch_failed', 'waiting_configuration') AND dispatch_attempts < 4
       AND julianday(detected_at) >= julianday('now', '-24 hours')
     ORDER BY detected_at ASC LIMIT 3`
  ).all();
  for (const row of rows.results || []) {
    const source = SOURCES.find(item => item.id === row.source_id);
    if (!source) continue;
    const result = await dispatchGithub(env, { source, url: row.url, title: row.title, detectedAt: row.detected_at })
      .catch(() => ({ ok: false, error: "GitHub: fallo de conexión" }));
    await env.DB.prepare(
      `UPDATE event_candidates SET status = ?, dispatch_attempts = dispatch_attempts + 1,
       dispatched_at = ?, last_error = ? WHERE event_key = ?`
    ).bind(result.ok ? "dispatched" : "dispatch_failed", result.ok ? detectedAt : null, result.error || null, row.event_key).run();
  }
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(pollAll(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/admin/run" && request.method === "POST") {
      if (!env.SETUP_KEY || request.headers.get("X-Setup-Key") !== env.SETUP_KEY) {
        return new Response("Forbidden", { status: 403 });
      }
      await pollAll(env);
      return Response.json({ status: "completed" });
    }
    if (url.pathname !== "/health") return new Response("Not found", { status: 404 });
    const sources = await env.DB.prepare(
      "SELECT source_id, checked_at, error_count, last_error FROM source_state ORDER BY source_id"
    ).all();
    const events = await env.DB.prepare("SELECT status, COUNT(*) AS total FROM event_candidates GROUP BY status").all();
    const states = sources.results || [];
    const unavailable = SOURCES.filter(source => {
      const state = states.find(row => row.source_id === source.id);
      return !state || state.error_count >= 3 || !Number.isFinite(Date.parse(state.checked_at))
        || Date.now() - Date.parse(state.checked_at) > Math.max(600, source.pollSeconds * 3) * 1000;
    });
    // A source the remote server blocks is reported, but is neither critical
    // nor an outage, so it must not downgrade the service or wake the watchdog.
    const mutedSet = new Set(states.filter(row => isMutedSource(row.source_id, row.last_error)).map(row => row.source_id));
    const criticalUnavailable = unavailable.filter(source => source.critical && !mutedSet.has(source.id));
    const blockedSources = [...mutedSet].sort();
    const missingConfiguration = !env.GITHUB_TOKEN || !env.GITHUB_REPOSITORY;
    return Response.json({
      service: "fuelintel-ecuador",
      status: missingConfiguration || criticalUnavailable.length ? "degraded" : (unavailable.length ? "partial" : "ok"),
      revision: "reliability-2026-10-16",
      unavailableSources: unavailable.map(source => source.id),
      criticalUnavailableSources: criticalUnavailable.map(source => source.id),
      blockedSources,
      directTelegramConfigured: Boolean(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID),
      xEnabled: env.FUELINTEL_X_ENABLED === "true" && Boolean(env.X_BEARER_TOKEN),
      githubConfigured: Boolean(env.GITHUB_TOKEN && env.GITHUB_REPOSITORY),
      sources: sources.results || [],
      events: events.results || []
    });
  }
};
