// Escena 3D: el servidor contado como una cafetería.
//
//   Mesa con cliente   = una solicitud de un usuario
//   Mesero             = un hilo (en modo paralelo, cada mesero es un proceso)
//   Tapete + pizarra   = el proceso y su memoria (compartida o propia)
//   Cafetera           = un núcleo de CPU (brillo y vapor = uso real medido)
//   Cocina             = E/S: el mesero espera sin usar ninguna cafetera
//   Llave dorada       = el GIL: solo quien la tiene ejecuta código Python
//
// Todo lo que se mueve responde a eventos reales del servidor Python.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

export const PALETA = ['#38bdf8', '#fb923c', '#4ade80', '#c084fc', '#facc15',
  '#f472b6', '#2dd4bf', '#f87171', '#818cf8', '#a3e635', '#fdba74', '#67e8f9'];

const COL = { espera: '#94a3b8', io: '#38bdf8', cpu: '#f59e0b', listo: '#34d399' };
const PIELES = ['#f1c27d', '#c68642', '#e0ac69', '#8d5524', '#ffdbac', '#d6a77a'];
const ROPA = ['#64748b', '#7c3aed', '#be185d', '#0f766e', '#b45309',
  '#1d4ed8', '#4d7c0f', '#9f1239', '#0e7490', '#6d28d9'];

// ---------------------------------------------------------------- plano
const MESAS = [];
for (const z of [3.5, 7.1]) for (const x of [-8, -4, 0, 4, 8]) MESAS.push({ x, z });
const Z_CASA = -0.6;          // donde esperan los meseros libres
const Z_BARRA = -5.0;         // mostrador y cocina
const Z_PUESTO = -3.85;       // donde se para un mesero a trabajar
const BARRA = { x0: -12.5, x1: 1.5 };
const COCINA = { x0: 3.0, x1: 12.5 };
const ANCHO_PERSONAL = 21;
const VELOCIDAD = 9;          // unidades por segundo al caminar
const SOBRE_CABEZA = new THREE.Vector3(0, 2.3, 0);

// ------------------------------------------------- geometrías y materiales
const G = {
  pierna: new THREE.CapsuleGeometry(0.09, 0.5, 4, 8),
  torso: new THREE.CapsuleGeometry(0.26, 0.42, 4, 12),
  brazo: new THREE.CapsuleGeometry(0.07, 0.42, 4, 8),
  cabeza: new THREE.SphereGeometry(0.2, 16, 12),
  gorro: new THREE.CylinderGeometry(0.17, 0.2, 0.1, 16),
  delantal: new THREE.BoxGeometry(0.4, 0.55, 0.03),
  bandeja: new THREE.CylinderGeometry(0.24, 0.24, 0.03, 20),
  taza: new THREE.CylinderGeometry(0.09, 0.07, 0.14, 14),
  cafeTaza: new THREE.CircleGeometry(0.08, 14),
  asa: new THREE.TorusGeometry(0.045, 0.014, 6, 12),
  plato: new THREE.CylinderGeometry(0.15, 0.15, 0.015, 18),
  ticket: new THREE.BoxGeometry(0.2, 0.02, 0.28),
  anillo: new THREE.TorusGeometry(0.55, 0.035, 8, 40),
  aro: new THREE.RingGeometry(0.86, 1.0, 40),
  llave: new THREE.OctahedronGeometry(0.16),
  vapor: new THREE.SphereGeometry(0.08, 8, 6),
  mesaTapa: new THREE.CylinderGeometry(0.72, 0.72, 0.05, 32),
  mesaPata: new THREE.CylinderGeometry(0.06, 0.06, 0.7, 10),
  mesaBase: new THREE.CylinderGeometry(0.34, 0.34, 0.03, 20),
  asiento: new THREE.BoxGeometry(0.5, 0.07, 0.5),
  respaldo: new THREE.BoxGeometry(0.5, 0.6, 0.06),
  pata: new THREE.BoxGeometry(0.05, 0.45, 0.05),
  cafCuerpo: new THREE.BoxGeometry(0.66, 0.82, 0.5),
  pantalla: new THREE.PlaneGeometry(0.44, 0.16),
  boquilla: new THREE.CylinderGeometry(0.035, 0.035, 0.12, 8),
};

