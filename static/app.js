// Demostrador EUIPO — lógica del frontend (sin dependencias).
const $ = (id) => document.getElementById(id);
const num = (x, d = 3) => x.toFixed(d).replace('.', ',');
const pct = (x) => num(x * 100, 1) + ' %';
// Probabilidades en porcentaje; por debajo de 0,1 % se muestra «< 0,1 %» para no enseñar 0,0 %
const prob = (x) => (x > 0 && x < 0.001 ? '< 0,1 %' : pct(x));
const etiqueta = (b) => (b ? 'Denegación absoluta' : 'Sin denegación');

let candidatos = [];
let candidatoActual = null;   // candidato elegido en el desplegable, si los campos no se han tocado

async function api(ruta, opciones) {
  const r = await fetch(ruta, opciones);
  if (!r.ok) {
    let msg = r.status + ' ' + r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* respuesta sin JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
const postJSON = (ruta, cuerpo, signal) => api(ruta, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo), signal,
});
const esDetenido = (err) => err.name === 'AbortError';

// ---------------- estado del servidor ----------------
async function cargarEstado() {
  const e = await api('/api/estado');
  $('chip-disc').textContent = 'Discriminativo · ' + e.clasificador.nombre;
  $('chip-disc').classList.toggle('simulado', e.clasificador.modo === 'simulado');
  $('aviso-simulado').hidden = e.clasificador.modo !== 'simulado';
  const gen = e.generador.nombre ? e.generador.nombre + ' (' + e.generador.modo + ')' : e.generador.modo;
  $('chip-gen').textContent = 'Generativo · ' + gen;
  $('chip-gen').classList.toggle('simulado', e.generador.modo === 'no disponible');
  $('lbl-cand').textContent = 'Marca candidata (partición de test; el botón trae otras 10 al azar)';
  $('ejemplo').hidden = !e.lote_ejemplo;
}

async function cargarCandidatos(aleatorio = false) {
  candidatos = await api('/api/candidatos' + (aleatorio ? '?aleatorio=1' : ''));
  const sel = $('cand');
  sel.length = 1;  // deja solo «Selecciona una marca…»
  for (const c of candidatos) {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = c.name;
    sel.appendChild(o);
  }
  $('otras').hidden = !candidatos.length;
  if (!candidatos.length) {
    $('cand-info').hidden = false;
    $('cand-info').textContent = 'No hay candidatos: arranca el servidor con --test test.jsonl.';
  }
}

$('otras').addEventListener('click', async () => {
  $('otras').disabled = true;
  try {
    await cargarCandidatos(true);
    $('cand').value = '';
    $('cand').dispatchEvent(new Event('change'));
  } finally {
    $('otras').disabled = false;
  }
});

// ---------------- pestañas y modos ----------------
document.querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.pestana').forEach((x) => x.classList.toggle('activa', x === b));
  $('tab-una').hidden = b.dataset.tab !== 'una';
  $('tab-lote').hidden = b.dataset.tab !== 'lote';
}));

document.querySelectorAll('.segmentado button').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.segmentado button').forEach((x) => x.classList.toggle('activo', x === b));
  $('modo-pick').hidden = b.dataset.modo !== 'pick';
  $('modo-txt').hidden = b.dataset.modo !== 'txt';
  if (b.dataset.modo === 'write') $('nm').focus();
}));

// ---------------- entrada ----------------
function entradaModelo() {
  const nombre = $('nm').value.trim();
  if (!nombre) return '(vacío)';
  const gs = $('gs').value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).join(' ');
  return nombre + ': ' + gs;
}

function alCambiarCampos() {
  $('analizar').disabled = !$('nm').value.trim();
  $('entrada').textContent = entradaModelo();
  if (candidatoActual && ($('nm').value !== candidatoActual.name || $('gs').value !== candidatoActual.goods)) {
    candidatoActual = null;
  }
  reiniciarResultado();
}
$('nm').addEventListener('input', alCambiarCampos);
$('gs').addEventListener('input', alCambiarCampos);

