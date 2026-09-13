import test from "node:test";
import assert from "node:assert/strict";
import {
  canonicalUrl,
  extractRssCandidates,
  extractTelegramCandidates,
  isRelevant,
  isRelevantCandidate,
  isMarketSquawkSignal,
  titleOverlap
} from "../src/core.js";

test("despierta el squawk solo ante un evento energético material", () => {
  assert.equal(
    isMarketSquawkSignal("cnbc-energy-rss", "Brent jumps after pipeline outage", "Oil supply disrupted"),
    true
  );
  assert.equal(isMarketSquawkSignal("cnbc-energy-rss", "Oil industry hiring", "Jobs report"), false);
  assert.equal(isMarketSquawkSignal("eluniverso-economia-rss", "Brent jumps", "Pipeline outage"), false);
  assert.equal(
    isMarketSquawkSignal("telegram-aja-news", "ارتفاع أسعار النفط", "تعطل إمدادات الخام"),
    true
  );
});

test("detecta temas de combustible sin depender de acentos", () => {
  assert.equal(isRelevant("Nuevo precio del diésel en Ecuador"), true);
  assert.equal(isRelevant("Resultados del campeonato"), false);
});

test("detecta titulares internacionales de inventarios y refinación", () => {
  assert.equal(isRelevant("U.S. gasoline inventories fall after refinery outage"), true);
  assert.equal(isRelevant("New smartphone enters the market"), false);
});

test("acepta solo los PDF mensuales de precios del histórico de Petroecuador", () => {
  assert.equal(
    isRelevantCandidate(
      "petroecuador-historico",
      "Septiembre",
      "",
      "https://www.eppetroecuador.ec/wp-content/uploads/2026/09/ESTRUCTURA-DE-PRECIOS-SEPTIEMBRE-2026.pdf"
    ),
    true
  );
  assert.equal(
    isRelevantCandidate(
      "petroecuador-historico",
      "Mapa",
      "",
      "https://www.eppetroecuador.ec/wp-content/uploads/2026/09/mapa.pdf"
    ),
    false
  );
});

test("elimina rastreadores al normalizar URLs", () => {
  assert.equal(
    canonicalUrl("/economia/gasolina/?utm_source=x#nota", "https://ejemplo.test/"),
    "https://ejemplo.test/economia/gasolina"
  );
});

test("extrae elementos RSS", () => {
  const xml = `<rss><channel><item><title>Precio de gasolina cambia</title><link>https://example.test/a</link><description>Dato oficial</description></item></channel></rss>`;
  assert.deepEqual(extractRssCandidates(xml, "https://example.test"), [
    { title: "precio de gasolina cambia", url: "https://example.test/a", summary: "dato oficial" }
  ]);
});

test("conserva mayúsculas en rutas RSS porque pueden ser significativas", () => {
  const xml = `<rss><channel><item><title>Precio de gasolina</title><link>https://example.test/Archivo/ABC</link></item></channel></rss>`;
  assert.equal(extractRssCandidates(xml, "https://example.test")[0].url, "https://example.test/Archivo/ABC");
});

test("mide coincidencia de titulares independientes", () => {
  assert.ok(titleOverlap("Gobierno anuncia cambio en precio de gasolina", "Nuevo precio de gasolina anunciado por el Gobierno") >= 0.5);
  assert.equal(titleOverlap("Precio de gasolina", "Partido de fútbol"), 0);
});

test("extrae mensajes individuales de un canal público de Telegram", () => {
  const html = `<div class="tgme_widget_message_wrap"><div data-post="Canal/42"><div class="tgme_widget_message_text">Oil supply <b>falls</b></div></div></div>`;
  assert.deepEqual(extractTelegramCandidates(html), [
    { title: "oil supply falls", url: "https://t.me/Canal/42", summary: "oil supply falls" }
  ]);
});
