(function() {
  const onReady = (fn) => (document.readyState !== 'loading') ? fn() : document.addEventListener('DOMContentLoaded', fn);
  onReady(() => {
    const overlay = document.getElementById('loadingOverlay');
    const showLoading = () => { if (overlay) overlay.style.display = 'block'; };
    const hideLoading = () => { if (overlay) overlay.style.display = 'none'; };

    // show while initializing
    showLoading();

    if (window.bootstrap) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        try { new bootstrap.Tooltip(el, { container: 'body', html: true }); } catch (e) {}
      });
    }

    // Toast helper
    let toastInst;
    function showToast() {
      if (!window.bootstrap) return;
      const el = document.getElementById('copyToast');
      if (!el) return;
      toastInst = toastInst || new bootstrap.Toast(el, { delay: 1500 });
      toastInst.show();
    }

    const modalEl = document.getElementById('claimModal');
    const qtyEl = document.getElementById('claimQty');
    const fitIdEl = document.getElementById('claimFitId');
    const fitNameEl = document.getElementById('claimFitName');
    const hintEl = document.getElementById('claimHint');
    const adminClaimantsSection = document.getElementById('adminClaimantsList');
    const claimantsContainer = document.getElementById('claimantsListContainer');
    const errorEl = document.getElementById('claimError');
    const clearBtn = document.getElementById('clearClaimBtn');
    let modal;

    document.querySelectorAll('.claim-link').forEach(link => {
      link.addEventListener('click', () => {
        const fitId = link.getAttribute('data-fit-id');
        const fitName = link.getAttribute('data-fit-name');
        const needed = parseInt(link.getAttribute('data-needed') || '0', 10);
        const available = parseInt(link.getAttribute('data-available') || '0', 10);
        const claimedMe = parseInt(link.getAttribute('data-claimed-me') || '0', 10);
        const claimedTotal = parseInt(link.getAttribute('data-claimed-total') || '0', 10);
        const claimants = (link.getAttribute('data-claimants') || '').trim();
        const claimantsRaw = (link.getAttribute('data-claimants-raw') || '').trim();

        if (clearBtn) {
          clearBtn.classList.toggle('d-none', !(claimedMe > 0));
        }
        fitIdEl.value = fitId;
        qtyEl.value = claimedMe > 0 ? claimedMe : '';
        qtyEl.min = 1;

        fitNameEl.innerHTML = `<span class="text-muted">${fitName}</span>`;

        hintEl.innerHTML = `· Needed: <b>${needed.toLocaleString()}</b><br> · Available: <b>${available.toLocaleString()}</b><br> · Claimed (all): <b>${claimedTotal.toLocaleString()}</b><br> · Claimed by You: <b>${claimedMe.toLocaleString()}</b><br> · Claimed by: <b>${claimants}</b>`;
        
        if (window.AASubsidyConfig.isAdmin && adminClaimantsSection && claimantsContainer) {
            claimantsContainer.innerHTML = '';
            if (claimantsRaw) {
                adminClaimantsSection.classList.remove('d-none');
                const parts = claimantsRaw.split('|');
                parts.forEach(p => {
                    const [uid, name, qty] = p.split(':');
                    const li = document.createElement('div');
                    li.className = 'list-group-item d-flex justify-content-between align-items-center bg-transparent border-secondary text-light px-0';
                    li.innerHTML = `
                        <span>${name} (${qty})</span>
                        <button type="button" class="btn btn-sm btn-outline-danger admin-clear-btn" data-user-id="${uid}">
                            <i class="fa fa-trash"></i>
                        </button>
                    `;
                    claimantsContainer.appendChild(li);
                });
            } else {
                adminClaimantsSection.classList.add('d-none');
            }
        }

        errorEl.style.display = 'none';
        errorEl.textContent = '';

        modal = modal || (window.bootstrap ? new bootstrap.Modal(modalEl) : null);
        if (modal) modal.show();
      });
    });

    document.addEventListener('click', async (e) => {
        const adminClearBtn = e.target.closest('.admin-clear-btn');
        if (adminClearBtn) {
            const userId = adminClearBtn.getAttribute('data-user-id');
            const fitId = fitIdEl.value;
            if (!userId || !fitId) return;
            if (!confirm('Are you sure you want to clear this claim?')) return;
            
            try {
                showLoading();
                const resp = await fetch(window.AASubsidyConfig.deleteClaimUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken'),
                    },
                    body: JSON.stringify({ fit_id: parseInt(fitId, 10), user_id: parseInt(userId, 10) }),
                    credentials: 'same-origin',
                });
                const data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || 'Failed to clear claim');
                window.location.reload();
            } catch (err) {
                errorEl.textContent = err.message || 'Error clearing claim.';
                errorEl.style.display = 'block';
                hideLoading();
            }
        }
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
    if (clearBtn) {
      clearBtn.addEventListener('click', async () => {
        const fitId = parseInt(fitIdEl.value || '0', 10);
        if (!(fitId > 0)) return;
        try {
          showLoading();
          const resp = await fetch(window.AASubsidyConfig.deleteClaimUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({ fit_id: fitId }),
            credentials: 'same-origin',
          });
          const data = await resp.json();
          if (!resp.ok || !data.ok) throw new Error(data.error || 'Failed to clear claim');
          window.location.reload();
        } catch (err) {
          errorEl.textContent = err.message || 'Error clearing claim.';
          errorEl.style.display = 'block';
          hideLoading();
        }
      });
    }
    if (modalEl) {
      modalEl.addEventListener('hide.bs.modal', () => {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) overlay.style.display = 'none';
      });
      modalEl.addEventListener('hidden.bs.modal', () => {
        const backdrops = document.querySelectorAll('.modal-backdrop.show');
        backdrops.forEach(b => b.parentNode && b.parentNode.removeChild(b));
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('padding-right');
      });
    }

    const claimForm = document.getElementById('claimForm');
    if (claimForm) {
      claimForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const qtyEl = document.getElementById('claimQty');
        const fitIdEl = document.getElementById('claimFitId');
        const errorEl = document.getElementById('claimError');
        const qty = parseInt(qtyEl.value || '0', 10);
        if (!(qty > 0)) {
          errorEl.textContent = 'Please enter a valid number.';
          errorEl.style.display = 'block';
          return;
        }
        const fitId = parseInt(fitIdEl.value, 10);
        try {
          showLoading();
          const resp = await fetch(window.AASubsidyConfig.saveClaimUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({ fit_id: fitId, quantity: qty }),
            credentials: 'same-origin',
          });
          const data = await resp.json();
          if (!resp.ok || !data.ok) throw new Error(data.error || 'Failed to save claim');
          window.location.reload();
        } catch (err) {
          errorEl.textContent = err.message || 'Error saving claim.';
          errorEl.style.display = 'block';
          hideLoading();
        }
      });
    }

    function getCookie(name) {
      const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
      return m ? decodeURIComponent(m[2]) : '';
    }
    // Sorting logic
    const STORAGE_KEY = 'aasubsidy_summary_table_state';
    const serverPref = window.AASubsidyConfig.tablePref;
    const state = serverPref ? { sort: { idx: serverPref.sort_idx, dir: serverPref.sort_dir } } : JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');

    const savePrefToServer = () => {
      if (!window.AASubsidyConfig.isAuthenticated) return;
      try {
        fetch(window.AASubsidyConfig.saveTablePrefUrl, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie('csrftoken')
          },
          body: JSON.stringify({
              table_key: "summary",
              sort_idx: state.sort?.idx || 0,
              sort_dir: state.sort?.dir || 'desc'
          })
        }).catch(() => {});
      } catch (e) {}
    };

    const getCellValue = (tr, idx) => {
      const td = tr.cells[idx];
      if (!td) return '';
      const v = (td.getAttribute('data-val') || td.textContent || '').trim();
      if (!v) return '';
      const n = Number(v.replace(/[, ]/g, ''));
      return isNaN(n) ? v.toLowerCase() : n;
    };

    const sortBy = (idx, dir) => {
      const container = document.querySelector('.allianceauth-subsidy-plugin');
      const tables = container ? container.querySelectorAll('table.table-bordered') : document.querySelectorAll('table.table-bordered');
      
      tables.forEach(table => {
          if (!table.tBodies || !table.tBodies[0]) return;
          const tbody = table.tBodies[0];
          const rows = Array.from(tbody.rows);
          if (rows.length <= 1) {
              const firstCell = rows[0] ? rows[0].cells[0] : null;
              if (firstCell && firstCell.classList.contains('text-muted')) return;
          }

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
      });
      state.sort = { idx, dir };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      savePrefToServer();
    };

    const container = document.querySelector('.allianceauth-subsidy-plugin');
    const headers = container ? container.querySelectorAll('table.table-bordered thead th') : document.querySelectorAll('table.table-bordered thead th');
    
    headers.forEach((th) => {
      if (!th.getAttribute('data-col')) return;
      th.addEventListener('click', () => {
        const i = th.cellIndex;
        const current = state.sort || {};
        const dir = (current.idx === i && current.dir === 'asc') ? 'desc' : 'asc';
        sortBy(i, dir);
      });
    });

    if (state.sort && Number.isInteger(state.sort.idx)) {
      sortBy(state.sort.idx, state.sort.dir || 'asc');
    }

    hideLoading();
  });
})();
