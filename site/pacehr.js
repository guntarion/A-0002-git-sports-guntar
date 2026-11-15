/* pacehr.js — "pace vs heart rate" efficiency panel, shared by analytics.html and coaching.html.
 *
 * Question it answers: am I getting fitter?  Fitter means (a) the same heart rate buys a faster
 * pace, or (b) the same pace costs a lower heart rate.  Both are shown, plus the raw cloud.
 *
 * Data unit = one FULL-KILOMETRE split (950–1050 m) of a running activity:
 *   - the first km is skipped (heart rate is still catching up with the effort),
 *   - 200 m / 400 m interval reps and merged laps are ignored (their pace/HR pairing is not
 *     steady-state, which is what makes the comparison fair),
 *   - treadmill runs are excluded (their pace is accelerometer-derived, not GPS).
 *
 * Needs Chart.js 4 on the page.  Usage:
 *   GSPaceHR.mount(hostEl, { activities: [...], title: 'Pace vs heart rate', note: 'optional' })
 */
(function (w, d) {
  'use strict';

  var CSS = [
    '.gph{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:24px}',
    '.gph-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}',
    '.gph-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}',
    '.gph-sub{font-size:11px;color:var(--muted);margin-bottom:12px;line-height:1.5}',
    '.gph-row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px}',
    '.gph-btn{background:var(--card2,#243048);border:1px solid var(--border);color:var(--muted);font-family:inherit;',
    '  font-size:11px;padding:5px 10px;border-radius:6px;cursor:pointer}',
    '.gph-btn:hover{color:var(--text)}',
    '.gph-btn.on{color:var(--accent);border-color:var(--accent);background:rgba(0,187,249,.1);font-weight:700}',
    '.gph-sel{background:var(--card2,#243048);border:1px solid var(--border);color:var(--text);font-family:inherit;',
    '  font-size:12px;padding:5px 8px;border-radius:6px}',
    '.gph-lbl{font-size:11px;color:var(--muted)}',
    '.gph-chart{position:relative;height:300px}',
    '.gph-verdict{margin:12px 0;padding:10px 14px;border-radius:8px;border-left:3px solid var(--accent);',
    '  background:rgba(0,187,249,.07);font-size:12px;line-height:1.55}',
    '.gph-verdict.good{border-left-color:var(--green);background:rgba(34,197,94,.08)}',
    '.gph-verdict.bad{border-left-color:var(--orange);background:rgba(249,115,22,.08)}',
    '.gph-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}',
    '.gph-tbl th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:left;',
    '  padding:6px 8px;border-bottom:1px solid var(--border);font-weight:600}',
    '.gph-tbl td{padding:7px 8px;border-bottom:1px solid rgba(51,65,85,.5)}',
    '.gph-tbl .r{text-align:right}',
    '.gph-good{color:var(--green);font-weight:700}.gph-bad{color:var(--orange);font-weight:700}.gph-flat{color:var(--muted)}',
    '.gph-scroll{overflow-x:auto}',
    '.gph-empty{padding:24px;text-align:center;color:var(--muted);font-size:12px}'
  ].join('\n');

  function injectCss() {
    if (d.getElementById('gph-css')) return;
    var s = d.createElement('style'); s.id = 'gph-css'; s.textContent = CSS; d.head.appendChild(s);
  }

  /* ── helpers ── */
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function median(a) {
    if (!a.length) return null;
    var b = a.slice().sort(function (x, y) { return x - y; }), m = b.length >> 1;
    return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2;
  }
  function fmtP(s) {
    if (s == null || !isFinite(s)) return '—';
    var m = Math.floor(s / 60), x = Math.round(s % 60);
    if (x === 60) { m++; x = 0; }
    return m + ':' + (x < 10 ? '0' : '') + x;
  }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
  function fmtMonth(ms) { var t = new Date(ms); return MON[t.getMonth()] + " '" + String(t.getFullYear()).slice(2); }
  function fmtDay(ms) { var t = new Date(ms); return t.getDate() + ' ' + MON[t.getMonth()] + ' ' + t.getFullYear(); }

  function isRunAct(a) {
    var ty = (a.type || '').toLowerCase(), rt = (a.raw_type || '').toLowerCase();
    if (rt.indexOf('treadmill') >= 0 || ty === 'virtualrun') return false;
    return ty === 'run' || ty === 'trailrun' || rt === 'running' || rt === 'trail_running';
  }

  function collect(acts) {
    var pts = [];
    (acts || []).forEach(function (a) {
      if (!isRunAct(a) || !a.date) return;
      (a.splits || []).forEach(function (s, i) {
        if (i === 0) return;
        var dist = s.distance || 0, p = s.pace_secs_per_km;
        if (dist < 950 || dist > 1050) return;
        if (!(p > 180 && p < 900) || !(s.avg_hr > 0)) return;
        pts.push({ t: Date.parse(a.date + 'T00:00:00'), pace: p, hr: s.avg_hr, name: a.name || '', id: a.id });
      });
    });
    return pts.sort(function (x, y) { return x.t - y.t; });
  }

  /* least-squares y = a + b x */
  function linfit(xs, ys) {
    var n = xs.length; if (n < 3) return null;
    var sx = 0, sy = 0, sxx = 0, sxy = 0, i;
    for (i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxx += xs[i] * xs[i]; sxy += xs[i] * ys[i]; }
    var den = n * sxx - sx * sx; if (!den) return null;
    var b = (n * sxy - sx * sy) / den;
    return { a: (sy - b * sx) / n, b: b };
  }

  function monthlyMedians(pts, key) {
    var g = {};
    pts.forEach(function (p) {
      var t = new Date(p.t), k = t.getFullYear() * 100 + t.getMonth();
      (g[k] = g[k] || { t: Date.UTC(t.getFullYear(), t.getMonth(), 15), v: [] }).v.push(p[key]);
    });
    return Object.keys(g).sort().map(function (k) { return g[k]; })
      .filter(function (m) { return m.v.length >= 2; })
      .map(function (m) { return { x: m.t, y: median(m.v) }; });
  }

  /* ── component ── */
  function mount(host, opts) {
    if (!host) return;
    opts = opts || {};
    injectCss();
    var ALL = collect(opts.activities);
    var S = { mode: 'hr', range: 0, hr: null, pace: null, chart: null };

    function inRange() {
      if (!S.range) return ALL;
      var cut = Date.now() - S.range * 864e5;
      return ALL.filter(function (p) { return p.t >= cut; });
    }

    function frame() {
      host.innerHTML = '<div class="gph">'
        + '<div class="gph-head"><div class="gph-title">' + esc(opts.title || 'Pace vs heart rate') + '</div></div>'
        + '<div class="gph-sub">' + esc(opts.note || '') + ' Satuan data: split 1 km penuh (km pertama dilewati karena HR masih menyusul; interval 200/400 m dan treadmill tidak dihitung).</div>'
        + '<div class="gph-row" id="gphMode"></div>'
        + '<div class="gph-row" id="gphCtl"></div>'
        + '<div id="gphBody"></div></div>';
    }

    function btn(label, on, attr) { return '<button class="gph-btn' + (on ? ' on' : '') + '" ' + attr + '>' + esc(label) + '</button>'; }

    function render() {
      var pts = inRange();
      var mode = d.getElementById('gphMode'), ctl = d.getElementById('gphCtl'), body = d.getElementById('gphBody');
      if (S.chart) { S.chart.destroy(); S.chart = null; }

      mode.innerHTML = btn('Pace pada HR tertentu', S.mode === 'hr', 'data-m="hr"')
        + btn('HR pada pace tertentu', S.mode === 'pace', 'data-m="pace"')
        + btn('Semua split', S.mode === 'cloud', 'data-m="cloud"')
        + '<span style="flex:1"></span>'
        + [[90, '3B'], [180, '6B'], [365, '1T'], [0, 'Semua']].map(function (r) { return btn(r[1], S.range === r[0], 'data-r="' + r[0] + '"'); }).join('');

      if (pts.length < 8) {
        ctl.innerHTML = '';
        body.innerHTML = '<div class="gph-empty">Belum cukup split 1 km penuh pada rentang ini (' + pts.length + ', butuh ≥ 8).</div>';
        return;
      }

      var cut = median(pts.map(function (p) { return p.t; }));   // early vs recent = either side of the median date
      if (S.mode === 'cloud') { ctl.innerHTML = ''; body.innerHTML = '<div class="gph-chart"><canvas id="gphCv"></canvas></div>' + legendCloud(); drawCloud(pts); return; }

      var key = S.mode === 'hr' ? 'hr' : 'pace';          // x-variable we hold fixed
      var val = S.mode === 'hr' ? 'pace' : 'hr';          // what we watch
      var tol = key === 'hr' ? 5 : 10;
      var step = key === 'hr' ? 5 : 15;
      var vals = pts.map(function (p) { return p[key]; });
      var lo = Math.floor(Math.min.apply(null, vals) / step) * step, hi = Math.ceil(Math.max.apply(null, vals) / step) * step;

      if (S[key] == null) {                                // default target = the busiest value
        var best = null, bn = -1;
        for (var v = lo; v <= hi; v += step) {
          var n = pts.filter(function (p) { return Math.abs(p[key] - v) <= tol; }).length;
          if (n > bn) { bn = n; best = v; }
        }
        S[key] = best;
      }
      var opt = '';
      for (var v2 = lo; v2 <= hi; v2 += step) {
        opt += '<option value="' + v2 + '"' + (v2 === S[key] ? ' selected' : '') + '>' + (key === 'hr' ? v2 + ' bpm' : fmtP(v2) + ' /km') + '</option>';
      }
      ctl.innerHTML = '<span class="gph-lbl">' + (key === 'hr' ? 'Detak jantung' : 'Pace') + ' ≈</span><select class="gph-sel" id="gphTarget">' + opt + '</select>'
        + '<span class="gph-lbl">± ' + tol + (key === 'hr' ? ' bpm' : ' dtk/km') + '</span>';

      var sel = pts.filter(function (p) { return Math.abs(p[key] - S[key]) <= tol; });
      body.innerHTML = verdict(sel, cut, key, val) + '<div class="gph-chart"><canvas id="gphCv"></canvas></div>' + table(pts, cut, key, val);
      if (sel.length) drawSeries(sel, key, val);
    }

    /* Δ of a median: for pace and for HR alike, LOWER is better. */
    function cls(delta, eps) { return Math.abs(delta) < eps ? 'gph-flat' : (delta < 0 ? 'gph-good' : 'gph-bad'); }
    function fmtVal(v, val) { return val === 'pace' ? fmtP(v) + '/km' : Math.round(v) + ' bpm'; }
    function fmtDelta(dl, val) {
      var s = dl < 0 ? '−' : '+', a = Math.abs(dl);
      return s + (val === 'pace' ? Math.round(a) + ' dtk/km' : a.toFixed(1) + ' bpm');
    }

    function verdict(sel, cut, key, val) {
      var tgt = key === 'hr' ? 'HR ≈ ' + S.hr + ' bpm' : 'pace ≈ ' + fmtP(S.pace) + '/km';
      var e = sel.filter(function (p) { return p.t <= cut; }).map(function (p) { return p[val]; });
      var r = sel.filter(function (p) { return p.t > cut; }).map(function (p) { return p[val]; });
      if (e.length < 3 || r.length < 3) {
        return '<div class="gph-verdict">Pada ' + tgt + ' baru ada ' + sel.length + ' split — belum cukup untuk membandingkan periode awal dengan terbaru (butuh ≥ 3 di masing-masing).</div>';
      }
      var me = median(e), mr = median(r), dl = mr - me, eps = val === 'pace' ? 3 : 1;
      var better = dl < -eps, worse = dl > eps;
      var phrase = val === 'pace'
        ? (better ? 'lebih cepat ' + Math.round(-dl) + ' dtk/km' : worse ? 'lebih lambat ' + Math.round(dl) + ' dtk/km' : 'relatif sama')
        : (better ? 'detak jantung lebih rendah ' + (-dl).toFixed(1) + ' bpm' : worse ? 'detak jantung lebih tinggi ' + dl.toFixed(1) + ' bpm' : 'relatif sama');
      var lead = val === 'pace' ? 'Pada ' + tgt + ', pace' : 'Pada ' + tgt + ', HR';
      return '<div class="gph-verdict ' + (better ? 'good' : worse ? 'bad' : '') + '">' + lead + ' dulu <b>' + fmtVal(me, val) + '</b> (n=' + e.length
        + ') → kini <b>' + fmtVal(mr, val) + '</b> (n=' + r.length + '): ' + phrase + '.</div>';
    }

    function table(pts, cut, key, val) {
      var w = key === 'hr' ? 10 : 30, g = {};
      pts.forEach(function (p) { var b = Math.floor(p[key] / w) * w; (g[b] = g[b] || []).push(p); });
      var rows = Object.keys(g).map(Number).sort(function (a, b) { return a - b; }).map(function (b) {
        var arr = g[b]; if (arr.length < 4) return '';
        var e = arr.filter(function (p) { return p.t <= cut; }).map(function (p) { return p[val]; });
        var r = arr.filter(function (p) { return p.t > cut; }).map(function (p) { return p[val]; });
        var label = key === 'hr' ? b + '–' + (b + w - 1) + ' bpm' : fmtP(b) + '–' + fmtP(b + w) + ' /km';
        var cells;
        if (e.length >= 3 && r.length >= 3) {
          var me = median(e), mr = median(r), dl = mr - me;
          cells = '<td class="r">' + fmtVal(me, val) + ' <span class="gph-flat">(' + e.length + ')</span></td>'
            + '<td class="r">' + fmtVal(mr, val) + ' <span class="gph-flat">(' + r.length + ')</span></td>'
            + '<td class="r ' + cls(dl, val === 'pace' ? 3 : 1) + '">' + fmtDelta(dl, val) + '</td>';
        } else {
          cells = '<td class="r gph-flat">' + (e.length ? fmtVal(median(e), val) : '—') + ' (' + e.length + ')</td>'
            + '<td class="r gph-flat">' + (r.length ? fmtVal(median(r), val) : '—') + ' (' + r.length + ')</td>'
            + '<td class="r gph-flat">data kurang</td>';
        }
        return '<tr><td>' + label + '</td>' + cells + '</tr>';
      }).join('');
      var head = (key === 'hr' ? 'Rentang HR' : 'Rentang pace') + '</th><th class="r">' + (val === 'pace' ? 'Pace' : 'HR') + ' dulu</th><th class="r">'
        + (val === 'pace' ? 'Pace' : 'HR') + ' kini</th><th class="r">Δ';
      return '<div style="margin-top:14px;font-size:10px;color:var(--muted)">Semua rentang · periode awal ≤ ' + fmtDay(cut) + ' &lt; periode terbaru · median (jumlah split) · hijau = membaik</div>'
        + '<div class="gph-scroll"><table class="gph-tbl"><thead><tr><th>' + head + '</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    }

    function baseOpts(yTitle, reverse) {
      var cs = getComputedStyle(d.documentElement);
      var muted = (cs.getPropertyValue('--muted') || '#94a3b8').trim(), grid = 'rgba(148,163,184,.12)';
      return {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'nearest', intersect: true },
        scales: {
          x: { type: 'linear', ticks: { color: muted, maxTicksLimit: 6, callback: function (v) { return fmtMonth(v); } }, grid: { color: grid } },
          y: { reverse: !!reverse, title: { display: true, text: yTitle, color: muted }, ticks: { color: muted }, grid: { color: grid } }
        },
        plugins: { legend: { labels: { color: muted, boxWidth: 12, font: { size: 11 } } } }
      };
    }

    function drawSeries(sel, key, val) {
      var cv = d.getElementById('gphCv'); if (!cv || !w.Chart) return;
      var isPace = val === 'pace';
      var o = baseOpts(isPace ? 'pace (menit/km) — ke atas = lebih cepat' : 'HR rata-rata (bpm) — ke bawah = lebih ringan', isPace);
      if (isPace) o.scales.y.ticks.callback = function (v) { return fmtP(v); };
      o.plugins.tooltip = {
        callbacks: {
          label: function (c) {
            var p = c.raw; if (p.name == null) return (c.dataset.label + ': ' + (isPace ? fmtP(p.y) : Math.round(p.y)));
            return fmtDay(p.x) + ' · ' + (isPace ? fmtP(p.y) + '/km' : Math.round(p.y) + ' bpm') + ' · ' + p.name;
          }
        }
      };
      var pointsDs = { type: 'scatter', label: 'split 1 km', data: sel.map(function (p) { return { x: p.t, y: p[val], name: p.name }; }),
        backgroundColor: 'rgba(148,163,184,.55)', pointRadius: 3 };
      var med = monthlyMedians(sel, val);
      var ds = [pointsDs];
      if (med.length >= 2) ds.push({ type: 'line', label: 'median bulanan', data: med, borderColor: '#00f5d4', backgroundColor: '#00f5d4', borderWidth: 2.5, pointRadius: 3, tension: .25 });
      var fit = linfit(sel.map(function (p) { return p.t; }), sel.map(function (p) { return p[val]; }));
      if (fit) {
        var t0 = sel[0].t, t1 = sel[sel.length - 1].t;
        ds.push({ type: 'line', label: 'tren', data: [{ x: t0, y: fit.a + fit.b * t0 }, { x: t1, y: fit.a + fit.b * t1 }],
          borderColor: '#f59e0b', borderDash: [6, 4], borderWidth: 2, pointRadius: 0 });
      }
      S.chart = new w.Chart(cv, { data: { datasets: ds }, options: o });
    }

    function legendCloud() {
      return '<div style="font-size:10px;color:var(--muted);margin-top:8px">Setiap titik = 1 split 1 km. Garis = tren per periode. '
        + 'Jika awan bergeser <b>ke atas</b> pada HR yang sama, Anda makin cepat pada usaha yang sama.</div>';
    }

    function drawCloud(pts) {
      var cv = d.getElementById('gphCv'); if (!cv || !w.Chart) return;
      var t0 = pts[0].t, t1 = pts[pts.length - 1].t, a = t0 + (t1 - t0) / 3, b = t0 + 2 * (t1 - t0) / 3;
      var groups = [
        { label: 'awal', color: '#64748b', f: function (p) { return p.t <= a; } },
        { label: 'tengah', color: '#00bbf9', f: function (p) { return p.t > a && p.t <= b; } },
        { label: 'terbaru', color: '#00f5d4', f: function (p) { return p.t > b; } }
      ];
      var o = baseOpts('pace (menit/km) — ke atas = lebih cepat', true);
      o.scales.x = { type: 'linear', title: { display: true, text: 'HR rata-rata split (bpm)', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,.12)' } };
      o.scales.y.ticks.callback = function (v) { return fmtP(v); };
      o.plugins.tooltip = { callbacks: { label: function (c) { var p = c.raw; return p.d ? fmtDay(p.d) + ' · ' + Math.round(p.x) + ' bpm · ' + fmtP(p.y) + '/km · ' + p.name : c.dataset.label; } } };
      var ds = [];
      groups.forEach(function (g) {
        var sub = pts.filter(g.f);
        ds.push({ type: 'scatter', label: g.label + ' (' + sub.length + ')', data: sub.map(function (p) { return { x: p.hr, y: p.pace, d: p.t, name: p.name }; }),
          backgroundColor: g.color + 'aa', pointRadius: 3 });
        var fit = linfit(sub.map(function (p) { return p.hr; }), sub.map(function (p) { return p.pace; }));
        if (fit && sub.length >= 8) {
          var xs = sub.map(function (p) { return p.hr; }), x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
          ds.push({ type: 'line', label: 'tren ' + g.label, data: [{ x: x0, y: fit.a + fit.b * x0 }, { x: x1, y: fit.a + fit.b * x1 }],
            borderColor: g.color, borderWidth: 2.5, pointRadius: 0 });
        }
      });
      S.chart = new w.Chart(cv, { data: { datasets: ds }, options: o });
    }

    frame();
    host.onclick = function (e) {
      var t = e.target.closest ? e.target.closest('button') : null; if (!t) return;
      if (t.hasAttribute('data-m')) { S.mode = t.getAttribute('data-m'); render(); }
      else if (t.hasAttribute('data-r')) { S.range = +t.getAttribute('data-r'); S.hr = null; S.pace = null; render(); }
    };
    host.onchange = function (e) {
      if (e.target && e.target.id === 'gphTarget') { S[S.mode === 'hr' ? 'hr' : 'pace'] = +e.target.value; render(); }
    };
    // assigning on* (not addEventListener) keeps a re-mount from stacking handlers
    if (host.__gphDestroy) host.__gphDestroy();
    host.__gphDestroy = function () { if (S.chart) { S.chart.destroy(); S.chart = null; } };
    render();
  }

  w.GSPaceHR = { mount: mount, collect: collect, median: median };
})(window, document);
