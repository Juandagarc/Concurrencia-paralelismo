// Interfaz del laboratorio: controles, eventos en vivo (SSE), métricas y comparación.
import { Escena } from './escena.js';
import { Gantt } from './gantt.js';

const $ = (s) => document.querySelector(s);
const NOMBRE_MODO = { secuencial: 'Secuencial', concurrente: 'Concurrente', paralelo: 'Paralelo', distribuido: 'Distribuido' };
const MODOS = ['secuencial', 'concurrente', 'paralelo', 'distribuido'];
const NOMBRE_ESC = { io: 'E/S', cpu: 'CPU', mixto: 'Mixto' };
const NOMBRE_ETAPA = { pedido: 'toma el pedido', preparar: 'prepara', caja_camino: 'va a la caja',
  caja_espera: 'pide el Lock de la caja', caja_cobro: 'obtiene el Lock y cobra', entrega: 'suelta el Lock y entrega' };
const ETQ_LOG = { ejecucion_inicio: 'ejecución', ejecucion_fin: 'total', nodo_alta: 'nodo', asignada: 'mensaje',
  nodo_caido: 'sin latido', falla: 'falla', sin_meseros: 'esperando' };
const COLOR_MODO = { secuencial: 'var(--sec)', concurrente: 'var(--con)', paralelo: 'var(--par)', distribuido: 'var(--dis)' };
const ms = (s) => `${(s * 1000).toFixed(s < 0.01 ? 2 : 0)} ms`;

const CONCEPTO = {
  secuencial: '<b>Una solicitud después de otra.</b> Un solo mesero (hilo) atiende mesa por mesa: no toma el siguiente pedido hasta entregar el anterior. Si espera en la cocina, las cafeteras (núcleos) quedan quietas y las demás mesas hacen fila.',
  concurrente: '<b>Varias solicitudes avanzan intercalándose.</b> Varios meseros (hilos) del mismo proceso comparten la pizarra (memoria). Mientras uno espera en la cocina, otro trabaja. Pero hay una sola llave (GIL): solo quien la tiene prepara en una cafetera, así que con pedidos de CPU se turnan.',
  paralelo: '<b>Varias solicitudes se ejecutan al mismo tiempo.</b> Cada mesero es un proceso con su propia pizarra (memoria) y su propia llave (GIL), así que varios usan cafeteras (núcleos) a la vez. El costo: crear procesos tarda y se comunican por mensajes (IPC).',
  distribuido: '<b>Varios nodos independientes coordinados por la red.</b> Cada mesero es un <b>nodo</b>: un programa aparte en este servidor o el navegador de alguien que prestó su equipo, sin memoria compartida. El <b>coordinador</b> (rack) reparte las solicitudes (balanceo de carga), presta el candado de la caja (Lock distribuido) y da la hora (cada nodo tiene su propio reloj). Cada mensaje paga la latencia de la red, y si un nodo deja de enviar latidos su trabajo se reasigna.',
};
// Versión web (siempre distribuida): cada computador conectado es un mesero y el
// modelo decide cómo los coordina el coordinador.
const CONCEPTO_RED = {
  secuencial: '<b>Un mesero a la vez: anillo con testigo.</b> Todos los computadores conectados son meseros, pero solo atiende quien tiene el testigo (la llave dorada). Al entregar el pedido, el testigo pasa al siguiente mesero del anillo. El sistema es distribuido, pero el trabajo va de a uno: los demás miran.',
  concurrente: '<b>Todos atienden, pero se turnan la CPU.</b> Cada mesero toma su pedido y espera la cocina al mismo tiempo que los demás, pero para usar la cafetera necesita la única llave del sistema: un GIL distribuido que presta el coordinador. Los cálculos van de a uno y cada préstamo de la llave cuesta un viaje por la red.',
  paralelo: '<b>Todos atienden y calculan a la vez, cada uno con su CPU.</b> Cada computador tiene su propia llave: nadie espera a nadie para calcular. El coordinador le da el siguiente pedido al mesero que quede libre, así que un computador rápido sirve más pedidos que uno lento.',
};
const esRed = () => !!estado.info?.red;
const puedeControlar = () => !esRed() || estado.rol === 'coordinador';
const modosActivos = () => (esRed() ? MODOS.slice(0, 3) : MODOS);
const meseros = () => (estado.info?.coordinador?.remotos ?? []).slice(0, estado.info?.coordinador?.max_nodos ?? 12);

const PISTA = {
  io: 'Cada pedido se espera en la cocina, como una consulta a una base de datos: el mesero no usa ninguna cafetera mientras espera.',
  cpu: 'Cada pedido se prepara en una cafetera sin parar: ocupa un núcleo entero y necesita la llave (GIL).',
  mixto: 'Los pedidos impares se esperan en la cocina (E/S) y los pares se preparan en la barra (CPU).',
};

