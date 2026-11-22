/* actdetail.js — Garmin-Connect-style activity detail: stacked charts (pace, heart rate, stamina,
 * cadence, stride, power, ...) over time or distance, a zoom view with overlays, a Stats grid and
 * Time in Zones.  Shared by activities.html and coaching.html.
 *
 * Data: GET /api/activity-detail?id=…  (written by scripts/sync_activity_streams.py).
 * Needs Chart.js 4; it is loaded on demand when the page does not already have it.
 *
 *   GSActDetail.mount(hostEl, { id, act, distUnit })
 *     act = the activities.json entry (used for HR zones and as a fallback for Stats)
 */
(function (w, d) {
  'use strict';

  var CHARTJS = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
  var CSS = [
    '.gsad{margin-top:14px}',
    '.gsad-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}',
    '.gsad-seg{display:inline-flex;border:1px solid var(--border,#334155);border-radius:6px;overflow:hidden}',
    '.gsad-seg button,.gsad-tab{background:transparent;border:0;color:var(--text-muted,var(--muted,#94a3b8));font:inherit;font-size:12px;padding:6px 12px;cursor:pointer}',
    '.gsad-seg button.on{background:#64748b;color:#fff}',
    '.gsad-read{display:flex;gap:6px 14px;flex-wrap:wrap;font-size:11px;min-height:18px;color:var(--text-muted,var(--muted,#94a3b8));margin-bottom:6px;font-variant-numeric:tabular-nums}',
    '.gsad-read b{color:var(--text,#f1f5f9);font-weight:600}',
    '.gsad-read i{font-style:normal;display:inline-flex;align-items:center;gap:5px}',
    '.gsad-dot{display:inline-block;width:9px;height:9px;border-radius:50%}',
    '.gsad-panel{margin-bottom:10px}',
    '.gsad-ph{display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:2px}',
    '.gsad-ph .avg{margin-left:auto;font-size:11px;color:var(--text-muted,var(--muted,#94a3b8))}',
    '.gsad-ph button{background:transparent;border:0;color:var(--text-muted,var(--muted,#94a3b8));cursor:pointer;font-size:15px;padding:0 4px}',
    '.gsad-ph button:hover{color:var(--text,#f1f5f9)}',
    '.gsad-cv{position:relative;height:120px}',
    '.gsad-msg{padding:14px;font-size:12px;color:var(--text-muted,var(--muted,#94a3b8));border:1px dashed var(--border,#334155);border-radius:8px}',
    '.gsad-tabs{display:flex;gap:2px;border-bottom:1px solid var(--border,#334155);margin:16px 0 12px}',
    '.gsad-tab{border-bottom:2px solid transparent}',
    '.gsad-tab.on{color:var(--text,#f1f5f9);border-bottom-color:var(--accent,#38bdf8)}',
    '.gsad-stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:4px 28px}',
    '.gsad-sec{margin-bottom:14px}',
    '.gsad-sec h4{font-size:12px;font-weight:600;color:var(--text-muted,var(--muted,#94a3b8));border-bottom:1px solid var(--border,#334155);padding-bottom:4px;margin-bottom:6px;display:flex;align-items:center;gap:8px}',
    '.gsad-sec h4 .gsad-seg button{padding:2px 8px;font-size:11px}',
    '.gsad-kv{padding:4px 0}.gsad-kv b{display:block;font-size:17px;font-weight:500;color:var(--text,#f1f5f9)}',
    '.gsad-kv span{font-size:11px;color:var(--text-muted,var(--muted,#94a3b8))}',
    '.gsad-zh{font-size:15px;margin:6px 0 10px;color:var(--text,#f1f5f9)}',
    '.gsad-z{display:grid;grid-template-columns:190px 1fr 92px;gap:10px;align-items:center;margin-bottom:8px;font-size:12px}',
    '.gsad-z em{font-style:normal;color:var(--text-muted,var(--muted,#94a3b8));font-size:11px;display:block}',
    '.gsad-zb{height:14px;background:rgba(148,163,184,.18);border-radius:3px;overflow:hidden}',
    '.gsad-zb div{height:100%}',
    '.gsad-z .t{text-align:right;font-variant-numeric:tabular-nums}',
    '.gsad-modal{position:fixed;inset:0;z-index:9999;background:var(--bg,#0f172a);display:flex;flex-direction:column;padding:14px 18px}',
    '.gsad-mh{display:flex;align-items:center;gap:10px;margin-bottom:8px}',
    '.gsad-mh h3{font-size:15px;margin-right:auto}',
    '.gsad-mh select{background:var(--surface,var(--card,#1e293b));color:var(--text,#f1f5f9);border:1px solid var(--border,#334155);border-radius:6px;padding:6px 8px;font:inherit;font-size:12px}',
    '.gsad-mh .x{background:transparent;border:0;color:var(--text,#f1f5f9);font-size:22px;cursor:pointer}',
    '.gsad-mc{position:relative;flex:1;min-height:0}',
    '.gsad-mf{display:flex;justify-content:center;padding-top:10px}',
    '@media(max-width:640px){.gsad-z{grid-template-columns:1fr 70px}.gsad-z .gsad-zb{grid-column:1/-1;order:3}}'
  ].join('\n');

  /* ── metadata ─────────────────────────────────────────────────────────────────────────────── */
  function fT(s) {
    if (s == null || !isFinite(s)) return '—';
    s = Math.round(s); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return h ? h + ':' + pad(m) + ':' + pad(x) : m + ':' + pad(x);
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fPace(s) { return s == null || !isFinite(s) ? '—' : fT(s); }
  function fN(dec) { return function (v) { return v == null ? '—' : v.toFixed(dec); }; }

  var META = {
    pace: { label: 'Pace', color: '#3b82f6', unit: '/km', fmt: fPace, rev: true },
    hr: { label: 'Heart Rate', color: '#d9382f', unit: 'bpm', fmt: fN(0), fill: true },
    elev: { label: 'Elevation', color: '#9aa5b1', unit: 'm', fmt: fN(0), fill: true },
    power: { label: 'Power', color: '#a855f7', unit: 'W', fmt: fN(0), fill: true },
    stamina: { label: 'Stamina', color: '#e8924a', unit: '%', fmt: fN(0), fill: true, min: 0, max: 100, sub: 'staminaPot' },
    staminaPot: { label: 'Stamina Potential', color: '#9aa5b1', unit: '%', fmt: fN(0), hidden: true },
    perfcond: { label: 'Performance Condition', color: '#5b9cf0', unit: '', fmt: fN(0), fill: true, step: true },
    cadence: { label: 'Run Cadence', color: '#e8924a', unit: 'spm', fmt: fN(0), dots: true },
    stride: { label: 'Stride Length', color: '#3b6fd4', unit: 'm', fmt: fN(2), dots: true },
    gct: { label: 'Ground Contact Time', color: '#14b8a6', unit: 'ms', fmt: fN(0) },
    vosc: { label: 'Vertical Oscillation', color: '#ec4899', unit: 'cm', fmt: fN(1) },
    vratio: { label: 'Vertical Ratio', color: '#f59e0b', unit: '%', fmt: fN(1) },
    resp: { label: 'Respiration Rate', color: '#22c55e', unit: 'brpm', fmt: fN(0) },
    temp: { label: 'Temperature', color: '#f97316', unit: '°C', fmt: fN(1) },
    gap: { label: 'Grade-Adjusted Pace', color: '#60a5fa', unit: '/km', fmt: fPace, rev: true, hidden: true }
  };
  var ORDER = ['pace', 'hr', 'elev', 'power', 'stamina', 'perfcond', 'cadence', 'stride', 'gct', 'vosc', 'vratio', 'resp', 'temp'];
  var PALETTE = ['#38bdf8', '#f472b6', '#a3e635', '#fbbf24', '#c084fc', '#34d399'];

  function pretty(k) { return k.replace(/([A-Z])/g, ' $1').replace(/^./, function (c) { return c.toUpperCase(); }); }

  /* ── series ───────────────────────────────────────────────────────────────────────────────── */
  function canon(key) {
    var k = key.toLowerCase();
    if (k.indexOf('stamina') >= 0) return k.indexOf('potential') >= 0 ? 'staminaPot' : 'stamina';
    if (k.indexOf('performancecondition') >= 0 || k.indexOf('perfcond') >= 0) return 'perfcond';
    return key;
  }

  function build(streams, distUnit) {
    var m = streams.m || {}, n = streams.n, ser = {}, i, k;
    for (k in m) ser[canon(k)] = m[k];
    if (ser.speed) {
      ser.pace = ser.speed.map(function (v) { return v == null ? null : (v > 0.67 ? Math.min(1000 / v, 1500) : 1500); });
    }
    if (ser.gap) ser.gap = ser.gap.map(function (v) { return v == null ? null : (v > 0.67 ? Math.min(1000 / v, 1500) : 1500); });
    if (ser.stride) {
      var nums = ser.stride.filter(function (v) { return v != null; }).sort(function (a, b) { return a - b; });
      if (nums.length && nums[nums.length >> 1] > 3) ser.stride = ser.stride.map(function (v) { return v == null ? null : v / 100; });
    }
    var names = ORDER.filter(function (x) { return ser[x]; });
    var extra = Object.keys(ser).filter(function (x) { return !META[x] && x !== 'speed' && x !== 'gap'; });
    extra.forEach(function (x, j) { META[x] = META[x] || { label: pretty(x), color: PALETTE[j % PALETTE.length], unit: '', fmt: fN(1) }; });
    names = names.concat(extra);
    return { n: n, ser: ser, names: names, t: streams.t, dist: streams.d, mi: distUnit === 'mi' };
  }

  function xs(B, mode) {
    if (mode === 'dist' && B.dist) return B.dist.map(function (v) { return v == null ? null : v / (B.mi ? 1609.344 : 1000); });
    return B.t;
  }
  function mean(a) {
    var s = 0, c = 0; for (var i = 0; i < a.length; i++) if (a[i] != null) { s += a[i]; c++; }
    return c ? s / c : null;
  }
  function quant(a, q) {
    var b = a.filter(function (v) { return v != null; }).sort(function (x, y) { return x - y; });
    return b.length ? b[Math.min(b.length - 1, Math.floor(q * b.length))] : null;
  }
  function avgOf(B, name) {
    if (name === 'pace') { var sp = B.ser.speed; var mv = sp && sp.filter(function (v) { return v > 0.67; }); return mv && mv.length ? 1000 / mean(mv) : null; }
    return mean(B.ser[name]);
  }
  function points(x, y) {
    var out = [];
    for (var i = 0; i < x.length; i++) if (x[i] != null) out.push({ x: x[i], y: y[i] });
    return out;
  }
  function yRange(meta, arr) {
    var lo = meta.min, hi = meta.max;
    if (meta.rev) { lo = Math.max(150, (quant(arr, 0.02) || 300) * 0.92); hi = Math.min(1500, (quant(arr, 0.97) || 600) * 1.12); }
    return { min: lo, max: hi };
  }

  /* ── chart helpers ────────────────────────────────────────────────────────────────────────── */
  var sync = { x: null, charts: [], onMove: null };
  var cross = {
    id: 'gsadCross',
    afterDatasetsDraw: function (c) {
      if (sync.x == null || !c.scales.x) return;
      var px = c.scales.x.getPixelForValue(sync.x), a = c.chartArea;
      if (px < a.left || px > a.right) return;
      var g = c.ctx; g.save(); g.strokeStyle = 'rgba(241,245,249,.55)'; g.lineWidth = 1;
      g.beginPath(); g.moveTo(px, a.top); g.lineTo(px, a.bottom); g.stroke(); g.restore();
    }
  };
  function fmtTick(mode, B) {
    return function (v) { return mode === 'dist' ? (+v.toFixed(1)) + (B.mi ? ' mi' : ' km') : fT(v); };
  }
  function bindHover(chart, xarr, B, mode) {
    var cv = chart.canvas;
    function at(e) {
      var r = cv.getBoundingClientRect(), cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      var v = chart.scales.x.getValueForPixel(cx);
      sync.x = v;
      sync.charts.forEach(function (c) { c.draw(); });
      if (sync.onMove) sync.onMove(v, xarr);
    }
    function out() { sync.x = null; sync.charts.forEach(function (c) { c.draw(); }); if (sync.onMove) sync.onMove(null); }
    cv.addEventListener('mousemove', at); cv.addEventListener('touchmove', at, { passive: true });
    cv.addEventListener('mouseleave', out); cv.addEventListener('touchend', out);
  }
  function baseOpts(mode, B, extraScales) {
    var muted = '#94a3b8', xa = xs(B, mode).filter(function (v) { return v != null; });
    var scales = { x: { type: 'linear', min: xa[0], max: xa[xa.length - 1], ticks: { color: muted, maxTicksLimit: 8, callback: fmtTick(mode, B) }, grid: { display: false } } };
    for (var k in extraScales) scales[k] = extraScales[k];
    return {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false, normalized: true,
      interaction: { mode: 'nearest', intersect: false, axis: 'x' },
      plugins: { legend: { display: false }, tooltip: { enabled: false }, decimation: { enabled: false } },
      elements: { point: { radius: 0 } }, scales: scales
    };
  }
  function dataset(name, B, mode, opt) {
    var meta = META[name], x = xs(B, mode), y = B.ser[name];
    var col = (opt && opt.color) || meta.color;
    var fill = meta.fill && !(opt && opt.noFill);
    return {
      label: meta.label, data: points(x, y), yAxisID: (opt && opt.axis) || 'y', borderColor: col,
      backgroundColor: fill ? (opt && opt.fillColor) || col + (name === 'hr' ? 'cc' : '99') : col,
      borderWidth: meta.dots ? 0 : 1.4, showLine: !meta.dots, pointRadius: meta.dots ? 1.6 : 0,
      fill: fill ? 'origin' : false, stepped: meta.step ? 'before' : false, spanGaps: false, tension: 0
    };
  }
  function yScale(name, B, pos, grid, color) {
    var meta = META[name], r = yRange(meta, B.ser[name]);
    var s = {
      afterFit: function (a) { a.width = 46; },
      position: pos, reverse: !!meta.rev, grid: { color: 'rgba(148,163,184,.16)', drawOnChartArea: grid },
      ticks: { color: color || meta.color, maxTicksLimit: 5, callback: function (v) { return meta.fmt(v); } }
    };
    if (r.min != null) s.min = r.min; if (r.max != null) s.max = r.max;
    return s;
  }

  function panel(host, B, name, mode, onZoom) {
    var meta = META[name], wrap = d.createElement('div'); wrap.className = 'gsad-panel';
    var avg = avgOf(B, name);
    wrap.innerHTML = '<div class="gsad-ph"><span class="gsad-dot" style="background:' + meta.color + '"></span>' + meta.label +
      (meta.sub ? ' <span style="color:#9aa5b1">· ' + META[meta.sub].label + '</span>' : '') +
      '<span class="avg">' + (avg != null && name !== 'stamina' && name !== 'perfcond' ? 'Avg: ' + meta.fmt(avg) + ' ' + meta.unit : '') + '</span>' +
      '<button title="Perbesar" aria-label="Perbesar">⤢</button></div><div class="gsad-cv"><canvas></canvas></div>';
    host.appendChild(wrap);
    wrap.querySelector('button').onclick = function () { onZoom(name); };
    var ds = [dataset(name, B, mode)];
    if (meta.sub && B.ser[meta.sub]) ds.push(dataset(meta.sub, B, mode, { axis: 'y', color: '#9aa5b1' }));
    if (avg != null && name !== 'stamina' && name !== 'perfcond') {
      var x = xs(B, mode), xv = x.filter(function (v) { return v != null; });
      ds.push({ data: [{ x: xv[0], y: avg }, { x: xv[xv.length - 1], y: avg }], borderColor: '#e2e8f0', borderDash: [5, 4], borderWidth: 1, pointRadius: 0, yAxisID: 'y', fill: false });
    }
    var c = new w.Chart(wrap.querySelector('canvas'), { type: 'line', data: { datasets: ds }, plugins: [cross], options: baseOpts(mode, B, { y: yScale(name, B, 'left', true) }) });
    sync.charts.push(c); bindHover(c, xs(B, mode), B, mode);
    return c;
  }

  /* ── zoom (single metric + up to two overlays) ────────────────────────────────────────────── */
  function zoom(B, name, mode0) {
    var mode = mode0, ov = [null, null], chart = null;
    var first = name === 'pace' ? 'hr' : 'pace';
    ov[0] = B.ser[first] ? first : null;
    var el = d.createElement('div'); el.className = 'gsad-modal';
    function opts(sel, idx) {
      return '<option value="">No Overlay</option>' + B.names.filter(function (n) { return n !== name; }).map(function (n) {
        return '<option value="' + n + '"' + (ov[idx] === n ? ' selected' : '') + '>' + META[n].label + '</option>';
      }).join('');
    }
    el.innerHTML = '<div class="gsad-mh"><h3>Zoom</h3><select data-i="0">' + opts(0, 0) + '</select><select data-i="1">' + opts(1, 1) +
      '</select><button class="x" aria-label="Tutup">×</button></div><div class="gsad-mc"><canvas></canvas></div>' +
      '<div class="gsad-mf"><div class="gsad-seg"><button data-m="time">Over Time</button><button data-m="dist">Over Distance</button></div></div>';
    d.body.appendChild(el);
    var prevOverflow = d.body.style.overflow; d.body.style.overflow = 'hidden';
    function close() { if (chart) chart.destroy(); el.remove(); d.body.style.overflow = prevOverflow; d.removeEventListener('keydown', key); }
    function key(e) { if (e.key === 'Escape') close(); }
    d.addEventListener('keydown', key);
    el.querySelector('.x').onclick = close;

    function draw() {
      if (chart) chart.destroy();
      var active = ov.filter(Boolean), meta = META[name], ds = [], sc = {};
      var grey = active.length > 0;
      ds.push(dataset(name, B, mode, grey ? { color: '#9ca3af', fillColor: 'rgba(148,163,184,.18)' } : {}));
      if (meta.sub && B.ser[meta.sub] && !grey) ds.push(dataset(meta.sub, B, mode, { color: '#9aa5b1' }));
      sc.y = yScale(name, B, 'left', true, grey ? '#9ca3af' : meta.color);
      active.forEach(function (n, i) {
        var m2 = META[n], id = 'y' + (i + 1);
        ds.push(dataset(n, B, mode, { axis: id, noFill: true }));
        sc[id] = yScale(n, B, 'right', false); sc[id].grid.drawOnChartArea = false;
      });
      chart = new w.Chart(el.querySelector('canvas'), { type: 'line', data: { datasets: ds }, options: baseOpts(mode, B, sc) });
      el.querySelectorAll('.gsad-seg button').forEach(function (b) { b.classList.toggle('on', b.dataset.m === mode); });
      el.querySelector('h3').innerHTML = '<span class="gsad-dot" style="background:' + (grey ? '#9ca3af' : meta.color) + '"></span> ' + meta.label +
        active.map(function (n) { return ' <span style="color:' + META[n].color + ';font-size:12px">● ' + META[n].label + '</span>'; }).join('');
    }
    el.querySelectorAll('select').forEach(function (s) { s.onchange = function () { ov[+s.dataset.i] = s.value || null; draw(); }; });
    el.querySelectorAll('.gsad-seg button').forEach(function (b) {
      if (b.dataset.m === 'dist' && !B.dist) { b.disabled = true; b.style.opacity = .4; }
      b.onclick = function () { if (!b.disabled) { mode = b.dataset.m; draw(); } };
    });
    draw();
  }

  /* ── stats ────────────────────────────────────────────────────────────────────────────────── */
  function first(S, keys) {
    for (var i = 0; i < keys.length; i++) { var v = S[keys[i]]; if (v != null && v !== '' && !(typeof v === 'number' && !isFinite(v))) return v; }
    return null;
  }
  var FEEL = { 0: 'Very Weak', 25: 'Weak', 50: 'Normal', 75: 'Strong', 100: 'Very Strong' };
  var RPE = ['', 'Very Light', 'Light', 'Moderate', 'Somewhat Hard', 'Hard', 'Hard', 'Very Hard', 'Very Hard', 'Extremely Hard', 'Max'];

  function statsSections(S, o) {
    var mi = o.mi, dk = mi ? 1609.344 : 1000, du = mi ? 'mi' : 'km', pk = mi ? 1.609344 : 1;
    function K(keys, f, unit, label) { return { keys: keys, f: f, unit: unit, label: label }; }
    function dist(v) { return (v / dk).toFixed(2); }
    function pace(v) { return v > 0 ? fT(1000 * pk / v) : null; }
    function spd(v) { return (v * 3.6 / pk).toFixed(1); }
    var n0 = function (v) { return Math.round(v); }, n1 = function (v) { return (+v).toFixed(1); };
    var cal = first(S, ['calories']), bmr = first(S, ['bmrCalories']);
    var mod = first(S, ['moderateIntensityMinutes']), vig = first(S, ['vigorousIntensityMinutes']);
    var feel = first(S, ['directWorkoutFeel']), rpe = first(S, ['directWorkoutRpe']);
    var sections = [
      ['Distance', [K(['distance'], dist, du, 'Distance')]],
      ['Timing', [K(['duration', 'elapsedDuration'], fT, '', 'Time'), K(['movingDuration'], fT, '', 'Moving Time'), K(['elapsedDuration'], fT, '', 'Elapsed Time')]],
      ['Pace/Speed', 'PACE'],
      ['Heart Rate', [K(['averageHR'], n0, 'bpm', 'Avg HR'), K(['maxHR'], n0, 'bpm', 'Max HR'), K(['minHR'], n0, 'bpm', 'Min HR')]],
      ['Power', [K(['averagePower', 'avgPower'], n0, 'W', 'Avg Power'), K(['maxPower'], n0, 'W', 'Max Power'), K(['normalizedPower', 'normPower'], n0, 'W', 'Normalized Power')]],
      ['Elevation', [K(['elevationGain'], n0, 'm', 'Total Ascent'), K(['elevationLoss'], n0, 'm', 'Total Descent'), K(['minElevation'], n0, 'm', 'Min Elev'), K(['maxElevation'], n0, 'm', 'Max Elev')]],
      ['Running Dynamics', [K(['averageRunCadence'], n0, 'spm', 'Avg Run Cadence'), K(['maxRunCadence'], n0, 'spm', 'Max Run Cadence'),
        { keys: ['strideLength', 'avgStrideLength'], f: function (v) { return (v > 3 ? v / 100 : v).toFixed(2); }, unit: 'm', label: 'Avg Stride Length' },
        K(['verticalRatio', 'avgVerticalRatio'], n1, '%', 'Avg Vertical Ratio'), K(['verticalOscillation', 'avgVerticalOscillation'], n1, 'cm', 'Avg Vertical Oscillation'),
        K(['groundContactTime', 'avgGroundContactTime'], n0, 'ms', 'Avg Ground Contact Time')]],
      ['Respiration Rate', [K(['avgRespirationRate', 'averageRespirationRate'], n0, 'brpm', 'Avg Respiration Rate'), K(['minRespirationRate'], n0, 'brpm', 'Min Respiration Rate'), K(['maxRespirationRate'], n0, 'brpm', 'Max Respiration Rate')]],
      ['Temperature', [K(['averageTemperature', 'avgTemperature'], n1, '°C', 'Avg Temp'), K(['minTemperature'], n1, '°C', 'Min Temp'), K(['maxTemperature'], n1, '°C', 'Max Temp')]],
      ['Nutrition & Hydration', [
        { v: bmr, f: n0, label: 'Resting Calories' },
        { v: cal != null && bmr != null ? cal - bmr : null, f: n0, label: 'Active Calories' },
        K(['calories'], n0, '', 'Total Calories Burned'), K(['waterEstimated'], n0, 'ml', 'Est. Sweat Loss')]],
      ['Training Effect', [
        { v: first(S, ['trainingEffectLabel']), f: function (v) { return String(v).replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }, label: 'Primary Benefit' },
        K(['trainingEffect'], n1, '', 'Aerobic'), K(['anaerobicTrainingEffect'], n1, '', 'Anaerobic'), K(['activityTrainingLoad'], n0, '', 'Exercise Load')]],
      ['Stamina', [K(['beginPotentialStamina'], n0, '%', 'Beginning Potential'), K(['endPotentialStamina'], n0, '%', 'Ending Potential'), K(['minAvailableStamina'], n0, '%', 'Min Stamina')]],
      ['Execution Score', [K(['directWorkoutComplianceScore'], n0, '%', 'Execution Score')]],
      ['Self Evaluation', [
        { v: feel, f: function (v) { return FEEL[v] || v; }, label: 'How did you feel?' },
        { v: rpe, f: function (v) { var r = Math.round(v / 10); return r + '/10 ' + (RPE[r] || ''); }, label: 'Perceived Effort' }]],
      ['Intensity Minutes', [
        { v: mod, f: n0, unit: 'min', label: 'Moderate' }, { v: vig, f: n0, unit: 'min', label: 'Vigorous' },
        { v: mod != null || vig != null ? (mod || 0) + 2 * (vig || 0) : null, f: n0, unit: 'min', label: 'Total' }]],
      ['Steps', [K(['steps'], n0, '', 'Steps')]]
    ];
    function kv(r) {
      var v = r.v !== undefined ? r.v : first(S, r.keys);
      if (v == null) return '';
      var txt = r.f ? r.f(v) : v;
      if (txt == null) return '';
      return '<div class="gsad-kv"><b>' + esc(txt) + (r.unit ? ' <small style="font-weight:400;font-size:12px">' + esc(r.unit) + '</small>' : '') + '</b><span>' + esc(r.label) + '</span></div>';
    }
    return sections.map(function (sec) {
      if (sec[1] === 'PACE') {
        var u = o.paceMode === 'speed';
        var rows = u ? [K(['averageSpeed'], spd, mi ? 'mph' : 'km/h', 'Avg Speed'), K(['averageMovingSpeed'], spd, mi ? 'mph' : 'km/h', 'Avg Moving Speed'), K(['maxSpeed'], spd, mi ? 'mph' : 'km/h', 'Max Speed'), K(['avgGradeAdjustedSpeed', 'averageGradeAdjustedSpeed'], spd, mi ? 'mph' : 'km/h', 'Avg Grade-Adjusted Speed')]
          : [K(['averageSpeed'], pace, '/' + du, 'Avg Pace'), K(['averageMovingSpeed'], pace, '/' + du, 'Avg Moving Pace'), K(['maxSpeed'], pace, '/' + du, 'Best Pace'), K(['avgGradeAdjustedSpeed', 'averageGradeAdjustedSpeed'], pace, '/' + du, 'Avg Grade-Adjusted Pace')];
        var body = rows.map(kv).join('');
        return body ? '<div class="gsad-sec"><h4>Pace/Speed <span class="gsad-seg"><button data-p="pace" class="' + (u ? '' : 'on') + '">Pace</button><button data-p="speed" class="' + (u ? 'on' : '') + '">Speed</button></span></h4>' + body + '</div>' : '';
      }
      var html = sec[1].map(kv).join('');
      return html ? '<div class="gsad-sec"><h4>' + sec[0] + '</h4>' + html + '</div>' : '';
    }).join('');
  }

  var HR_NAMES = ['Warm Up', 'Easy', 'Aerobic', 'Threshold', 'Maximum'];
  var PW_NAMES = ['Easy', 'Moderate', 'Tempo', 'Long Interval', 'Short Interval'];
  var ZCOL = ['#a8a8a8', '#5594ec', '#68c068', '#e5822f', '#d9382f'];
  function zones(title, list, names, unit, lo, hi) {
    if (!list || !list.length) return '';
    var tot = list.reduce(function (s, z) { return s + (z.seconds || 0); }, 0);
    if (!tot) return '';
    var asc = list.slice().sort(function (a, b) { return a.zone - b.zone; });
    var rows = asc.slice().reverse().map(function (z) {
      var i = Math.min(Math.max(z.zone - 1, 0), 4), pc = Math.round(z.seconds / tot * 100);
      var nxt = asc.filter(function (o) { return o.zone === z.zone + 1; })[0];
      var l = z[lo], h = z[hi] != null ? z[hi] : (nxt && nxt[lo] != null ? nxt[lo] - 1 : null);
      var range = l == null ? '' : (z.zone === asc[asc.length - 1].zone || h == null) ? '&gt; ' + (l - 1) + ' ' + unit : l + ' - ' + h + ' ' + unit;
      return '<div class="gsad-z"><div><b>Zone ' + z.zone + '</b> <em>' + range + ' • ' + names[i] + '</em></div><div class="gsad-zb"><div style="width:' + pc + '%;background:' + ZCOL[i] + '"></div></div><div class="t">' + fT(z.seconds) + ' &nbsp; ' + pc + '%</div></div>';
    }).join('');
    return '<div class="gsad-zh">' + title + '</div>' + rows;
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

  /* ── mount ────────────────────────────────────────────────────────────────────────────────── */
  function ensureChart(cb) {
    if (w.Chart) return cb();
    var s = d.createElement('script'); s.src = CHARTJS; s.onload = function () { cb(); };
    s.onerror = function () { cb(new Error('Chart.js gagal dimuat')); };
    d.head.appendChild(s);
  }
  function fallbackSummary(a) {
    a = a || {};
    return {
      distance: a.distance, movingDuration: a.moving_time, averageHR: a.avg_hr, maxHR: a.max_hr, calories: a.calories,
      averageRunCadence: a.avg_cadence, strideLength: a.avg_stride_len, verticalOscillation: a.avg_vertical_osc,
      groundContactTime: a.avg_ground_contact, trainingEffect: a.aerobic_te, anaerobicTrainingEffect: a.anaerobic_te,
      elevationGain: a.elevation_gain, directWorkoutComplianceScore: a.execution_score,
      averageSpeed: a.avg_pace_secs_per_km ? 1000 / a.avg_pace_secs_per_km : null
    };
  }

  function mount(host, o) {
    if (!d.getElementById('gsad-css')) { var st = d.createElement('style'); st.id = 'gsad-css'; st.textContent = CSS; d.head.appendChild(st); }
    o = o || {};
    host.classList.add('gsad');
    host.innerHTML = '<div class="gsad-msg">Memuat grafik…</div>';
    var mi = o.distUnit === 'mi';
    fetch('/api/activity-detail?id=' + encodeURIComponent(o.id))
      .then(function (r) { return r.ok ? r.json() : { available: false }; })
      .catch(function () { return { available: false, offline: true }; })
      .then(function (api) { ensureChart(function (err) { render(host, o, api, err, mi); }); });
  }

  function render(host, o, api, chartErr, mi) {
    sync.charts = []; sync.x = null;
    var S = Object.assign(fallbackSummary(o.act), api.summary || {});
    var streams = api.streams && api.streams.n > 1 ? api.streams : null;
    var html = '<div id="gsad-charts"></div>' + (o.chartsOnly ? '<div id="gsad-pane" hidden></div>' : '<div class="gsad-tabs"><button class="gsad-tab on" data-t="stats">Stats</button><button class="gsad-tab" data-t="zones">Time in Zones</button></div><div id="gsad-pane"></div>');
    host.innerHTML = html;
    var cbox = host.querySelector('#gsad-charts'), pane = host.querySelector('#gsad-pane');

    if (!streams || chartErr || !w.Chart) {
      cbox.innerHTML = '<div class="gsad-msg">' + (chartErr ? 'Chart.js gagal dimuat.' : 'Grafik per waktu/jarak belum tersedia untuk aktivitas ini — data stream belum disinkronkan (jalankan <code>python scripts/sync_activity_streams.py</code>).') + '</div>';
    } else {
      var B = build(streams, o.distUnit), mode = 'time';
      cbox.innerHTML = '<div class="gsad-bar"><div class="gsad-seg"><button data-m="time" class="on">Over Time</button>' +
        (B.dist ? '<button data-m="dist">Over Distance</button>' : '') + '</div></div><div class="gsad-read"></div><div class="gsad-panels"></div>';
      var panels = cbox.querySelector('.gsad-panels'), read = cbox.querySelector('.gsad-read');
      var draw = function () {
        sync.charts.forEach(function (c) { c.destroy(); }); sync.charts = []; panels.innerHTML = '';
        B.names.forEach(function (n) { panel(panels, B, n, mode, function (nm) { zoom(B, nm, mode); }); });
        cbox.querySelectorAll('.gsad-bar button').forEach(function (b) { b.classList.toggle('on', b.dataset.m === mode); });
      };
      sync.onMove = function (xv) {
        if (xv == null) { read.innerHTML = ''; return; }
        var x = xs(B, mode), lo = 0, hi = x.length - 1;
        while (lo < hi) { var mid = (lo + hi) >> 1; if ((x[mid] == null ? -1 : x[mid]) < xv) lo = mid + 1; else hi = mid; }
        var i = lo, parts = ['<i><b>' + fT(B.t[i]) + '</b></i>'];
        if (B.dist && B.dist[i] != null) parts.push('<i><b>' + (B.dist[i] / (B.mi ? 1609.344 : 1000)).toFixed(2) + '</b> ' + (B.mi ? 'mi' : 'km') + '</i>');
        B.names.forEach(function (n) {
          var v = B.ser[n][i]; if (v == null) return;
          parts.push('<i><span class="gsad-dot" style="background:' + META[n].color + '"></span><b>' + META[n].fmt(v) + '</b> ' + META[n].unit + '</i>');
        });
        read.innerHTML = parts.join('');
      };
      cbox.querySelectorAll('.gsad-bar button').forEach(function (b) { b.onclick = function () { mode = b.dataset.m; draw(); }; });
      draw();
    }

    var tab = 'stats', paceMode = 'pace';
    function pane_() {
      host.querySelectorAll('.gsad-tab').forEach(function (b) { b.classList.toggle('on', b.dataset.t === tab); });
      if (tab === 'stats') {
        pane.innerHTML = '<div class="gsad-stats">' + statsSections(S, { mi: mi, paceMode: paceMode }) + '</div>';
        pane.querySelectorAll('[data-p]').forEach(function (b) { b.onclick = function () { paceMode = b.dataset.p; pane_(); }; });
      } else {
        pane.innerHTML = zones('Heart Rate Zones', (o.act && o.act.hr_zones) || [], HR_NAMES, 'bpm', 'low_bpm', 'high_bpm') +
          zones('Power Zones', api.power_zones || [], PW_NAMES, 'Watts', 'low_w', 'high_w') ||
          '<div class="gsad-msg">Tidak ada data zona.</div>';
      }
    }
    host.querySelectorAll('.gsad-tab').forEach(function (b) { b.onclick = function () { tab = b.dataset.t; pane_(); }; });
    if (!o.chartsOnly) pane_();
  }

  w.GSActDetail = { mount: mount };
})(window, document);
