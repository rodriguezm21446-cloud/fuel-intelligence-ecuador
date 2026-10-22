# Red mundial de datos de petróleo, gas y movilidad

Investigación inicial — 19 de septiembre de 2026

> Estado: diseño e inventario. Estas fuentes todavía no están conectadas al bot.
> “Gratis para consultar” no significa necesariamente “libre para redistribuir”.

## Conclusión ejecutiva

Es posible construir una primera red mundial útil sin pagar por datos, pero no
una réplica gratuita de Bloomberg, ICE, Kpler o Vortexa. La versión gratuita
puede cubrir:

- cierres diarios o datos retrasados de referencias de mercado;
- producción, demanda, importaciones, exportaciones, refino e inventarios por
  país con frecuencia mensual;
- precios minoristas nacionales cuando el regulador los publica;
- almacenamiento y flujos físicos de gas europeos con frecuencia diaria;
- posiciones de futuros, volatilidad del petróleo y estadísticas de pasos
  marítimos;
- alertas informativas internacionales con actualización de 15 minutos.

No existe una fuente gratuita y jurídicamente reutilizable que entregue, al
mismo tiempo, todas las cotizaciones físicas mundiales en tiempo real y todos
los movimientos AIS de petroleros. Los precios físicos de Urals, ESPO, Arab
Light, Murban, Basrah, Brent Dated, Dubai, TTF, JKM y los flujos barco por barco
suelen estar sujetos a licencias de ICE, CME, S&P Global, Argus, OPIS, Kpler,
Vortexa o proveedores AIS.

La arquitectura correcta es de tres capas:

1. **Datos oficiales abiertos** para hechos y series históricas.
2. **Datos de mercado gratuitos o retrasados** como señal secundaria, nunca
   como única prueba oficial.
3. **Métricas propias reproducibles** calculadas sobre los datos anteriores:
   volatilidad realizada, anomalías, dependencia importadora, presión de
   inventarios y riesgo de rutas.

## Clasificación legal usada

- **Verde:** uso y visualización permitidos con atribución clara.
- **Amarillo:** acceso gratuito, pero se debe confirmar la licencia concreta
  antes de redistribuirlo en un producto comercial.
- **Rojo:** requiere licencia, suscripción o acuerdo para redistribución.

## Núcleo mundial gratuito

