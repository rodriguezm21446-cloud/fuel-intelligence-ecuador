// Kept self-contained so Wrangler can upload it with --no-bundle on restricted hosts.
const TOPIC_WORDS = [
  "gasolina", "ecopais", "extra", "super", "diesel", "combustible",
  "petroleo", "crudo", "brent", "wti", "opep", "opec", "refineria",
  "subsidio", "hidrocarburo", "derivados", "petroecuador", "arch",
  "gasoline", "diesel fuel", "crude oil", "refinery", "refining", "inventory",
  "inventories", "shortage", "outage", "volatility", "rbob", "ulsd"
];
const TRACKING_KEYS = new Set([
  "fbclid", "gclid", "mc_cid", "mc_eid", "utm_campaign", "utm_content",
  "utm_medium", "utm_source", "utm_term"
]);

function normalizeText(value = "") {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/<[^>]*>/g, " ").replace(/&amp;/gi, "&").replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'").replace(/\s+/g, " ").trim().toLowerCase();
}

function isRelevant(title = "", summary = "") {
  const text = normalizeText(`${title} ${summary}`);
  return TOPIC_WORDS.some((word) => text.includes(word));
}

function isRelevantCandidate(source, raw) {
  if (source.id === "petroecuador-historico") {
    return /\/ESTRUCTURA-DE-PRECIOS[^/]*\.pdf(?:$|\?)/i.test(raw.url);
  }
  return isRelevant(raw.title, raw.summary);
}

function isDomesticPriceSignal(source, raw) {
  const domestic = new Set([
    "eluniverso-economia-rss", "primicias-economia", "ecuavisa-economia"
  ]);
  if (!domestic.has(source.id)) return false;
  const text = normalizeText(`${raw.title} ${raw.summary}`);
  const hasFuel = ["gasolina", "ecopais", "extra", "super", "diesel", "combustible"]
    .some((word) => text.includes(word));
  const hasPriceEvent = ["precio", "sube", "baja", "aumento", "incremento", "reduccion", "vigencia"]
    .some((word) => text.includes(word));
  return hasFuel && hasPriceEvent;
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
    if (match) return normalizeText(match[1].replace(/^<!\[CDATA\[|\]\]>$/g, ""));
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
    if (title && url) results.push({ title, url, summary });
  }
  return results;
}

function extractLinkCandidates(html, baseUrl) {
  const results = [];
  const seen = new Set();
  const anchor = /<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = anchor.exec(html)) && results.length < 100) {
    const title = normalizeText(match[2]);
    const url = canonicalUrl(match[1], baseUrl);
    if (!title || !url || seen.has(url) || !/^https?:/i.test(url)) continue;
    seen.add(url);
    results.push({ title, url, summary: "" });
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
  { id: "arch-precios", name: "ARCH - Precios de derivados", url: "https://controlhidrocarburos.gob.ec/precios-gasolinas-extra-y-ecopais/", kind: "html", trust: 100, pollSeconds: 60 },
  { id: "arch-biblioteca", name: "ARCH - Biblioteca y resoluciones", url: "https://controlhidrocarburos.gob.ec/biblioteca/", kind: "links", trust: 100, pollSeconds: 120 },
  { id: "presidencia-decretos", name: "Presidencia - Decretos", url: "https://minka.presidencia.gob.ec/", kind: "links", trust: 100, pollSeconds: 120 },
  { id: "bce-petroleo", name: "BCE - Estadísticas petroleras", url: "https://contenido.bce.fin.ec/?bcedata_grid_app=1", kind: "html", trust: 100, pollSeconds: 1800 },
  { id: "petroecuador-precios", name: "EP Petroecuador - Estructuras de precios", url: "https://www.eppetroecuador.ec/?s=estructura+de+precios", kind: "links", trust: 100, pollSeconds: 60 },
  { id: "petroecuador-historico", name: "EP Petroecuador - Histórico mensual de precios en terminal", url: "https://www.eppetroecuador.ec/?p=20421", kind: "links", trust: 100, pollSeconds: 60 },
  { id: "eluniverso-economia-rss", name: "El Universo - Economía", url: "https://www.eluniverso.com/arc/outboundfeeds/rss-subsection/noticias/economia?outputType=xml", kind: "rss", trust: 75, pollSeconds: 60 },
  { id: "primicias-economia", name: "Primicias - Economía", url: "https://www.primicias.ec/economia/", kind: "links", trust: 75, pollSeconds: 120 },
  { id: "ecuavisa-economia", name: "Ecuavisa - Economía", url: "https://www.ecuavisa.com/economia", kind: "links", trust: 75, pollSeconds: 60 },
  { id: "opec-press", name: "OPEP - Comunicados", url: "https://www.opec.org/", kind: "links", trust: 95, pollSeconds: 300 },
  { id: "cnbc-finance-rss", name: "CNBC - Finance RSS", url: "https://www.cnbc.com/id/10000664/device/rss/rss.html", kind: "rss", trust: 82, pollSeconds: 60 },
  { id: "eia-weekly-petroleum", name: "EIA - Weekly Petroleum Status Report", url: "https://www.eia.gov/petroleum/supply/weekly/", kind: "html", trust: 100, pollSeconds: 300 }
];