const estado = {
  modo: 'secuencial', escenario: 'io', trabajadores: 4, duracion: 1, realista: true,
  llegadas: 'escalonadas', llegaron: 0, latencia: 30, caida: false, rol: null, sesion: null, mesas: 10,
  info: null, ocupado: false, t0: 0, corriendo: false,
  carriles: new Map(), listas: 0, activas: new Set(), solicitudes: [],
  eventos: [], ultimaEjecucion: null, historial: [], esperando: null, nEventos: 0,
};

const escena = new Escena($('#escena'));
const gantt = new Gantt($('#gantt'));
// Acceso desde la consola del navegador (F12) para explorar el estado
window.laboratorio = { escena, gantt, estado };

// ------------------------------------------------------------- controles
function segmentos(id, clave, alCambiar) {
  const g = $(id);
  g.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || estado.ocupado || !puedeControlar()) return;
    g.querySelectorAll('button').forEach((x) => x.classList.toggle('activo', x === b));
    estado[clave] = b.dataset.v;
    alCambiar?.();
  });
}
function marcar(id, valor) {
  $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('activo', x.dataset.v === valor));
}

function refrescarTextos() {
  $('#concepto').innerHTML = (esRed() ? CONCEPTO_RED : CONCEPTO)[estado.modo];
  $('#pista-escenario').textContent = PISTA[estado.escenario];
  $('#pista-llegadas').textContent = estado.llegadas === 'escalonadas'
    ? 'Los clientes entran de a uno, con separaciones al azar (en promedio 0,6 s). El patrón es el mismo en cada prueba para comparar justo.'
    : 'Los 10 clientes llegan en el mismo instante: el peor caso para la fila.';
  // El control nunca se bloquea por el modo: en secuencial muestra 1 y, si se
  // mueve, la página pasa a concurrente (ver el manejador de 'input').
  $('#v-trab').textContent = estado.modo === 'secuencial' ? '1 (secuencial)' : estado.trabajadores;
  $('#v-dur').textContent = `${estado.duracion.toFixed(1)} s`;
  $('#v-mesas').textContent = estado.mesas;
  $('#realista').checked = estado.realista;
  $('#sec-red').hidden = !esRed() && estado.modo !== 'distribuido';
  $('#v-lat').textContent = `${estado.latencia} ms`;
  $('#caida').checked = estado.caida;
  const red = estado.info?.coordinador ?? {};
  const externos = red.remotos ?? [];
  const cupo = Math.max(0, (red.max_nodos ?? 12) - estado.trabajadores);
  if (esRed()) {
    const lista = meseros();
    $('#pista-red').innerHTML = (lista.length
      ? `<b>${lista.length} ${lista.length === 1 ? 'mesero conectado' : 'meseros conectados'}</b>: ${lista.map(escapar).join(', ')}. `
      : '<b>Todavía no hay meseros.</b> Pidan a sus compañeros que entren como mesero desde esta misma dirección. ')
      + (estado.caida ? 'Un mesero se apagará sin avisar a mitad de un pedido: el coordinador lo nota al faltar sus <b>latidos</b> (1 s) y reasigna el pedido.' : 'La latencia es la de la red real de cada computador.');
  } else $('#pista-red').innerHTML = `El control de latencia simula la red de los <b>${estado.trabajadores} nodos del servidor</b>; los equipos que se suman desde el navegador usan su red real. `
    + (estado.caida ? 'Un nodo del servidor se apagará sin avisar: el coordinador lo nota al faltar sus <b>latidos</b> (1 s) y reasigna su solicitud. ' : '')
    + (externos.length
      ? `Se suman <b>${Math.min(externos.length, cupo)}</b> de ${externos.length} nodo(s) externo(s) conectados.`
      : 'Abre <a href="/nodo.html" target="_blank" rel="noopener">/nodo.html</a> en otro equipo o celular para sumarlo como nodo.');
  $('#pista-realista').innerHTML = estado.realista
    ? 'Cada solicitud pasa por <b>4 etapas reales</b> en el servidor: tomar el pedido (E/S), prepararlo (CPU o E/S), cobrar en la <b>única caja</b> (sección crítica con <code>Lock</code>) y entregarlo (E/S). Los meseros caminan sincronizados con esas etapas.'
    : 'Solo se mide la preparación: el modelo puro, ideal para comparar tiempos. Caminar es animación y los meseros se apuran para alcanzar a los eventos.';
}

