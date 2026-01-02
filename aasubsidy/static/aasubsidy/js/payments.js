(function() {
  function onReady(fn){ if(document.readyState!=='loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  onReady(() => {
    // Initialize tooltips safely
    try {
      if (window.bootstrap) {
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
          try { new bootstrap.Tooltip(el); } catch(e){}
        });
      }
    } catch(e) {}

    // Toast helper
    let toastInst;
    function showToast() {
      try {
        if (!window.bootstrap) return;
        const el = document.getElementById('copyToast');
        if (!el) return;
        toastInst = toastInst || new bootstrap.Toast(el, { delay: 1500 });
        toastInst.show();
      } catch(e) {}
    }

    const overlay = document.getElementById('loadingOverlay');
    const showLoading = () => { if (overlay) overlay.style.display = 'block'; };
    const hideLoading = () => { if (overlay) overlay.style.display = 'none'; };

    // show during init
    showLoading();

    // Initialize tooltips safely
    try {
      if (window.bootstrap) {
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
          try { new bootstrap.Tooltip(el); } catch(e){}
        });
      }
    } catch(e) {}

    // Single delegated listener
    document.addEventListener('click', async (e) => {
      const copyEl = e.target.closest('.copyable');
      if (copyEl) {
        const txt = copyEl.getAttribute('data-copy') || copyEl.textContent || '';
        try { await navigator.clipboard?.writeText(String(txt).trim()); showToast(); } catch(_){}
        return;
      }

      const btn = e.target.closest('.mark-paid-btn');
      if (btn) {
        const character = btn.getAttribute('data-character') || '';
        if (!character) return;
        btn.disabled = true;
        btn.classList.add('disabled');
        try {
          showLoading();
          const resp = await fetch(window.AASubsidyConfig.paymentsMarkPaidUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-CSRFToken': getCookie('csrftoken'),
            },
            body: new URLSearchParams({ character }),
            credentials: 'same-origin',
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.error || 'failed');
          window.location.reload();
        } catch (err) {
          // Re-enable on failure
          btn.disabled = false;
          btn.classList.remove('disabled');
          console.error('Mark as Paid failed:', err);
          alert('Failed to mark as paid. Please try again.');
          hideLoading();
        }
      }
    });

    function getCookie(name) {
      const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
      return m ? decodeURIComponent(m[2]) : '';
    }
    hideLoading();
  });
})();
