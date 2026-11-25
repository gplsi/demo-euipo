// Demostrador EUIPO — lógica del frontend (sin dependencias).
const $ = (id) => document.getElementById(id);
const num = (x, d = 3) => x.toFixed(d).replace('.', ',');
const pct = (x) => num(x * 100, 1) + ' %';
// Probabilidades en porcentaje; por debajo de 0,1 % se muestra «< 0,1 %» para no enseñar 0,0 %
const prob = (x) => (x > 0 && x < 0.001 ? '< 0,1 %' : pct(x));
const etiqueta = (b) => (b ? 'Denegación absoluta' : 'Sin denegación');

let candidatos = [];
let candidatoActual = null;
// Flujo de análisis: 'clasico' (discriminativo + generativo) o 'json' (solo generativo nuevo)
let flujo = 'clasico';
let estadoServidor = null;
const guardar = (k, v) => { try { localStorage.setItem('euipo.' + k, v); } catch (e) { /* sin almacenamiento */ } };
const leer = (k) => { try { return localStorage.getItem('euipo.' + k); } catch (e) { return null; } };   // candidato elegido en el desplegable, si los campos no se han tocado

async function api(ruta, opciones) {
  const r = await fetch(ruta, opciones);
  if (!r.ok) {
    let msg = r.status + ' ' + r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* respuesta sin JSON */ }
    if (r.status === 401) mostrarClave(msg);
    throw new Error(msg);
  }
  return r.json();
}

// ---------------- API key (la misma para los dos modelos) ----------------
function mostrarClave(msg) {
  $('aviso-simulado').hidden = false;
  if (msg) { $('clave-error').textContent = msg; $('clave-error').hidden = false; }
}
$('form-clave').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const boton = $('guardar-clave');
  boton.disabled = true;
  $('clave-error').hidden = true;
  try {
    await postJSON('/api/clave', { clave: $('clave').value });
    $('clave').value = '';
    await cargarEstado();
  } catch (e) {
    mostrarClave(e.message);
  } finally {
    boton.disabled = false;
  }
});
const postJSON = (ruta, cuerpo, signal) => api(ruta, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo), signal,
});
const esDetenido = (err) => err.name === 'AbortError';

// ---------------- estado del servidor ----------------
async function cargarEstado() {
  const e = await api('/api/estado');
  $('chip-disc').textContent = 'Discriminativo · ' + e.clasificador.nombre
    + (e.clasificador.modo === 'simulado' ? '' : ' (' + e.clasificador.modo + ')');
  $('chip-disc').classList.toggle('simulado', e.clasificador.modo === 'simulado');
  $('aviso-simulado').hidden = e.clave.origen !== null;
  if (e.clave.origen !== null) $('clave-error').hidden = true;
  const gen = e.generador.nombre ? e.generador.nombre + ' (' + e.generador.modo + ')' : e.generador.modo;
  $('chip-gen').textContent = 'Generativo · ' + gen;
  $('chip-gen').classList.toggle('simulado', e.generador.modo === 'no disponible');
  $('lbl-cand').textContent = 'Marca candidata (partición de test; el botón trae otras 10 al azar)';
  $('ejemplo').hidden = !e.lote_ejemplo;
  estadoServidor = e;
  rellenarModelos(e.modelos);
}

function rellenar(sel, items, guardado) {
  sel.textContent = '';
  for (const it of items) {
    const o = document.createElement('option');
    o.value = it.id;
    o.textContent = it.nombre;
    sel.appendChild(o);
  }
  if (guardado && items.some((it) => it.id === guardado)) sel.value = guardado;
}

function rellenarModelos(m) {
  rellenar($('sel-disc'), m.discriminativos, leer('disc'));
  rellenar($('sel-gen'), m.generativos_json, leer('gen'));
  // El selector de flujo solo aparece si hay generativos JSON configurados
  $('bloque-flujo').hidden = !m.generativos_json.length;
  if (!m.generativos_json.length) flujo = 'clasico';
  else if (leer('flujo') === 'json') flujo = 'json';
  aplicarFlujo();
}