function previsualizar() {
  refrescarTextos();
  if (!estado.ocupado) {
    const red = esRed();
    escena.reconfigurar({ modo: estado.modo, trabajadores: red ? Math.max(1, meseros().length) : estado.trabajadores,
      nucleos: estado.info?.nucleos ?? 4, topologia: estado.info?.topologia,
      gil: estado.info?.gil ?? true, realista: estado.realista, latencia: red ? 0 : estado.latencia / 1000,
      red, remotos: red ? meseros() : [] });
  }
  escena.mesas(estado.mesas);
  pintarComparacion();
}

segmentos('#modo', 'modo', previsualizar);
segmentos('#escenario', 'escenario', previsualizar);
segmentos('#llegadas', 'llegadas', previsualizar);
$('#trabajadores').addEventListener('input', (e) => {
  estado.trabajadores = +e.target.value;
  if (estado.modo === 'secuencial') {      // secuencial siempre es 1: mover el control pasa a concurrente
    estado.modo = 'concurrente';
    marcar('#modo', 'concurrente');
  }
  previsualizar();
});
$('#realista').addEventListener('change', (e) => {
  estado.realista = e.target.checked;
  try { localStorage.setItem('cafeteria-tiempo-real', estado.realista ? '1' : '0'); } catch { /* sin almacenamiento */ }
  previsualizar();
});
$('#latencia').addEventListener('input', (e) => { estado.latencia = +e.target.value; refrescarTextos(); });
$('#caida').addEventListener('change', (e) => { estado.caida = e.target.checked; refrescarTextos(); });
$('#mesas').addEventListener('input', (e) => { estado.mesas = +e.target.value; previsualizar(); });
$('#duracion').addEventListener('input', (e) => { estado.duracion = +e.target.value; refrescarTextos(); pintarComparacion(); });
$('#ejecutar').addEventListener('click', () => ejecutar(estado.modo).catch(avisar));
$('#comparar').addEventListener('click', compararTodos);
$('#detener').addEventListener('click', async () => {
  $('#detener').disabled = true;
  estado.detenida = true;                  // que "Comparar" no siga con el siguiente modelo
  try {
    await fetch('/api/detener', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Sesion': estado.sesion ?? '' }, body: '{}' });
  } catch { $('#detener').disabled = false; }
});
$('#vista').addEventListener('click', () => escena.vistaInicial());
$('#exp-json').addEventListener('click', () => exportar('json'));
$('#exp-csv').addEventListener('click', () => exportar('csv'));

function bloquear(si) {
  estado.ocupado = si;
  const no = si || !puedeControlar();
  ['#ejecutar', '#comparar', '#trabajadores', '#duracion', '#mesas', '#realista', '#latencia', '#caida'].forEach((s) => { $(s).disabled = no; });
  document.querySelectorAll('.segmentos button').forEach((b) => { b.disabled = no; });
  $('#detener').hidden = !(si && puedeControlar());
  $('#detener').disabled = false;
  if (!si) refrescarTextos();
}

async function ejecutar(modo) {
  const r = await fetch('/api/ejecutar', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Sesion': estado.sesion ?? '' },
    body: JSON.stringify({ modo, escenario: estado.escenario,
      trabajadores: estado.trabajadores, duracion: estado.duracion, realista: estado.realista,
      llegadas: estado.llegadas, latencia: estado.latencia, caida: estado.caida, mesas: estado.mesas }),
  });
  const datos = await r.json();
  if (r.status === 401 && esRed()) {          // la sesión venció (p. ej. se reinició el servidor)
    estado.rol = null;
    try { sessionStorage.removeItem('cafeteria-sesion'); } catch { /* sin almacenamiento */ }
    mostrarEntrada();
  }
  if (!r.ok) throw new Error(datos.error || 'No se pudo ejecutar');
  bloquear(true);
  return new Promise((ok, fallo) => { estado.esperando = { ok, fallo }; });
}

async function compararTodos() {
  estado.detenida = false;
  try {
    for (const modo of modosActivos()) {
      if (estado.detenida) break;
      estado.modo = modo;
      marcar('#modo', modo);
      refrescarTextos();
      await ejecutar(modo);
      await new Promise((r) => setTimeout(r, 900));
    }
  } catch (e) { avisar(e); }
}

function avisar(e) {
  bloquear(false);
  $('#estado-escena').textContent = `⚠ ${e.message}`;
  registrar({ tipo: 'error', t: 0, mensaje: e.message });
}

// -------------------------------------------------------------- eventos
function conectar() {
  const fuente = new EventSource('/api/eventos');
  const chip = $('#conexion');
  fuente.onopen = () => { chip.className = 'chip ok'; chip.lastChild.textContent = 'en vivo'; };
  fuente.onerror = () => { chip.className = 'chip mal'; chip.lastChild.textContent = 'reconectando…'; };
  fuente.onmessage = (m) => manejar(JSON.parse(m.data));
}

