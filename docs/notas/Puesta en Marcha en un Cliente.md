# Puesta en Marcha en un Cliente

> Guía operativa para instalar el sistema en una empresa: qué configurar, en qué orden, qué cargar antes de la primera marcación y qué **no** está incluido. Escrita el **2026-09-14**, con el sistema en el estado que describe [[Auditoría Técnica · Hallazgos Críticos]].

## Antes de instalar: lo que hay que preguntarle al cliente

Cuatro datos condicionan todo lo demás y conviene tenerlos antes de tocar un servidor.

| Pregunta | Dónde se configura | Si se equivoca |
| --- | --- | --- |
| ¿Qué turnos tiene la empresa? | *Gestión → Turnos* | Las tardanzas se miden contra la hora equivocada |
| ¿Hay pasantes o solo funcionarios? | `tipo_vinculo` de cada legajo | Se aplica el reglamento que no corresponde (Res. 3028/2024 vs 1307/2010) |
| ¿Desde cuándo trabaja cada persona? | `fecha_ingreso` del legajo | Vacaciones y aguinaldo mal liquidados |
| ¿Va a usar reconocimiento facial? | `BIOMETRIA_OBLIGATORIA` y `BIOMETRIA_PRUEBA_VIDA` | Ver más abajo |

El `.env` conserva `JORNADA_INICIO` para sembrar el turno inicial de la instalación. Es un valor de arranque, no la configuración: a partir de ahí los horarios se administran desde la pantalla de turnos (ver [[Turnos y Rotación de Horarios]]).

**La fecha de ingreso es el dato que más se subestima.** De ella dependen los días de vacaciones (12 / 20 / 30 según la escala de la Ley 1626/00) y los meses de aguinaldo. Si se migra la plantilla sin cargarla, el sistema asume que todos ingresaron el día de la migración y liquida 12 días a quien le corresponden 30.

## Una instalación, uno o varios clientes

Una misma instalación puede alojar a varias empresas sin que ninguna vea los datos de otra ([[Multiempresa · Aislamiento entre Clientes]]). La migración crea la empresa inicial, que es la que usa un cliente único; las demás se alojan una por una:

```bash
python src/app.py alta-empresa
```

Cada empresa tiene su propio personal, sus turnos, sus permisos y su administrador. **Ninguna sesión puede ver dos a la vez**: cambiar de cliente exige volver a entrar.

## Orden de instalación

```bash
# 1. Dependencias
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. Secretos: los tres son obligatorios y sin valor por defecto
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # COMPROBANTE_CLAVE
python -c "import sys; sys.path.insert(0,'src'); import biometria; print(biometria.generar_clave())"

# 3. Esquema (paso explícito, previo a levantar cualquier proceso)
python src/migrate.py

# 4. Primer administrador
python src/app.py        # el menú ofrece crearlo si la tabla está vacía

# 5. Rol restringido del servicio (imprime DB_USER y DB_PASSWORD)
python src/migrate.py rol-app

# 6. Servidor, ya con ese rol en el .env
python src/web_server.py
```

El `migrate.py` avisa si `BIOMETRIA_CLAVE` falta y cifra las fotos que hayan quedado en claro de una instalación anterior. También dice si el aislamiento entre empresas está en vigor: con el rol administrador las políticas quedan instaladas pero inertes, porque PostgreSQL exceptúa siempre a los superusuarios.

> [!important] Dos roles, dos momentos
> El rol **administrador** solo migra. El rol **restringido** (`marcacion_app`) es el que atiende tráfico: no puede alterar tablas y no puede saltear el aislamiento por fila. Es la diferencia entre que el aislamiento entre clientes lo prometa la aplicación y que lo imponga la base. Ver [[Multiempresa · Aislamiento entre Clientes]].

## Los tres secretos

Ninguno tiene valor por defecto: el proceso no arranca sin ellos. Es deliberado —un secreto con respaldo hardcodeado deja el sistema firmando con una clave que cualquiera puede leer en el repositorio.

| Variable | Qué protege | Si se pierde |
| --- | --- | --- |
| `JWT_SECRET_KEY` | Firma de las sesiones | Se cierran todas las sesiones abiertas; nada más |
| `COMPROBANTE_CLAVE` | Firma HMAC de los tickets de marcación | Los comprobantes viejos dejan de verificarse |
| `BIOMETRIA_CLAVE` | Cifrado de las plantillas faciales | **Las fotos registradas quedan ilegibles y hay que volver a tomarlas** |

