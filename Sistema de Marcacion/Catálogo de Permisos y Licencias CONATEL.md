# Catálogo de Permisos y Licencias CONATEL

> Catálogo reglamentario digitalizado de permisos, licencias y justificaciones de la CONATEL. Implementado en `src/reglamento.py`, consumido por `database.py` (CHECK de tipos), `auth.py` (validación de cuotas), `reports.py` (disponibilidad en el resumen) y `gui.py` (panel de justificación).

## Reglamentos vigentes

| Reglamento | Alcance | Artículos codificados |
| --- | --- | --- |
| **Reglamento Interno CONATEL** (Res. Directorio N.º 1307/2010, homologada por la SFP) | Funcionarios y personal contratado | Art. 18, Art. 29 y Art. 34 |
| **Programa de Pasantía** (Res. Directorio N.º 3028/2024) | Pasantes | Art. 10, 14, 23 y 25 |

## Funcionarios (Res. 1307/2010)

| Artículo | Tipo | Cuota | Período |
| --- | --- | --- | --- |
| Art. 18 | Salidas por motivos personales | 6 h/mes (no acumulables) | Mensual |
| Art. 29 | Vacaciones anuales con goce de sueldo | 12/20/30 días según antigüedad (Ley 1626/00) | Anual |
| Art. 34 a.1 | Maternidad | Código del Trabajo | Evento |
| Art. 34 a.2 | Paternidad | 10 días corridos | Evento |
| Art. 34 a.3 | Adopción | 6 semanas (42 días) | Evento |
| Art. 34 a.4 | Lactancia | 2×30' o 1 h diaria | Evento |
| Art. 34 a.5 + Art. 39 | Salud propia | Máx. 90 días/año | Anual |
| Art. 34 a.6 | Salud de cónyuge/hijos/padres | Autorización de Presidencia | Evento |
| Art. 34 a.7 | Matrimonio | 10 días corridos | Evento |
| Art. 34 a.8 | Fallecimiento (cónyuge/hijos/padres/hermanos) | 10 días corridos | Evento |
| Art. 34 a.9 | Exámenes universitarios o técnicos | Día del examen | Evento |
| Art. 34 a.10 | Motivos particulares con goce | 5 días/año | Anual |
| Art. 34 a.11 | Motivos particulares descontando vacaciones causadas | 10 días/año | Anual |
| Art. 34 b.2 | Motivos particulares sin goce | 10 días/año | Anual |
| Art. 13 | Justificación de omisión de registro | Día hábil siguiente | Evento |

## Pasantes (Res. 3028/2024)

| Artículo | Tipo | Cuota | Período |
| --- | --- | --- | --- |
| Art. 10 | Justificación de omisión de registro | 3 veces/mes | Mensual |
| Art. 14 | Salidas por motivos personales | 4 h/mes, máx. 3 usos/mes | Mensual |
| Art. 23 | Licencia anual del pasante | 10 días hábiles/año (12 meses sin sanciones; no acumulable ni adelantable) | Anual |
| Art. 25 a.1 | Maternidad | Código del Trabajo + Ley 5508/2015 | Evento |
| Art. 25 a.2 | Paternidad | 14 días corridos | Evento |
| Art. 25 a.3 | Adopción | 6 semanas (42 días) | Evento |
| Art. 25 b | Lactancia | 90' (0-6 m) / 60' (7-24 m) | Evento |
| Art. 25 c.1 + Art. 31 | Salud propia | Máx. 90 días/ejercicio fiscal | Anual |
| Art. 25 c.2 | Salud de cónyuge/hijos/tutor/padres | Autorización de GCH | Evento |
| Art. 25 c.3 | Papanicolaou y mamografía (Ley 6211/2018) | 2 veces/año | Anual |
| Art. 25 c.4 | Próstata y colón (Ley 6280/2019) | 2 veces/año | Anual |
| Art. 25 d | Matrimonio | 10 días corridos | Evento |
| Art. 25 e.1 | Fallecimiento (cónyuge/hijos/tutor/padres/hermanos) | 10 días corridos | Evento |
| Art. 25 e.2 | Fallecimiento de abuelos (Ley 3384/2007) | 3 días corridos | Evento |
| Art. 25 f | Exámenes finales | Día del examen | Evento |
| Art. 25 g | Fuerza mayor | 5 días hábiles/año | Anual |
| Art. 25 h | Salidas por consultas médicas | 6 h/mes (no acumulable) | Mensual |
| Art. 25 i | Día libre por horas no remuneradas | 2 veces/mes (6 h → 1 día) | Mensual |

## Reglas de cómputo

- **Días** → suma de días calendario `(fin − inicio + 1)` dentro del período.
- **Horas** → suma de la columna `justificaciones.horas_usadas` del período.
- **Veces / eventos** → cantidad de justificaciones del tipo en el período.
- **Períodos**: anual = año calendario; mensual = mes calendario; evento = todo el historial.
- Las vacaciones del funcionario usan cuota dinámica devengada según antigüedad (12/20/30 días); el pasante usa la licencia fija de 10 días (Art. 23).

## Reglas de validación (`auth.crear_justificacion`)

1. El tipo debe pertenecer al catálogo **y** aplicar al vínculo del empleado (un pasante no puede usar artículos de funcionario y viceversa).
2. Las fechas deben cumplir `desde ≤ hasta ≤ hoy` (no se pueden registrar permisos futuros).
3. Si el artículo tiene cuota y está agotada → se rechaza con mensaje "Cuota agotada".
4. Los artículos en horas exigen la cantidad de horas y esta no puede superar el remanente del mes.

## Historial de marcas

- El empleado (web y GUI) puede consultar su historial **desde el 1 de enero de cualquier año hasta el día actual**; la fecha "hasta" se valida contra el día de hoy (`reports.resumen_historico`).
- El tablero (`reports.resumen_empleado`) expone ahora la lista `disponibilidad` (artículo, cuota, usados, restantes y disponibilidad) y la etiqueta del reglamento aplicable.

## Base de datos

- Nueva columna `justificaciones.horas_usadas NUMERIC(4,1) NOT NULL DEFAULT 0`.
- El CHECK `justificaciones_tipo_permiso_check` se migra a la lista completa del catálogo (más el tipo histórico `'Permiso'` para no romper filas existentes).
- Los tipos del catálogo son ASCII-safe (p. ej. `Salidas Personales`, `Salud Familiar`, `Duelo Abuelos`, `Prevencion Femenina`, `Horas No Remuneradas`).

## Documentación vinculada

- [[Manual de Diseño UI-UX Simplificado y Reportes PDF]]
- [[Reglamento de Asistencia y Disciplina CONATEL]]
- [[Módulo de Justificaciones y Aguinaldos]]
- [[Ecosistema Sistema de Marcación]]