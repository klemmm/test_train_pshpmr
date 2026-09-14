(function () {
  "use strict";

  const ROLE_ICON = { "départ": "🚉", "correspondance": "🔄", "arrivée": "🏁" };
  const ROLE_LABEL = { "départ": "Départ", "correspondance": "Correspondance", "arrivée": "Arrivée" };
  const LINE_ICON = { success: "✓", info: "ℹ", warning: "⚠", error: "✗" };
  const VERDICT_META = { OK: ["OK", "ok"], MEH: ["Vigilance", "meh"], KO: ["Bloquant", "ko"] };
  const WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
  const MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
  ];
  const TIME_RE = /^([0-1]?\d|2[0-3]):[0-5]\d$/;

  const form = document.getElementById("trip-form");
  const dateInput = document.getElementById("trip-date");
  const stepsEl = document.getElementById("steps");
  const addStopBtn = document.getElementById("add-stop");
  const submitBtn = document.getElementById("submit-btn");
  const errorBanner = document.getElementById("error-banner");
  const loadingEl = document.getElementById("loading");
  const resultEl = document.getElementById("result");

  const pasteToggle = document.getElementById("paste-toggle");
  const pastePanel = document.getElementById("paste-panel");
  const pasteText = document.getElementById("paste-text");
  const pasteAnalyzeBtn = document.getElementById("paste-analyze");
  const pasteErrorEl = document.getElementById("paste-error");
  const pasteResultsEl = document.getElementById("paste-results");

  /** @type {{role: string, station: string, arr: string, dep: string}[]} */
  let steps = [
    { role: "départ", station: "", dep: "" },
    { role: "arrivée", station: "", arr: "" },
  ];

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showError(message) {
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  function hideError() {
    errorBanner.hidden = true;
    errorBanner.textContent = "";
  }

  function setLoading(isLoading) {
    loadingEl.hidden = !isLoading;
    submitBtn.disabled = isLoading;
  }

  // ---- autocomplete -------------------------------------------------------

  function setupAutocomplete(input, listEl, step) {
    let debounceTimer = null;
    let items = [];
    let activeIndex = -1;

    function hide() {
      listEl.hidden = true;
      listEl.innerHTML = "";
      activeIndex = -1;
    }

    function updateActive() {
      Array.from(listEl.children).forEach((el, i) => {
        el.classList.toggle("active", i === activeIndex);
      });
    }

    function pick(item) {
      input.value = item.name;
      step.station = item.name;
      hide();
    }

    function show(results) {
      items = results;
      activeIndex = -1;
      if (!results.length) {
        listEl.innerHTML = '<div class="autocomplete-empty">Aucune gare trouvée</div>';
        listEl.hidden = false;
        return;
      }
      listEl.innerHTML = results
        .map(
          (r, i) => `
        <div class="autocomplete-item" data-idx="${i}">
          <span>${escapeHtml(r.name)} <span class="uic">UIC ${escapeHtml(r.uic)}</span></span>
          <span class="badge ${r.eligible ? "yes" : "no"}">${r.eligible ? "PMR ✓" : "PMR ?"}</span>
        </div>`
        )
        .join("");
      listEl.hidden = false;
      Array.from(listEl.children).forEach((el) => {
        el.addEventListener("mousedown", (e) => {
          e.preventDefault();
          pick(items[Number(el.dataset.idx)]);
        });
      });
    }

    input.addEventListener("input", () => {
      step.station = input.value;
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (q.length < 2) {
        hide();
        return;
      }
      debounceTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/stations?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          show(data.results || []);
        } catch (e) {
          hide();
        }
      }, 200);
    });

    input.addEventListener("keydown", (e) => {
      if (listEl.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
        updateActive();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        updateActive();
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && items[activeIndex]) {
          e.preventDefault();
          pick(items[activeIndex]);
        } else {
          hide();
        }
      } else if (e.key === "Escape") {
        hide();
      }
    });

    input.addEventListener("blur", () => setTimeout(hide, 100));
  }

  function bindTimeInput(input, step, field) {
    input.addEventListener("input", () => {
      step[field] = input.value;
    });
    input.addEventListener("blur", () => {
      const v = input.value.trim();
      input.classList.toggle("invalid", v !== "" && !TIME_RE.test(v));
    });
  }

  // ---- form rendering -------------------------------------------------------

  function renderSteps() {
    stepsEl.innerHTML = "";
    steps.forEach((step, idx) => {
      const isDeparture = step.role === "départ";
      const isArrival = step.role === "arrivée";

      const stepEl = document.createElement("div");
      stepEl.className = "step";

      const removeBtn = !isDeparture && !isArrival
        ? `<button type="button" class="btn-danger" title="Supprimer cette correspondance" data-remove="${idx}">✕</button>`
        : "";

      const rowClass = isDeparture || isArrival ? "with-time" : "with-times";

      stepEl.innerHTML = `
        <div class="step-marker">${ROLE_ICON[step.role] || "•"}</div>
        <div class="step-body">
          <div class="step-title">
            <span class="role-tag">${ROLE_LABEL[step.role] || step.role}</span>
            ${removeBtn}
          </div>
          <div class="step-row ${rowClass}">
            <div class="autocomplete">
              <input type="text" class="station-input" placeholder="Gare (nom ou code UIC)" autocomplete="off" value="${escapeHtml(step.station)}">
              <div class="autocomplete-list" hidden></div>
            </div>
            ${!isDeparture ? `<input type="text" class="arr-input" placeholder="HH:MM arrivée" value="${escapeHtml(step.arr || "")}">` : ""}
            ${!isArrival ? `<input type="text" class="dep-input" placeholder="HH:MM départ" value="${escapeHtml(step.dep || "")}">` : ""}
          </div>
        </div>
      `;

      const stationInput = stepEl.querySelector(".station-input");
      const listEl = stepEl.querySelector(".autocomplete-list");
      setupAutocomplete(stationInput, listEl, step);

      const arrInput = stepEl.querySelector(".arr-input");
      if (arrInput) bindTimeInput(arrInput, step, "arr");
      const depInput = stepEl.querySelector(".dep-input");
      if (depInput) bindTimeInput(depInput, step, "dep");

      const removeButtonEl = stepEl.querySelector("[data-remove]");
      if (removeButtonEl) {
        removeButtonEl.addEventListener("click", () => {
          steps.splice(idx, 1);
          renderSteps();
        });
      }

      stepsEl.appendChild(stepEl);
    });
  }

  addStopBtn.addEventListener("click", () => {
    steps.splice(steps.length - 1, 0, { role: "correspondance", station: "", arr: "", dep: "" });
    renderSteps();
  });

  // ---- paste-import (coller une page 1.2.TRAIN) --------------------------

  pasteToggle.addEventListener("click", () => {
    const expanded = pasteToggle.getAttribute("aria-expanded") === "true";
    pasteToggle.setAttribute("aria-expanded", String(!expanded));
    pastePanel.hidden = expanded;
    if (!expanded) pasteText.focus();
  });

  function showPasteError(message) {
    pasteErrorEl.textContent = message;
    pasteErrorEl.hidden = false;
  }

  function hidePasteError() {
    pasteErrorEl.hidden = true;
    pasteErrorEl.textContent = "";
  }

  function journeyBadge(journey) {
    if (journey.error) return `<span class="pill unknown">?</span>`;
    const [label, cls] = VERDICT_META[journey.report.verdict.name];
    return `<span class="pill ${cls}">${label}</span>`;
  }

  function renderJourneyOption(journey, idx) {
    const unresolved = journey.steps.filter((s) => s.resolved === false);
    const stopsSummary = journey.steps
      .map((s) => escapeHtml(s.station))
      .join(" → ");
    const warnHtml = unresolved.length
      ? `<span class="warn">⚠ ${unresolved.length} gare(s) non reconnue(s)</span>`
      : "";
    const dateHtml = journey.date
      ? `${journey.date.slice(6, 8)}/${journey.date.slice(4, 6)}/${journey.date.slice(0, 4)}`
      : "date inconnue";
    return `
      <div class="journey-item">
        <button type="button" class="journey-option" data-idx="${idx}" aria-expanded="false">
          <div class="journey-head">
            <span class="journey-route">${escapeHtml(journey.from)} → ${escapeHtml(journey.to)}</span>
            <span class="journey-times">${escapeHtml(journey.departure)}–${escapeHtml(journey.arrival)}</span>
            ${journeyBadge(journey)}
            <span class="chevron">▾</span>
          </div>
          <div class="journey-meta">
            <span>${dateHtml}</span>
            <span>·</span>
            <span>${journey.transfers} correspondance${journey.transfers > 1 ? "s" : ""}</span>
            ${journey.duration_text ? `<span>· ${escapeHtml(journey.duration_text)}</span>` : ""}
            ${warnHtml}
          </div>
          <div class="journey-meta">${stopsSummary}</div>
        </button>
        <div class="journey-detail" hidden></div>
      </div>`;
  }

  function loadJourney(journey) {
    steps = journey.steps.map((s) => ({
      role: s.role,
      station: s.station,
      arr: s.arr || "",
      dep: s.dep || "",
    }));
    if (journey.date) {
      dateInput.value = `${journey.date.slice(0, 4)}-${journey.date.slice(4, 6)}-${journey.date.slice(6, 8)}`;
    }
    renderSteps();
    hideError();
    resultEl.innerHTML = "";
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderJourneyDetail(journey, idx) {
    let html = "";
    if (journey.error) {
      html += `<div class="journey-error">${escapeHtml(journey.error)}</div>`;
    } else {
      html += reportBodyHtml(journey.report);
    }
    html += `<div class="journey-detail-actions">
      <button type="button" class="btn btn-ghost" data-load="${idx}">Charger dans le formulaire</button>
    </div>`;
    return html;
  }

  function toggleJourneyDetail(itemEl, journey, idx) {
    const btn = itemEl.querySelector(".journey-option");
    const detailEl = itemEl.querySelector(".journey-detail");
    const expanded = btn.getAttribute("aria-expanded") === "true";
    if (expanded) {
      btn.setAttribute("aria-expanded", "false");
      detailEl.hidden = true;
      return;
    }
    if (!detailEl.dataset.rendered) {
      detailEl.innerHTML = renderJourneyDetail(journey, idx);
      detailEl.dataset.rendered = "1";
      const loadBtn = detailEl.querySelector("[data-load]");
      if (loadBtn) {
        loadBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          loadJourney(journey);
        });
      }
    }
    btn.setAttribute("aria-expanded", "true");
    detailEl.hidden = false;
  }

  pasteAnalyzeBtn.addEventListener("click", async () => {
    hidePasteError();
    pasteResultsEl.innerHTML = "";
    const text = pasteText.value;
    if (!text.trim()) {
      showPasteError("Collez d'abord le texte d'une page 1.2.TRAIN.");
      return;
    }
    pasteAnalyzeBtn.disabled = true;
    try {
      const res = await fetch("/api/parse-paste", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok) {
        showPasteError(data.error || "Analyse impossible.");
        return;
      }
      const journeys = data.journeys || [];
      pasteResultsEl.innerHTML = journeys.map(renderJourneyOption).join("");
      Array.from(pasteResultsEl.querySelectorAll(".journey-item")).forEach((itemEl, i) => {
        itemEl.querySelector(".journey-option").addEventListener("click", () => {
          toggleJourneyDetail(itemEl, journeys[i], i);
        });
      });
    } catch (e) {
      showPasteError("Impossible de contacter le service.");
    } finally {
      pasteAnalyzeBtn.disabled = false;
    }
  });

  // ---- result rendering (mirrors report_html.py) -----------------------

  function formatDateFr(dayStr) {
    const y = Number(dayStr.slice(0, 4));
    const m = Number(dayStr.slice(4, 6));
    const d = Number(dayStr.slice(6, 8));
    const jsDow = new Date(y, m - 1, d).getDay(); // 0 = Sunday
    const idx = (jsDow + 6) % 7; // 0 = Monday, matching WEEKDAYS_FR order
    return `${WEEKDAYS_FR[idx]} ${d} ${MONTHS_FR[m - 1]} ${y}`;
  }

  function renderTimes(station) {
    let out = "";
    if (station.arrival_time) {
      out += `<span class="chip">↘ arrivée <b>${escapeHtml(station.arrival_time)}</b></span>`;
    }
    if (station.departure_time) {
      out += `<span class="chip">↗ départ <b>${escapeHtml(station.departure_time)}</b></span>`;
    }
    return out;
  }

  function renderLine(line) {
    const icon = LINE_ICON[line.kind] || "ℹ";
    return `<li class="pmr-line ${line.kind}"><span class="pmr-icon">${icon}</span><span>${escapeHtml(line.text)}</span></li>`;
  }

  function renderStation(station) {
    const icon = ROLE_ICON[station.role] || "•";
    const [label, cls] = VERDICT_META[station.verdict.name];

    let transferHtml = "";
    if (station.transfer_minutes !== null && station.transfer_minutes !== undefined) {
      const warn = station.transfer_warning;
      const badgeCls = warn ? "meh" : "ok";
      transferHtml = `<div class="transfer ${badgeCls}">${warn ? "⚠" : "✓"} Correspondance : ${station.transfer_minutes} min${warn ? " (&lt; 30 min)" : ""}</div>`;
    }

    return `
      <li class="station">
        <div class="marker ${cls}">${icon}</div>
        <div class="result-card">
          <div class="card-head">
            <div class="card-title">
              <span class="idx">${station.n}</span>
              <span class="name">${escapeHtml(station.name)}</span>
              <span class="uic">UIC ${escapeHtml(station.uic)}</span>
            </div>
            <span class="pill ${cls}">${label}</span>
          </div>
          <div class="role-row">
            <span class="role-tag">${ROLE_LABEL[station.role] || station.role}</span>
            ${renderTimes(station)}
          </div>
          ${transferHtml}
          <ul class="pmr-lines">${station.lines.map(renderLine).join("")}</ul>
        </div>
      </li>`;
  }

  function reportBodyHtml(report) {
    const [label, cls] = VERDICT_META[report.verdict.name];
    const stationsHtml = report.stations.map(renderStation).join("");
    return `
      <header>
        <div>
          <p class="subtitle">${escapeHtml(formatDateFr(report.day))}</p>
        </div>
        <span class="pill big ${cls}">${label}</span>
      </header>
      <ol class="timeline">${stationsHtml}</ol>
    `;
  }

  function renderResult(report) {
    resultEl.innerHTML = reportBodyHtml(report);
  }

  // ---- submit -------------------------------------------------------------

  function buildEtapes() {
    const etapes = [];
    steps.forEach((step) => {
      if (step.role === "départ") {
        etapes.push(step.station, step.dep);
      } else if (step.role === "arrivée") {
        etapes.push(step.station, step.arr);
      } else {
        etapes.push(step.station, step.arr, step.dep);
      }
    });
    return etapes;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideError();
    resultEl.innerHTML = "";

    if (!dateInput.value) {
      showError("Merci de choisir une date.");
      return;
    }
    const date = dateInput.value.replace(/-/g, "");
    const etapes = buildEtapes();
    if (etapes.some((t) => !String(t || "").trim())) {
      showError("Merci de renseigner toutes les gares et tous les horaires.");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, etapes }),
      });
      const data = await res.json();
      if (!res.ok) {
        showError(data.error || "Erreur inconnue.");
        return;
      }
      renderResult(data);
    } catch (err) {
      showError("Impossible de contacter le service.");
    } finally {
      setLoading(false);
    }
  });

  renderSteps();
})();
