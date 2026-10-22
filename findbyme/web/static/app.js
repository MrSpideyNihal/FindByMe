"use strict";

const app = { data: null, run: null, plan: null, pollTimer: null };
const $ = (selector) => document.querySelector(selector);

// Build DOM nodes without innerHTML so user text is never parsed as markup.
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const init = { ...options };
  if (init.json !== undefined) {
    init.body = JSON.stringify(init.json);
    init.headers = { "Content-Type": "application/json" };
    delete init.json;
  }
  const response = await fetch(path, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return body;
}

function notify(message, bad = false) {
  const el = $("#notice");
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.hidden = !message;
}

async function busy(button, label, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await task();
  } catch (error) {
    notify(error.message, true);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function show(view) {
  if (!app.data.project) view = "setup";
  for (const section of document.querySelectorAll(".view")) {
    section.hidden = section.id !== `view-${view}`;
  }
  for (const tab of document.querySelectorAll(".tab")) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
    tab.disabled = !app.data.project;
  }
  window.scrollTo(0, 0);
  if (view === "library") loadLibrary();
}

function options(select, items, selected) {
  select.replaceChildren(
    ...items.map(([value, label]) => h("option", { value, selected: value === selected }, label)),
  );
}

async function refresh() {
  app.data = await api("/api/state");
  $("#root").textContent = app.data.root;
  if (!app.data.project) {
    renderSetup();
    show("setup");
    return;
  }
  renderCreateForm();
  renderCast();
  renderSettings();
}

/* ---------- one-time setup ---------- */

function memberFields(index, prefix) {
  const preview = h("div", { class: "preview" }, "No image yet");
  const file = h("input", {
    type: "file", accept: "image/png,image/jpeg,image/webp", required: true,
    id: `${prefix}-image-${index}`, name: "image",
    onchange: (event) => {
      const [picked] = event.target.files;
      if (!picked) return;
      const img = h("img", { src: URL.createObjectURL(picked), alt: "Reference preview" });
      preview.replaceChildren(img);
    },
  });
  return h("div", { class: "member", "data-index": index },
    preview,
    h("div", { class: "field" }, h("label", { for: file.id }, "Reference image"), file),
    h("div", { class: "field" },
      h("label", { for: `${prefix}-name-${index}` }, "Name"),
      h("input", { id: `${prefix}-name-${index}`, name: "name", required: true })),
    h("div", { class: "field" },
      h("label", { for: `${prefix}-role-${index}` }, "Role"),
      h("input", { id: `${prefix}-role-${index}`, name: "role", placeholder: "mother, son, host" })),
    h("div", { class: "field" },
      h("label", { for: `${prefix}-sheet-${index}` }, "Voice sheet"),
      h("textarea", {
        id: `${prefix}-sheet-${index}`, name: "voice_sheet", rows: 4,
        placeholder: "How they talk: accent, pace, mood, favourite phrases. Leave empty for the default.",
      })),
  );
}

function renderSetup() {
  const countInput = $("#setup-count");
  const grid = $("#setup-cast");
  const sync = () => {
    const wanted = Math.max(1, Math.min(12, Number(countInput.value) || 1));
    while (grid.children.length < wanted) {
      grid.append(h("div", { class: "cast-card" },
        h("h3", {}, `Character ${grid.children.length + 1}`),
        memberFields(grid.children.length, "setup")));
    }
    while (grid.children.length > wanted) grid.lastElementChild.remove();
  };
  countInput.oninput = sync;
  sync();
  options($("#setup-language"), Object.entries(app.data.languages), "en");
  options($("#setup-aspect"), app.data.aspects.map((a) => [a, a]), "9:16");
}

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  const cast = [];
  for (const member of document.querySelectorAll("#setup-cast .member")) {
    cast.push({
      name: member.querySelector("[name=name]").value,
      role: member.querySelector("[name=role]").value,
      voice_sheet: member.querySelector("[name=voice_sheet]").value,
    });
    form.append("images", member.querySelector("[name=image]").files[0]);
  }
  form.append("cast", JSON.stringify(cast));
  form.append("language", $("#setup-language").value);
  form.append("aspect", $("#setup-aspect").value);
  const button = event.submitter;
  const ok = await busy(button, "Creating...", () => api("/api/setup", { method: "POST", body: form }));
  if (ok) {
    notify("Project created. Write your first video below.");
    await refresh();
    show("create");
  }
});

