// Interfaz del laboratorio: controles, eventos en vivo (SSE), métricas y comparación.
import { Escena } from './escena.js';
import { Gantt } from './gantt.js';

const $ = (s) => document.querySelector(s);
const NOMBRE_MODO = { secuencial: 'Secuencial', concurrente: 'Concurrente', paralelo: 'Paralelo' };
const NOMBRE_ESC = { io: 'E/S', cpu: 'CPU', mixto: 'Mixto' };
const NOMBRE_ETAPA = { pedido: 'toma el pedido', preparar: 'prepara', caja_camino: 'va a la caja',
  caja_espera: 'pide el Lock de la caja', caja_cobro: 'obtiene el Lock y cobra', entrega: 'suelta el Lock y entrega' };
const ETQ_LOG = { ejecucion_inicio: 'ejecución', ejecucion_fin: 'total' };
const COLOR_MODO = { secuencial: 'var(--sec)', concurrente: 'var(--con)', paralelo: 'var(--par)' };

const CONCEPTO = {
  secuencial: '<b>Una solicitud después de otra.</b> Un solo mesero (hilo) atiende mesa por mesa: no toma el siguiente pedido hasta entregar el anterior. Si espera en la cocina, las cafeteras (núcleos) quedan quietas y las demás mesas hacen fila.',
  concurrente: '<b>Varias solicitudes avanzan intercalándose.</b> Varios meseros (hilos) del mismo proceso comparten la pizarra (memoria). Mientras uno espera en la cocina, otro trabaja. Pero hay una sola llave (GIL): solo quien la tiene prepara en una cafetera, así que con pedidos de CPU se turnan.',
  paralelo: '<b>Varias solicitudes se ejecutan al mismo tiempo.</b> Cada mesero es un proceso con su propia pizarra (memoria) y su propia llave (GIL), así que varios usan cafeteras (núcleos) a la vez. El costo: crear procesos tarda y se comunican por mensajes (IPC).',
};
const PISTA = {
  io: 'Cada pedido se espera en la cocina, como una consulta a una base de datos: el mesero no usa ninguna cafetera mientras espera.',
  cpu: 'Cada pedido se prepara en una cafetera sin parar: ocupa un núcleo entero y necesita la llave (GIL).',
  mixto: 'Los pedidos impares se esperan en la cocina (E/S) y los pares se preparan en la barra (CPU).',
};

const estado = {
  modo: 'secuencial', escenario: 'io', trabajadores: 4, duracion: 1, realista: true,
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
    if (!b || estado.ocupado) return;
    g.querySelectorAll('button').forEach((x) => x.classList.toggle('activo', x === b));
    estado[clave] = b.dataset.v;
    alCambiar?.();
  });
}
function marcar(id, valor) {
  $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('activo', x.dataset.v === valor));
}

function refrescarTextos() {
  $('#concepto').innerHTML = CONCEPTO[estado.modo];
  $('#pista-escenario').textContent = PISTA[estado.escenario];
  // El control nunca se bloquea por el modo: en secuencial muestra 1 y, si se
  // mueve, la página pasa a concurrente (ver el manejador de 'input').
  $('#v-trab').textContent = estado.modo === 'secuencial' ? '1 (secuencial)' : estado.trabajadores;
  $('#v-dur').textContent = `${estado.duracion.toFixed(1)} s`;
  $('#realista').checked = estado.realista;
  $('#pista-realista').innerHTML = estado.realista
    ? 'Cada solicitud pasa por <b>4 etapas reales</b> en el servidor: tomar el pedido (E/S), prepararlo (CPU o E/S), cobrar en la <b>única caja</b> (sección crítica con <code>Lock</code>) y entregarlo (E/S). Los meseros caminan sincronizados con esas etapas.'
    : 'Solo se mide la preparación: el modelo puro, ideal para comparar tiempos. Caminar es animación y los meseros se apuran para alcanzar a los eventos.';
}

