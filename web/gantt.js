// Línea de tiempo (Gantt) en vivo: una fila por solicitud.
// Gris = esperando en cola · color = trabajador que la atiende · marcas = eventos de progreso.
import { PALETA } from './escena.js';

export class Gantt {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.filas = [];
    this.ahora = 0;
    this.escala = 1;
    new ResizeObserver(() => this.dibujar()).observe(canvas);
  }

  reiniciar(solicitudes) {
    this.filas = solicitudes.map((s) => ({ s, inicio: null, fin: null, carril: null, marcas: [], etapas: [] }));
    this.ahora = 0;
    this.escala = 1;
  }

  _fila(id) { return this.filas.find((f) => f.s.id === id); }

  inicio(ev, carril) { const f = this._fila(ev.id); if (f) { f.inicio = ev.t; f.carril = carril; } }
  progreso(ev) { this._fila(ev.id)?.marcas.push(ev.t); }
  etapa(ev) { this._fila(ev.id)?.etapas.push({ nombre: ev.etapa, t: ev.t }); }
  fin(ev) { const f = this._fila(ev.id); if (f) f.fin = ev.t; }
  tiempo(t) { this.ahora = Math.max(this.ahora, t); }

  dibujar() {
    const { canvas, ctx } = this;
    const dpr = Math.min(devicePixelRatio, 2);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const css = getComputedStyle(document.documentElement);
    const tinta = css.getPropertyValue('--tinta').trim();
    const suave = css.getPropertyValue('--tinta-suave').trim();
    const linea = css.getPropertyValue('--linea').trim();

    const izq = 96, der = 14, arriba = 24, abajo = 6;
    const anchoUtil = w - izq - der;
    const n = Math.max(this.filas.length, 10);
    const alto = (h - arriba - abajo) / n;

    // La escala crece suavemente con el tiempo transcurrido
    const objetivo = Math.max(1, this.ahora * 1.12);
    this.escala += (objetivo - this.escala) * 0.15;
    const X = (t) => izq + (t / this.escala) * anchoUtil;

    // Eje de tiempo
    const pasos = [0.1, 0.25, 0.5, 1, 2, 5, 10, 20];
    const paso = pasos.find((p) => this.escala / p <= 8) ?? 50;
    ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'center';
    for (let t = 0; t <= this.escala + 1e-9; t += paso) {
      const x = X(t);
      ctx.strokeStyle = linea; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, arriba - 4); ctx.lineTo(x, h - abajo); ctx.stroke();
      ctx.fillStyle = suave;
      ctx.fillText(`${+t.toFixed(2)} s`, x, 13);
    }

    this.filas.forEach((f, i) => {
      const y = arriba + i * alto;
      const yc = y + alto / 2;
      const bh = Math.max(4, alto * 0.62);
      ctx.textAlign = 'left';
      ctx.fillStyle = tinta;
      ctx.font = '600 11.5px system-ui, sans-serif';
      ctx.fillText(`#${f.s.id}`, 8, yc + 4);
      ctx.fillStyle = suave;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillText(`${f.s.usuario} · ${f.s.trabajo === 'io' ? 'E/S' : 'CPU'}`, 30, yc + 4);

      const finCola = f.inicio ?? this.ahora;
      ctx.fillStyle = 'rgba(148,163,184,0.18)';
      ctx.fillRect(X(0), yc - bh / 2, Math.max(0, X(finCola) - X(0)), bh);
      if (f.inicio != null) {
        const color = PALETA[f.carril % PALETA.length];
        const hasta = f.fin ?? this.ahora;
        if (!f.etapas.length) {
          ctx.fillStyle = color;
          ctx.globalAlpha = f.fin != null ? 0.95 : 0.7;
          ctx.fillRect(X(f.inicio), yc - bh / 2, Math.max(1.5, X(hasta) - X(f.inicio)), bh);
        } else {
          // Modo tiempo real: un tramo por etapa
          f.etapas.forEach((e, k) => {
            const t1 = f.etapas[k + 1]?.t ?? hasta;
            if (e.nombre === 'caja_espera') { ctx.fillStyle = '#ef4444'; ctx.globalAlpha = 0.85; }
            else if (e.nombre === 'caja_cobro') { ctx.fillStyle = '#facc15'; ctx.globalAlpha = 0.95; }
            else if (e.nombre === 'preparar') { ctx.fillStyle = color; ctx.globalAlpha = 0.95; }
            else { ctx.fillStyle = color; ctx.globalAlpha = 0.4; }   // pedido, camino a la caja, entrega
            ctx.fillRect(X(e.t), yc - bh / 2, Math.max(1, X(t1) - X(e.t)), bh);
          });
        }
        ctx.globalAlpha = 1;
        ctx.strokeStyle = 'rgba(7,11,18,0.55)';
        for (const m of f.marcas) {
          const x = X(m);
          ctx.beginPath(); ctx.moveTo(x, yc - bh / 2); ctx.lineTo(x, yc + bh / 2); ctx.stroke();
        }
      }
    });

    // Cursor del tiempo actual
    const xc = X(this.ahora);
    ctx.strokeStyle = '#f8fafc'; ctx.globalAlpha = 0.5;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(xc, arriba - 4); ctx.lineTo(xc, h - abajo); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
}
