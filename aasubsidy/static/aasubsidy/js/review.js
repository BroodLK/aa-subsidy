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

    function getColumnIndex(key) {
      const headers = Array.from(document.querySelectorAll('#contractsTable thead tr:first-child th'));
      return headers.findIndex(th => th.getAttribute('data-col') === key);
    }

    function formatMatchSource(source) {
      return {
        auto: 'Auto',
        learned_rule: 'Rule',
        forced: 'Forced',
        manual_accept: 'One-off'
      }[source] || String(source || '').replaceAll('_', ' ');
    }

    function formatMatchStatus(status) {
      const value = String(status || '').replaceAll('_', ' ');
      return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'No Match';
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function renderIssueSummary(issues, title, cssClass) {
      if (!Array.isArray(issues) || !issues.length) return '';
      const items = issues
        .map(issue => `<li>${escapeHtml(issue && issue.message ? issue.message : String(issue || ''))}</li>`)
        .join('');
      return `
        <div class="mt-2">
          <div class="fw-semibold ${cssClass}">${escapeHtml(title)}</div>
          <ul class="mb-0 ps-3 ${cssClass}">${items}</ul>
        </div>
      `;
    }

    function updateContractRow(id, analysis) {
      const row = document.querySelector(`.contract-row[data-id="${id}"]`);
      if (!row || !analysis) return;

      const selectedName = analysis.selected_fit_name || 'No Match';
      const score = Number(analysis.score || 0);

      // Simplified doctrine display
      const doctrineIdx = getColumnIndex('doctrine');
      if (doctrineIdx >= 0) {
        const doctrineCell = row.cells[doctrineIdx];
        const doctrineDisplay = doctrineCell.querySelector('.doctrine-display');
        let doctrineHtml = '';

        if (!selectedName || selectedName === 'No Match') {
          doctrineHtml = '<div class="text-muted">No Match</div>';
        } else if (score >= 100.0) {
          // Perfect match - just show the name
          doctrineHtml = `<div>${selectedName}</div>`;
        } else if (score >= 90.0) {
          // Close match
          doctrineHtml = `<div class="text-warning">Close match to ${selectedName}</div>`;
        } else {
          // Lower score match
          doctrineHtml = `<div class="text-warning">${selectedName} (${score.toFixed(0)}%)</div>`;
        }

        if (doctrineDisplay) doctrineDisplay.innerHTML = doctrineHtml;
        doctrineCell.setAttribute('data-val', selectedName);
      }

      const reviewStatusIdx = getColumnIndex('review_status');
      if (reviewStatusIdx >= 0 && analysis.review_status) {
        const reviewStatusCell = row.cells[reviewStatusIdx];
        const reviewStatus = String(analysis.review_status);
        let reviewStatusClass = '';
        if (reviewStatus === 'Approved') reviewStatusClass = 'text-success';
        if (reviewStatus === 'Rejected') reviewStatusClass = 'text-danger';
        reviewStatusCell.innerHTML = `<span class="${reviewStatusClass}">${reviewStatus}</span>`;
        reviewStatusCell.setAttribute('data-val', reviewStatus);
      }

      const pricing = analysis.pricing || {};
      const basisValue = Number(analysis.basis_isk ?? pricing.basis_isk ?? 0);
      const suggestedValue = Number(analysis.suggested_subsidy ?? pricing.suggested_subsidy ?? 0);
      const storedSubsidyValue = Number(analysis.stored_subsidy_amount ?? 0);
      const subsidyValue = Number(analysis.subsidy_amount ?? suggestedValue ?? 0);

      const subsidyIdx = getColumnIndex('subsidy_amount');
      if (subsidyIdx >= 0) {
        const subsidyCell = row.cells[subsidyIdx];
        const subsidyInput = subsidyCell.querySelector("input[data-field='subsidy_amount']");
        if (subsidyInput) {
          const currentValue = Number(subsidyInput.value || 0);
          if (storedSubsidyValue > 0 || currentValue === 0) {
            subsidyInput.value = subsidyValue.toFixed(2);
          }
        }
        subsidyCell.setAttribute('data-val', subsidyValue.toFixed(2));
        subsidyCell.setAttribute('data-suggested', suggestedValue.toFixed(2));
        subsidyCell.setAttribute('data-basis', basisValue.toFixed(2));
      }

      const reasonIdx = getColumnIndex('reason');
      if (reasonIdx >= 0 && Object.prototype.hasOwnProperty.call(analysis, 'reason')) {
        const reasonCell = row.cells[reasonIdx];
        const reason = String(analysis.reason || '');
        if (reason) {
          reasonCell.innerHTML = `<i class="fa fa-comment text-info" title="${escapeHtml(reason)}"></i>`;
        } else {
          reasonCell.innerHTML = '&mdash;';
        }
        reasonCell.setAttribute('data-val', reason);
      }

      const paidIdx = getColumnIndex('paid');
      if (paidIdx >= 0 && Object.prototype.hasOwnProperty.call(analysis, 'paid')) {
        const paidCell = row.cells[paidIdx];
        const paid = Boolean(analysis.paid);
        paidCell.innerHTML = `<span class="${paid ? 'text-success' : ''}">${paid ? 'Yes' : 'No'}</span>`;
        paidCell.setAttribute('data-val', paid ? 'Yes' : 'No');
      }

      const pctIdx = getColumnIndex('pct_jita');
      if (pctIdx >= 0) {
        const pctCell = row.cells[pctIdx];
        const priceValue = Number(analysis.price_listed ?? pctCell.getAttribute('data-price') ?? 0);
        const pctValue = Number(analysis.pct_jita ?? ((priceValue > 0 && basisValue > 0) ? ((priceValue / basisValue) * 100) : 0));
        pctCell.setAttribute('data-price', String(priceValue));
        pctCell.setAttribute('data-basis', basisValue.toFixed(2));
        pctCell.setAttribute('data-val', pctValue.toFixed(2));
        pctCell.textContent = `${pctValue.toFixed(2)}%`;
        pctCell.classList.remove('text-danger', 'text-warning', 'text-success');
        if (pctValue > 101) {
          pctCell.classList.add('text-danger');
        } else if (pctValue > 0 && pctValue < 99) {
          pctCell.classList.add('text-warning');
        } else if (pctValue > 0) {
          pctCell.classList.add('text-success');
        }
      }
    }

    async function refreshRowSummariesOnLoad() {
      const ids = Array.from(document.querySelectorAll('.contract-row[data-id]'))
        .map(row => row.getAttribute('data-id'))
        .filter(Boolean);
      if (!ids.length) return;

      // Batch requests to avoid URL length limits (max ~50 IDs per request)
      const batchSize = 50;
      const batches = [];
      for (let i = 0; i < ids.length; i += batchSize) {
        batches.push(ids.slice(i, i + batchSize));
      }

      console.log(`Fetching summaries for ${ids.length} contracts in ${batches.length} batches`);

      for (const batch of batches) {
        const url = `${window.AASubsidyConfig.reviewSummariesUrl}?contract_ids=${encodeURIComponent(batch.join(','))}`;
        const resp = await fetch(url, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
          console.error(`Failed to load batch: ${data.error || 'Unknown error'}`);
          continue; // Skip failed batches but continue with others
        }

        (data.rows || []).forEach(summary => {
          if (summary && summary.id) {
            updateContractRow(String(summary.id), summary);
          }
        });
      }

      applyFilters();
    }

    async function loadContractItems(id) {
        const container = document.querySelector(`.items-container[data-id="${id}"]`);
        if (container.getAttribute('data-loaded') === 'true') return;

        try {
            const url = window.AASubsidyConfig.contractItemsUrl.replace("/0/", `/${id}/`);
            const resp = await fetch(url);
            const data = await resp.json();
            if (!resp.ok || !data.ok) throw new Error(data.error || 'Failed to load items');

            const analysis = data.analysis || null;
            const showValidation = Boolean(analysis && analysis.selected_fit_name);
            let summaryHtml = '';
            if (showValidation) {
                const statusClass = analysis.match_status === 'matched'
                    ? 'text-success'
                    : (analysis.match_status === 'needs_review' ? 'text-warning' : 'text-danger');
                const sourceLabel = formatMatchSource(analysis.match_source);
                const statusLabel = formatMatchStatus(analysis.match_status);
                const candidateText = (analysis.candidates || [])
                    .slice(0, 3)
                    .map(candidate => `${candidate.fit_name} (${candidate.score.toFixed ? candidate.score.toFixed(2) : candidate.score})`)
                    .join(', ');
                const failureSummary = renderIssueSummary(analysis.hard_failures, 'Rejected Because', 'text-danger');
                const warningSummary = renderIssueSummary(analysis.warnings, 'Warnings', 'text-warning');
                const scoringDetails = analysis.scoring_details || {};
                const pointsEarned = scoringDetails.points_earned !== undefined ? scoringDetails.points_earned.toFixed(1) : '';
                const expectedItems = scoringDetails.expected_items || '';
                const scoreBreakdown = pointsEarned && expectedItems ? ` (${pointsEarned}/${expectedItems})` : '';

                summaryHtml = `
                    <div class="px-3 py-2 small border-bottom border-secondary">
                        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                            <div>
                                <span class="fw-semibold">${analysis.selected_fit_name || 'No doctrine selected'}</span>
                                <span class="ms-2 badge text-bg-secondary">${sourceLabel}</span>
                                <span class="ms-2 ${statusClass}">${statusLabel}</span>
                                <span class="ms-2">Score: ${Number(analysis.score || 0).toFixed(2)}%${scoreBreakdown}</span>
                            </div>
                            <div class="d-flex gap-2">
                                ${analysis.can_accept_once ? `<button type="button" class="btn btn-sm btn-outline-success accept-once-btn" data-contract="${id}" data-fit="${analysis.selected_fit_id || ''}">${window.AASubsidyConfig.lang.acceptOnce}</button>` : ''}
                                ${analysis.can_undo_accept_once ? `<button type="button" class="btn btn-sm btn-outline-warning undo-accept-once-btn" data-contract="${id}">${window.AASubsidyConfig.lang.undoAcceptOnce}</button>` : ''}
                            </div>
                        </div>
                        ${candidateText ? `<div class="mt-2 text-muted">Candidates: ${candidateText}</div>` : ''}
                        ${failureSummary}
                        ${warningSummary}
                    </div>
                `;
            }

            const renderIncluded = (item) => {
                if (item.is_included === true) return '<i class="fas fa-check text-success"></i>';
                if (item.is_included === false) return '<i class="fas fa-times text-danger"></i>';
                return '<i class="fas fa-triangle-exclamation text-warning"></i>';
            };

            const renderStatus = (item) => {
                if (item.is_missing) return '<span class="badge bg-danger"><i class="fas fa-times-circle me-1"></i>Missing</span>';
                if (item.status === 'error') return '<span class="badge bg-danger"><i class="fas fa-exclamation-triangle me-1"></i>Error</span>';
                if (item.status === 'warning') return '<span class="badge bg-warning text-dark"><i class="fas fa-exclamation-circle me-1"></i>Warning</span>';
                return '<span class="badge bg-success"><i class="fas fa-check-circle me-1"></i>OK</span>';
            };

            const renderActions = (item) => {
                const actions = Array.isArray(item.actions) ? item.actions : [];
                if (!actions.length || !analysis || !analysis.selected_fit_id) return '<span class="text-muted fst-italic">No actions</span>';
                const labels = {
                    optional_item: window.AASubsidyConfig.lang.allowMissing,
                    quantity_tolerance: window.AASubsidyConfig.lang.allowQuantity,
                    specific_substitute: window.AASubsidyConfig.lang.allowSubstitute,
                    ignore_extra_item: window.AASubsidyConfig.lang.ignoreExtra,
                };
                return '<div class="d-flex gap-1 flex-wrap">' + actions.map(action => {
                    const params = new URLSearchParams({
                        fit_id: String(analysis.selected_fit_id || ''),
                        action_name: action,
                        expected_type_id: item.expected_type_id || '',
                        actual_type_id: item.actual_type_id || item.type_id || '',
                        expected_qty: item.expected_qty || '',
                        actual_qty: item.qty || '',
                        category: item.category || '',
                    });
                    return `<button type="button" class="btn btn-sm btn-outline-primary create-rule-btn" data-contract="${id}" data-payload="${encodeURIComponent(params.toString())}">${labels[action] || action}</button>`;
                }).join('') + '</div>';
            };

            let html = '<table class="table table-sm table-hover mb-0" style="font-size: 0.875rem;">';
            html = summaryHtml + html;
            html += showValidation
                ? '<thead class="table-dark"><tr><th style="width: 30%;">Item</th><th class="text-end" style="width: 10%;">Qty</th><th class="text-center" style="width: 12%;">Status</th><th style="width: 28%;">Details</th><th style="width: 20%;">Actions</th></tr></thead><tbody>'
                : '<thead class="table-dark"><tr><th>Type</th><th class="text-end">Qty</th><th class="text-center">Included</th></tr></thead><tbody>';
            data.items.forEach(item => {
                const textClass = item.status === 'error' ? 'text-danger' : item.status === 'warning' ? 'text-warning' : '';
                html += showValidation
                    ? `<tr>
                        <td class="fw-semibold ${textClass}">${item.name}</td>
                        <td class="text-end font-monospace ${textClass}">${item.qty.toLocaleString()}</td>
                        <td class="text-center">${renderStatus(item)}</td>
                        <td><small class="${textClass || 'text-muted'}">${item.reason || '<span class="fst-italic">No issues</span>'}</small></td>
                        <td>${renderActions(item)}</td>
                    </tr>`
                    : `<tr>
                        <td>${item.name}</td>
                        <td class="text-end">${item.qty.toLocaleString()}</td>
                        <td class="text-center">${renderIncluded(item)}</td>
                    </tr>`;
            });
            html += '</tbody></table>';
            container.innerHTML = html;
            container.setAttribute('data-loaded', 'true');
            if (analysis) {
                updateContractRow(id, analysis);
            }
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

    const fitNameCollator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });
    const renderForceFitOptions = (sel, query) => {
      const normalizedQuery = (query || '').trim().toLowerCase();
      const options = sel._fitOptions || [];
      const filtered = normalizedQuery
        ? options.filter(opt => opt.text.toLowerCase().includes(normalizedQuery))
        : options;

      const placeholderText = sel._placeholderText || 'Choose a doctrine';
      const clearText = sel._clearText || 'Clear choice';
      const frag = document.createDocumentFragment();

      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = placeholderText;
      frag.appendChild(placeholder);

      const clear = document.createElement('option');
      clear.value = '__clear__';
      clear.textContent = clearText;
      frag.appendChild(clear);

      filtered.forEach(opt => {
        const el = document.createElement('option');
        el.value = opt.value;
        el.textContent = opt.text;
        frag.appendChild(el);
      });

      sel.innerHTML = '';
      sel.appendChild(frag);
      sel.selectedIndex = 0;
    };

    const setupForceFitSelector = (container) => {
      const sel = container.querySelector('.force-fit-select');
      const search = container.querySelector('.force-fit-search');
      if (!sel || !search) return;

      const placeholderOption = sel.querySelector('option[value=""]');
      const clearOption = sel.querySelector('option[value="__clear__"]');
      sel._placeholderText = placeholderOption ? placeholderOption.textContent.trim() : 'Choose a doctrine';
      sel._clearText = clearOption ? clearOption.textContent.trim() : 'Clear choice';

      const fitOptions = Array.from(sel.options)
        .filter(opt => opt.value && opt.value !== '__clear__')
        .map(opt => ({ value: opt.value, text: opt.textContent.trim() }))
        .sort((a, b) => fitNameCollator.compare(a.text, b.text));

      sel._fitOptions = fitOptions;
      renderForceFitOptions(sel, '');

      search.addEventListener('input', () => {
        renderForceFitOptions(sel, search.value);
      });
    };

    document.querySelectorAll('.force-fit-container').forEach(setupForceFitSelector);

    document.addEventListener('click', (e) => {
      const td = e.target.closest('td[data-val]');
      const isDoctrineCell = td && Array.from(td.parentElement.parentElement.tHead?.rows?.[0]?.cells || document.querySelectorAll('#contractsTable thead tr:first-child th'))
        .some((th, idx) => th.getAttribute('data-col') === 'doctrine' && td.cellIndex === idx);

      if (isDoctrineCell) {
        if (e.target.closest('.force-fit-container')) return;
        const container = td.querySelector('.force-fit-container');
        document.querySelectorAll('.force-fit-container').forEach(c => {
          if (c !== container) c.classList.add('d-none');
        });
        if (container) {
          const search = container.querySelector('.force-fit-search');
          const sel = container.querySelector('.force-fit-select');
          if (search) search.value = '';
          if (sel) renderForceFitOptions(sel, '');
          container.classList.remove('d-none');
          if (search) search.focus();
        }
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
    document.addEventListener('click', async (e) => {
      const acceptBtn = e.target.closest('.accept-once-btn');
      if (acceptBtn) {
        e.preventDefault();
        e.stopPropagation();
        const contractId = acceptBtn.getAttribute('data-contract');
        const fitId = acceptBtn.getAttribute('data-fit');
        if (!contractId || !fitId) return;
        const token = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
        showLoading();
        try {
          await fetch(window.AASubsidyConfig.acceptOnceUrl.replace("/0/", `/${contractId}/`), {
            method: 'POST',
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-CSRFToken': token
            },
            body: new URLSearchParams({ fit_id: fitId }).toString()
          });
        } finally {
          location.reload();
        }
      }

      const undoBtn = e.target.closest('.undo-accept-once-btn');
      if (undoBtn) {
        e.preventDefault();
        e.stopPropagation();
        const contractId = undoBtn.getAttribute('data-contract');
        if (!contractId) return;
        const token = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
        showLoading();
        try {
          await fetch(window.AASubsidyConfig.undoAcceptOnceUrl.replace("/0/", `/${contractId}/`), {
            method: 'POST',
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-CSRFToken': token
            }
          });
        } finally {
          location.reload();
        }
      }

      const ruleBtn = e.target.closest('.create-rule-btn');
      if (ruleBtn) {
        e.preventDefault();
        e.stopPropagation();
        const contractId = ruleBtn.getAttribute('data-contract');
        const payload = decodeURIComponent(ruleBtn.getAttribute('data-payload') || '');
        if (!contractId || !payload) return;
        const token = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
        showLoading();
        try {
          await fetch(window.AASubsidyConfig.createRuleUrl.replace("/0/", `/${contractId}/`), {
            method: 'POST',
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-CSRFToken': token
            },
            body: payload
          });
        } finally {
          location.reload();
        }
      }
    });
    document.querySelectorAll('.force-fit-select').forEach(sel => {
          sel.addEventListener('change', async () => {
            const contractId = sel.getAttribute('data-contract');
            const fitId = sel.value;
            if (!fitId) return;
            const token = (document.querySelector('[name=csrfmiddlewaretoken]')||{}).value || '';
            const url = window.AASubsidyConfig.forceFitUrl.replace("/0/", `/${contractId}/`);
            showLoading();
            try {
              const resp = await fetch(url, {
                method: "POST",
                headers: {
                  "X-Requested-With": "XMLHttpRequest",
                  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                  "X-CSRFToken": token
                },
                body: new URLSearchParams({ fit_id: fitId }).toString()
              });
              const data = await resp.json().catch(() => ({}));
              if (!resp.ok || !data.ok) {
                const error = (data && data.error) ? data.error : 'force_fit_failed';
                throw new Error(error);
              }
              location.reload();
            } catch (err) {
              alert(`Force fit failed: ${err.message}`);
            } finally {
              hideLoading();
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

    // DISABLED: Server-side rendering now provides correct match data on initial page load
    // No need to refresh via AJAX on page load - it just overwrites the correct data!
    // refreshRowSummariesOnLoad()
    //   .catch((err) => {
    //     console.error('Initial review summary refresh failed:', err);
    //   })
    //   .finally(() => {
    //     hideLoading();
    //   });

    // Just hide the loading spinner immediately since data is already rendered
    hideLoading();
  });
})();
