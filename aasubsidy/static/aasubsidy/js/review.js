(function() {
  const onReady = (fn) => (document.readyState !== 'loading') ? fn() : document.addEventListener('DOMContentLoaded', fn);
  onReady(() => {
    const overlay = document.getElementById('loadingOverlay');
    const showLoading = () => { if (overlay) overlay.style.display = 'block'; };
    const hideLoading = () => { if (overlay) overlay.style.display = 'none'; };
    showLoading();

    document.querySelectorAll('#contractsTable tbody tr').forEach(tr => {
      const subTd = tr.querySelector('td[data-suggested]');
      const input = tr.querySelector("input[data-field='subsidy_amount']");
      if (!subTd || !input) return;
      const current = parseFloat(input.value || '0') || 0;
      const suggested = parseFloat(subTd.getAttribute('data-suggested') || '0') || 0;
      if (current === 0 && suggested > 0) {
        input.value = suggested.toFixed(2);
        subTd.setAttribute('data-val', suggested);
      }
      const pctTd = tr.querySelector("td[data-price][data-basis]");
      if (pctTd) {
        const price = parseFloat(pctTd.getAttribute('data-price') || '0') || 0;
        const basis = parseFloat(pctTd.getAttribute('data-basis') || '0') || 0;
        if (price > 0 && basis > 0) {
          const pct = ((price / basis) * 100);
          pctTd.textContent = pct.toFixed(2) + '%';
          pctTd.setAttribute('data-val', pct.toFixed(2));
        }
      }
    });

    // Handle accordion toggle and item loading
    function setupRowClickHandlers() {
      const rows = document.querySelectorAll('.contract-row');
      console.log('Setting up click handlers for', rows.length, 'contract rows');

      rows.forEach(function(row) {
        row.addEventListener('click', function(e) {
          console.log('Row clicked:', this.getAttribute('data-id'));

          // Don't toggle if clicking on interactive elements
          if (e.target.closest('button') ||
              e.target.closest('select') ||
              e.target.closest('input') ||
              e.target.closest('.copyable') ||
              e.target.closest('.force-fit-container') ||
              e.target.closest('.doctrine-display')
          ) {
            console.log('Clicked on interactive element, ignoring');
            return;
          }

          const id = this.getAttribute('data-id');
          const detailRow = document.getElementById('details-' + id);
          if (!detailRow) {
            console.error('Detail row not found for id:', id);
            return;
          }

          const isOpening = !detailRow.classList.contains('show');
          console.log('Toggle detail row:', id, 'isOpening:', isOpening);

          // Use simple toggle without Bootstrap Collapse - more reliable
          // Close all other open details
          document.querySelectorAll('.detail-row.show').forEach(function(el) {
            if (el.id !== 'details-' + id) {
              el.classList.remove('show');
              console.log('Closed detail row:', el.id);
            }
          });

          // Toggle current
          if (isOpening) {
            detailRow.classList.add('show');
            loadContractItems(id);
          } else {
            detailRow.classList.remove('show');
            console.log('Closing detail row');
          }
        });
      });
    }

    setupRowClickHandlers();

    async function loadContractItems(id) {
        const container = document.querySelector(`.items-container[data-id="${id}"]`);
        if (container.getAttribute('data-loaded') === 'true') return;

        try {
            const url = window.AASubsidyConfig.contractItemsUrl.replace("/0/", `/${id}/`);
            const resp = await fetch(url);
            const data = await resp.json();
            if (!resp.ok || !data.ok) throw new Error(data.error || 'Failed to load items');

            let html = '<table class="table table-sm table-dark mb-0 font-monospace" style="font-size: 0.8rem;">';
            html += '<thead><tr><th>Type</th><th class="text-end">Qty</th><th class="text-center">Included</th></tr></thead><tbody>';
            data.items.forEach(item => {
                html += `<tr>
                    <td>${item.name}</td>
                    <td class="text-end">${item.qty.toLocaleString()}</td>
                    <td class="text-center">${item.is_included ? '<i class="fas fa-check text-success"></i>' : '<i class="fas fa-times text-danger"></i>'}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            container.innerHTML = html;
            container.setAttribute('data-loaded', 'true');
        } catch (err) {
            container.innerHTML = `<div class="p-2 text-danger">Error: ${err.message}</div>`;
        }
    }

    const table = document.getElementById('contractsTable');
    if (!table) { hideLoading(); return; }

    // Toast helper
    let toastInst;
    function showToast() {
      const bootstrap = window.bootstrap;
      if (!bootstrap || !bootstrap.Toast) return;
      const el = document.getElementById('copyToast');
      if (!el) return;
      toastInst = toastInst || new bootstrap.Toast(el, { delay: 1500 });
      toastInst.show();
    }

    const STORAGE_KEY = 'aasubsidy_contracts_table_state';
    const serverPref = window.AASubsidyConfig.tablePref;
    const state = serverPref ? { sort: { idx: serverPref.sort_idx, dir: serverPref.sort_dir }, filters: JSON.parse(serverPref.filters || "{}") } : JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');

    const savePrefToServer = () => {
      if (!window.AASubsidyConfig.isAuthenticated) return;
      try {
        fetch(window.AASubsidyConfig.saveTablePrefUrl, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "X-CSRFToken": (document.querySelector('[name=csrfmiddlewaretoken]')||{}).value || ''
          },
          body: JSON.stringify({ sort_idx: state.sort?.idx || 0, sort_dir: state.sort?.dir || 'desc', filters: state.filters || {} })
        }).catch(() => {});
      } catch (e) {}
    };

    let applyFilters = () => {
      const rows = table.tBodies[0].rows;
      for (const tr of rows) {
        let show = true;
        for (const input of table.querySelectorAll('[data-filter]')) {
          const key = input.getAttribute('data-filter');
          const val = (input.value || '').toString().trim().toLowerCase();
          if (!val) continue;
          const idx = Array.from(table.tHead.rows[0].cells).findIndex(th => th.getAttribute('data-col')===key);
          const td = idx >= 0 ? tr.cells[idx] : null;
          const text = (td ? (td.getAttribute('data-val') || td.textContent || '') : '').toLowerCase();
          if (text.indexOf(val) === -1) { show = false; break; }
        }
        tr.style.display = show ? '' : 'none';
      }
    };

    for (const input of table.querySelectorAll('[data-filter]')) {
      if (state.filters && state.filters[input.getAttribute('data-filter')]) {
        input.value = state.filters[input.getAttribute('data-filter')];
      }
      const save = () => {
        state.filters = state.filters || {};
        state.filters[input.getAttribute('data-filter')] = input.value;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        applyFilters();
        savePrefToServer();
      };
      input.addEventListener('input', save);
      input.addEventListener('change', save);
    }

    const getCellValue = (tr, idx) => {
      const td = tr.cells[idx];
      if (!td) return '';
      const v = (td.getAttribute('data-val') || td.textContent || '').trim();
      if (!v) return '';
      const n = Number(v.replace(/[, %]/g, ''));
      return isNaN(n) ? v.toLowerCase() : n;
    };

    const header = table.tHead.rows[0];
    const sortBy = (idx, dir) => {
      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows);
      const pairs = [];
      for (let i = 0; i < rows.length; i++) {
          if (rows[i].classList.contains('contract-row')) {
              const mainRow = rows[i];
              const detailRow = (i + 1 < rows.length && rows[i+1].classList.contains('detail-row')) ? rows[i+1] : null;
              pairs.push({ main: mainRow, detail: detailRow });
              if (detailRow) i++;
          }
      }

      pairs.sort((a, b) => {
        const A = getCellValue(a.main, idx);
        const B = getCellValue(b.main, idx);
        if (A === B) return 0;
        if (A < B) return dir === 'asc' ? -1 : 1;
        return dir === 'asc' ? 1 : -1;
      });

      const fragment = document.createDocumentFragment();
      pairs.forEach(p => {
          fragment.appendChild(p.main);
          if (p.detail) fragment.appendChild(p.detail);
      });
      tbody.appendChild(fragment);

      table.querySelectorAll('thead th').forEach(th => {
          th.classList.remove('sorting-asc', 'sorting-desc');
          if (th.cellIndex === idx) {
              th.classList.add(dir === 'asc' ? 'sorting-asc' : 'sorting-desc');
          }
      });

      state.sort = { idx, dir };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      savePrefToServer();
    };

    for (let i = 0; i < header.cells.length; i++) {
      const th = header.cells[i];
      if (!th.getAttribute('data-col')) continue;
      th.addEventListener('click', () => {
        const current = state.sort || {};
        const dir = current.idx === i && current.dir === 'asc' ? 'desc' : 'asc';
        sortBy(i, dir);
      });
    }

    if (state.sort && Number.isInteger(state.sort.idx)) {
      sortBy(state.sort.idx, state.sort.dir || 'asc');
    } else {
      const idIdx = Array.from(header.cells).findIndex(th => th.getAttribute('data-col') === 'id');
      if (idIdx >= 0) sortBy(idIdx, 'desc');
    }

    applyFilters();

    document.addEventListener('click', (e) => {
      const td = e.target.closest('td[data-val]');
      const isDoctrineCell = td && Array.from(td.parentElement.parentElement.tHead?.rows?.[0]?.cells || document.querySelectorAll('#contractsTable thead tr:first-child th'))
        .some((th, idx) => th.getAttribute('data-col') === 'doctrine' && td.cellIndex === idx);

      if (isDoctrineCell) {
        const container = td.querySelector('.force-fit-container');
        if (container) container.classList.remove('d-none');
      } else {
        document.querySelectorAll('.force-fit-container').forEach(c => c.classList.add('d-none'));
      }
    });
    document.addEventListener('click', async (e) => {
      const copyEl = e.target.closest('.copyable');
      if (copyEl) {
        const txt = copyEl.getAttribute('data-copy') || copyEl.textContent || '';
        try {
          await navigator.clipboard?.writeText(txt.trim());
          showToast();
        } catch (_) {}
      }
      const btn = e.target.closest('.copy-btn');
      if (btn) {
        const targetSel = btn.getAttribute('data-copy-target');
        const input = targetSel && document.querySelector(targetSel);
        if (input) {
          try {
            await navigator.clipboard?.writeText(String(input.value || ''));
            showToast();
          } catch (_) {}
        }
      }
    });
    document.querySelectorAll('.force-fit-select').forEach(sel => {
          sel.addEventListener('change', async () => {
            const contractId = sel.getAttribute('data-contract');
            const fitId = sel.value;
            const token = (document.querySelector('[name=csrfmiddlewaretoken]')||{}).value || '';
            const url = window.AASubsidyConfig.forceFitUrl.replace("/0/", `/${contractId}/`);
            showLoading();
            try {
              await fetch(url, {
                method: "POST",
                headers: {
                  "X-Requested-With": "XMLHttpRequest",
                  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                  "X-CSRFToken": token
                },
                body: new URLSearchParams({ fit_id: fitId }).toString()
              });
            } finally {
              location.reload();
            }
          });
    });
    document.addEventListener('click', async (e) => {
      const actBtn = e.target.closest("button[data-action]");
      if (actBtn) {
        const id = actBtn.getAttribute('data-id');
        const action = actBtn.getAttribute('data-action');
        const subsidyInput = document.querySelector(`input[data-field="subsidy_amount"][data-id="${id}"]`);
        const subsidy_amount = subsidyInput ? subsidyInput.value : '';
        const post = (url, data) =>
          fetch(url, { method:'POST', headers:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/x-www-form-urlencoded'}, body:new URLSearchParams(data) });

        if (action === "approve") {
          showLoading();
          post(window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`), { subsidy_amount })
            .then(() => location.reload())
            .catch(() => location.reload());
          return;
        }
        if (action === "approve_with_comment" || action === "deny") {
          const url = action === "deny"
            ? window.AASubsidyConfig.denyUrl.replace("/0/", `/${id}/`)
            : window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`);
          const modalForm = document.getElementById('approvalModalForm');
          modalForm.setAttribute('action', url);
          document.getElementById('modalAction').value = action;
          document.getElementById('modalContractId').value = id;
          document.getElementById('modalSubsidyAmount').value = subsidy_amount || '';
          const label = document.getElementById('approvalModalLabel');
          if (label) label.textContent = action === 'deny' ? window.AASubsidyConfig.lang.denyWithReason : window.AASubsidyConfig.lang.approveWithComment;
          const modalEl = document.getElementById('approvalModal');
          const bootstrap = window.bootstrap;
          const modal = (modalEl && bootstrap && bootstrap.Modal) ? new bootstrap.Modal(modalEl) : null;
          modal && modal.show();
        }
      }
    });

    const modalForm = document.getElementById('approvalModalForm');
    modalForm && modalForm.addEventListener('submit', (ev) => {
      ev.preventDefault();
      showLoading();
      const data = new FormData(modalForm);
      const url = modalForm.getAttribute('action') || '#';
      fetch(url, { method:'POST', headers:{ 'X-Requested-With':'XMLHttpRequest' }, body:data })
        .then(() => location.reload())
        .catch(() => location.reload());
    });

    // ===== Bulk actions logic (with spinner) =====
    const bulkCheckAll = document.getElementById('bulkCheckAll');
    const bulkSelectedCountEl = document.getElementById('bulkSelectedCount');
    const bulkApproveBtn = document.getElementById('bulkApprove');
    const bulkApproveWithCommentBtn = document.getElementById('bulkApproveWithComment');
    const bulkDenyBtn = document.getElementById('bulkDeny');

    function bulkRowChecks() {
      return Array.from(document.querySelectorAll('.bulk-row-check'));
    }
    function getSelectedIds() {
      return bulkRowChecks()
        .filter(cb => cb.checked && cb.closest('tr')?.style.display !== 'none')
        .map(cb => cb.getAttribute('data-id'))
        .filter(Boolean);
    }
    function updateSelectedCount() {
      const visible = bulkRowChecks().filter(cb => cb.closest('tr')?.style.display !== 'none');
      const checked = visible.filter(cb => cb.checked);
      if (bulkSelectedCountEl) bulkSelectedCountEl.textContent = String(checked.length);
      if (bulkCheckAll) {
        bulkCheckAll.checked = visible.length > 0 && checked.length === visible.length;
        bulkCheckAll.indeterminate = checked.length > 0 && checked.length < visible.length;
      }
    }
    const postForm = (url, data) =>
      fetch(url, { method:'POST', headers:{ 'X-Requested-With':'XMLHttpRequest','Content-Type':'application/x-www-form-urlencoded' }, body:new URLSearchParams(data) });

    async function runBulkApprove(ids) {
      const reqs = ids.map(id => {
        const input = document.querySelector(`input[data-field="subsidy_amount"][data-id="${id}"]`);
        const subsidy_amount = input ? input.value : '';
        const url = window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`);
        return postForm(url, { subsidy_amount });
      });
      await Promise.allSettled(reqs);
    }
    async function runBulkApproveWithComment(ids, comment) {
      const reqs = ids.map(id => {
        const input = document.querySelector(`input[data-field="subsidy_amount"][data-id="${id}"]`);
        const subsidy_amount = input ? input.value : '';
        const url = window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`);
        return postForm(url, { subsidy_amount, comment });
      });
      await Promise.allSettled(reqs);
    }
    async function runBulkDeny(ids, reason) {
      const reqs = ids.map(id => {
        const input = document.querySelector(`input[data-field="subsidy_amount"][data-id="${id}"]`);
        const subsidy_amount = input ? input.value : '';
        const url = window.AASubsidyConfig.denyUrl.replace("/0/", `/${id}/`);
        return postForm(url, { subsidy_amount, comment: reason });
      });
      await Promise.allSettled(reqs);
    }

    if (bulkCheckAll) {
      bulkCheckAll.addEventListener('change', () => {
        const visible = bulkRowChecks().filter(cb => cb.closest('tr')?.style.display !== 'none');
        visible.forEach(cb => { cb.checked = bulkCheckAll.checked; });
        updateSelectedCount();
      });
    }
    document.addEventListener('change', (e) => {
      if (e.target && e.target.classList && e.target.classList.contains('bulk-row-check')) {
        updateSelectedCount();
      }
    });
    updateSelectedCount();

    if (bulkApproveBtn) {
      bulkApproveBtn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const ids = getSelectedIds();
        if (!ids.length) { alert(window.AASubsidyConfig.lang.selectAtLeastOne); return; }
        showLoading();
        await runBulkApprove(ids);
        location.reload();
      });
    }
    if (bulkApproveWithCommentBtn) {
      bulkApproveWithCommentBtn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const ids = getSelectedIds();
        if (!ids.length) { alert(window.AASubsidyConfig.lang.selectAtLeastOne); return; }
        const comment = window.prompt(window.AASubsidyConfig.lang.enterComment, "");
        if (comment === null) return;
        showLoading();
        await runBulkApproveWithComment(ids, (comment || '').trim());
        location.reload();
      });
    }
    if (bulkDenyBtn) {
      bulkDenyBtn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const ids = getSelectedIds();
        if (!ids.length) { alert(window.AASubsidyConfig.lang.selectAtLeastOne); return; }
        const reason = window.prompt(window.AASubsidyConfig.lang.enterReason, "");
        if (reason === null) return;
        if (!reason.trim()) { alert(window.AASubsidyConfig.lang.reasonRequired); return; }
        showLoading();
        await runBulkDeny(ids, reason.trim());
        location.reload();
      });
    }

    hideLoading();
  });
})();
