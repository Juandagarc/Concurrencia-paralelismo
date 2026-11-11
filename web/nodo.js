// Página del mesero: este computador se vuelve un nodo del sistema distribuido.
// Un computador = un mesero = un Web Worker (ver nodo-trabajador.js).
const $ = (s) => document.querySelector(s);
const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/nodo`;
let actual = null;
let referencia = 15_000_000;

fetch('/api/info').then((r) => r.json()).then((i) => { referencia = i.referencia_js || referencia; }).catch(() => {});
const guardado = new URLSearchParams(location.search).get('nombre');
try { $('#nombre').value = guardado || localStorage.getItem('cafeteria-nombre') || ''; } catch { $('#nombre').value = guardado || ''; }

$('#formulario').addEventListener('submit', (e) => { e.preventDefault(); entrar(); });

const ms = (s) => `${s >= 0 ? '+' : ''}${(s * 1000).toFixed(s && Math.abs(s) < 0.01 ? 2 : 0)} ms`;

function entrar() {
  const nombre = $('#nombre').value.trim() || 'Invitado';
  try { localStorage.setItem('cafeteria-nombre', nombre); } catch { /* sin almacenamiento */ }
  if (actual?.vivo) apagar(actual, 'reemplazado');
  $('#nodos').innerHTML = '';
  $('#unirme').textContent = 'Volver a entrar';

  const tarjeta = document.createElement('article');
  tarjeta.className = 'nodo';
  tarjeta.innerHTML = `<header><h3></h3><span class="estado">arrancando</span></header>
    <p class="velocidad">midiendo tu CPU…</p>
    <div class="barra"><i></i></div>
    <dl><dt>pedido</dt><dd class="d-sol">—</dd>
      <dt>pedidos servidos</dt><dd class="d-at">0</dd>
      <dt>RTT con el coordinador</dt><dd class="d-rtt">—</dd>
      <dt>ajuste de tu reloj</dt><dd class="d-aj">—</dd></dl>
    <button class="peligro" type="button">Apagar sin avisar</button>`;
  tarjeta.querySelector('h3').textContent = nombre;
  $('#nodos').append(tarjeta);

  const w = { worker: new Worker('nodo-trabajador.js'), tarjeta, atendidas: 0, vivo: true };
  actual = w;
  const q = (s) => tarjeta.querySelector(s);
  const estado = (texto, clase = '') => {
    q('.estado').textContent = texto;
    tarjeta.classList.toggle('atendiendo', clase === 'atendiendo');
    tarjeta.classList.toggle('caido', clase === 'caido');
  };
  w.worker.onmessage = ({ data: d }) => {
    if (d.estado === 'calibrando') estado('midiendo la CPU');
    else if (d.estado === 'sincronizando') estado('sincronizando reloj');
    else if (d.estado === 'conectado') {
      q('h3').textContent = d.nombre;
      const veces = d.opsPorSeg / referencia;
      q('.velocidad').textContent = `Tu CPU: ${(d.opsPorSeg / 1e6).toFixed(0)} M op/s · ${veces.toFixed(2)}× un portátil promedio`
        + ` · un pedido de 1 s te toma ${(1 / veces).toFixed(2)} s`;
      q('.d-rtt').textContent = `${(d.rtt * 1000).toFixed(1)} ms`;
      q('.d-aj').textContent = ms(d.ajuste);
      q('.d-aj').title = 'Algoritmo de Cristian: lo que se suma a tu reloj para tener la hora del coordinador';
      estado('esperando pedidos');
    } else if (d.estado === 'atendiendo') {
      estado('atendiendo', 'atendiendo');
      q('.d-sol').textContent = `#${d.solicitud.id} ${d.solicitud.producto ?? ''}`;
      q('.barra i').style.width = '0%';
    } else if (d.estado === 'esperando_llave') {
      estado('esperando la llave (GIL)', 'atendiendo');
    } else if (d.estado === 'progreso') {
      estado('atendiendo', 'atendiendo');
      q('.barra i').style.width = `${Math.round(d.progreso * 100)}%`;
    } else if (d.estado === 'listo') {
      w.atendidas++;
      q('.d-at').textContent = w.atendidas;
      estado('esperando pedidos');
    } else if (d.estado === 'caido') {
      w.vivo = false;
      estado('el coordinador simuló tu caída', 'caido');
      q('.peligro').disabled = true;
    } else if (d.estado === 'rechazado') {
      estado(d.mensaje, 'caido');
    } else if (d.estado === 'desconectado' || d.estado === 'error') {
      if (w.vivo) estado('sin conexión', 'caido');
      w.vivo = false;
    }
  };
  q('.peligro').addEventListener('click', () => apagar(w, 'apagado sin avisar'));
  w.worker.postMessage({ orden: 'iniciar', url, nombre, hilo: 1, nucleos: navigator.hardwareConcurrency || 1 });
}

function apagar(w, texto) {
  if (!w.vivo) return;
  w.worker.terminate();            // como desenchufar el equipo: no se despide del coordinador
  w.vivo = false;
  w.tarjeta.querySelector('.estado').textContent = texto;
  w.tarjeta.classList.remove('atendiendo');
  w.tarjeta.classList.add('caido');
  w.tarjeta.querySelector('.peligro').disabled = true;
}

if (guardado) entrar();
