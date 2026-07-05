// Dropdown menu support with improved hover stability
document.addEventListener('DOMContentLoaded', function() {
    // ============================
    // DROPDOWN MENU HANDLING - IMPROVED
    // ============================
    const dropdowns = document.querySelectorAll('.dropdown');
    let hoverTimeout;
    let currentOpenDropdown = null;
    
    dropdowns.forEach(dropdown => {
        const toggle = dropdown.querySelector('.dropdown-toggle');
        const menu = dropdown.querySelector('.dropdown-menu');
        
        if (toggle && menu) {
            // Desktop hover behavior with auto-close of other dropdowns
            dropdown.addEventListener('mouseenter', function() {
                if (window.innerWidth > 768) {
                    clearTimeout(hoverTimeout);
                    
                    // Close all other dropdowns immediately
                    dropdowns.forEach(other => {
                        if (other !== dropdown) {
                            other.classList.remove('active');
                        }
                    });
                    
                    // Open this dropdown
                    dropdown.classList.add('active');
                    currentOpenDropdown = dropdown;
                }
            });
            
            dropdown.addEventListener('mouseleave', function() {
                if (window.innerWidth > 768) {
                    hoverTimeout = setTimeout(() => {
                        dropdown.classList.remove('active');
                        if (currentOpenDropdown === dropdown) {
                            currentOpenDropdown = null;
                        }
                    }, 200); // 200ms delay before closing
                }
            });
            
            // Mobile click behavior
            toggle.addEventListener('click', function(e) {
                if (window.innerWidth <= 768) {
                    e.preventDefault();
                    
                    const isCurrentlyActive = dropdown.classList.contains('active');
                    
                    // Close all dropdowns
                    dropdowns.forEach(other => {
                        other.classList.remove('active');
                    });
                    
                    // Toggle this dropdown
                    if (!isCurrentlyActive) {
                        dropdown.classList.add('active');
                    }
                }
            });
        }
    });
    
    // Close all dropdowns when clicking outside
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.dropdown')) {
            dropdowns.forEach(dropdown => {
                dropdown.classList.remove('active');
            });
            currentOpenDropdown = null;
        }
    });

    // ============================
    // CHOICES.JS INITIALIZATION
    // ============================
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
        voucherForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            fetch('/add_voucher', {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.json())
            .then(data => {
                const messageDiv = document.getElementById('voucherMessageDynamic');
                messageDiv.style.color = data.success ? 'green' : 'red';
                messageDiv.textContent = data.message;
                if (data.success) {
                    setTimeout(() => location.reload(), 1000);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                document.getElementById('voucherMessageDynamic').textContent = `Error: ${error.message || 'Unknown error occurred'}`;
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
                    opening_balance_type: r["Opening Balance Type"]
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
});