/* ---------- create: script, edit, render ---------- */

function renderCreateForm() {
  const select = $("#style");
  const current = select.value || "explainer";
  options(select, app.data.styles.map((s) => [s.name, s.name]), current);
  const describe = () => {
    const style = app.data.styles.find((s) => s.name === select.value);
    $("#style-help").textContent = style ? `${style.description} (${style.seconds}s)` : "";
  };
  select.onchange = describe;
  describe();

  const placeholders = (app.data.doctor || []).filter((c) => c.warn);
  if (placeholders.length) {
    notify(`No API key for: ${placeholders.map((c) => c.name.split(":")[0]).join(", ")}. `
      + "Those parts of the video will be placeholders. Add keys to .env in the project "
      + "folder and restart, or see Settings for details.", true);
  }
}

$("#script-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const seconds = Number($("#seconds").value) || null;
  const result = await busy($("#script-btn"), "Writing...", () => api("/api/script", {
    method: "POST",
    json: { topic: $("#topic").value, style: $("#style").value, notes: $("#notes").value, seconds },
  }));
  if (!result) return;
  notify("");
  openPlan(result.run, result.plan);
});

function openPlan(run, plan, finished = null) {
  app.run = run;
  app.plan = plan;
  $("#plan-title").textContent = plan.title;
  $("#plan-run").textContent = run;
  $("#editor").hidden = false;
  $("#job").hidden = true;
  $("#result").hidden = true;
  renderLines();
  if (finished) showResult(finished);
}

function renderLines() {
  const cast = app.data.project.characters;
  const rows = app.plan.lines.map((line, i) => {
    const bind = (key) => (event) => { app.plan.lines[i][key] = event.target.value; };
    const speaker = h("select", { "aria-label": `Line ${i + 1} speaker`, onchange: bind("speaker") });
    options(speaker, cast.map((c) => [c.id, c.name]), line.speaker);
    return h("div", { class: "line" },
      h("span", { class: "num" }, i + 1),
      speaker,
      h("textarea", { rows: 2, "aria-label": `Line ${i + 1} text`, value: line.text, oninput: bind("text") }),
      h("textarea", {
        rows: 2, "aria-label": `Line ${i + 1} action`, value: line.action,
        placeholder: "hands, head, body", oninput: bind("action"),
      }),
      h("input", { "aria-label": `Line ${i + 1} emotion`, value: line.emotion, oninput: bind("emotion") }),
      h("button", {
        type: "button", class: "danger", "aria-label": `Remove line ${i + 1}`, title: "Remove line",
        onclick: () => { app.plan.lines.splice(i, 1); renderLines(); },
      }, "x"),
    );
  });
  $("#lines").replaceChildren(...rows);
}

$("#add-line").addEventListener("click", () => {
  const last = app.plan.lines[app.plan.lines.length - 1];
  app.plan.lines.push({
    speaker: last ? last.speaker : app.data.project.characters[0].id,
    text: "", emotion: "neutral", action: "", camera: "medium shot",
  });
  renderLines();
});

$("#render-btn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const job = await busy(button, "Starting...", async () => {
    await api(`/api/runs/${app.run}/plan`, { method: "PUT", json: app.plan });
    return api(`/api/runs/${app.run}/render`, { method: "POST" });
  });
  if (!job) return;
  notify("");
  $("#job").hidden = false;
  $("#job-error").hidden = true;
  $("#result").hidden = true;
  button.disabled = true;
  poll(job.id, button);
});

