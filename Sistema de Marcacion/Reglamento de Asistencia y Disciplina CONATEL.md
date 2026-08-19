# Reglamento de Asistencia y Disciplina CONATEL

> Cumplimiento de la Resolución de Directorio N.º 3028/2024 de la CONATEL sobre asistencia de pasantes y funcionarios. Implementado en el motor de reglas del [[Ecosistema Sistema de Marcación|Sistema de Marcación]].

## Reglas vigentes por tipo de vínculo

| Regla | Pasante | Funcionario |
| --- | --- | --- |
| Tolerancia ordinaria en la entrada | 10 minutos | 15 minutos |
| Uso mensual de la tolerancia | Máximo 3 veces (la 4.ª llegada cuenta de inmediato) | Sin límite |
| Tolerancia climática (lluvia intensa) | 30 minutos automáticos | 30 minutos automáticos |
| Corte por retraso | Más de 30 minutos → Ausencia Injustificada | Sin corte de ausencia |
| Incidencia generada | Llegada Tardía / Ausencia Injustificada | Llegada Tardía |

## Criterios de implementación

- La tolerancia climática de 30 minutos se activa desde el kiosco con el interruptor **"Día de Lluvia Intensa"** y cubre también a los funcionarios (cláusula legal general); su corte de 30 minutos pasa directo a **Ausencia Injustificada**.
- El conteo mensual de tardanzas del pasante se calcula por mes calendario en `America/Asunción` sobre el estado `es_tardanza` de la tabla `marcajes`.
- Cuando la cuota ordinaria está agotada y no llueve, cualquier retraso mayor a cero cuenta como **Llegada Tardía** de inmediato.
- Cada marcaje guarda `tolerancia_aplicada` (si se consumió alguna gracia) y `condicion_climatica` ('Lluvia intensa') para trazabilidad ante fiscalización.

## Implementación técnica

- `src/database.py`: columnas `users.tipo_vinculo` (`Pasante`/`Funcionario`), `marcajes.tolerancia_aplicada` y `marcajes.condicion_climatica`; método `contar_tardanzas_mes()`.
- `src/clock_engine.py`: `evaluar_asistencia_conatel()` (tolerancias, cuota mensual, corte de ausencia) integrado en `ClockEngine.clock_in()` y `registrar_asistencia(es_dia_lluvioso=...)`.
- `src/auth.py`: `TIPOS_VINCULO`, alta/edición con vínculo validado y auditoría; `TIPOS_PERMISO` incluye **Permiso por Examen** para pasantes.
- `src/gui.py`: interruptor de lluvia en el kiosco, selector de vínculo en Personal y en el modal de edición.
- `src/reports.py` y `src/web_server.py`: el vínculo y la condición climática viajan en los resúmenes de consulta e historial.

## Enlaces

- [[Motor de Reglas de Horas Extra]] (cómputo de jornada Ley 213)
- [[Módulo de Gestión de Usuarios]] (alta con vínculo)
- [[Panel de Reportes y Auditoría]] (trazabilidad de incidencias)
- [[Diseño de Interfaz Premium UI-UX]] (kiosco con interruptor climático)