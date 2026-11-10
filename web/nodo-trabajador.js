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

const LATIDO_MS = 250;
const PASOS = 10;

let ws = null;
let ajuste = 0;        // segundos que hay que sumar al reloj local para tener la hora del coordinador
let rtt = 0;
let opsPorSeg = 0;
let esperandoCandado = null;
let pendienteHora = null;

const avisar = (estado, extra = {}) => postMessage({ estado, ...extra });
const dormir = (s) => new Promise((r) => setTimeout(r, s * 1000));
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

/** Calcula durante `seg` segundos sin bloquear los latidos: trabaja en tramos de 40 ms. */
async function calcular(seg) {
  let falta = seg * opsPorSeg;
  const tramo = opsPorSeg * 0.04;
  while (falta > 0) {
    quemar(Math.min(tramo, falta));
    falta -= tramo;
    await dormir(0);              // deja pasar los latidos
  }
}

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
  for (let paso = 1; paso <= PASOS; paso++) {
    if (s.trabajo === 'io') await dormir(s.duracion / PASOS);   // esperar no usa la CPU
    else await calcular(s.duracion / PASOS);                   // calcular sí
    evento('progreso', { progreso: paso / PASOS });
    avisar('progreso', { solicitud: s, progreso: paso / PASOS });
  }
  if (etapas) {
    evento('etapa', { etapa: 'caja_camino', dur: etapas.camino_caja });
    await dormir(etapas.camino_caja);
    evento('etapa', { etapa: 'caja_espera' });
    await new Promise((ok) => { esperandoCandado = ok; enviar({ tipo: 'tomar' }); });
    evento('etapa', { etapa: 'caja_cobro', dur: etapas.cobro });
    await dormir(etapas.cobro);
    enviar({ tipo: 'soltar' });
    evento('etapa', { etapa: 'entrega', dur: etapas.entrega });
    await dormir(etapas.entrega);
  }
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
    try { await atender(cola.shift()); } finally { ocupado = false; siguiente(); }
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
    else if (m.tipo === 'solicitud') { cola.push(m); siguiente(); }
    else if (m.tipo === 'candado') { esperandoCandado?.(); esperandoCandado = null; }
    else if (m.tipo === 'lleno') avisar('rechazado', { mensaje: m.mensaje });
  };
  ws.onclose = () => { clearInterval(latidos); avisar('desconectado'); };
  ws.onerror = () => avisar('error');
};
