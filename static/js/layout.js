(function () {
  var mobileQuery = window.matchMedia("(max-width: 1100px)");
  var toggles = Array.prototype.slice.call(document.querySelectorAll("[data-sidebar-toggle]"));
  var closeTargets = Array.prototype.slice.call(document.querySelectorAll("[data-sidebar-close]"));
  var sidebar = document.getElementById("sidebarPanel");
  var header = document.querySelector(".app-header");
  var storageKey = "intermilan.sidebarCollapsed";

  function readCollapsedState() {
    try {
      var val = window.localStorage.getItem(storageKey);
      if (val === null || val === undefined) {
        return true; // Default collapsed when user has no stored preference yet
      }
      return val === "true";
    } catch (error) {
      return true;
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

    var exactHeight = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--topbar-height", exactHeight + "px");
    document.documentElement.style.setProperty("--app-header-offset", exactHeight + "px");
    document.documentElement.style.setProperty("--app-sidebar-sticky-top", exactHeight + "px");
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

  // Progressive Enhancement: enable JS-driven animation styles safely (Phase UI-CARD-1)
  document.documentElement.classList.add("js-enabled");

  // Lightweight Viewport Entrance & Container Scroll Animations
  var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var animTargets = Array.prototype.slice.call(document.querySelectorAll(".animate-card, .animate-on-scroll"));

  if (!prefersReducedMotion && "IntersectionObserver" in window && animTargets.length > 0) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target); // Execute only once per instruction
        }
      });
    }, { threshold: 0.05, rootMargin: "0px 0px 50px 0px" });
    animTargets.forEach(function (el) { observer.observe(el); });
  } else {
    // If reduced motion, unsupported browser, or fallback, make visible immediately
    animTargets.forEach(function (el) {
      el.classList.add("is-visible");
    });
  }

  // Dedicated Reveal on Scroll for Compact Contact Cards (Section F)
  var contactCards = Array.prototype.slice.call(document.querySelectorAll(".home-contact-card.reveal-on-scroll"));
  if (contactCards.length > 0) {
    if (!prefersReducedMotion && "IntersectionObserver" in window) {
      var revealObserver = new IntersectionObserver(function (entries, obs) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            obs.unobserve(entry.target);
          }
        });
      }, { root: null, threshold: 0.15, rootMargin: "0px 0px -10% 0px" });
      contactCards.forEach(function (card) { revealObserver.observe(card); });
    } else {
      contactCards.forEach(function (card) {
        card.classList.add("is-visible");
      });
    }
  }

  // Approved Container Scroll Animation for Hero Logo Section
  var heroScrollSection = document.querySelector(".js-hero-scroll-target");
  if (heroScrollSection && !prefersReducedMotion) {
    var ticking = false;
    var startRotateX = 20; // 18-22deg
    var startScale = 0.88; // 0.86-0.90
    var startTranslateY = 16; // reduced to keep overlap tight with title

    // Adjust parameters responsively based on screen width
    var width = window.innerWidth || document.documentElement.clientWidth;
    if (width < 640) {
      startRotateX = 7;
      startScale = 0.94;
      startTranslateY = 6;
    } else if (width < 1100) {
      startRotateX = 14;
      startScale = 0.91;
      startTranslateY = 10;
    }

    var isHeroVisible = true;
    if ("IntersectionObserver" in window) {
      var heroObserver = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
          isHeroVisible = entry.isIntersecting;
        });
      }, { rootMargin: "250px 0px 250px 0px" });
      heroObserver.observe(heroScrollSection);
    }

    function updateHeroScroll() {
      var scrollY = window.pageYOffset || document.documentElement.scrollTop;
      // Phase 1: From scroll 0 to center threshold, transitions smoothly to upright position
      var centerDistance = 320;
      if (scrollY <= centerDistance) {
        var progress = Math.max(0, Math.min(1, scrollY / centerDistance));
        var rotateX = (startRotateX * (1 - progress)).toFixed(2);
        var scale = (startScale + (1 - startScale) * progress).toFixed(4);
        var translateY = (startTranslateY * (1 - progress)).toFixed(1);

        heroScrollSection.style.setProperty("--home-rotate-x", rotateX + "deg");
        heroScrollSection.style.setProperty("--home-scale", scale);
        heroScrollSection.style.setProperty("--home-translate-y", translateY + "px");
        heroScrollSection.style.setProperty("--home-scroll-y", translateY + "px");
      } else {
        // Phase 2: Saat berada di tengah atau lanjut scroll, container tetap tegak dan bergerak natural
        heroScrollSection.style.setProperty("--home-rotate-x", "0deg");
        heroScrollSection.style.setProperty("--home-scale", "1");
        heroScrollSection.style.setProperty("--home-translate-y", "0px");
        heroScrollSection.style.setProperty("--home-scroll-y", "0px");
      }
      ticking = false;
    }

    // Initial render
    updateHeroScroll();

    window.addEventListener("scroll", function () {
      if (!ticking && isHeroVisible) {
        window.requestAnimationFrame(updateHeroScroll);
        ticking = true;
      }
    }, { passive: true });
  }
})();

