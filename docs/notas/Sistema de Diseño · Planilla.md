# Sistema de Diseño · Planilla

> Lenguaje visual compartido por el portal web y la interfaz de escritorio. Nota reescrita el **2026-09-14**; antes se llamaba *Tinta sobre Papel* y describía una versión intermedia que conservaba la tarjeta flotante como unidad de composición.

## Por qué se rehízo

La versión anterior ya tenía la paleta cálida y las cifras tabulares, pero la estructura seguía siendo la del tablero genérico: tarjeta con `border-radius: 10px`, sombra suave, barra pegajosa con `backdrop-filter: blur(12px)`, píldoras de estado de 20 px de radio, cuatro métricas de igual peso y una grilla de cuadraditos de color para el mes. La primera fuente declarada era `Inter`.

Cada uno de esos patrones es defendible por separado. Juntos son la huella de una interfaz generada: nada en ellos sale del problema que el software resuelve.

## El objeto de referencia

Un sistema de asistencia paraguayo tiene un antecesor físico concreto: la **planilla**, la hoja rayada donde se anota entrada, salida y novedad, con una marca al margen y un sello al pie. Ese es el objeto que el lenguaje visual imita, y de ahí salen las cinco reglas.

**1. Reglas, no cajas.** La jerarquía la dan filetes de 1 px y espacio en blanco. No hay `border-radius` positivo ni una sola sombra proyectada en toda la hoja de estilos. El membrete cierra con una regla gruesa y abre el índice con una fina: la convención de una cabecera impresa.

**2. La cifra manda y es monoespaciada.** Toda hora, monto y número de día va en `--mono` con `tabular-nums`, alineado a la derecha. Las columnas de horas caen una debajo de la otra aunque cambien de valor.

**3. Tres tipografías con oficio.** Serif para títulos (`Iowan Old Style` → `Palatino` → `Georgia`), grotesca del sistema para el cuerpo, monoespaciada para cifras. Ninguna se descarga: la política de contenido no permite orígenes externos y una fuente remota sería un punto de falla en un kiosco sin internet.

**4. El acento *es* la tinta.** `PRIMARY` vale lo mismo que `TEXT`: el botón principal es un bloque macizo negro sobre papel, como el de un formulario impreso, y se invierte en el tema oscuro. El único color de reserva es el del sello (`#8C3A2B`), para lo destructivo y el contador de pendientes.

**5. El color sólo cuando significa.** No hay rellenos tintados. Un estado se comunica con la palabra en su color y un subrayado fino —la abreviatura que un encargado escribiría al margen— y, en el parte del día, con la regla superior.

## Paleta

| Token | Claro | Oscuro | Uso |
| --- | --- | --- | --- |
| `papel` / `BG` | `#F4F1E9` | `#15140F` | Fondo de la aplicación |
| `superficie` / `CARD` | `#FBF9F4` | `#1D1B15` | Secciones y modales |
| — / `INPUT_BG` | `#EDE8DC` | `#100F0B` | Pozo de los campos |
| `regla` / `CARD_BORDER` | `#DAD4C6` | `#333026` | Filete de separación |
| `regla-fuerte` / `INPUT_BORDER` | `#8F8874` | `#6F6A5A` | Borde de campo y puntillado |
| `tinta` / `TEXT` | `#17150F` | `#EDE9DC` | Texto y acento |
| `tinta-suave` / `MUTED` | `#6F6857` | `#8E8875` | Texto secundario |
| `sobre-acento` / `ON_PRIMARY` | `#F4F1E9` | `#15140F` | Texto sobre el bloque de tinta |

Estados, usados sólo como color de texto: en curso `#2F5D45` / `#6FAE8B`, tardanza `#8A5A16` / `#D9A441`, ausente y sello `#8C3A2B` / `#D4735C`, justificado `#3C5A72` / `#8FAEC9`.

## Qué verifica el CI

`tests/test_paleta.py` cubre tres cosas, porque las tres se rompen sin que falle nada:

- **Contraste WCAG** de trece pares reales en los dos temas. Todos superan AA; el peor es `4,53:1` (marcador de posición dentro de un campo, tema claro).
- **Paridad** de ocho tokens entre `gui.py` y `estilos.css`. Cambiar uno solo de los dos deja de compilar.
- **Deriva del lenguaje**: falla si reaparece un `border-radius` positivo, una sombra proyectada, un `backdrop-filter`, la tipografía de plantilla o si se pierden las cifras tabulares.

