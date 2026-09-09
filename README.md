# Fuel Intelligence Ecuador

Monitor privado y verificable de precios y noticias de combustibles para Ecuador. Está diseñado para ejecutarse con pocos recursos en una VM gratuita de Oracle Cloud y enviar por Telegram únicamente datos actuales y confirmados.

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
- extracción inicial de precios y periodos escritos en español;
- cálculos con `Decimal`, sin delegar matemáticas a un modelo de IA;
- memoria local SQLite en modo WAL;
- mensajes de Telegram con borrador limitado a 280 caracteres;
- gráfico nacional oscuro de estilo terminal financiera cada 10 horas;
- servicio `systemd` con reinicio automático y límites de CPU/RAM;
- réplica por lotes hacia Supabase con cola local y reintentos, preparada para revisión;

La réplica hacia Supabase permanece desactivada en modo seguro. El esquema propuesto usa tablas aisladas, pero no se debe ejecutar el SQL sin compararlo primero con las tablas del bot existente.

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

## Uso austero en Oracle

El servicio usa un solo proceso, duerme entre consultas, evita navegadores completos y descarga nuevamente solo cuando la fuente cambió. Pillow se carga únicamente al generar el gráfico programado. La plantilla `deploy/fuelintel.service` limita el proceso a 512 MB y 60% de un núcleo y lo reinicia si falla.

## Próximos pasos antes de producción

1. Auditar el ZIP del bot existente.
2. Validar manualmente cada selector y endpoint de las fuentes.
3. Incorporar copias reales de PDF como pruebas de regresión.
4. Mapear el Supabase existente sin alterar datos.
5. Añadir el feed internacional autorizado para cotizaciones intradía, si se contrata.
6. Ejecutar 24 horas en modo sombra sin notificaciones.
7. Activar Telegram y después desplegar como servicio en Oracle.