function manejar(ev) {
  // Un nodo remoto informa los núcleos de SU computador, no de esta barra
  if (ev.remoto && ev.nucleo != null) ev = { ...ev, nucleo: null };
  switch (ev.tipo) {
    case 'ejecucion_inicio': {
      Object.assign(estado, { t0: performance.now(), corriendo: true, listas: 0, llegaron: 0,
        solicitudes: ev.solicitudes, eventos: [], carriles: new Map(), activas: new Set() });
      estado.modo = ev.modo; estado.escenario = ev.escenario; estado.realista = !!ev.realista;
      estado.llegadas = ev.llegadas ?? 'juntas';
      if (ev.mesas) { estado.mesas = ev.mesas; $('#mesas').value = ev.mesas; }
      marcar('#llegadas', estado.llegadas);
      marcar('#modo', ev.modo); marcar('#escenario', ev.escenario);
      if (!estado.ocupado) bloquear(true);   // otra pestaña lanzó la ejecución
      escena.reconfigurar({ modo: ev.modo, trabajadores: ev.trabajadores, nucleos: ev.nucleos,
        topologia: estado.info?.topologia,
        pid: ev.pid_servidor, gil: estado.info?.gil ?? true, realista: !!ev.realista,
        latencia: ev.latencia ?? 0, remotos: ev.remotos ?? [], red: !!ev.red });
      escena.crearSolicitudes(ev.solicitudes);
      gantt.reiniciar(ev.solicitudes);
      $('#log').innerHTML = '';
      estado.nEventos = 0;
      const quien = ev.red ? `${ev.trabajadores} ${ev.trabajadores === 1 ? 'mesero' : 'meseros'} (computadores)`
        : ev.modo === 'secuencial' ? '1 mesero'
        : ev.modo === 'distribuido' ? `${ev.trabajadores} nodos · latencia ${ms(ev.latencia ?? 0)}`
        : `${ev.trabajadores} meseros (${ev.modo === 'paralelo' ? 'procesos' : 'hilos'})`;
      $('#estado-escena').innerHTML = `Ejecutando · <b>${NOMBRE_MODO[ev.modo]}</b> · ${NOMBRE_ESC[ev.escenario]} · ${quien}${ev.realista ? ' · tiempo real' : ''}`;
      ['#m-resp', '#m-arranque'].forEach((s) => { $(s).textContent = '—'; });
      $('#exp-json').disabled = $('#exp-csv').disabled = true;
      break;
    }
    case 'llegada':
      estado.llegaron++;
      escena.llegada(ev); gantt.llegada(ev);
      break;
    case 'inicio': {
      if (!estado.carriles.has(ev.trabajador)) {
        // En distribuido cada nodo tiene su puesto fijo (el que le dio el coordinador)
        estado.carriles.set(ev.trabajador, ev.nodo_idx ?? estado.carriles.size);
      }
      const carril = estado.carriles.get(ev.trabajador);
      estado.activas.add(ev.id);
      escena.inicio(ev, carril);
      gantt.inicio(ev, carril);
      break;
    }
    case 'progreso': escena.progreso(ev); gantt.progreso(ev); break;
    case 'etapa': escena.etapa(ev); gantt.etapa(ev); break;
    case 'fin':
      estado.activas.delete(ev.id);
      estado.listas++;
      escena.fin(ev); gantt.fin(ev);
      break;
    case 'cpu': escena.cpu(ev.uso); break;
    case 'nodo_alta': escena.nodo(ev); break;
    case 'asignada': escena.asignada(ev); break;
    case 'nodo_caido': escena.caido(ev); break;
    case 'red':
      if (estado.info) estado.info.coordinador = ev;
      pintarRed();
      if (!estado.ocupado) refrescarTextos();
      return;                                // no es un evento de la ejecución
    case 'ejecucion_fin':
      estado.corriendo = false;
      gantt.ahora = ev.t;
      $('#m-reloj').textContent = `${ev.t.toFixed(2)} s`;
      $('#estado-escena').innerHTML = `Terminó en <b>${ev.t.toFixed(2)} s</b> · ${NOMBRE_MODO[estado.modo]} · ${NOMBRE_ESC[estado.escenario]}`;
      break;
    case 'resumen': alResumen(ev); break;
    case 'error':
      estado.esperando?.fallo(new Error(ev.mensaje));
      estado.esperando = null;
      break;
  }
  if (ev.tipo !== 'cpu' && ev.tipo !== 'resumen') {
    estado.eventos.push(ev);
    registrar(ev);
  }
}

