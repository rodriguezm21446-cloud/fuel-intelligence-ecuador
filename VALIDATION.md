# Validación técnica inicial — 8 de septiembre de 2026

Se ejecutó el monitor en modo seguro: Telegram desactivado, Supabase sin escrituras y primera lectura usada únicamente como línea base.

## Resultado

| Fuente | Tipo | Resultado |
|---|---|---|
| ARCH — precios | Página oficial | Respondió y generó una huella de control |
| ARCH — biblioteca | Índice oficial | Respondió; se registraron enlaces sin enviar documentos anteriores |
| Presidencia — decretos | Plataforma oficial | Respondió; el HTML público no expuso enlaces en esta prueba |
| Banco Central | Estadísticas | Respondió y quedó registrado como línea base |
| El Universo — Economía | RSS | Respondió con 22 entradas; ninguna se envió durante el arranque |
| Primicias — Economía | Índice de noticias | Respondió con 86 enlaces; quedaron filtrados como línea base |
| OPEP | Página oficial | Respondió con 80 enlaces; ninguna publicación anterior se envió |

Las siete fuentes terminaron sin errores en la segunda prueba. La página de ARCH fue considerablemente más lenta que las demás. Para impedir que esto retrase las alertas RSS, cada fuente se ejecuta ahora en un trabajador independiente. Una fuente lenta o caída no bloquea al resto.

## Controles verificados

- 13 pruebas automatizadas aprobadas.
- Un periodo vencido no se envía ni se guarda como precio vigente.
- Un dato oficial vigente se envía una sola vez.
- Una alerta repetida se bloquea por huella y similitud.
- Los parámetros de rastreo se eliminan al normalizar enlaces.
- Los cálculos usan `Decimal`.
- El borrador para X no supera 280 caracteres.
- El gráfico generado es un PNG válido de 1600 × 900.
- La primera lectura de cada fuente no notifica contenido anterior.
- Supabase permanece desactivado en modo seguro y conserva una cola local reintentable.

## Pendiente antes de producción

- Auditar el ZIP del bot actual y su esquema real de Supabase.
- Añadir pruebas con los PDF oficiales concretos que use el bot.
- Confirmar el endpoint autorizado para datos intradía si se requieren cotizaciones en tiempo real.
- Ejecutar una etapa de sombra de 24 horas antes de activar Telegram.