function previsualizar() {
  refrescarTextos();
  if (!estado.ocupado) {
    escena.reconfigurar({ modo: estado.modo, trabajadores: estado.trabajadores,
      nucleos: estado.info?.nucleos ?? 4, gil: estado.info?.gil ?? true, realista: estado.realista });
  }
  pintarComparacion();
}

segmentos('#modo', 'modo', previsualizar);
segmentos('#escenario', 'escenario', previsualizar);
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
$('#duracion').addEventListener('input', (e) => { estado.duracion = +e.target.value; refrescarTextos(); pintarComparacion(); });
$('#ejecutar').addEventListener('click', () => ejecutar(estado.modo).catch(avisar));
$('#comparar').addEventListener('click', compararTodos);
$('#vista').addEventListener('click', () => escena.vistaInicial());
$('#exp-json').addEventListener('click', () => exportar('json'));
$('#exp-csv').addEventListener('click', () => exportar('csv'));

function bloquear(si) {
  estado.ocupado = si;
  ['#ejecutar', '#comparar', '#trabajadores', '#duracion', '#realista'].forEach((s) => { $(s).disabled = si; });
  document.querySelectorAll('.segmentos button').forEach((b) => { b.disabled = si; });
  if (!si) refrescarTextos();
}

async function ejecutar(modo) {
  const r = await fetch('/api/ejecutar', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modo, escenario: estado.escenario,
      trabajadores: estado.trabajadores, duracion: estado.duracion, realista: estado.realista }),
  });
  const datos = await r.json();
  if (!r.ok) throw new Error(datos.error || 'No se pudo ejecutar');
  bloquear(true);
  return new Promise((ok, fallo) => { estado.esperando = { ok, fallo }; });
}