function alResumen(r) {
  estado.ultimaEjecucion = { resumen: r, eventos: estado.eventos.slice() };
  estado.historial.push(r);
  $('#m-resp').textContent = `${r.respuesta_promedio.toFixed(2)} s`;
  $('#m-arranque').textContent = `${(r.arranque * 1000).toFixed(0)} ms`;
  $('#exp-json').disabled = $('#exp-csv').disabled = false;
  pintarHistorial(true);
  pintarComparacion(r);
  bloquear(false);
  estado.esperando?.ok(r);
  estado.esperando = null;
}

// ------------------------------------------------------------- registro
function registrar(ev) {
  const log = $('#log');
  const li = document.createElement('li');
  let detalle = '';
  let clase = ev.tipo;
  if (['inicio', 'progreso', 'fin'].includes(ev.tipo)) {
    const nucleo = ev.nucleo != null ? ` · núcleo ${ev.nucleo}` : '';
    const pct = ev.tipo === 'progreso' ? ` ${Math.round(ev.progreso * 100)}%` : '';
    const donde = ev.navegador ? 'navegador' : `pid ${ev.pid}, tid ${ev.tid}`;
    detalle = `#${ev.id}${pct} · ${ev.trabajador} (${donde})${nucleo}`;
  } else if (ev.tipo === 'etapa') {
    const dur = ev.dur ? ` · ${ev.dur.toFixed(2)} s` : '';
    detalle = `#${ev.id} ${NOMBRE_ETAPA[ev.etapa] ?? ev.etapa}${dur} · ${ev.trabajador}`;
    if (ev.etapa.startsWith('caja')) clase = 'caja';
  } else if (ev.tipo === 'llegada') {
    const pedido = estado.solicitudes.find((s) => s.id === ev.id)?.producto;
    detalle = `#${ev.id} ${ev.usuario} llega${pedido ? ` y pide ${pedido}` : ''} · ${NOMBRE_ESC[ev.trabajo]}`;
    clase = 'sistema';
  } else if (ev.tipo === 'ejecucion_inicio') {
    detalle = `${NOMBRE_MODO[ev.modo]} · ${ev.trabajadores} trabajador(es) · servidor pid ${ev.pid_servidor}`;
    clase = 'sistema';
  } else if (ev.tipo === 'ejecucion_fin') {
    detalle = `tiempo total ${ev.t.toFixed(3)} s`;
    clase = 'sistema';
  } else if (ev.tipo === 'nodo_alta') {
    const reloj = ev.desfase ? ` · su reloj iba ${ev.desfase > 0 ? '+' : ''}${ms(ev.desfase)}, Cristian ajusta ${ev.ajuste > 0 ? '+' : ''}${ms(ev.ajuste)}` : '';
    const donde = ev.pid?.startsWith?.('web-') ? `navegador de ${ev.host}` : `pid ${ev.pid}, ${ev.host}`;
    const corr = !ev.desfase && Math.abs(ev.ajuste) > 0.001 ? ` · Cristian ajusta su reloj ${ev.ajuste > 0 ? '+' : ''}${ms(ev.ajuste)}` : reloj;
    detalle = ev.vuelve
      ? `${ev.nodo} vuelve a entrar y recupera su puesto en la ejecución · RTT ${ms(ev.rtt)}`
      : `${ev.nodo} se registra (${donde}) · RTT ${ms(ev.rtt)}${corr}`;
    if (ev.vuelve) clase = 'red';
    clase = 'red';
  } else if (ev.tipo === 'asignada') {
    detalle = `coordinador → ${ev.nodo}: solicitud #${ev.id}${ev.reintento ? ' (REASIGNADA)' : ''} · latencia ${ms(ev.latencia)}`;
    clase = ev.reintento ? 'falla' : 'red';
  } else if (ev.tipo === 'falla') {
    detalle = `${ev.nodo} se apaga sin avisar con la solicitud #${ev.id} a medias`;
    clase = 'falla';
  } else if (ev.tipo === 'nodo_caido') {
    detalle = ev.motivo === 'reinicio'
      ? `${ev.nodo} volvió a conectarse: su instancia anterior se da por caída${ev.id ? ` y #${ev.id} vuelve a la fila` : ''}`
      : `${ev.nodo} lleva ${ev.silencio.toFixed(2)} s sin latido: el coordinador lo da por caído${ev.id ? ` y reasigna #${ev.id}` : ''}`;
    clase = 'falla';
  } else if (ev.tipo === 'sin_meseros') {
    detalle = `no queda ningún mesero vivo: el coordinador espera ${ev.espera} s a que alguno vuelva o entre`;
    clase = 'falla';
  } else if (ev.tipo === 'error') {
    detalle = ev.mensaje;
  }
  li.innerHTML = `<span class="t">${(ev.t ?? 0).toFixed(3)}</span><span class="tipo ${clase}">${ETQ_LOG[ev.tipo] ?? ev.tipo}</span>`;
  li.append(detalle);
  log.prepend(li);
  while (log.children.length > 300) log.lastChild.remove();
  $('#n-eventos').textContent = ++estado.nEventos;
}

