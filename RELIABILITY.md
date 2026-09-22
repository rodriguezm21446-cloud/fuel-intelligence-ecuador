# Auditoría de fiabilidad — combustibleEc

## Cambios verificables

- Los precios reportados por medios activan aviso sin depender de un PDF. El informe conserva fuente, fecha de vigencia y carácter reportado.
- Las decisiones de noticia antigua o rechazada son terminales: otra regla no las convierte en actuales.
- Una imagen fallida permite entregar la noticia. Las cifras con conflicto no generan el gráfico preliminar.
- Cloudflare conserva la fecha del RSS/Telegram, limita conexiones y tiempos de espera, reduce consultas a fuentes caídas y reintenta despachos fallidos hasta cuatro intentos.
- Un feed sin artículos se registra como fallo de extracción. El endpoint de salud muestra cobertura incompleta, configuración y revisión instalada.
- Avisos técnicos por fuentes fallidas, envíos agotados y fallo del análisis de GitHub. Un monitor independiente de GitHub comprueba el Worker.
- X: cuentas DanielNoboaOk, EPPetroecuador y business. Adaptador opcional con seis consultas por hora como máximo, sin activación por defecto. Requiere acceso autorizado a X; el número de consultas no equivale a un presupuesto monetario.

## Condiciones antes de vender a una institución

Esta revisión no certifica disponibilidad ni cobertura total. El arranque de GitHub y los retrasos de las fuentes impiden prometer una entrega en 60 segundos. La alarma directa solo usa publicaciones fechadas; el resto requiere leer el artículo.

Quedan pendientes para un servicio institucional: cola duradera con confirmación del procesamiento y de cada entrega, recuperación de runners cancelados, aislamiento por cliente, restauración demostrada de backups, permisos por usuario, derechos comerciales de los datos, métricas reales de latencia y cobertura y un acuerdo de servicio medido. La caché de Actions no sustituye una base de datos duradera.

La vigilancia X señala saturación cuando hay más de diez resultados; no afirma cobertura completa. Los reintentos actuales de despacho conservan artículos web, pero el payload completo de X requerirá una cola duradera antes de activación institucional. No se garantizan posiciones AIS ni un mapa de todos los barcos.

Antes del despliegue: comparar el commit probado con la revisión de `/health`, comprobar secretos sin mostrarlos y verificar entrega real de una prueba técnica. Una prueba local aprobada no significa que el bot instalado haya cambiado.