| Fuente | Cobertura | Frecuencia/retraso | Acceso | Uso propuesto | Estado |
|---|---|---|---|---|---|
| [EIA Open Data](https://www.eia.gov/opendata/) | EE. UU. y numerosas series internacionales de petróleo, derivados, gas, electricidad y comercio | diaria, semanal, mensual o anual según serie | API con clave gratuita; descargas masivas sin clave | WTI, Henry Hub, inventarios, refino, gasolina, diésel, producción y comercio | Verde; atribuir EIA |
| [JODI Oil](https://www.jodidata.org/oil/database/data-downloads.aspx) | países participantes de todo el mundo; producción, demanda, importación, exportación, refino y stocks | mensual; normalmente un mes de retraso | CSV gratuito desde 2002 | columna vertebral comparable por país | Amarillo; descarga gratuita, revisar licencia de redistribución del producto final |
| [JODI Gas](https://www.jodidata.org/gas/database/data-downloads.aspx) | países participantes; producción, consumo, importación, exportación y stocks de gas | mensual; hasta un mes de retraso | CSV gratuito desde 2009 | gráficos de gas por país | Amarillo |
| [Energy Institute Statistical Review](https://www.energyinst.org/statistical-review/resources-and-data-downloads) | más de 50 países y grandes regiones | anual | CSV/XLSX gratuito | historia larga, cuotas mundiales y comparación estructural | Amarillo; atribuir y confirmar reutilización comercial |
| [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets) | precios de referencia de energía y materias primas | mensual y anual | XLSX gratuito | contexto de largo plazo de crudo, gas y productos | Amarillo; verificar términos de cada serie |
| [IMF Primary Commodity Prices](https://www.imf.org/en/research/commodity-prices) | Brent, Dubai, WTI, Henry Hub, TTF, LNG del noreste asiático y otras materias primas | principalmente mensual; algunas tablas semanales | CSV/XLSX | comparación de referencias regionales | Amarillo; atribuir y revisar términos |
| [CFTC Commitments of Traders](https://publicreporting.cftc.gov/stories/s/r4w3-av2u) | futuros y opciones de petróleo, productos y gas de mercados estadounidenses | semanal | API y archivos, normalmente sin token | posicionamiento de productores, swaps y managed money | Verde |
| [Cboe OVX histórico](https://www.cboe.com/tradable_products/vix/vix_historical_data) | volatilidad implícita a 30 días del ETF USO | cierre diario histórico | descarga oficial | indicador de miedo/volatilidad del petróleo | Amarillo; visualización con atribución, no asumir derecho de reventa masiva |
| [IMF PortWatch](https://portwatch.imf.org/) | actividad de puertos y 24 pasos marítimos críticos | indicadores diarios; revisables | descarga, consulta y APIs | tránsito de tanqueros/carga, volumen y disrupciones en Hormuz, Suez, Panamá, etc. | Verde/Amarillo; citar PortWatch y UN Global Platform, respetar condiciones del conjunto |
| [GDELT 2.0](https://gdeltproject.org/data.html) | noticias y eventos mundiales en múltiples idiomas | cada 15 minutos | API y archivos abiertos | alerta temprana de ataques, sanciones, cierres, huelgas y refinerías | Verde para metadatos; no republicar artículos completos |

## Fuentes regionales y nacionales prioritarias

### Estados Unidos

- **EIA:** petróleo, productos, gas, refinerías, inventarios, importaciones,
  exportaciones y precios. Es la fuente oficial principal y su API permite crear
  servicios de búsqueda, visualización y análisis con atribución.
- **CFTC:** posicionamiento semanal por categoría de participante.
- **Cboe OVX:** volatilidad implícita diaria del petróleo.
- **CME/NYMEX:** las cotizaciones oficiales en tiempo real y su redistribución
  no son gratuitas. El API de producción cobra transmisión y licencia.

Gráficos: inventarios vs media estacional, utilización de refinerías, producción,
exportaciones, demanda implícita, posiciones COT, curva si hay licencia y OVX.

### Unión Europea

- [Eurostat API](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction):
  API REST gratuita para balances, producción, comercio y consumo energético.
- [Weekly Oil Bulletin](https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en):
  precios semanales de gasolina y diésel, con y sin impuestos, por país de la UE.
- [GIE AGSI/ALSI API](https://www.gie.eu/transparency-platform/GIE_API_documentation_v007.pdf):
  almacenamiento de gas y terminales LNG, datos diarios, clave gratuita. GIE
  permite reutilización con atribución obligatoria.
- [ENTSOG Transparency API](https://transparency.entsog.eu/api/archiveDirectories/8/api-manual/ENTSOG_TP_API_UserManual_v3.0.pdf):
  flujos físicos de gas por punto y operador. Es pública, pero exige consultas
  razonables, ventanas pequeñas, caché y backoff.
- **ICE TTF, Brent, gasoil y opciones:** tiempo real y redistribución son
  licenciados. No se deben extraer por scraping de una pantalla pública.

Gráficos: precio minorista por país, impuestos, almacenamiento vs rango de cinco
años, entradas de LNG, flujos por gasoducto, dependencia de importaciones y
volatilidad realizada.

### Reino Unido

- [Weekly road fuel prices](https://www.gov.uk/government/statistics/weekly-road-fuel-prices):
  gasolina y diésel semanales desde 2003, con CSV oficial.
- [Fuel Finder](https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email):
  precios de estaciones; API OAuth y CSV actualizado dos veces al día. Los
  cambios deben reportarse al sistema dentro de 30 minutos.
- [ONS weekly shipping indicators](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/datasets/weeklyshippingindicators):
  visitas semanales a puertos seleccionados.
- [ONS API](https://developer.ons.gov.uk/): abierta y sin clave.

Gráficos: gasolina/diésel minorista, diferencial mayorista-minorista, visitas a
puertos y exposición a Brent/North Sea.

### Noruega

- [Norwegian Offshore Directorate Open Data](https://www.sodir.no/en/about-us/open-data/):
  API REST, CSV, XML, WMS/WFS y datos de campos, pozos, instalaciones,
  gasoductos y producción bajo la licencia pública noruega NLOD.

Gráficos: producción por campo, petróleo vs gas, declino de campos, capacidad e
infraestructura del Mar del Norte.

### Rusia

- **JODI Oil/Gas:** mejor base gratuita comparable para producción, demanda,
  exportación y stocks mensuales.
- [MOEX ISS](https://www.moex.com/a8531): datos de mercado gratuitos con unos
  15 minutos de retraso; tiempo real requiere suscripción.
- Ministerio de Finanzas/Banco de Rusia publican referencias y análisis de
  Urals de forma periódica, pero no se ha encontrado un API oficial estable con
  el precio físico diario.
- Urals, ESPO y otros diferenciales físicos diarios fiables suelen depender de
  Argus, S&P Global o Bloomberg y no deben inventarse a partir de Brent.

Gráficos gratuitos: producción/exportación mensual, descuento Urals únicamente
cuando exista observación oficial verificable, rublo vs petróleo y series MOEX
retrasadas.

### Oriente Medio

- [OPEC Monthly Oil Market Report](https://publications.opec.org/momr): cesta
  OPEC, grados seleccionados, producción, comercio, stocks, refino y tanqueros.
- [OPEC Basket](https://www.opec.org/opec_web/en/data_graphs/40.htm): valor
  diario publicado por OPEC.
- [Oman Ministry daily oil price](https://mem.gov.om/en-us/KPI/Daily-Oil-Price):
  referencia diaria oficial de Omán.
- [Gulf Mercantile Exchange data](https://www.gulfmerc.com/gme-product-services/gme-data):
  muestra el marcador OQD; feeds retrasados/intradía y redistribución completa
  requieren acuerdo comercial.
- Arab Light, Murban y Basrah físicos diarios completos no son un conjunto
  abierto uniforme; se usarán publicaciones oficiales puntuales y OPEC hasta
  contratar una licencia.

Gráficos: cesta OPEC, producción/cuotas, exportaciones JODI, Omán, capacidad
ociosa, cumplimiento OPEC+ y exposición a Hormuz/Mar Rojo.

### China

- [Shanghai INE daily reports](https://www.ine.cn/eng/reports/statistical/daily/):
  apertura, máximo, mínimo, cierre, liquidación, volumen e interés abierto de
  futuros SC de crudo y LSFO. Es publicación diaria oficial; debe revisarse el
  permiso antes de redistribuir masivamente.
- [China Customs monthly statistics](https://english.customs.gov.cn/statics/report/monthly.html):
  importaciones de crudo, productos refinados y gas en cantidad y valor.
- National Bureau of Statistics: producción nacional y estadísticas anuales;
  el acceso automatizado no es tan estable como JODI.

Gráficos: SC crude/LSFO, importación mensual, coste unitario implícito de
importación, refino, producción y dependencia exterior.

### India

- [PPAC](https://ppac.gov.in/): informes oficiales descargables sobre producción,
  refino, ventas, importaciones/exportaciones, gas y LNG. No se ha confirmado
  un API formal; se tratará como descarga programada con control de cambios.

Gráficos: demanda por producto, utilización de refinería, dependencia de crudo,
LNG y precio minorista cuando exista serie oficial reutilizable.

### Japón

- [METI petroleum statistics](https://www.meti.go.jp/statistics/tyo/sekiyuka/index.html):
  informes mensuales oficiales en XLS/PDF sobre petróleo y derivados.
- Aduanas/estadísticas nacionales para importaciones de crudo y LNG.
- JODI como formato mundial normalizado.

Gráficos: importación, inventarios, ventas, refino y coste medio de LNG/crudo.

### Corea del Sur

- [Korea Open Data — precios nacionales](https://www.data.go.kr/data/15150932/openapi.do):
  API XML gratuita de KNOC/Opinet para gasolina, diésel y gasolina premium,
  con precio diario y variación. La ficha declara uso sin restricciones, aunque
  producción puede requerir aprobación.
- El portal público también ofrece archivos y APIs energéticos adicionales.

Gráficos: precios diarios por combustible, diferenciales regionales y tendencia
de productos refinados.

### Canadá

- [Statistics Canada Web Data Service](https://www.statcan.gc.ca/en/developers/wds/user-guide):
  API REST JSON, CSV/SDMX y actualizaciones cada día laborable; cubre crudo, gas,
  productos, oleoductos, comercio y reservas.

Gráficos: producción, oleoductos, exportaciones a EE. UU., inventarios y precios
minoristas si la tabla correspondiente lo permite.

### Brasil

- [ANP Dados Abertos](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos):
  CSV abiertos de producción, pozos, plataformas, importaciones/exportaciones,
  refino, ventas, terminales y precios de combustibles.
- [ANP price history](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/serie-historica-de-precos-de-combustiveis):
  precios semanales por estación y agregados geográficos.

Gráficos: gasolina/diésel/etanol por estado, producción offshore, refino,
exportaciones y coste de paridad de importación.

### Australia

- [Australian Petroleum Statistics](https://www.energy.gov.au/energy-data/australian-petroleum-statistics):
  datos mensuales oficiales nacionales y estatales de producción, ventas,
  importaciones, exportaciones, refino e inventarios.

Gráficos: seguridad de inventarios, dependencia importadora, producción por
estado y consumo de productos.

## Movilidad, petroleros y pasos estratégicos

| Fuente | Qué entrega | Coste y límite | Decisión |
|---|---|---|---|
| [IMF PortWatch](https://portwatch.imf.org/) | llegadas/salidas, volumen estimado y tránsitos de 24 pasos; series diarias | gratuito; datos experimentales y revisables | fuente principal de movilidad agregada |
| [Panama Canal Developer Portal](https://developer.pancanal.com/) | conteos diarios, llegadas, tiempos y carga | plan gratis de 50 consultas/mes; OAuth | consultar una vez al día y guardar localmente |
| [Suez Canal statistics](https://www.suezcanal.gov.eg/English/Navigation/Pages/NavigationStatistics.aspx) | reportes mensuales/anuales | gratuito, sin API pública confirmada | descarga controlada, no polling frecuente |
| [AISHub](https://www.aishub.net/join-us) | posiciones AIS mundiales | gratis solo si se aporta una estación AIS propia con requisitos de cobertura y uptime | no sirve como fuente gratuita inmediata |
| MarineTraffic, Spire, exactEarth, Kpler, Vortexa | posición, carga, destino y estimaciones comerciales | pago/licencia | fase empresarial, no MVP |

PortWatch es un proxy de comercio marítimo, no un manifiesto de carga perfecto.
Sus datos pueden revisarse y no permiten afirmar que un buque transporta un
producto concreto si la clasificación disponible no lo demuestra.

Para pasos como Hormuz, la serie pública permite graficar por día el total de
tránsitos y separar `tanker` de las categorías de carga. El conteo público suma
ambas direcciones: no permite publicar “salieron N petroleros del Golfo” ni
“entraron N tanqueros” sin una fuente AIS direccional adicional. PortWatch
publica estos días por lotes y los últimos valores pueden llegar con varios días
de retraso y ser revisados. El gráfico debe titularse “Tránsitos detectados por
AIS — ambos sentidos” y mostrar claramente `último día disponible`, nunca
“tráfico de hoy” cuando la fecha de referencia sea anterior.

## Precios y volatilidad

### Lo que se puede mostrar gratis

- cierres diarios de WTI, Brent, RBOB, heating oil y Henry Hub mediante EIA u
  otras fuentes con licencia compatible;
- OVX histórico de Cboe;
- SC crude y LSFO diarios desde INE;
- cesta OPEC y marcador oficial de Omán;
- MOEX retrasado;
- precios mensuales IMF/World Bank;
- precios nacionales de reguladores;
- volatilidad **realizada** calculada por nosotros a 5, 20 y 60 sesiones;
- rango verdadero medio, drawdown, z-score, correlación y beta móvil;
- dispersión entre referencias, siempre que todas las series tengan licencia.

### Lo que no se debe prometer gratis

- libro de órdenes o tick-by-tick mundial;
- superficies de volatilidad de opciones ICE/CME;
- curva completa de Brent, TTF, JKM, gasoil, Murban y crack spreads con derecho
  de redistribución comercial;
- precio físico diario uniforme de todos los grados nacionales;
- ubicación y carga en tiempo real de cada petrolero.

ICE exige aprobación incluso para mostrar públicamente datos retrasados, y CME
cobra la API de producción más las licencias aplicables. Los agregadores privados
solo sirven como respaldo de investigación, pero son no oficiales y sus datos no
deben constituir la base redistribuida de un producto comercial; las series
vienen del proveedor primario (U.S. EIA) o del histórico oficial en eia.gov/dnav.

## Catálogo de gráficos por país

Cada ficha nacional tendrá solo gráficos respaldados por datos disponibles:

1. Producción de crudo y condensados.
2. Producción de gas.
3. Importaciones y exportaciones de crudo, productos y gas.
4. Demanda aparente por producto.
5. Inventarios y días de cobertura cuando sea calculable.
6. Capacidad y utilización de refinerías.
7. Precio minorista de gasolina, diésel, premium y GLP.
8. Referencia de mercado relevante en moneda local y USD.
9. Volatilidad realizada y drawdown.
10. Dependencia importadora y balance neto.
11. Exposición a pasos marítimos y disrupciones.
12. Eventos verificados que expliquen rupturas de la serie.

No todos los países tendrán los doce. La interfaz debe mostrar cobertura,
frecuencia, último periodo observado y fecha de verificación para evitar que un
usuario confunda una cifra mensual con una cotización en vivo.

## Métricas propias reproducibles

Estas métricas no requieren que una IA invente números:

- retorno diario/semanal/mensual;
- volatilidad anualizada de retornos logarítmicos;
- z-score robusto con mediana y MAD;
- drawdown desde máximo móvil;
- sorpresa de inventarios frente a media estacional;
- dependencia importadora = importaciones netas / demanda aparente;
- utilización de refino = throughput / capacidad;
- días de cobertura = stocks / consumo diario medio;
- presión logística = percentil de tránsitos + tiempos de espera + volumen;
- riesgo regional = combinación transparente de volatilidad, inventarios,
  comercio, disrupciones y calidad/frescura de los datos.

Toda predicción debe guardar modelo, variables, fecha de entrenamiento,
intervalo de confianza y error retrospectivo. Si faltan observaciones, la salida
correcta es “datos insuficientes”, no completar huecos con texto generado.

## Modelo mínimo de procedencia

Cada observación debe guardar como mínimo:

- `series_id`, `provider`, `source_url` y `license_class`;
- país/región, producto, grado y tipo de mercado;
- valor, unidad, moneda y base de conversión;
- `reference_period`, `published_at`, `retrieved_at` y zona horaria;
- frecuencia y retraso esperado;
- revisión/vintage y hash del archivo original;
- estado: `raw`, `validated`, `revised`, `stale`, `conflict` o `rejected`;
- método de validación y fuente de contraste.

## Plan de incorporación sin coste

### Fase 1 — columna vertebral mundial

Conectar EIA, JODI Oil, JODI Gas, Eurostat, GIE, ENTSOG, CFTC, Cboe OVX,
PortWatch, OPEC, GDELT e INE. Esto permite lanzar comparaciones mundiales y
alertas sin simular tiempo real.

### Fase 2 — conectores nacionales

Añadir Reino Unido, Noruega, Brasil, Canadá, China Customs, India PPAC, Japón
METI, Corea Opinet, Australia y los países que demuestren demanda de usuarios.

### Fase 3 — datos licenciados

Solo cuando existan clientes de pago: ICE/CME oficial, benchmarks físicos,
curvas, opciones y AIS comercial. La licencia se compra para una función que ya
tiene ingresos; no para llenar la base sin usuarios.

## Frecuencia recomendada para no agotar servicios

- GDELT/noticias: cada 15 minutos, consulta incremental.
- precios de mercado gratuitos/retrasados: cada 15–60 minutos durante mercado,
  solo si la licencia lo permite; cierre definitivo una vez al día.
- EIA: según calendario de publicación, no cada minuto.
- GIE/ENTSOG/PortWatch: una a cuatro veces al día con caché.
- reguladores de precios: una vez al día o según calendario.
- JODI/IMF/World Bank/Energy Institute: revisar calendario; mensual/anual.
- fuentes PDF/XLS: HTTP condicional con `ETag` y `Last-Modified`.

## Criterio de publicación

Un gráfico público debe mostrar siempre:

- qué mide y qué no mide;
- país, producto, unidad, moneda y periodo;
- frecuencia y última observación real;
- fuente primaria y hora de verificación;
- si el valor es observado, calculado, estimado o revisable;
- nota metodológica cuando se normalicen unidades o monedas.

Nunca se etiquetará como “en tiempo real” una serie diaria, mensual o revisable.
