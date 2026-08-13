/* ============================================================
   NEWS AGGREGATOR — frontend behaviour
   ============================================================ */
(function () {
  'use strict';

  /* ---------- AOS init ---------- */
  if (window.AOS) AOS.init({ duration: 700, once: true, easing: 'ease-out-cubic' });

  /* ---------- Sticky header shadow on scroll ---------- */
  const header = document.querySelector('.site-header');
  const onScroll = () => {
    if (window.scrollY > 8) header?.classList.add('scrolled');
    else header?.classList.remove('scrolled');
    const btn = document.getElementById('scrollTop');
    if (btn) btn.classList.toggle('show', window.scrollY > 400);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  document.getElementById('scrollTop')?.addEventListener('click', () =>
    window.scrollTo({ top: 0, behavior: 'smooth' }));

  /* ---------- Dark mode toggle ---------- */
  const applyTheme = (mode) => {
    document.documentElement.setAttribute('data-bs-theme', mode);
    try { localStorage.setItem('na-theme', mode); } catch (e) {}
    document.querySelectorAll('[data-theme-toggle] i').forEach(i =>
      i.className = mode === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars');
  };
  try {
    const saved = localStorage.getItem('na-theme');
    if (saved) applyTheme(saved);
  } catch (e) {}
  document.querySelectorAll('[data-theme-toggle]').forEach(btn =>
    btn.addEventListener('click', () =>
      applyTheme(document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark')));

  /* ---------- Swiper ---------- */
  if (window.Swiper) {
    document.querySelectorAll('.headline-swiper').forEach(el =>
      new Swiper(el, {
        slidesPerView: 1, spaceBetween: 16,
        breakpoints: {
          576: { slidesPerView: 2 },
          992: { slidesPerView: 3 },
          1200: { slidesPerView: 4 }
        },
        pagination: { el: el.querySelector('.swiper-pagination'), clickable: true },
        navigation: { nextEl: el.querySelector('.swiper-button-next'), prevEl: el.querySelector('.swiper-button-prev') },
        autoplay: { delay: 5000, disableOnInteraction: false }
      }));
  }

  /* ---------- Auto-dismiss flash messages ---------- */
  setTimeout(() => {
    document.querySelectorAll('.flash-stack .alert').forEach(a => {
      try { bootstrap.Alert.getOrCreateInstance(a).close(); } catch (e) {}
    });
  }, 5500);

  /* ---------- Live search (top header pill) ---------- */
  const searchInput = document.querySelector('.search-pill input');
  if (searchInput) {
    let timer;
    searchInput.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const q = searchInput.value.trim();
        if (q.length > 1) window.location.href = '/search?q=' + encodeURIComponent(q);
      }, 700);
    });
  }

  /* ---------- Reading-progress on article page ---------- */
  const article = document.querySelector('.article-body');
  if (article) {
    const bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,#ef4444,#f59e0b,#8b5cf6);z-index:2000;transition:width .1s';
    document.body.appendChild(bar);
    window.addEventListener('scroll', () => {
      const r = article.getBoundingClientRect();
      const total = r.height - window.innerHeight;
      const done = Math.min(1, Math.max(0, (-r.top) / Math.max(1, total)));
      bar.style.width = (done * 100) + '%';
    }, { passive: true });
  }

  /* ---------- Toast for forms ---------- */
  document.querySelectorAll('form[data-toast]').forEach(f =>
    f.addEventListener('submit', e => {
      e.preventDefault();
      showToast('Thanks! Your message was received.');
    }));

  function showToast(message) {
    let host = document.querySelector('.toast-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'toast-host';
      host.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:1080';
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = 'shadow-lg';
    el.style.cssText = 'background:#0f172a;color:#fff;padding:.8rem 1.2rem;border-radius:999px;margin-top:.5rem;animation:slideUp .25s ease';
    el.innerHTML = '<i class="bi bi-check-circle text-success me-2"></i>' + message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  /* ---------- Smooth page transitions ---------- */
  document.querySelectorAll('a[href]:not([target=_blank])').forEach(a => {
    a.addEventListener('click', e => {
      const url = new URL(a.href, location.origin);
      const same = url.origin === location.origin && url.pathname !== location.pathname;
      if (same && !url.hash && document.startViewTransition) {
        e.preventDefault();
        document.startViewTransition(() => { location.href = url.href; });
      }
    });
  });

  /* ---------- Lazy-load image fallback for older browsers ---------- */
  document.querySelectorAll('img:not([loading])').forEach(i => i.setAttribute('loading', 'lazy'));
})();
