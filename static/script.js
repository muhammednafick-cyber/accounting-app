// Global Upload Blocking Functions
function blockUI(message) {
    const overlay = document.getElementById('uploadOverlay');
    if (overlay) {
        overlay.style.display = 'flex';
        if (message) {
            overlay.querySelector('.message').innerText = message;
        }
    }
    document.body.classList.add('uploading');
    window.onbeforeunload = function () {
        return "Upload in progress. Are you sure you want to leave? Your upload will be cancelled.";
    };
}

// CSRF Protection Setup
document.addEventListener('DOMContentLoaded', function() {
    const csrfTokenMeta = document.querySelector('meta[name="csrf-token"]');
    if (csrfTokenMeta) {
        const csrfToken = csrfTokenMeta.getAttribute('content');
        
        // 1. Setup for fetch API
        const originalFetch = window.fetch;
        window.fetch = function(url, options = {}) {
            options.headers = options.headers || {};
            // Add CSRF token to non-GET requests if not already present
            if (options.method && 
                ['POST', 'PUT', 'DELETE', 'PATCH'].includes(options.method.toUpperCase()) && 
                !options.headers['X-CSRFToken']) {
                options.headers['X-CSRFToken'] = csrfToken;
            }
            return originalFetch(url, options);
        };

        // 2. Setup for jQuery (if loaded)
        if (window.jQuery) {
            window.jQuery.ajaxSetup({
                beforeSend: function(xhr, settings) {
                    if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                        xhr.setRequestHeader("X-CSRFToken", csrfToken);
                    }
                }
            });
        }
        
        // 3. Setup for XMLHttpRequest
        const originalOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._method = method;
            return originalOpen.apply(this, arguments);
        };
        const originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function() {
            if (this._method && 
                ['POST', 'PUT', 'DELETE', 'PATCH'].includes(this._method.toUpperCase())) {
                this.setRequestHeader("X-CSRFToken", csrfToken);
            }
            return originalSend.apply(this, arguments);
        };
    }
});

function unblockUI() {
    const overlay = document.getElementById('uploadOverlay');
    if (overlay) {
        overlay.style.display = 'none';
        // Reset message
        overlay.querySelector('.message').innerText = "Uploading in progress...";
    }
    document.body.classList.remove('uploading');
    window.onbeforeunload = null;
}