// ----------------------------------------------------- comparación e historial
// Solo se comparan ejecuciones con la misma carga, duración y modo (tiempo real o no)
const clave = (r) => `${r.escenario}|${(+r.duracion).toFixed(1)}|${r.realista ? 'TR' : 'puro'}|${r.llegadas ?? 'juntas'}|${r.mesas ?? 10}`;

function ultimoPor(escenario, duracion, modo, realista, llegadas, mesas) {
  const buscada = clave({ escenario, duracion, realista, llegadas, mesas });
  for (let i = estado.historial.length - 1; i >= 0; i--) {
    const r = estado.historial[i];
    if (r.modo === modo && clave(r) === buscada) return r;
  }
  return null;
}

function aceleracion(r) {
  const base = ultimoPorAntesDe(r);
  return base ? base.total / r.total : null;
}
function ultimoPorAntesDe(r) {
  const idx = estado.historial.indexOf(r);
  for (let i = estado.historial.length - 1; i >= 0; i--) {
    const b = estado.historial[i];
    if (b.modo === 'secuencial' && clave(b) === clave(r) && (i <= idx || idx < 0)) return b;
  }
  for (const b of estado.historial) if (b.modo === 'secuencial' && clave(b) === clave(r)) return b;
  return null;
}

function pintarComparacion(ref) {
  const esc = ref?.escenario ?? estado.escenario;
  const dur = ref ? +ref.duracion : estado.duracion;
  const tr = ref ? !!ref.realista : estado.realista;
  const lleg = ref ? (ref.llegadas ?? 'juntas') : estado.llegadas;
  $('#comp-titulo').textContent = `${NOMBRE_ESC[esc]} · ${dur.toFixed(1)} s · ${ref ? (ref.mesas ?? 10) : estado.mesas} mesas · llegadas ${lleg}${tr ? ' · tiempo real' : ''}`;
  const mes = ref ? (ref.mesas ?? 10) : estado.mesas;
  const filas = modosActivos().map((m) => [m, ultimoPor(esc, dur, m, tr, lleg, mes)]);
  const max = Math.max(0.001, ...filas.map(([, r]) => r?.total ?? 0));
  $('#barras').innerHTML = filas.map(([m, r]) => {
    const ancho = r ? Math.max(4, (r.total / max) * 100) : 0;
    const texto = r ? `${r.total.toFixed(2)} s` : 'sin medir';
    return `<div class="barra-m"><span class="etq-modo"><i style="background:${COLOR_MODO[m]}"></i>${NOMBRE_MODO[m]}</span>
      <div class="pista-b"><div class="relleno" style="width:${ancho}%;background:${COLOR_MODO[m]}"></div>
      <span class="valor ${ancho < 30 ? 'fuera' : ''}" style="${ancho < 30 ? `left:calc(${ancho}% + 8px)` : ''}">${texto}</span></div></div>`;
  }).join('');
  $('#conclusion').textContent = conclusion(esc, Object.fromEntries(filas), tr);
}

function conclusion(esc, r, tr) {
  const { secuencial: s, concurrente: c, paralelo: p, distribuido: d } = r;
  if (!s || (!c && !p && !d)) return 'Ejecuta los modelos con la misma carga para compararlos.';
  const x = (a) => (a ? `${(s.total / a.total).toFixed(1)}×` : '—');
  const extra = d ? ` Distribuido ${x(d)}: los nodos trabajan a la vez como los procesos, pero cada mensaje paga la red y el coordinador es un punto central; a cambio se puede crecer sumando computadores.` : '';
  return conclusionLocal(esc, x, c, p, tr) + extra;
}

