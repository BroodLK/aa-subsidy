(function() {
  const onReady = (fn) => (document.readyState !== 'loading') ? fn() : document.addEventListener('DOMContentLoaded', fn);
  onReady(() => {
    const overlay = document.getElementById('loadingOverlay');
    const showLoading = () => { if (overlay) overlay.style.display = 'block'; };
    const hideLoading = () => { if (overlay) overlay.style.display = 'none'; };

    // show while initializing
    showLoading();

    if (window.bootstrap) {
      document.querySelectorAll('.claim-link').forEach(el => {
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
        if (clearBtn) {
          clearBtn.classList.toggle('d-none', !(claimedMe > 0));
        }
        fitIdEl.value = fitId;
        qtyEl.value = claimedMe > 0 ? claimedMe : '';
        qtyEl.min = 1;

        fitNameEl.innerHTML = `<span class="text-muted">${fitName}</span>`;

        hintEl.innerHTML = `· Needed: <b>${needed.toLocaleString()}</b><br> · Available: <b>${available.toLocaleString()}</b><br> · Claimed (all): <b>${claimedTotal.toLocaleString()}</b><br> · Claimed by You: <b>${claimedMe.toLocaleString()}</b><br> · Claimed by: <b>${claimants}</b>`;
        errorEl.style.display = 'none';
        errorEl.textContent = '';

        modal = modal || (window.bootstrap ? new bootstrap.Modal(modalEl) : null);
        if (modal) modal.show();
      });
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
    hideLoading();
  });
})();
