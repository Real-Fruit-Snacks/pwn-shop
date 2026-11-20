// pwn-shop page: theme switch, copy buttons, and the session terminal (BRAND.md §8), as on pwn-broker's page.
// Without JS, or with reduced motion, the terminal is the finished still frame.
(function () {
    var root = document.documentElement;
    var still = matchMedia('(prefers-reduced-motion: reduce)');
    var light = matchMedia('(prefers-color-scheme: light)');
    var css = getComputedStyle(root);
    var ms = function (name, d) { return parseFloat(css.getPropertyValue(name)) || d; };
    var $$ = function (sel, el) { return [].slice.call((el || document).querySelectorAll(sel)); };
    var toggle = document.getElementById('theme');

    // ---- theme: a saved choice wins over the system setting; the new theme sweeps in from the control ----
    function theme() { return root.dataset.theme || (light.matches ? 'dayshift' : 'nightshift'); }
    function paint() {
        var night = theme() === 'nightshift';
        document.querySelector('meta[name="theme-color"]').content = night ? '#0C0D10' : '#F6F5F2';
        toggle.setAttribute('aria-label', 'Theme: ' + (night ? 'night' : 'day') + '. Switch to ' + (night ? 'day' : 'night'));
    }
    toggle.addEventListener('click', function () {
        var next = theme() === 'nightshift' ? 'dayshift' : 'nightshift';
        var apply = function () { root.dataset.theme = next; try { localStorage.setItem('rfs-theme', next); } catch (e) {} paint(); };
        if (!document.startViewTransition || still.matches) return apply();
        var r = toggle.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
        var end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
        document.startViewTransition(apply).ready.then(function () {
            root.animate({ clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + end + 'px at ' + x + 'px ' + y + 'px)'] },
                { duration: ms('--dur-slow', 600), easing: 'cubic-bezier(.2, .7, .2, 1)', pseudoElement: '::view-transition-new(root)' });
        });
    });
    if (light.addEventListener) light.addEventListener('change', paint);
    paint();

    // ---- copy ----
    $$('[data-copy]').forEach(function (b) {
        var label = b.textContent;
        b.addEventListener('click', function () {
            var done = function (ok) { b.textContent = ok ? 'Copied' : 'Select it'; clearTimeout(b._t); b._t = setTimeout(function () { b.textContent = label; }, 1600); };
            if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(b.dataset.copy).then(function () { done(true); }, function () { done(false); });
            else done(false);
        });
    });

    // ---- the session: commands type at --type-rate, every other line prints 120 ms after the last,
    // and the cursor blinks twice and stays ----
    var screen = document.getElementById('screen');
    if (!screen || still.matches) return;
    var rate = ms('--type-rate', 45), lines = $$('.ln:not(.end)', screen), end = screen.querySelector('.ln.end');
    var cursor = screen.querySelector('.cursor');
    lines.forEach(function (ln) {
        var t = ln.querySelector('.t');
        if (t) { ln._text = t.textContent; ln._rest = document.createElement('span'); ln._rest.className = 'rest'; }
    });

    function hide() {
        clearTimeout(screen._t);
        cursor.classList.remove('blink');
        lines.forEach(function (ln) {
            var t = ln.querySelector('.t');
            if (t) { t.textContent = ''; ln._rest.textContent = ln._text; }
            ln.classList.add('wait');
        });
        end.classList.add('wait');
    }
    function play(delay) {
        var i = 0;
        hide();
        function step() {
            if (i >= lines.length) {
                end.appendChild(cursor); end.classList.remove('wait');
                void cursor.offsetWidth; cursor.classList.add('blink');
                return;
            }
            var ln = lines[i++], t = ln.querySelector('.t'), n = 0;
            ln.classList.remove('wait');
            if (!t) { screen._t = setTimeout(step, 120); return; }
            t.after(cursor); cursor.after(ln._rest);
            (function tick() {
                t.textContent = ln._text.slice(0, n); ln._rest.textContent = ln._text.slice(n);
                screen._t = n++ < ln._text.length ? setTimeout(tick, rate) : setTimeout(step, 250);
            })();
        }
        screen._t = setTimeout(step, delay);
    }

    // hold every line back until the terminal is in view, then play it once
    hide();
    screen.classList.add('live');
    if ('IntersectionObserver' in window) {
        var io = new IntersectionObserver(function (es) {
            es.forEach(function (e) { if (e.isIntersecting) { io.disconnect(); play(300); } });
        }, { threshold: 0.25 });
        io.observe(screen);
    } else {
        play(300);
    }
    $$('[data-replay]').forEach(function (b) {
        b.hidden = false;
        b.addEventListener('click', function () { if (io) io.disconnect(); play(0); });
    });
})();