$('cand').addEventListener('change', (ev) => {
  const c = candidatos.find((x) => x.id === ev.target.value) || null;
  $('nm').value = c ? c.name : '';
  $('gs').value = c ? c.goods : '';
  alCambiarCampos();
  candidatoActual = c;
  $('cand-info').hidden = !c;
  if (c) $('cand-info').innerHTML = 'Etiqueta real en el dataset: <strong></strong> · ID ' + c.id;
  if (c) $('cand-info').querySelector('strong').textContent = etiqueta(c.absolute_refusal);
});

$('txt').addEventListener('change', async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const lineas = (await f.text()).replace(/^﻿/, '').split(/\r?\n/);
  $('nm').value = (lineas.shift() || '').trim();
  $('gs').value = lineas.map((l) => l.trim()).filter(Boolean).join('\n');
  $('cand').value = '';
  $('cand-info').hidden = true;
  alCambiarCampos();
  $('txt-info').hidden = false;
  $('txt-info').textContent = 'Cargado: ' + f.name;
});

$('limpiar').addEventListener('click', () => {
  $('nm').value = ''; $('gs').value = ''; $('cand').value = ''; $('txt').value = '';
  $('cand-info').hidden = true; $('txt-info').hidden = true;
  alCambiarCampos();
});

// ---------------- análisis de una marca ----------------
function reiniciarResultado() {
  $('res-vacio').hidden = false;
  for (const id of ['res-cargando', 'res-error', 'res-detenido', 'res-veredicto', 'res-explicacion', 'res-no-gen', 'res-real']) $(id).hidden = true;
}

function mostrarError(id, err) {
  $(id).hidden = false;
  $(id).textContent = 'Error: ' + err.message;
}

let analisis = null;  // { ctrl, pid } del análisis en curso, para poder detenerlo

$('detener').addEventListener('click', () => {
  if (!analisis) return;
  $('detener').disabled = true;
  analisis.ctrl.abort();
  // El servidor también corta la generación del modelo generativo
  fetch('/api/detener/' + analisis.pid, { method: 'POST' }).catch(() => {});
});

