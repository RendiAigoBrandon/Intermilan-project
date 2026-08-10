/* INTERMILAN Loader Overlay JavaScript */
(function () {
  var loader = document.getElementById('intermilanGlobalLoader');
  if (!loader) return;

  var titleEl   = loader.querySelector('.js-loader-title');
  var descEl    = loader.querySelector('.js-loader-desc');
  var filesInfo = loader.querySelector('.js-loader-files-info');
  var filesText = loader.querySelector('.js-loader-files-text');

  function showLoader(title, desc) {
    if (titleEl) titleEl.textContent = title || 'Sedang memproses data...';
    if (descEl)  descEl.textContent  = desc  || 'Mohon tunggu beberapa saat.';
    loader.removeAttribute('hidden');
    loader.style.display = 'flex';
  }

  function hideLoader() {
    loader.setAttribute('hidden', '');
    loader.style.display = 'none';
  }

  function setLoaderFiles(count) {
    if (filesInfo) {
      filesInfo.removeAttribute('hidden');
      filesInfo.style.display = 'flex';
    }
    if (filesText) {
      filesText.textContent = count + ' file terpilih';
    }
  }

  function hideLoaderFiles() {
    if (filesInfo) {
      filesInfo.setAttribute('hidden', '');
      filesInfo.style.display = 'none';
    }
  }

  // Expose globally for use by other scripts (DRPP, SP2D, etc.)
  window.IntermilanLoader = {
    show:      showLoader,
    hide:      hideLoader,
    setFiles:  setLoaderFiles,
    hideFiles: hideLoaderFiles,
  };
})();
