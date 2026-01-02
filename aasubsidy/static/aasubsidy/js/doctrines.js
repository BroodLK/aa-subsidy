(function() {
  const onReady = (fn) => (document.readyState !== 'loading') ? fn() : document.addEventListener('DOMContentLoaded', fn);
  onReady(() => {
      function setupAutocomplete(inputId, resultsId, hiddenId, category) {
          const searchInput = document.getElementById(inputId);
          const resultsContainer = document.getElementById(resultsId);
          const selectedIdInput = document.getElementById(hiddenId);
          let debounceTimer;

          if (!searchInput) return;

          searchInput.addEventListener('input', function() {
              clearTimeout(debounceTimer);
              const query = this.value;

              if (query.length < 3) {
                  resultsContainer.style.display = 'none';
                  resultsContainer.innerHTML = '';
                  return;
              }

              debounceTimer = setTimeout(() => {
                  let url = `${window.AASubsidyConfig.locationSearchUrl}?q=${encodeURIComponent(query)}`;
                  if (category) {
                      url += `&category=${category}`;
                  }
                  fetch(url, {
                      credentials: 'same-origin',
                      headers: { 'Accept': 'application/json' },
                  })
                  .then(response => {
                      if (!response.ok) {
                          throw new Error(`location_search HTTP ${response.status}`);
                      }
                      return response.json();
                  })
                  .then(data => {
                      resultsContainer.innerHTML = '';

                      const results = (data && data.results) ? data.results : [];
                      if (results.length > 0) {
                          results.forEach(item => {
                              const btn = document.createElement('button');
                              btn.type = 'button';
                              btn.className = 'list-group-item list-group-item-action list-group-item-dark py-1 px-2 small';
                              btn.textContent = item.name;
                              btn.addEventListener('click', function() {
                                  searchInput.value = item.name;
                                  selectedIdInput.value = item.id;
                                  resultsContainer.style.display = 'none';
                              });
                              resultsContainer.appendChild(btn);
                          });
                          resultsContainer.style.display = 'block';
                      } else {
                          resultsContainer.style.display = 'none';
                      }
                  })
                  .catch(err => {
                      console.error('location_search failed:', err);
                      resultsContainer.style.display = 'none';
                  });
              }, 300);
          });

          document.addEventListener('click', function(e) {
              if (!resultsContainer.contains(e.target) && e.target !== searchInput) {
                  resultsContainer.style.display = 'none';
              }
          });
      }

      setupAutocomplete('systemNameSearch', 'systemSearchResults', 'selectedSystemEveId', 'solar_system');
  });
})();
