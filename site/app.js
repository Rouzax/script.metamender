// The live Details window mock: picking a field shows its story, the way the
// window in Kodi does; the swatches repaint it in the add-on's four themes.
(function () {
  "use strict";

  // Wording as the add-on's Details window shows it for the same demo item.
  var STORIES = {
    plot: {
      title: "Plot",
      value: "The night crossing to the islands is delayed when a passenger who boarded at the harbour cannot be found anywhere on the ferry. The crew has until dawn to work out who saw her last.",
      lines: [["From", "TMDB, in English"], ["Replaced", "empty"],
              ["Last checked", "12 Nov 2026"], ["Next check", "19 Nov 2026"]]
    },
    rating: {
      title: "Rating",
      value: "7.4 (312 votes)",
      lines: [["From", "IMDb"], ["Last checked", "12 Nov 2026"], ["Next check", "19 Nov 2026"]]
    },
    runtime: {
      title: "Runtime",
      value: "45 min",
      lines: [["From", "TMDB"], ["Last checked", "12 Nov 2026"], ["Next check", "19 Nov 2026"]]
    },
    aired: {
      title: "Aired",
      value: "1 Nov 2026",
      lines: [["From", "your scraper"], ["Last checked", "12 Nov 2026"],
              ["Next check", "settled, not checked again"],
              ["Why", "Kodi already has this value, so nothing needed to change."]]
    },
    title: {
      title: "Title",
      value: "Departure",
      lines: [["From", "your scraper"], ["Last checked", "12 Nov 2026"],
              ["Next check", "19 Nov 2026"],
              ["Why", "Kodi already has this value, so nothing needed to change."]]
    },
    director: {
      title: "Director",
      value: "Empty",
      lines: [["Last checked", "12 Nov 2026"], ["Next check", "15 Nov 2026"],
              ["Why", "The sites MetaMender asks have nothing for this field yet."]]
    }
  };

  var details = document.querySelector(".details");
  if (!details) {
    return;
  }
  var options = details.querySelectorAll('[role="option"]');
  var pane = details.querySelector(".details-pane");

  function show(option) {
    var story = STORIES[option.getAttribute("data-field")];
    if (!story) {
      return;
    }
    options.forEach(function (o) { o.setAttribute("aria-selected", String(o === option)); });
    pane.querySelector(".pane-title").textContent = story.title;
    pane.querySelector(".pane-value").textContent = story.value;
    var list = pane.querySelector(".pane-lines");
    list.textContent = "";
    story.lines.forEach(function (line) {
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      dt.textContent = line[0];
      dd.textContent = line[1];
      list.appendChild(dt);
      list.appendChild(dd);
    });
  }

  options.forEach(function (option, index) {
    option.addEventListener("mouseenter", function () { show(option); });
    option.addEventListener("focus", function () { show(option); });
    option.addEventListener("click", function () { show(option); });
    option.addEventListener("keydown", function (event) {
      var step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
      if (step && options[index + step]) {
        event.preventDefault();
        options[index + step].focus();
      }
    });
  });

  document.querySelectorAll(".swatch").forEach(function (swatch) {
    swatch.addEventListener("click", function () {
      details.setAttribute("data-theme", swatch.getAttribute("data-theme"));
      document.querySelectorAll(".swatch").forEach(function (s) {
        s.setAttribute("aria-checked", String(s === swatch));
      });
    });
  });
})();
