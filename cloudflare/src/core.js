export const TOPIC_WORDS = [
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

export function normalizeText(value = "") {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function isRelevant(title = "", summary = "") {
  const text = normalizeText(`${title} ${summary}`);
  return TOPIC_WORDS.some((word) => text.includes(word));
}

export function isMarketSquawkSignal(sourceId, title = "", summary = "") {
  if (!["cnbc-energy-rss", "cnbc-finance-rss"].includes(sourceId)) return false;
  const text = normalizeText(`${title} ${summary}`);
  const market = ["oil", "crude", "brent", "wti", "gasoline", "diesel", "refinery", "rbob", "ulsd"]
    .some((word) => text.includes(word));
  const event = ["surge", "jump", "rise", "fall", "drop", "attack", "outage", "shutdown", "pipeline",
    "sanction", "inventory", "shortage", "opec", "supply", "disruption"]
    .some((word) => text.includes(word));
  return market && event;
}

export function isRelevantCandidate(sourceId, title = "", summary = "", url = "") {
  if (sourceId === "petroecuador-historico") {
    return /\/ESTRUCTURA-DE-PRECIOS[^/]*\.pdf(?:$|\?)/i.test(url);
  }
  return isRelevant(title, summary);
}

export function canonicalUrl(value, base) {
  try {
    const url = new URL(value, base);
    url.hash = "";
    for (const key of [...url.searchParams.keys()]) {
      if (TRACKING_KEYS.has(key.toLowerCase()) || key.toLowerCase().startsWith("utm_")) {
        url.searchParams.delete(key);
      }
    }
    if (url.pathname !== "/") url.pathname = url.pathname.replace(/\/+$/, "");
    return url.toString();
  } catch {
    return "";
  }
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
      return match[1]
        .replace(/^<!\[CDATA\[|\]\]>$/g, "")
        .replace(/<[^>]*>/g, " ")
        .replace(/&amp;/gi, "&")
        .replace(/&quot;/gi, '"')
        .replace(/&#39;|&apos;/gi, "'")
        .replace(/\s+/g, " ")
        .trim();
    }
  }
  return "";
}

export function extractRssCandidates(xml, baseUrl) {
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

export function extractLinkCandidates(html, baseUrl) {
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

export function titleOverlap(first, second) {
  const ignored = new Set(["para", "como", "desde", "hasta", "sobre", "esta", "este", "estas", "estos", "ecuador"]);
  const tokens = (value) => new Set(normalizeText(value).split(/[^a-z0-9]+/).filter((word) => word.length >= 4 && !ignored.has(word)));
  const a = tokens(first);
  const b = tokens(second);
  if (!a.size || !b.size) return 0;
  let common = 0;
  for (const word of a) if (b.has(word)) common += 1;
  return common / Math.min(a.size, b.size);
}

export async function sha256(value) {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
