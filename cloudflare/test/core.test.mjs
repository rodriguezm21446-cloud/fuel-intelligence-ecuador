import test from "node:test";
import assert from "node:assert/strict";
import {
  canonicalUrl,
  extractRssCandidates,
  isRelevant,
  titleOverlap
} from "../src/core.js";

test("detecta temas de combustible sin depender de acentos", () => {
  assert.equal(isRelevant("Nuevo precio del diésel en Ecuador"), true);
  assert.equal(isRelevant("Resultados del campeonato"), false);
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
