/* Progressive UI enhancements. Do not attach table tools to editable or grouped rows. */
(() => {
    'use strict';
    const storage = {
        get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
        set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Storage is optional. */ } },
    };
    const announce = message => {
        const status = document.getElementById('uiStatus');
        if (!status) return;
        status.textContent = message; status.hidden = false;
        clearTimeout(announce.timer); announce.timer = setTimeout(() => { status.hidden = true; }, 4000);
    };
    const mobile = matchMedia('(max-width: 1000px)');
    const toggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('appSidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    function setNavigation(open, save = false) {
        if (!toggle) return;
        document.body.classList.toggle('sidebar-open', mobile.matches && open);
        document.body.classList.toggle('sidebar-collapsed', !mobile.matches && !open);
        toggle.setAttribute('aria-expanded', String(open));
        backdrop.hidden = !(mobile.matches && open);
        if (save && !mobile.matches) storage.set('ui.sidebar.collapsed', !open);
    }
    if (toggle) {
        setNavigation(!mobile.matches && !storage.get('ui.sidebar.collapsed', false));
        toggle.addEventListener('click', () => setNavigation(toggle.getAttribute('aria-expanded') !== 'true', true));
        backdrop.addEventListener('click', () => { setNavigation(false); toggle.focus(); });
        mobile.addEventListener('change', () => setNavigation(!mobile.matches && !storage.get('ui.sidebar.collapsed', false)));
    }
    sidebar?.querySelectorAll('.sidebar-toggle').forEach((button, index) => {
        const group = button.parentElement;
        const children = group.querySelector(':scope > .sidebar-children');
        if (!children) return;
        children.id = `sidebar-group-${index}`;
        button.setAttribute('aria-controls', children.id);
        button.addEventListener('click', () => {
            const open = group.classList.toggle('is-open');
            button.setAttribute('aria-expanded', String(open));
        });
    });
    sidebar?.querySelectorAll('a[href]').forEach(link => {
        const target = new URL(link.href, location.href);
        if (target.pathname !== location.pathname || target.search !== location.search) return;
        link.setAttribute('aria-current', 'page');
        for (let group = link.closest('.sidebar-group'); group; group = group.parentElement.closest('.sidebar-group')) {
            group.classList.add('is-open');
            group.querySelector(':scope > button')?.setAttribute('aria-expanded', 'true');
        }
    });
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && toggle) {
            setNavigation(true);
            document.getElementById('globalSearch')?.focus();
        }
        if (event.key === 'Escape' && mobile.matches && document.body.classList.contains('sidebar-open')) {
            setNavigation(false); toggle?.focus();
        }
        // Keep keyboard focus within the mobile drawer until it is dismissed.
        if (event.key === 'Tab' && mobile.matches && document.body.classList.contains('sidebar-open')) {
            const focusable = [toggle, ...sidebar.querySelectorAll('a,button,input,select')].filter(el => el && el.getClientRects().length);
            if (event.shiftKey && document.activeElement === focusable[0]) {
                event.preventDefault(); focusable.at(-1)?.focus();
            } else if (!event.shiftKey && document.activeElement === focusable.at(-1)) {
                event.preventDefault(); focusable[0]?.focus();
            }
        }
    }, true);

    const number = new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const moneyHeader = /amount|balance|debit|credit|price|cost|value|receivable|payable|total|\bvat\b/i;
    function parsedNumber(text) {
        const value = text.trim().replace(/,/g, '').replace(/^\((.*)\)$/, '-$1');
        return /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : null;
    }
    function formatCells(table, headers) {
        for (const row of table.rows) {
            if (row.parentElement.tagName === 'THEAD' || row.cells.length !== headers.length) continue;
            Array.from(row.cells).forEach((cell, index) => {
                if (cell.colSpan !== 1 || cell.rowSpan !== 1 || cell.childElementCount) return;
                const text = cell.textContent.trim();
                if (moneyHeader.test(headers[index]) && !/date|code|name|number|days|quantity|count|%/i.test(headers[index])) {
                    const value = parsedNumber(text);
                    if (value !== null && Number.isFinite(value)) {
                        const formatted = number.format(value);
                        if (cell.textContent !== formatted) cell.textContent = formatted;
                        cell.classList.add('ui-number');
                        cell.classList.toggle('ui-negative', value < 0);
                    }
                } else if (/date/i.test(headers[index]) && /^\d{4}-\d{2}-\d{2}$/.test(text)) {
                    cell.dataset.uiSortValue = text;
                    const [year, month, day] = text.split('-'); cell.textContent = `${day}-${month}-${year}`;
                }
            });
        }
    }
    let tableIndex = 0;
    function enhanceTable(table) {
        if (table.dataset.uiReady || table.closest('#root,.global-chat-window') || table.dataset.ui === 'off') return;
        table.dataset.uiReady = 'true';
        const head = table.tHead;
        if (!head || !table.tBodies.length) return;
        const headers = Array.from(head.rows[head.rows.length - 1].cells);
        const names = headers.map(th => th.textContent.trim());
        const editable = table.querySelector('input:not([type=hidden]),select,textarea,[contenteditable=true]');
        if (!editable) formatCells(table, names);
        // Scroll wrapping does not move rows or alter form controls.
        let scroll = table.closest('.ui-table-scroll');
        if (!scroll) {
            const parent = table.parentElement;
            const existingScroll = parent.children.length === 1 &&
                /auto|scroll/.test(getComputedStyle(parent).overflowX + getComputedStyle(parent).overflowY);
            scroll = existingScroll ? parent : document.createElement('div'); scroll.classList.add('ui-table-scroll');
            scroll.tabIndex = 0; scroll.setAttribute('role', 'region');
            scroll.setAttribute('aria-label', `${table.caption?.textContent || document.title} table; scroll for more columns`);
            if (!existingScroll) { table.before(scroll); scroll.append(table); }
        }
        let body = table.tBodies[0];
        const rows = Array.from(body.rows);
        const originalOrder = new WeakMap(rows.map((row, index) => [row, index]));
        let nextOrder = rows.length;
        if (editable || head.rows.length !== 1 || table.tBodies.length !== 1 || !names.every(Boolean) ||
            headers.some(th => th.colSpan !== 1 || th.rowSpan !== 1) ||
            rows.some(row => row.cells.length !== headers.length || Array.from(row.cells).some(c => c.colSpan !== 1 || c.rowSpan !== 1)) ||
            table.querySelector('thead [onclick], thead .sortable, thead [data-sort]') ||
            rows.some(row => /^(grand total|total|opening balance|closing balance)\b/i.test(row.cells[0]?.textContent.trim()))) return;
        const key = `ui.table.${document.body.dataset.userId || ''}.${document.body.dataset.companyId || ''}.${location.pathname}.${table.id || tableIndex++}.${names.join('|')}`;
        const prefs = storage.get(key, {});
        const widths = prefs.widths || {};
        const hidden = new Set(Array.isArray(prefs.hidden) ? prefs.hidden : []);
        // Filters use session storage so sensitive search terms do not persist after a browser session.
        let savedFilter = '';
        try { savedFilter = sessionStorage.getItem(key + '.filter') || ''; } catch { /* Optional. */ }
        const toolbar = document.createElement('div'); toolbar.className = 'ui-table-toolbar';
        const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Filter loaded rows…'; search.value = savedFilter;
        search.setAttribute('aria-label', 'Filter loaded table rows');
        if (table.id) document.querySelectorAll('[data-ui-filter-for]').forEach(input => {
            if (input.dataset.uiFilterFor === table.id) input.hidden = true;
        });
        const count = document.createElement('span'); count.className = 'ui-table-count'; count.setAttribute('role', 'status');
        const columns = document.createElement('details'); columns.className = 'ui-columns';
        const summary = document.createElement('summary'); summary.textContent = 'Columns';
        const panel = document.createElement('div'); panel.className = 'ui-columns-panel';
        columns.append(summary, panel);
        const reset = document.createElement('button'); reset.type = 'button'; reset.textContent = 'Reset view';
        toolbar.append(search, count, columns, reset); scroll.before(toolbar);
        const empty = document.createElement('div'); empty.className = 'ui-empty'; empty.textContent = 'No matching rows. Clear the filter or adjust the report dates.'; empty.hidden = true; scroll.after(empty);
        const note = document.createElement('p'); note.className = 'ui-table-note';
        note.textContent = (names.some(name => moneyHeader.test(name)) ? `Amounts in ${document.body.dataset.currency || 'company currency'}. ` : '') +
            'View controls apply to loaded rows. Report totals and exports are unchanged.';
        empty.after(note);
        const save = () => storage.set(key, { widths, hidden: Array.from(hidden) });
        function filter() {
            const term = search.value.trim().toLocaleLowerCase(); let visible = 0;
            Array.from(body.rows).forEach(row => {
                const match = row.textContent.toLocaleLowerCase().includes(term);
                row.toggleAttribute('data-ui-filtered', !match);
                // Use a dedicated marker rather than overriding the page's hidden state.
                if (match && !row.hidden && row.style.display !== 'none') visible++;
            });
            count.textContent = `${visible} of ${body.rows.length} loaded rows`;
            empty.hidden = visible > 0;
            try { sessionStorage.setItem(key + '.filter', search.value); } catch { /* Optional. */ }
        }
        function applyColumns() {
            if (hidden.size >= headers.length) hidden.clear();
            for (const row of table.rows) Array.from(row.cells).forEach((cell, i) => {
                cell.toggleAttribute('data-ui-column-hidden', hidden.has(i));
            });
        }
        const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
        headers.forEach((th, i) => {
            th.scope = 'col';
            const sort = document.createElement('button'); sort.type = 'button'; sort.className = 'table-sort'; sort.textContent = names[i];
            sort.setAttribute('aria-label', `Sort by ${names[i]}`); th.textContent = ''; th.append(sort);
            sort.addEventListener('click', () => {
                const direction = th.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
                headers.forEach(h => h.removeAttribute('aria-sort')); th.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
                const value = cell => {
                    const text = cell.dataset.uiSortValue || cell.textContent.trim();
                    const date = text.match(/^(\d{2})-(\d{2})-(\d{4})$/);
                    return date ? `${date[3]}-${date[2]}-${date[1]}` : text;
                };
                const sorted = Array.from(body.rows).sort((a, b) => {
                    const x = value(a.cells[i]), y = value(b.cells[i]);
                    const nx = parsedNumber(x), ny = parsedNumber(y);
                    return direction * (nx !== null && ny !== null ? nx - ny : collator.compare(x, y));
                });
                body.append(...sorted); announce(`Sorted by ${names[i]}, ${direction === 1 ? 'ascending' : 'descending'}`);
            });
            const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.checked = !hidden.has(i);
            check.setAttribute('aria-label', `Show ${names[i]} column`);
            label.append(check, document.createTextNode(names[i])); panel.append(label);
            check.addEventListener('change', () => {
                if (!check.checked && hidden.size >= headers.length - 1) { check.checked = true; announce('Keep at least one column visible.'); return; }
                if (check.checked) hidden.delete(i); else hidden.add(i); applyColumns(); save();
            });
            const resize = document.createElement('button'); resize.type = 'button'; resize.className = 'column-resize';
            resize.setAttribute('aria-label', `Resize ${names[i]} column with left and right arrow keys`); th.append(resize);
            const setWidth = width => { widths[i] = Math.max(85, Math.min(800, width)); th.style.minWidth = `${widths[i]}px`; th.style.width = `${widths[i]}px`; };
            if (widths[i]) setWidth(widths[i]);
            resize.addEventListener('keydown', event => { if (['ArrowLeft','ArrowRight'].includes(event.key)) { event.preventDefault(); setWidth(th.offsetWidth + (event.key === 'ArrowRight' ? 20 : -20)); save(); } });
            resize.addEventListener('pointerdown', event => {
                event.preventDefault(); const start = event.clientX, width = th.offsetWidth; resize.setPointerCapture(event.pointerId);
                resize.onpointermove = e => setWidth(width + e.clientX - start);
                resize.onpointerup = resize.onpointercancel = () => { resize.onpointermove = null; save(); };
            });
        });
        reset.addEventListener('click', () => {
            search.value = ''; hidden.clear(); for (const key in widths) delete widths[key];
            headers.forEach(th => { th.style.width = ''; th.style.minWidth = ''; th.removeAttribute('aria-sort'); });
            panel.querySelectorAll('input').forEach(c => { c.checked = true; });
            // Restore original order only for rows still present; never resurrect deleted records.
            Array.from(body.rows).sort((a, b) => (originalOrder.get(a) ?? Infinity) - (originalOrder.get(b) ?? Infinity))
                .forEach(row => body.append(row));
            applyColumns(); filter(); save(); announce('Table view reset.');
        });
        search.addEventListener('input', filter); applyColumns(); filter();
        let refresh;
        new MutationObserver(() => {
            clearTimeout(refresh);
            refresh = setTimeout(() => {
                body = table.tBodies[0];
                if (!body) return;
                Array.from(body.rows).forEach(row => {
                    if (!originalOrder.has(row)) originalOrder.set(row, nextOrder++);
                });
                const unsafe = Array.from(body.rows).some(row => row.cells.length !== headers.length ||
                    Array.from(row.cells).some(cell => cell.colSpan !== 1 || cell.rowSpan !== 1)) ||
                    body.querySelector('input:not([type=hidden]),select,textarea,[contenteditable=true]');
                if (unsafe) {
                    toolbar.hidden = true; empty.hidden = true; note.hidden = true;
                    headers.forEach(th => th.querySelectorAll('button').forEach(button => { button.disabled = true; }));
                    table.querySelectorAll('[data-ui-filtered],[data-ui-column-hidden]').forEach(el => {
                        el.removeAttribute('data-ui-filtered'); el.removeAttribute('data-ui-column-hidden');
                    });
                    return;
                }
                toolbar.hidden = false; note.hidden = false;
                headers.forEach(th => th.querySelectorAll('button').forEach(button => { button.disabled = false; }));
                formatCells(table, names); applyColumns(); filter();
            }, 80);
        }).observe(table, { childList: true, subtree: true, characterData: true });
        // Existing screen filters can coexist with the saved view filter.
        document.querySelectorAll('main input[type=search],main input[oninput]').forEach(input => {
            if (input !== search) input.addEventListener('input', () => requestAnimationFrame(filter));
        });
    }
    document.querySelectorAll('main table').forEach(enhanceTable);
    // New report tables inserted by AJAX receive the same controls; React owns its own DOM.
    let scan;
    new MutationObserver(records => {
        if (!records.some(record => Array.from(record.addedNodes).some(n => n.nodeType === 1 && (n.matches?.('table') || n.querySelector?.('table'))))) return;
        clearTimeout(scan); scan = setTimeout(() => document.querySelectorAll('main table:not([data-ui-ready])').forEach(enhanceTable), 100);
    }).observe(document.querySelector('main') || document.body, { childList: true, subtree: true });
    document.querySelectorAll('input,select,textarea').forEach((field, index) => {
        if (field.type === 'hidden' || field.getAttribute('aria-label') || field.labels?.length) return;
        const label = field.closest('.form-group')?.querySelector('label');
        if (label && !label.htmlFor) { field.id ||= `ui-field-${index}`; label.htmlFor = field.id; }
        else if (field.title || field.placeholder) field.setAttribute('aria-label', field.title || field.placeholder);
    });
    addEventListener('offline', () => announce('You are offline. Check your connection before saving.'));
    addEventListener('online', () => announce('Connection restored.'));
})();
