# Fuel Intelligence Ecuador

Todas las horas visibles en Telegram y en los gráficos se convierten a
`America/Guayaquil` y se identifican como **hora de Ecuador (UTC−5)**. Las
marcas UTC se conservan únicamente en almacenamiento interno para ordenar y
deduplicar eventos correctamente.

Cuando aparece un PDF oficial cuyo periodo empieza al día siguiente, el bot lo
procesa de inmediato: envía primero el gráfico, después la noticia/análisis y
finalmente el PDF original como documento separado. Los documentos vencidos se rechazan y los periodos publicados con más
de 48 horas de anticipación quedan pendientes de revisión.

Monitor privado y verificable de precios y noticias de combustibles para Ecuador. Está diseñado para ejecutarse con pocos recursos mediante Cloudflare y GitHub Actions y enviar por Telegram únicamente datos actuales y confirmados.

## Estado de esta primera versión

Incluye:

- seguimiento configurable de páginas oficiales, índices y RSS;
- consultas HTTP condicionales con `ETag` y `Last-Modified`;
- frecuencia intensiva de 60 segundos alrededor de las fechas habituales de actualización;
- arranque seguro: la primera lectura crea la línea base sin enviar noticias antiguas;
- filtro temático para gasolina, diésel, petróleo, subsidios, refinerías y mercados;
- bloqueo de publicaciones antiguas y periodos vencidos;
- deduplicación por contenido, URL y similitud de titulares;
- confirmación por fuente oficial o coincidencia entre dos fuentes independientes;
- alerta temprana explícitamente no publicable cuando un medio ecuatoriano informa un cambio de precio antes del PDF oficial;
- alarma directa opcional desde Cloudflare a Telegram: llega antes de que arranque Python y siempre identifica si es medio, fuente oficial o evento internacional;
- extracción inicial de precios y periodos escritos en español;
- cálculos con `Decimal`, sin delegar matemáticas a un modelo de IA;
- cualquier proyección se envía en un mensaje independiente, se identifica como estimación propia y se omite cuando el modelo no tiene datos suficientes o no supera su validación retrospectiva;
- memoria local SQLite en modo WAL;
- entrega editorial separada: borrador para X de hasta 300 caracteres, contexto verificable y estimación estadística en mensajes distintos;
- lectura de canales públicos de Telegram por mensaje, con traducción editorial de árabe/inglés;
- avisos marítimos oficiales de UKMTO para riesgos de tanqueros y rutas estratégicas;
- detección robusta de saltos atípicos y cambios de régimen, explicada sin jerga cuantitativa;
- histórico analítico normalizado con Polars y DuckDB para conservar observaciones pequeñas;
- gráficos editoriales de alta resolución con HTML/CSS, Chart.js y Playwright, más workspace interactivo opcional con bqplot;
- capa de mercado directa, sin dependencia de OpenBB: futuros diferidos desde Yahoo Finance y precios minoristas semanales oficiales desde EIA cuando se configura `EIA_API_KEY`;
- vigilancia intradía de Yahoo Finance en intervalos de un minuto, con timestamp original, comprobación de antigüedad, dos confirmaciones y 30 minutos de enfriamiento por patrón;
- alarmas cuantitativas por salto de un minuto, movimiento de cinco minutos y cambio de régimen de volatilidad, sin atribuir una causa hasta encontrar noticias verificadas;
- selección de gráfico por tipo de dato y noticia: escalonado para precios oficiales, línea para cierres y diferencial Brent–WTI, área para volatilidad, barras para comparaciones, base 100 para evolución relativa y mapa de calor para rendimientos;
- colores estables por combustible, líneas escalonadas y anotaciones automáticas de decretos;
- histórico, máximos, mínimos y variaciones mensual/anual calculados en cada inserción;
- bloqueo de `UNKNOWN` y `SOURCE_CONFLICT` antes de publicar;
- gráfico nacional bajo demanda o cuando entra un precio nuevo confirmado;
- servicio `systemd` con reinicio automático y límites de CPU/RAM;
- réplica por lotes hacia Supabase con cola local y reintentos, preparada para revisión;

El esquema propuesto de Supabase usa tablas aisladas `fuelintel_*`. Debe aplicarse `sql/supabase_schema.sql` una vez antes de activar la réplica remota.

## Seguridad temporal

Un dato puede quedar en cuatro estados:

- `verified`: oficial o corroborado y dentro de su vigencia;
- `pending`: falta fecha, vigencia o una segunda fuente;
- `stale`: publicación o periodo vencido;
- `rejected`: irrelevante o incoherente.

Los precios sin periodo verificable no se envían. La base puede conservar datos históricos para gráficos, pero nunca los etiqueta como vigentes.

## Desarrollo local

