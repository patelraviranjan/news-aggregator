/**
 * Live-update poller.
 *
 * Every second this checks /api/pulse -- a single cheap DB aggregate query,
 * not a re-fetch of any external RSS/news URL -- to see if new articles
 * have landed since the page was rendered. External feeds themselves are
 * still only pulled on the backend schedule (see scheduler.py); this script
 * just makes sure that as soon as new content exists in *our* database
 * (from the scheduler, a manual "fetch now", or a source being added/
 * edited), every open browser tab picks it up within about a second,
 * without the visitor having to refresh the page.
 *
 * Only runs on pages that opt in via data-live-page on #live-region
 * (home / channel / category templates).
 */
(function () {
  var POLL_MS = 1000;

  document.addEventListener("DOMContentLoaded", function () {
    var region = document.getElementById("live-region");
    if (!region) return;
    var livePage = region.getAttribute("data-live-page");
    if (!livePage) return;

    var lastVersion = null;
    var refreshing = false;

    function applyUpdate(html) {
      var parser = new DOMParser();
      var doc = parser.parseFromString(html, "text/html");
      var freshRegion = doc.getElementById("live-region");
      if (!freshRegion) return;

      region.innerHTML = freshRegion.innerHTML;

      // Re-init anything that scans the DOM on load.
      if (window.AOS && typeof AOS.refreshHard === "function") {
        AOS.refreshHard();
      }
      document.dispatchEvent(new CustomEvent("live-content-updated"));

      // Briefly flag the region so a stylesheet/animation can hook a subtle
      // "just updated" cue if desired -- purely cosmetic, safe if unused.
      region.classList.add("just-updated");
      setTimeout(function () { region.classList.remove("just-updated"); }, 600);
    }

    function checkPulse() {
      if (refreshing || document.hidden) return;
      fetch("/api/pulse", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          if (lastVersion === null) {
            lastVersion = data.v;
            return;
          }
          if (data.v !== lastVersion) {
            lastVersion = data.v;
            refreshing = true;
            return fetch(window.location.href, { cache: "no-store" })
              .then(function (r) { return r.ok ? r.text() : null; })
              .then(function (html) {
                if (html) applyUpdate(html);
              })
              .finally(function () { refreshing = false; });
          }
        })
        .catch(function () { /* network hiccup -- just try again next tick */ });
    }

    setInterval(checkPulse, POLL_MS);
  });
})();