El borde de campo se oscureció de `#B4AC99` a `#8F8874` precisamente porque esa tercera comprobación lo delató: el valor viejo daba `2,15:1` contra la superficie y WCAG 1.4.11 exige `3:1` al límite de un control.

## Portal del empleado

El orden sigue respondiendo las tres preguntas que generan casi toda la consulta a RRHH; lo que cambió es la forma de cada respuesta.

**1. «¿Marqué hoy?»** — El **parte del día**: dos reglas horizontales, el estado en serif a la izquierda y el tiempo transcurrido en cifras grandes a la derecha. La regla superior es lo único que toma el color del estado.

**2. «¿Cómo vengo este mes?»** — El **registro**: una fila por día con día, novedad, entrada, salida, trabajado y tipo de jornada. Reemplaza a la grilla de cuadraditos coloreados, que respondía "cuántas tardanzas llevo" pero no "a qué hora salí el martes". La columna de novedades se escanea de arriba abajo igual que la de una planilla de papel.

Lo que todavía no pasó va en blanco, aunque el servidor ya sepa su carácter: un domingo futuro conserva su sello de *Descanso* pero no lleva las rayas de una fila sin datos. El estado no alcanza para decidirlo, porque `descanso` y `justificado` ganan sobre `futuro` en `_estado_del_dia`.

**3. «¿Cuántos días me quedan?»** — **Cuentas con línea de puntos**, el recurso del índice impreso y de la factura: concepto a la izquierda, artículo que lo respalda, puntillado y cifra a la derecha. Una línea por permiso —*"quedan 12 de 12 días"*— y la regla de consumo sólo si algo se consumió: un indicador en cero no informa nada y triplicaba el alto de la sección.

Las horas del mes cierran con una **regla doble** y el total. Las nocturnas quedan fuera de esa suma y lo dicen en el renglón: son un recargo sobre horas ya contadas, y sumarlas sería liquidar dos veces la misma hora.

## Kiosco

La pantalla que más identidad carga. Reloj monoespaciado a 3,75 rem con los segundos en un peso menor, fecha en versalitas espaciadas, campos con rótulo en mayúscula fina, bloque de tinta para registrar.

Al marcar, el **comprobante**: la hora grabada en grande, qué se registró, el nombre en serif y el sello SHA-256 entre reglas punteadas. Reemplaza al tilde verde dentro de un círculo — lo que la persona necesita confirmar es la hora que quedó, no que la operación salió bien.

El reloj en vivo se oculta mientras está el comprobante. Con los dos visibles quedaban dos horas grandes en pantalla y ninguna señal de cuál era la propia.

## Escritorio

`gui.py` ya tenía un sistema de tokens con etiquetas `_rol` sobre cada widget, así que cambiar toda la paleta fue editar dos diccionarios. Además:

- `RADIO = 2` reemplaza a los 27 `corner_radius` de 8, 10, 12 y 16.
- Los helpers `titulo()` (serif) y `cifra()` (monoespaciada) separan rótulo de número; catorce encabezados pasaron a serif.
- Las secciones del panel lateral se numeran `01`–`07` en vez de llevar los glifos `▦ ✦ ▤ ✎ ◉ ◈ 🔔`. Un número es un asidero que se puede decir por teléfono.
- Los botones de contorno resaltan con la superficie hundida y no con el acento: con la tinta como color principal, rellenarlos dejaba texto oscuro sobre fondo oscuro. Siete botones rellenos recibieron `text_color` explícito, que CustomTkinter no infiere.

## Consecuencia técnica

Sacar la interfaz del `f-string` a `src/static/` redujo `web_server.py` de **1.565 a 774 líneas** y permitió endurecer la política de contenido a `script-src 'self'`: sin script embebido ni manejadores `onclick`, un script inyectado ya no llega a ejecutarse. `tests/smoke_portal_js.py` vigila que no reaparezcan.

Las tablas viven dentro de un `.marco` con `overflow-x: auto`. Las cabeceras no parten palabras, así que en un teléfono el ancho mínimo de la tabla supera al de la hoja; el desborde se resuelve dentro del marco y el cuerpo conserva su margen lateral.

## Enlaces

[[Arquitectura Objetivo · Plataforma y Portal del Empleado]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Motor de Reglas de Horas Extra]] · [[Diseño de Interfaz Premium UI-UX]] · [[Manual de Diseño UI-UX Simplificado y Reportes PDF]]