$('analizar').addEventListener('click', async () => {
  const pid = Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  analisis = { ctrl: new AbortController(), pid };
  const signal = analisis.ctrl.signal;
  const cuerpo = {
    name: $('nm').value.trim(), goods: $('gs').value,
    candidato_id: candidatoActual ? candidatoActual.id : null, peticion_id: pid,
  };
  const real = candidatoActual ? candidatoActual.absolute_refusal : null;
  reiniciarResultado();
  $('res-vacio').hidden = true;
  $('res-cargando').hidden = false;
  $('res-paso').textContent = 'Clasificando con el modelo discriminativo…';
  $('analizar').disabled = true;
  $('analizar').textContent = 'Analizando…';
  $('detener').hidden = false;
  $('detener').disabled = false;
  try {
    const c = await postJSON('/api/clasificar', cuerpo, signal);
    $('res-cargando').hidden = true;
    $('entrada').textContent = c.entrada;
    const v = $('res-veredicto');
    v.hidden = false;
    v.classList.toggle('deneg', c.denegacion);
    $('ver-texto').textContent = c.denegacion ? 'Denegación absoluta' : 'Sin motivo de denegación absoluta';
    $('ver-prob').textContent = prob(c.probabilidad);
    $('ver-umbral').textContent = '(se considera denegación a partir del ' + pct(c.umbral).replace(',0', '')
      + (c.modo === 'simulado' ? ', SIMULADO' : '') + ')';
    $('ver-barra').style.width = Math.round(c.probabilidad * 100) + '%';
    if (real !== null) {
      $('res-real').hidden = false;
      $('res-real').innerHTML = '<span>Etiqueta real: <strong></strong></span><span></span>';
      $('res-real').querySelector('strong').textContent = etiqueta(real);
      $('res-real').lastChild.textContent = real === c.denegacion ? 'La predicción coincide' : 'La predicción no coincide';
    }
    if (!c.denegacion) {
      $('res-no-gen').hidden = false;
      return;
    }
    $('res-explicacion').hidden = false;
    $('exp-cargando').hidden = false;
    $('exp-cuerpo').hidden = true;
    try {
      const e = await postJSON('/api/explicar', cuerpo, signal);
      $('exp-cargando').hidden = true;
      $('exp-cuerpo').hidden = false;
      $('exp-tipo').textContent = e.tipo ? e.tipo.charAt(0).toUpperCase() + e.tipo.slice(1) : '—';
      $('exp-texto').textContent = e.explicacion;
      $('exp-fuente').textContent = e.fuente === 'precalculada'
        ? 'Salida precalculada del modelo (predictions.jsonl).'
        : 'Generada por el modelo en ' + num(e.segundos, 1) + ' s.';
    } catch (err) {
      $('exp-cargando').hidden = true;
      if (esDetenido(err)) {
        $('res-explicacion').hidden = true;
        $('res-detenido').hidden = false;
        $('res-detenido').textContent = 'Explicación detenida: el modelo generativo no ha terminado.';
      } else {
        mostrarError('res-error', err);
      }
    }
  } catch (err) {
    $('res-cargando').hidden = true;
    if (esDetenido(err)) {
      $('res-detenido').hidden = false;
      $('res-detenido').textContent = 'Análisis detenido.';
    } else {
      mostrarError('res-error', err);
    }
  } finally {
    analisis = null;
    $('detener').hidden = true;
    $('analizar').textContent = 'Analizar';
    $('analizar').disabled = !$('nm').value.trim();
  }
});

// ---------------- lotes ----------------
let sondeo = null;
let loteActual = null;

$('detener-lote').addEventListener('click', async () => {
  if (!loteActual) return;
  $('detener-lote').disabled = true;
  $('lote-prog-texto').textContent = 'Deteniendo…';
  try { await api('/api/lote/' + loteActual + '/detener', { method: 'POST' }); } catch (e) { /* ya había terminado */ }
});

async function iniciarLote(promesa) {
  clearTimeout(sondeo);
  $('lote-vacio').hidden = true;
  $('lote-res').hidden = true;
  $('lote-error').hidden = true;
  $('lote-progreso').hidden = false;
  $('lote-prog-texto').textContent = 'Subiendo…';
  $('lote-prog-barra').style.width = '0%';
  $('detener-lote').hidden = true;
  try {
    const { id } = await promesa;
    loteActual = id;
    $('detener-lote').hidden = false;
    $('detener-lote').disabled = false;
    seguirLote(id);
  } catch (err) {
    $('lote-progreso').hidden = true;
    mostrarError('lote-error', err);
  }
}

async function seguirLote(id) {
  try {
    const t = await api('/api/lote/' + id);
    if (t.estado === 'en curso') {
      const p = t.total ? t.hechas / t.total : 0;
      if (!t.detener) $('lote-prog-texto').textContent = 'Clasificando ' + t.archivo + ': ' + t.hechas + ' de ' + t.total + ' marcas…';
      $('lote-prog-barra').style.width = Math.round(p * 100) + '%';
      sondeo = setTimeout(() => seguirLote(id), 700);
      return;
    }
    $('lote-progreso').hidden = true;
    loteActual = null;
    if (t.estado === 'error') throw new Error(t.error);
    pintarLote(id, t);
  } catch (err) {
    $('lote-progreso').hidden = true;
    mostrarError('lote-error', err);
  }
}