async function compararTodos() {
  try {
    for (const modo of ['secuencial', 'concurrente', 'paralelo']) {
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
  switch (ev.tipo) {
    case 'ejecucion_inicio': {
      Object.assign(estado, { t0: performance.now(), corriendo: true, listas: 0,
        solicitudes: ev.solicitudes, eventos: [], carriles: new Map(), activas: new Set() });
      estado.modo = ev.modo; estado.escenario = ev.escenario; estado.realista = !!ev.realista;
      marcar('#modo', ev.modo); marcar('#escenario', ev.escenario);
      if (!estado.ocupado) bloquear(true);   // otra pestaña lanzó la ejecución
      escena.reconfigurar({ modo: ev.modo, trabajadores: ev.trabajadores, nucleos: ev.nucleos,
        pid: ev.pid_servidor, gil: estado.info?.gil ?? true, realista: !!ev.realista });
      escena.crearSolicitudes(ev.solicitudes);
      gantt.reiniciar(ev.solicitudes);
      $('#log').innerHTML = '';
      estado.nEventos = 0;
      const quien = ev.modo === 'secuencial' ? '1 mesero'
        : `${ev.trabajadores} meseros (${ev.modo === 'paralelo' ? 'procesos' : 'hilos'})`;
      $('#estado-escena').innerHTML = `Ejecutando · <b>${NOMBRE_MODO[ev.modo]}</b> · ${NOMBRE_ESC[ev.escenario]} · ${quien}${ev.realista ? ' · tiempo real' : ''}`;
      ['#m-resp', '#m-arranque'].forEach((s) => { $(s).textContent = '—'; });
      $('#exp-json').disabled = $('#exp-csv').disabled = true;
      break;
    }
    case 'inicio': {
      if (!estado.carriles.has(ev.trabajador)) estado.carriles.set(ev.trabajador, estado.carriles.size);
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
    detalle = `#${ev.id}${pct} · ${ev.trabajador} (pid ${ev.pid}, tid ${ev.tid})${nucleo}`;
  } else if (ev.tipo === 'etapa') {
    const dur = ev.dur ? ` · ${ev.dur.toFixed(2)} s` : '';
    detalle = `#${ev.id} ${NOMBRE_ETAPA[ev.etapa] ?? ev.etapa}${dur} · ${ev.trabajador}`;
    if (ev.etapa.startsWith('caja')) clase = 'caja';
  } else if (ev.tipo === 'llegada') {
    detalle = `#${ev.id} ${ev.usuario} · ${NOMBRE_ESC[ev.trabajo]}`;
    clase = 'sistema';
  } else if (ev.tipo === 'ejecucion_inicio') {
    detalle = `${NOMBRE_MODO[ev.modo]} · ${ev.trabajadores} trabajador(es) · servidor pid ${ev.pid_servidor}`;
    clase = 'sistema';
  } else if (ev.tipo === 'ejecucion_fin') {
    detalle = `tiempo total ${ev.t.toFixed(3)} s`;
    clase = 'sistema';
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
const clave = (r) => `${r.escenario}|${(+r.duracion).toFixed(1)}|${r.realista ? 'TR' : 'puro'}`;

function ultimoPor(escenario, duracion, modo, realista) {
  const buscada = clave({ escenario, duracion, realista });
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
  $('#comp-titulo').textContent = `${NOMBRE_ESC[esc]} · ${dur.toFixed(1)} s por solicitud${tr ? ' · tiempo real' : ''}`;
  const filas = ['secuencial', 'concurrente', 'paralelo'].map((m) => [m, ultimoPor(esc, dur, m, tr)]);
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
  const { secuencial: s, concurrente: c, paralelo: p } = r;
  if (!s || (!c && !p)) return 'Ejecuta los tres modelos con la misma carga para compararlos.';
  const x = (a) => (a ? `${(s.total / a.total).toFixed(1)}×` : '—');
  if (tr) {
    const cobros = 10 * (estado.info?.etapas?.cobro ?? 0.3);
    return `Tiempo real: hilos ${x(c)}, procesos ${x(p)}. Tomar pedidos y entregar se superpone entre meseros, pero la caja es una sola: los 10 cobros van en fila (≥ ${cobros.toFixed(1)} s) por más meseros que haya. Esa parte secuencial limita la aceleración (ley de Amdahl).`;
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
      <td>${NOMBRE_ESC[r.escenario]} ${(+r.duracion).toFixed(1)}s${r.realista ? ' <span class="tr" title="tiempo real">TR</span>' : ''}</td><td>${r.trabajadores}</td>
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
    $('#m-cola').textContent = n - estado.listas - estado.activas.size;
  }
  gantt.dibujar();
  requestAnimationFrame(tic);
}

// ----------------------------------------------------------------- arranque
async function iniciar() {
  const info = await (await fetch('/api/info')).json();
  estado.info = info;
  try {
    const guardado = localStorage.getItem('cafeteria-tiempo-real');
    if (guardado !== null) estado.realista = guardado === '1';
  } catch { /* sin almacenamiento: queda el valor por defecto */ }
  const nucleos = info.nucleos ?? 4;
  estado.trabajadores = Math.min(Math.max(2, nucleos), 8);
  const slider = $('#trabajadores');
  slider.max = 16;
  slider.value = estado.trabajadores;
  $('#equipo').insertAdjacentHTML('afterbegin', `
    <span class="chip"><b>${nucleos}</b> núcleos</span>
    <span class="chip">Python <b>${info.python}</b></span>
    <span class="chip" title="Global Interpreter Lock">GIL <b>${info.gil ? 'activo' : 'desactivado'}</b></span>
    <span class="chip" title="Linux permite leer en qué núcleo corre cada hilo">núcleo por hilo <b>${info.nucleo_detectable ? 'sí' : 'no'}</b></span>
    <span class="chip">uso de CPU <b>${info.uso_cpu_disponible ? 'real' : 'no disponible'}</b></span>`);
  estado.historial = await (await fetch('/api/historial')).json();
  pintarHistorial();
  previsualizar();
  conectar();
  if (info.ocupado) bloquear(true);
}

refrescarTextos();
iniciar().catch(avisar);
requestAnimationFrame(tic);
