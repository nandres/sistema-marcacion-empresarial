/* Sistema de Marcación · portal del empleado, kiosco y panel de gestión.
   Sin manejadores en línea: todo se engancha por addEventListener, de modo
   que la política de contenido pueda prohibir el script embebido. */

(function () {
  "use strict";

  var sesion = {
    token: null,
    rol: null,
    nombre: null,
    resumen: null,
    socket: null,
    personal: [],
    roles: []
  };

  // ---------------------------------------------------------------- utilidades

  function $(id) { return document.getElementById(id); }

  function esc(valor) {
    return String(valor == null ? "" : valor)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function iso(fecha) {
    return fecha.getFullYear() + "-" +
      String(fecha.getMonth() + 1).padStart(2, "0") + "-" +
      String(fecha.getDate()).padStart(2, "0");
  }

  function guaranies(monto) {
    return "Gs. " + Math.round(monto || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function duracion(minutos) {
    var h = Math.floor(minutos / 60), m = minutos % 60;
    return h + ":" + String(m).padStart(2, "0");
  }

  /* Siempre dos decimales: en una columna de horas el 8 y el 7,72 tienen que
     terminar en la misma posición. */
  function horas(valor) {
    return (Number(valor) || 0).toFixed(2).replace(".", ",");
  }

  /* El catálogo guarda la unidad como clave ("dias"); la pantalla la escribe
     como se lee. */
  var UNIDADES = { dias: "días", horas: "horas", veces: "veces" };

  function unidad(clave) {
    return UNIDADES[clave] || clave || "";
  }

  /* Los saldos son enteros salvo las medias horas: mostrar "12,00 días"
     sobra y "2,50 horas" no. */
  function cantidad(valor) {
    var n = Number(valor) || 0;
    return n % 1 === 0 ? String(n) : horas(n);
  }

  var DIA_CORTO = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

  function diaSemana(fechaIso) {
    return DIA_CORTO[new Date(fechaIso + "T12:00:00").getDay()];
  }

  function raya() { return '<span class="raya">—</span>'; }

  function avisar(contenedor, mensaje, clase) {
    var caja = typeof contenedor === "string" ? $(contenedor) : contenedor;
    if (!caja) return;
    caja.innerHTML = mensaje
      ? '<div class="aviso ' + (clase || "") + '">' + esc(mensaje) + "</div>"
      : "";
  }

  function almacenar(clave, valor) {
    try { valor === null ? localStorage.removeItem(clave) : localStorage.setItem(clave, valor); }
    catch (e) { /* navegación privada o almacenamiento bloqueado */ }
  }

  function recuperar(clave) {
    try { return localStorage.getItem(clave); } catch (e) { return null; }
  }

  // ------------------------------------------------------------------- red

  function pedir(ruta, opciones) {
    opciones = opciones || {};
    var config = { method: opciones.metodo || "GET", headers: {} };
    if (sesion.token) config.headers.Authorization = "Bearer " + sesion.token;
    if (opciones.cuerpo !== undefined) {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(opciones.cuerpo);
      config.method = opciones.metodo || "POST";
    }
    return fetch(ruta, config).then(function (respuesta) {
      if (respuesta.status === 401 && sesion.token) {
        cerrarSesion("Tu sesión expiró. Ingresá de nuevo.");
        return Promise.reject(new Error("sesion"));
      }
      return respuesta.json().catch(function () { return {}; }).then(function (datos) {
        if (!respuesta.ok) {
          var error = new Error(datos.detail || "No se pudo completar la operación.");
          error.datos = datos;
          throw error;
        }
        return datos;
      });
    });
  }

  /* Los PDF se descargan con el token en la cabecera y no en la URL: así no
     quedan credenciales en los registros del servidor ni en el historial. */
  function descargarPdf(ruta, nombre) {
    return fetch(ruta, { headers: { Authorization: "Bearer " + sesion.token } })
      .then(function (r) {
        if (!r.ok) throw new Error("No se pudo generar el documento.");
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var enlace = document.createElement("a");
        enlace.href = url;
        enlace.download = nombre;
        document.body.appendChild(enlace);
        enlace.click();
        enlace.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      });
  }

  // ------------------------------------------------------------------ tema

  function aplicarTema(tema) {
    document.documentElement.setAttribute("data-tema", tema);
    almacenar("marcacion_tema", tema);
  }

  function temaInicial() {
    var guardado = recuperar("marcacion_tema");
    if (guardado) return guardado;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "oscuro" : "claro";
  }

  // ---------------------------------------------------------------- vistas

  var VISTAS = ["kiosco", "acceso", "portal", "historial", "gestion"];

  function mostrar(vista) {
    var enApp = vista === "portal" || vista === "historial" || vista === "gestion";
    $("app").classList.toggle("oculto", !enApp);
    $("vista-kiosco").classList.toggle("oculto", vista !== "kiosco");
    $("vista-acceso").classList.toggle("oculto", vista !== "acceso");
    VISTAS.forEach(function (v) {
      var nodo = $("vista-" + v);
      if (nodo && v !== "kiosco" && v !== "acceso") nodo.classList.toggle("oculto", v !== vista);
    });
    document.querySelectorAll(".indice-item").forEach(function (boton) {
      if (boton.dataset.vista === vista) boton.setAttribute("aria-current", "page");
      else boton.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
    if (vista === "portal") cargarPortal();
    if (vista === "gestion") abrirPestana("pendientes");
    // La condición puede haberse declarado después de cargar la página: se
    // relee al entrar al kiosco y no solo al arrancar.
    if (vista === "kiosco") cargarCondicionDia();
  }

  // --------------------------------------------------------------- kiosco

  function pintarReloj() {
    var ahora = new Date();
    var reloj = $("reloj-kiosco");
    if (!reloj) return;
    reloj.innerHTML = String(ahora.getHours()).padStart(2, "0") + ":" +
      String(ahora.getMinutes()).padStart(2, "0") +
      '<span class="segundos">:' + String(ahora.getSeconds()).padStart(2, "0") + "</span>";
    $("fecha-kiosco").textContent = ahora.toLocaleDateString("es-PY", {
      weekday: "long", day: "numeric", month: "long", year: "numeric"
    });
  }

  /* La condición excepcional del día es un dato de la empresa, no del que
     marca: el kiosco la muestra pero no la puede declarar. */
  function cargarCondicionDia() {
    return fetch("/api/condicion-hoy")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $("k-condicion").innerHTML = d.condicion
          ? '<div class="aviso bien">' + esc(d.condicion) +
            " · tolerancia declarada de " + esc(d.tolerancia_min) + " minutos</div>"
          : "";
      })
      .catch(function () { /* el kiosco sigue operando sin el cartel */ });
  }

  function marcarEnKiosco() {
    var cedula = $("k-cedula").value.trim();
    var clave = $("k-clave").value;
    avisar("k-aviso", "");
    if (!cedula || !clave) {
      avisar("k-aviso", "Ingresá tu cédula y tu contraseña.");
      return;
    }
    var boton = $("k-marcar");
    boton.disabled = true;
    boton.textContent = "Registrando…";
    pedir("/api/marcar", {
      cuerpo: { cedula: cedula, password: clave }
    }).then(function (datos) {
      /* El reloj en vivo se va mientras está el comprobante: si no, quedan
         dos horas grandes en pantalla y la persona no sabe cuál es la suya. */
      $("kiosco-reloj").classList.add("oculto");
      $("kiosco-formulario").classList.add("oculto");
      var caja = $("kiosco-resultado");
      caja.classList.remove("oculto");
      /* El comprobante imita al que emite un reloj de fábrica: la hora en
         grande, qué se registró y el sello criptográfico al pie. */
      caja.innerHTML =
        '<div class="comprobante">' +
          '<div class="comprobante-hora">' + esc(datos.hora) + "</div>" +
          '<div class="comprobante-que">' +
            esc(datos.tipo === "ENTRADA" ? "Entrada registrada" : "Salida registrada") +
          "</div>" +
          '<div class="comprobante-quien">' + esc(datos.nombre) + "</div>" +
          '<div class="ticket">' + esc(datos.ticket) + "</div>" +
        "</div>" +
        '<button class="btn-suave btn-ancho" id="k-otro" style="margin-top:18px">Registrar otra persona</button>';
      $("k-otro").addEventListener("click", reiniciarKiosco);
      setTimeout(reiniciarKiosco, 15000);
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("k-aviso", error.message);
    }).finally(function () {
      boton.disabled = false;
      boton.textContent = "Registrar asistencia";
    });
  }

  function reiniciarKiosco() {
    $("k-cedula").value = "";
    $("k-clave").value = "";
    avisar("k-aviso", "");
    $("kiosco-resultado").classList.add("oculto");
    $("kiosco-resultado").innerHTML = "";
    $("kiosco-reloj").classList.remove("oculto");
    $("kiosco-formulario").classList.remove("oculto");
    $("k-cedula").focus();
  }

  // --------------------------------------------------------------- acceso

  function iniciarSesion(evento) {
    evento.preventDefault();
    avisar("a-aviso", "");
    var cuerpo = { cedula: $("a-cedula").value.trim(), password: $("a-clave").value };
    if (!cuerpo.cedula || !cuerpo.password) {
      avisar("a-aviso", "Completá tu cédula y contraseña.");
      return;
    }
    pedir("/api/login", { cuerpo: cuerpo }).then(function (datos) {
      sesion.token = datos.token;
      sesion.rol = datos.rol;
      sesion.nombre = datos.nombre;
      almacenar("marcacion_jwt", datos.token);
      almacenar("marcacion_rol", datos.rol);
      almacenar("marcacion_nombre", datos.nombre);
      $("a-clave").value = "";
      entrarALaApp();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("a-aviso", error.message);
    });
  }

  function entrarALaApp() {
    $("barra-nombre").textContent = sesion.nombre || "";
    $("barra-rol").textContent = sesion.rol || "";
    var gestiona = sesion.rol === "Administrador" || sesion.rol === "Recursos Humanos";
    $("nav-gestion").classList.toggle("oculto", !gestiona);
    conectarAlertas();
    mostrar("portal");
  }

  function cerrarSesion(mensaje) {
    sesion.token = sesion.rol = sesion.nombre = sesion.resumen = null;
    almacenar("marcacion_jwt", null);
    almacenar("marcacion_rol", null);
    almacenar("marcacion_nombre", null);
    if (sesion.socket) { sesion.socket.onclose = null; sesion.socket.close(); sesion.socket = null; }
    mostrar("acceso");
    if (mensaje) avisar("a-aviso", mensaje);
  }

  // ------------------------------------------------------- portal · parte

  var TITULARES = {
    en_curso: "Jornada en curso",
    cerrada: "Jornada completa",
    sin_marcar: "Todavía no marcaste",
    descanso: "Día de descanso",
    sin_cierre: "Tu jornada quedó sin cerrar"
  };

  function pintarParte(hoy) {
    var detalle;
    if (hoy.estado === "en_curso") {
      detalle = "Entrada registrada a las " + esc(hoy.entrada) + " · sin salida";
    } else if (hoy.estado === "cerrada") {
      detalle = "Entrada " + esc(hoy.entrada) + " · salida " + esc(hoy.salida);
    } else if (hoy.estado === "sin_cierre") {
      detalle = "Entraste a las " + esc(hoy.entrada) +
        " y no quedó registrada tu salida. Pedí la corrección desde el día.";
    } else if (hoy.estado === "descanso") {
      detalle = "No corresponde marcar. Si trabajás, se liquida con recargo del 100 %.";
    } else {
      detalle = "Cuando marques, tu entrada va a aparecer acá.";
    }
    if (hoy.incidencia) {
      detalle += ' · <span class="sello t-tardanza">' + esc(hoy.incidencia) + "</span>";
    }
    var conteo = "";
    if (hoy.estado === "en_curso" || hoy.estado === "cerrada") {
      conteo =
        '<div class="parte-conteo">' +
          '<div class="reloj">' + esc(duracion(hoy.minutos)) + "</div>" +
          '<div class="reloj-leyenda">' +
            (hoy.estado === "en_curso" ? "horas transcurridas" : "horas trabajadas") +
          "</div>" +
        "</div>";
    }
    $("parte-hoy").innerHTML =
      '<div class="parte es-' + esc(hoy.estado) + '">' +
        "<div>" +
          '<div class="parte-estado">' +
            (hoy.estado === "en_curso" ? '<span class="pulso late"></span>' : "") +
            esc(TITULARES[hoy.estado] || "Hoy") +
          "</div>" +
          '<div class="parte-detalle">' + detalle + "</div>" +
        "</div>" + conteo +
      "</div>";
  }

  /* El empleado tiene que poder responder "a qué hora entro" sin preguntar.
     La línea dice el turno, su horario y de dónde salió: una rotación no
     puede aplicarse sin que quien la cumple se entere. */
  function pintarTurno(turno) {
    if (!turno) { $("parte-turno").innerHTML = ""; return; }
    var partes = [
      '<span class="turno-nombre">' + esc(turno.nombre) + "</span>",
      '<span class="turno-horario">' + esc(turno.horario) + "</span>",
      "<span>" + esc(turno.dias_texto) + "</span>"
    ];
    if (turno.origen === "asignacion") {
      partes.push('<span class="turno-nota">Rotación vigente</span>');
    }
    if (!turno.trabaja_hoy) {
      partes.push('<span class="turno-nota">Hoy no tenés turno</span>');
    }
    if (turno.partida) {
      partes.push('<span class="turno-nota">Jornada partida</span>');
    }
    $("parte-turno").innerHTML = partes.join("");
  }

  // ---------------------------------------------------- portal · registro

  var NOVEDAD = {
    tardanza: "Tardanza", ausente: "Sin marcar", justificado: "Justificado",
    descanso: "Descanso", en_curso: "En curso", sin_cierre: "Sin cierre",
    franco: "Franco", normal: "", futuro: ""
  };

  /* El mes se lee como una planilla: una fila por día, la novedad al margen
     y las cifras en columna. Un vistazo a la columna de novedades responde
     "cuántas tardanzas llevo" sin contar cuadraditos de colores. */
  function pintarRegistro(dias, mesNombre) {
    $("titulo-mes").textContent = mesNombre
      ? mesNombre.charAt(0).toUpperCase() + mesNombre.slice(1)
      : "Este mes";

    var conMarca = dias.filter(function (d) { return d.entrada; }).length;
    $("mes-cerrado").textContent = conMarca + (conMarca === 1 ? " día con marca" : " días con marca");

    /* Lo que todavía no pasó se deja en blanco aunque el servidor ya sepa
       su carácter: un domingo futuro conserva su sello de descanso, pero no
       lleva las rayas de una fila sin datos. El estado no alcanza para
       decidirlo porque `descanso` y `justificado` ganan sobre `futuro`. */
    var indiceHoy = dias.findIndex(function (d) { return d.hoy; });

    $("registro-mes").innerHTML = dias.map(function (d, posicion) {
      var futuro = indiceHoy >= 0 ? posicion > indiceHoy : d.estado === "futuro";
      var numero = String(d.dia).padStart(2, "0");
      var celdaDia = '<span class="marca-dia">' + numero + "</span>" +
        '<span class="dia-semana">' + esc(diaSemana(d.fecha)) + "</span>";
      var novedad = d.incidencia || NOVEDAD[d.estado] || "";

      return '<tr class="dia' + (futuro ? " futuro" : "") + (d.hoy ? " es-hoy" : "") + '"' +
          (futuro ? "" : ' data-fecha="' + esc(d.fecha) + '"') + ">" +
        "<td>" + (futuro ? celdaDia
          : '<button class="dia-boton" type="button" data-fecha="' + esc(d.fecha) + '">' +
            celdaDia + "</button>") + "</td>" +
        "<td>" + (novedad ? '<span class="sello t-' + esc(d.estado) + '">' + esc(novedad) + "</span>"
                          : (futuro ? "" : raya())) + "</td>" +
        '<td class="num">' + (d.entrada ? esc(d.entrada) : (futuro ? "" : raya())) + "</td>" +
        '<td class="num">' + (d.salida ? esc(d.salida) : (futuro ? "" : raya())) + "</td>" +
        '<td class="num">' + (d.horas ? esc(horas(d.horas)) : (futuro ? "" : raya())) + "</td>" +
        '<td class="num prescindible">' + (d.jornada ? esc(d.jornada) : (futuro ? "" : raya())) + "</td>" +
      "</tr>";
    }).join("");
  }

  // ----------------------------------------------------- portal · cuentas

  function cuenta(concepto, fuente, cifra, unidad, clase) {
    return '<div class="cuenta' + (clase ? " " + clase : "") + '">' +
      '<span class="cuenta-concepto">' + esc(concepto) +
        (fuente ? '<span class="cuenta-fuente">' + esc(fuente) + "</span>" : "") + "</span>" +
      '<span class="cuenta-relleno"></span>' +
      '<span class="cuenta-cifra">' + esc(cifra) +
        (unidad ? "<small>" + esc(unidad) + "</small>" : "") + "</span>" +
    "</div>";
  }

  /* Las nocturnas no se suman: son un recargo sobre horas que ya están
     contadas en las ordinarias o en las extra. Sumarlas al total sería
     liquidar dos veces la misma hora. */
  function pintarCuentasMes(extras) {
    var total = (Number(extras.horas_ordinarias) || 0) +
      (Number(extras.horas_50) || 0) + (Number(extras.horas_100) || 0);
    $("cuentas-mes").innerHTML =
      cuenta("Ordinarias", "Art. 194", horas(extras.horas_ordinarias), "h") +
      cuenta("Nocturnas", "Art. 232 · recargo 30 %, ya contadas arriba",
             horas(extras.horas_nocturnas), "h") +
      cuenta("Extra diurnas", "Art. 234 · recargo 50 %", horas(extras.horas_50), "h") +
      cuenta("Extra nocturnas, domingo o feriado", "Art. 233 · recargo 100 %",
             horas(extras.horas_100), "h") +
      cuenta("Total trabajado", "", horas(total), "h", "suma");
  }

  // ------------------------------------------------------- portal · saldos

  function pintarSaldos(datos) {
    /* Las vacaciones encabezan siempre: su cuota es dinámica según antigüedad
       y no sale del catálogo. El resto se ordena por cercanía al límite, que
       es el orden en que le importan al empleado. */
    var esPasante = datos.vinculo === "Pasante";
    var tipoVacaciones = esPasante ? "Licencia de Pasante" : "Vacaciones";
    var vac = datos.vacaciones;
    var filas = [saldoHtml({
      nombre: esPasante ? "Licencia de pasante" : "Vacaciones",
      articulo: esPasante ? "Art. 23" : "Art. 29",
      usados: vac.usadas, cuota: vac.devengadas, unidad: "días"
    })];

    (datos.disponibilidad || [])
      .filter(function (d) { return d.cuota != null && d.tipo !== tipoVacaciones; })
      .sort(function (a, b) {
        return (b.usados / b.cuota) - (a.usados / a.cuota);
      })
      .forEach(function (d) {
        filas.push(saldoHtml({
          nombre: d.nombre, articulo: d.articulo,
          usados: d.usados, cuota: d.cuota, unidad: d.unidad,
          usos: d.usos, usosMax: d.usos_max
        }));
      });

    $("saldos").innerHTML = filas.join("");
  }

  /* Una sola línea por permiso: "quedan 12 de 12 días". La regla de consumo
     se dibuja sólo si algo se consumió —un indicador en cero no informa
     nada y multiplica por tres el alto de la sección. */
  function saldoHtml(s) {
    var restante = Math.max(0, s.cuota - s.usados);
    var porcentaje = s.cuota ? Math.min(100, (s.usados / s.cuota) * 100) : 0;
    var agotada = restante <= 0;
    var extra = s.usosMax
      ? cantidad(s.usos) + " de " + cantidad(s.usosMax) + " usos en el mes" : "";
    return '<div class="saldo">' +
      cuenta(s.nombre, s.articulo || "",
             agotada ? "sin saldo" : cantidad(restante) + " de " + cantidad(s.cuota),
             agotada ? "" : unidad(s.unidad)) +
      (porcentaje > 0
        ? '<div class="consumo' + (agotada ? " agotado" : "") + '">' +
            '<i style="width:' + porcentaje.toFixed(1) + '%"></i></div>'
        : "") +
      (extra ? '<div class="saldo-nota">' + esc(extra) + "</div>" : "") +
    "</div>";
  }

  // ----------------------------------------------------- portal · permisos

  function pintarPermisos(permisos) {
    if (!permisos || !permisos.length) {
      $("permisos").innerHTML = '<div class="vacio">Todavía no tenés permisos aprobados.</div>';
      return;
    }
    $("permisos").innerHTML = '<div class="marco"><table><tbody>' + permisos.map(function (p) {
      return "<tr><td><b>" + esc(p.tipo) + "</b><br>" +
        '<span class="apunte">' + esc(p.inicio) + " al " + esc(p.fin) +
        " · aprobó " + esc(p.aprobador) + "</span></td>" +
        '<td><button class="btn-suave btn-chico" data-pdf="' + esc(p.id) + '">PDF</button></td></tr>';
    }).join("") + "</tbody></table></div>";
  }

  // ------------------------------------------------- portal · mis pedidos

  var ESTADO_PEDIDO = { Pendiente: "t-tardanza", Aprobado: "t-normal", Rechazado: "t-ausente" };

  function pintarSolicitudes(solicitudes) {
    if (!solicitudes || !solicitudes.length) {
      $("mis-solicitudes").innerHTML =
        '<div class="vacio">No presentaste ningún pedido de permiso.</div>';
      return;
    }
    $("mis-solicitudes").innerHTML = '<div class="marco"><table class="registro"><thead><tr>' +
      "<th>Pedido</th><th>Período</th><th>Estado</th><th></th>" +
      "</tr></thead><tbody>" +
      solicitudes.map(function (s) {
        var detalle = s.horas ? horas(s.horas) + " h" : "";
        var cierre = s.observacion ? " · " + s.observacion
                   : (s.revisor ? " · resolvió " + s.revisor : "");
        return "<tr><td><b>" + esc(s.tipo) + "</b>" +
            (detalle ? " " + esc(detalle) : "") +
            '<br><span class="apunte">' + esc(s.motivo) + esc(cierre) + "</span></td>" +
          "<td>" + esc(s.inicio) + (s.fin !== s.inicio ? " al " + esc(s.fin) : "") + "</td>" +
          '<td><span class="sello ' + (ESTADO_PEDIDO[s.estado] || "") + '">' +
            esc(s.estado) + "</span></td>" +
          "<td>" + (s.justificacion_id
            ? '<button class="btn-suave btn-chico" data-pdf="' + esc(s.justificacion_id) +
              '">PDF</button>'
            : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  // --------------------------------------------- portal · pedir un permiso

  /* El formulario se arma con los artículos que de verdad le aplican a esta
     persona y muestra el saldo antes de pedir: el reglamento deja de ser un
     PDF que hay que consultar aparte. */
  function abrirPedidoPermiso() {
    pedir("/api/permisos/catalogo").then(function (datos) {
      var opciones = (datos.articulos || []).map(function (a) {
        var saldo = a.restantes === null
          ? "sin tope"
          : cantidad(a.restantes) + " " + unidad(a.unidad) + " disponibles";
        return '<option value="' + esc(a.tipo) + '"' +
          (a.solicitable ? "" : " disabled") + ">" +
          esc(a.nombre) + " · " + esc(a.articulo) + " · " + esc(saldo) +
          (a.solicitable ? "" : " · sin cupo") + "</option>";
      }).join("");

      abrirModal(
        '<div class="modal-cabeza"><div>' +
          "<h2>Pedir un permiso</h2>" +
          '<p class="apunte">Vínculo ' + esc(datos.vinculo) +
          " · el pedido se valida contra el reglamento antes de enviarse.</p>" +
        "</div>" + botonCerrar() + "</div>" +
        '<div class="campo"><label for="p-tipo">Artículo que invocás</label>' +
          '<select id="p-tipo">' + opciones + "</select></div>" +
        '<div id="p-condiciones"></div>' +
        '<div class="rejilla-campos">' +
          '<div class="campo"><label for="p-desde">Desde</label>' +
            '<input id="p-desde" type="date" value="' + iso(new Date()) + '"></div>' +
          '<div class="campo"><label for="p-hasta">Hasta</label>' +
            '<input id="p-hasta" type="date" value="' + iso(new Date()) + '"></div>' +
        "</div>" +
        '<div class="campo oculto" id="p-campo-horas"><label for="p-horas">Cantidad de horas</label>' +
          '<input id="p-horas" type="number" min="0.5" step="0.5" value="1"></div>' +
        '<div class="campo"><label for="p-motivo">Motivo</label>' +
          '<textarea id="p-motivo" placeholder="Ej. tengo turno médico a las 10:00 y vuelvo después."></textarea></div>' +
        '<div id="p-aviso"></div>' +
        '<div class="acciones fin" style="margin-top:16px">' +
          '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
          '<button id="p-enviar">Enviar pedido</button>' +
        "</div>"
      );

      sesion.articulos = datos.articulos || [];
      $("p-tipo").addEventListener("change", refrescarArticulo);
      $("p-enviar").addEventListener("click", enviarPedidoPermiso);
      refrescarArticulo();
    }).catch(function (error) {
      if (error.message !== "sesion") notificar("No se pudo abrir el formulario", error.message, "alta");
    });
  }

  function articuloElegido() {
    var tipo = $("p-tipo") && $("p-tipo").value;
    return (sesion.articulos || []).find(function (a) { return a.tipo === tipo; });
  }

  /* Las condiciones del artículo se muestran en el momento de pedirlo, que
     es cuando el empleado puede hacer algo con ellas. */
  function refrescarArticulo() {
    var articulo = articuloElegido();
    if (!articulo) return;
    var porHoras = articulo.unidad === "horas";
    $("p-campo-horas").classList.toggle("oculto", !porHoras);
    $("p-hasta").disabled = porHoras;
    if (porHoras) $("p-hasta").value = $("p-desde").value;
    $("p-condiciones").innerHTML =
      '<div class="cuenta"><span class="cuenta-concepto">' + esc(articulo.articulo) +
        '<span class="cuenta-fuente">' + esc(articulo.reglamento) + "</span></span>" +
        '<span class="cuenta-relleno"></span><span class="cuenta-cifra">' +
        (articulo.restantes === null ? "sin tope"
          : esc(cantidad(articulo.restantes)) + "<small>" +
            esc(unidad(articulo.unidad)) + "</small>") +
      "</span></div>" +
      '<div class="saldo-nota">' + esc(articulo.condiciones) +
      (articulo.pendientes ? " · " + esc(cantidad(articulo.pendientes)) + " " +
        esc(unidad(articulo.unidad)) + " ya comprometidos en pedidos sin resolver"
        : "") + "</div>";
  }

  function enviarPedidoPermiso() {
    var articulo = articuloElegido();
    if (!articulo) return;
    var porHoras = articulo.unidad === "horas";
    var cuerpo = {
      tipo_permiso: articulo.tipo,
      fecha_inicio: $("p-desde").value,
      fecha_fin: porHoras ? $("p-desde").value : $("p-hasta").value,
      horas_solicitadas: porHoras ? parseFloat($("p-horas").value) || 0 : 0,
      motivo: $("p-motivo").value.trim()
    };
    if (!cuerpo.fecha_inicio || !cuerpo.fecha_fin) {
      avisar("p-aviso", "Elegí las fechas del permiso."); return;
    }
    $("p-enviar").disabled = true;
    pedir("/api/permisos/solicitar", { cuerpo: cuerpo }).then(function (datos) {
      cerrarModal();
      notificar("Pedido enviado", datos.mensaje, "baja");
      cargarPortal();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("p-aviso", error.message);
      $("p-enviar").disabled = false;
    });
  }

  function periodoActual() {
    var hoy = new Date();
    return {
      anio: hoy.getFullYear(),
      mes: hoy.getMonth() + 1,
      sufijo: hoy.getFullYear() + String(hoy.getMonth() + 1).padStart(2, "0")
    };
  }

  function descargarPlanillaExtra() {
    var p = periodoActual();
    descargarPdf(
      "/api/horas-extra/pdf?anio=" + p.anio + "&mes=" + p.mes,
      "horas_extra_" + p.sufijo + ".pdf"
    ).catch(function (e) { notificar("No se pudo generar la planilla", e.message, "alta"); });
  }

  /* La constancia sale del mismo rango que el empleado está mirando: es el
     papel que hasta ahora había que ir a pedirle a Recursos Humanos. */
  function descargarConstancia() {
    var desde = $("h-desde").value, hasta = $("h-hasta").value;
    if (!desde || !hasta) {
      avisar("h-resultado", "Elegí el rango que querés certificar."); return;
    }
    descargarPdf(
      "/api/constancia/pdf?desde=" + desde + "&hasta=" + hasta,
      "constancia_" + desde + "_" + hasta + ".pdf"
    ).catch(function (e) { notificar("No se pudo emitir la constancia", e.message, "alta"); });
  }

  function descargarExtrasDe(userId, usuario) {
    var p = periodoActual();
    descargarPdf(
      "/api/panel/horas-extra/" + userId + "/pdf?anio=" + p.anio + "&mes=" + p.mes,
      "horas_extra_" + (usuario || userId) + "_" + p.sufijo + ".pdf"
    ).catch(function (e) { notificar("No se pudo generar la planilla", e.message, "alta"); });
  }

  function cargarPortal() {
    return pedir("/api/resumen").then(function (datos) {
      sesion.resumen = datos;
      pintarParte(datos.hoy);
      pintarTurno(datos.turno);
      pintarRegistro(datos.dias_mes || [], datos.mes_nombre);
      pintarCuentasMes(datos.extras_mes || {});
      pintarSaldos(datos);
      pintarSolicitudes(datos.solicitudes);
      pintarPermisos(datos.permisos);
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("parte-hoy", error.message);
    });
  }

  // ---------------------------------------------------------------- modal

  function abrirModal(html) {
    $("capa-modal").innerHTML = '<div class="telon"><div class="modal" role="dialog" aria-modal="true">' + html + "</div></div>";
    var telon = $("capa-modal").firstElementChild;
    telon.addEventListener("click", function (e) { if (e.target === telon) cerrarModal(); });
    document.addEventListener("keydown", escapeCierra);
  }

  function cerrarModal() {
    $("capa-modal").innerHTML = "";
    document.removeEventListener("keydown", escapeCierra);
  }

  function escapeCierra(e) { if (e.key === "Escape") cerrarModal(); }

  function botonCerrar() {
    return '<button class="boton-signo" data-cerrar="1" aria-label="Cerrar">×</button>';
  }

  var ETIQUETA_DIA = {
    normal: "Normal", tardanza: "Llegada tardía", ausente: "Sin marcar",
    justificado: "Justificado", descanso: "Descanso", en_curso: "En curso",
    sin_cierre: "Salida no registrada", franco: "Día franco",
    futuro: "Pendiente"
  };

  function abrirDia(fecha) {
    var dia = (sesion.resumen && sesion.resumen.dias_mes || [])
      .find(function (d) { return d.fecha === fecha; });
    if (!dia) return;
    var lineas = "";
    if (dia.entrada) {
      lineas += cuenta("Entrada", "", dia.entrada, "") +
        cuenta("Salida", "", dia.salida || "en curso", "") +
        cuenta("Trabajado", "", horas(dia.horas), "h", "suma");
      if (dia.jornada) lineas += cuenta("Tipo de jornada", "Art. 194", dia.jornada, "");
    }
    if (dia.incidencia) lineas += cuenta("Novedad", "", dia.incidencia, "");
    if (dia.descanso) lineas += cuenta("Carácter del día", "Art. 233", "Descanso · recargo 100 %", "");

    abrirModal(
      '<div class="modal-cabeza"><div>' +
        "<h2>" + esc(new Date(fecha + "T12:00:00").toLocaleDateString("es-PY",
          { weekday: "long", day: "numeric", month: "long" })) + "</h2>" +
        '<p><span class="sello t-' + esc(dia.estado) + '">' +
          esc(ETIQUETA_DIA[dia.estado] || dia.estado) + "</span></p>" +
      "</div>" + botonCerrar() + "</div>" +
      (lineas || '<div class="vacio">No hay marcas registradas este día.</div>') +
      '<div class="acciones fin" style="margin-top:22px">' +
        '<button class="btn-suave" data-cerrar="1">Cerrar</button>' +
        (dia.estado === "futuro" ? "" :
          '<button data-reclamar="' + esc(fecha) + '">Pedir corrección</button>') +
      "</div>"
    );
  }

  function abrirReclamo(fecha) {
    abrirModal(
      '<div class="modal-cabeza"><div>' +
        "<h2>Pedir una corrección</h2>" +
        '<p class="apunte">Recursos Humanos revisa y aprueba cada pedido.</p>' +
      "</div>" + botonCerrar() + "</div>" +
      '<div class="campo"><label for="r-fecha">Día del incidente</label>' +
        '<input id="r-fecha" type="date" max="' + iso(new Date()) +
        '" value="' + esc(fecha || iso(new Date())) + '"></div>' +
      '<div class="rejilla-campos">' +
        '<div class="campo"><label for="r-tipo">Marca a corregir</label>' +
          '<select id="r-tipo"><option value="Entrada">Entrada</option>' +
          '<option value="Salida">Salida</option></select></div>' +
        '<div class="campo"><label for="r-hora">Hora correcta</label>' +
          '<input id="r-hora" type="time"></div>' +
      "</div>" +
      '<div class="campo"><label for="r-motivo">¿Qué pasó?</label>' +
        '<textarea id="r-motivo" placeholder="Ej. el lector de huella no me reconoció y no pude marcar la entrada."></textarea></div>' +
      '<div id="r-aviso"></div>' +
      '<div class="acciones fin" style="margin-top:16px">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button id="r-enviar">Enviar a Recursos Humanos</button>' +
      "</div>"
    );
    $("r-enviar").addEventListener("click", enviarReclamo);
  }

  function enviarReclamo() {
    var cuerpo = {
      tipo_marca: $("r-tipo").value,
      fecha: $("r-fecha").value,
      hora_propuesta: $("r-hora").value,
      motivo: $("r-motivo").value.trim()
    };
    if (!cuerpo.fecha || !cuerpo.hora_propuesta) {
      avisar("r-aviso", "Indicá el día y la hora correcta."); return;
    }
    if (cuerpo.motivo.length < 10) {
      avisar("r-aviso", "Contá brevemente qué pasó (al menos 10 caracteres)."); return;
    }
    $("r-enviar").disabled = true;
    pedir("/api/reclamo", { cuerpo: cuerpo }).then(function (datos) {
      cerrarModal();
      notificar("Pedido enviado", datos.mensaje, "baja");
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("r-aviso", error.message);
      $("r-enviar").disabled = false;
    });
  }

  // ------------------------------------------------------------ historial

  function consultarHistorial() {
    var cuerpo = { desde: $("h-desde").value, hasta: $("h-hasta").value };
    if (!cuerpo.desde || !cuerpo.hasta) {
      avisar("h-resultado", "Elegí el rango de fechas."); return;
    }
    pedir("/api/consulta", { cuerpo: cuerpo }).then(pintarHistorial)
      .catch(function (error) {
        if (error.message !== "sesion") avisar("h-resultado", error.message);
      });
  }

  function pintarHistorial(d) {
    var marcas = d.marcas || [];
    var tabla = marcas.length
      ? '<div class="marco"><table class="registro"><thead><tr><th>Fecha</th><th>Novedad</th>' +
        '<th class="num">Entrada</th><th class="num">Salida</th>' +
        '<th class="num prescindible">Jornada</th><th class="num">Ordinarias</th>' +
        '<th class="num prescindible">Nocturnas</th><th class="num">50 %</th>' +
        '<th class="num">100 %</th></tr></thead><tbody>' +
        marcas.map(function (m) {
          return "<tr><td>" + esc(m.fecha) + "</td>" +
            "<td>" + (m.incidencia ? '<span class="sello t-tardanza">' +
              esc(m.incidencia) + "</span>" : raya()) + "</td>" +
            '<td class="num">' + esc(m.entrada) + "</td>" +
            '<td class="num">' + esc(m.salida || "en curso") + "</td>" +
            '<td class="num prescindible">' + esc(m.jornada || "—") + "</td>" +
            '<td class="num">' + esc(m.ordinarias) + "</td>" +
            '<td class="num prescindible">' + esc(m.nocturnas || "00:00") + "</td>" +
            '<td class="num">' + esc(m.extra_50) + "</td>" +
            '<td class="num">' + esc(m.extra_100) + "</td></tr>";
        }).join("") + "</tbody></table></div>"
      : '<div class="vacio">Sin marcas en el período elegido.</div>';

    var e = d.extras_periodo || {};
    var ag = d.aguinaldo_periodo || {};
    $("h-resultado").innerHTML =
      "<section>" + tabla + "</section>" +
      '<section><div class="rubro"><h3>Totales del período ' +
        esc(d.desde) + " al " + esc(d.hasta) + "</h3></div>" +
        cuenta("Nocturnas", "Art. 232 · recargo 30 %", e.texto_nocturnas || "00:00", "") +
        cuenta("Extra diurnas", "Art. 234 · recargo 50 %", e.texto_50 || "00:00", "") +
        cuenta("Extra nocturnas, domingo o feriado", "Art. 233 · recargo 100 %",
               e.texto_100 || "00:00", "") +
        cuenta("Aguinaldo estimado",
               (ag.meses_periodo || 0) + " meses · proyección, no liquidación",
               guaranies(ag.aguinaldo), "", "suma") +
      "</section>";
  }

  // -------------------------------------------------------------- gestión

  var PANELES = ["pendientes", "personal", "turnos", "justificaciones",
                 "condiciones", "alertas", "auditoria"];

  function abrirPestana(nombre) {
    PANELES.forEach(function (p) {
      $("g-" + p).classList.toggle("oculto", p !== nombre);
    });
    document.querySelectorAll("#pestanas-gestion .pestana").forEach(function (b) {
      b.setAttribute("aria-selected", b.dataset.panel === nombre ? "true" : "false");
    });
    if (nombre === "pendientes") cargarPendientes();
    if (nombre === "personal") cargarPersonal();
    if (nombre === "turnos") cargarTurnos();
    if (nombre === "justificaciones") cargarJustificaciones();
    if (nombre === "condiciones") cargarCondiciones();
    if (nombre === "alertas") cargarAlertas();
    if (nombre === "auditoria") cargarAuditoria();
  }

  /* La bandeja de RRHH es por excepción: de 200 empleados importan los que
     tienen algo sin resolver, no el recuento total de la plantilla. */
  function cargarPendientes() {
    $("g-pendientes").innerHTML = '<div class="vacio">Cargando…</div>';
    Promise.all([
      pedir("/api/panel/resumen"),
      pedir("/api/panel/correcciones"),
      pedir("/api/panel/solicitudes-permiso")
    ]).then(function (r) {
      var resumen = r[0], correcciones = r[1] || [], permisos = r[2] || [];
      var abiertas = correcciones.filter(function (c) { return c.estado === "Pendiente"; });
      var pedidos = permisos.filter(function (p) { return p.estado === "Pendiente"; });

      var listaPermisos = pedidos.map(function (p) {
        var cantidad = Number(p.horas_solicitadas)
          ? horas(p.horas_solicitadas) + " h"
          : p.fecha_inicio + (p.fecha_fin !== p.fecha_inicio ? " al " + p.fecha_fin : "");
        return '<div class="pendiente media">' +
          '<div class="pendiente-cuerpo">' +
            '<div class="pendiente-titulo">' + esc(p.full_name) + " · " +
              esc(p.tipo_permiso) + " · " + esc(cantidad) + "</div>" +
            '<div class="pendiente-detalle">' + esc(p.motivo) +
              " · " + esc(p.tipo_vinculo || "Funcionario") + "</div>" +
          "</div>" +
          '<div class="acciones">' +
            '<button class="btn-chico" data-permiso-ok="' + esc(p.id) + '">Aprobar</button>' +
            '<button class="btn-suave btn-chico" data-permiso-no="' + esc(p.id) + '">Rechazar</button>' +
          "</div></div>";
      }).join("");

      var listaCorrecciones = abiertas.map(function (c) {
        return '<div class="pendiente urgente">' +
          '<div class="pendiente-cuerpo">' +
            '<div class="pendiente-titulo">' + esc(c.full_name) +
              " · " + esc(c.tipo_marca) + " del " + esc(c.fecha_registro) + "</div>" +
            '<div class="pendiente-detalle">Propone ' + esc(c.hora_propuesta) +
              " · " + esc(c.motivo) + "</div>" +
          "</div>" +
          '<div class="acciones">' +
            '<button class="btn-chico" data-aprobar="' + esc(c.id) + '">Aprobar</button>' +
            '<button class="btn-suave btn-chico" data-rechazar="' + esc(c.id) + '">Rechazar</button>' +
          "</div></div>";
      }).join("");

      $("g-pendientes").innerHTML =
        '<div class="rubro"><h3>Pedidos de permiso</h3>' +
          '<span class="apunte">Validados contra el reglamento al presentarse</span></div>' +
        (listaPermisos || '<div class="vacio">Ningún pedido esperando resolución.</div>') +
        '<div class="bloque" style="margin-top:34px">' +
          '<div class="rubro"><h3>Correcciones de marcaje</h3></div>' +
          (listaCorrecciones || '<div class="vacio">Ninguna corrección sin resolver.</div>') +
        "</div>" +
        '<div class="bloque" style="margin-top:34px">' +
          '<div class="rubro"><h3>Estado del día</h3></div>' +
          cuenta("Condición declarada para hoy", "",
                 resumen.condicion_hoy || "día normal", "") +
          cuenta("Marcas sin verificación biométrica", "últimos 7 días",
                 resumen.marcas_sin_verificar || 0, "") +
          cuenta("Alertas sin leer", "", resumen.alertas_no_leidas || 0, "") +
          cuenta("Marcajes registrados hoy", "", resumen.marcas_hoy || 0, "") +
          cuenta("Personal en plantilla", "", resumen.personal || 0, "") +
        "</div>";

      var total = abiertas.length + pedidos.length;
      $("nav-gestion").innerHTML = "Gestión" +
        (total ? '<span class="contador">' + total + "</span>" : "");
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-pendientes", error.message);
    });
  }

  /* Un rechazo sin motivo obliga al empleado a preguntar; se exige acá para
     que la respuesta viaje con el pedido. */
  function rechazarPermiso(id) {
    abrirModal(
      '<div class="modal-cabeza"><div><h2>Rechazar el pedido</h2>' +
      '<p class="apunte">El motivo se muestra al empleado junto a su solicitud.</p></div>' +
      botonCerrar() + "</div>" +
      '<div class="campo"><label for="rp-motivo">Motivo del rechazo</label>' +
        '<textarea id="rp-motivo" placeholder="Ej. el equipo ya tiene dos personas de licencia esa semana."></textarea></div>' +
      '<div id="rp-aviso"></div>' +
      '<div class="acciones fin">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button class="btn-riesgo" id="rp-confirmar">Rechazar</button>' +
      "</div>"
    );
    $("rp-confirmar").addEventListener("click", function () {
      var motivo = $("rp-motivo").value.trim();
      if (motivo.length < 10) {
        avisar("rp-aviso", "Explicá el motivo (al menos 10 caracteres)."); return;
      }
      cerrarModal();
      resolverPermiso(id, false, motivo);
    });
  }

  function resolverPermiso(id, aprobar, observacion) {
    pedir("/api/panel/solicitudes-permiso/" + id + "/resolver", {
      cuerpo: { aprobar: aprobar, observacion: observacion || "" }
    }).then(function (d) {
      notificar("Permiso " + d.estado.toLowerCase(), d.mensaje, aprobar ? "baja" : "media");
      cargarPendientes();
    }).catch(function (error) {
      if (error.message !== "sesion") notificar("No se pudo resolver", error.message, "alta");
    });
  }

  // ------------------------------------------- gestión · condición del día

  var CONDICIONES = [
    ["Lluvia intensa", 30],
    ["Corte de rutas o manifestación", 30],
    ["Corte de energía", 30],
    ["Paro de transporte público", 60]
  ];

  /* Reemplaza a la casilla que el empleado marcaba en el kiosco: la
     tolerancia la reconoce la empresa, queda firmada y alcanza a todos. */
  // ------------------------------------------------------ gestión · turnos

  var DIAS_SEMANA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

  /* Los turnos se editan como se dictan: un nombre, una o dos franjas y los
     días en que rigen. La segunda franja es la jornada partida del comercio
     y aparece solo si se la pide, para no cargar el formulario del caso
     común con campos que casi nadie completa. */
  function cargarTurnos() {
    $("g-turnos").innerHTML = '<div class="vacio">Cargando…</div>';
    Promise.all([
      pedir("/api/panel/turnos?incluir_inactivos=true"),
      pedir("/api/panel/personal")
    ]).then(function (r) {
      sesion.turnos = r[0] || [];
      sesion.personal = (r[1] || {}).personal || [];
      pintarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-turnos", error.message);
    });
  }

  function casillasDias(mascara) {
    return DIAS_SEMANA.map(function (dia, i) {
      var marcado = mascara.charAt(i) === "1" ? " checked" : "";
      return '<label class="dia-casilla"><input type="checkbox" data-dia="' + i +
        '"' + marcado + "><span>" + dia + "</span></label>";
    }).join("");
  }

  function pintarTurnos() {
    var filas = sesion.turnos.map(function (t) {
      var marcas = [];
      if (t.predeterminado) marcas.push('<span class="sello t-justificado">Predeterminado</span>');
      if (t.partida) marcas.push('<span class="sello t-normal">Partida</span>');
      if (t.nocturno) marcas.push('<span class="sello t-justificado">Nocturno</span>');
      if (!t.activo) marcas.push('<span class="sello t-ausente">Retirado</span>');
      var dotacion = Math.max(t.dotacion || 0, t.asignados || 0);

      return '<tr' + (t.activo ? "" : ' class="futuro"') + "><td><b>" + esc(t.nombre) + "</b>" +
          (marcas.length ? "<br>" + marcas.join(" ") : "") +
          '<br><span class="apunte">' + esc(t.sucursal) + "</span></td>" +
        '<td class="num mono">' + esc(t.horario) + "</td>" +
        "<td>" + esc(t.dias_texto) + "</td>" +
        '<td class="num">' + esc(t.horas_previstas) + " h</td>" +
        '<td class="num">' + (t.tolerancia_min === null ? "según vínculo"
                                                        : esc(t.tolerancia_min) + " min") + "</td>" +
        '<td class="num">' + esc(dotacion) + "</td>" +
        '<td><div class="acciones fin">' +
          (t.predeterminado || !t.activo ? ""
            : '<button class="btn-suave btn-chico" data-turno-predeterminado="' + esc(t.id) +
              '">Hacer predeterminado</button>') +
          '<button class="btn-suave btn-chico" data-turno-editar="' + esc(t.id) + '">Editar</button>' +
          (t.predeterminado ? ""
            : '<button class="btn-riesgo btn-chico" data-turno-retirar="' + esc(t.id) +
              '">Retirar</button>') +
        "</div></td></tr>";
    }).join("");

    var plantilla = sesion.personal.filter(function (p) { return p.activo !== false; });
    var dotacion = plantilla.map(function (p) {
      return "<tr><td><b>" + esc(p.full_name) + "</b>" +
          '<br><span class="apunte">' + esc(p.username) + "</span></td>" +
        "<td>" + esc(p.turno_hoy || "Turno predeterminado") +
          (p.turno_origen === "asignacion"
            ? ' <span class="sello t-justificado">Rotación</span>'
            : "") +
          (p.turno_origen === "predeterminado"
            ? '<br><span class="apunte">Sin turno propio en el legajo</span>'
            : "") + "</td>" +
        '<td><div class="acciones fin">' +
          '<button class="btn-suave btn-chico" data-turno-asignar="' + esc(p.id) + '">Cambiar turno</button>' +
          '<button class="btn-suave btn-chico" data-turno-rotar="' + esc(p.id) + '">Rotar</button>' +
        "</div></td></tr>";
    }).join("");

    $("g-turnos").innerHTML =
      '<div class="bloque"><div class="rubro"><h3>Nuevo turno</h3>' +
        '<span class="apunte">El horario contra el que se miden las tardanzas</span></div>' +
        '<div class="rejilla-campos">' +
          '<div><label for="t-nombre">Nombre</label><input id="t-nombre" placeholder="Mañana, Noche, Mostrador…"></div>' +
          '<div><label for="t-sucursal">Sucursal</label><input id="t-sucursal" value="Casa Central"></div>' +
          '<div><label for="t-entrada">Entrada</label><input id="t-entrada" type="time" value="08:00"></div>' +
          '<div><label for="t-salida">Salida</label><input id="t-salida" type="time" value="16:00"></div>' +
          '<div><label for="t-tolerancia">Tolerancia propia (min)</label>' +
            '<input id="t-tolerancia" type="number" min="0" max="60" placeholder="según vínculo"></div>' +
        "</div>" +
        '<div class="rejilla-campos" style="margin-top:12px">' +
          '<div><label>Días</label><div class="dias-fila" id="t-dias">' +
            casillasDias("1111100") + "</div></div>" +
        "</div>" +
        '<label class="casilla" style="margin-top:12px">' +
          '<input type="checkbox" id="t-partida"> Jornada partida (dos franjas)</label>' +
        '<div class="rejilla-campos oculto" id="t-segundo-tramo" style="margin-top:8px">' +
          '<div><label for="t-entrada2">Segunda entrada</label><input id="t-entrada2" type="time" value="14:00"></div>' +
          '<div><label for="t-salida2">Segunda salida</label><input id="t-salida2" type="time" value="18:00"></div>' +
        "</div>" +
        '<div class="acciones fin" style="margin-top:16px"><button id="t-crear">Crear turno</button></div>' +
        '<div id="t-aviso"></div></div>' +
      '<div class="bloque"><div class="rubro"><h3>Turnos definidos</h3></div>' +
        (filas ? '<div class="marco"><table><thead><tr><th>Turno</th><th class="num">Horario</th>' +
          '<th>Días</th><th class="num">Previstas</th><th class="num">Tolerancia</th>' +
          '<th class="num">Dotación</th><th></th></tr></thead><tbody>' + filas + "</tbody></table></div>"
               : '<div class="vacio">Todavía no hay turnos definidos.</div>') +
      "</div>" +
      '<div class="bloque"><div class="rubro"><h3>Quién trabaja en qué turno</h3>' +
        '<span class="apunte">Cambiar turno fija el del contrato; rotar lo desplaza por un período</span></div>' +
        (dotacion ? '<div class="marco"><table><thead><tr><th>Empleado</th><th>Turno de hoy</th>' +
          "<th></th></tr></thead><tbody>" + dotacion + "</tbody></table></div>"
                  : '<div class="vacio">Sin personal activo.</div>') +
      "</div>";

    $("t-partida").addEventListener("change", function () {
      $("t-segundo-tramo").classList.toggle("oculto", !$("t-partida").checked);
    });
    $("t-crear").addEventListener("click", crearTurno);
  }

  function mascaraDe(contenedor) {
    var casillas = $(contenedor).querySelectorAll("input[data-dia]");
    var mascara = "";
    for (var i = 0; i < 7; i++) mascara += casillas[i].checked ? "1" : "0";
    return mascara;
  }

  function tramosDe(prefijo, partida) {
    var tramos = [{ entrada: $(prefijo + "-entrada").value, salida: $(prefijo + "-salida").value }];
    if (partida) {
      tramos.push({ entrada: $(prefijo + "-entrada2").value, salida: $(prefijo + "-salida2").value });
    }
    return tramos;
  }

  function crearTurno() {
    var mascara = mascaraDe("t-dias");
    if (mascara.indexOf("1") < 0) {
      avisar("t-aviso", "Marcá al menos un día de la semana."); return;
    }
    var tolerancia = $("t-tolerancia").value;
    pedir("/api/panel/turnos", {
      cuerpo: {
        nombre: $("t-nombre").value.trim(),
        tramos: tramosDe("t", $("t-partida").checked),
        dias: mascara,
        sucursal: $("t-sucursal").value.trim() || "Casa Central",
        tolerancia_min: tolerancia === "" ? null : Number(tolerancia)
      }
    }).then(function (t) {
      notificar("Turno creado", t.nombre + " · " + t.horario, "baja");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("t-aviso", error.message);
    });
  }

  function editarTurno(id) {
    var turno = sesion.turnos.filter(function (t) { return t.id === id; })[0];
    if (!turno) return;
    var segundo = turno.tramos[1] || { entrada: "14:00", salida: "18:00" };
    abrirModal(
      "<h3>" + esc(turno.nombre) + "</h3>" +
      '<p class="apunte">El cambio rige hacia adelante: los días ya liquidados ' +
        "conservan la incidencia que se calculó con el horario de entonces.</p>" +
      '<div class="rejilla-campos">' +
        '<div><label for="m-nombre">Nombre</label><input id="m-nombre" value="' + esc(turno.nombre) + '"></div>' +
        '<div><label for="m-sucursal">Sucursal</label><input id="m-sucursal" value="' + esc(turno.sucursal) + '"></div>' +
        '<div><label for="m-entrada">Entrada</label><input id="m-entrada" type="time" value="' +
          esc(turno.tramos[0].entrada) + '"></div>' +
        '<div><label for="m-salida">Salida</label><input id="m-salida" type="time" value="' +
          esc(turno.tramos[0].salida) + '"></div>' +
        '<div><label for="m-tolerancia">Tolerancia propia (min)</label>' +
          '<input id="m-tolerancia" type="number" min="0" max="60" value="' +
          (turno.tolerancia_min === null ? "" : esc(turno.tolerancia_min)) +
          '" placeholder="según vínculo"></div>' +
      "</div>" +
      '<div class="rejilla-campos" style="margin-top:12px"><div><label>Días</label>' +
        '<div class="dias-fila" id="m-dias">' + casillasDias(turno.dias) + "</div></div></div>" +
      '<label class="casilla" style="margin-top:12px"><input type="checkbox" id="m-partida"' +
        (turno.partida ? " checked" : "") + "> Jornada partida (dos franjas)</label>" +
      '<div class="rejilla-campos' + (turno.partida ? "" : " oculto") + '" id="m-segundo-tramo">' +
        '<div><label for="m-entrada2">Segunda entrada</label><input id="m-entrada2" type="time" value="' +
          esc(segundo.entrada) + '"></div>' +
        '<div><label for="m-salida2">Segunda salida</label><input id="m-salida2" type="time" value="' +
          esc(segundo.salida) + '"></div>' +
      "</div>" +
      '<div id="m-aviso"></div>' +
      '<div class="acciones fin" style="margin-top:16px">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button data-turno-guardar="' + esc(id) + '">Guardar</button></div>'
    );
    $("m-partida").addEventListener("change", function () {
      $("m-segundo-tramo").classList.toggle("oculto", !$("m-partida").checked);
    });
  }

  function guardarTurno(id) {
    var mascara = mascaraDe("m-dias");
    if (mascara.indexOf("1") < 0) {
      avisar("m-aviso", "Marcá al menos un día de la semana."); return;
    }
    var tolerancia = $("m-tolerancia").value;
    pedir("/api/panel/turnos/" + id, {
      metodo: "PUT",
      cuerpo: {
        nombre: $("m-nombre").value.trim(),
        tramos: tramosDe("m", $("m-partida").checked),
        dias: mascara,
        sucursal: $("m-sucursal").value.trim() || "Casa Central",
        tolerancia_min: tolerancia === "" ? null : Number(tolerancia),
        borrar_tolerancia: tolerancia === ""
      }
    }).then(function (t) {
      cerrarModal();
      notificar("Turno actualizado", t.nombre + " · " + t.horario, "baja");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("m-aviso", error.message);
    });
  }

  function retirarTurno(id) {
    var turno = sesion.turnos.filter(function (t) { return t.id === id; })[0];
    pedir("/api/panel/turnos/" + id, { metodo: "DELETE" }).then(function (d) {
      notificar("Turno " + esc((turno || {}).nombre || ""), d.mensaje, "media");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-turnos", error.message);
    });
  }

  function hacerPredeterminado(id) {
    pedir("/api/panel/turnos/" + id + "/predeterminado", { cuerpo: {} })
      .then(function (t) {
        notificar("Turno predeterminado", t.nombre, "baja");
        cargarTurnos();
      }).catch(function (error) {
        if (error.message !== "sesion") avisar("g-turnos", error.message);
      });
  }

  function opcionesTurno(elegido) {
    var activos = sesion.turnos.filter(function (t) { return t.activo; });
    return '<option value="">Turno predeterminado de la empresa</option>' +
      activos.map(function (t) {
        return '<option value="' + esc(t.id) + '"' + (t.id === elegido ? " selected" : "") +
          ">" + esc(t.nombre) + " · " + esc(t.horario) + "</option>";
      }).join("");
  }

  function abrirAsignacionTurno(usuarioId) {
    var persona = sesion.personal.filter(function (p) { return p.id === usuarioId; })[0];
    if (!persona) return;
    abrirModal(
      "<h3>Turno de " + esc(persona.full_name) + "</h3>" +
      '<p class="apunte">Es el horario de contrato: rige mientras no haya una rotación vigente.</p>' +
      '<div><label for="a-turno">Turno</label><select id="a-turno">' +
        opcionesTurno(persona.turno_id) + "</select></div>" +
      '<div id="a-aviso"></div>' +
      '<div class="acciones fin" style="margin-top:16px">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button data-turno-base="' + esc(usuarioId) + '">Guardar</button></div>'
    );
  }

  function guardarTurnoBase(usuarioId) {
    var elegido = $("a-turno").value;
    pedir("/api/panel/personal/" + usuarioId + "/turno", {
      cuerpo: { turno_id: elegido === "" ? null : Number(elegido) }
    }).then(function (t) {
      cerrarModal();
      notificar("Turno asignado", t.nombre + " · " + t.horario, "baja");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("a-aviso", error.message);
    });
  }

  function abrirRotacion(usuarioId) {
    var persona = sesion.personal.filter(function (p) { return p.id === usuarioId; })[0];
    if (!persona) return;
    pedir("/api/panel/personal/" + usuarioId + "/turno").then(function (actual) {
      var vigentes = (actual.rotaciones || []).map(function (r) {
        return "<tr><td>" + esc(r.turno) + "</td>" +
          '<td class="num mono">' + esc(r.desde) + " → " + esc(r.hasta || "sin fin") + "</td>" +
          "<td>" + esc(r.motivo || "—") + "</td>" +
          '<td><button class="btn-riesgo btn-chico" data-rotacion-revocar="' + esc(r.id) +
            '">Cancelar</button></td></tr>';
      }).join("");

      abrirModal(
        "<h3>Rotar a " + esc(persona.full_name) + "</h3>" +
        '<p class="apunte">Vigente hoy: <b>' + esc(actual.nombre) + " · " + esc(actual.horario) +
          "</b>. Al vencer la rotación vuelve solo a su turno de contrato.</p>" +
        '<div class="rejilla-campos">' +
          '<div><label for="r-turno">Turno</label><select id="r-turno">' +
            sesion.turnos.filter(function (t) { return t.activo; }).map(function (t) {
              return '<option value="' + esc(t.id) + '">' + esc(t.nombre) + " · " +
                esc(t.horario) + "</option>";
            }).join("") + "</select></div>" +
          '<div><label for="r-desde">Desde</label><input id="r-desde" type="date" value="' +
            iso(new Date()) + '"></div>' +
          '<div><label for="r-hasta">Hasta (opcional)</label><input id="r-hasta" type="date"></div>' +
          '<div><label for="r-motivo">Motivo</label><input id="r-motivo" placeholder="Relevo, licencia de un compañero…"></div>' +
        "</div>" +
        (vigentes ? '<div class="marco" style="margin-top:16px"><table><thead><tr>' +
          '<th>Turno</th><th class="num">Vigencia</th><th>Motivo</th><th></th>' +
          "</tr></thead><tbody>" + vigentes + "</tbody></table></div>" : "") +
        '<div id="r-aviso"></div>' +
        '<div class="acciones fin" style="margin-top:16px">' +
          '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
          '<button data-rotacion-guardar="' + esc(usuarioId) + '">Programar rotación</button></div>'
      );
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-turnos", error.message);
    });
  }

  function guardarRotacion(usuarioId) {
    if (!$("r-desde").value) { avisar("r-aviso", "Indicá desde cuándo rige."); return; }
    pedir("/api/panel/personal/" + usuarioId + "/rotacion", {
      cuerpo: {
        turno_id: Number($("r-turno").value),
        desde: $("r-desde").value,
        hasta: $("r-hasta").value || null,
        motivo: $("r-motivo").value.trim()
      }
    }).then(function (t) {
      cerrarModal();
      notificar("Rotación programada", t.nombre + " · " + t.horario, "baja");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("r-aviso", error.message);
    });
  }

  function revocarRotacion(id) {
    pedir("/api/panel/rotaciones/" + id, { metodo: "DELETE" }).then(function (d) {
      cerrarModal();
      notificar("Rotación cancelada", d.mensaje, "baja");
      cargarTurnos();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("r-aviso", error.message);
    });
  }

  function cargarCondiciones() {
    $("g-condiciones").innerHTML = '<div class="vacio">Cargando…</div>';
    pedir("/api/panel/condiciones").then(function (lista) {
      var filas = (lista || []).map(function (c) {
        return "<tr><td>" + esc(c.fecha) + "</td>" +
          "<td><b>" + esc(c.condicion) + "</b>" +
            (c.nota ? '<br><span class="apunte">' + esc(c.nota) + "</span>" : "") + "</td>" +
          '<td class="num">' + esc(c.tolerancia_min) + " min</td>" +
          "<td>" + esc(c.declarante) + "</td>" +
          '<td><button class="btn-riesgo btn-chico" data-revocar="' + esc(c.fecha) +
            '">Revocar</button></td></tr>';
      }).join("");

      $("g-condiciones").innerHTML =
        '<div class="bloque"><div class="rubro"><h3>Declarar una condición</h3>' +
          '<span class="apunte">Alcanza a toda la plantilla · Res. 3028/2024</span></div>' +
          '<div class="rejilla-campos">' +
            '<div><label for="c-fecha">Día</label><input id="c-fecha" type="date" value="' +
              iso(new Date()) + '"></div>' +
            '<div><label for="c-condicion">Condición</label><select id="c-condicion">' +
              CONDICIONES.map(function (c) {
                return '<option value="' + esc(c[1]) + '">' + esc(c[0]) + "</option>";
              }).join("") + "</select></div>" +
            '<div><label for="c-tolerancia">Tolerancia (minutos)</label>' +
              '<input id="c-tolerancia" type="number" min="0" max="120" step="5" value="30"></div>' +
            '<div><label for="c-nota">Nota interna</label><input id="c-nota"></div>' +
          "</div>" +
          '<div class="acciones fin" style="margin-top:16px">' +
            '<button id="c-declarar">Declarar</button></div>' +
          '<div id="c-aviso"></div></div>' +
        '<div class="bloque"><div class="rubro"><h3>Declaradas</h3></div>' +
          (filas ? '<div class="marco"><table><thead><tr><th>Fecha</th><th>Condición</th>' +
            '<th class="num">Tolerancia</th><th>Firmó</th><th></th></tr></thead><tbody>' +
            filas + "</tbody></table></div>"
                 : '<div class="vacio">Ningún día con condición declarada.</div>') +
        "</div>";

      $("c-condicion").addEventListener("change", function () {
        $("c-tolerancia").value = $("c-condicion").value;
      });
      $("c-declarar").addEventListener("click", declararCondicion);
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-condiciones", error.message);
    });
  }

  function declararCondicion() {
    var select = $("c-condicion");
    var cuerpo = {
      fecha: $("c-fecha").value,
      condicion: select.options[select.selectedIndex].textContent,
      tolerancia_min: parseInt($("c-tolerancia").value, 10) || 0,
      nota: $("c-nota").value.trim()
    };
    if (!cuerpo.fecha) { avisar("c-aviso", "Elegí el día."); return; }
    pedir("/api/panel/condiciones", { cuerpo: cuerpo }).then(function (d) {
      notificar("Condición declarada", d.mensaje, "media");
      cargarCondiciones();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("c-aviso", error.message);
    });
  }

  function revocarCondicion(fecha) {
    pedir("/api/panel/condiciones/" + fecha, { metodo: "DELETE" }).then(function (d) {
      notificar("Condición revocada", d.mensaje, "baja");
      cargarCondiciones();
    }).catch(function (error) {
      if (error.message !== "sesion") notificar("No se pudo revocar", error.message, "alta");
    });
  }

  function resolverCorreccion(id, aprobar) {
    pedir("/api/panel/correcciones/" + id + (aprobar ? "/aprobar" : "/rechazar"), { cuerpo: {} })
      .then(function (d) {
        notificar("Corrección " + d.estado.toLowerCase(), d.mensaje, "baja");
        cargarPendientes();
      }).catch(function (error) {
        if (error.message !== "sesion") notificar("No se pudo resolver", error.message, "alta");
      });
  }

  function cargarPersonal() {
    $("g-personal").innerHTML = '<div class="vacio">Cargando…</div>';
    pedir("/api/panel/personal").then(function (d) {
      sesion.personal = d.personal || [];
      sesion.roles = d.roles || [];
      sesion.turnos = d.turnos || [];
      pintarPersonal(null);
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-personal", error.message);
    });
  }

  function opciones(valores, elegido) {
    return valores.map(function (v) {
      return "<option" + (v === elegido ? " selected" : "") + ">" + esc(v) + "</option>";
    }).join("");
  }

  function pintarPersonal(editando) {
    var filas = sesion.personal.map(function (p) {
      if (p.id === editando) {
        return '<tr><td colspan="7"><div class="rejilla-campos">' +
          '<div><label>Nombre</label><input id="e-nombre" value="' + esc(p.full_name) + '"></div>' +
          "<div><label>Rol</label><select id=\"e-rol\">" + opciones(sesion.roles, p.role_name) + "</select></div>" +
          "<div><label>Vínculo</label><select id=\"e-vinculo\">" +
            opciones(["Funcionario", "Pasante"], p.tipo_vinculo) + "</select></div>" +
          '<div><label>Salario mensual</label><input id="e-salario" type="number" min="0" step="100000" value="' +
            esc(p.salario_mensual || 0) + '"></div>' +
          '<div><label>Fecha de ingreso</label><input id="e-ingreso" type="date" value="' +
            esc(p.fecha_ingreso || "") + '"></div>' +
          '<div><label>Turno</label><select id="e-turno">' + opcionesTurno(p.turno_id) + "</select></div>" +
          '<div><label>Nueva contraseña</label><input id="e-clave" type="password" placeholder="dejar vacío = sin cambio"></div>' +
          '</div><div class="acciones fin" style="margin-top:14px">' +
            '<button class="btn-suave btn-chico" data-cancelar="1">Cancelar</button>' +
            '<button class="btn-chico" data-guardar="' + esc(p.id) + '">Guardar</button>' +
          "</div></td></tr>";
      }
      var baja = p.activo === false;
      return '<tr' + (baja ? ' class="futuro"' : "") + "><td><b>" + esc(p.full_name) + "</b>" +
        (baja ? ' <span class="sello t-ausente">Baja ' + esc(p.fecha_baja || "") + "</span>" : "") +
        '<br><span class="apunte">' + esc(p.username) + "</span></td>" +
        "<td>" + esc(p.role_name) + "</td>" +
        "<td>" + esc(p.tipo_vinculo || "—") + "</td>" +
        "<td>" + (p.turno_nombre ? esc(p.turno_nombre)
                                 : '<span class="apunte">Predeterminado</span>') +
          (p.turno_origen === "asignacion"
            ? '<br><span class="apunte">Hoy: ' + esc(p.turno_hoy) + "</span>"
            : "") + "</td>" +
        '<td class="num">' + esc(p.fecha_ingreso || "—") + "</td>" +
        '<td class="num">' + esc(guaranies(p.salario_mensual)) + "</td>" +
        '<td><div class="acciones fin">' +
          (baja
            ? '<button class="btn-suave btn-chico" data-reincorporar="' + esc(p.id) +
              '">Reincorporar</button>'
            : '<button class="btn-suave btn-chico" data-extras="' + esc(p.id) +
              '" data-nombre="' + esc(p.username) + '">Extras</button>' +
              '<button class="btn-suave btn-chico" data-editar="' + esc(p.id) + '">Editar</button>' +
              '<button class="btn-riesgo btn-chico" data-baja="' + esc(p.id) + '">Dar de baja</button>') +
        "</div></td></tr>";
    }).join("");

    $("g-personal").innerHTML =
      '<div class="bloque"><div class="rubro"><h3>Nuevo empleado</h3></div>' +
        '<div class="rejilla-campos">' +
          '<div><label>Usuario o cédula</label><input id="n-usuario"></div>' +
          '<div><label>Contraseña</label><input id="n-clave" type="password"></div>' +
          '<div><label>Nombre y apellido</label><input id="n-nombre"></div>' +
          "<div><label>Rol</label><select id=\"n-rol\">" + opciones(sesion.roles, "Empleado") + "</select></div>" +
          "<div><label>Vínculo</label><select id=\"n-vinculo\">" +
            opciones(["Funcionario", "Pasante"], "Funcionario") + "</select></div>" +
          '<div><label>Salario mensual</label><input id="n-salario" type="number" min="0" step="100000" value="0"></div>' +
          '<div><label>Fecha de ingreso</label><input id="n-ingreso" type="date" value="' + iso(new Date()) + '"></div>' +
          '<div><label>Turno</label><select id="n-turno">' + opcionesTurno(null) + "</select></div>" +
        "</div>" +
        '<div class="acciones fin" style="margin-top:16px"><button id="n-crear">Crear empleado</button></div>' +
        '<div id="n-aviso"></div></div>' +
      '<div class="bloque"><div class="rubro"><h3>Personal registrado</h3></div>' +
        '<div class="marco"><table><thead><tr><th>Nombre</th><th>Rol</th><th>Vínculo</th>' +
        '<th>Turno</th><th class="num">Ingreso</th><th class="num">Salario</th><th></th></tr></thead>' +
        "<tbody>" + filas + "</tbody></table></div></div>";
    $("n-crear").addEventListener("click", crearPersonal);
  }

  function crearPersonal() {
    var cuerpo = {
      username: $("n-usuario").value.trim(),
      password: $("n-clave").value,
      full_name: $("n-nombre").value.trim(),
      role_name: $("n-rol").value,
      tipo_vinculo: $("n-vinculo").value,
      salario_mensual: parseFloat($("n-salario").value) || 0,
      fecha_ingreso: $("n-ingreso").value || null,
      turno_id: $("n-turno").value === "" ? null : Number($("n-turno").value)
    };
    if (!cuerpo.username || !cuerpo.password || !cuerpo.full_name) {
      avisar("n-aviso", "Completá usuario, contraseña y nombre."); return;
    }
    pedir("/api/panel/personal", { cuerpo: cuerpo }).then(function () {
      notificar("Empleado creado", cuerpo.full_name, "baja");
      cargarPersonal();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("n-aviso", error.message);
    });
  }

  function guardarPersonal(id) {
    var cuerpo = {
      full_name: $("e-nombre").value.trim(),
      role_name: $("e-rol").value,
      tipo_vinculo: $("e-vinculo").value,
      salario_mensual: parseFloat($("e-salario").value) || 0,
      fecha_ingreso: $("e-ingreso").value || null,
      turno_id: $("e-turno").value === "" ? null : Number($("e-turno").value)
    };
    if ($("e-clave").value) cuerpo.password = $("e-clave").value;
    pedir("/api/panel/personal/" + id, { cuerpo: cuerpo, metodo: "PUT" }).then(function () {
      notificar("Cambios guardados", "", "baja");
      cargarPersonal();
    }).catch(function (error) {
      if (error.message !== "sesion") notificar("No se pudo guardar", error.message, "alta");
    });
  }

  /* Dar de baja conserva el legajo; eliminar lo destruye. Son dos acciones
     distintas y la que se ofrece en el panel es la primera. */
  function confirmarBaja(id) {
    var persona = sesion.personal.find(function (p) { return String(p.id) === String(id); });
    abrirModal(
      '<div class="modal-cabeza"><div><h2>Dar de baja a ' +
        esc(persona ? persona.full_name : "") + "</h2>" +
      '<p class="apunte">Pierde el acceso y sale de la nómina. Sus marcajes, ' +
      "permisos y comprobantes se conservan para el archivo laboral.</p></div></div>" +
      '<div class="acciones fin">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button class="btn-riesgo" id="b-confirmar">Dar de baja</button>' +
      "</div>"
    );
    $("b-confirmar").addEventListener("click", function () {
      cerrarModal();
      pedir("/api/panel/personal/" + id + "/baja", { cuerpo: {} })
        .then(function (d) { notificar("Baja registrada", d.mensaje, "media"); cargarPersonal(); })
        .catch(function (e) {
          if (e.message !== "sesion") notificar("No se pudo dar de baja", e.message, "alta");
        });
    });
  }

  function reincorporar(id) {
    pedir("/api/panel/personal/" + id + "/reincorporar", { cuerpo: {} })
      .then(function (d) { notificar("Reincorporado", d.mensaje, "baja"); cargarPersonal(); })
      .catch(function (e) {
        if (e.message !== "sesion") notificar("No se pudo reincorporar", e.message, "alta");
      });
  }

  function borrarPersonal(id) {
    var persona = sesion.personal.find(function (p) { return String(p.id) === String(id); });
    abrirModal(
      '<div class="modal-cabeza"><div><h2>Eliminar a ' + esc(persona ? persona.full_name : "") + "</h2>" +
      '<p class="apunte">Se borran también sus marcajes. La acción queda auditada y no se puede deshacer.</p></div></div>' +
      '<div class="acciones fin">' +
        '<button class="btn-suave" data-cerrar="1">Cancelar</button>' +
        '<button class="btn-riesgo" data-confirmar-borrado="' + esc(id) + '">Eliminar</button>' +
      "</div>"
    );
  }

  function cargarJustificaciones() {
    $("g-justificaciones").innerHTML = '<div class="vacio">Cargando…</div>';
    pedir("/api/panel/justificaciones").then(function (d) {
      var empleados = (d.personal || []).map(function (p) {
        return '<option value="' + esc(p.id) + '">' + esc(p.full_name) + "</option>";
      }).join("");
      var tipos = (d.tipos || []).map(function (t) {
        return "<option>" + esc(t) + "</option>";
      }).join("");
      var filas = (d.justificaciones || []).map(function (j) {
        return "<tr><td><b>" + esc(j.full_name) + "</b></td><td>" + esc(j.tipo_permiso) + "</td>" +
          "<td>" + esc(j.fecha_inicio) + " al " + esc(j.fecha_fin) + "</td>" +
          '<td class="num">' + esc(j.horas_usadas || 0) + "</td>" +
          '<td><button class="btn-suave btn-chico" data-pdf-panel="' + esc(j.id) + '">PDF</button></td></tr>';
      }).join("");

      $("g-justificaciones").innerHTML =
        '<div class="bloque"><div class="rubro"><h3>Emitir justificación</h3></div>' +
          '<div class="rejilla-campos">' +
            "<div><label>Empleado</label><select id=\"j-empleado\">" + empleados + "</select></div>" +
            "<div><label>Tipo de permiso</label><select id=\"j-tipo\">" + tipos + "</select></div>" +
            '<div><label>Desde</label><input id="j-desde" type="date" value="' + iso(new Date()) + '"></div>' +
            '<div><label>Hasta</label><input id="j-hasta" type="date" value="' + iso(new Date()) + '"></div>' +
            '<div><label>Horas (solo permisos por horas)</label><input id="j-horas" type="number" min="0" step="0.5" value="0"></div>' +
          "</div>" +
          '<div class="acciones fin" style="margin-top:16px"><button id="j-emitir">Emitir</button></div>' +
          '<div id="j-aviso"></div></div>' +
        '<div class="bloque"><div class="rubro"><h3>Emitidas</h3></div>' +
          (filas ? '<div class="marco"><table><thead><tr><th>Empleado</th><th>Tipo</th><th>Período</th>' +
            '<th class="num">Horas</th><th></th></tr></thead><tbody>' +
            filas + "</tbody></table></div>" : '<div class="vacio">Sin justificaciones emitidas.</div>') +
        "</div>";
      $("j-emitir").addEventListener("click", emitirJustificacion);
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-justificaciones", error.message);
    });
  }

  function emitirJustificacion() {
    var cuerpo = {
      empleado_id: parseInt($("j-empleado").value, 10),
      tipo_permiso: $("j-tipo").value,
      fecha_inicio: $("j-desde").value,
      fecha_fin: $("j-hasta").value,
      horas_usadas: parseFloat($("j-horas").value) || 0
    };
    pedir("/api/panel/justificaciones", { cuerpo: cuerpo }).then(function (d) {
      notificar("Justificación emitida", d.mensaje, "baja");
      cargarJustificaciones();
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("j-aviso", error.message);
    });
  }

  function cargarAlertas() {
    $("g-alertas").innerHTML = '<div class="vacio">Cargando…</div>';
    pedir("/api/alertas").then(function (d) {
      var filas = (d.alertas || []).map(function (a) {
        return '<div class="pendiente ' + (a.severidad === "alta" ? "urgente" : "media") + '">' +
          '<div class="pendiente-cuerpo">' +
          '<div class="pendiente-titulo">' + esc(a.mensaje) + "</div>" +
          '<div class="pendiente-detalle">' + esc(a.detalle || "") + "</div></div>" +
          '<span class="sello">' + esc(a.severidad) + "</span></div>";
      }).join("");
      $("g-alertas").innerHTML =
        '<div class="rubro"><h3>Alertas</h3>' +
          (d.no_leidas ? '<button class="btn-suave btn-chico" id="a-leidas">Marcar todas como leídas</button>' : "") +
        "</div>" +
        (filas || '<div class="vacio">Sin alertas registradas.</div>');
      if ($("a-leidas")) {
        $("a-leidas").addEventListener("click", function () {
          pedir("/api/alertas/leidas", { cuerpo: {} }).then(cargarAlertas);
        });
      }
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-alertas", error.message);
    });
  }

  function cargarAuditoria() {
    $("g-auditoria").innerHTML = '<div class="vacio">Cargando…</div>';
    pedir("/api/panel/auditoria").then(function (lista) {
      var filas = (lista || []).map(function (e) {
        var cuando = e.creado_en ? new Date(e.creado_en).toLocaleString("es-PY") : "";
        return "<tr><td>" + esc(cuando) + "</td><td>" + esc(e.full_name || e.username) + "</td>" +
          '<td><span class="sello">' + esc(e.accion) + "</span> " + esc(e.tabla) + "</td>" +
          '<td class="apunte" style="text-align:left">' +
          esc(JSON.stringify(e.valores_nuevos || {}).slice(0, 90)) + "</td></tr>";
      }).join("");
      $("g-auditoria").innerHTML = filas
        ? '<div class="marco"><table><thead><tr><th>Fecha</th><th>Actor</th>' +
          "<th>Acción</th><th>Cambio</th></tr></thead><tbody>" +
          filas + "</tbody></table></div>"
        : '<div class="vacio">Sin eventos registrados.</div>';
    }).catch(function (error) {
      if (error.message !== "sesion") avisar("g-auditoria", error.message);
    });
  }

  // ------------------------------------------------------------- alertas

  function notificar(titulo, detalle, severidad) {
    var caja = document.createElement("div");
    caja.className = "nota " + (severidad || "media");
    var t = document.createElement("b");
    t.textContent = titulo;
    caja.appendChild(t);
    if (detalle) {
      var d = document.createElement("span");
      d.textContent = detalle;
      caja.appendChild(d);
    }
    $("avisos").appendChild(caja);
    setTimeout(function () { caja.remove(); }, 8000);
  }

  function conectarAlertas() {
    if (!sesion.token) return;
    if (sesion.socket) { sesion.socket.onclose = null; sesion.socket.close(); }
    var protocolo = location.protocol === "https:" ? "wss://" : "ws://";
    sesion.socket = new WebSocket(
      protocolo + location.host + "/ws/alertas?token=" + encodeURIComponent(sesion.token)
    );
    sesion.socket.onmessage = function (evento) {
      var alerta;
      try { alerta = JSON.parse(evento.data); } catch (e) { return; }
      notificar(alerta.mensaje, alerta.detalle, alerta.severidad);
    };
    sesion.socket.onclose = function () {
      if (sesion.token) setTimeout(conectarAlertas, 5000);
    };
  }

  // ---------------------------------------------------------- delegación

  document.addEventListener("click", function (evento) {
    var objetivo = evento.target.closest("[data-vista],[data-panel],[data-fecha],[data-pdf]," +
      "[data-pdf-panel],[data-cerrar],[data-reclamar],[data-aprobar],[data-rechazar]," +
      "[data-editar],[data-guardar],[data-borrar],[data-cancelar],[data-confirmar-borrado]," +
      "[data-permiso-ok],[data-permiso-no],[data-revocar],[data-extras]," +
      "[data-baja],[data-reincorporar],[data-turno-editar],[data-turno-guardar]," +
      "[data-turno-retirar],[data-turno-predeterminado],[data-turno-asignar]," +
      "[data-turno-base],[data-turno-rotar],[data-rotacion-guardar]," +
      "[data-rotacion-revocar]");
    if (!objetivo) return;
    var d = objetivo.dataset;

    if (d.turnoEditar) { editarTurno(parseInt(d.turnoEditar, 10)); return; }
    if (d.turnoGuardar) { guardarTurno(d.turnoGuardar); return; }
    if (d.turnoRetirar) { retirarTurno(d.turnoRetirar); return; }
    if (d.turnoPredeterminado) { hacerPredeterminado(d.turnoPredeterminado); return; }
    if (d.turnoAsignar) { abrirAsignacionTurno(parseInt(d.turnoAsignar, 10)); return; }
    if (d.turnoBase) { guardarTurnoBase(d.turnoBase); return; }
    if (d.turnoRotar) { abrirRotacion(parseInt(d.turnoRotar, 10)); return; }
    if (d.rotacionGuardar) { guardarRotacion(d.rotacionGuardar); return; }
    if (d.rotacionRevocar) { revocarRotacion(d.rotacionRevocar); return; }
    if (d.baja) { confirmarBaja(d.baja); return; }
    if (d.reincorporar) { reincorporar(d.reincorporar); return; }
    if (d.permisoOk) { resolverPermiso(d.permisoOk, true); return; }
    if (d.permisoNo) { rechazarPermiso(d.permisoNo); return; }
    if (d.revocar) { revocarCondicion(d.revocar); return; }
    if (d.extras) { descargarExtrasDe(d.extras, objetivo.dataset.nombre || ""); return; }
    if (d.vista) { mostrar(d.vista); return; }
    if (d.panel) { abrirPestana(d.panel); return; }
    if (d.fecha) { abrirDia(d.fecha); return; }
    if (d.cerrar) { cerrarModal(); return; }
    if (d.reclamar) { cerrarModal(); abrirReclamo(d.reclamar); return; }
    if (d.pdf) { descargarPdf("/api/permiso/" + d.pdf + "/pdf", "permiso_" + d.pdf + ".pdf")
      .catch(function (e) { notificar("No se pudo descargar", e.message, "alta"); }); return; }
    if (d.pdfPanel) { descargarPdf("/api/panel/justificaciones/" + d.pdfPanel + "/pdf",
      "justificacion_" + d.pdfPanel + ".pdf")
      .catch(function (e) { notificar("No se pudo descargar", e.message, "alta"); }); return; }
    if (d.aprobar) { resolverCorreccion(d.aprobar, true); return; }
    if (d.rechazar) { resolverCorreccion(d.rechazar, false); return; }
    if (d.editar) { pintarPersonal(parseInt(d.editar, 10)); return; }
    if (d.cancelar) { pintarPersonal(null); return; }
    if (d.guardar) { guardarPersonal(d.guardar); return; }
    if (d.borrar) { borrarPersonal(d.borrar); return; }
    if (d.confirmarBorrado) {
      cerrarModal();
      pedir("/api/panel/personal/" + d.confirmarBorrado, { metodo: "DELETE" })
        .then(function () { notificar("Empleado eliminado", "", "baja"); cargarPersonal(); })
        .catch(function (e) {
          if (e.message !== "sesion") notificar("No se pudo eliminar", e.message, "alta");
        });
    }
  });

  // --------------------------------------------------------------- inicio

  function arrancar() {
    aplicarTema(temaInicial());
    $("btn-tema").addEventListener("click", function () {
      aplicarTema(document.documentElement.getAttribute("data-tema") === "oscuro" ? "claro" : "oscuro");
    });
    $("btn-salir").addEventListener("click", function () { cerrarSesion(); });
    $("ir-acceso").addEventListener("click", function () { mostrar("acceso"); });
    $("ir-kiosco").addEventListener("click", function () { mostrar("kiosco"); });
    $("form-acceso").addEventListener("submit", iniciarSesion);
    $("k-marcar").addEventListener("click", marcarEnKiosco);
    $("k-clave").addEventListener("keydown", function (e) {
      if (e.key === "Enter") marcarEnKiosco();
    });
    $("btn-reclamo-libre").addEventListener("click", function () { abrirReclamo(null); });
    $("btn-pedir-permiso").addEventListener("click", abrirPedidoPermiso);
    $("btn-planilla-extra").addEventListener("click", descargarPlanillaExtra);
    $("h-consultar").addEventListener("click", consultarHistorial);
    $("h-constancia").addEventListener("click", descargarConstancia);
    $("h-hoy").addEventListener("click", function () {
      $("h-desde").value = $("h-hasta").value = iso(new Date());
      consultarHistorial();
    });

    // Consultar el futuro no tiene sentido y el servidor lo rechaza: mejor
    // que el selector no lo ofrezca a que el error aparezca después.
    var hoy = new Date();
    $("h-hasta").value = $("h-hasta").max = iso(hoy);
    $("h-desde").max = iso(hoy);
    $("h-desde").value = iso(new Date(hoy.getFullYear(), hoy.getMonth(), 1));

    pintarReloj();
    setInterval(pintarReloj, 1000);
    cargarCondicionDia();
    setInterval(cargarCondicionDia, 300000);

    sesion.token = recuperar("marcacion_jwt");
    sesion.rol = recuperar("marcacion_rol");
    sesion.nombre = recuperar("marcacion_nombre");
    if (sesion.token) entrarALaApp();
    else mostrar("kiosco");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arrancar);
  } else {
    arrancar();
  }
})();
