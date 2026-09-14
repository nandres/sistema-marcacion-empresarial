# AGENTS · Guía de la bóveda

> Nota de entrada. Reemplaza el texto de bienvenida por defecto de Obsidian, que quedó sin tocar desde la creación de la bóveda. El mapa técnico vive en [[Ecosistema Sistema de Marcación]].

## Qué es esta bóveda

Documentación del **Sistema de Marcación** (control de asistencia bajo normativa laboral paraguaya). El código vive en el repositorio Git, en `src/`; esta bóveda guarda el razonamiento detrás de ese código: por qué una regla se implementó así, qué artículo la respalda y qué se sabe que está mal.

La configuración local (`.obsidian/`) está excluida del control de versiones; las notas sí se publican.

## Por dónde empezar

| Si buscás… | Leé |
| --- | --- |
| El mapa de módulos y cómo se conectan | [[Ecosistema Sistema de Marcación]] |
| Qué se construyó y en qué orden | [[Bitácora de Implementación]] |
| **Qué está roto hoy** | [[Auditoría Técnica · Hallazgos Críticos]] |
| Hacia dónde debería ir el sistema | [[Arquitectura Objetivo · Plataforma y Portal del Empleado]] |
| Fraude en las marcas y carga en el pico | [[Antifraude y Resiliencia en Picos de Marcación]] |
| Cómo pide permisos un empleado y qué papeles salen solos | [[Autoservicio de Permisos y Formularios]] |
| A qué hora entra cada persona, rotación y jornada partida | [[Turnos y Rotación de Horarios]] |
| **Instalarlo en una empresa** | [[Puesta en Marcha en un Cliente]] |

## Notas por dominio

**Reglas de negocio y normativa**
[[Motor de Reglas de Horas Extra]] · [[Turnos y Rotación de Horarios]] · [[Reglamento de Asistencia y Disciplina]] · [[Catálogo de Permisos y Licencias]] · [[Módulo de Justificaciones y Aguinaldos]] · [[Autoservicio de Permisos y Formularios]]

**Acceso y seguridad**
[[Control de Roles y Permisos RBAC]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Módulo de Gestión de Usuarios]]

**Interfaces y reportes**
[[Sistema de Diseño · Planilla]] · [[Diseño de Interfaz Premium UI-UX]] · [[Manual de Diseño UI-UX Simplificado y Reportes PDF]] · [[Panel de Analítica Visual y UX Premium]] · [[Panel de Reportes y Auditoría]]

**Infraestructura**
[[Estructura Web y Conexión Biométrica]] · [[Despliegue en la Nube e Infraestructura SaaS]] · [[Puesta en Marcha en un Cliente]]

## Convenciones

- **Las notas describen lo que hay, no lo que se quiso hacer.** Cuando el código contradice a la nota, se corrige la nota y se deja el enlace al hallazgo que lo explica.
- **Toda regla legal cita su artículo.** Sin la fuente normativa, una tolerancia es un número mágico.
- Los hallazgos de auditoría se referencian por su código (`P0-3`, `P1-5`) desde cualquier nota, para poder rastrear una regla hasta el defecto que la afecta.
- Los ejemplos de cálculo se copian de la **salida real** del motor, no de lo que debería devolver.