function conclusionLocal(esc, x, c, p, tr) {
  if (tr) {
    const nm = estado.solicitudes.length || estado.mesas;
    const cobros = nm * (estado.info?.etapas?.cobro ?? 0.3);
    return `Tiempo real: hilos ${x(c)}, procesos ${x(p)}. Tomar pedidos y entregar se superpone entre meseros, pero la caja es una sola: los ${nm} cobros van en fila (≥ ${cobros.toFixed(1)} s) por más meseros que haya. Esa parte secuencial limita la aceleración (ley de Amdahl).`;
  }
  if (esc === 'io') return `Con E/S los hilos aceleran ${x(c)} y los procesos ${x(p)}: mientras una solicitud espera, otra avanza. Los hilos lo logran sin el costo de crear procesos.`;
  if (esc === 'cpu') return `Con cálculo los hilos aceleran ${x(c)} (el GIL los turna) y los procesos ${x(p)}: solo el paralelismo real usa varios núcleos a la vez.`;
  return `Carga mixta: hilos ${x(c)}, procesos ${x(p)}. Los hilos aprovechan las esperas; los procesos, además, reparten el cálculo entre núcleos.`;
}

function pintarHistorial(nuevo = false) {
  const tb = $('#historial');
  if (!estado.historial.length) return;
  tb.innerHTML = estado.historial.slice().reverse().map((r, i) => {
    const a = aceleracion(r);
    return `<tr class="${nuevo && i === 0 ? 'nuevo' : ''}">
      <td><span class="etq-modo"><i style="background:${COLOR_MODO[r.modo]}"></i>${NOMBRE_MODO[r.modo]}</span></td>
      <td>${NOMBRE_ESC[r.escenario]} ${(+r.duracion).toFixed(1)}s${r.llegadas === 'escalonadas' ? ' <span class="tr" title="llegadas escalonadas">ESC</span>' : ''}${r.realista ? ' <span class="tr" title="tiempo real">TR</span>' : ''}</td><td>${r.trabajadores}</td>
      <td>${r.total.toFixed(2)}</td><td>${a ? a.toFixed(1) : '—'}</td></tr>`;
  }).join('');
}

// --------------------------------------------------------------- exportar
function exportar(formato) {
  const u = estado.ultimaEjecucion;
  if (!u) return;
  const base = `${u.resumen.modo}_${u.resumen.escenario}${u.resumen.realista ? '_tiempo-real' : ''}_${u.resumen.fecha.replace(/[:T]/g, '-')}`;
  let contenido, tipo;
  if (formato === 'json') {
    contenido = JSON.stringify(u, null, 1);
    tipo = 'application/json';
  } else {
    const cols = ['tipo', 'id', 't', 'trabajador', 'pid', 'tid', 'nucleo', 'progreso', 'etapa', 'dur'];
    const filas = u.eventos.filter((e) => ['llegada', 'inicio', 'progreso', 'etapa', 'fin'].includes(e.tipo))
      .map((e) => cols.map((c) => e[c] ?? '').join(','));
    contenido = [cols.join(','), ...filas].join('\n');
    tipo = 'text/csv';
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([contenido], { type: tipo }));
  a.download = `eventos_${base}.${formato}`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------ bucle de UI
function tic() {
  if (estado.corriendo) {
    const t = (performance.now() - estado.t0) / 1000;
    gantt.tiempo(t);
    $('#m-reloj').textContent = `${t.toFixed(2)} s`;
  }
  const n = estado.solicitudes.length || 10;
  $('#m-listas').textContent = `${estado.listas} / ${n}`;
  if (estado.solicitudes.length) {
    $('#m-activas').textContent = estado.activas.size;
    $('#m-cola').textContent = Math.max(0, estado.llegaron - estado.listas - estado.activas.size);
  }
  gantt.dibujar();
  requestAnimationFrame(tic);
}

function pintarRed() {
  const chip = $('#chip-red');
  if (!chip) return;
  const n = estado.info?.coordinador?.remotos?.length ?? 0;
  chip.innerHTML = esRed()
    ? `<b>${n}</b> ${n === 1 ? 'mesero conectado' : 'meseros conectados'} · Entrar como mesero`
    : `<b>${n}</b> ${n === 1 ? 'nodo externo' : 'nodos externos'} · Sumar mi equipo`;
  if (esRed() && !estado.ocupado) previsualizar();
}

function escapar(texto) {
  const d = document.createElement('span');
  d.textContent = texto;
  return d.innerHTML;
}

// ------------------------------------------------- roles (versión web)
function mostrarEntrada() {
  $('#entrada').hidden = false;
  $('#nombre-mesero').focus();
}

function aplicarRol() {
  const red = esRed();
  $('#modo [data-v=distribuido]').hidden = red;          // en la web todo es distribuido
  $('#modo').classList.toggle('cuatro', !red);
  $('#trabajadores').closest('label').hidden = red;      // los meseros son los computadores
  $('.ajustes .pista').hidden = red;
  $('#latencia').closest('label').hidden = red;          // la latencia es la real
  $('#comparar').textContent = `Comparar los ${modosActivos().length} modelos`;
  if (red && estado.modo === 'distribuido') { estado.modo = 'paralelo'; marcar('#modo', 'paralelo'); }
  const rol = $('#chip-rol');
  if (red) {
    rol.hidden = false;
    rol.innerHTML = estado.rol === 'coordinador' ? '<b>coordinador</b>' : 'espectador · <b>entrar</b>';
  }
  let aviso = $('#aviso-espectador');
  if (red && !puedeControlar()) {
    if (!aviso) {
      aviso = document.createElement('p');
      aviso.id = 'aviso-espectador';
      aviso.className = 'aviso-espectador';
      aviso.innerHTML = 'Estás mirando. Solo el coordinador lanza las ejecuciones. ¿Quieres participar? <a href="/nodo.html" target="_blank" rel="noopener">Entra como mesero</a>.';
      $('.acciones').prepend(aviso);
    }
  } else aviso?.remove();
  ['#ejecutar', '#comparar'].forEach((s) => { $(s).hidden = red && !puedeControlar(); });
  bloquear(estado.ocupado);
  previsualizar();
}

$('#form-mesero').addEventListener('submit', (e) => {
  e.preventDefault();
  const nombre = $('#nombre-mesero').value.trim();
  if (nombre) location.href = `/nodo.html?nombre=${encodeURIComponent(nombre)}`;
});
$('#form-coordinador').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#entrada-error').textContent = '';
  const r = await fetch('/api/entrar', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clave: $('#clave-coordinador').value }) });
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) { $('#entrada-error').textContent = datos.error || 'No se pudo entrar'; return; }
  estado.rol = 'coordinador';
  estado.sesion = datos.sesion;
  try { sessionStorage.setItem('cafeteria-sesion', datos.sesion); } catch { /* sin almacenamiento */ }
  $('#clave-coordinador').value = '';
  $('#entrada').hidden = true;
  aplicarRol();
});
$('#solo-mirar').addEventListener('click', () => {
  estado.rol = 'espectador';
  $('#entrada').hidden = true;
  aplicarRol();
});