function barraClase(titulo, aciertos, fallos) {
  const n = aciertos + fallos;
  const d = document.createElement('div');
  d.className = 'barra-clase';
  d.innerHTML = '<div class="cab"><span></span><span></span></div><div class="barra-apilada" role="img"><div class="a"></div><div class="f"></div></div>';
  d.querySelector('.cab span:first-child').textContent = titulo + ' (' + n + ')';
  d.querySelector('.cab span:last-child').textContent = aciertos + ' aciertos · ' + fallos + (fallos === 1 ? ' fallo' : ' fallos');
  d.querySelector('.barra-apilada').setAttribute('aria-label', titulo + ': ' + aciertos + ' aciertos y ' + fallos + ' fallos');
  d.querySelector('.a').style.width = n ? (aciertos / n * 100) + '%' : '0';
  const f = d.querySelector('.f');
  f.style.flex = '1';
  f.style.minWidth = fallos ? '4px' : '0';
  if (!fallos) f.hidden = true;
  return d;
}

function pintarLote(id, t) {
  const r = t.resultado;
  $('lote-res').hidden = false;
  $('lote-archivo').innerHTML = 'Archivo: <strong class="mono"></strong> · ' + r.total + ' marcas (' + (r.tp + r.fn) + ' con denegación absoluta)'
    + (t.descartadas ? ' · ' + t.descartadas + ' líneas descartadas por formato' : '')
    + (t.detenido ? ' · <strong>detenido</strong>: resultados de las ' + r.total + ' marcas clasificadas de ' + t.total : '');
  $('lote-archivo').querySelector('strong').textContent = t.archivo;
  $('k-total').textContent = r.total;
  $('k-aciertos').innerHTML = r.aciertos + ' <small>' + pct(r.accuracy) + '</small>';
  $('k-fallos').innerHTML = r.fallos + ' <small>' + pct(r.total ? r.fallos / r.total : 0) + '</small>';
  $('k-f1').textContent = num(r.f1, 2);
  const barras = $('barras');
  barras.innerHTML = '';
  barras.appendChild(barraClase('Denegación absoluta', r.tp, r.fn));
  barras.appendChild(barraClase('Sin denegación', r.tn, r.fp));
  barras.appendChild(barraClase('Total', r.aciertos, r.fallos));
  $('m-tp').textContent = r.tp; $('m-fn').textContent = r.fn;
  $('m-fp').textContent = r.fp; $('m-tn').textContent = r.tn;
  $('m-metricas').textContent = 'Precisión ' + num(r.precision, 2) + ' · Exhaustividad ' + num(r.recall, 2) + ' (clase denegación)';
  $('h-fallos').textContent = 'Marcas con fallo (' + r.fallos + ')';
  $('csv').href = '/api/lote/' + id + '/csv';
  const tb = $('fallos');
  tb.innerHTML = '';
  for (const f of r.fallos_lista) {
    const tr = document.createElement('tr');
    const celdas = [f.name, etiqueta(f.real), etiqueta(f.pred), prob(f.prob),
      f.real ? 'Falso negativo' : 'Falso positivo'];
    celdas.forEach((txt, i) => {
      const td = document.createElement('td');
      td.textContent = txt;
      if (i === 3) td.className = 'mono';
      if (i === 4) td.className = 'err';
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  }
  $('fallos-nota').textContent = r.fallos_truncados ? 'Se muestran los 500 fallos más cercanos al umbral; el CSV tiene todos.'
    : (r.fallos ? 'Ordenados de más dudoso a menos (probabilidad más cercana al umbral).' : 'Sin fallos.');
}

$('jsonl').addEventListener('change', (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('archivo', f);
  iniciarLote(api('/api/lote', { method: 'POST', body: fd }));
  ev.target.value = '';
});
$('ejemplo').addEventListener('click', () => iniciarLote(api('/api/lote/ejemplo', { method: 'POST' })));

// ---------------- arranque ----------------
alCambiarCampos();
cargarEstado().catch((e) => console.error(e));
cargarCandidatos().catch((e) => console.error(e));