function mat(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...extra });
}
const M = {
  pantalon: mat('#1f2937'),
  blanco: mat('#f8fafc', { roughness: 0.5 }),
  madera: mat('#9a6b47', { roughness: 0.55 }),
  maderaOscura: mat('#5b3a29'),
  metal: mat('#a3adbb', { metalness: 0.75, roughness: 0.3 }),
  metalOscuro: mat('#3f4652', { metalness: 0.6, roughness: 0.4 }),
  ceramica: mat('#f5f5f4', { roughness: 0.3 }),
  cafe: mat('#3b2416', { roughness: 0.2 }),
  oro: mat('#facc15', { metalness: 0.6, roughness: 0.25, emissive: '#f59e0b', emissiveIntensity: 0.7 }),
  pizarra: mat('#1f3d2b', { roughness: 0.9 }),
};
Object.values(M).forEach((m) => { m.userData.compartido = true; });

// --------------------------------------------------------------- ayudas
function etiqueta(html, clase, cx = 0.5, cy = 1) {
  const div = document.createElement('div');
  div.className = clase;
  div.innerHTML = html;
  const o = new CSS2DObject(div);
  o.center.set(cx, cy);
  return o;
}

function girar(actual, objetivo, k) {
  const d = Math.atan2(Math.sin(objetivo - actual), Math.cos(objetivo - actual));
  return actual + d * k;
}

const suave = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
const rebote = (t) => 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;

function textura(dibujar, repetir) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  dibujar(c.getContext('2d'));
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(...repetir);
  t.anisotropy = 4;
  return t;
}

const texturaMadera = () => textura((x) => {
  for (let i = 0; i < 8; i++) {
    x.fillStyle = i % 2 ? '#3d2b1e' : '#463222';
    x.fillRect(0, i * 32, 256, 32);
    x.fillStyle = 'rgba(0,0,0,0.3)';
    x.fillRect(0, i * 32, 256, 2);
    x.fillRect((i * 97) % 256, i * 32, 2, 32);
  }
}, [5, 1.5]);

const texturaBaldosa = () => textura((x) => {
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      x.fillStyle = (i + j) % 2 ? '#1c2331' : '#232c3d';
      x.fillRect(i * 64, j * 64, 64, 64);
    }
  }
  x.strokeStyle = 'rgba(0,0,0,0.35)';
  x.lineWidth = 2;
  for (let i = 0; i <= 4; i++) {
    x.beginPath(); x.moveTo(i * 64, 0); x.lineTo(i * 64, 256); x.stroke();
    x.beginPath(); x.moveTo(0, i * 64); x.lineTo(256, i * 64); x.stroke();
  }
}, [7, 2]);

// ------------------------------------------------------------- personajes
function crearMesero(color, piel) {
  const g = new THREE.Group();
  const ropa = mat(color, { roughness: 0.55 });
  const pielM = mat(piel, { roughness: 0.6 });
  const piernas = [-0.12, 0.12].map((x) => {
    const p = new THREE.Group();
    p.position.set(x, 0.72, 0);
    const m = new THREE.Mesh(G.pierna, M.pantalon);
    m.position.y = -0.36;
    p.add(m);
    g.add(p);
    return p;
  });
  const torso = new THREE.Mesh(G.torso, ropa);
  torso.position.y = 1.2;
  const delantal = new THREE.Mesh(G.delantal, M.blanco);
  delantal.position.set(0, 1.0, 0.25);
  const cabeza = new THREE.Mesh(G.cabeza, pielM);
  cabeza.position.y = 1.86;
  const gorro = new THREE.Mesh(G.gorro, ropa);
  gorro.position.y = 2.06;
  const brazo = (x, rot) => {
    const p = new THREE.Group();
    p.position.set(x, 1.5, 0);
    p.rotation.x = rot;
    const m = new THREE.Mesh(G.brazo, ropa);
    m.position.y = -0.28;
    p.add(m);
    g.add(p);
    return p;
  };
  const brazoI = brazo(-0.34, 0);
  const brazoD = brazo(0.34, -1.1);
  const bandeja = new THREE.Mesh(G.bandeja, M.metal);
  bandeja.position.set(0.36, 1.27, 0.52);
  g.add(torso, delantal, cabeza, gorro, bandeja);
  return { g, piernas, brazoI, brazoD };
}

function crearCliente(color, piel) {
  const g = new THREE.Group();
  const asiento = new THREE.Mesh(G.asiento, M.maderaOscura);
  asiento.position.y = 0.45;
  const respaldo = new THREE.Mesh(G.respaldo, M.maderaOscura);
  respaldo.position.set(0, 0.78, -0.26);
  g.add(asiento, respaldo);
  for (const [x, z] of [[-0.21, -0.21], [0.21, -0.21], [-0.21, 0.21], [0.21, 0.21]]) {
    const p = new THREE.Mesh(G.pata, M.maderaOscura);
    p.position.set(x, 0.225, z);
    g.add(p);
  }
  const cuerpo = new THREE.Group();
  const ropa = mat(color);
  const torso = new THREE.Mesh(G.torso, ropa);
  torso.position.y = 0.97;
  torso.scale.set(0.95, 0.9, 0.95);
  const cabeza = new THREE.Mesh(G.cabeza, mat(piel, { roughness: 0.6 }));
  cabeza.position.y = 1.6;
  for (const x of [-0.3, 0.3]) {
    const b = new THREE.Group();
    b.position.set(x, 1.25, 0);
    b.rotation.x = -1.0;
    const m = new THREE.Mesh(G.brazo, ropa);
    m.position.y = -0.28;
    b.add(m);
    cuerpo.add(b);
  }
  cuerpo.add(torso, cabeza);
  g.add(cuerpo);
  return { g, cuerpo };
}