function poll(jobId, button) {
  clearTimeout(app.pollTimer);
  const tick = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
    } catch (error) {
      notify(error.message, true);
      button.disabled = false;
      return;
    }
    renderSteps(job.steps);
    if (job.status === "running") {
      app.pollTimer = setTimeout(tick, 1500);
      return;
    }
    button.disabled = false;
    if (job.status === "failed") {
      $("#job-error").textContent = job.error;
      $("#job-error").hidden = false;
    } else {
      $("#job").hidden = true;
      showResult(job.result);
    }
  };
  tick();
}

function renderSteps(steps) {
  const labels = { lines: "Voice and animation", compose: "Final cut" };
  const rows = Object.entries(steps).filter(([name]) => name !== "script").map(([name, step]) => {
    const pct = step.total ? Math.round((step.done / step.total) * 100) : 0;
    return h("div", { class: "step" },
      h("div", { class: "step-label" },
        h("span", {}, labels[name] || name),
        h("span", { class: "muted" }, `${step.done}/${step.total}  ${step.message}`)),
      h("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
        h("span", { style: `width:${pct}%` })));
  });
  $("#steps").replaceChildren(...rows);
}

function showResult(run) {
  const manifest = run.manifest || {};
  const box = $("#result");
  box.replaceChildren(h("div", { class: "result" },
    h("div", {},
      h("video", { src: run.video, controls: true, playsinline: true, poster: run.cover || null }),
      h("p", {}, h("a", { href: run.video, download: `${run.name}.mp4` }, "Download MP4"))),
    h("div", {},
      h("h2", {}, run.title),
      h("p", { class: "muted" },
        `${manifest.duration ?? "?"}s video, rendered in ${manifest.render_seconds ?? "?"}s`),
      h("h3", {}, "Post caption"),
      h("pre", {}, run.post || ""),
      run.frames ? h("img", { class: "frames", src: run.frames, alt: "Frame contact sheet" }) : null),
  ));
  box.hidden = false;
}

/* ---------- library ---------- */

async function loadLibrary() {
  const runs = await api("/api/runs").catch((error) => { notify(error.message, true); return []; });
  const grid = $("#runs");
  if (!runs.length) {
    grid.replaceChildren(h("p", { class: "muted" }, "No videos yet."));
    return;
  }
  grid.replaceChildren(...runs.map((run) => h("button", {
    type: "button", class: "run",
    onclick: async () => {
      const detail = await api(`/api/runs/${run.name}`);
      show("create");
      openPlan(run.name, detail.plan, detail.video ? detail : null);
    },
  },
  run.cover ? h("img", { src: run.cover, alt: "" }) : h("div", { class: "preview" }, "Not rendered"),
  h("div", { class: "run-body" },
    h("div", { class: "run-title" }, run.title),
    h("div", { class: "run-meta" }, `${run.style} - ${run.lines} lines`)))));
}

/* ---------- cast ---------- */

function renderCast() {
  const sheets = app.data.sheets || {};
  const cards = app.data.project.characters.map((c) => {
    const sheet = h("textarea", { rows: 4, id: `sheet-${c.id}`, value: c.voice_sheet ? sheets[c.id] : "" ,
      placeholder: "Default voice sheet" });
    const voice = h("input", { id: `voice-${c.id}`, value: c.tts_voice });
    return h("div", { class: "cast-card" },
      h("img", { src: `/files/${c.image}`, alt: `${c.name} reference image` }),
      h("h3", {}, c.name),
      h("div", { class: "meta" }, `${c.role || "no role"} - id ${c.id}`),
      h("div", { class: "field" }, h("label", { for: voice.id }, "TTS voice"), voice),
      h("div", { class: "field" }, h("label", { for: sheet.id }, "Voice sheet"), sheet),
      h("div", { class: "row" },
        h("button", {
          type: "button",
          onclick: async (event) => {
            const ok = await busy(event.currentTarget, "Saving...", () => api(`/api/cast/${c.id}/voice`, {
              method: "PUT", json: { sheet: sheet.value, tts_voice: voice.value },
            }));
            if (ok) { app.data = ok; notify(`Saved ${c.name}'s voice.`); }
          },
        }, "Save voice"),
        h("button", {
          type: "button", class: "danger",
          onclick: async (event) => {
            if (!window.confirm(`Remove ${c.name} from the cast?`)) return;
            const ok = await busy(event.currentTarget, "Removing...", () => api(`/api/cast/${c.id}`, { method: "DELETE" }));
            if (ok) await refresh();
          },
        }, "Remove")));
  });
  $("#cast-list").replaceChildren(...cards);
  $("#add-fields").replaceChildren(memberFields(0, "add"));
}

