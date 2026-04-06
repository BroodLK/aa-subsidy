(function () {
  const onReady = (fn) => {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  };

  const STORAGE_KEY = "aasubsidy_user_stats_state";

  const loadState = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (e) {
      return {};
    }
  };

  const saveState = (state) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {}
  };

  const cleanText = (value) => String(value || "").replace(/\s+/g, " ").trim();

  const toSortValue = (raw) => {
    const normalized = cleanText(raw);
    if (!normalized) return "";
    const num = Number(normalized.replace(/[, ]/g, ""));
    return Number.isNaN(num) ? normalized.toLowerCase() : num;
  };

  const getCellSortValue = (tr, idx) => {
    const td = tr.cells[idx];
    if (!td) return "";
    return toSortValue(td.getAttribute("data-val") || td.textContent || "");
  };

  const attachSorter = (table, state, stateKey, fallbackSort) => {
    if (!table || !table.tHead || !table.tBodies || !table.tBodies[0]) return;
    const header = table.tHead.rows[0];
    if (!header) return;

    const sortBy = (idx, dir) => {
      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows);
      rows.sort((a, b) => {
        const A = getCellSortValue(a, idx);
        const B = getCellSortValue(b, idx);
        if (A === B) return 0;
        if (A < B) return dir === "asc" ? -1 : 1;
        return dir === "asc" ? 1 : -1;
      });

      const fragment = document.createDocumentFragment();
      rows.forEach((row) => fragment.appendChild(row));
      tbody.appendChild(fragment);

      table.querySelectorAll("thead th").forEach((th) => {
        th.classList.remove("sorting-asc", "sorting-desc");
        if (th.cellIndex === idx) {
          th.classList.add(dir === "asc" ? "sorting-asc" : "sorting-desc");
        }
      });

      state[stateKey] = { idx, dir };
      saveState(state);
    };

    for (let i = 0; i < header.cells.length; i++) {
      const th = header.cells[i];
      if (!th.getAttribute("data-col")) continue;
      th.addEventListener("click", () => {
        const current = state[stateKey] || {};
        const nextDir = current.idx === i && current.dir === "asc" ? "desc" : "asc";
        sortBy(i, nextDir);
      });
    }

    const current = state[stateKey];
    if (current && Number.isInteger(current.idx)) {
      sortBy(current.idx, current.dir || "asc");
      return;
    }

    const idx =
      fallbackSort && Number.isInteger(fallbackSort.idx) ? fallbackSort.idx : 0;
    const dir = fallbackSort && fallbackSort.dir ? fallbackSort.dir : "asc";
    sortBy(idx, dir);
  };

  const getStationText = (td) => {
    const icon = td.querySelector("[title]");
    if (icon) return cleanText(icon.getAttribute("title"));
    return "";
  };

  const getExportCellValue = (td) => {
    const rawText = cleanText(td.textContent || "");
    if (rawText) return rawText;
    const station = getStationText(td);
    if (station) return station;
    return cleanText(td.getAttribute("data-val") || "");
  };

  const toCsv = (rows) =>
    rows
      .map((row) =>
        row
          .map((value) => `"${String(value || "").replace(/"/g, '""')}"`)
          .join(",")
      )
      .join("\r\n");

  const exportVisibleContracts = (table) => {
    if (!table || !table.tHead || !table.tBodies || !table.tBodies[0]) return;
    const header = Array.from(table.tHead.rows[0].cells).map((th) => cleanText(th.textContent));
    const bodyRows = Array.from(table.tBodies[0].rows).filter((tr) => tr.style.display !== "none");

    const rows = [header];
    bodyRows.forEach((tr) => {
      if (tr.cells.length === 1 && tr.cells[0].colSpan > 1) return;
      rows.push(Array.from(tr.cells).map(getExportCellValue));
    });

    const csv = toCsv(rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const now = new Date();
    const datePart = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const link = document.createElement("a");
    link.href = url;
    link.download = `subsidy-contract-stats-${datePart}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  onReady(() => {
    if (window.bootstrap) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
        try {
          new bootstrap.Tooltip(el);
        } catch (e) {}
      });
    }

    const state = loadState();
    const topTable = document.getElementById("statsByCharacterTable");
    const bottomTable = document.getElementById("contractsStatsTable");
    const hideApproved = document.getElementById("hideApproved");
    const hideRejected = document.getElementById("hideRejected");
    const exportBtn = document.getElementById("exportContractsCsv");

    attachSorter(topTable, state, "topSort", { idx: 2, dir: "desc" });
    attachSorter(bottomTable, state, "bottomSort", { idx: 0, dir: "desc" });

    const applyStatusFilters = () => {
      if (!bottomTable || !bottomTable.tBodies || !bottomTable.tBodies[0]) return;
      const hideApprovedOn = Boolean(hideApproved && hideApproved.checked);
      const hideRejectedOn = Boolean(hideRejected && hideRejected.checked);

      Array.from(bottomTable.tBodies[0].rows).forEach((tr) => {
        const status = String(tr.getAttribute("data-review-status") || "").toLowerCase();
        let show = true;
        if (hideApprovedOn && status === "approved") show = false;
        if (hideRejectedOn && status === "rejected") show = false;
        tr.style.display = show ? "" : "none";
      });
    };

    if (hideApproved) {
      hideApproved.checked = Boolean(state.hideApproved);
      hideApproved.addEventListener("change", () => {
        state.hideApproved = hideApproved.checked;
        saveState(state);
        applyStatusFilters();
      });
    }

    if (hideRejected) {
      hideRejected.checked = Boolean(state.hideRejected);
      hideRejected.addEventListener("change", () => {
        state.hideRejected = hideRejected.checked;
        saveState(state);
        applyStatusFilters();
      });
    }

    if (exportBtn) {
      exportBtn.addEventListener("click", () => exportVisibleContracts(bottomTable));
    }

    applyStatusFilters();
  });
})();
