// The pwn handbook: pwn-broker, pwn-shop and pwn-playbooks share this script word for word, and a page
// adds its own part after "this page". It runs the theme switch, the copy controls, and the checklist:
// items marked data-step start open while a demo plays, the next one is live (amber), and each turns done
// when the demo reaches the line or step that proves it. Without JS, or with reduced motion, every item is
// done and every demo is its finished frame, as the HTML ships.
var Handbook = (function () {
    var root = document.documentElement;
    var still = matchMedia('(prefers-reduced-motion: reduce)');
    var light = matchMedia('(prefers-color-scheme: light)');
    var css = getComputedStyle(root);
    var $$ = function (sel, el) { return [].slice.call((el || document).querySelectorAll(sel)); };
    var toggle = document.getElementById('theme');

    // ---- theme: a saved choice wins over the system setting; the key is shared with the other RFS pages ----
    function theme() { return root.dataset.theme || (light.matches ? 'dayshift' : 'nightshift'); }
    function paint() {
        var night = theme() === 'nightshift';
        document.querySelector('meta[name="theme-color"]').content = night ? '#0E1012' : '#34373C';
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
                { duration: 600, easing: 'cubic-bezier(.2, .7, .2, 1)', pseudoElement: '::view-transition-new(root)' });
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

    // ---- the checklist ----
    function checklist(list) {
        var items = $$('li[data-step]', list);
        return {
            open: function () {
                items.forEach(function (li) { li.dataset.state = 'open'; });
                if (items[0]) items[0].dataset.state = 'live';
            },
            tick: function (step) {
                var i = items.findIndex(function (li) { return li.dataset.step === String(step); });
                if (i < 0) return;
                items.slice(0, i + 1).forEach(function (li) { li.dataset.state = 'done'; });
                if (items[i + 1]) items[i + 1].dataset.state = 'live';
            },
            done: function () { items.forEach(function (li) { li.dataset.state = 'done'; }); }
        };
    }

    // play fn(delay) once when el scrolls into view, and again from its Replay button
    function whenSeen(el, reset, play) {
        var io;
        if ('IntersectionObserver' in window) {
            io = new IntersectionObserver(function (es) {
                es.forEach(function (e) { if (e.isIntersecting) { io.disconnect(); play(300); } });
            }, { threshold: 0.25 });
            io.observe(el);
        } else {
            play(300);
        }
        $$('[data-replay="' + el.id + '"]').forEach(function (b) {
            b.hidden = false;
            b.addEventListener('click', function () { if (io) io.disconnect(); reset(); play(0); });
        });
    }

    // ---- a terminal: commands type at 45 ms a character, every other line prints 120 ms after the last,
    // a line marked data-step ticks that item, and the cursor blinks twice and stays ----
    function terminal(screen, list) {
        if (!screen || still.matches) return;
        var ck = list ? checklist(list) : null;
        var code = screen.querySelector('code');
        [].slice.call(code.childNodes).forEach(function (n) {
            if (n.nodeType !== 3 || !/^\n+$/.test(n.data)) return;
            var prev = n.previousSibling, count = n.data.length;
            if (prev && prev.classList && prev.classList.contains('ln')) { prev.appendChild(document.createTextNode('\n')); count--; }
            for (var k = 0; k < count; k++) { var b = document.createElement('span'); b.className = 'ln blank'; b.textContent = '\n'; code.insertBefore(b, n); }
            n.remove();
        });
        var rate = 45, lines = $$('.ln:not(.end)', screen), end = screen.querySelector('.ln.end');
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
            if (ck) ck.open();
        }
        function play(delay) {
            var i = 0;
            hide();
            function shown(ln) { if (ck && ln.dataset.step) ck.tick(ln.dataset.step); }
            function step() {
                if (i >= lines.length) {
                    end.appendChild(cursor); end.classList.remove('wait');
                    void cursor.offsetWidth; cursor.classList.add('blink');
                    if (ck) ck.done();
                    return;
                }
                var ln = lines[i++], t = ln.querySelector('.t'), n = 0;
                ln.classList.remove('wait');
                if (!t) { shown(ln); screen._t = setTimeout(step, ln.classList.contains('blank') ? 0 : 120); return; }
                t.after(cursor); cursor.after(ln._rest);
                (function tick() {
                    t.textContent = ln._text.slice(0, n); ln._rest.textContent = ln._text.slice(n);
                    if (n++ < ln._text.length) { screen._t = setTimeout(tick, rate); } else { shown(ln); screen._t = setTimeout(step, 250); }
                })();
            }
            screen._t = setTimeout(step, delay);
        }
        hide();
        screen.classList.add('live');
        whenSeen(screen, hide, play);
    }

    return { still: still, $$: $$, checklist: checklist, whenSeen: whenSeen, terminal: terminal };
})();

// ---------- this page: pwn-shop ----------
Handbook.terminal(document.getElementById('screen'), document.getElementById('shop-ck'));