$("#add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const member = event.target.querySelector(".member");
  const form = new FormData();
  for (const key of ["name", "role", "voice_sheet"]) {
    form.append(key, member.querySelector(`[name=${key}]`).value);
  }
  form.append("image", member.querySelector("[name=image]").files[0]);
  const ok = await busy(event.submitter, "Adding...", () => api("/api/cast", { method: "POST", body: form }));
  if (ok) { notify("Character added."); await refresh(); }
});

/* ---------- settings ---------- */

function settingField(key, label, value, kind = "text", choices = null) {
  const id = `set-${key.replaceAll(".", "-")}`;
  let input;
  if (choices) {
    input = h("select", { id });
    options(input, choices.map((c) => [c, c]), value);
  } else if (kind === "checkbox") {
    input = h("input", { id, type: "checkbox", checked: value });
  } else {
    input = h("input", { id, type: kind, value: value ?? "" });
  }
  input.addEventListener("change", async () => {
    const raw = kind === "checkbox" ? String(input.checked) : input.value;
    try {
      app.data = await api("/api/settings", { method: "POST", json: { key, value: raw } });
      notify(`Saved ${key}.`);
      renderChecks();
    } catch (error) {
      notify(error.message, true);
    }
  });
  return h("div", { class: "field" }, h("label", { for: id }, label), input);
}

function renderChecks() {
  $("#checks").replaceChildren(...(app.data.doctor || []).map((c) => h("tr", {},
    h("td", { class: c.ok && !c.warn ? "ok" : "bad" }, c.ok ? (c.warn ? "placeholder" : "ok") : "fix"),
    h("td", {}, c.name),
    h("td", { class: "muted" }, c.detail))));
}

function renderSettings() {
  renderChecks();
  const p = app.data.project;
  const providerFields = [];
  for (const kind of ["llm", "tts", "stt", "video"]) {
    providerFields.push(
      settingField(`providers.${kind}.provider`, `${kind.toUpperCase()} provider`, p.providers[kind].provider),
      settingField(`providers.${kind}.model`, `${kind.toUpperCase()} model`, p.providers[kind].model),
    );
  }
  $("#providers").replaceChildren(...providerFields);

  const r = p.render;
  $("#render-settings").replaceChildren(
    settingField("language", "Language", p.language, "text", Object.keys(app.data.languages)),
    settingField("render.aspect", "Aspect ratio", r.aspect, "text", app.data.aspects),
    settingField("render.short_side", "Short side (px)", r.short_side, "number"),
    settingField("render.fps", "Frames per second", r.fps, "number"),
    settingField("render.concurrency", "Lines in parallel", r.concurrency, "number"),
    settingField("render.voice_takes", "Max voice takes", r.voice_takes, "number"),
    settingField("render.captions.enabled", "Captions", r.captions.enabled, "checkbox"),
    settingField("render.captions.words_per_group", "Words per caption", r.captions.words_per_group, "number"),
    settingField("render.captions.highlight", "Highlight colour", r.captions.highlight, "color"),
    settingField("render.music", "Background music file", r.music || ""),
  );
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => show(tab.dataset.view));
}

function hideBoot() {
  const boot = $("#boot");
  if (boot) boot.hidden = true;
}

refresh()
  .then(() => {
    hideBoot();
    if (app.data.project) show("create");
  })
  .catch((error) => {
    // Keep the "not running" hint visible when the server cannot be reached at all.
    if (!(error instanceof TypeError)) {
      hideBoot();
      notify(error.message, true);
    }
  });