> [!warning] El backup de la base no alcanza
> `BIOMETRIA_CLAVE` vive fuera de la base a propósito: así un backup robado no trae con qué abrirlo. La contracara es que **restaurar la base sin la clave no recupera las fotos**. Guardar el `.env` en el mismo lugar que el backup anula la protección; guardarlo en ningún lado pierde el dato.

## Carga inicial de la plantilla

El orden importa porque cada paso depende del anterior:

1. **Roles**: ya vienen creados (`Administrador`, `Recursos Humanos`, `Empleado`).
2. **Turnos**, desde *Gestión → Turnos*: uno por cada horario real de la empresa. El esquema siembra una jornada administrativa de 8 horas que sirve de punto de partida y de red para quien todavía no tenga turno propio.
3. **Legajos**, desde *Gestión → Personal*, con **vínculo**, **fecha de ingreso** y **turno** reales.
4. **Fotos biométricas**, si se van a usar: desde la GUI de escritorio, botón *Foto* de cada legajo. Requiere cámara en el equipo.
5. **Feriados trasladados del año**: el calendario base ubica cada feriado en su fecha estatutaria, que es lo legalmente correcto a falta de decreto. Los traslados del año se cargan en `FERIADOS_TRASLADADOS` de `clock_engine.py`. Ver [[Motor de Reglas de Horas Extra]].

## La decisión sobre biometría

`BIOMETRIA_OBLIGATORIA` viene **apagada** y conviene dejarla así hasta terminar de cargar las fotos.

- **Apagada**: una marca que el motor no puede verificar pasa, pero queda registrada como *No verificada* y Recursos Humanos la ve contada en su bandeja. Es lo opuesto a darla por buena en silencio, que era el comportamiento anterior.
- **Encendida**: sin verificación no hay marca. Cierra también el kiosco web, porque el navegador no tiene cámara: solo marca el kiosco físico.

Encenderla con la plantilla a medio cargar deja gente sin poder marcar.

`BIOMETRIA_PRUEBA_VIDA` es aparte y también viene apagada. Encendida, el kiosco sortea un gesto —acercarse, girar— y lo pide antes de capturar, de modo que una foto sostenida frente a la cámara no pase. **Avisale a la plantilla antes de encenderla**: cambia lo que hay que hacer para marcar, y un kiosco que de golpe pide algo que nadie explicó genera una cola de gente confundida.

## Operación diaria

| Situación | Quién la resuelve | Dónde |
| --- | --- | --- |
| Alojar un cliente nuevo en la misma instalación | Quien instala | `python src/app.py alta-empresa` |
| Alguien cambia de horario por un tiempo | RRHH programa una rotación con vigencia | *Gestión → Turnos* |
| Cambia el horario de un turno entero | RRHH lo edita; el cambio rige desde la fecha que indique y el pasado conserva el suyo | *Gestión → Turnos* |
| La empresa rota turnos cada semana | RRHH define un ciclo y asigna posiciones; el resto se calcula | *Gestión → Turnos → Rotación automática* |
| Alguien olvidó marcar la salida | Se libera solo a las 18 h y avisa a RRHH | *Gestión → Pendientes* |
| Lluvia, paro de transporte, corte de rutas | RRHH declara la condición del día | *Gestión → Condiciones del día* |
| Pedido de permiso | El empleado desde el portal, RRHH aprueba | *Gestión → Pendientes* |
| Marca fallida | El empleado toca el día y pide corrección | *Gestión → Pendientes* |
| Planilla de horas extra | Se emite sola | Portal del empleado o *Personal → Extras* |
| Constancia para un banco | El empleado la baja del historial | Portal → *Historial → Constancia* |
| Alguien deja la empresa | RRHH lo da de baja (no lo elimina) | *Gestión → Personal* |

La diferencia entre **dar de baja** y **eliminar** es la que más hay que explicar: la baja conserva marcajes y comprobantes para el archivo laboral; eliminar los destruye y solo debería usarse para corregir un alta equivocada.

### Cuando el cliente llama diciendo que algo falló

Tres preguntas, en este orden, y cada una tiene un comando que la contesta.