function aplicarFlujo() {
  const json = flujo === 'json';
  for (const b of document.querySelectorAll('[data-flujo]')) b.classList.toggle('activo', b.dataset.flujo === flujo);
  $('sel-disc-bloque').hidden = json || $('sel-disc').options.length < 2;
  $('sel-gen-bloque').hidden = !json;
  const g = estadoServidor && estadoServidor.modelos.generativos_json.find((x) => x.id === $('sel-gen').value);
  $('gen-cfg').textContent = g
    ? 'Minúsculas: ' + (g.lowercase ? 'sí' : 'no') + ' · Número de clase: ' + (g.include_class_number ? 'sí' : 'no')
    : '';
  $('entrada-resumen').textContent = json
    ? 'Ver la entrada que recibe el modelo generativo'
    : 'Ver la entrada que recibe el modelo discriminativo';
  $('res-vacio').innerHTML = json
    ? 'Introduce una marca y pulsa <strong>Analizar</strong>. El modelo generativo decide si hay denegación absoluta y, en ese caso, explica el motivo en una sola respuesta JSON.'
    : 'Introduce una marca y pulsa <strong>Analizar</strong>. Primero decide el modelo discriminativo; si hay denegación absoluta, el generativo explica el motivo.';
  $('chip-disc').hidden = json;
  if (json && g) {
    $('chip-gen').textContent = 'Generativo JSON · ' + g.nombre + ' (remoto)';
    $('chip-gen').classList.remove('simulado');
  } else if (estadoServidor) {
    const e = estadoServidor;
    const gen = e.generador.nombre ? e.generador.nombre + ' (' + e.generador.modo + ')' : e.generador.modo;
    $('chip-gen').textContent = 'Generativo · ' + gen;
    $('chip-gen').classList.toggle('simulado', e.generador.modo === 'no disponible');
  }
  $('entrada').textContent = entradaModelo();
}

for (const b of document.querySelectorAll('[data-flujo]')) {
  b.addEventListener('click', () => {
    flujo = b.dataset.flujo;
    guardar('flujo', flujo);
    reiniciarResultado();
    aplicarFlujo();
  });
}
$('sel-disc').addEventListener('change', () => guardar('disc', $('sel-disc').value));
$('sel-gen').addEventListener('change', () => { guardar('gen', $('sel-gen').value); aplicarFlujo(); });

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
  if (flujo === 'json') return '(el prompt exacto, con la configuración del modelo, se muestra al analizar)';
  const gs = $('gs').value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).join(' ');
  return 'text (nombre):\n' + nombre + '\n\ntext_pair (bienes y servicios):\n' + gs;
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
  for (const id of ['res-cargando', 'res-error', 'res-detenido', 'res-veredicto', 'res-explicacion', 'res-no-gen', 'res-real', 'res-json']) $(id).hidden = true;
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
    discriminativo: $('sel-disc').value || null, modelo: $('sel-gen').value || null,
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
    if (flujo === 'json') {
      $('res-paso').textContent = 'Analizando con el modelo generativo…';
      await analizarJSON(cuerpo, signal, real);
      return;
    }
    const c = await postJSON('/api/clasificar', cuerpo, signal);
    $('res-cargando').hidden = true;
    $('entrada').textContent = 'text (nombre):\n' + c.entrada_par.text
      + '\n\ntext_pair (bienes y servicios):\n' + c.entrada_par.text_pair;
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