function crearTaza() {
  const g = new THREE.Group();
  const plato = new THREE.Mesh(G.plato, M.ceramica);
  const taza = new THREE.Mesh(G.taza, M.ceramica);
  taza.position.y = 0.08;
  const cafe = new THREE.Mesh(G.cafeTaza, M.cafe);
  cafe.rotation.x = -Math.PI / 2;
  cafe.position.y = 0.151;
  const asa = new THREE.Mesh(G.asa, M.ceramica);
  asa.position.set(0.1, 0.08, 0);
  g.add(plato, taza, cafe, asa);
  return g;
}

function crearMesa() {
  const g = new THREE.Group();
  const tapa = new THREE.Mesh(G.mesaTapa, M.madera);
  tapa.position.y = 0.74;
  const pata = new THREE.Mesh(G.mesaPata, M.metalOscuro);
  pata.position.y = 0.37;
  const base = new THREE.Mesh(G.mesaBase, M.metalOscuro);
  base.position.y = 0.015;
  g.add(tapa, pata, base);
  return g;
}

function crearCafetera() {
  const g = new THREE.Group();
  const cuerpo = new THREE.Mesh(G.cafCuerpo, M.metal);
  cuerpo.position.y = 0.41;
  const pantallaM = new THREE.MeshStandardMaterial({ color: '#0b1220', emissive: '#22d3ee', emissiveIntensity: 0 });
  const pantalla = new THREE.Mesh(G.pantalla, pantallaM);
  pantalla.position.set(0, 0.62, 0.251);
  const boquilla = new THREE.Mesh(G.boquilla, M.metalOscuro);
  boquilla.position.set(0, 0.3, 0.2);
  const taza = crearTaza();
  taza.position.set(0, 0, 0.18);
  taza.scale.setScalar(0.9);
  const vapor = [0, 1, 2].map(() => {
    const m = new THREE.Mesh(G.vapor, new THREE.MeshBasicMaterial({
      color: '#e2e8f0', transparent: true, opacity: 0, depthWrite: false }));
    m.position.y = 0.9;
    g.add(m);
    return m;
  });
  g.add(cuerpo, pantalla, boquilla, taza);
  return { g, pantallaM, vapor };
}

// ================================================================= escena
export class Escena {
  constructor(contenedor) {
    this.contenedor = contenedor;
    this.reloj = new THREE.Clock();
    this.meseros = [];
    this.cafeteras = [];
    this.llaves = [];
    this.clientes = new Map();
    this.vuelos = [];
    this.k = 0;
    this.tLlegada = null;
    this.ultimoTexto = 0;
    this._v = new THREE.Vector3();

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    contenedor.appendChild(this.renderer.domElement);

    this.etiquetas = new CSS2DRenderer();
    this.etiquetas.domElement.className = 'capa-etiquetas';
    contenedor.appendChild(this.etiquetas.domElement);

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog('#070b12', 45, 95);
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 200);

    this.controles = new OrbitControls(this.camera, this.etiquetas.domElement);
    this.controles.enableDamping = true;
    this.controles.maxPolarAngle = Math.PI * 0.47;
    this.controles.minDistance = 8;
    this.controles.maxDistance = 70;

    this.scene.add(new THREE.HemisphereLight('#ffe8cc', '#1a1410', 1.15));
    const sol = new THREE.DirectionalLight('#fff4e0', 1.5);
    sol.position.set(6, 14, 10);
    this.scene.add(sol);

    this._construirLocal();
    this.personal = new THREE.Group();
    this.barra = new THREE.Group();
    this.comensales = new THREE.Group();
    this.efectos = new THREE.Group();
    this.scene.add(this.personal, this.barra, this.comensales, this.efectos);

