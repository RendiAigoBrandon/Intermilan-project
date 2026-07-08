(function () {
  var mobileQuery = window.matchMedia("(max-width: 1100px)");
  var toggles = Array.prototype.slice.call(document.querySelectorAll("[data-sidebar-toggle]"));
  var closeTargets = Array.prototype.slice.call(document.querySelectorAll("[data-sidebar-close]"));
  var sidebar = document.getElementById("sidebarPanel");
  var header = document.querySelector(".app-header");
  var storageKey = "intermilan.sidebarCollapsed";

  function readCollapsedState() {
    try {
      return window.localStorage.getItem(storageKey) === "true";
    } catch (error) {
      return false;
    }
  }

  function writeCollapsedState(collapsed) {
    try {
      window.localStorage.setItem(storageKey, collapsed ? "true" : "false");
    } catch (error) {
      return;
    }
  }

  function isMobile() {
    return mobileQuery.matches;
  }

  function setExpanded(expanded) {
    toggles.forEach(function (toggle) {
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
  }

  function closeMobileSidebar() {
    document.body.classList.remove("sidebar-open");
    if (isMobile()) {
      setExpanded(false);
    }
  }

  function syncDesktopState() {
    if (isMobile()) {
      document.body.classList.remove("sidebar-collapsed");
      setExpanded(document.body.classList.contains("sidebar-open"));
      return;
    }

    document.body.classList.remove("sidebar-open");
    var collapsed = readCollapsedState();
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    setExpanded(!collapsed);
  }

  function syncHeaderOffset() {
    if (!header) {
      return;
    }

    var offset = Math.ceil(header.getBoundingClientRect().height + 12);
    document.documentElement.style.setProperty("--app-header-offset", offset + "px");
    document.documentElement.style.setProperty("--app-sidebar-sticky-top", offset + 16 + "px");
  }

  function toggleSidebar() {
    if (isMobile()) {
      document.body.classList.toggle("sidebar-open");
      setExpanded(document.body.classList.contains("sidebar-open"));
      return;
    }

    document.body.classList.toggle("sidebar-collapsed");
    var collapsed = document.body.classList.contains("sidebar-collapsed");
    writeCollapsedState(collapsed);
    setExpanded(!collapsed);
  }

  toggles.forEach(function (toggle) {
    toggle.addEventListener("click", toggleSidebar);
  });

  closeTargets.forEach(function (target) {
    target.addEventListener("click", closeMobileSidebar);
  });

  if (sidebar) {
    sidebar.addEventListener("click", function (event) {
      if (isMobile() && event.target.closest("a.navlink")) {
        closeMobileSidebar();
      }
    });
  }

  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMobileSidebar();
    }
  });

  window.addEventListener("resize", syncHeaderOffset);

  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", syncDesktopState);
  } else if (typeof mobileQuery.addListener === "function") {
    mobileQuery.addListener(syncDesktopState);
  }

  syncHeaderOffset();
  syncDesktopState();
})();
