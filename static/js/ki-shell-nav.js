// Moved out of base.html so browsers cache it; loaded at the same spot.
(function() {
  const MOBILE = 1280;   // keep in step with the media query in css/ki-shell.css
  const nav = document.getElementById('navMenu');
  const overlay = document.getElementById('navOverlay');
  const toggle = document.getElementById('navToggle');

  function isMobile() { return window.innerWidth <= MOBILE; }

  // ── Hamburger ──
  function openNav() {
    nav.classList.add('show');
    overlay.classList.add('show');
    document.body.style.overflow = 'hidden';
  }
  function closeNav() {
    nav.classList.remove('show');
    overlay.classList.remove('show');
    document.body.style.overflow = '';
    // collapse any open dropdowns on mobile
    document.querySelectorAll('.kn-dropdown.open').forEach(d => d.classList.remove('open'));
  }
  toggle && toggle.addEventListener('click', () => {
    nav.classList.contains('show') ? closeNav() : openNav();
  });
  overlay && overlay.addEventListener('click', closeNav);

  // ── Dropdowns ──
  document.querySelectorAll('.kn-dropdown').forEach(function(dd) {
    const btn = dd.querySelector('.kn-dropdown-toggle, .kn-user-toggle');
    if (!btn) return;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      // On desktop dropdowns also work via :hover, but click toggles too
      const isOpen = dd.classList.contains('open');
      // One menu open at a time — on the bar and in the drawer alike.
      document.querySelectorAll('.kn-dropdown.open').forEach(d => {
        if (d !== dd) d.classList.remove('open');
      });
      dd.classList.toggle('open', !isOpen);
    });
  });
  // Click anywhere else closes the open menu.
  document.addEventListener('click', function(e) {
    if (isMobile()) return;
    if (!e.target.closest('.kn-dropdown')) {
      document.querySelectorAll('.kn-dropdown.open')
        .forEach(d => d.classList.remove('open'));
    }
  });
  // Close mobile nav when clicking a navigation link
  nav && nav.querySelectorAll('a.kn-link, a.kn-menu-item').forEach(function(a) {
    a.addEventListener('click', function() {
      if (isMobile()) closeNav();
    });
  });

  // ── Active link highlighting ──
  const path = window.location.pathname;
  let foundActive = false;
  nav && nav.querySelectorAll('a.kn-link, a.kn-menu-item').forEach(function(a) {
    if (a.getAttribute('href') === path) {
      a.classList.add('active');
      foundActive = true;
      // also mark parent dropdown toggle as active
      const parent = a.closest('.kn-dropdown');
      if (parent) {
        const tog = parent.querySelector('.kn-dropdown-toggle');
        tog && tog.classList.add('active');
      }
    }
  });

  // ── ESC closes mobile nav ──
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && nav.classList.contains('show')) closeNav();
  });
})();