    new ResizeObserver(() => this._ajustar()).observe(contenedor);
    this._ajustar();
    this.renderer.setAnimationLoop(() => this._cuadro());
  }

  // ------------------------------------------------------- el local fijo
  _construirLocal() {
    const s = this.scene;
    const piso = (w, d, x, z, mapa) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d),
        new THREE.MeshStandardMaterial({ map: mapa, roughness: 0.9 }));
      m.rotation.x = -Math.PI / 2;
      m.position.set(x, 0, z);
      s.add(m);
    };
    const suelo = new THREE.Mesh(new THREE.PlaneGeometry(140, 140), mat('#0a0d14', { roughness: 1 }));
    suelo.rotation.x = -Math.PI / 2;
    suelo.position.y = -0.02;
    s.add(suelo);
    piso(28, 7.6, 0, 5.4, texturaMadera());     // comedor
    piso(28, 8.2, 0, -2.5, texturaBaldosa());   // personal, barra y cocina

    for (const m of MESAS) {
      const mesa = crearMesa();
      mesa.position.set(m.x, 0, m.z);
      s.add(mesa);
    }

    // Barra (mostrador) con su pared
    const bx = (BARRA.x0 + BARRA.x1) / 2, bw = BARRA.x1 - BARRA.x0;
    const mueble = new THREE.Mesh(new THREE.BoxGeometry(bw, 1.0, 0.9), mat('#4a3326'));
    mueble.position.set(bx, 0.5, Z_BARRA);
    const cubierta = new THREE.Mesh(new THREE.BoxGeometry(bw + 0.1, 0.06, 1.0), mat('#d9cbb5', { roughness: 0.35 }));
    cubierta.position.set(bx, 1.03, Z_BARRA);
    const pared = new THREE.Mesh(new THREE.BoxGeometry(bw, 2.8, 0.2), mat('#2a1f1a'));
    pared.position.set(bx, 1.4, Z_BARRA - 0.8);
    s.add(mueble, cubierta, pared);

    // Cocina con ventanilla
    const kx = (COCINA.x0 + COCINA.x1) / 2, kw = COCINA.x1 - COCINA.x0;
    const muro = mat('#334155');
    const abajo = new THREE.Mesh(new THREE.BoxGeometry(kw, 1.05, 0.3), muro);
    abajo.position.set(kx, 0.525, Z_BARRA - 0.1);
    const arriba = new THREE.Mesh(new THREE.BoxGeometry(kw, 0.7, 0.3), muro);
    arriba.position.set(kx, 2.45, Z_BARRA - 0.1);
    const repisa = new THREE.Mesh(new THREE.BoxGeometry(kw, 0.06, 0.6), M.metal);
    repisa.position.set(kx, 1.08, Z_BARRA + 0.05);
    const fuego = new THREE.Mesh(new THREE.PlaneGeometry(kw - 0.4, 1.05),
      new THREE.MeshStandardMaterial({ color: '#2b1208', emissive: '#c2410c', emissiveIntensity: 0.55 }));
    fuego.position.set(kx, 1.6, Z_BARRA - 0.7);
    s.add(abajo, arriba, repisa, fuego);
    for (const x of [COCINA.x0 + 0.15, COCINA.x1 - 0.15]) {
      const poste = new THREE.Mesh(new THREE.BoxGeometry(0.3, 2.8, 0.3), muro);
      poste.position.set(x, 1.4, Z_BARRA - 0.1);
      s.add(poste);
    }

    const zona = (titulo, sub, x, y, z, cx = 0.5, cy = 1) => {
      const e = etiqueta(`${titulo}<small>${sub}</small>`, 'lbl lbl-zona', cx, cy);
      e.position.set(x, y, z);
      s.add(e);
    };
    zona('Barra', 'cada cafetera es un núcleo de la CPU', bx, 3.25, Z_BARRA - 0.8);
    zona('Cocina · E/S', 'esperar aquí no ocupa ninguna cafetera', kx, 3.25, Z_BARRA - 0.1);
    zona('Comedor', '10 mesas = 10 solicitudes', -10.3, 0, 2.3, 0.5, 0.5);
  }

  _vaciar(grupo) {
    grupo.traverse((o) => {
      if (o.isCSS2DObject) o.element.remove();
      if (o.material && !o.material.userData.compartido) o.material.dispose();
    });
    grupo.clear();
  }

  // -------------------------------------------------------- reconfigurar
  /** Arma el personal según el modelo: cuántos meseros, procesos y llaves. */
  reconfigurar({ modo, trabajadores, nucleos, pid, gil = true }) {
    this._vaciar(this.personal);
    this.meseros = [];
    this.llaves = [];
    this.modo = modo;
    const n = modo === 'secuencial' ? 1 : Math.max(1, trabajadores);
    this.compacto = n > 8;
    const esp = Math.min(2.2, ANCHO_PERSONAL / n);
    const xs = Array.from({ length: n }, (_, i) => (i - (n - 1) / 2) * esp);
    const zMat = Z_CASA - 0.45, prof = 2.7;

    const tapete = (x, ancho, color) => {
      const geo = new THREE.PlaneGeometry(ancho, prof);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.1, depthWrite: false }));
      m.rotation.x = -Math.PI / 2;
      m.position.set(x, 0.015, zMat);
      const borde = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 }));
      borde.rotation.x = -Math.PI / 2;
      borde.position.set(x, 0.02, zMat);
      this.personal.add(m, borde);
    };
    const pizarra = (x, ancho) => {
      const tabla = new THREE.Mesh(new THREE.BoxGeometry(ancho, 0.65, 0.05), M.pizarra);
      tabla.position.set(x, 1.05, zMat - prof / 2 + 0.2);
      const marco = new THREE.LineSegments(new THREE.EdgesGeometry(tabla.geometry),
        new THREE.LineBasicMaterial({ color: '#a16207' }));
      marco.position.copy(tabla.position);
      const pie = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.78, 0.05), M.maderaOscura);
      pie.position.set(x, 0.39, tabla.position.z);
      this.personal.add(tabla, marco, pie);
      return new THREE.Vector3(x, 1.52, tabla.position.z);
    };

    const pizarras = [];
    let texto;
    if (modo === 'paralelo') {
      xs.forEach((x) => {
        tapete(x, esp * 0.88, '#fdba74');
        pizarras.push(pizarra(x, Math.min(0.9, esp * 0.6)));
      });
      texto = `${n} procesos<small>cada uno con su pizarra (memoria)${gil ? ' y su propia llave (GIL)' : ''}</small>`;
    } else {
      const ancho = Math.max(3.2, n * esp + 0.2);
      tapete(0, ancho, '#7dd3fc');
      pizarras.push(pizarra(0, 1.5));
      const proc = `1 proceso${pid ? ` · PID ${pid}` : ''}`;
      texto = modo === 'secuencial'
        ? `${proc}<small>1 mesero (hilo) atiende todo</small>`
        : `${proc}<small>${n} meseros (hilos) comparten la pizarra (memoria)${gil ? ' y una sola llave (GIL)' : ''}</small>`;
    }
    // A la izquierda del personal si cabe; si no, debajo del primer tapete
    const izq = modo === 'paralelo' ? xs[0] - esp * 0.44 : -Math.max(3.2, n * esp + 0.2) / 2;
    const cabe = izq > -7;
    const e = etiqueta(texto, `lbl lbl-proceso${cabe ? ' derecha' : ''}`, cabe ? 1 : 0, cabe ? 0.5 : 0);
    e.position.set(cabe ? izq - 0.3 : izq, 0.02, cabe ? zMat : zMat + prof / 2 + 0.08);
    this.personal.add(e);

    xs.forEach((x, i) => {
      const color = PALETA[i % PALETA.length];
      const p = crearMesero(color, PIELES[(i * 3 + 1) % PIELES.length]);
      p.g.position.set(x, 0, Z_CASA);
      const lbl = etiqueta('', 'lbl lbl-mesero', 0.5, 1);
      lbl.position.set(0, 2.6 + (i % 2) * 0.55, 0);
      const anillo = new THREE.Mesh(G.anillo, new THREE.MeshBasicMaterial({
        color: COL.io, transparent: true, opacity: 0.85 }));
      anillo.rotation.x = Math.PI / 2;
      anillo.position.y = 0.06;
      anillo.visible = false;
      p.g.add(lbl, anillo);
      this.personal.add(p.g);
      const m = {
        ...p, idx: i, color, lbl, anillo,
        nombre: modo === 'secuencial' ? 'Hilo principal' : modo === 'paralelo' ? `Proceso ${i + 1}` : `Hilo_${i}`,
        casa: new THREE.Vector3(x, 0, Z_CASA), objetivo: new THREE.Vector3(x, 0, Z_CASA),
        rotObjetivo: 0, fase: 0, solicitud: null, tipo: null, nucleo: null, progreso: 0, regresarEn: null,
      };
      this.meseros.push(m);
      this._lblMesero(m);
    });

    if (gil) {
      pizarras.forEach((pos) => {
        const mesh = new THREE.Mesh(G.llave, M.oro);
        mesh.position.copy(pos);
        const lbl = etiqueta('GIL', 'lbl lbl-gil', 0, 0.5);
        lbl.position.set(0.22, 0, 0);
        mesh.add(lbl);
        this.personal.add(mesh);
        this.llaves.push({ mesh, descanso: pos.clone(), dueno: null });
      });
    }

    const k = Math.max(1, nucleos || 1);
    if (k !== this.k) this._construirCafeteras(k);
  }

  _construirCafeteras(k) {
    this._vaciar(this.barra);
    this.cafeteras = [];
    this.k = k;
    const sp = Math.min(3.2, (BARRA.x1 - BARRA.x0 - 1) / k);
    const x0 = (BARRA.x0 + BARRA.x1) / 2 - (k * sp) / 2;
    const esc = Math.min(1.3, sp / 0.95);
    for (let j = 0; j < k; j++) {
      const c = crearCafetera();
      const x = x0 + (j + 0.5) * sp;
      c.g.position.set(x, 1.06, Z_BARRA);
      c.g.scale.setScalar(esc);
      const lbl = etiqueta('', 'lbl lbl-cafetera', 0.5, 0.5);
      lbl.position.set(x, 2.3, Z_BARRA - 0.68);   // rótulo en la pared, sobre su cafetera
      this.barra.add(c.g, lbl);
      const caf = { ...c, x, sp, j, lbl, uso: 0, objetivo: 0, corto: k > 6 };
      this.cafeteras.push(caf);
      this._lblCafetera(caf, 0);
    }
  }

  // ------------------------------------------------------------- clientes
  crearSolicitudes(solicitudes) {
    this._vaciar(this.comensales);
    this._vaciar(this.efectos);
    this.vuelos = [];
    this.clientes.clear();
    this.tLlegada = performance.now();
    solicitudes.forEach((s, i) => {
      const mesa = MESAS[i % MESAS.length];
      const p = crearCliente(ROPA[i % ROPA.length], PIELES[i % PIELES.length]);
      p.g.position.set(mesa.x, 0, mesa.z - 0.95);
      p.g.scale.setScalar(0.001);
      const lbl = etiqueta(
        `<b>#${s.id} ${s.usuario}</b> · ${s.trabajo === 'io' ? 'E/S' : 'CPU'}<em>esperando</em>`,
        'lbl lbl-cliente', 0.5, 1);
      lbl.position.set(0, 2.05, 0);
      p.g.add(lbl);
      const aro = new THREE.Mesh(G.aro, new THREE.MeshBasicMaterial({
        color: COL.espera, transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false }));
      aro.rotation.x = -Math.PI / 2;
      aro.position.set(mesa.x, 0.025, mesa.z);
      const taza = crearTaza();
      taza.position.set(mesa.x + 0.15, 0.765, mesa.z + 0.15);
      taza.visible = false;
      this.comensales.add(p.g, aro, taza);
      this.clientes.set(s.id, {
        s, ...p, lbl, estadoEl: lbl.element.querySelector('em'), aro, taza, mesa,
        estado: 'espera', retraso: i * 0.05, vida: 0, salto: 0,
      });
    });
  }

  // --------------------------------------------------------------- eventos
  _llaveDe(m) { return this.llaves[this.modo === 'paralelo' ? m.idx : 0]; }
  _tomarLlave(m) { const l = this._llaveDe(m); if (l) l.dueno = m; }
  _soltarLlave(m) { const l = this._llaveDe(m); if (l && l.dueno === m) l.dueno = null; }

  _puesto(m) {
    if (m.tipo === 'cpu' && this.cafeteras.length) {
      // El mesero va a la cafetera del núcleo donde el sistema operativo lo
      // está ejecutando. Si el sistema no lo informa, se usa una aproximación.
      const j = m.nucleo != null && m.nucleo < this.cafeteras.length
        ? m.nucleo : m.idx % this.cafeteras.length;
      const c = this.cafeteras[j];
      const off = ((m.idx % 3) - 1) * Math.min(0.45, c.sp * 0.35);
      return new THREE.Vector3(c.x + off, 0, Z_PUESTO);
    }
    const n = this.meseros.length;
    const util = COCINA.x1 - COCINA.x0 - 1.4;
    const paso = n > 1 ? Math.min(1.3, util / (n - 1)) : 0;
    const x = (COCINA.x0 + COCINA.x1) / 2 + (m.idx - (n - 1) / 2) * paso;
    return new THREE.Vector3(x, 0, Z_PUESTO);
  }

  _lblMesero(m) {
    const pct = Math.round(m.progreso * 100);
    let sub = 'libre';
    if (m.solicitud != null) {
      if (this.compacto) sub = `#${m.solicitud}`;
      else if (m.tipo === 'io') sub = `#${m.solicitud} · esperando cocina · ${pct}%`;
      else sub = `#${m.solicitud} · ${pct}%${m.nucleo != null ? ` · núcleo ${m.nucleo}` : ''}`;
    }
    m.lbl.element.innerHTML = `<b style="color:${m.color}">${m.nombre}</b><small>${sub}</small>`;
  }

  _lblCafetera(c, u) {
    c.lbl.element.innerHTML = c.corto ? `${c.j}<small>${u}%</small>` : `núcleo ${c.j}<small>${u}% de uso</small>`;
  }

  _vuelo(obj, desde, hasta, dur, altura, alTerminar) {
    obj.position.copy(desde);
    this.efectos.add(obj);
    this.vuelos.push({ obj, desde: desde.clone(), hasta, t: 0, dur, altura, alTerminar });
  }

  inicio(ev, carril) {
    const m = this.meseros[carril];
    const c = this.clientes.get(ev.id);
    if (!m || !c) return;
    Object.assign(m, { nombre: ev.trabajador, solicitud: ev.id, tipo: ev.trabajo,
      nucleo: ev.nucleo, progreso: 0, regresarEn: null });
    m.objetivo.copy(this._puesto(m));
    m.rotObjetivo = Math.PI;
    m.anillo.visible = ev.trabajo === 'io';
    if (ev.trabajo === 'cpu') this._tomarLlave(m); else this._soltarLlave(m);
    this._lblMesero(m);

    c.estado = 'atendida';
    c.aro.material.color.set(m.color);
    c.aro.material.opacity = 0.75;
    c.estadoEl.textContent = `lo atiende ${ev.trabajador}`;
    // El pedido viaja de la mesa al mesero
    const ticket = new THREE.Mesh(G.ticket, M.blanco);
    this._vuelo(ticket, new THREE.Vector3(c.mesa.x, 0.9, c.mesa.z),
      () => this._v.copy(m.g.position).setY(1.4), 0.45, 1.0,
      (o) => this.efectos.remove(o));
  }

  progreso(ev) {
    const m = this.meseros.find((x) => x.solicitud === ev.id);
    const c = this.clientes.get(ev.id);
    if (!m) return;
    m.progreso = ev.progreso;
    if (ev.nucleo !== m.nucleo) {
      m.nucleo = ev.nucleo;
      if (m.tipo === 'cpu') m.objetivo.copy(this._puesto(m));   // el SO lo movió de núcleo
    }
    if (m.tipo === 'cpu') this._tomarLlave(m);
    this._lblMesero(m);
    if (c) c.estadoEl.textContent = `${ev.trabajador} · ${Math.round(ev.progreso * 100)}%`;
  }

  fin(ev) {
    const m = this.meseros.find((x) => x.solicitud === ev.id);
    const c = this.clientes.get(ev.id);
    if (c) {
      c.estado = 'servida';
      c.aro.material.color.set(COL.listo);
      c.aro.material.opacity = 0.8;
      c.estadoEl.textContent = `servida en ${ev.t.toFixed(2)} s`;
      c.lbl.element.classList.add('servida');
    }
    if (!m) return;
    if (c) {
      // La taza viaja de la bandeja del mesero a la mesa
      const desde = m.g.localToWorld(new THREE.Vector3(0.36, 1.32, 0.52));
      this._vuelo(crearTaza(), desde, new THREE.Vector3(c.taza.position.x, 0.765, c.taza.position.z),
        0.6, 1.4, (o) => { this.efectos.remove(o); c.taza.visible = true; c.salto = 1; });
    }
    this._soltarLlave(m);
    Object.assign(m, { solicitud: null, tipo: null, progreso: 0, regresarEn: this.reloj.elapsedTime + 0.35 });
    m.anillo.visible = false;
    this._lblMesero(m);
  }

  /** Uso real (%) de cada núcleo medido por psutil. */
  cpu(uso) {
    uso.forEach((u, j) => {
      const c = this.cafeteras[j];
      if (!c) return;
      c.objetivo = u / 100;
      this._lblCafetera(c, Math.round(u));
    });
  }

  // ------------------------------------------------------------- cámara
  _ajustar() {
    const { clientWidth: w, clientHeight: h } = this.contenedor;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.etiquetas.setSize(w, h);
    this.camera.aspect = w / h;
    const vfov = THREE.MathUtils.degToRad(this.camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect);
    const dist = Math.max(12.6 / Math.tan(hfov / 2), 7.2 / Math.tan(vfov / 2));
    const objetivo = new THREE.Vector3(0, 0.8, 1.6);
    const dir = new THREE.Vector3(0, 0.8, 0.6).normalize();
    this.camera.position.copy(objetivo).addScaledVector(dir, dist);
    this.controles.target.copy(objetivo);
    this.camera.updateProjectionMatrix();
  }

  vistaInicial() { this._ajustar(); }

  // ----------------------------------------------------------- animación
  _cuadro() {
    const dt = Math.min(this.reloj.getDelta(), 0.05);
    const t = this.reloj.elapsedTime;
    const k = 1 - Math.exp(-dt * 10);

    for (let i = this.vuelos.length - 1; i >= 0; i--) {
      const v = this.vuelos[i];
      v.t = Math.min(1, v.t + dt / v.dur);
      const e = suave(v.t);
      const hasta = typeof v.hasta === 'function' ? v.hasta() : v.hasta;
      v.obj.position.lerpVectors(v.desde, hasta, e);
      v.obj.position.y += Math.sin(Math.PI * e) * v.altura;
      if (v.t >= 1) {
        this.vuelos.splice(i, 1);
        v.alTerminar?.(v.obj);
      }
    }

    for (const m of this.meseros) {
      if (m.regresarEn != null && t >= m.regresarEn && m.solicitud == null) {
        m.objetivo.copy(m.casa);
        m.rotObjetivo = 0;
        m.regresarEn = null;
      }
      const p = m.g.position;
      const dx = m.objetivo.x - p.x, dz = m.objetivo.z - p.z;
      const dist = Math.hypot(dx, dz);
      if (dist > 0.03) {
        const paso = Math.min(dist, VELOCIDAD * dt);
        p.x += (dx / dist) * paso;
        p.z += (dz / dist) * paso;
        m.fase += dt * 14;
        const s = Math.sin(m.fase);
        m.g.rotation.y = girar(m.g.rotation.y, Math.atan2(dx, dz), 1 - Math.exp(-dt * 14));
        m.piernas[0].rotation.x = s * 0.6;
        m.piernas[1].rotation.x = -s * 0.6;
        m.brazoI.rotation.x = -s * 0.5;
        p.y = Math.abs(Math.cos(m.fase)) * 0.05;
      } else {
        m.g.rotation.y = girar(m.g.rotation.y, m.rotObjetivo, k);
        for (const pierna of m.piernas) pierna.rotation.x *= 0.8;
        m.brazoI.rotation.x *= 0.8;
        p.y *= 0.8;
        if (m.tipo === 'cpu') {           // preparando en la cafetera
          m.brazoD.rotation.x = -1.1 + Math.sin(t * 18) * 0.18;
          m.brazoI.rotation.x = -0.9 + Math.sin(t * 18 + 1) * 0.18;
        } else {
          m.brazoD.rotation.x += (-1.1 - m.brazoD.rotation.x) * k;
        }
      }
      if (m.anillo.visible) {             // esperando la cocina (E/S)
        m.anillo.rotation.z += dt * 3;
        m.anillo.scale.setScalar(1 + 0.1 * Math.sin(t * 4));
      }
    }

    for (const l of this.llaves) {
      const destino = l.dueno ? this._v.copy(l.dueno.g.position).add(SOBRE_CABEZA) : l.descanso;
      l.mesh.position.lerp(destino, 1 - Math.exp(-dt * 16));
      l.mesh.rotation.y += dt * 3;
    }

    for (const c of this.cafeteras) {
      c.uso += (c.objetivo - c.uso) * k;
      c.pantallaM.emissive.setHSL(0.52 - c.uso * 0.45, 0.9, 0.5);
      c.pantallaM.emissiveIntensity = 0.15 + c.uso * 2;
      c.vapor.forEach((v, i) => {
        const f = (t * 0.8 + i / 3) % 1;
        v.position.y = 0.9 + f * 0.9;
        v.position.x = Math.sin(t * 2 + i) * 0.06;
        v.scale.setScalar(0.6 + f * 1.4);
        v.material.opacity = c.uso * (1 - f) * 0.7;
      });
    }

    const ahora = this.tLlegada ? (performance.now() - this.tLlegada) / 1000 : 0;
    const escribir = t - this.ultimoTexto > 0.1;
    if (escribir) this.ultimoTexto = t;
    for (const c of this.clientes.values()) {
      c.vida += dt;
      const a = THREE.MathUtils.clamp((c.vida - c.retraso) / 0.35, 0, 1);
      c.g.scale.setScalar(a < 1 ? 0.001 + rebote(a) : 1);
      if (c.estado === 'espera') {
        if (escribir) c.estadoEl.textContent = `esperando ${ahora.toFixed(1)} s`;
        c.aro.material.opacity = 0.35 + 0.25 * Math.sin(t * 4 + c.s.id);
        c.cuerpo.rotation.y = Math.sin(t * 1.5 + c.s.id) * 0.15;
      } else {
        c.cuerpo.rotation.y *= 0.9;
      }
      if (c.salto > 0) {
        c.salto = Math.max(0, c.salto - dt * 2.2);
        c.cuerpo.position.y = Math.sin(Math.PI * (1 - c.salto)) * 0.3;
      }
    }

    this.controles.update();
    this.renderer.render(this.scene, this.camera);
    this.etiquetas.render(this.scene, this.camera);
  }
}