**¿Está vivo?** `curl -fsS http://<servidor>:8000/salud`. Responde `{"estado":"ok"}` solo si además la base contesta: no alcanza con que el proceso esté levantado, porque una conexión abierta contra un PostgreSQL que dejó de responder se ve igual de sana desde afuera. Un `503` dice que el servicio está en pie pero la base no.

**¿Qué versión tiene esta instalación?** `python src/migrate.py estado`. Contesta con el sello que lleva la propia base, no con el que uno cree haberle instalado, y dice qué le falta aplicar. Es la primera pregunta de cualquier incidente en cuanto hay más de un cliente instalado en momentos distintos.

**¿Qué pasó exactamente?** El registro sale por la salida estándar (`docker compose logs web`, o el journal del servicio). Pedile al que reporta la hora exacta; si el error se vio en el navegador, la respuesta trae una cabecera `X-Peticion` con un código que aparece entre corchetes en todas las líneas de esa misma petición.

```
2026-09-15 07:42:19 WARNING database     [c1d4f8a0] el pool de 10 conexiones se agotó; la petición esperó 380 ms
2026-09-15 07:42:19 ERROR   web          [c1d4f8a0] POST /api/marcar -> 500 en 10041 ms
```

Esa primera línea es un aviso, no una falla: la marca entró. Pero dice que el pool quedó chico para el pico de esta empresa, y que conviene subir `DB_POOL_MAX` antes de que el pico siguiente lo convierta en marcas rechazadas.

## Qué NO está incluido

Decirlo por adelantado evita una venta mal hecha.

| Falta | Consecuencia | Detalle |
| --- | --- | --- |
| **Prueba de vida a prueba de video** | El gesto que pide el kiosco deja afuera una foto quieta, pero no a quien mueva el teléfono siguiendo la consigna. Lo cierra una cámara con infrarrojo o un modelo anti-suplantación | P1-2b |
| **Sedes en husos horarios distintos** | La sucursal es un campo del turno: alcanza para horarios por sede, no para sedes en husos distintos. Con todas las sucursales en Paraguay no se nota | — |

Ninguna de las tres bloquea una venta en Paraguay, pero conviene decirlas
antes y no después.

> [!warning] Esta tabla se quedó corta una vez
> Hasta el 2026-09-15 listaba solo la prueba de vida y afirmaba que nada de lo
> que faltaba bloqueaba una venta. Era falso: no había instalador —para poner
> el kiosco en una PC había que instalarle Python y clonar el repositorio—, no
> había procedimiento de respaldo, una marcación no registraba su origen, y el
> pico de la mañana rechazaba tres de cada cuatro marcas. Cuatro cosas que sí
> bloqueaban, en la tabla que existe justamente para no vender de más.
>
> La lección no es la lista sino el hábito: una tabla de *lo que falta* se
> corrige cuando se agrega algo, no cuando alguien la lee antes de una venta.

## Verificación post-instalación

La suite completa corre contra la base real y tarda unos minutos:

```bash
python tests/setup_ci.py
python tests/test_motor_horario.py
python tests/test_condicion_dia.py
python tests/test_turno_nocturno.py
python tests/test_turnos.py
python tests/test_multiempresa.py
python tests/test_rotacion.py
python tests/test_antiguedad_y_bajas.py
python tests/test_seguridad_datos.py
python tests/test_planilla_extras.py
python tests/smoke_permisos_autoservicio.py
```

Con el servidor levantado, además:

```bash
python src/migrate.py estado                 # tiene que terminar en "Al día"
curl -fsS http://127.0.0.1:8000/salud        # {"estado":"ok"}
WEB_BASE=http://127.0.0.1:8000 python tests/test_operacion.py
```

Si `test_seguridad_datos` falla en la primera comprobación, falta `BIOMETRIA_CLAVE`.

Si `migrate.py estado` dice que falta aplicar algo, el servidor no va a arrancar: es deliberado. Correr `python src/migrate.py` y volver a preguntar.

## Enlaces

[[Despliegue en la Nube e Infraestructura SaaS]] · [[Multiempresa · Aislamiento entre Clientes]] · [[Turnos y Rotación de Horarios]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Autoservicio de Permisos y Formularios]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Reglamento de Asistencia y Disciplina]] · [[Motor de Reglas de Horas Extra]]
