// The pwn handbook (Cyclorama Dawn v2.1.0): pwn-broker, pwn-shop, pwn-playbooks and pwn-report share
// this script word for word up to "this page". Dark (LX 263) default; Light (LX 512) opt-in toggle
// with radial View Transition and cross-tab rfs-theme / color-scheme storage sync.
var Handbook = (function () {
    var root = document.documentElement;
    var still = matchMedia('(prefers-reduced-motion: reduce)');
    var $$ = function (sel, el) { return [].slice.call((el || document).querySelectorAll(sel)); };
    var toggle = document.getElementById('theme');
    var toastEl = document.getElementById('rfs-toast');
    var toastTimer = null;

    function showToast(msg, kind) {
        if (!toastEl) return;
        var tag = kind === 'fail' ? '[x]' : '[ok]';
        toastEl.className = 'rfs-toast ' + (kind === 'fail' ? 'is-fail' : 'is-ok');
        toastEl.innerHTML = '<span class="k">' + tag + '</span> ' + msg;
        clearTimeout(toastTimer);
        try {
            if (typeof toastEl.showPopover === 'function') {
                if (toastEl.matches(':popover-open')) toastEl.hidePopover();
                toastEl.showPopover();
            } else {
                toastEl.hidden = false;
            }
        } catch (e) { toastEl.hidden = false; }
        if (typeof navigator.ariaNotify === 'function') {
            try { navigator.ariaNotify(tag + ' ' + msg, { priority: 'normal' }); } catch (e) {}
        }
        toastTimer = setTimeout(function () {
            try {
                if (typeof toastEl.hidePopover === 'function' && toastEl.matches(':popover-open')) toastEl.hidePopover();
                else toastEl.hidden = true;
            } catch (e) { toastEl.hidden = true; }
        }, 2400);
    }

    // ---- theme: LX 263 dark explicit default; LX 512 light opt-in; rfs-theme + color-scheme shared across RFS ----
    function isLight() { return root.dataset.theme === 'light'; }
    function syncMeta() {
        var light = isLight();
        var mc = document.querySelector('meta[name="theme-color"]');
        if (mc) mc.content = light ? '#F5F7F9' : '#050507';
        var cs = document.querySelector('meta[name="color-scheme"]');
        if (cs) cs.content = light ? 'light' : 'dark';
        if (toggle) {
            toggle.setAttribute('aria-pressed', String(light));
            toggle.setAttribute('aria-label', light ? 'Switch to LX 263 dark theme' : 'Switch to LX 512 light theme');
        }
    }
    function applyTheme(next) {
        if (next === 'light' || next === 'dayshift') root.dataset.theme = 'light';
        else delete root.dataset.theme;
        syncMeta();
    }
    if (toggle) {
        toggle.addEventListener('click', function () {
            var next = isLight() ? 'dark' : 'light';
            var commit = function () {
                applyTheme(next);
                try {
                    localStorage.setItem('rfs-theme', next);
                    localStorage.setItem('color-scheme', next);
                } catch (e) {}
            };
            if (!document.startViewTransition || still.matches) return commit();
            var r = toggle.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
            var end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
            var transition = document.startViewTransition(commit);
            var animated = transition.ready.then(function () {
                root.animate(
                    { clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + end + 'px at ' + x + 'px ' + y + 'px)'] },
                    { duration: 520, easing: 'cubic-bezier(.22, .61, .36, 1)', pseudoElement: '::view-transition-new(root)' }
                );
            });
            Promise.allSettled([animated, transition.updateCallbackDone, transition.finished]);
        });
    }
    window.addEventListener('storage', function (ev) {
        if (ev.key === 'rfs-theme' || ev.key === 'color-scheme') applyTheme(ev.newValue);
    });
    syncMeta();

    // ---- copy ----
    $$('[data-copy]').forEach(function (b) {
        var label = b.textContent;
        b.addEventListener('click', function () {
            var done = function (ok) {
                b.textContent = ok ? '[ok] Copied' : 'Select it';
                if (ok) showToast('Command copied to clipboard.', 'ok');
                clearTimeout(b._t);
                b._t = setTimeout(function () { b.textContent = label; }, 1600);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(b.dataset.copy).then(function () { done(true); }, function () { done(false); });
            } else {
                done(false);
            }
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

    return { still: still, $$: $$, checklist: checklist, whenSeen: whenSeen, terminal: terminal, toast: showToast };
})();

// ---------- this page: pwn-shop ----------
Handbook.terminal(document.getElementById('screen'), document.getElementById('shop-ck'));