// ----------------------------------------------------------------- arranque
async function iniciar() {
  const info = await (await fetch('/api/info')).json();
  estado.info = info;
  try {
    const guardado = localStorage.getItem('cafeteria-tiempo-real');
    if (guardado !== null) estado.realista = guardado === '1';
  } catch { /* sin almacenamiento: queda el valor por defecto */ }
  const nucleos = info.nucleos ?? 4;
  estado.trabajadores = 4;                // una cafetería real tiene pocos meseros
  const slider = $('#trabajadores');
  slider.max = 8;
  slider.value = estado.trabajadores;
  $('#equipo').insertAdjacentHTML('afterbegin', `
    <span class="chip" title="Cafeteras = núcleos físicos · boquillas = núcleos lógicos${info.topologia_exacta ? '' : ' (agrupación aproximada)'}"><b>${info.topologia?.length ?? nucleos}</b> físicos · <b>${nucleos}</b> lógicos</span>
    <span class="chip">Python <b>${info.python}</b></span>
    <span class="chip" title="Global Interpreter Lock">GIL <b>${info.gil ? 'activo' : 'desactivado'}</b></span>
    <span class="chip" title="Linux permite leer en qué núcleo corre cada hilo">núcleo por hilo <b>${info.nucleo_detectable ? 'sí' : 'no'}</b></span>
    <span class="chip">uso de CPU <b>${info.uso_cpu_disponible ? 'real' : 'no disponible'}</b></span>
    <a class="chip enlace" id="chip-red" href="/nodo.html" target="_blank" rel="noopener" title="Abre esta página en otro equipo o celular para prestarlo como nodo"></a>
    <button class="chip enlace" id="chip-rol" type="button" hidden></button>`);
  $('#chip-rol').addEventListener('click', () => { if (estado.rol !== 'coordinador') mostrarEntrada(); });
  if (info.red) {
    const pedido = new URLSearchParams(location.search).get('rol');
    let sesion = null;
    try { sesion = sessionStorage.getItem('cafeteria-sesion'); } catch { /* sin almacenamiento */ }
    if (sesion) { estado.rol = 'coordinador'; estado.sesion = sesion; }
    else if (pedido === 'espectador') estado.rol = 'espectador';
    else { estado.rol = 'espectador'; mostrarEntrada(); }
  }
  aplicarRol();
  pintarRed();
  estado.historial = await (await fetch('/api/historial')).json();
  pintarHistorial();
  previsualizar();
  conectar();
  if (info.ocupado) bloquear(true);
}

refrescarTextos();
iniciar().catch(avisar);
requestAnimationFrame(tic);
