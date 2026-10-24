// Escena 3D: usuarios → servidor (procesos, hilos) → respuestas, con la CPU debajo.
// Todo lo que se mueve responde a eventos reales que llegan del servidor Python.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

export const PALETA = ['#38bdf8', '#fb923c', '#4ade80', '#c084fc', '#facc15',
  '#f472b6', '#2dd4bf', '#f87171', '#818cf8', '#a3e635', '#fdba74', '#67e8f9'];

const COLOR = {
  espera: new THREE.Color('#475569'),
  io: new THREE.Color('#38bdf8'),
  cpu: new THREE.Color('#f59e0b'),
  listo: new THREE.Color('#34d399'),
};

const LARGO_CARRIL = 10;          // de x = -5 a x = 5
const X_USUARIOS = -12.1;
const X_RESPUESTAS = 12.1;

function etiqueta(texto, clase = 'lbl') {
  const div = document.createElement('div');
  div.className = clase;
  div.textContent = texto;
  return new CSS2DObject(div);
}

function posicionRejilla(xBase, i) {
  // 10 fichas en 2 columnas × 5 filas
  const col = i % 2, fila = Math.floor(i / 2);
  return new THREE.Vector3(xBase + (col - 0.5) * 1.7, 0.3, -3.4 + fila * 1.7);
}

export class Escena {
  constructor(contenedor) {
    this.contenedor = contenedor;
    this.reloj = new THREE.Clock();
    this.fichas = new Map();       // id → ficha
    this.carriles = [];            // índice → {grupo, etiqueta, z}
    this.nucleos = [];             // índice → {barra, material, uso, destello}
    this.listas = 0;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    contenedor.appendChild(this.renderer.domElement);

    this.etiquetas = new CSS2DRenderer();
    this.etiquetas.domElement.className = 'capa-etiquetas';
    contenedor.appendChild(this.etiquetas.domElement);

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog('#070b12', 38, 80);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);

    this.controles = new OrbitControls(this.camera, this.etiquetas.domElement);
    this.controles.enableDamping = true;
    this.controles.maxPolarAngle = Math.PI * 0.47;
    this.controles.minDistance = 10;
    this.controles.maxDistance = 70;

    this.scene.add(new THREE.HemisphereLight('#b6c8ff', '#0b0f17', 1.1));
    const sol = new THREE.DirectionalLight('#ffffff', 1.4);
    sol.position.set(6, 14, 10);
    this.scene.add(sol);

    const suelo = new THREE.Mesh(
      new THREE.PlaneGeometry(120, 120),
      new THREE.MeshStandardMaterial({ color: '#0a0f19', roughness: 1 }));
    suelo.rotation.x = -Math.PI / 2;
    suelo.position.y = -0.02;
    this.scene.add(suelo);
    const rejilla = new THREE.GridHelper(80, 80, '#1b2638', '#111a28');
    rejilla.position.y = -0.01;
    this.scene.add(rejilla);

    this._zonasFijas();
    this.estructura = new THREE.Group();
    this.scene.add(this.estructura);

    this.geoFicha = new THREE.BoxGeometry(1.1, 0.5, 1.1);
    this.geoAnillo = new THREE.TorusGeometry(0.95, 0.05, 8, 40);

