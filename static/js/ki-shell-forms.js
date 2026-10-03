// Moved out of base.html so browsers cache it; loaded at the same spot.
// ── One submit per form ──────────────────────────────────────────────
// A laggy page invites a second click on Save, which POSTs twice and
// creates duplicate clients/sales/leads. Guard every write form once,
// here, rather than per template.
(function () {
  function buttons(form) {
    return form.querySelectorAll(
      'button[type="submit"], input[type="submit"], button:not([type]):not([data-bs-toggle])');
  }
  function unlock(form) {
    delete form.dataset.submitting;
    buttons(form).forEach(function (b) {
      b.disabled = false;
      if (b.dataset.kiLabel !== undefined) { b.innerHTML = b.dataset.kiLabel; delete b.dataset.kiLabel; }
    });
  }
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    // GET filters are harmless to repeat; opt-outs and new-tab posts stay free.
    if ((form.method || 'get').toLowerCase() !== 'post') return;
    if (form.hasAttribute('data-allow-resubmit') || form.target) return;
    if (form.dataset.submitting === '1') { e.preventDefault(); return; }
    if (e.defaultPrevented) return;   // client-side validation / htmx already stopped it
    form.dataset.submitting = '1';
    // Next tick: the clicked button's name/value must still make it into the POST.
    setTimeout(function () {
      buttons(form).forEach(function (b) {
        b.disabled = true;
        if (b.tagName === 'BUTTON') {
          b.dataset.kiLabel = b.innerHTML;
          b.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> '
            + (b.dataset.busyText || (b.textContent || '').trim() || 'Working');
        }
      });
      // ponytail: 20s escape hatch so a POST that never navigates (file
      // download, silent failure) can't leave the page permanently dead.
      setTimeout(function () { unlock(form); }, 20000);
    }, 0);
  });
  // Back button / bfcache restore must not land on a dead Save button.
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('form[data-submitting]').forEach(unlock);
  });
})();

// ── One in-flight POST per request ───────────────────────────────────
// The submit guard above only sees real form submits. Screens that post
// with fetch() (calendar events, bulk lead import, quick-add client,
// category creates) would still double-fire on a double click, so an
// identical POST — same URL, same body — that is ALREADY in flight is
// handed the first one's response instead of being sent again. Requests
// that have finished are not affected: this collapses the double click,
// nothing else.
(function () {
  var inflight = new Map();
  var rawFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var body = init && init.body;
    var comparable = typeof body === 'string' || body instanceof URLSearchParams;
    if (method !== 'POST' || !comparable) return rawFetch(input, init);
    var key = url + '|' + String(body);
    if (inflight.has(key)) {
      console.warn('[ki] duplicate POST collapsed while the first was in flight:', url);
      return inflight.get(key).then(function (r) { return r.clone(); });
    }
    // Every caller gets a clone; the original response is never read, so
    // a clone taken later is still valid.
    var shared = rawFetch(input, init);
    inflight.set(key, shared);
    shared.then(function () { inflight.delete(key); },
                function () { inflight.delete(key); });
    return shared.then(function (r) { return r.clone(); });
  };
})();

// ── Progress bars ────────────────────────────────────────────────────
// `.ki-progress > [data-progress]` fills to its percentage. This used to
// live only in the employee dashboard's script, so bars on every other
// page (targets, admin) rendered at content width — i.e. wrong.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.ki-progress [data-progress]').forEach(function (bar) {
    var pct = parseFloat(bar.dataset.progress || '0');
    bar.style.width = Math.max(0, Math.min(pct, 100)) + '%';
  });

  // ── Searchable <select> ────────────────────────────────────────────
  // Add `data-searchable` to any long dropdown and it gets a filter box.
  document.querySelectorAll('select[data-searchable]').forEach(function (sel) {
    var all = Array.from(sel.options);
    if (all.length < 8) return;                 // short list — nothing to search
    var box = document.createElement('input');
    box.type = 'search';
    box.className = sel.className.indexOf('form-select') >= 0 ? 'form-control mb-1' : 'ki-input mb-1';
    box.placeholder = sel.dataset.searchPlaceholder || 'Type to filter…';
    box.setAttribute('aria-label', box.placeholder);
    box.autocomplete = 'off';
    sel.parentNode.insertBefore(box, sel);
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase();
      var current = sel.value;
      var keep = all.filter(function (o) {
        return !o.value || o.text.toLowerCase().indexOf(q) >= 0;
      });
      sel.replaceChildren.apply(sel, keep);
      sel.value = keep.some(function (o) { return o.value === current; }) ? current : '';
    });
    // Enter in the box picks the only remaining match instead of submitting.
    box.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var opts = Array.from(sel.options).filter(function (o) { return o.value; });
      if (opts.length === 1) { sel.value = opts[0].value; sel.focus(); }
    });
  });
});
