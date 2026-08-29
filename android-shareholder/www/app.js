/*
 * Shareholder app.
 *
 * Talks only to the accounting server's /api/mobile/* endpoints over HTTPS.
 * There are no database credentials here and no direct database connection -
 * the phone signs in, keeps a bearer token, and reads figures the server has
 * already aggregated.
 */
(function () {
    'use strict';

    var KEY = 'shareholder.session';
    var session = load();

    // ---------------------------------------------------------- plumbing

    function load() {
        try { return JSON.parse(localStorage.getItem(KEY) || 'null'); }
        catch (e) { return null; }
    }

    function save(value) {
        session = value;
        try {
            if (value) localStorage.setItem(KEY, JSON.stringify(value));
            else localStorage.removeItem(KEY);
        } catch (e) { /* private mode - the session just won't survive a restart */ }
    }

    function el(id) { return document.getElementById(id); }

    function api(path, options) {
        options = options || {};
        var headers = { 'Content-Type': 'application/json' };
        if (session && session.token) {
            headers.Authorization = 'Bearer ' + session.token;
        }
        return fetch(session.server + path, {
            method: options.method || 'GET',
            headers: headers,
            body: options.body ? JSON.stringify(options.body) : undefined
        }).then(function (response) {
            if (response.status === 401) {
                signOut('Your session expired. Please sign in again.');
                throw new Error('signed out');
            }
            return response.json().then(function (data) {
                if (!data.success) throw new Error(data.message || 'Request failed');
                return data;
            });
        });
    }

    // Money the way an owner reads it: thousands separated, two decimals,
    // and large figures shortened so they fit a phone without wrapping.
    function money(value) {
        var n = Number(value || 0);
        var sign = n < 0 ? '-' : '';
        var abs = Math.abs(n);
        if (abs >= 1000000) return sign + (abs / 1000000).toFixed(2) + 'M';
        if (abs >= 100000) return sign + (abs / 1000).toFixed(1) + 'K';
        return sign + abs.toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        });
    }

    function exact(value) {
        return Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        });
    }

    // Dates arrive as YYYY-MM-DD and are shown as DD-MM-YYYY, matching the
    // web app.
    function showDate(value) {
        var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
        return match ? match[3] + '-' + match[2] + '-' + match[1] : (value || '');
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ------------------------------------------------------------ signin

    function showScreen(name) {
        el('signin').classList.toggle('active', name === 'signin');
        el('main').classList.toggle('active', name === 'main');
    }

    el('signinBtn').addEventListener('click', function () {
        var server = el('server').value.trim().replace(/\/+$/, '');
        var loginId = el('login_id').value.trim();
        var password = el('password').value;
        var message = el('signinMessage');

        if (!server || !loginId || !password) {
            message.textContent = 'Fill in all three fields.';
            message.className = 'message error';
            return;
        }
        if (!/^https?:\/\//i.test(server)) server = 'https://' + server;

        this.disabled = true;
        // A free Render instance sleeps; the first call can take ~50 seconds,
        // so say so rather than looking frozen.
        message.textContent = 'Signing in… (the server may take a moment to wake)';
        message.className = 'message';

        var self = this;
        fetch(server + '/api/mobile/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                login_id: loginId, password: password,
                device: navigator.userAgent.slice(0, 100)
            })
        })
            .then(function (r) {
                // A server that answers but has no mobile API redirects to its
                // own sign-in page, so the reply is HTML. Saying "check your
                // connection" there sends people hunting the wrong problem.
                var type = r.headers.get('content-type') || '';
                if (type.indexOf('json') === -1) {
                    throw new Error('NO_API');
                }
                return r.json();
            })
            .then(function (data) {
                self.disabled = false;
                if (!data.success) {
                    message.textContent = data.message || 'Could not sign in.';
                    message.className = 'message error';
                    return;
                }
                save({
                    server: server, token: data.token,
                    company_id: data.company_id, username: data.user.username
                });
                el('password').value = '';
                openApp();
            })
            .catch(function (error) {
                self.disabled = false;
                if (error.message === 'NO_API') {
                    message.textContent = 'Reached ' + server + ', but it has '
                        + 'no shareholder API. The accounting app on that '
                        + 'server needs updating to a version that includes '
                        + '/api/mobile.';
                } else {
                    message.textContent = 'Could not reach ' + server + '. '
                        + 'Check the address and your connection. ('
                        + error.message + ')';
                }
                message.className = 'message error';
            });
    });

    el('signoutBtn').addEventListener('click', function () {
        api('/api/mobile/logout', { method: 'POST' }).catch(function () {});
        signOut();
    });

    function signOut(message) {
        save(null);
        showScreen('signin');
        if (message) {
            el('signinMessage').textContent = message;
            el('signinMessage').className = 'message error';
        }
    }

    // --------------------------------------------------------- dashboard

    function openApp() {
        showScreen('main');
        refreshAll();
    }

    // One place that reloads every data screen, so the refresh button, the
    // pull-down gesture and a fresh sign-in all behave identically.
    function refreshAll() {
        var button = el('refreshBtn');
        button.classList.add('spinning');
        el('updatedLabel').textContent = 'Refreshing...';
        Promise.all([
            loadDashboard().catch(function () {}),
            loadShareholder().catch(function () {}),
            loadInsights().catch(function () {})
        ]).then(function () {
            button.classList.remove('spinning');
            var now = new Date();
            el('updatedLabel').textContent = 'Updated '
                + ('0' + now.getHours()).slice(-2) + ':'
                + ('0' + now.getMinutes()).slice(-2);
        });
    }

    el('refreshBtn').addEventListener('click', refreshAll);

    // Pull down at the top of a list to refresh - what a phone user tries
    // first, before hunting for a button.
    (function () {
        var startY = 0, pulling = false;
        document.addEventListener('touchstart', function (e) {
            var scroller = e.target.closest && e.target.closest('.scroll');
            pulling = !!scroller && scroller.scrollTop <= 0;
            startY = e.touches[0].clientY;
        }, { passive: true });
        document.addEventListener('touchend', function (e) {
            if (!pulling) return;
            pulling = false;
            if (e.changedTouches[0].clientY - startY > 90) refreshAll();
        }, { passive: true });
    }());

    function loadDashboard() {
        return api('/api/mobile/dashboard').then(function (payload) {
            var d = payload.data;
            el('companyName').textContent = d.company || 'Company';
            // Short enough to sit on one line next to the buttons.
            el('periodLabel').textContent = showDate(d.period.from) + ' – '
                + showDate(d.period.to);

            var profit = d.headline.net_profit;
            var trend = Array.isArray(d.sales_trend) ? d.sales_trend : [];
            var peak = Math.max.apply(null, trend.map(function (m) {
                return Number(m.total || m.amount || 0);
            }).concat([1]));

            var html = ''
                + '<div class="card headline">'
                + '  <div class="label">Net profit this year</div>'
                + '  <div class="value ' + (profit >= 0 ? 'pos' : 'neg') + '">'
                + money(profit) + '</div>'
                + '  <div class="sub">Income ' + money(d.headline.income)
                + ' · Expenses ' + money(d.headline.expenses)
                + (d.headline.margin_percent === null ? ''
                    : ' · Margin ' + d.headline.margin_percent + '%')
                + '</div>'
                + '</div>'

                + '<div class="grid">'
                + stat('Cash & bank', d.position.cash_and_bank)
                + stat('Stock value', d.position.stock_value)
                + stat('Receivable', d.position.receivable)
                + stat('Payable', d.position.payable)
                + '</div>'

                + '<div class="card" style="margin-top:12px">'
                + '  <h2>Working capital</h2>'
                + '  <div class="headline" style="padding:4px 0 0">'
                + '    <div class="value" style="font-size:1.6rem">'
                + money(d.position.working_capital) + '</div>'
                + '    <div class="sub">Cash + receivables + stock − payables</div>'
                + '  </div>'
                + '</div>';

            if (trend.length) {
                html += '<div class="card"><h2>Sales by month</h2><div class="bars">'
                    + trend.map(function (m) {
                        var value = Number(m.total || m.amount || 0);
                        return '<div class="bar" style="height:'
                            + Math.max(2, (value / peak) * 100) + '%" title="'
                            + exact(value) + '"></div>';
                    }).join('')
                    + '</div><div class="bar-labels">'
                    + trend.map(function (m) {
                        return '<span>' + escapeHtml(
                            String(m.month || m.label || '').slice(0, 3)) + '</span>';
                    }).join('')
                    + '</div></div>';
            }

            if ((d.top_customers || []).length) {
                html += '<div class="card"><h2>Top customers</h2>'
                    + d.top_customers.map(function (c) {
                        return row(c.name || c.ledger_name, c.total || c.amount);
                    }).join('') + '</div>';
            }

            el('dashboardBody').innerHTML = html;
        }).catch(function (error) {
            el('dashboardBody').innerHTML = '<div class="card">Could not load: '
                + escapeHtml(error.message) + '</div>';
        });
    }

    function stat(label, value) {
        return '<div class="stat"><div class="label">' + label
            + '</div><div class="value">' + money(value) + '</div></div>';
    }

    function row(name, amount, sub) {
        return '<div class="row"><span class="name">' + escapeHtml(name)
            + (sub ? '<span class="sub">' + escapeHtml(sub) + '</span>' : '')
            + '</span><span class="amount">' + exact(amount) + '</span></div>';
    }

    // ------------------------------------------------------- shareholder

    function loadShareholder() {
        return api('/api/mobile/shareholder').then(function (payload) {
            var d = payload.data;
            var pl = d.profit_and_loss;

            var html = ''
                + '<div class="card headline">'
                + '  <div class="label">Owners\' equity</div>'
                + '  <div class="value ' + (d.equity.total >= 0 ? 'pos' : 'neg')
                + '">' + money(d.equity.total) + '</div>'
                + '  <div class="sub">Capital and reserves, including this '
                + 'year\'s result</div>'
                + '</div>'

                + '<div class="card"><h2>Equity</h2>'
                + d.equity.lines.map(function (line) {
                    return row(line.name, line.amount, line.group);
                }).join('')
                + row('Retained this year', d.equity.retained_this_year)
                + '<div class="row total"><span class="name">Total</span>'
                + '<span class="amount">' + exact(d.equity.total)
                + '</span></div></div>'

                + '<div class="card"><h2>Profit and loss</h2>'
                + row('Income', pl.income)
                + row('Expenses', pl.expenses)
                + '<div class="row total"><span class="name">Net '
                + (pl.net_profit >= 0 ? 'profit' : 'loss') + '</span>'
                + '<span class="amount">' + exact(pl.net_profit)
                + '</span></div></div>';

            if (pl.income_lines.length) {
                html += '<div class="card"><h2>Where income came from</h2>'
                    + pl.income_lines.map(function (l) {
                        return row(l.name, l.amount, l.group);
                    }).join('') + '</div>';
            }
            if (pl.expense_lines.length) {
                html += '<div class="card"><h2>Largest expenses</h2>'
                    + pl.expense_lines.map(function (l) {
                        return row(l.name, l.amount, l.group);
                    }).join('') + '</div>';
            }

            html += '<div class="note">' + escapeHtml(d.note) + '</div>';
            el('shareholderBody').innerHTML = html;
        }).catch(function (error) {
            el('shareholderBody').innerHTML = '<div class="card">Could not load: '
                + escapeHtml(error.message) + '</div>';
        });
    }

    // ---------------------------------------------------------- insights

    // Colours run "fine" to "worrying" as debt ages, so the bar reads at a
    // glance before anyone studies the legend.
    var AGE_BANDS = [
        ['not_due', 'Not due', '#1a7f37'],
        ['0_90', '0-90 days', '#2563ab'],
        ['91_180', '91-180', '#d97706'],
        ['181_270', '181-270', '#ea580c'],
        ['271_365', '271-365', '#dc2626'],
        ['over_1y', 'Over a year', '#7f1d1d']
    ];

    function ageingCard(title, ageing, blurb) {
        if (!ageing || !ageing.total) {
            return '<div class="card"><h2>' + title + '</h2>'
                + '<div class="muted tiny">Nothing outstanding.</div></div>';
        }
        var total = ageing.total || 1;
        var bar = AGE_BANDS.map(function (band) {
            var value = ageing.buckets[band[0]] || 0;
            if (value <= 0) return '';
            return '<span style="width:' + ((value / total) * 100)
                + '%;background:' + band[2] + '"></span>';
        }).join('');
        var legend = AGE_BANDS.filter(function (band) {
            return (ageing.buckets[band[0]] || 0) > 0;
        }).map(function (band) {
            return '<span><i style="background:' + band[2] + '"></i>'
                + band[1] + ' ' + money(ageing.buckets[band[0]]) + '</span>';
        }).join('');

        return '<div class="card"><h2>' + title + '</h2>'
            + '<div class="row" style="border:0;padding-top:0">'
            + '<span class="name">Total<span class="sub">' + blurb
            + '</span></span>'
            + '<span class="amount">' + exact(ageing.total) + '</span></div>'
            + '<div class="age-bar">' + bar + '</div>'
            + '<div class="age-legend">' + legend + '</div>'
            + (ageing.overdue > 0
                ? '<div style="margin-top:10px"><span class="pill warn">'
                  + money(ageing.overdue) + ' overdue</span></div>'
                : '<div style="margin-top:10px"><span class="pill good">'
                  + 'Nothing overdue</span></div>')
            + (ageing.top.length
                ? '<div style="margin-top:12px">' + ageing.top.map(function (p) {
                    return row(p.name, p.amount);
                }).join('') + '</div>'
                : '')
            + '</div>';
    }

    function loadInsights() {
        return api('/api/mobile/insights').then(function (payload) {
            var d = payload.data;

            var html = ageingCard('Owed to us', d.receivables,
                    'Customers still to pay')
                + ageingCard('We owe', d.payables,
                    'Suppliers still to be paid');

            if (d.cash_accounts.length) {
                html += '<div class="card"><h2>Where the cash is</h2>'
                    + d.cash_accounts.map(function (a) {
                        return row(a.name, a.amount, a.group);
                    }).join('') + '</div>';
            }

            if (d.top_items.length) {
                html += '<div class="card"><h2>Best sellers this year</h2>'
                    + d.top_items.map(function (i) {
                        return row(i.name, i.value, exact(i.quantity) + ' sold');
                    }).join('') + '</div>';
            }

            if (d.idle_stock.length) {
                html += '<div class="card"><h2>Stock not sold this year</h2>'
                    + '<div class="muted tiny" style="margin:-6px 0 8px">'
                    + 'Cash sitting on a shelf - worth asking about.</div>'
                    + d.idle_stock.map(function (i) {
                        return row(i.name, i.value,
                                   exact(i.quantity) + ' in stock');
                    }).join('') + '</div>';
            }

            html += '<div class="card"><h2>VAT position</h2>'
                + row('Output VAT (collected)', d.vat.output)
                + row('Input VAT (paid)', d.vat.input)
                + '<div class="row total"><span class="name">'
                + (d.vat.payable >= 0 ? 'Payable to authority' : 'Refundable')
                + '</span><span class="amount">'
                + exact(Math.abs(d.vat.payable)) + '</span></div></div>'

                + '<div class="card"><h2>Book activity</h2>'
                + row('Vouchers this year', d.activity.vouchers_this_year)
                + '<div class="row"><span class="name">Last entry</span>'
                + '<span class="amount">'
                + (d.activity.last_entry ? showDate(d.activity.last_entry) : '-')
                + '</span></div></div>'

                + '<div class="note">Figures update as the books are kept. '
                + 'Pull down, or tap the refresh icon, for the latest.</div>';

            el('insightsBody').innerHTML = html;
        }).catch(function (error) {
            el('insightsBody').innerHTML = '<div class="card">Could not load: '
                + escapeHtml(error.message) + '</div>';
            throw error;
        });
    }

    // -------------------------------------------------------------- chat

    var history = [];

    el('chatForm').addEventListener('submit', function (event) {
        event.preventDefault();
        var input = el('chatText');
        var question = input.value.trim();
        if (!question) return;
        input.value = '';
        ask(question);
    });

    // The assistant answers some questions with choice chips - "did you mean
    // ABC Trading?", "shall I use AI? yes / no". Clicking one is the same as
    // typing it, so the server reads it as the reply to what it just asked.
    // Without this the AI path is unreachable from the phone: it always asks
    // permission first, and an unclickable button is a dead end.
    el('chatLog').addEventListener('click', function (event) {
        var pick = event.target.closest && event.target.closest('.rv-pick');
        if (!pick) return;
        event.preventDefault();
        var value = pick.getAttribute('data-value') || pick.textContent.trim();
        if (!value) return;
        Array.prototype.forEach.call(
            pick.parentElement.querySelectorAll('.rv-pick'),
            function (b) { b.disabled = true; });
        ask(value);
    });

    function ask(question) {
        addBubble(question, 'me');

        var thinking = addBubble('…', 'bot');
        api('/api/mobile/chat', {
            method: 'POST',
            body: {
                question: question,
                // The coded reports answer first either way; this decides
                // whether anything they cannot answer goes to the AI model.
                ai_enabled: el('aiToggle').checked,
                history: history
            }
        }).then(function (payload) {
            thinking.remove();
            renderAnswer(payload.data);
            history.push({ role: 'user', content: question });
        }).catch(function (error) {
            thinking.remove();
            addBubble('Sorry - ' + error.message, 'bot');
        });
    }

    function addBubble(text, who) {
        var node = document.createElement('div');
        node.className = 'bubble ' + who;
        node.textContent = text;
        var log = el('chatLog');
        log.appendChild(node);
        log.scrollTop = log.scrollHeight;
        return node;
    }

    // The chatbot answers with prose plus, often, a table. Both are shown -
    // the table is what makes an answer checkable.
    function renderAnswer(answer) {
        var node = document.createElement('div');
        node.className = 'bubble bot';

        // The answer arrives as HTML from our own server - the same markup
        // the web chat renders - so it is inserted as HTML. Nothing here comes
        // from another user or another site.
        var text = answer.response || answer.summary || answer.explanation
            || 'No answer came back.';
        node.innerHTML = text;

        // The web answer offers Excel/CSV/PDF downloads, but those links are
        // session-authenticated and relative - inside the app they would land
        // on a sign-in page. Drop them rather than leave a dead end.
        Array.prototype.forEach.call(
            node.querySelectorAll('.rv-dl, .rv-alt, a[href^="/export"]'),
            function (link) { link.remove(); });

        var result = answer.data || {};
        var columns = result.columns || [];
        var rows = result.rows || [];
        // Only build a table when the answer did not already carry one.
        if (columns.length && rows.length && !/<table/i.test(text)) {
            var table = '<table><tr>' + columns.map(function (c, i) {
                return '<th class="' + (i ? 'num' : '') + '">'
                    + escapeHtml(c) + '</th>';
            }).join('') + '</tr>';
            rows.slice(0, 15).forEach(function (r) {
                table += '<tr>' + r.map(function (cell, i) {
                    var numeric = typeof cell === 'number';
                    return '<td class="' + (numeric ? 'num' : '') + '">'
                        + escapeHtml(numeric ? exact(cell) : cell) + '</td>';
                }).join('') + '</tr>';
            });
            table += '</table>';
            if (rows.length > 15) {
                table += '<div class="tiny muted">Showing 15 of '
                    + rows.length + ' rows.</div>';
            }
            node.insertAdjacentHTML('beforeend', table);
        }

        var log = el('chatLog');
        log.appendChild(node);
        log.scrollTop = log.scrollHeight;
    }

    // -------------------------------------------------------------- tabs

    Array.prototype.forEach.call(document.querySelectorAll('.tab-btn'),
        function (button) {
            button.addEventListener('click', function () {
                var name = button.dataset.tab;
                Array.prototype.forEach.call(document.querySelectorAll('.tab-btn'),
                    function (b) { b.classList.toggle('active', b === button); });
                Array.prototype.forEach.call(document.querySelectorAll('.tab'),
                    function (t) {
                        t.classList.toggle('active', t.id === 'tab-' + name);
                    });
            });
        });

    // ------------------------------------------------------------- start

    if (session && session.token && session.server) {
        el('server').value = session.server;
        openApp();
    } else if (session && session.server) {
        el('server').value = session.server;
    }
}());
