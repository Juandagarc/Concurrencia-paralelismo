// Un nodo del sistema distribuido que corre dentro del navegador (Web Worker).
//
// Cada Worker es un nodo independiente: su propia conexión WebSocket, su propio
// reloj corregido y sus propios latidos. Si se apaga (terminate), el
// coordinador lo nota porque dejan de llegar los latidos y reasigna su trabajo.
//
// Protocolo con el coordinador (mensajes JSON):
//   → hora {c}            ← hora {c, s}       algoritmo de Cristian
//   → hola {nombre, …}    ← bienvenido {id, nombre}
//   → latido              (cada 0,25 s)
//   ← solicitud {…}       → evento {ev}       inicio, progreso, etapa, fin
//   → tomar               ← candado           Lock distribuido de la caja
//   → soltar
//   → llave               ← llave_ok          GIL distribuido (modo concurrente):
//   → soltar_llave                            hay que tenerlo para calcular
//   ← caer                                    simulacro: el nodo se apaga sin avisar
//
// El trabajo de CPU es una cantidad FIJA de iteraciones: un computador rápido
// lo termina antes y uno lento después. La eficiencia del mesero es la de su CPU.

const LATIDO_MS = 250;
const PASOS = 10;

let ws = null;
let ajuste = 0;        // segundos que hay que sumar al reloj local para tener la hora del coordinador
let rtt = 0;
let opsPorSeg = 0;
let esperandoCandado = null;
let esperandoLlave = null;
let caido = false;            // simulacro de caída: deja de latir y de trabajar
let pendienteHora = null;

const avisar = (estado, extra = {}) => postMessage({ estado, ...extra });
const dormir = (s) => new Promise((r) => setTimeout(r, s * 1000));
class Caido extends Error {}
const vivo = () => { if (caido) throw new Caido(); };
const enviar = (m) => { if (ws?.readyState === 1) ws.send(JSON.stringify(m)); };

function quemar(n) {            // el mismo cálculo que hace el servidor en Python
  let x = 0;
  for (let i = 0; i < n; i++) x += (i * i) % 7;
  return x;
}

function calibrar() {
  let mejor = 0;
  for (let k = 0; k < 3; k++) {
    const n = 2_000_000, t = performance.now();
    quemar(n);
    mejor = Math.max(mejor, n / ((performance.now() - t) / 1000));
  }
  return mejor;
}

/** Hace `n` iteraciones sin bloquear los latidos: trabaja en tramos de ~40 ms. */
async function calcular(n) {
  let falta = n;
  const tramo = Math.max(10_000, opsPorSeg * 0.04);
  while (falta > 0) {
    vivo();
    quemar(Math.min(tramo, falta));
    falta -= tramo;
    await dormir(0);              // deja pasar los latidos
  }
}

const pedir = (tipo, guardar) => new Promise((ok) => { guardar(ok); enviar({ tipo }); });

function pedirHora() {
  return new Promise((ok) => {
    const c = Date.now() / 1000;
    pendienteHora = (m) => {
      const t2 = Date.now() / 1000;
      ok({ rtt: t2 - c, ajuste: m.s + (t2 - c) / 2 - t2 });
    };
    enviar({ tipo: 'hora', c });
  });
}

async function atender(m) {
  const s = m.solicitud, etapas = m.etapas;
  const t = () => Date.now() / 1000 + ajuste - m.t0;          // hora del coordinador
  const evento = (tipo, extra = {}) => enviar({ tipo: 'evento', ev: { tipo, id: s.id, t: t(), ...extra } });
  avisar('atendiendo', { solicitud: s });
  evento('inicio');
  if (etapas) {
    evento('etapa', { etapa: 'pedido', dur: etapas.pedido });
    await dormir(etapas.pedido);
    evento('etapa', { etapa: 'preparar' });
  }
  const iteraciones = s.iteraciones || s.duracion * opsPorSeg;
  for (let paso = 1; paso <= PASOS; paso++) {
    vivo();
    if (s.trabajo === 'io') {
      await dormir(s.duracion / PASOS);                        // esperar no usa la CPU
    } else {
      // En concurrente solo calcula quien tiene la llave del sistema (GIL distribuido)
      const gil = m.modo === 'concurrente';
      if (gil) { avisar('esperando_llave', { solicitud: s }); await pedir('llave', (f) => { esperandoLlave = f; }); vivo(); }
      await calcular(iteraciones / PASOS);                     // calcular sí: depende de tu CPU
      evento('progreso', { progreso: paso / PASOS });
      if (gil) enviar({ tipo: 'soltar_llave' });
      avisar('progreso', { solicitud: s, progreso: paso / PASOS });
      continue;
    }
    evento('progreso', { progreso: paso / PASOS });
    avisar('progreso', { solicitud: s, progreso: paso / PASOS });
  }
  if (etapas) {
    evento('etapa', { etapa: 'caja_camino', dur: etapas.camino_caja });
    await dormir(etapas.camino_caja);
    evento('etapa', { etapa: 'caja_espera' });
    await pedir('tomar', (f) => { esperandoCandado = f; });
    vivo();
    evento('etapa', { etapa: 'caja_cobro', dur: etapas.cobro });
    await dormir(etapas.cobro);
    enviar({ tipo: 'soltar' });
    evento('etapa', { etapa: 'entrega', dur: etapas.entrega });
    await dormir(etapas.entrega);
  }
  vivo();
  evento('fin');
  avisar('listo', { solicitud: s });
}

onmessage = async ({ data }) => {
  if (data.orden !== 'iniciar') return;
  avisar('calibrando');
  opsPorSeg = calibrar();
  ws = new WebSocket(data.url);
  let latidos = null;
  const cola = [];               // las solicitudes se atienden de a una, en orden
  let ocupado = false;
  const siguiente = async () => {
    if (ocupado || !cola.length) return;
    ocupado = true;
    try { await atender(cola.shift()); } catch (e) { if (!(e instanceof Caido)) throw e; } finally { ocupado = false; siguiente(); }
  };

  ws.onopen = async () => {
    avisar('sincronizando');
    let mejor = null;
    for (let i = 0; i < 5; i++) {
      const r = await pedirHora();
      if (!mejor || r.rtt < mejor.rtt) mejor = r;
    }
    ({ ajuste, rtt } = mejor);
    enviar({ tipo: 'hola', nombre: data.nombre, hilo: data.hilo,
      nucleos: data.nucleos, rtt, ajuste });
    latidos = setInterval(() => enviar({ tipo: 'latido' }), LATIDO_MS);
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.tipo === 'hora') { pendienteHora?.(m); pendienteHora = null; }
    else if (m.tipo === 'bienvenido') avisar('conectado', { id: m.id, nombre: m.nombre, ajuste, rtt, opsPorSeg });
    if (caido) return;
    else if (m.tipo === 'solicitud') { cola.push(m); siguiente(); }
    else if (m.tipo === 'candado') { esperandoCandado?.(); esperandoCandado = null; }
    else if (m.tipo === 'llave_ok') { esperandoLlave?.(); esperandoLlave = null; }
    else if (m.tipo === 'caer') {
      // Simulacro pedido por el coordinador: el equipo "se desenchufa". No se
      // despide: deja de latir y de responder, y el coordinador debe notarlo solo.
      caido = true;
      clearInterval(latidos);
      cola.length = 0;
      avisar('caido');
      setTimeout(() => ws.close(), 1500);
    }
    else if (m.tipo === 'lleno') avisar('rechazado', { mensaje: m.mensaje });
  };
  ws.onclose = () => { clearInterval(latidos); if (!caido) avisar('desconectado'); };
  ws.onerror = () => avisar('error');
};