Requiere Python 3.11 o posterior.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
$env:FUELINTEL_DRY_RUN = "true"
$env:FUELINTEL_TELEGRAM_ENABLED = "false"
.\.venv\Scripts\python -m fuelintel.main --once
```

En Linux/Oracle:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
FUELINTEL_DRY_RUN=true .venv/bin/python -m fuelintel.main --once
```

La primera ejecución no envía contenido: registra una línea base para evitar una avalancha de noticias anteriores.

Playwright necesita Chromium una sola vez por entorno:

```powershell
python -m playwright install chromium
```

`python -m fuelintel.main --dashboard` revisa el gráfico nacional de los últimos cuatro periodos y envía **un gráfico de mercado por día de Ecuador**. La vista se escoge según la noticia verificada o la anomalía medida, sin cambiar el tipo de gráfico solo por decoración. Si no hay un dato nuevo (por ejemplo, en fin de semana), el mensaje identifica la fecha del último dato y no lo presenta como cotización nueva. Una segunda ejecución el mismo día no duplica el resumen. `python -m fuelintel.main --historical-dashboard` genera el histórico nacional completo solo cuando el operador lo solicita.

`python -m fuelintel.main --interactive-market` exporta un HTML bqplot local para exploración. No publica ni envía nada. bqplot es el visor interactivo; no es proveedor de datos ni sustituye las validaciones. Una marca de hora indica cuándo se verificó la información, pero nunca convierte un cierre diferido o una publicación semanal en dato en tiempo real.

La vigilancia intradía se configura con `FUELINTEL_INTRADAY_ENABLED`,
`FUELINTEL_INTRADAY_INTERVAL_SECONDS` y
`FUELINTEL_INTRADAY_ALERT_COOLDOWN_MINUTES`. El intervalo mínimo permitido es
60 segundos. Yahoo Finance puede retrasar o revisar cotizaciones; por eso cada
alerta conserva la hora de observación y la hora de verificación. `yfinance` y
los datos de Yahoo son adecuados para investigación personal, no sustituyen un
feed bursátil con licencia para vender una terminal institucional.

El seguimiento AIS de posiciones de barcos no se activa sin una clave del proveedor y un proceso persistente. UKMTO sí funciona como vigilancia oficial de incidentes. Una anomalía estadística despierta una revisión, pero nunca se publica como causa o pronóstico por sí sola.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

## Activación de Telegram

Ejecuta `python -m fuelintel.setup_telegram`. El asistente valida el bot, detecta tu
chat privado después de que envíes `/start`, manda un mensaje de prueba y guarda el
token localmente en `data/secrets/telegram.env`. El archivo está excluido de Git y de
los paquetes de entrega. Si el bot ya usa un webhook, el asistente no lo elimina.

Después:

1. Ejecutar primero `python -m fuelintel.main --once` y comprobar el mensaje privado.
2. Revisar `python -m fuelintel.main --health`.
3. Copiar el archivo privado a Oracle como `/etc/fuelintel/fuelintel.env`.

Nunca se deben guardar tokens en el código, el ZIP o GitHub.

## Uso austero en la nube

Cloudflare detecta cambios y GitHub Actions hace el procesamiento pesado. Chromium solo se abre cuando hay que capturar un gráfico; las consultas usan `ETag`/`Last-Modified`, el contenido repetido se descarta y el gráfico programado solo se envía cuando su firma cambia.

La alarma directa requiere los secretos `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en Cloudflare. Solo se envía directamente para reportes nacionales con fecha reciente comprobable; las páginas sin fecha y las noticias extranjeras pasan por lectura y edición en Python. El intervalo de sondeo no garantiza entrega en un minuto: depende de la fuente y del arranque del procesamiento.

Para no bombardear Telegram, la primera fuente dispara la alarma, la segunda noticia independiente sirve como corroboración y las copias adicionales solo quedan registradas. Una coincidencia entre medios no convierte por sí sola el precio en oficial.

El conector opcional de X vigila `@DanielNoboaOk`, `@EPPetroecuador` y `@business`; requiere `FUELINTEL_X_ENABLED=true` y `X_BEARER_TOKEN` en Cloudflare. Consulta como máximo cada diez minutos. No está habilitado por defecto ni promete acceso gratuito a X. Los criterios y límites están en `config/x_accounts.json`.

El monitor independiente de GitHub verifica el Worker cada seis horas para reducir consumo; Cloudflare comprueba errores de sus fuentes en cada ciclo. Los límites pendientes para comercialización institucional están en `RELIABILITY.md`.

## Próximos pasos antes de producción

1. Auditar el ZIP del bot existente.
2. Validar manualmente cada selector y endpoint de las fuentes.
3. Incorporar copias reales de PDF como pruebas de regresión.
4. Mapear el Supabase existente sin alterar datos.
5. Añadir el feed internacional autorizado para cotizaciones intradía, si se contrata.
6. Ejecutar 24 horas en modo sombra sin notificaciones.
7. Activar Telegram y después desplegar como servicio en Oracle.