    new ResizeObserver(() => this._ajustar()).observe(contenedor);
    this._ajustar();
    this.renderer.setAnimationLoop(() => this._cuadro());
  }

  // ---------------------------------------------------------------- zonas
  _zona(x, ancho, texto, sub) {
    const g = new THREE.Group();
    const placa = new THREE.Mesh(
      new THREE.BoxGeometry(ancho, 0.08, 9.6),
      new THREE.MeshStandardMaterial({ color: '#111a2a', roughness: 0.9 }));
    placa.position.set(x, 0.0, 0);
    g.add(placa);
    const borde = new THREE.LineSegments(
      new THREE.EdgesGeometry(placa.geometry),
      new THREE.LineBasicMaterial({ color: '#24324a' }));
    borde.position.copy(placa.position);
    g.add(borde);
    const e = etiqueta(texto, 'lbl lbl-zona');
    e.element.innerHTML = `${texto}<small>${sub}</small>`;
    e.position.set(x, 0.1, -5.1);
    e.center.set(0.5, 1);
    g.add(e);
    this.scene.add(g);
  }

  _zonasFijas() {
    this._zona(X_USUARIOS, 4.4, 'Usuarios', '10 solicitudes llegan a la vez');
    this._zona(X_RESPUESTAS, 4.4, 'Respuestas', 'solicitudes atendidas');
  }

  // ------------------------------------------------------------ estructura
  /** Reconstruye el servidor según el modelo: procesos, hilos y núcleos. */
  reconfigurar({ modo, trabajadores, nucleos, pid }) {
    this._limpiar(this.estructura);
    this.carriles = [];
    this.nucleos = [];
    this.modo = modo;
    const n = modo === 'secuencial' ? 1 : trabajadores;
    const paso = Math.min(1.7, 8.4 / n);
    const z0 = -((n - 1) * paso) / 2;

    const matCaja = (color) => new THREE.MeshStandardMaterial({
      color, transparent: true, opacity: 0.07, depthWrite: false });
    const caja = (z, prof, color, texto) => {
      const geo = new THREE.BoxGeometry(LARGO_CARRIL + 1.6, 1.3, prof);
      const m = new THREE.Mesh(geo, matCaja(color));
      m.position.set(0, 0.65, z);
      const b = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 }));
      b.position.copy(m.position);
      this.estructura.add(m, b);
      if (!texto) return null;
      const e = etiqueta('', 'lbl lbl-proceso');
      e.element.innerHTML = texto;
      e.position.set(-(LARGO_CARRIL + 1.6) / 2, 1.3, z - prof / 2);
      e.center.set(0, 1);
      this.estructura.add(e);
      return e;
    };

    if (modo !== 'paralelo') {
      const prof = (n - 1) * paso + 1.5;
      caja(0, prof, '#7dd3fc',
        `Proceso del servidor${pid ? ` · PID ${pid}` : ''} · ${modo === 'secuencial'
          ? '1 hilo atiende todo' : `${n} hilos con memoria compartida`}`);
    }

    for (let i = 0; i < n; i++) {
      const z = z0 + i * paso;
      const color = new THREE.Color(PALETA[i % PALETA.length]);
      const carril = new THREE.Mesh(
        new THREE.PlaneGeometry(LARGO_CARRIL, Math.min(1.0, paso * 0.7)),
        new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.16 }));
      carril.rotation.x = -Math.PI / 2;
      carril.position.set(0, 0.06, z);
      const linea = new THREE.Mesh(
        new THREE.PlaneGeometry(LARGO_CARRIL, 0.05),
        new THREE.MeshBasicMaterial({ color }));
      linea.rotation.x = -Math.PI / 2;
      linea.position.set(0, 0.07, z + Math.min(0.5, paso * 0.35));
      this.estructura.add(carril, linea);

            if (modo === 'paralelo') {
        caja(z, paso * 0.92, '#fdba74', i === 0 ? `${n} procesos · cada uno con memoria propia` : null);
      }
      const e = etiqueta(modo === 'secuencial' ? 'Hilo principal'
        : modo === 'paralelo' ? `Proceso ${i + 1}` : `Hilo ${i}`, 'lbl lbl-carril');
      e.position.set(LARGO_CARRIL / 2 + 0.35, 0.1, z);
      e.center.set(0, 0.5);
      this.estructura.add(e);
      this.carriles.push({ z, etiqueta: e, material: carril.material, actividad: 0 });
    }

    // CPU: un bloque por núcleo, detrás del servidor
    const k = Math.max(1, nucleos || 1);
    const ancho = Math.min(1.3, 12 / k);
    const zCpu = z0 - 4.2;
    const base = new THREE.Mesh(
      new THREE.BoxGeometry(k * ancho + 1.2, 0.2, ancho + 1.0),
      new THREE.MeshStandardMaterial({ color: '#1e293b', metalness: 0.4, roughness: 0.5 }));
    base.position.set(0, 0.1, zCpu);
    this.estructura.add(base);
    const eCpu = etiqueta(`CPU · ${k} núcleos`, 'lbl lbl-cpu');
    eCpu.position.set((k * ancho + 1.2) / 2 + 0.3, 0.2, zCpu);
    eCpu.center.set(0, 0.5);
    this.estructura.add(eCpu);
    for (let c = 0; c < k; c++) {
      const material = new THREE.MeshStandardMaterial({
        color: '#334155', emissive: '#22d3ee', emissiveIntensity: 0 });
      const barra = new THREE.Mesh(new THREE.BoxGeometry(ancho * 0.8, 1, ancho * 0.8), material);
      const x = (c - (k - 1) / 2) * ancho;
      barra.position.set(x, 0.2, zCpu);
      barra.scale.y = 0.05;
      this.estructura.add(barra);
      let lbl = null;
      if (k <= 16) {
        lbl = etiqueta(`${c}`, 'lbl lbl-nucleo');
        lbl.position.set(x, 0.25, zCpu + ancho * 0.55);
        this.estructura.add(lbl);
      }
      this.nucleos.push({ barra, material, x, z: zCpu, uso: 0, objetivo: 0, destello: 0, lbl });
    }
  }

  _limpiar(grupo) {
    grupo.traverse((o) => {
      if (o.isCSS2DObject) o.element.remove();
      o.geometry?.dispose?.();
      o.material?.dispose?.();
    });
    grupo.clear();
  }

  // --------------------------------------------------------------- fichas
  crearSolicitudes(solicitudes) {
    for (const f of this.fichas.values()) {
      this.scene.remove(f.grupo);
      f.etq.element.remove();
      f.material.dispose();
      f.linea.geometry.dispose();
    }
    this.fichas.clear();
    this.listas = 0;
    solicitudes.forEach((s, i) => {
      const material = new THREE.MeshStandardMaterial({
        color: COLOR.espera.clone(), emissive: COLOR.espera.clone(),
        emissiveIntensity: 0.15, roughness: 0.35, metalness: 0.1 });
      const cuerpo = new THREE.Mesh(this.geoFicha, material);
      const anillo = new THREE.Mesh(this.geoAnillo,
        new THREE.MeshBasicMaterial({ color: COLOR.io, transparent: true, opacity: 0.8 }));
      anillo.rotation.x = Math.PI / 2;
      anillo.visible = false;
      const grupo = new THREE.Group();
      grupo.add(cuerpo, anillo);
      const destino = posicionRejilla(X_USUARIOS, i);
      grupo.position.copy(destino).add(new THREE.Vector3(0, 8 + i * 0.6, 0));
      const etq = etiqueta(`${s.id}`, 'lbl lbl-ficha');
      etq.position.set(0, 0.55, 0);
      grupo.add(etq);
      const linea = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
        new THREE.LineDashedMaterial({ color: '#22d3ee', dashSize: 0.25, gapSize: 0.18,
          transparent: true, opacity: 0.7 }));
      linea.visible = false;
      this.scene.add(grupo, linea);
      this.fichas.set(s.id, {
        s, grupo, cuerpo, anillo, material, etq, linea,
        estado: 'espera', objetivo: destino.clone(), vuelo: null,
        progreso: 0, carril: null, nucleo: null, color: COLOR.espera.clone(),
      });
    });
  }

  _volar(f, hasta, dur = 0.55, altura = 2.2) {
    f.vuelo = { desde: f.grupo.position.clone(), hasta: hasta.clone(), t: 0, dur, altura };
    f.objetivo.copy(hasta);
  }

  _posCarril(carril, progreso) {
    return new THREE.Vector3(-LARGO_CARRIL / 2 + 0.6 + progreso * (LARGO_CARRIL - 1.2),
      0.38, this.carriles[carril].z);
  }

  inicio(ev, carril) {
    const f = this.fichas.get(ev.id);
    const c = this.carriles[carril];
    if (!f || !c) return;
    f.estado = ev.trabajo;
    f.carril = carril;
    f.color.copy(COLOR[ev.trabajo]);
    f.anillo.visible = ev.trabajo === 'io';
    this._volar(f, this._posCarril(carril, 0));
    const nucleo = ev.nucleo != null ? ` · núcleo ${ev.nucleo}` : '';
    c.etiqueta.element.textContent = `${ev.trabajador}${nucleo}`;
    c.actividad = 1;
    this._marcarNucleo(f, ev.nucleo);
  }

  progreso(ev) {
    const f = this.fichas.get(ev.id);
    if (!f || f.carril == null) return;
    f.progreso = ev.progreso;
    const destino = this._posCarril(f.carril, ev.progreso);
    if (f.vuelo) f.vuelo.hasta.copy(destino);
    f.objetivo.copy(destino);
    this.carriles[f.carril].actividad = 1;
    const c = this.carriles[f.carril];
    if (ev.nucleo != null) c.etiqueta.element.textContent = `${ev.trabajador} · núcleo ${ev.nucleo}`;
    this._marcarNucleo(f, ev.nucleo);
  }

  _marcarNucleo(f, nucleo) {
    f.nucleo = nucleo;
    if (nucleo != null && this.nucleos[nucleo]) this.nucleos[nucleo].destello = 1;
  }

  fin(ev) {
    const f = this.fichas.get(ev.id);
    if (!f) return;
    f.estado = 'listo';
    f.color.copy(COLOR.listo);
    f.anillo.visible = false;
    f.nucleo = null;
    this._volar(f, posicionRejilla(X_RESPUESTAS, this.listas++), 0.7, 3);
  }

  /** Uso real (%) de cada núcleo medido por psutil. */
  cpu(uso) {
    uso.forEach((u, i) => { if (this.nucleos[i]) this.nucleos[i].objetivo = u / 100; });
  }

  // ------------------------------------------------------------- animación
  _ajustar() {
    const { clientWidth: w, clientHeight: h } = this.contenedor;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.etiquetas.setSize(w, h);
    this.camera.aspect = w / h;
    // Encuadrar ~33 unidades de ancho, sea cual sea la proporción de la ventana
    const vfov = THREE.MathUtils.degToRad(this.camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect);
    const dist = Math.max(16.6 / Math.tan(hfov / 2), 6 / Math.tan(vfov / 2));
    const dir = new THREE.Vector3(0, 0.6, 0.8).normalize();
    this.camera.position.copy(dir.multiplyScalar(dist)).add(new THREE.Vector3(0, 0, -2.6));
    this.controles.target.set(0, 0, -2.6);
    this.camera.updateProjectionMatrix();
  }

  vistaInicial() { this._ajustar(); }

  _cuadro() {
    const dt = Math.min(this.reloj.getDelta(), 0.05);
    const t = this.reloj.elapsedTime;
    const suave = 1 - Math.exp(-dt * 9);

    for (const f of this.fichas.values()) {
      const p = f.grupo.position;
      if (f.vuelo) {
        const v = f.vuelo;
        v.t = Math.min(1, v.t + dt / v.dur);
        const e = v.t < 0.5 ? 2 * v.t * v.t : 1 - (-2 * v.t + 2) ** 2 / 2;
        p.lerpVectors(v.desde, v.hasta, e);
        p.y += Math.sin(Math.PI * e) * v.altura;
        if (v.t >= 1) f.vuelo = null;
      } else {
        p.lerp(f.objetivo, suave);
      }
      f.material.color.lerp(f.color, suave);
      f.material.emissive.lerp(f.color, suave);
      if (f.estado === 'cpu') {
        f.cuerpo.rotation.y += dt * 5;
        f.material.emissiveIntensity = 0.55 + 0.25 * Math.sin(t * 14);
      } else if (f.estado === 'io') {
        f.cuerpo.rotation.y *= 0.9;
        f.anillo.rotation.z += dt * 2.5;
        f.anillo.scale.setScalar(1 + 0.12 * Math.sin(t * 4));
        f.material.emissiveIntensity = 0.3 + 0.2 * Math.sin(t * 4);
      } else {
        f.cuerpo.rotation.y *= 0.9;
        f.material.emissiveIntensity = f.estado === 'listo' ? 0.35 : 0.12;
      }
      // Línea de la ficha al núcleo donde corre (si el sistema lo reporta)
      const n = f.nucleo != null ? this.nucleos[f.nucleo] : null;
      if (n && (f.estado === 'io' || f.estado === 'cpu') && !f.vuelo) {
        const pos = f.linea.geometry.attributes.position;
        pos.setXYZ(0, p.x, p.y, p.z);
        pos.setXYZ(1, n.x, 0.25 + n.barra.scale.y, n.z);
        pos.needsUpdate = true;
        f.linea.computeLineDistances();
        f.linea.material.color.copy(f.color);
        f.linea.visible = true;
      } else {
        f.linea.visible = false;
      }
    }

    for (const n of this.nucleos) {
      n.uso += (n.objetivo - n.uso) * suave;
      n.destello = Math.max(0, n.destello - dt * 1.5);
      const h = 0.05 + n.uso * 2.2;
      n.barra.scale.y = h;
      n.barra.position.y = 0.2 + h / 2;
      n.material.emissiveIntensity = n.uso * 1.6 + n.destello * 0.6;
      n.material.color.setHSL(0.55 - n.uso * 0.48, 0.6, 0.22 + n.uso * 0.2);
      n.material.emissive.setHSL(0.55 - n.uso * 0.48, 0.9, 0.5);
    }
    for (const c of this.carriles) {
      c.actividad = Math.max(0, c.actividad - dt * 1.2);
      c.material.opacity = 0.12 + c.actividad * 0.25;
    }

    this.controles.update();
    this.renderer.render(this.scene, this.camera);
    this.etiquetas.render(this.scene, this.camera);
  }
}
