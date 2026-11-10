// Página para sumar este equipo como nodos del sistema distribuido.
// Cada trabajador es un Web Worker independiente (ver nodo-trabajador.js).
const $ = (s) => document.querySelector(s);
const nucleos = navigator.hardwareConcurrency || 2;
const maximo = Math.max(1, Math.min(8, nucleos));
const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/nodo`;
const trabajadores = [];

$('#cuantos').max = maximo;
$('#cuantos').value = Math.min(2, maximo);
$('#v-cuantos').textContent = $('#cuantos').value;
$('#r-nucleos').textContent = nucleos;
try { $('#nombre').value = localStorage.getItem('cafeteria-nombre') || ''; } catch { /* sin almacenamiento */ }

$('#cuantos').addEventListener('input', (e) => { $('#v-cuantos').textContent = e.target.value; });
$('#formulario').addEventListener('submit', (e) => { e.preventDefault(); unirme(); });
$('#salir').addEventListener('click', salir);

const ms = (s) => `${s >= 0 ? '+' : ''}${(s * 1000).toFixed(s && Math.abs(s) < 0.01 ? 2 : 0)} ms`;

function unirme() {
  const nombre = $('#nombre').value.trim() || 'Invitado';
  try { localStorage.setItem('cafeteria-nombre', nombre); } catch { /* sin almacenamiento */ }
  salir();
  $('#nodos').innerHTML = '';
  for (let i = 1; i <= +$('#cuantos').value; i++) crear(nombre, i);
  $('#resumen').hidden = false;
  $('#unirme').textContent = 'Volver a unirme';
}

function crear(nombre, hilo) {
  const tarjeta = document.createElement('article');
  tarjeta.className = 'nodo';
  tarjeta.innerHTML = `<header><h3></h3><span class="estado">arrancando</span></header>
    <div class="barra"><i></i></div>
    <dl><dt>solicitud</dt><dd class="d-sol">—</dd>
      <dt>atendidas</dt><dd class="d-at">0</dd>
      <dt>RTT</dt><dd class="d-rtt">—</dd>
      <dt>ajuste de reloj</dt><dd class="d-aj">—</dd></dl>
    <button class="peligro" type="button">Apagar sin avisar</button>`;
  tarjeta.querySelector('h3').textContent = `${nombre}·${hilo}`;
  $('#nodos').append(tarjeta);

  const w = { worker: new Worker('nodo-trabajador.js'), tarjeta, atendidas: 0, vivo: true };
  trabajadores.push(w);
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
      q('.d-rtt').textContent = `${(d.rtt * 1000).toFixed(1)} ms`;
      q('.d-aj').textContent = ms(d.ajuste);
      q('.d-aj').title = 'Algoritmo de Cristian: lo que se suma a tu reloj para tener la hora del coordinador';
      estado('esperando trabajo');
    } else if (d.estado === 'atendiendo') {
      estado('atendiendo', 'atendiendo');
      q('.d-sol').textContent = `#${d.solicitud.id} ${d.solicitud.producto ?? ''}`;
      q('.barra i').style.width = '0%';
    } else if (d.estado === 'progreso') {
      q('.barra i').style.width = `${Math.round(d.progreso * 100)}%`;
    } else if (d.estado === 'listo') {
      w.atendidas++;
      q('.d-at').textContent = w.atendidas;
      estado('esperando trabajo');
      resumen();
    } else if (d.estado === 'rechazado') {
      estado(d.mensaje, 'caido');
    } else if (d.estado === 'desconectado' || d.estado === 'error') {
      if (w.vivo) estado('sin conexión', 'caido');
      w.vivo = false;
      resumen();
    }
  };
  q('.peligro').addEventListener('click', () => apagar(w, 'apagado sin avisar'));
  w.worker.postMessage({ orden: 'iniciar', url, nombre, hilo, nucleos });
  resumen();
}

function apagar(w, texto) {
  if (!w.vivo) return;
  w.worker.terminate();            // como desenchufar el equipo: no se despide del coordinador
  w.vivo = false;
  w.tarjeta.querySelector('.estado').textContent = texto;
  w.tarjeta.classList.remove('atendiendo');
  w.tarjeta.classList.add('caido');
  w.tarjeta.querySelector('.peligro').disabled = true;
  resumen();
}

function salir() {
  trabajadores.forEach((w) => apagar(w, 'desconectado'));
}

function resumen() {
  $('#r-vivos').textContent = trabajadores.filter((w) => w.vivo).length;
  $('#r-atendidas').textContent = trabajadores.reduce((a, w) => a + w.atendidas, 0);
}
