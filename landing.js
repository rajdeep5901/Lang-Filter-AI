/* =============================================================
   LangFilterAI — Landing Page Interactions
   Vanilla JS. No dependencies. Purely static (no backend).
   - Constellation particle background (canvas)
   - Generated hero waveform bars
   - Scroll-triggered reveals (IntersectionObserver)
   - Navbar scroll state + hero stat count-up
   ============================================================= */

(function () {
    'use strict';

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ---------------------------------------------------------
       1. Hero waveform bars — generated for organic randomness
       --------------------------------------------------------- */
    (function buildWaveform() {
        const wrap = document.getElementById('heroWaves');
        if (!wrap) return;

        const BAR_COUNT = 80;
        const frag = document.createDocumentFragment();

        for (let i = 0; i < BAR_COUNT; i++) {
            const bar = document.createElement('span');
            // Height forms a soft "audio spectrum" envelope, tallest in the middle.
            const centerBias = 1 - Math.abs(i - BAR_COUNT / 2) / (BAR_COUNT / 2);
            const base = 40 + centerBias * 150 + Math.random() * 40;
            bar.style.height = base + 'px';
            bar.style.animationDelay = (Math.random() * -1.4).toFixed(2) + 's';
            bar.style.animationDuration = (1.1 + Math.random() * 0.9).toFixed(2) + 's';
            frag.appendChild(bar);
        }
        wrap.appendChild(frag);
    })();

    /* ---------------------------------------------------------
       2. Constellation particle background
       --------------------------------------------------------- */
    (function constellation() {
        const canvas = document.getElementById('constellation');
        if (!canvas || prefersReducedMotion) return;

        const ctx = canvas.getContext('2d');
        let width, height, particles, animId;
        const COLORS = ['0, 229, 153', '34, 211, 238', '168, 85, 247'];
        const LINK_DIST = 140;
        const mouse = { x: -9999, y: -9999 };

        function densityCount() {
            // Scale particle count with viewport area, capped for performance.
            return Math.min(110, Math.floor((width * height) / 16000));
        }

        function resize() {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
            initParticles();
        }

        function initParticles() {
            const count = densityCount();
            particles = [];
            for (let i = 0; i < count; i++) {
                particles.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    vx: (Math.random() - 0.5) * 0.35,
                    vy: (Math.random() - 0.5) * 0.35,
                    r: Math.random() * 1.8 + 0.6,
                    c: COLORS[Math.floor(Math.random() * COLORS.length)]
                });
            }
        }

        function step() {
            ctx.clearRect(0, 0, width, height);

            for (let i = 0; i < particles.length; i++) {
                const p = particles[i];

                p.x += p.vx;
                p.y += p.vy;

                // Wrap around edges
                if (p.x < 0) p.x = width;
                else if (p.x > width) p.x = 0;
                if (p.y < 0) p.y = height;
                else if (p.y > height) p.y = 0;

                // Draw particle
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(' + p.c + ', 0.85)';
                ctx.fill();

                // Links to nearby particles
                for (let j = i + 1; j < particles.length; j++) {
                    const q = particles[j];
                    const dx = p.x - q.x;
                    const dy = p.y - q.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < LINK_DIST) {
                        const alpha = (1 - dist / LINK_DIST) * 0.5;
                        ctx.beginPath();
                        ctx.moveTo(p.x, p.y);
                        ctx.lineTo(q.x, q.y);
                        ctx.strokeStyle = 'rgba(' + p.c + ', ' + alpha.toFixed(3) + ')';
                        ctx.lineWidth = 0.7;
                        ctx.stroke();
                    }
                }

                // Subtle attraction highlight near the cursor
                const mdx = p.x - mouse.x;
                const mdy = p.y - mouse.y;
                const mdist = Math.sqrt(mdx * mdx + mdy * mdy);
                if (mdist < 160) {
                    const alpha = (1 - mdist / 160) * 0.6;
                    ctx.beginPath();
                    ctx.moveTo(p.x, p.y);
                    ctx.lineTo(mouse.x, mouse.y);
                    ctx.strokeStyle = 'rgba(' + p.c + ', ' + alpha.toFixed(3) + ')';
                    ctx.lineWidth = 0.8;
                    ctx.stroke();
                }
            }

            animId = requestAnimationFrame(step);
        }

        window.addEventListener('mousemove', function (e) {
            mouse.x = e.clientX;
            mouse.y = e.clientY;
        });
        window.addEventListener('mouseout', function () {
            mouse.x = -9999; mouse.y = -9999;
        });

        let resizeTimer;
        window.addEventListener('resize', function () {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(resize, 150);
        });

        // Pause when tab hidden to save CPU
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) {
                cancelAnimationFrame(animId);
            } else {
                animId = requestAnimationFrame(step);
            }
        });

        resize();
        step();
    })();

    /* ---------------------------------------------------------
       3. Scroll-triggered reveals with stagger
       --------------------------------------------------------- */
    (function scrollReveal() {
        const items = document.querySelectorAll('.reveal');
        if (!items.length) return;

        if (prefersReducedMotion || !('IntersectionObserver' in window)) {
            items.forEach(function (el) { el.classList.add('in'); });
            return;
        }

        // Give grouped cards a staggered delay based on position among siblings.
        const observer = new IntersectionObserver(function (entries, obs) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                const el = entry.target;
                const siblings = Array.prototype.slice.call(el.parentElement.children)
                    .filter(function (n) { return n.classList.contains('reveal'); });
                const idx = siblings.indexOf(el);
                el.style.setProperty('--delay', (idx * 0.08).toFixed(2) + 's');
                el.classList.add('in');
                obs.unobserve(el);
            });
        }, { threshold: 0.15, rootMargin: '0px 0px -60px 0px' });

        items.forEach(function (el) { observer.observe(el); });
    })();

    /* ---------------------------------------------------------
       4. Navbar scrolled state
       --------------------------------------------------------- */
    (function navScroll() {
        const nav = document.getElementById('nav');
        if (!nav) return;
        function onScroll() {
            nav.classList.toggle('scrolled', window.scrollY > 20);
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    })();

    /* ---------------------------------------------------------
       5. Hero stat count-up (for numeric [data-count] values)
       --------------------------------------------------------- */
    (function countUp() {
        const els = document.querySelectorAll('[data-count]');
        if (!els.length || prefersReducedMotion) return;

        els.forEach(function (el) {
            const target = parseInt(el.getAttribute('data-count'), 10);
            const suffix = el.getAttribute('data-suffix') || '';
            if (isNaN(target)) return;

            let started = false;
            const obs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting || started) return;
                    started = true;
                    const duration = 1200;
                    const start = performance.now();
                    function tick(now) {
                        const t = Math.min((now - start) / duration, 1);
                        const eased = 1 - Math.pow(1 - t, 3);
                        el.textContent = Math.round(eased * target) + suffix;
                        if (t < 1) requestAnimationFrame(tick);
                    }
                    requestAnimationFrame(tick);
                    obs.disconnect();
                });
            }, { threshold: 0.5 });
            obs.observe(el);
        });
    })();

})();
