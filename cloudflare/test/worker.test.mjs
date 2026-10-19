import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// Exercise the actual self-contained deployed worker, not a copy in core.js.
const source = readFileSync(new URL("../src/worker.js", import.meta.url), "utf8");
const context = vm.createContext({ URL, Intl, AbortSignal, TextEncoder });
vm.runInContext(source.replace("export default {", "const worker = {") +
  ";globalThis.api = {freshness,nextPollSeconds,extractRssCandidates,extractLinkCandidates,isRelevant,sendFastTelegramAlarm,xPostRelevant,pollX,isDomesticPriceSignal,fastAlarmKind,SOURCES};", context);
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
  // A flaky non-critical feed recovers quickly: ceiling of 10 minutes.
  assert.equal(api.nextPollSeconds({pollSeconds: 300}, {error_count: 99}), 600);
  // A critical source may back off up to an hour.
  assert.equal(api.nextPollSeconds({pollSeconds: 60, critical: true}, {error_count: 99}), 3600);
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
    title: "El precio de la gasolina baja en Ecuador ".repeat(30), source: {name: "Primicias"},
    url: "https://example.test", detectedAt: "2026-09-13T18:00:00Z"
  }, "domestic");
  assert.equal(result.ok, true);
  assert.ok(body.text.length <= 300);
  assert.equal(body.parse_mode, undefined);
  assert.match(body.text, /aún no es precio oficial/);
});

test("solo lo doméstico u oficial despierta al operador en el carril rápido", () => {
  // Noticia internacional en árabe o inglés: nunca un aviso rápido crudo.
  assert.equal(api.fastAlarmKind({ id: "telegram-aja-news" }, { title: "ارتفاع أسعار النفط", summary: "تعطل إمدادات الخام" }), "");
  assert.equal(api.fastAlarmKind({ id: "cnbc-energy-rss" }, { title: "Brent jumps after pipeline outage", summary: "" }), "");
  assert.equal(api.fastAlarmKind({ id: "oilprice-main-rss" }, { title: "Oil surges on supply cut", summary: "" }), "");
  // Reporte doméstico de combustible: sí.
  assert.equal(api.fastAlarmKind({ id: "google-news-ecuador-precios" }, { title: "Sube el precio de la gasolina en Ecuador", summary: "" }), "domestic");
  // Fuente oficial de precios: sí.
  assert.equal(api.fastAlarmKind({ id: "arch-precios" }, { title: "Precios de derivados", summary: "" }), "official");
});

test("squawk local: combustible + actor o cambio despierta al operador", () => {
  const source = { id: "google-news-ecuador-presidente" };
  // El presidente habla de combustible: basta el actor, sin palabra de cambio.
  assert.equal(api.isDomesticPriceSignal(source, { title: "Noboa habla sobre el diésel", summary: "" }), true);
  // Combustible + cambio, sin actor.
  assert.equal(api.isDomesticPriceSignal(source, { title: "La gasolina baja en Ecuador", summary: "" }), true);
  // Solo actor, sin combustible: no dispara.
  assert.equal(api.isDomesticPriceSignal(source, { title: "Noboa inaugura una escuela", summary: "" }), false);
  // Solo cambio, sin combustible: no dispara.
  assert.equal(api.isDomesticPriceSignal(source, { title: "El precio del banano sube", summary: "" }), false);
  // Fuente no doméstica: nunca dispara como local.
  assert.equal(api.isDomesticPriceSignal({ id: "cnbc-energy-rss" }, { title: "Noboa y la gasolina", summary: "" }), false);
});

test("las fuentes del squawk local están registradas como críticas", () => {
  const ids = new Set(api.SOURCES.map((source) => source.id));
  for (const id of ["google-news-ecuador-presidente", "google-news-ecuador-precios", "google-news-ecuador-combustibles"]) {
    assert.ok(ids.has(id), `falta la fuente ${id}`);
  }
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