const USER_AGENT = "FuelIntelligenceEcuador/0.2 (+verified private monitor)";

function discover(source, body, finalUrl) {
  if (source.kind === "rss") return extractRssCandidates(body, finalUrl);
  if (source.kind === "links") return extractLinkCandidates(body, finalUrl);
  const title = normalizeText(body.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || source.name);
  return [{ title, url: canonicalUrl(finalUrl, source.url), summary: normalizeText(body).slice(0, 2000) }];
}

async function dispatchGithub(env, candidate, corroboratedBy = "") {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPOSITORY) return { ok: false, waiting: true, error: "GitHub aún no configurado" };
  const workflow = env.GITHUB_WORKFLOW || "cloud-job.yml";
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/actions/workflows/${workflow}/dispatches`, {
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
      }
    })
  });
  if (response.status === 204) return { ok: true, waiting: false, error: "" };
  return { ok: false, waiting: false, error: `GitHub respondió HTTP ${response.status}` };
}

async function saveCandidate(env, source, raw, bootstrap, detectedAt) {
  if (!isRelevantCandidate(source, raw)) return;
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
       WHERE source_id <> ? AND detected_at >= datetime('now', '-12 hours')
       ORDER BY detected_at DESC LIMIT 30`
    ).bind(source.id).all();
    const match = (recent.results || []).find((item) => titleOverlap(raw.title, item.title) >= 0.5);
    if (match) corroboratedBy = match.source_name;
    // Dispatch domestic price reports immediately as unconfirmed warnings.
    // Python still blocks publication until ARCH or Petroecuador agrees.
    if (!match && !isDomesticPriceSignal(source, raw)) return;
  }

  const candidate = { source, ...raw, detectedAt };
  const dispatched = await dispatchGithub(env, candidate, corroboratedBy);
  const nextStatus = dispatched.ok ? "dispatched" : (dispatched.waiting ? "waiting_configuration" : "dispatch_failed");
  await env.DB.prepare(
    `UPDATE event_candidates SET status = ?, dispatch_attempts = dispatch_attempts + 1,
     dispatched_at = CASE WHEN ? = 'dispatched' THEN ? ELSE dispatched_at END, last_error = ?
     WHERE event_key = ?`
  ).bind(nextStatus, nextStatus, detectedAt, dispatched.error || null, eventKey).run();
}

async function pollSource(env, source, state, detectedAt) {
  const headers = { "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/html, application/pdf;q=0.5, */*;q=0.1" };
  if (state?.etag) headers["If-None-Match"] = state.etag;
  if (state?.last_modified) headers["If-Modified-Since"] = state.last_modified;
  try {
    const response = await fetch(source.url, { headers, redirect: "follow" });
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
      for (const candidate of candidates.slice(0, 50)) {
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
    const message = `${error?.name || "Error"}: ${error?.message || String(error)}`.slice(0, 500);
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
    return !Number.isFinite(checkedAt) || nowMs - checkedAt >= (source.pollSeconds || 60) * 1000;
  });
  await Promise.all(due.map((source) => pollSource(env, source, byId.get(source.id), detectedAt)));
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
    return Response.json({
      service: "fuelintel-ecuador",
      status: "ok",
      githubConfigured: Boolean(env.GITHUB_TOKEN && env.GITHUB_REPOSITORY),
      sources: sources.results || [],
      events: events.results || []
    });
  }
};
