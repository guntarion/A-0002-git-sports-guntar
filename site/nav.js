/* nav.js — one navigation component for every page.
 *
 * Replaces three divergent hand-written navs (.header-nav, .header-links,
 * .nav-back) that had to be edited separately each time a page was added, and
 * which wrapped into three ragged rows on a phone.
 *
 * Desktop : icon + full label, one row.
 * Mobile  : icon + short label, one horizontally scrollable row — it never
 *           wraps, and the active item is scrolled into view. A drawer was the
 *           alternative but hides navigation behind a tap; on a dashboard you
 *           move between pages constantly, so keeping them one tap away wins.
 *
 * Usage: <script src="nav.js"></script> anywhere in <body>; it injects itself
 * into [data-gs-nav], or into the first <header> if that is absent.
 */
(function (window, document) {
  'use strict';

  // 24x24 stroke icons; currentColor so they follow the link state.
  var I = {
    dash:  '<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
    act:   '<path d="M4 6h16M4 12h16M4 18h10"/>',
    anal:  '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    rec:   '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM5 6H3v2a3 3 0 0 0 3 3M19 6h2v2a3 3 0 0 1-3 3"/>',
    perf:  '<path d="M2 12h4l3 8 4-16 3 8h6"/>',
    well:  '<path d="M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 1 0-7.1 7.1L12 21.4l8.8-8.7a5 5 0 0 0 0-7.1z"/>',
    body:  '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 10a4 4 0 0 1 8 0"/><path d="M12 10l2.2-2.4"/>',
    map:   '<path d="M21 10c0 6-9 12-9 12s-9-6-9-12a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    jrn:   '<path d="M4 4a2 2 0 0 1 2-2h13v18H6a2 2 0 0 0-2 2V4z"/><path d="M9 7h7M9 11h7"/>',
    todo:  '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>'
  };

  // href, full label, short label (mobile), icon
  var PAGES = [
    ['./',               'Dashboard',   'Dash', I.dash],
    ['activities.html',  'Activities',  'Act',  I.act],
    ['analytics.html',   'Analytics',   'Anly', I.anal],
    ['records.html',     'Records',     'Rec',  I.rec],
    ['performance.html', 'Performance', 'Perf', I.perf],
    ['wellness.html',    'Wellness',    'Well', I.well],
    ['body.html',        'Body',        'Body', I.body],
    ['map.html',         'Map',         'Map',  I.map],
    ['journal.html',     'Journal',     'Jrnl', I.jrn],
    ['todos.html',       'Todos',       'Todo', I.todo]
  ];

  var CSS = [
    /* min-width:0 lets it shrink inside flex headers instead of forcing
       overflow; grid-column spans the full row when the header is a grid
       (index.html), where it was otherwise squeezed into an 86px column.
       Both properties are inert in the layout they do not apply to. */
    '.gsnav{display:flex;gap:4px;align-items:center;overflow-x:auto;scrollbar-width:none;',
    '  -webkit-overflow-scrolling:touch;max-width:100%;min-width:0;grid-column:1/-1}',
    '.gsnav::-webkit-scrollbar{display:none}',
    '.gsnav a{display:inline-flex;align-items:center;gap:6px;flex:0 0 auto;text-decoration:none;',
    '  color:#94a3b8;font-family:inherit;font-size:12px;line-height:1;padding:7px 11px;border-radius:8px;',
    '  border:1px solid transparent;transition:background .15s,color .15s,border-color .15s;white-space:nowrap}',
    '.gsnav a:hover{color:#f1f5f9;background:rgba(148,163,184,.12);text-decoration:none}',
    '.gsnav a.on{color:#00bbf9;background:rgba(0,187,249,.13);border-color:rgba(0,187,249,.42);font-weight:700}',
    '.gsnav svg{width:15px;height:15px;flex:0 0 15px;stroke:currentColor;fill:none;stroke-width:2;',
    '  stroke-linecap:round;stroke-linejoin:round}',
    '.gsnav .s{display:none}',
    /* Rescued repo link sits beside the nav, styled to match. */
    '.gsnav-extra{color:#94a3b8;font-size:11px;text-decoration:none;padding:6px 9px;border-radius:8px;',
    '  border:1px solid transparent;white-space:nowrap;transition:.15s}',
    '.gsnav-extra:hover{color:#f1f5f9;background:rgba(148,163,184,.12)}',
    '@media (max-width:760px){.gsnav-extra{display:none}}',
    /* Phones: short labels, tighter chips, one scrollable row. The row is
       edge-to-edge so the scroll affordance is obvious. */
    '@media (max-width:760px){',
    '  .gsnav{gap:2px;padding-bottom:2px}',
    '  .gsnav a{flex-direction:column;gap:3px;padding:6px 9px;font-size:9.5px;letter-spacing:.02em}',
    '  .gsnav svg{width:17px;height:17px;flex:0 0 17px}',
    '  .gsnav .f{display:none}.gsnav .s{display:inline}',
    '}'
  ].join('');

  function currentFile() {
    var p = location.pathname.replace(/\/+$/, '');
    var f = p.substring(p.lastIndexOf('/') + 1);
    return f || 'index.html';
  }

  function isActive(href) {
    var cur = currentFile();
    if (href === './') return cur === 'index.html' || cur === '';
    return href === cur;
  }

  function build() {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var nav = document.createElement('nav');
    nav.className = 'gsnav';
    nav.setAttribute('aria-label', 'Pages');
    nav.innerHTML = PAGES.map(function (p) {
      var on = isActive(p[0]);
      return '<a href="' + p[0] + '"' + (on ? ' class="on" aria-current="page"' : '') + '>'
        + '<svg viewBox="0 0 24 24" aria-hidden="true">' + p[3] + '</svg>'
        + '<span class="f">' + p[1] + '</span><span class="s">' + p[2] + '</span></a>';
    }).join('');

    var host = document.querySelector('[data-gs-nav]');
    if (host) {
      host.innerHTML = '';
      host.appendChild(nav);
      return finish(nav);
    }

    // Otherwise replace whatever nav the page shipped with. Pages use three
    // different shapes: a .header-nav/.header-links container, or (in
    // activities.html) bare .nav-back links sitting straight in the header —
    // so clear all of them, or the old links survive alongside the new nav.
    var container = document.querySelector('header .header-nav, header .header-links');
    var loose = Array.prototype.slice.call(
      document.querySelectorAll('header .nav-back, header .header-link'));
    var hdr = document.querySelector('header');

    // index.html keeps its repo link inside .header-links, and app.js still
    // reads/updates it (.repo-link). Rescue it before the container goes, or
    // the link silently disappears from the header. It also carries the
    // .header-link class, so it must be dropped from the cleanup list too —
    // otherwise the sweep below deletes it again straight after rescuing it.
    var keep = container && container.querySelector('.repo-link');
    if (keep) loose = loose.filter(function (el) { return el !== keep; });

    if (container) {
      container.parentNode.replaceChild(nav, container);
      if (keep) {
        keep.classList.add('gsnav-extra');
        nav.parentNode.insertBefore(keep, nav.nextSibling);
      }
    } else if (loose.length && hdr) {
      loose[0].parentNode.insertBefore(nav, loose[0]);
    } else if (hdr) {
      hdr.appendChild(nav);
    } else {
      document.body.insertBefore(nav, document.body.firstChild);
    }
    loose.forEach(function (el) { el.remove(); });
    finish(nav);
  }

  function finish(nav) {
    // Keep the current page visible when the row overflows on a phone.
    var on = nav.querySelector('a.on');
    if (on && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft = Math.max(0, on.offsetLeft - nav.clientWidth / 2 + on.offsetWidth / 2);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})(window, document);
