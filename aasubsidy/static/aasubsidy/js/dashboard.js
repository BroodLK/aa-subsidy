(function() {
  const onReady = (fn) => (document.readyState !== 'loading') ? fn() : document.addEventListener('DOMContentLoaded', fn);
  onReady(() => {
    const path = String(window.location.pathname || '');
    const isReviewPage = /\/review\/?$/.test(path);
    const stockHeader = document.getElementById("stockHeader");
    const contractsHeader = document.getElementById("contractsHeader");
    const stockSection = document.getElementById("stockSection");
    const contractsSection = document.getElementById("contractsSection");

    if (isReviewPage) {
      stockHeader && stockHeader.classList.add("d-none");
      contractsHeader && contractsHeader.classList.remove("d-none");
      stockSection && stockSection.classList.add("d-none");
      contractsSection && contractsSection.classList.remove("d-none");
    } else {
      stockHeader && stockHeader.classList.remove("d-none");
      contractsHeader && contractsHeader.classList.add("d-none");
      stockSection && stockSection.classList.remove("d-none");
      contractsSection && contractsSection.classList.add("d-none");
    }

    const table = document.getElementById('contractsTable');
    if (!table) return;

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

    const applyFilters = () => {
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
      const n = Number(v.replace(/[, ]/g, ''));
      return isNaN(n) ? v.toLowerCase() : n;
    };

    const header = table.tHead.rows[0];
    const sortBy = (idx, dir) => {
      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows);
      rows.sort((a, b) => {
        const A = getCellValue(a, idx);
        const B = getCellValue(b, idx);
        if (A === B) return 0;
        if (A < B) return dir === 'asc' ? -1 : 1;
        return dir === 'asc' ? 1 : -1;
      });
      const fragment = document.createDocumentFragment();
      rows.forEach(r => fragment.appendChild(r));
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
      const copyEl = e.target.closest('.copyable');
      if (copyEl) {
        const txt = copyEl.getAttribute('data-copy') || copyEl.textContent || '';
        navigator.clipboard?.writeText(txt.trim());
      }
      const btn = e.target.closest('.copy-btn');
      if (btn) {
        const targetSel = btn.getAttribute('data-copy-target');
        const input = targetSel && document.querySelector(targetSel);
        if (input) navigator.clipboard?.writeText(String(input.value || ''));
      }
    });

    if (window.bootstrap) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        try { new bootstrap.Tooltip(el, { container: 'body' }); } catch (e) {}
      });
    }

    const modalEl = document.getElementById('approvalModal');
    const modalForm = document.getElementById('approvalModalForm');
    const modal = modalEl ? new bootstrap.Modal(modalEl) : null;

    function post(url, data) {
      fetch(url, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(data)
      }).then(() => location.reload()).catch(() => location.reload());
    }

    document.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      const action = btn.dataset.action;

      const subsidyInput = document.querySelector(`input[data-field="subsidy_amount"][data-id="${id}"]`);
      const subsidy_amount = subsidyInput ? subsidyInput.value : '';

      if (action === "approve") {
        post(window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`), { subsidy_amount });
        return;
      }
      if (action === "approve_with_comment" || action === "deny") {
        const url = action === "deny"
          ? window.AASubsidyConfig.denyUrl.replace("/0/", `/${id}/`)
          : window.AASubsidyConfig.approveUrl.replace("/0/", `/${id}/`);
        modalForm.setAttribute('action', url);
        document.getElementById('modalAction').value = action;
        document.getElementById('modalContractId').value = id;
        document.getElementById('modalSubsidyAmount').value = subsidy_amount || '';
        const label = document.getElementById('approvalModalLabel');
        if (label) label.textContent = action === 'deny' ? window.AASubsidyConfig.lang.denyWithReason : window.AASubsidyConfig.lang.approveWithComment;
        modal && modal.show();
      }
    });

    modalForm && modalForm.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const data = new FormData(modalForm);
      const url = modalForm.getAttribute('action') || '#';
      fetch(url, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: data
      }).then(r => r.json()).then(() => location.reload()).catch(() => location.reload());
    });
  });
})();