// Dropdown menu support with improved hover stability
document.addEventListener('DOMContentLoaded', function () {
    // ============================
    // DROPDOWN MENU HANDLING - FIXED
    // ============================
    const dropdowns = document.querySelectorAll('.dropdown');
    let closeTimeout = null;

    dropdowns.forEach(dropdown => {
        const toggle = dropdown.querySelector('.dropdown-toggle');
        const menu = dropdown.querySelector('.dropdown-menu');

        if (toggle && menu) {
            // Mouse enter - open this dropdown and close others
            dropdown.addEventListener('mouseenter', function () {
                if (window.innerWidth > 768) {
                    // Clear any pending close timeout
                    if (closeTimeout) {
                        clearTimeout(closeTimeout);
                        closeTimeout = null;
                    }

                    // Close ALL other dropdowns immediately
                    dropdowns.forEach(other => {
                        if (other !== dropdown) {
                            other.classList.remove('active');
                        }
                    });

                    // Open this dropdown
                    dropdown.classList.add('active');
                }
            });

            // Mouse leave - delay before closing
            dropdown.addEventListener('mouseleave', function () {
                if (window.innerWidth > 768) {
                    closeTimeout = setTimeout(() => {
                        dropdown.classList.remove('active');
                    }, 150); // 150ms delay
                }
            });

            // Mobile click behavior
            toggle.addEventListener('click', function (e) {
                if (window.innerWidth <= 768) {
                    e.preventDefault();
                    e.stopPropagation();

                    const wasActive = dropdown.classList.contains('active');

                    // Close all dropdowns
                    dropdowns.forEach(other => {
                        other.classList.remove('active');
                    });

                    // Toggle this one
                    if (!wasActive) {
                        dropdown.classList.add('active');
                    }
                }
            });
        }
    });

    // ============================
    // SUBMENU HANDLING (Click-based)
    // ============================
    const submenus = document.querySelectorAll('.dropdown-submenu');

    submenus.forEach(submenu => {
        const toggle = submenu.querySelector('.dropdown-toggle');

        if (toggle) {
            toggle.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();

                // Close sibling submenus
                const siblings = submenu.parentElement.querySelectorAll('.dropdown-submenu');
                siblings.forEach(sibling => {
                    if (sibling !== submenu) {
                        sibling.classList.remove('active');
                    }
                });

                // Toggle this submenu
                submenu.classList.toggle('active');
            });
        }
    });

    // Close submenus when clicking elsewhere
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.dropdown-submenu')) {
            submenus.forEach(submenu => {
                submenu.classList.remove('active');
            });
        }
    });

    // Close all dropdowns when clicking outside
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.dropdown')) {
            dropdowns.forEach(dropdown => {
                dropdown.classList.remove('active');
            });
        }
    });

    // Close all dropdowns when mouse leaves navbar
    const navbar = document.querySelector('.navbar');
    if (navbar) {
        navbar.addEventListener('mouseleave', function () {
            if (window.innerWidth > 768) {
                if (closeTimeout) {
                    clearTimeout(closeTimeout);
                }
                dropdowns.forEach(dropdown => {
                    dropdown.classList.remove('active');
                });
            }
        });
    }

    const chatFab = document.getElementById('globalChatFab');
    const chatOverlay = document.getElementById('globalChatOverlay');
    const chatWindow = document.getElementById('globalChatWindow');
    const chatClose = document.getElementById('globalChatClose');
    const voucherSelect = document.getElementById('globalChatVoucherType');

    const messagesEl = document.getElementById('vaChatMessages');
    const quickEl = document.getElementById('vaChatQuick');
    const inputEl = document.getElementById('vaChatInput');
    const sendBtn = document.getElementById('vaChatSendBtn');
    const micBtn = document.getElementById('vaChatMicBtn');

    if (micBtn) {
        let recognition = null;
        micBtn.addEventListener('click', function () {
            if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
                alert('Speech recognition is not supported in this browser.');
                return;
            }
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

            if (recognition && recognition.started) {
                recognition.stop();
                return;
            }

            recognition = new SpeechRecognition();
            recognition.lang = 'en-US';
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;

            recognition.onstart = function () {
                recognition.started = true;
                micBtn.style.color = 'red';
                inputEl.placeholder = 'Listening...';
            };

            recognition.onend = function () {
                recognition.started = false;
                micBtn.style.color = '';
                inputEl.placeholder = voucherExamples[voucherSelect.value] || 'Type here...';
            };

            recognition.onresult = function (event) {
                const transcript = event.results[0][0].transcript;
                if (inputEl) {
                    inputEl.value = transcript;
                    inputEl.focus();
                    updateSendAvailability();
                }
            };

            recognition.onerror = function (event) {
                console.error('Speech recognition error', event.error);
                recognition.stop();
            };

            recognition.start();
        });
    }

    const newBtn = document.getElementById('vaChatNewBtn');
    const submitBtn = document.getElementById('vaChatSubmitBtn');
    const showDraftsBtn = document.getElementById('vaChatShowDraftsBtn');
    const addAllBtn = document.getElementById('vaChatAddAllBtn');
    const ledgerPickerEl = document.getElementById('vaChatLedgerPicker');
    const ledgerSelectEl = document.getElementById('vaChatLedgerSelect');
    const ledgerOptionsEl = document.getElementById('vaChatLedgerOptions');
    const ledgerPickBtn = document.getElementById('vaChatLedgerPickBtn');
    const aiInvoiceInput = document.getElementById('vaAIInvoiceInput');

    // AI Invoice Upload Handler
    if (aiInvoiceInput) {
        aiInvoiceInput.addEventListener('change', async function () {
            const file = this.files[0];
            if (!file) return;

            const vt = assistantState.voucherType;
            if (!['Purchase', 'Expense'].includes(vt)) {
                globalChatAppendMessage('bot', 'AI Invoice processing is only for Purchase or Expense.');
                return;
            }

            globalChatAppendMessage('user', `Uploading ${file.name}...`);
            globalChatAppendMessage('bot', '⏳ Processing invoice with AI. Please wait...');

            const formData = new FormData();
            formData.append('file', file);
            formData.append('invoice_type', vt);

            try {
                const res = await fetch('/api/upload_and_analyze_invoice', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();

                if (!data.success) {
                    globalChatAppendMessage('bot', `❌ Error: ${data.message}`);
                    return;
                }

                if (data.type === 'Purchase') {
                    globalChatAppendMessage('bot', '✅ Invoice processed successfully!');

                    // Show download link
                    const linkRow = document.createElement('div');
                    linkRow.className = 'rv-msg bot';
                    const bubble = document.createElement('div');
                    bubble.className = 'rv-bubble';
                    bubble.innerHTML = `📥 <a href="${data.download_url}" download style="color: #007bff; text-decoration: underline;">Download Excel for Import</a>`;
                    linkRow.appendChild(bubble);
                    if (messagesEl) messagesEl.appendChild(linkRow);

                    globalChatAppendMessage('bot', 'Upload this Excel to Purchase Import to create vouchers.');

                } else if (data.type === 'Expense') {
                    globalChatAppendMessage('bot', 'I found the following details from your invoice:');
                    const ed = data.data;
                    globalChatAppendMessage('bot', `Party: ${ed.party_name || 'N/A'}\nAmount: ${ed.total_amount || 'N/A'}\nVAT: ${ed.vat_amount || 0}\nDate: ${ed.invoice_date || 'N/A'}\nDescription: ${ed.narration || 'N/A'}`);

                    // Store for posting after ledger selection
                    window._pendingExpenseData = ed;
                    window._pendingExpenseData.debitLedger = '';
                    window._pendingExpenseData.creditLedger = '';

                    // Fetch ledgers for selection
                    globalChatAppendMessage('bot', 'Now, please tell me which accounts to use for this expense:');

                    // Create ledger selection UI
                    const ledgerContainer = document.createElement('div');
                    ledgerContainer.className = 'rv-expense-ledger-selection';
                    ledgerContainer.style.cssText = 'background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 10px 0;';

                    const hasVat = parseFloat(ed.vat_amount) > 0;

                    ledgerContainer.innerHTML = `
                        <div style="margin-bottom: 10px;">
                            <label style="display: block; font-weight: bold; margin-bottom: 5px;">What type of expense is this?</label>
                            <input type="text" id="aiExpenseDebitLedger" class="form-control" list="aiDebitLedgerList" placeholder="e.g., Office Expenses, Fuel, Repairs">
                            <datalist id="aiDebitLedgerList"></datalist>
                        </div>
                        ${hasVat ? `
                        <div style="margin-bottom: 10px;">
                            <label style="display: block; font-weight: bold; margin-bottom: 5px;">VAT Input Ledger (VAT: ${ed.vat_amount}):</label>
                            <input type="text" id="aiExpenseVatLedger" class="form-control" list="aiVatLedgerList" placeholder="e.g., VAT Input, Input VAT">
                            <datalist id="aiVatLedgerList"></datalist>
                        </div>
                        ` : ''}
                        <div style="margin-bottom: 10px;">
                            <label style="display: block; font-weight: bold; margin-bottom: 5px;">Cost Center:</label>
                            <input type="text" id="aiExpenseCostCenter" class="form-control" list="aiCostCenterList" placeholder="Select Cost Center (if applicable)">
                            <datalist id="aiCostCenterList"></datalist>
                        </div>
                        <div style="margin-bottom: 10px;">
                            <label style="display: block; font-weight: bold; margin-bottom: 5px;">How was this paid?</label>
                            <input type="text" id="aiExpenseCreditLedger" class="form-control" list="aiCreditLedgerList" placeholder="e.g., Cash, Bank, Petty Cash">
                            <datalist id="aiCreditLedgerList"></datalist>
                        </div>
                        <div style="display: flex; gap: 10px; margin-top: 15px;">
                            <button id="aiExpensePostBtn" class="btn btn-success btn-sm">Create Expense</button>
                            <button id="aiExpenseCancelBtn" class="btn btn-secondary btn-sm">Cancel</button>
                        </div>
                    `;

                    if (messagesEl) messagesEl.appendChild(ledgerContainer);

                    // Fetch allowed ledgers for debit and credit sides
                    try {
                        // Fetch cost centers first
                        const ccRes = await fetch('/api/master/get_cost_centers');
                        if (ccRes.ok) {
                            const costCenters = await ccRes.json();
                            const ccList = document.getElementById('aiCostCenterList');
                            if (ccList && Array.isArray(costCenters)) {
                                costCenters.forEach(cc => {
                                    // Assuming cc is {center_code: '...', center_name: '...'} or just name strings if simplified
                                    // The API returns {center_code, center_name} objects based on master_routes.py
                                    const opt = document.createElement('option');
                                    opt.value = cc.center_name;
                                    ccList.appendChild(opt);
                                });
                            }
                        }

                        // Fetch debit ledgers (expense accounts)
                        const debitRes = await fetch('/api/master/get_ledgers?voucher_type=Expense&side=Debit');
                        const debitLedgers = await debitRes.json();
                        const debitList = document.getElementById('aiDebitLedgerList');

                        if (debitList && Array.isArray(debitLedgers)) {
                            debitLedgers.forEach(l => {
                                const opt = document.createElement('option');
                                opt.value = l.ledger_name;
                                debitList.appendChild(opt);
                            });
                        }

                        // Also populate VAT ledger list with same debit ledgers
                        if (hasVat) {
                            const vatList = document.getElementById('aiVatLedgerList');
                            if (vatList && Array.isArray(debitLedgers)) {
                                debitLedgers.forEach(l => {
                                    const opt = document.createElement('option');
                                    opt.value = l.ledger_name;
                                    vatList.appendChild(opt);
                                });
                            }
                        }

                        // Fetch credit ledgers (payment accounts)
                        const creditRes = await fetch('/api/master/get_ledgers?voucher_type=Expense&side=Credit');
                        const creditLedgers = await creditRes.json();
                        const creditList = document.getElementById('aiCreditLedgerList');

                        if (creditList && Array.isArray(creditLedgers)) {
                            creditLedgers.forEach(l => {
                                const opt = document.createElement('option');
                                opt.value = l.ledger_name;
                                creditList.appendChild(opt);
                            });
                        }
                    } catch (e) {
                        console.error('Failed to fetch ledgers:', e);
                    }

                    // Add event listeners
                    document.getElementById('aiExpensePostBtn').onclick = () => postExpenseFromAI();
                    document.getElementById('aiExpenseCancelBtn').onclick = () => {
                        globalChatAppendMessage('bot', 'No problem, I\'ve cancelled this expense.');
                        window._pendingExpenseData = null;
                        ledgerContainer.remove();
                    };
                }
            } catch (err) {
                globalChatAppendMessage('bot', `❌ Error: ${err.message}`);
            }

            // Reset input
            this.value = '';
        });
    }

    // Helper to post expense from AI extracted data
    async function postExpenseFromAI() {
        const ed = window._pendingExpenseData;
        if (!ed) {
            globalChatAppendMessage('bot', 'Sorry, I couldn\'t find the expense details. Please try uploading the invoice again.');
            return;
        }

        const debitLedger = document.getElementById('aiExpenseDebitLedger')?.value?.trim();
        const creditLedger = document.getElementById('aiExpenseCreditLedger')?.value?.trim();
        const vatLedger = document.getElementById('aiExpenseVatLedger')?.value?.trim();
        const costCenter = document.getElementById('aiExpenseCostCenter')?.value?.trim() || '';

        const vatAmount = parseFloat(ed.vat_amount) || 0;
        const totalAmount = parseFloat(ed.total_amount) || 0;
        const netAmount = totalAmount - vatAmount;  // Expense amount without VAT

        if (!debitLedger || !creditLedger) {
            globalChatAppendMessage('bot', 'Please select both the expense type and payment method before continuing.');
            return;
        }

        if (vatAmount > 0 && !vatLedger) {
            globalChatAppendMessage('bot', 'Please select a VAT ledger for the VAT amount.');
            return;
        }

        globalChatAppendMessage('bot', 'Creating your expense entry...');

        const params = new URLSearchParams();
        params.append('voucher_type', 'Expense');
        params.append('date', ed.invoice_date || new Date().toISOString().slice(0, 10));
        params.append('narration', ed.narration || `Invoice ${ed.invoice_number || ''}`);
        
        // Header Cost Center (optional but good to have if backend supports it)
        if (costCenter) {
             params.append('cost_center_name', costCenter);
        }

        // Debit entry 1: Expense (net amount without VAT)
        params.append('ledger_name[]', debitLedger);
        params.append('ledger_amount[]', String(netAmount));
        params.append('ledger_type[]', 'Debit');
        params.append('ledger_cost_center[]', costCenter);
        params.append('ledger_vat_applicable[]', '0');
        params.append('ledger_vat_amount[]', '0');

        // Debit entry 2: VAT Input (if applicable)
        if (vatAmount > 0 && vatLedger) {
            params.append('ledger_name[]', vatLedger);
            params.append('ledger_amount[]', String(vatAmount));
            params.append('ledger_type[]', 'Debit');
            params.append('ledger_cost_center[]', costCenter);
            params.append('ledger_vat_applicable[]', '0');
            params.append('ledger_vat_amount[]', '0');
        }

        // Credit entry: Payment (total amount including VAT)
        params.append('ledger_name[]', creditLedger);
        params.append('ledger_amount[]', String(totalAmount));
        params.append('ledger_type[]', 'Credit');
        params.append('ledger_cost_center[]', costCenter);
        params.append('ledger_vat_applicable[]', '0');
        params.append('ledger_vat_amount[]', '0');

        try {
            const res = await fetch('/add_voucher', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: params.toString()
            });
            const data = await res.json();

            if (data.success) {
                globalChatAppendMessage('bot', `✅ Expense voucher created: ${data.voucher_number || ''}`);
                // Remove ledger selection UI
                document.querySelector('.rv-expense-ledger-selection')?.remove();
            } else {
                globalChatAppendMessage('bot', `❌ Failed: ${data.message || 'Unknown error'}`);
            }
        } catch (err) {
            globalChatAppendMessage('bot', `❌ Error posting: ${err.message}`);
        }

        window._pendingExpenseData = null;
    }

    function formatChatTime(d = new Date()) {
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const yy = String(d.getFullYear()).slice(-2);
        const hh = String(d.getHours()).padStart(2, '0');
        const min = String(d.getMinutes()).padStart(2, '0');
        return `${dd}-${mm}-${yy} ${hh}:${min}`;
    }

    function globalChatAppendMessage(who, text) {
        if (!messagesEl) return;
        const row = document.createElement('div');
        row.className = `rv-msg ${who}`;
        const bubble = document.createElement('div');
        bubble.className = 'rv-bubble';
        if (who === 'user') {
            bubble.textContent = text;
        } else {
            bubble.innerHTML = text;
        }
        const time = document.createElement('div');
        time.className = 'rv-time';
        time.textContent = formatChatTime();
        bubble.appendChild(time);
        row.appendChild(bubble);
        messagesEl.appendChild(row);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    const voucherExamples = {
        Receipt: 'received 5000 from ABC by cash today',
        Payment: 'paid 1200 to ABC by cash today',
        Contra: 'transfer 1000 from Cash to Bank today',
        Expense: 'expense 300 for Fuel by cash today',
        'Service Income': 'income 5000 for Consulting from Client',
    };

    function updateInputPlaceholder() {
        if (!inputEl) return;
        const hintEl = document.getElementById('vaChatHint');
        const vt = assistantState.voucherType;
        if (!vt) {
            const hint = 'Ask any accounting question...';
            inputEl.placeholder = hint;
            if (hintEl) hintEl.textContent = hint;
            return;
        }
        const example = voucherExamples[vt] || 'Type your voucher here';
        const hint = `Example: ${example}. Date format dd-mm-yy.`;
        inputEl.placeholder = hint;
        if (hintEl) hintEl.textContent = hint;
    }

    function updateSendAvailability() {
        if (!sendBtn || !inputEl) return;
        const hasText = normalizeText(inputEl.value).length > 0;
        const disabled = !hasText || inputEl.disabled;
        // console.log('Chatbot: updateSendAvailability', { hasText, inputDisabled: inputEl.disabled, disabled });
        sendBtn.disabled = disabled;
    }

    function setChatActionsEnabled(enabled) {
        const buttons = [newBtn, submitBtn, showDraftsBtn, addAllBtn, sendBtn];
        buttons.forEach(b => {
            if (!b) return;
            b.disabled = !enabled;
        });
        if (inputEl) inputEl.disabled = !enabled;
        if (chatWindow) chatWindow.classList.toggle('va-enabled', enabled);
        updateSendAvailability();
    }

    function currentVoucherSlug() {
        const vt = window.__currentVoucherType;
        if (vt === 'Receipt') return 'receipt';
        if (vt === 'Payment') return 'payment';
        if (vt === 'Contra') return 'contra';
        if (vt === 'Expense') return 'expense';
        return null;
    }

    function readSelectedVoucherSlug() {
        if (voucherSelect && voucherSelect.value) return voucherSelect.value;
        const saved = localStorage.getItem('globalChatVoucherType') || '';
        return saved;
    }

    function applySelectedVoucherSlug(slug) {
        if (voucherSelect) voucherSelect.value = slug || '';
        localStorage.setItem('globalChatVoucherType', slug || '');
    }

    function normalizeText(s) { return (s || '').toString().trim(); }
    function safeLower(s) { return normalizeText(s).toLowerCase(); }
    function parseAmount(text) {
        const m = (text || '').replace(/,/g, '').match(/(\d+(\.\d{1,2})?)\s*([km])?/i);
        if (!m) return null;
        let val = Number(m[1]);
        const suffix = (m[3] || '').toLowerCase();
        if (suffix === 'k') val *= 1000;
        if (suffix === 'm') val *= 1000000;
        return Number.isFinite(val) ? Math.round(val * 100) / 100 : null;
    }
    function parseDateFromText(text) {
        const t = safeLower(text);
        if (t.includes('today')) {
            const d = new Date();
            const yy = String(d.getFullYear()).slice(-2);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${dd}-${mm}-${yy}`;
        }
        if (t.includes('yesterday')) {
            const d = new Date();
            d.setDate(d.getDate() - 1);
            const yy = String(d.getFullYear()).slice(-2);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${dd}-${mm}-${yy}`;
        }
        const iso = (text || '').match(/\b(\d{4})-(\d{2})-(\d{2})\b/);
        if (iso) {
            const yy = String(iso[1]).slice(-2);
            return `${iso[3]}-${iso[2]}-${yy}`;
        }
        const dmy = (text || '').match(/\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b/);
        if (dmy) {
            const dd = String(dmy[1]).padStart(2, '0');
            const mm = String(dmy[2]).padStart(2, '0');
            let yy = dmy[3];
            if (yy.length === 4) yy = yy.slice(-2);
            return `${dd}-${mm}-${yy}`;
        }

        // Handle "27th Jan 2026" or "27 Jan 26"
        const verbose = (text || '').match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-zA-Z]+)\s+(\d{2,4})\b/i);
        if (verbose) {
            const dd = String(verbose[1]).padStart(2, '0');
            const monStr = verbose[2].toLowerCase();
            let yy = verbose[3];
            if (yy.length === 4) yy = yy.slice(-2);

            const monthMap = {
                jan: '01', feb: '02', mar: '03', apr: '04', may: '05', jun: '06',
                jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12',
                january: '01', february: '02', march: '03', april: '04', june: '06',
                july: '07', august: '08', september: '09', october: '10', november: '11', december: '12'
            };

            // Try partial match if full match fails (e.g. "Sept" -> "09")
            let mm = monthMap[monStr];
            if (!mm) {
                const key = Object.keys(monthMap).find(k => k.startsWith(monStr) || monStr.startsWith(k));
                if (key) mm = monthMap[key];
            }

            if (mm) {
                return `${dd}-${mm}-${yy}`;
            }
        }

        return null;
    }

    function clearQuick() {
        if (!quickEl) return;
        quickEl.innerHTML = '';
    }

    function hideLedgerPicker() {
        if (ledgerPickerEl) ledgerPickerEl.hidden = true;
        if (ledgerSelectEl) ledgerSelectEl.value = '';
        assistantState.pendingLedgerField = '';
        if (inputEl) inputEl.disabled = false;
        if (sendBtn) sendBtn.disabled = false;
        updateSendAvailability();
    }

    function setLedgerSelectOptions(names) {
        if (!ledgerOptionsEl) return;
        const list = Array.isArray(names) ? names : [];
        ledgerOptionsEl.innerHTML = '';
        list.forEach(n => {
            const opt = document.createElement('option');
            opt.value = n;

            // Show balance in label if available
            if (vaBootstrap && vaBootstrap.ledgers) {
                const l = vaBootstrap.ledgers.find(x => x.name === n);
                if (l && typeof l.balance === 'number') {
                    const type = l.balance >= 0 ? 'Dr' : 'Cr';
                    const abs = Math.abs(l.balance).toFixed(2);
                    opt.label = `${n} (${abs} ${type})`;
                }
            }

            ledgerOptionsEl.appendChild(opt);
        });
    }

    function renderQuickButtons(buttons) {
        if (!quickEl) return;
        quickEl.innerHTML = '';
        (buttons || []).forEach(b => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rv-quick-btn';
            btn.textContent = b.label;
            btn.dataset.action = b.action || '';
            btn.dataset.value = b.value || '';
            quickEl.appendChild(btn);
        });
    }

    let vaBootstrap = null;
    async function loadVoucherAssistantBootstrap() {
        if (vaBootstrap) return vaBootstrap;
        const res = await fetch('/api/voucher_assistant_bootstrap', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            const msg = data && data.message ? data.message : `Failed with status ${res.status}`;
            throw new Error(msg);
        }
        vaBootstrap = {
            ledgers: Array.isArray(data.ledgers) ? data.ledgers : [],
            costCenters: Array.isArray(data.cost_centers) ? data.cost_centers : [],
            vatApplicable: !!data.vat_applicable,
            costCenterApplicable: !!data.cost_center_applicable,
            costCenterMandatory: !!data.cost_center_mandatory,
        };
        return vaBootstrap;
    }

    function ledgerNamesList() {
        return (vaBootstrap && vaBootstrap.ledgers) ? vaBootstrap.ledgers.map(l => l.name) : [];
    }

    function findLedgerCandidates(query, limit = 6) {
        const q = safeLower(query);
        if (!q) return [];
        const names = ledgerNamesList();
        const exact = names.find(n => safeLower(n) === q);
        if (exact) return [exact];
        const starts = names.filter(n => safeLower(n).startsWith(q));
        const contains = names.filter(n => safeLower(n).includes(q) && !safeLower(n).startsWith(q));
        return [...starts, ...contains].slice(0, limit);
    }

    function bestDefaultCashLedger() {
        const names = ledgerNamesList();
        const exact = names.find(n => safeLower(n) === 'cash');
        if (exact) return exact;
        const cashLike = names.find(n => safeLower(n).includes('cash'));
        return cashLike || null;
    }

    function bestDefaultBankLedger() {
        const names = ledgerNamesList();
        const bankLike = names.find(n => safeLower(n).includes('bank'));
        return bankLike || null;
    }

    function voucherDraftStorageKey(voucherType) {
        return `voucherDrafts:${voucherType}`;
    }

    function getDrafts(voucherType) {
        try {
            const raw = localStorage.getItem(voucherDraftStorageKey(voucherType));
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch {
            return [];
        }
    }

    function setDrafts(voucherType, drafts) {
        localStorage.setItem(voucherDraftStorageKey(voucherType), JSON.stringify(drafts || []));
    }

    function updateDraftCount(voucherType) {
        const el = document.getElementById('vaDraftCount');
        if (!el) return;
        el.textContent = String(getDrafts(voucherType).length);
    }

    let statusTimer = null;
    function setStatus(text, isError = false) {
        const el = document.getElementById('vaChatStatus');
        if (!el) return;
        el.textContent = text || '';
        el.style.color = isError ? '#b00020' : '#2b6a2b';
        if (statusTimer) clearTimeout(statusTimer);
        if (text && !isError) {
            statusTimer = setTimeout(() => {
                el.textContent = '';
            }, 4000);
        }
    }

    function buildDraftSummary(d) {
        const date = d.date || '';
        const amount = (typeof d.amount === 'number') ? Number(d.amount).toFixed(2) : '';
        const narration = d.narration || '';
        const entries = Array.isArray(d.ledgerEntries) ? d.ledgerEntries : [];
        const debits = entries.filter(e => e.type === 'Debit').map(e => e.ledgerName).filter(Boolean);
        const credits = entries.filter(e => e.type === 'Credit').map(e => e.ledgerName).filter(Boolean);
        const cc = d.costCenterName ? `\nCost Center: ${d.costCenterName}` : '';
        return `Type: ${d.voucherType}\nDate: ${date}\nAmount: ${amount}\nDebit: ${debits.join(', ')}\nCredit: ${credits.join(', ')}${cc}\nNarration: ${narration}`;
    }

    function tryParseOneLineVoucher(text) {
        const amount = parseAmount(text);
        const date = parseDateFromText(text);
        let fromParty = null;
        let toParty = null;
        const fromMatch = (text || '').match(/\bfrom\s+(.+?)(?=\s+(by|into|to|on|date|today|narration|note)\b|$)/i);
        if (fromMatch) fromParty = normalizeText(fromMatch[1]);
        const toMatch = (text || '').match(/\bto\s+(.+?)(?=\s+(by|from|into|on|date|today|narration|note)\b|$)/i);
        if (toMatch) toParty = normalizeText(toMatch[1]);

        let cashBankHint = null;
        if (/\bcash\b/i.test(text)) cashBankHint = 'cash';
        if (/\bbank\b/i.test(text)) cashBankHint = 'bank';

        let narration = '';
        const narrMatch = (text || '').match(/\b(narration|note)\s*[:\-]?\s*(.+)$/i);
        if (narrMatch) narration = normalizeText(narrMatch[2]);
        const forMatch = !narration ? (text || '').match(/\bfor\s+(.+)$/i) : null;
        if (forMatch) narration = normalizeText(forMatch[1]);

        return { amount, date, narration, fromParty, toParty, cashBankHint };
    }

    async function postVoucher(draft) {
        const params = new URLSearchParams();
        params.append('voucher_type', draft.voucherType);
        params.append('date', draft.date);
        params.append('narration', draft.narration || '');
        if (draft.costCenterName) params.append('cost_center_name', draft.costCenterName);

        const entries = Array.isArray(draft.ledgerEntries) ? draft.ledgerEntries : [];
        entries.forEach(e => {
            params.append('ledger_name[]', e.ledgerName);
            params.append('ledger_amount[]', String(e.amount));
            params.append('ledger_type[]', e.type);
            params.append('ledger_cost_center[]', draft.costCenterName || '');
        });

        if (draft.voucherType === 'Expense') {
            entries.forEach(() => {
                params.append('ledger_vat_applicable[]', '0');
                params.append('ledger_vat_amount[]', '0');
            });
        }

        const res = await fetch('/add_voucher', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: params.toString()
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            const msg = (data && data.message) ? data.message : `Failed with status ${res.status}`;
            throw new Error(msg);
        }
        return data;
    }

    const voucherTypeMap = {
        receipt: 'Receipt',
        payment: 'Payment',
        contra: 'Contra',
        expense: 'Expense',
        sales: 'Sales',
        purchase: 'Purchase',
        service_income: 'Service Income',
    };

    const assistantState = {
        voucherType: '',
        step: 'idle',
        current: null,
        pendingLedgerField: '',
        autoNext: null,
        autoFinalize: false,
    };

    function ensureVoucherTypeSelected() {
        const slug = readSelectedVoucherSlug();
        const vt = voucherTypeMap[slug] || '';
        assistantState.voucherType = vt;
        return vt;
    }

    function setVoucherTypeFromSlug(slug) {
        applySelectedVoucherSlug(slug);
        const vt = voucherTypeMap[slug] || '';
        assistantState.voucherType = vt;
        assistantState.step = 'idle';
        assistantState.current = null;
        assistantState.pendingLedgerField = '';
        assistantState.autoNext = null;
        assistantState.autoFinalize = false;
        setStatus('', false);
        clearQuick();
        hideLedgerPicker();
        updateDraftCount(assistantState.voucherType || '');
        if (newBtn) newBtn.textContent = assistantState.voucherType ? `New ${assistantState.voucherType}` : 'New';

        // --- Custom Logic for Import-Only Vouchers ---
        const isImportOnly = ['Sales', 'Purchase'].includes(vt);
        if (isImportOnly) {
            setChatActionsEnabled(false); // Disable inputs
            if (messagesEl) messagesEl.innerHTML = ''; // Clear chat

            if (vt === 'Purchase') {
                // AI Invoice Processing for Purchase
                globalChatAppendMessage('bot', `Purchase Invoice Processing with AI.`);
                globalChatAppendMessage('bot', 'Upload a PDF or Image to extract invoice data automatically.');

                const container = document.createElement('div');
                container.className = 'rv-chat-options';
                container.style.display = 'flex';
                container.style.flexDirection = 'column';
                container.style.gap = '10px';
                container.style.marginTop = '10px';

                // AI Upload Button
                const aiUpBtn = document.createElement('button');
                aiUpBtn.className = 'btn btn-primary btn-sm';
                aiUpBtn.innerHTML = '📄 Upload Invoice (AI Extract)';
                aiUpBtn.onclick = () => {
                    const aiInput = document.getElementById('vaAIInvoiceInput');
                    if (aiInput) {
                        aiInput.value = '';
                        aiInput.click();
                    }
                };

                // Download Template Button
                const dlBtn = document.createElement('button');
                dlBtn.className = 'btn btn-secondary btn-sm';
                dlBtn.textContent = 'Download Template';
                dlBtn.onclick = () => {
                    window.location.href = `/download_voucher_template/${encodeURIComponent(vt)}`;
                };

                // Manual Upload Excel
                const upBtn = document.createElement('button');
                upBtn.className = 'btn btn-outline-secondary btn-sm';
                upBtn.textContent = 'Upload Manual Excel';
                upBtn.onclick = () => {
                    const fileInput = document.getElementById('vaChatFileInput');
                    if (fileInput) {
                        fileInput.value = '';
                        fileInput.click();
                    }
                };

                container.appendChild(aiUpBtn);
                container.appendChild(dlBtn);
                container.appendChild(upBtn);
                if (messagesEl) messagesEl.appendChild(container);
            } else {
                // Sales - keep original behavior
                globalChatAppendMessage('bot', `Chat is not available for ${vt}.`);
                globalChatAppendMessage('bot', 'Please use the buttons below to manage vouchers via Excel.');

                const container = document.createElement('div');
                container.className = 'rv-chat-options';
                container.style.display = 'flex';
                container.style.gap = '10px';
                container.style.marginTop = '10px';

                const dlBtn = document.createElement('button');
                dlBtn.className = 'btn btn-secondary btn-sm';
                dlBtn.textContent = 'Download Template';
                dlBtn.onclick = () => {
                    window.location.href = `/download_voucher_template/${encodeURIComponent(vt)}`;
                };

                const upBtn = document.createElement('button');
                upBtn.className = 'btn btn-primary btn-sm';
                upBtn.textContent = 'Upload Excel';
                upBtn.onclick = () => {
                    const fileInput = document.getElementById('vaChatFileInput');
                    if (fileInput) {
                        fileInput.value = '';
                        fileInput.click();
                    }
                };

                container.appendChild(dlBtn);
                container.appendChild(upBtn);
                if (messagesEl) messagesEl.appendChild(container);
            }

            // Disable input field specifically and set placeholder
            if (inputEl) {
                inputEl.placeholder = 'Chat disabled for this voucher type.';
                inputEl.disabled = true;
            }
            if (sendBtn) sendBtn.disabled = true;

            return;
        }

        // Enable chat actions for General Chat and other voucher types
        setChatActionsEnabled(true);
        updateInputPlaceholder();
        updateSendAvailability();
        if (assistantState.voucherType) {
            globalChatAppendMessage('bot', `Switched to ${assistantState.voucherType}. Type "new" to start.`);

            // Add AI Invoice option for Expense mode
            if (assistantState.voucherType === 'Expense') {
                const container = document.createElement('div');
                container.className = 'rv-chat-options';
                container.style.display = 'flex';
                container.style.gap = '10px';
                container.style.marginTop = '8px';
                container.style.marginBottom = '8px';

                const aiBtn = document.createElement('button');
                aiBtn.className = 'btn btn-outline-primary btn-sm';
                aiBtn.innerHTML = '📄 Upload Invoice (AI Extract)';
                aiBtn.onclick = () => {
                    const aiInput = document.getElementById('vaAIInvoiceInput');
                    if (aiInput) {
                        aiInput.value = '';
                        aiInput.click();
                    }
                };
                container.appendChild(aiBtn);

                if (messagesEl) messagesEl.appendChild(container);
            }
        } else {
            // General Chat
            globalChatAppendMessage('bot', 'Hi! Ask me accounting questions or select a voucher type to create one.');
        }
    }

    function ensureCurrent() {
        if (assistantState.current) return;
        const dateVal = parseDateFromText('today');
        assistantState.current = {
            voucherType: assistantState.voucherType,
            date: dateVal,
            amount: null,
            narration: '',
            costCenterName: '',
            partyLedgerName: '',
            accountLedgerName: '',
            fromLedgerName: '',
            toLedgerName: '',
            expenseLedgerName: '',
            incomeLedgerName: '',
        };
    }

    function setAccountFromHint(hintText) {
        const hint = safeLower(hintText);
        if (hint === 'cash') return bestDefaultCashLedger();
        if (hint === 'bank') return bestDefaultBankLedger();
        return null;
    }

    function setLedgerFieldFromText(field, promptText, text) {
        let candidates = findLedgerCandidates(text, 6);

        // Filter for Receipt/Payment restriction
        const vt = assistantState.voucherType;
        const all = (vaBootstrap && vaBootstrap.ledgers) ? vaBootstrap.ledgers : [];
        const isRestricted = (vt === 'Receipt' || vt === 'Payment') && field === 'accountLedgerName';

        if (isRestricted) {
            candidates = candidates.filter(name => {
                const match = all.find(l => l.name === name);
                return match && (match.group_code === 'G005' || match.group_code === 'G006');
            });
        }

        if (candidates.length === 1) {
            assistantState.current[field] = candidates[0];
            return true;
        }
        const q = normalizeText(text);
        let sugg = candidates.length ? candidates : findLedgerCandidates(q.split(' ')[0], 6);

        if (isRestricted && !candidates.length) {
            sugg = sugg.filter(name => {
                const match = all.find(l => l.name === name);
                return match && (match.group_code === 'G005' || match.group_code === 'G006');
            });
        }

        if (sugg.length) {
            globalChatAppendMessage('bot', promptText);
            renderQuickButtons(sugg.map(v => ({ label: v, action: `pick_${field}`, value: v })));
        } else {
            globalChatAppendMessage('bot', 'I could not find that ledger. Type the exact ledger name.');
            clearQuick();
        }
        return false;
    }

    async function showLedgerPicker(field, promptText, preselectText = '') {
        if (!ledgerPickerEl || !ledgerSelectEl) return;
        const vt = ensureVoucherTypeSelected();
        if (!vt) {
            globalChatAppendMessage('bot', 'Choose a voucher type from the dropdown to start.');
            return;
        }
        if (inputEl) inputEl.disabled = true;
        if (sendBtn) sendBtn.disabled = true;
        try {
            await loadVoucherAssistantBootstrap();
        } catch (e) {
            globalChatAppendMessage('bot', e.message || String(e));
            return;
        }
        clearQuick();
        ledgerSelectEl.value = '';

        // Filter options for Receipt/Payment (Cash/Bank restrictions)
        let allLedgers = (vaBootstrap && vaBootstrap.ledgers) ? vaBootstrap.ledgers : [];
        let namesToShow = [];
        const vtState = assistantState.voucherType;

        if ((vtState === 'Receipt' || vtState === 'Payment') && field === 'accountLedgerName') {
            namesToShow = allLedgers
                .filter(l => l.group_code === 'G005' || l.group_code === 'G006')
                .map(l => l.name);
        } else {
            namesToShow = allLedgers.map(l => l.name);
        }

        setLedgerSelectOptions(namesToShow);
        assistantState.pendingLedgerField = field;
        globalChatAppendMessage('bot', promptText);
        ledgerPickerEl.hidden = false;
        const wanted = normalizeText(preselectText);
        if (wanted) {
            const candidates = findLedgerCandidates(wanted, 1);
            if (candidates.length === 1) ledgerSelectEl.value = candidates[0];
        }
        if (!ledgerSelectEl.value && (field === 'accountLedgerName' || field === 'fromLedgerName' || field === 'toLedgerName')) {
            const candidates = findLedgerCandidates('cash', 1);
            if (candidates.length === 1) ledgerSelectEl.value = candidates[0];
        }
        ledgerSelectEl.focus();
    }

    async function applyPickedLedger(field, value) {
        ensureCurrent();
        if (!assistantState.current) return;
        assistantState.current[field] = value;
        hideLedgerPicker();
        clearQuick();

        const vt = assistantState.voucherType;
        const needCC = vaBootstrap && vaBootstrap.costCenterApplicable && vaBootstrap.costCenterMandatory && vt === 'Expense';

        if (assistantState.autoNext && assistantState.autoNext.afterField === field) {
            const next = assistantState.autoNext;
            assistantState.autoNext = null;
            if (next.nextField) {
                assistantState.step = next.nextStep || assistantState.step;
                await showLedgerPicker(next.nextField, next.promptText || 'Select ledger:', next.preselectText || '');
                return;
            }
            if (next.nextAction === 'finalize') {
                assistantState.autoFinalize = false;
                finalizeAndOfferActions();
                return;
            }
        }

        if (assistantState.autoFinalize && (field === 'accountLedgerName' || field === 'toLedgerName')) {
            if (field === 'accountLedgerName' && needCC && !assistantState.current.costCenterName) {
                assistantState.autoFinalize = false;
                assistantState.step = 'ask_cost_center';
                globalChatAppendMessage('bot', 'Select Cost Center. Type name to search.');
                return;
            }
            assistantState.autoFinalize = false;
            finalizeAndOfferActions();
            return;
        }

        if (field === 'partyLedgerName' || field === 'expenseLedgerName' || field === 'incomeLedgerName') {
            assistantState.step = 'ask_amount';
            globalChatAppendMessage('bot', 'Enter amount (example: 1500).');
            return;
        }
        if (field === 'fromLedgerName') {
            assistantState.step = 'ask_to';
            await showLedgerPicker('toLedgerName', 'Select TO account (Debit):');
            return;
        }
        if (field === 'toLedgerName') {
            assistantState.step = 'ask_narration';
            globalChatAppendMessage('bot', 'Any narration? Type it or type "skip".');
            return;
        }
        if (field === 'accountLedgerName') {
            if (needCC && !assistantState.current.costCenterName) {
                assistantState.step = 'ask_cost_center';
                globalChatAppendMessage('bot', 'Select Cost Center. Type name to search.');
                return;
            }
            assistantState.step = 'ask_narration';
            globalChatAppendMessage('bot', 'Any narration? Type it or type "skip".');
            return;
        }
    }

    function buildDraftFromCurrent() {
        const c = assistantState.current;
        const vt = assistantState.voucherType;
        if (!c) throw new Error('No voucher in progress');
        if (!c.date) throw new Error('Date is required');
        if (typeof c.amount !== 'number') throw new Error('Amount is required');

        const needCC = vaBootstrap && vaBootstrap.costCenterApplicable && vaBootstrap.costCenterMandatory && vt === 'Expense';
        if (needCC && !normalizeText(c.costCenterName)) throw new Error('Cost Center is required');

        let ledgerEntries = [];
        if (vt === 'Receipt') {
            if (!c.partyLedgerName) throw new Error('Party is required');
            if (!c.accountLedgerName) throw new Error('Cash/Bank account is required');
            ledgerEntries = [
                { ledgerName: c.accountLedgerName, amount: c.amount, type: 'Debit' },
                { ledgerName: c.partyLedgerName, amount: c.amount, type: 'Credit' },
            ];
        } else if (vt === 'Payment') {
            if (!c.partyLedgerName) throw new Error('Party is required');
            if (!c.accountLedgerName) throw new Error('Cash/Bank account is required');
            ledgerEntries = [
                { ledgerName: c.partyLedgerName, amount: c.amount, type: 'Debit' },
                { ledgerName: c.accountLedgerName, amount: c.amount, type: 'Credit' },
            ];
        } else if (vt === 'Contra') {
            if (!c.fromLedgerName) throw new Error('From account is required');
            if (!c.toLedgerName) throw new Error('To account is required');
            ledgerEntries = [
                { ledgerName: c.toLedgerName, amount: c.amount, type: 'Debit' },
                { ledgerName: c.fromLedgerName, amount: c.amount, type: 'Credit' },
            ];
        } else if (vt === 'Expense') {
            if (!c.expenseLedgerName) throw new Error('Expense ledger is required');
            if (!c.accountLedgerName) throw new Error('Paid from account is required');
            ledgerEntries = [
                { ledgerName: c.expenseLedgerName, amount: c.amount, type: 'Debit' },
                { ledgerName: c.accountLedgerName, amount: c.amount, type: 'Credit' },
            ];
        } else if (vt === 'Service Income') {
            if (!c.incomeLedgerName) throw new Error('Income ledger is required');
            if (!c.accountLedgerName) throw new Error('Received-into account is required');
            ledgerEntries = [
                { ledgerName: c.accountLedgerName, amount: c.amount, type: 'Debit' },
                { ledgerName: c.incomeLedgerName, amount: c.amount, type: 'Credit' },
            ];
        } else {
            throw new Error('Choose voucher type');
        }

        const narration = normalizeText(c.narration) || vt;
        return {
            voucherType: vt,
            date: c.date,
            amount: c.amount,
            narration,
            costCenterName: normalizeText(c.costCenterName),
            ledgerEntries,
            createdAt: new Date().toISOString(),
        };
    }

    function startNewVoucher() {
        const vt = ensureVoucherTypeSelected();
        if (!vt) {
            globalChatAppendMessage('bot', 'Choose a voucher type from the dropdown to start.');
            return;
        }
        assistantState.step = 'idle';
        assistantState.current = null;
        assistantState.autoNext = null;
        assistantState.autoFinalize = false;
        ensureCurrent();
        setStatus('', false);
        clearQuick();
        hideLedgerPicker();
        updateDraftCount(vt);
        if (vt === 'Receipt') {
            assistantState.step = 'ask_party';
            showLedgerPicker('partyLedgerName', 'Select party ledger (Credit):');
            return;
        }
        if (vt === 'Payment') {
            assistantState.step = 'ask_party';
            showLedgerPicker('partyLedgerName', 'Select party ledger (Debit):');
            return;
        }
        if (vt === 'Contra') {
            assistantState.step = 'ask_amount';
            globalChatAppendMessage('bot', 'Enter the amount to transfer (example: 1500).');
            return;
        }
        if (vt === 'Expense') {
            assistantState.step = 'ask_expense';
            showLedgerPicker('expenseLedgerName', 'Select expense ledger (Debit):');
            return;
        }
        if (vt === 'Service Income') {
            assistantState.step = 'ask_income';
            showLedgerPicker('incomeLedgerName', 'Select income ledger (Credit):');
            return;
        }
    }

    function showDrafts() {
        const vt = ensureVoucherTypeSelected();
        if (!vt) {
            globalChatAppendMessage('bot', 'Choose a voucher type from the dropdown to start.');
            return;
        }
        const drafts = getDrafts(vt);
        updateDraftCount(vt);
        if (!drafts.length) {
            globalChatAppendMessage('bot', `No drafts yet for ${vt}. Type "new" to create one.`);
            return;
        }
        const lines = drafts.map((d, i) => {
            const deb = (d.ledgerEntries || []).filter(e => e.type === 'Debit')[0]?.ledgerName || '';
            const cre = (d.ledgerEntries || []).filter(e => e.type === 'Credit')[0]?.ledgerName || '';
            return `${i + 1}) ${d.date} | ${Number(d.amount).toFixed(2)} | Dr ${deb} | Cr ${cre}`;
        });
        globalChatAppendMessage('bot', `Draft ${vt} vouchers:\n${lines.join('\n')}\nType "delete 1" to remove.`);
    }

    function deleteDraft(index1Based) {
        const vt = ensureVoucherTypeSelected();
        if (!vt) return;
        const drafts = getDrafts(vt);
        const idx = index1Based - 1;
        if (idx < 0 || idx >= drafts.length) {
            globalChatAppendMessage('bot', 'Draft number not found.');
            return;
        }
        const removed = drafts.splice(idx, 1)[0];
        setDrafts(vt, drafts);
        updateDraftCount(vt);
        globalChatAppendMessage('bot', `Deleted draft:\n${buildDraftSummary(removed)}`);
    }

    async function addAllDrafts() {
        const vt = ensureVoucherTypeSelected();
        if (!vt) {
            globalChatAppendMessage('bot', 'Choose a voucher type from the dropdown to start.');
            return;
        }
        const drafts = getDrafts(vt);
        updateDraftCount(vt);
        if (!drafts.length) {
            globalChatAppendMessage('bot', 'No drafts to add.');
            return;
        }
        setStatus('Adding drafts...', false);
        let ok = 0;
        const failed = [];
        for (let i = 0; i < drafts.length; i++) {
            const d = drafts[i];
            try {
                const result = await postVoucher(d);
                ok += 1;
                globalChatAppendMessage('bot', `Added: ${result.voucher_number}`);
            } catch (e) {
                failed.push(d);
                globalChatAppendMessage('bot', `Failed:\n${buildDraftSummary(d)}\n${e.message || e}`);
            }
        }
        setDrafts(vt, failed);
        updateDraftCount(vt);
        if (failed.length) setStatus(`Added ${ok}. Failed ${failed.length}.`, true);
        else setStatus(`Added ${ok}.`, false);
    }

    function finalizeAndOfferActions() {
        try {
            const draft = buildDraftFromCurrent();
            globalChatAppendMessage('bot', `Voucher ready:\n${buildDraftSummary(draft)}`);
            renderQuickButtons([
                { label: 'Submit Now', action: 'submit_now', value: '' },
                { label: 'Save Draft', action: 'save_draft', value: '' },
            ]);
            assistantState.step = 'confirm';
        } catch (e) {
            globalChatAppendMessage('bot', e.message || String(e));
        }
    }

    async function submitCurrentNow() {
        try {
            const draft = buildDraftFromCurrent();
            setStatus('Submitting...', false);
            const result = await postVoucher(draft);
            setStatus('', false);
            globalChatAppendMessage('bot', `Added: ${result.voucher_number}`);
            assistantState.step = 'idle';
            assistantState.current = null;
            clearQuick();
        } catch (e) {
            setStatus(e.message || String(e), true);
            globalChatAppendMessage('bot', e.message || String(e));
        }
    }

    function saveCurrentDraft() {
        try {
            const vt = ensureVoucherTypeSelected();
            if (!vt) return;
            const draft = buildDraftFromCurrent();
            const drafts = getDrafts(vt);
            drafts.push(draft);
            setDrafts(vt, drafts);
            updateDraftCount(vt);
            globalChatAppendMessage('bot', 'Saved to drafts.');
            assistantState.step = 'idle';
            assistantState.current = null;
            clearQuick();
        } catch (e) {
            globalChatAppendMessage('bot', e.message || String(e));
        }
    }

    function openGlobalChat() {
        if (!chatOverlay) return;
        chatOverlay.hidden = false;
        chatOverlay.removeAttribute('hidden');
        chatOverlay.style.removeProperty('display');
        hideLedgerPicker();
        const pageSlug = currentVoucherSlug();
        const savedSlug = readSelectedVoucherSlug();
        const slugToShow = pageSlug || savedSlug || '';
        applySelectedVoucherSlug(slugToShow);
        ensureVoucherTypeSelected();
        if (newBtn) newBtn.textContent = assistantState.voucherType ? `New ${assistantState.voucherType}` : 'New';
        updateInputPlaceholder();
        updateSendAvailability();

        if (!assistantState.voucherType) {
            setChatActionsEnabled(true);
            globalChatAppendMessage('bot', 'Hi! Ask me accounting questions or select a voucher type to create one.');
            return;
        }

        setChatActionsEnabled(true);
        loadVoucherAssistantBootstrap()
            .then(() => {
                updateDraftCount(assistantState.voucherType);
                if (messagesEl && messagesEl.dataset.vaWelcomeFor !== assistantState.voucherType) {
                    globalChatAppendMessage('bot', `Tell me your ${assistantState.voucherType} in simple words.\nType "new" to start, or one-line examples:\nReceipt: received 5000 from ABC by cash today\nPayment: paid 1200 to ABC by cash today\nContra: transfer 1000 from Cash to Bank today\nExpense: expense 300 for Fuel by cash today`);
                    messagesEl.dataset.vaWelcomeFor = assistantState.voucherType;
                }
            })
            .catch((e) => {
                setChatActionsEnabled(false);
                globalChatAppendMessage('bot', e.message || String(e));
            });

        if (inputEl && !inputEl.disabled) inputEl.focus();
    }

    function closeGlobalChat() {
        if (!chatOverlay) return;
        chatOverlay.hidden = true;
        chatOverlay.setAttribute('hidden', '');
        chatOverlay.style.removeProperty('display');
        hideLedgerPicker();
    }

    function isAiEnabled() {
        const toggle = document.getElementById('vaChatAiToggle');
        if (toggle) return toggle.checked;
        return localStorage.getItem('vaChatAiEnabled') === '1';
    }

    function initAiToggle() {
        const toggle = document.getElementById('vaChatAiToggle');
        if (!toggle) return;
        toggle.checked = localStorage.getItem('vaChatAiEnabled') === '1';
        toggle.addEventListener('change', function () {
            localStorage.setItem('vaChatAiEnabled', toggle.checked ? '1' : '0');
        });
    }

    async function analyzeMessageWithAI(text) {
        console.log('analyzeMessageWithAI called with:', text);
        setStatus('Thinking...', false);
        try {
            const res = await fetch('/api/analyze_voucher_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, ai_enabled: isAiEnabled() })
            });
            console.log('API response status:', res.status);
            const data = await res.json();
            console.log('API response data:', data);
            if (!data.success) throw new Error(data.message);
            setStatus('', false);
            console.log('Returning AI data:', data.data);
            return data.data;
        } catch (e) {
            console.error('AI Error:', e);
            setStatus('', false);
            globalChatAppendMessage('bot', `AI Error: ${e.message}`);
            return null;
        }
    }

    function convertAiDataToParsed(aiData, vt) {
        const p = {
            amount: aiData.amount,
            date: aiData.date,
            narration: aiData.narration,
            fromParty: null,
            toParty: null,
            cashBankHint: null
        };

        const entries = aiData.ledger_entries || [];
        const debits = entries.filter(e => e.type === 'Debit');
        const credits = entries.filter(e => e.type === 'Credit');

        if (vt === 'Receipt') {
            if (credits.length) p.fromParty = credits[0].ledger;
            if (debits.length) p.cashBankHint = debits[0].ledger;
        } else if (vt === 'Payment') {
            if (debits.length) p.toParty = debits[0].ledger;
            if (credits.length) p.cashBankHint = credits[0].ledger;
        } else if (vt === 'Contra') {
            if (debits.length) p.toParty = debits[0].ledger;
            if (credits.length) p.fromParty = credits[0].ledger;
        } else if (vt === 'Expense') {
            if (credits.length) p.cashBankHint = credits[0].ledger;
            // Use debit ledger as narration hint if narration is missing, or rely on old logic's behavior
            if (debits.length && !p.narration) p.narration = debits[0].ledger;
        } else if (vt === 'Service Income') {
            if (debits.length) p.cashBankHint = debits[0].ledger;
            if (credits.length && !p.narration) p.narration = credits[0].ledger;
        }

        return p;
    }

    // When the bot asks for a report period, remember the original request so the
    // user's next message (e.g. "this month") completes it.
    let chatPendingDateQuery = null;

    async function handleGeneralChatQuery(query) {
        if (!query) return;

        // If the bot just asked for a period, combine the answer with the original request
        if (chatPendingDateQuery) {
            query = chatPendingDateQuery + ' ' + query;
            chatPendingDateQuery = null;
        }

        setStatus('Thinking...', false);
        try {
            const res = await fetch('/api/chat_query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: query, ai_enabled: isAiEnabled() })
            });
            const data = await res.json();
            setStatus('', false);

            if (data.success && data.data) {
                globalChatAppendMessage('bot', data.data.response);
                if (data.data.data && data.data.data.need_date) {
                    chatPendingDateQuery = data.data.data.pending_query || null;
                }
            } else {
                globalChatAppendMessage('bot', `Error: ${data.message || 'Unknown error'}`);
            }
        } catch (e) {
            setStatus('', false);
            globalChatAppendMessage('bot', `Error: ${e.message}`);
        }
    }

    async function sendFromInput() {
        console.log('Chatbot: sendFromInput called');
        if (!inputEl) {
            console.error('Chatbot: inputEl not found');
            return;
        }
        const text = (inputEl.value || '').toString().trim();
        console.log('Chatbot: sending text:', text);
        if (!text) return;
        inputEl.value = '';
        updateSendAvailability();
        globalChatAppendMessage('user', text);
        const lower = safeLower(text);

        if (lower === 'new' || lower === `new ${safeLower(assistantState.voucherType)}`) {
            startNewVoucher();
            return;
        }
        if (lower === 'drafts' || lower === 'show drafts') {
            showDrafts();
            return;
        }
        if (lower === 'add all' || lower === 'add all vouchers') {
            addAllDrafts();
            return;
        }
        if (lower.startsWith('delete ')) {
            const n = parseInt(lower.replace('delete', '').trim(), 10);
            if (Number.isFinite(n)) deleteDraft(n);
            else globalChatAppendMessage('bot', 'Use: delete 1');
            return;
        }

        ensureCurrent();
        const vt = assistantState.voucherType;
        if (!vt) {
            await handleGeneralChatQuery(text);
            return;
        }

        if (assistantState.step === 'idle') {
            // AI INTEGRATION
            const aiData = await analyzeMessageWithAI(text);
            if (!aiData) return; // Error shown in status
            const parsed = convertAiDataToParsed(aiData, vt);

            // const parsed = tryParseOneLineVoucher(text); // OLD REGEX LOGIC REPLACED
            if (parsed.amount != null) assistantState.current.amount = parsed.amount;
            if (parsed.date) assistantState.current.date = parsed.date;
            if (parsed.narration) assistantState.current.narration = parsed.narration;

            if (vt === 'Receipt' && parsed.fromParty && parsed.amount != null) {
                const hinted = parsed.cashBankHint || 'cash';
                const accHint = setAccountFromHint(hinted) || hinted;
                assistantState.step = 'ask_party';
                assistantState.autoFinalize = true;
                assistantState.autoNext = {
                    afterField: 'partyLedgerName',
                    nextField: 'accountLedgerName',
                    nextStep: 'ask_account',
                    promptText: 'Select cash/bank ledger (Debit):',
                    preselectText: accHint,
                };
                showLedgerPicker('partyLedgerName', 'Select party ledger (Credit):', parsed.fromParty);
                return;
            }

            if (vt === 'Payment' && parsed.toParty && parsed.amount != null) {
                const hinted = parsed.cashBankHint || 'cash';
                const accHint = setAccountFromHint(hinted) || hinted;
                assistantState.step = 'ask_party';
                assistantState.autoFinalize = true;
                assistantState.autoNext = {
                    afterField: 'partyLedgerName',
                    nextField: 'accountLedgerName',
                    nextStep: 'ask_account',
                    promptText: 'Select cash/bank ledger (Credit):',
                    preselectText: accHint,
                };
                showLedgerPicker('partyLedgerName', 'Select party ledger (Debit):', parsed.toParty);
                return;
            }

            if (vt === 'Contra' && parsed.amount != null && parsed.fromParty && parsed.toParty) {
                assistantState.current.amount = parsed.amount;
                assistantState.step = 'ask_from';
                assistantState.autoFinalize = true;
                assistantState.autoNext = {
                    afterField: 'fromLedgerName',
                    nextField: 'toLedgerName',
                    nextStep: 'ask_to',
                    promptText: 'Select TO account (Debit):',
                    preselectText: parsed.toParty,
                };
                showLedgerPicker('fromLedgerName', 'Select FROM account (Credit):', parsed.fromParty);
                return;
            }

            if (vt === 'Expense' && parsed.amount != null) {
                assistantState.current.amount = parsed.amount;
                const expenseHint = parsed.narration || '';
                if (expenseHint) {
                    const hinted = parsed.cashBankHint || 'cash';
                    const accHint = setAccountFromHint(hinted) || hinted;
                    assistantState.step = 'ask_expense';
                    assistantState.autoFinalize = true;
                    assistantState.autoNext = {
                        afterField: 'expenseLedgerName',
                        nextField: 'accountLedgerName',
                        nextStep: 'ask_account',
                        promptText: 'Select paid-from ledger (Credit):',
                        preselectText: accHint,
                    };
                    showLedgerPicker('expenseLedgerName', 'Select expense ledger (Debit):', expenseHint);
                    return;
                }
            }


            if (vt === 'Service Income' && parsed.amount != null) {
                assistantState.current.amount = parsed.amount;
                const incomeHint = parsed.narration || '';

                assistantState.step = 'ask_income';
                assistantState.autoFinalize = true;
                assistantState.autoNext = {
                    afterField: 'incomeLedgerName',
                    nextField: 'accountLedgerName',
                    nextStep: 'ask_account',
                    promptText: 'Select received-into ledger (Debit):',
                    preselectText: parsed.fromParty || 'cash',
                };
                showLedgerPicker('incomeLedgerName', 'Select income ledger (Credit):', incomeHint);
                return;
            }


            // If we got here, the AI parsed something but it didn't match our patterns
            // Let's show what we got and guide the user
            console.log('AI parsed data:', aiData);
            console.log('Converted parsed:', parsed);

            if (parsed.amount != null) {
                // We have an amount, so let's help the user complete the voucher
                globalChatAppendMessage('bot', `I detected amount: ${parsed.amount}. Let me help you complete this ${vt} voucher.`);

                if (vt === 'Receipt') {
                    assistantState.step = 'ask_party';
                    showLedgerPicker('partyLedgerName', 'Select party ledger (Credit):', parsed.fromParty || '');
                    return;
                } else if (vt === 'Payment') {
                    assistantState.step = 'ask_party';
                    showLedgerPicker('partyLedgerName', 'Select party ledger (Debit):', parsed.toParty || '');
                    return;
                } else if (vt === 'Expense') {
                    assistantState.step = 'ask_expense';
                    showLedgerPicker('expenseLedgerName', 'Select expense ledger (Debit):', parsed.narration || '');
                    return;
                }
            }

            globalChatAppendMessage('bot', 'Type "new" to start, or type one-line voucher like: received 5000 from ABC by cash today');
            return;
        }

        if (assistantState.step === 'ask_party') {
            showLedgerPicker('partyLedgerName', 'Select party ledger:', text);
            return;
        }

        if (assistantState.step === 'ask_expense') {
            showLedgerPicker('expenseLedgerName', 'Select expense ledger:', text);
            return;
        }
        if (assistantState.step === 'ask_income') {
            showLedgerPicker('incomeLedgerName', 'Select income ledger:', text);
            return;
        }

        if (assistantState.step === 'ask_amount') {
            const amount = parseAmount(text);
            if (amount == null) {
                globalChatAppendMessage('bot', 'Please enter the amount (example: 1500).');
                return;
            }
            assistantState.current.amount = amount;
            if (vt === 'Contra') {
                assistantState.step = 'ask_from';
                await showLedgerPicker('fromLedgerName', 'Select FROM account (Credit):', 'cash');
                return;
            }
            assistantState.step = 'ask_account';
            if (vt === 'Receipt') {
                await showLedgerPicker('accountLedgerName', 'Select cash/bank ledger (Debit):', 'cash');
                return;
            }
            if (vt === 'Payment') {
                await showLedgerPicker('accountLedgerName', 'Select cash/bank ledger (Credit):', 'cash');
                return;
            }
            if (vt === 'Expense') {
                await showLedgerPicker('accountLedgerName', 'Select paid-from ledger (Credit):', 'cash');
                return;
            }
            if (vt === 'Service Income') {
                await showLedgerPicker('accountLedgerName', 'Select received-into ledger (Debit):', 'cash');
                return;
            }
            globalChatAppendMessage('bot', 'Select account ledger.');
            return;
        }

        if (assistantState.step === 'ask_from') {
            const hinted = setAccountFromHint(text) || text;
            showLedgerPicker('fromLedgerName', 'Select FROM account (Credit):', hinted);
            return;
        }

        if (assistantState.step === 'ask_to') {
            const hinted = setAccountFromHint(text) || text;
            showLedgerPicker('toLedgerName', 'Select TO account (Debit):', hinted);
            return;
        }

        if (assistantState.step === 'ask_account') {
            const hinted = setAccountFromHint(text) || text;
            showLedgerPicker('accountLedgerName', 'Select account ledger:', hinted);
            return;
        }

        if (assistantState.step === 'ask_cost_center') {
            const q = safeLower(text);
            const names = (vaBootstrap && vaBootstrap.costCenters) ? vaBootstrap.costCenters : [];
            const exact = names.find(n => safeLower(n) === q);
            const starts = names.filter(n => safeLower(n).startsWith(q));
            const contains = names.filter(n => safeLower(n).includes(q) && !safeLower(n).startsWith(q));
            const candidates = (exact ? [exact] : [...starts, ...contains]).slice(0, 8);
            if (candidates.length === 1) {
                assistantState.current.costCenterName = candidates[0];
                assistantState.step = 'ask_narration';
                globalChatAppendMessage('bot', 'Any narration? Type it or type "skip".');
                clearQuick();
                return;
            }
            if (candidates.length > 1) {
                globalChatAppendMessage('bot', 'Choose Cost Center:');
                renderQuickButtons(candidates.map(v => ({ label: v, action: 'pick_cost_center', value: v })));
                return;
            }
            globalChatAppendMessage('bot', 'Cost Center not found. Type the exact name.');
            return;
        }

        if (assistantState.step === 'ask_narration') {
            assistantState.current.narration = (safeLower(text) === 'skip') ? vt : text;
            finalizeAndOfferActions();
            return;
        }

        if (assistantState.step === 'confirm') {
            if (lower === 'submit' || lower === 'submit now') {
                submitCurrentNow();
                return;
            }
            if (lower === 'save' || lower === 'save draft') {
                saveCurrentDraft();
                return;
            }
            globalChatAppendMessage('bot', 'Use Submit Now or Save Draft.');
        }
    }

    // Expose openGlobalChat to window so inline onclick handlers can find it
    window.openGlobalChat = openGlobalChat;
    window.closeGlobalChat = closeGlobalChat;

    console.log('Chatbot: Initializing event listeners...');

    try {
        initAiToggle();

        if (chatFab) {
            chatFab.addEventListener('click', openGlobalChat);
            console.log('Chatbot: chatFab listener attached');
        } else {
            console.warn('Chatbot: chatFab not found');
        }

        if (chatClose) chatClose.addEventListener('click', closeGlobalChat);

        if (voucherSelect) {
            console.log('Chatbot: voucherSelect found, attaching listener');
            voucherSelect.addEventListener('change', () => {
                console.log('Chatbot: voucherSelect changed', voucherSelect.value);
                const slug = (voucherSelect.value || '').toString().trim();
                setVoucherTypeFromSlug(slug);
                if (!slug) {
                    // General Chat - keep actions enabled
                    setChatActionsEnabled(true);
                    updateInputPlaceholder();
                    return;
                }
                setChatActionsEnabled(true);
                updateInputPlaceholder();
                loadVoucherAssistantBootstrap()
                    .then(() => {
                        updateDraftCount(assistantState.voucherType);
                        if (messagesEl && messagesEl.dataset.vaWelcomeFor !== assistantState.voucherType) {
                            globalChatAppendMessage('bot', `Tell me your ${assistantState.voucherType} in simple words.\nType "new" to start, or one-line examples:\nReceipt: received 5000 from ABC by cash today\nPayment: paid 1200 to ABC by cash today\nContra: transfer 1000 from Cash to Bank today\nExpense: expense 300 for Fuel by cash today`);
                            messagesEl.dataset.vaWelcomeFor = assistantState.voucherType;
                        }
                        if (inputEl && !inputEl.disabled) inputEl.focus();
                    })
                    .catch((e) => {
                        console.error('Chatbot: bootstrap failed', e);
                        setChatActionsEnabled(false);
                        globalChatAppendMessage('bot', e.message || String(e));
                    });
            });
        } else {
            console.error('Chatbot: voucherSelect NOT found');
        }

        if (chatOverlay) {
            chatOverlay.addEventListener('click', (e) => {
                const target = e.target;
                if (!(target instanceof Element)) return;
                if (!target.closest('#globalChatWindow')) closeGlobalChat();
            });
        }
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && chatOverlay && !chatOverlay.hidden) closeGlobalChat();
        });

        if (sendBtn) {
            console.log('Chatbot: sendBtn found, attaching listener');
            sendBtn.addEventListener('click', (e) => {
                console.log('Chatbot: sendBtn clicked');
                sendFromInput();
            });
        } else {
            console.error('Chatbot: sendBtn NOT found');
        }
        if (inputEl) {
            inputEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    sendFromInput();
                }
            });
            inputEl.addEventListener('input', () => {
                updateSendAvailability();
            });
        }

        if (quickEl) {
            quickEl.addEventListener('click', (e) => {
                const target = e.target;
                if (!(target instanceof HTMLElement)) return;
                if (!target.classList.contains('rv-quick-btn')) return;

                // Stop propagation to prevent chatOverlay listener from closing the chat
                // (especially if the button is removed from DOM synchronously)
                e.stopPropagation();

                const action = target.dataset.action || '';
                const value = target.dataset.value || '';
                ensureCurrent();
                if (action === 'submit_now') return submitCurrentNow();
                if (action === 'save_draft') return saveCurrentDraft();
                if (action === 'pick_from_hint') {
                    const hinted = setAccountFromHint(value) || value;
                    assistantState.step = 'ask_from';
                    showLedgerPicker('fromLedgerName', 'Select FROM account (Credit):', hinted);
                    return;
                }
                if (action === 'pick_account_hint') {
                    const hinted = setAccountFromHint(value) || value;
                    assistantState.step = 'ask_account';
                    showLedgerPicker('accountLedgerName', 'Select account ledger:', hinted);
                    return;
                }
                if (action === 'pick_cost_center') {
                    assistantState.current.costCenterName = value;
                    assistantState.step = 'ask_narration';
                    globalChatAppendMessage('bot', 'Any narration? Type it or type "skip".');
                    clearQuick();
                    return;
                }
                if (action.startsWith('pick_')) {
                    const field = action.replace('pick_', '');
                    if (field in assistantState.current) {
                        applyPickedLedger(field, value);
                    }
                    return;
                }
            });
        }

        if (newBtn) newBtn.addEventListener('click', () => {
            startNewVoucher();
        });
        if (submitBtn) submitBtn.addEventListener('click', () => {
            if (assistantState.step === 'confirm') {
                submitCurrentNow();
                return;
            }
            ensureCurrent();
            finalizeAndOfferActions();
        });
        if (showDraftsBtn) showDraftsBtn.addEventListener('click', () => {
            showDrafts();
        });
        if (addAllBtn) addAllBtn.addEventListener('click', () => {
            addAllDrafts();
        });

        if (ledgerPickBtn) {
            ledgerPickBtn.addEventListener('click', () => {
                const field = assistantState.pendingLedgerField;
                const val = ledgerSelectEl ? normalizeText(ledgerSelectEl.value) : '';
                if (!field || !val) return;
                applyPickedLedger(field, val);
            });
        }

        console.log('Chatbot: Listeners attached successfully');
    } catch (e) {
        console.error('Chatbot: Error attaching listeners', e);
    }
    // Check if Choices.js is loaded
    if (typeof Choices === 'undefined') {
        console.warn('Choices.js not loaded. Select dropdowns will use native behavior.');
    } else {
        // Initialize Choices.js for all select elements with class 'searchable'
        const searchableSelects = document.querySelectorAll('select.searchable');
        searchableSelects.forEach(select => {
            if (!select.choices) { // Prevent re-initialization
                new Choices(select, {
                    searchEnabled: true,
                    searchChoices: true,
                    itemSelectText: '',
                    shouldSort: false
                });
            }
        });
    }

    // ============================
    // HELPER FUNCTIONS
    // ============================
    // Helper: Debounce function for input updates
    function debounce(func, wait) {
        let timeout;
        return function (...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    // ============================
    // GROUP MANAGEMENT
    // ============================
    // Handle Add Group Form
    const addGroupForm = document.getElementById('addGroupForm');
    if (addGroupForm) {
        addGroupForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_group', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('groupMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('groupMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // Handle Delete Group Form
    const deleteGroupForm = document.getElementById('deleteGroupForm');
    if (deleteGroupForm) {
        deleteGroupForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/delete_group', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('deleteGroupMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('deleteGroupMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // ============================
    // INVENTORY GROUP MANAGEMENT
    // ============================
    // Handle Add Inventory Group Form
    const addInventoryGroupForm = document.getElementById('addInventoryGroupForm');
    if (addInventoryGroupForm) {
        addInventoryGroupForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_inventory_group', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('inventoryGroupMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('inventoryGroupMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // Handle Delete Inventory Group Form
    const deleteInventoryGroupForm = document.getElementById('deleteInventoryGroupForm');
    if (deleteInventoryGroupForm) {
        deleteInventoryGroupForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/delete_inventory_group', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('deleteInventoryGroupMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('deleteInventoryGroupMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // ============================
    // COST CENTER MANAGEMENT
    // ============================
    // Handle Add Cost Center Form
    const addCostCenterForm = document.getElementById('addCostCenterForm');
    if (addCostCenterForm) {
        addCostCenterForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_cost_center', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('costCenterMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('costCenterMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // Handle Delete Cost Center Form
    const deleteCostCenterForm = document.getElementById('deleteCostCenterForm');
    if (deleteCostCenterForm) {
        deleteCostCenterForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/delete_cost_center', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('deleteCostCenterMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('deleteCostCenterMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // ============================
    // LEDGER MANAGEMENT
    // ============================
    // Handle Add Ledger Form
    const addLedgerForm = document.getElementById('addLedgerForm');
    if (addLedgerForm) {
        addLedgerForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_ledger', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('ledgerMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('ledgerMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // Handle Delete Ledger Form
    const deleteLedgerForm = document.getElementById('deleteLedgerForm');
    if (deleteLedgerForm) {
        deleteLedgerForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/delete_ledger', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('deleteLedgerMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('deleteLedgerMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // ============================
    // INVENTORY ITEM MANAGEMENT
    // ============================
    // Handle Add Inventory Form (Add Item)
    const addInventoryForm = document.getElementById('addInventoryForm');
    if (addInventoryForm) {
        addInventoryForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_inventory', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('inventoryMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('inventoryMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // Handle Delete Inventory Form (Delete Item)
    const deleteInventoryForm = document.getElementById('deleteInventoryForm');
    if (deleteInventoryForm) {
        deleteInventoryForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/delete_inventory', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(response => response.json())
                .then(data => {
                    const messageDiv = document.getElementById('deleteInventoryMessage');
                    messageDiv.style.color = data.success ? 'green' : 'red';
                    messageDiv.textContent = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('deleteInventoryMessage').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
                });
        });
    }

    // ============================
    // VOUCHER MANAGEMENT
    // ============================
    // Handle Voucher Form Submission and Dynamic Entries
    const voucherForm = document.getElementById('voucherForm');
    if (voucherForm) {
        function resetVoucherMessage() {
            const msg = document.getElementById('voucherMessageDynamic');
            if (msg) {
                msg.classList.remove('alert-success', 'alert-error');
                msg.textContent = '';
            }
        }

        voucherForm.addEventListener('input', resetVoucherMessage);
        voucherForm.addEventListener('change', resetVoucherMessage);

        voucherForm.addEventListener('submit', function (e) {
            e.preventDefault();
            resetVoucherMessage();

            // Client-side balance validation (exclude Physical Stock)
            try {
                const rowsLedger = Array.from(document.querySelectorAll('.ledger-row'));
                const rowsItem = Array.from(document.querySelectorAll('.item-row'));
                let totalDebit = 0;
                let totalCredit = 0;

                rowsLedger.forEach(row => {
                    const amt = parseFloat((row.querySelector('.ledger-amount-input')?.value || '0')) || 0;
                    const type = row.querySelector('.ledger-type-select')?.value;
                    if (type === 'Debit') totalDebit += amt;
                    else if (type === 'Credit') totalCredit += amt;
                });

                rowsItem.forEach(row => {
                    // Ensure item amount is up-to-date
                    const qty = parseFloat((row.querySelector('.quantity-input')?.value || '0')) || 0;
                    const price = parseFloat((row.querySelector('.unit-price-input')?.value || '0')) || 0;
                    const amountInput = row.querySelector('.item-amount-input');
                    const amtComputed = (qty * price);
                    if (amountInput) amountInput.value = amtComputed.toFixed(2);

                    const amt = amtComputed || 0;
                    const type = row.querySelector('.item-type-select')?.value;
                    if (type === 'Debit') totalDebit += amt;
                    else if (type === 'Credit') totalCredit += amt;
                });

                const voucherTypeClient = (window.voucherType || document.querySelector('input[name="voucher_type"]')?.value || '').trim();
                if (voucherTypeClient !== 'Physical Stock') {
                    // Include VAT injection expectations to mirror server-side rules
                    const itemVatAmounts = Array.from(document.querySelectorAll('.vat-amount-input-item'))
                        .map(inp => parseFloat(inp.value || '0') || 0);
                    const totalItemVAT = itemVatAmounts.reduce((a, b) => a + b, 0);

                    const ledgerVatRows = Array.from(document.querySelectorAll('.ledger-row'));
                    let totalLedgerVATExpenseJournal = 0;
                    let totalLedgerVATServiceIncome = 0;
                    ledgerVatRows.forEach(row => {
                        const checkbox = row.querySelector('.vat-applicable-checkbox');
                        const checked = !!(checkbox && checkbox.checked);
                        const vatAmt = parseFloat((row.querySelector('.vat-amount-input')?.value || '0')) || 0;
                        const type = row.querySelector('.ledger-type-select')?.value;
                        if (checked && type === 'Debit') totalLedgerVATExpenseJournal += vatAmt;
                        if (checked && type === 'Credit') totalLedgerVATServiceIncome += vatAmt;
                    });

                    if (voucherTypeClient === 'Sales') {
                        totalCredit += totalItemVAT;
                    } else if (voucherTypeClient === 'Sales Return') {
                        totalDebit += totalItemVAT;
                    } else if (voucherTypeClient === 'Purchase') {
                        totalDebit += totalItemVAT;
                    } else if (voucherTypeClient === 'Purchase Return') {
                        totalCredit += totalItemVAT;
                    } else if (voucherTypeClient === 'Expense') {
                        totalDebit += totalLedgerVATExpenseJournal;
                    } else if (voucherTypeClient === 'Service Income') {
                        totalCredit += totalLedgerVATServiceIncome;
                    }

                    const tolerance = 0.01;
                    if (Math.abs(totalDebit - totalCredit) > tolerance) {
                        if (messageDiv) {
                            messageDiv.classList.add('alert-error');
                            messageDiv.textContent = `Debit ${totalDebit.toFixed(2)} and Credit ${totalCredit.toFixed(2)} not matching`;
                        }
                        return; // Stop submit, allow user to correct
                    }
                }
            } catch (err) {
                // If client-side check fails for any reason, continue to server
                console.warn('Client-side balance check skipped:', err);
            }

            const formData = new FormData(this);
            const submitUrl = this.action || '/add_voucher';
            fetch(submitUrl, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(async (response) => {
                    const contentType = response.headers.get('content-type') || '';
                    if (contentType.includes('application/json')) {
                        return response.json();
                    }
                    const text = await response.text();
                    return { success: false, message: 'Unexpected response from server' };
                })
                .then(data => {
                    const messageDiv = document.getElementById('voucherMessageDynamic');
                    if (messageDiv) {
                        messageDiv.classList.remove('alert-success', 'alert-error');
                        messageDiv.classList.add(data.success ? 'alert-success' : 'alert-error');
                        messageDiv.textContent = data.message || 'Operation completed';
                    }
                    if (data.success) {
                        if (window.__printAfterSubmit && data.voucher_number) {
                            window.open('/print/voucher/' + encodeURIComponent(data.voucher_number), '_blank');
                        }
                        window.__printAfterSubmit = false;
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        window.__printAfterSubmit = false;
                    }
                })
                .catch(error => {
                    window.__printAfterSubmit = false;
                    console.error('Error:', error);
                    const messageDiv = document.getElementById('voucherMessageDynamic');
                    if (messageDiv) {
                        messageDiv.classList.remove('alert-success');
                        messageDiv.classList.add('alert-error');
                        const msg = (error.message === 'Failed to fetch')
                            ? 'Could not reach the server. Please check it is running and try again.'
                            : (error.message || 'Unknown error occurred');
                        messageDiv.textContent = `Error: ${msg}`;
                    }
                });
        });

        // Add Ledger Entry (Scoped to voucher page)
        window.addLedgerEntry = function () {
            const ledgerEntries = document.getElementById('ledgerEntries');
            const newEntry = document.createElement('div');
            newEntry.className = 'entry';
            newEntry.innerHTML = `
                <select name="ledger_name[]" class="searchable" required>
                    ${window.ledgers.map(ledger => `<option value="${ledger[1]}">${ledger[1]}</option>`).join('')}
                </select>
                <input type="number" name="ledger_amount[]" placeholder="Amount" step="0.01" required>
                <select name="ledger_type[]" required>
                    <option value="Debit">Debit</option>
                    <option value="Credit">Credit</option>
                </select>
                <button type="button" onclick="removeEntry(this)">Delete</button>
            `;
            ledgerEntries.appendChild(newEntry);
            const newSelect = newEntry.querySelector('select.searchable');
            if (typeof Choices !== 'undefined' && !newSelect.choices) {
                new Choices(newSelect, {
                    searchEnabled: true,
                    searchChoices: true,
                    itemSelectText: '',
                    shouldSort: false
                });
            }
        };

        // Add Item Entry (Scoped to voucher page)
        window.addItemEntry = function () {
            const itemEntries = document.getElementById('itemEntries');
            const filteredLedgers = window.ledgers.filter(ledger => {
                const groupCode = ledger[2];
                if (window.voucherType === 'Sales' || window.voucherType === 'Sales Return') {
                    return groupCode === window.salesGroupCode;
                } else if (window.voucherType === 'Purchase' || window.voucherType === 'Purchase Return') {
                    return groupCode === window.purchaseGroupCode;
                }
                return true;
            });
            const newEntry = document.createElement('div');
            newEntry.className = 'item-entry';
            newEntry.innerHTML = `
                <select name="item_name[]" class="searchable" required>
                    ${window.items.map(item => `<option value="${item[1]}">${item[1]}</option>`).join('')}
                </select>
                <input type="number" name="quantity[]" placeholder="Quantity" step="1" required oninput="updateItemAmount(this)">
                <input type="number" name="unit_price[]" placeholder="Unit Price" step="0.01" required oninput="updateItemAmount(this)">
                <input type="number" name="item_amount[]" placeholder="Total Amount" readonly>
                <select name="item_ledger_name[]" class="searchable" required>
                    ${filteredLedgers.map(ledger => `<option value="${ledger[1]}">${ledger[1]}</option>`).join('')}
                </select>
                <input type="hidden" name="item_type[]" value="${window.voucherType in ['Purchase', 'Sales Return'] ? 'Debit' : 'Credit'}">
                <button type="button" onclick="removeEntry(this)">Delete</button>
            `;
            itemEntries.appendChild(newEntry);
            const newSelects = newEntry.querySelectorAll('select.searchable');
            if (typeof Choices !== 'undefined') {
                newSelects.forEach(select => {
                    if (!select.choices) {
                        new Choices(select, {
                            searchEnabled: true,
                            searchChoices: true,
                            itemSelectText: '',
                            shouldSort: false
                        });
                    }
                });
            }
        };

        // Remove Entry (Scoped to voucher page)
        window.removeEntry = function (button) {
            button.parentElement.remove();
        };

        // Update Item Amount with Debounce (Scoped to voucher page)
        window.updateItemAmount = debounce(function (input) {
            const entry = input.parentElement;
            const qtyInput = entry.querySelector('input[name="quantity[]"]');
            const priceInput = entry.querySelector('input[name="unit_price[]"]');
            const amountInput = entry.querySelector('input[name="item_amount[]"]');
            if (qtyInput && priceInput && amountInput) {
                const quantity = parseFloat(qtyInput.value) || 0;
                const unitPrice = parseFloat(priceInput.value) || 0;
                amountInput.value = (quantity * unitPrice).toFixed(2);
            }
        }, 300);
    }

    // ============================
    // REPORTS - TRANSACTION LOADING
    // ============================
    // Load Transactions for Report with Date Filtering
    const ledgerSelect = document.getElementById('ledgerSelect');
    const fromDateInput = document.getElementById('fromDate');
    const toDateInput = document.getElementById('toDate');
    const transactionsTable = document.getElementById('transactionsTable');
    const closingBalance = document.getElementById('closingBalance');
    const exportLink = document.getElementById('exportLink');

    if (ledgerSelect && transactionsTable && closingBalance && exportLink) {
        function loadTransactions() {
            const ledgerName = ledgerSelect.value;
            const fromDate = fromDateInput?.value || '';
            const toDate = toDateInput?.value || '';
            transactionsTable.innerHTML = `
                <tr>
                    <th>Voucher Number</th>
                    <th>Voucher Type</th>
                    <th>Date</th>
                    <th>Debit</th>
                    <th>Credit</th>
                    <th>Running Balance</th>
                </tr>
            `;
            if (ledgerName) {
                fetch(`/get_transactions?ledger_name=${encodeURIComponent(ledgerName)}&from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`)
                    .then(response => response.json())
                    .then(data => {
                        transactionsTable.innerHTML += data.transactions.map(t => `
                            <tr>
                                <td>${t[0]}</td>
                                <td>${t[1]}</td>
                                <td>${t[2]}</td>
                                <td>${t[3]}</td>
                                <td>${t[4]}</td>
                                <td>${t[5]}</td>
                            </tr>
                        `).join('');
                        closingBalance.textContent = data.closing_balance;
                        exportLink.href = `/export_report/${encodeURIComponent(ledgerName)}?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`;
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        transactionsTable.innerHTML += '<tr><td colspan="6">Error loading transactions</td></tr>';
                    });
            } else {
                closingBalance.textContent = '0';
                exportLink.href = '#';
            }
        }

        ledgerSelect.addEventListener('change', loadTransactions);
        if (fromDateInput) fromDateInput.addEventListener('change', loadTransactions);
        if (toDateInput) toDateInput.addEventListener('change', loadTransactions);
    }

    // ============================
    // EXCEL IMPORT FUNCTIONALITY
    // ============================
    // Excel -> JSON using SheetJS
    async function excelFileToJson(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = function (e) {
                try {
                    const data = new Uint8Array(e.target.result);
                    const workbook = XLSX.read(data, { type: "array" });
                    const firstSheetName = workbook.SheetNames[0];
                    const worksheet = workbook.Sheets[firstSheetName];
                    const json = XLSX.utils.sheet_to_json(worksheet, { defval: "" });
                    resolve(json);
                } catch (err) {
                    reject(err);
                }
            };
            reader.onerror = function (err) {
                reject(err);
            };
            reader.readAsArrayBuffer(file);
        });
    }

    // LEDGER IMPORT WITH EXCEL
    const downloadLedgerTemplateBtn = document.getElementById("download-ledger-template-btn");
    if (downloadLedgerTemplateBtn) {
        downloadLedgerTemplateBtn.addEventListener("click", function () {
            window.location.href = "/download_ledger_template";
        });
    }

    const ledgerImportBtn = document.getElementById("ledger-import-btn");
    const ledgerImportFile = document.getElementById("ledger-import-file");
    const ledgerImportStatus = document.getElementById("ledger-import-status");

    if (ledgerImportBtn && ledgerImportFile) {
        ledgerImportBtn.addEventListener("click", function () {
            ledgerImportFile.value = "";
            ledgerImportFile.click();
        });

        ledgerImportFile.addEventListener("change", async function (e) {
            const file = e.target.files[0];
            if (!file) return;

            if (ledgerImportStatus) ledgerImportStatus.textContent = "Reading Excel file...";

            try {
                const rows = await excelFileToJson(file);

                const data = rows.map(r => ({
                    ledger_code: r["Ledger Code"],
                    ledger_name: r["Ledger Name"],
                    group_name: r["Group Name"],
                    opening_balance: Number(r["Opening Balance"] || 0),
                    opening_balance_type: r["Opening Balance Type"],
                    opening_balance_date: (function (v) {
                        if (v === undefined || v === null || v === "") return "";
                        if (typeof v === "number") {
                            return new Date(Math.round((v - 25569) * 86400 * 1000)).toISOString().slice(0, 10);
                        }
                        return String(v).trim();
                    })(r["Opening Balance Date"])
                }));

                const payload = {
                    file_name: file.name,
                    voucher_type: "Ledger",
                    json_data: JSON.stringify(data)
                };

                if (ledgerImportStatus) ledgerImportStatus.textContent = "Queuing import...";

                const response = await fetch("/queue_import", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();
                if (ledgerImportStatus) {
                    ledgerImportStatus.style.color = result.success ? "green" : "red";
                    ledgerImportStatus.textContent = result.message || (result.success ? "Import queued successfully!" : "Import failed");
                }

                if (result.success) {
                    setTimeout(() => location.reload(), 1500);
                }
            } catch (error) {
                console.error("Ledger import error:", error);
                if (ledgerImportStatus) {
                    ledgerImportStatus.style.color = "red";
                    ledgerImportStatus.textContent = "Error reading or queuing Excel file";
                }
            }
        });
    }

    // INVENTORY IMPORT WITH EXCEL
    const downloadInventoryTemplateBtn = document.getElementById("download-inventory-template-btn");
    if (downloadInventoryTemplateBtn) {
        downloadInventoryTemplateBtn.addEventListener("click", function () {
            window.location.href = "/download_inventory_template";
        });
    }

    const inventoryImportBtn = document.getElementById("inventory-import-btn");
    const inventoryImportFile = document.getElementById("inventory-import-file");
    const inventoryImportStatus = document.getElementById("inventory-import-status");

    if (inventoryImportBtn && inventoryImportFile) {
        inventoryImportBtn.addEventListener("click", function () {
            inventoryImportFile.value = "";
            inventoryImportFile.click();
        });

        inventoryImportFile.addEventListener("change", async function (e) {
            const file = e.target.files[0];
            if (!file) return;

            if (inventoryImportStatus) inventoryImportStatus.textContent = "Reading Excel file...";

            try {
                const rows = await excelFileToJson(file);

                const data = rows.map(r => ({
                    item_code: r["Item Code"],
                    item_name: r["Item Name"],
                    group_name: r["Group Name"],
                    unit: r["Unit"],
                    unit_price: Number(r["Unit Price"] || 0),
                    vat_5: Number(r["VAT 5%"] || 0),
                    vat_applicable: r["VAT Applicable Yes or NO"]
                }));

                const payload = {
                    file_name: file.name,
                    voucher_type: "Inventory",
                    json_data: JSON.stringify(data)
                };

                if (inventoryImportStatus) inventoryImportStatus.textContent = "Queuing import...";

                const response = await fetch("/queue_import", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();
                if (inventoryImportStatus) {
                    inventoryImportStatus.style.color = result.success ? "green" : "red";
                    inventoryImportStatus.textContent = result.message || (result.success ? "Import queued successfully!" : "Import failed");
                }

                if (result.success) {
                    setTimeout(() => location.reload(), 1500);
                }
            } catch (error) {
                console.error("Inventory import error:", error);
                if (inventoryImportStatus) {
                    inventoryImportStatus.style.color = "red";
                    inventoryImportStatus.textContent = "Error reading or queuing Excel file";
                }
            }
        });
    }
    // CHATBOT VOUCHER IMPORT
    const vaChatFileInput = document.getElementById('vaChatFileInput');
    if (vaChatFileInput) {
        vaChatFileInput.addEventListener('change', async function () {
            if (!this.files || !this.files.length) return;
            const file = this.files[0];
            const vt = assistantState.voucherType;
            if (!vt) return;

            globalChatAppendMessage('bot', `Uploading ${file.name}...`);

            try {
                // Read Excel file
                let rows;
                if (typeof excelFileToJson === 'function') {
                    rows = await excelFileToJson(file);
                } else {
                    throw new Error("Excel reader not initialized");
                }

                const payload = {
                    file_name: file.name,
                    voucher_type: vt,
                    json_data: JSON.stringify(rows)
                };

                const response = await fetch("/queue_import", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();

                if (result.success) {
                    globalChatAppendMessage('bot', `Success! ${result.message}`);
                    globalChatAppendMessage('bot', `Redirecting to queue...`);
                    setTimeout(() => {
                        window.location.href = '/report/import-queue';
                    }, 2000);
                } else {
                    globalChatAppendMessage('bot', `Error: ${result.message}`);
                }

            } catch (error) {
                console.error("Chat import error:", error);
                globalChatAppendMessage('bot', `Upload failed: ${error.message}`);
            }

            // Reset input
            this.value = '';
        });
    }

});

(function () {
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const overlay = document.getElementById('globalChatOverlay');
        if (overlay && !overlay.hidden) {
            if (window.closeGlobalChat) window.closeGlobalChat();
            else {
                overlay.hidden = true;
                overlay.setAttribute('hidden', '');
                overlay.style.removeProperty('display');
            }
        }
    }, true);
})();

// ============================
// DATE PICKER INITIALIZATION (GLOBAL)
// ============================
document.addEventListener("DOMContentLoaded", function () {
    // Helper to init flatpickr safely
    function initFlatpickr(selectorOrElement) {
        if (typeof flatpickr !== 'undefined') {
            flatpickr(selectorOrElement, {
                dateFormat: "d-m-Y",
                allowInput: true,
                onClose: function (selectedDates, dateStr, instance) {
                    // Ensure change event is fired so other listeners (like calculateDueDate) pick it up
                    instance.element.dispatchEvent(new Event('input', { bubbles: true }));
                    instance.element.dispatchEvent(new Event('change', { bubbles: true }));
                }
            });
        }
    }

    // Initialize for inputs with class 'date-picker'
    initFlatpickr(".date-picker");

    // Initialize for inputs with specific placeholder/pattern if they miss the class
    const potentialDateInputs = document.querySelectorAll('input[placeholder="DD-MM-YYYY"], input[pattern="\\d{2}-\\d{2}-\\d{4}"]');
    potentialDateInputs.forEach(input => {
        if (!input.classList.contains("date-picker") && !input.classList.contains("flatpickr-input")) {
            initFlatpickr(input);
        }
    });
});