// Flujo «solo generativo»: una llamada que devuelve el JSON con veredicto, tipo y explicación
async function analizarJSON(cuerpo, signal, real) {
  const r = await postJSON('/api/analizar-json', cuerpo, signal);
  $('res-cargando').hidden = true;
  $('res-json').hidden = false;
  $('entrada').textContent = r.prompt;
  $('rj-crudo').textContent = r.crudo;
  const s = r.salida;
  $('rj-error').hidden = !r.error_formato;
  if (r.error_formato) $('rj-error').textContent = r.error_formato + '. Revisa la salida cruda.';
  $('rj-det').open = !!r.error_formato;
  $('rj-veredicto').hidden = !s;
  $('rj-expl').hidden = true;
  if (s) {
    $('rj-veredicto').classList.toggle('deneg', s.absolute_refusal);
    $('rj-texto').textContent = s.absolute_refusal ? 'Denegación absoluta' : 'Sin motivo de denegación absoluta';
    if (s.absolute_refusal) {
      $('rj-expl').hidden = false;
      const t = s.refusal_type ? String(s.refusal_type) : '';
      $('rj-tipo').textContent = t ? t.charAt(0).toUpperCase() + t.slice(1) : '—';
      $('rj-resumen').textContent = s.summary_explanation || '';
    }
    if (real !== null) {
      $('res-real').hidden = false;
      $('res-real').innerHTML = '<span>Etiqueta real: <strong></strong></span><span></span>';
      $('res-real').querySelector('strong').textContent = etiqueta(real);
      $('res-real').lastChild.textContent = real === s.absolute_refusal ? 'La predicción coincide' : 'La predicción no coincide';
    }
  }
  $('rj-meta').textContent = 'Modelo ' + r.modelo.nombre + ' · minúsculas: ' + (r.modelo.lowercase ? 'sí' : 'no')
    + ' · número de clase: ' + (r.modelo.include_class_number ? 'sí' : 'no') + ' · ' + num(r.segundos, 1) + ' s';
}

// ---------------- lotes ----------------
let sondeo = null;
let loteActual = null;

$('detener-lote').addEventListener('click', async () => {
  if (!loteActual) return;
  $('detener-lote').disabled = true;
  $('lote-prog-texto').textContent = 'Deteniendo…';
  try { await api('/api/lote/' + loteActual + '/detener', { method: 'POST' }); } catch (e) { /* ya había terminado */ }
});

// Discriminativo elegido en la pestaña de análisis, para que el lote use el mismo modelo
const qDisc = () => ($('sel-disc').value ? '?discriminativo=' + encodeURIComponent($('sel-disc').value) : '');

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
  iniciarLote(api('/api/lote' + qDisc(), { method: 'POST', body: fd }));
  ev.target.value = '';
});
$('ejemplo').addEventListener('click', () => iniciarLote(api('/api/lote/ejemplo' + qDisc(), { method: 'POST' })));

// ---------------- cabecera compacta al hacer scroll ----------------
(function () {
  const cab = document.querySelector('.cabecera');
  const raiz = document.documentElement;
  let compacta = false, pendiente = false;
  // Histéresis: compacta al bajar de 70 px y se expande al volver casi arriba, para no parpadear
  const actualizar = () => {
    pendiente = false;
    const y = window.scrollY;
    if (!compacta && y > 70) compacta = true;
    else if (compacta && y < 10) compacta = false;
    cab.classList.toggle('compacta', compacta);
  };
  window.addEventListener('scroll', () => {
    if (!pendiente) { pendiente = true; requestAnimationFrame(actualizar); }
  }, { passive: true });
  // El hueco reserva la altura expandida; la tarjeta lateral fija se coloca justo debajo de la cabecera
  const hueco = $('cab-hueco');
  const medir = () => {
    raiz.style.setProperty('--alto-cab', cab.offsetHeight + 'px');
    if (!compacta) hueco.style.height = cab.offsetHeight + 'px';
  };
  if ('ResizeObserver' in window) new ResizeObserver(medir).observe(cab);
  medir();
  actualizar();
})();

// ---------------- arranque ----------------
alCambiarCampos();
cargarEstado().catch((e) => console.error(e));
cargarCandidatos().catch((e) => console.error(e));
