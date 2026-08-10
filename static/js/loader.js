/* INTERMILAN Loader Overlay JavaScript */
(function () {
  var loader = document.getElementById('intermilanGlobalLoader');
  var titleEl   = loader ? loader.querySelector('.js-loader-title')   : null;
  var descEl    = loader ? loader.querySelector('.js-loader-desc')    : null;
  var filesInfo = loader ? loader.querySelector('.js-loader-files-info') : null;
  var filesText = loader ? loader.querySelector('.js-loader-files-text') : null;

  function showLoader(title, desc) {
    if (!loader) return;
    if (titleEl) titleEl.textContent = title || 'Sedang memproses data...';
    if (descEl)  descEl.textContent  = desc  || 'Mohon tunggu beberapa saat.';
    loader.removeAttribute('hidden');
    loader.style.display = 'flex';
  }

  function hideLoader() {
    if (!loader) return;
    loader.setAttribute('hidden', '');
    loader.style.display = 'none';
    if (filesInfo) filesInfo.setAttribute('hidden', '');
  }

  function setLoaderFiles(count) {
    if (!loader) return;
    if (filesInfo) {
      filesInfo.removeAttribute('hidden');
      filesInfo.style.display = 'flex';
    }
    if (filesText) {
      filesText.textContent = count + ' file terpilih';
    }
  }

  function hideLoaderFiles() {
    if (!loader) return;
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

  // Auto-bind loading overlay to forms that declare data-loading-title.
  function bindUploadForms() {
    var forms = document.querySelectorAll('form[data-loading-title]');
    for (var i = 0; i < forms.length; i++) {
      (function (form) {
        // Prevent double-binding
        if (form._intermilanLoaderBound) return;
        form._intermilanLoaderBound = true;

        form.addEventListener('submit', function (e) {
          // Only show if HTML form validation passes
          if (!form.checkValidity()) return;

          var title = form.dataset.loadingTitle   || 'Sedang memproses data...';
          var desc  = form.dataset.loadingDescription || '';

          // Count selected files
          var fileInputs = form.querySelectorAll('input[type="file"]');
          var totalFiles = 0;
          for (var j = 0; j < fileInputs.length; j++) {
            totalFiles += fileInputs[j].files.length;
          }

          showLoader(title, desc);
          if (totalFiles > 0) {
            setLoaderFiles(totalFiles);
          }
        });
      })(forms[i]);
    }
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindUploadForms);
  } else {
    bindUploadForms();
  }

  // Reset loader after BFCache restore (Back/Forward button)
  window.addEventListener('pageshow', function () {
    if (e.persisted) {
      hideLoader();
    }
  });
})();
