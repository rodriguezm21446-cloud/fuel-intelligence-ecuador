import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// Exercise the actual self-contained deployed worker, not a copy in core.js.
const source = readFileSync(new URL("../src/worker.js", import.meta.url), "utf8");
const context = vm.createContext({ URL, Intl, AbortSignal, TextEncoder });
vm.runInContext(source.replace("export default {", "const worker = {") +
  ";globalThis.api = {freshness,nextPollSeconds,extractRssCandidates,extractLinkCandidates,isRelevant,sendFastTelegramAlarm,xPostRelevant,pollX};", context);
const api = context.api;

test("el detector distingue publicación reciente, antigua, futura y sin fecha", () => {
  const now = "2026-09-13T18:00:00Z";
  assert.equal(api.freshness("2026-09-13T17:59:00Z", now), "fresh");
  assert.equal(api.freshness("2026-09-13T14:59:00Z", now), "stale");
  assert.equal(api.freshness("2026-09-13T14:59:00Z", now, 24), "fresh");
  assert.equal(api.freshness("2026-08-13T17:59:00Z", now), "stale");
  assert.equal(api.freshness("2026-09-14T17:59:00Z", now), "future");
  assert.equal(api.freshness("", now), "unknown");
});

test("RSS conserva fecha para no confundir descubrimiento con publicación", () => {
  const items = api.extractRssCandidates('<rss><item><title>Baja gasolina</title><link>https://example.test/new</link><pubDate>Sun, 13 Sep 2026 17:59:00 GMT</pubDate></item></rss>', "https://example.test");
  assert.equal(items[0].publishedAt, "Sun, 13 Sep 2026 17:59:00 GMT");
});

test("navegación extensa no oculta la noticia después del enlace 100", () => {
  const html = Array.from({length: 150}, (_, i) => `<a href='/menu/${i}'>Menú ${i}</a>`).join("") + '<a href="/gasolina">Baja el precio de la gasolina</a>';
  assert.ok(api.extractLinkCandidates(html, "https://example.test").some(item => item.url.endsWith("/gasolina")));
});

test("fuente caída reduce frecuencia y vuelve al intervalo normal al recuperarse", () => {
  assert.equal(api.nextPollSeconds({pollSeconds: 60}, {error_count: 3}), 480);
  assert.equal(api.nextPollSeconds({pollSeconds: 60}, {error_count: 0}), 60);
  assert.equal(api.nextPollSeconds({pollSeconds: 300}, {error_count: 99}), 3600);
});

test("palabras incidentales no disparan petróleo", () => {
  assert.equal(api.isRelevant("Supermercados y archivo extraordinario"), false);
  assert.equal(api.isRelevant("Gasolina Súper baja en Ecuador"), true);
});

test("HTTP 200 sin ok de Telegram no confirma entrega", async () => {
  context.fetch = async () => ({ok: true, status: 200, json: async () => ({ok: false})});
  const result = await api.sendFastTelegramAlarm({TELEGRAM_BOT_TOKEN: "fake", TELEGRAM_CHAT_ID: "fake"}, {
    title: "Gasolina baja", source: {name: "Medio"}, url: "https://example.test", detectedAt: "2026-09-13T18:00:00Z"
  }, "domestic");
  assert.equal(result.ok, false);
});

test("alarma inmediata es texto limpio de máximo 300 caracteres", async () => {
  let body;
  context.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return {ok: true, status: 200, json: async () => ({ok: true})};
  };
  const result = await api.sendFastTelegramAlarm({TELEGRAM_BOT_TOKEN: "fake", TELEGRAM_CHAT_ID: "fake"}, {
    title: "El petróleo sube con fuerza ".repeat(30), source: {name: "CNBC Energy"},
    url: "https://example.test", detectedAt: "2026-09-13T18:00:00Z"
  }, "market");
  assert.equal(result.ok, true);
  assert.ok(body.text.length <= 300);
  assert.equal(body.parse_mode, undefined);
  assert.match(body.text, /Impacto en Ecuador aún no confirmado/);
});

test("X filtra política ajena al combustible y noticias financieras ajenas al petróleo", () => {
  assert.equal(api.xPostRelevant("DanielNoboaOk", "Inauguramos una escuela"), false);
  assert.equal(api.xPostRelevant("DanielNoboaOk", "La gasolina baja tres centavos"), true);
  assert.equal(api.xPostRelevant("EPPetroecuador", "La refinería reinicia operaciones"), true);
  assert.equal(api.xPostRelevant("business", "Oil falls after inventory report"), true);
  assert.equal(api.xPostRelevant("business", "Technology shares climb"), false);
});

test("X no consulta ni genera gasto sin activación explícita", async () => {
  context.fetch = () => { throw new Error("No debe consultar"); };
  await api.pollX({X_BEARER_TOKEN: "fake"}, "2026-09-13T18:00:00Z");
  await api.pollX({FUELINTEL_X_ENABLED: "true"}, "2026-09-13T18:00:00Z");
});
