/* AnotherLab — front-end */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const STYLE_SUFFIX = {
    cinematic: ", cinematic lighting, anamorphic lens, film still, dramatic contrast",
    photo: ", photorealistic, natural skin texture, 85mm lens, shallow depth of field",
    anime: ", anime-style digital illustration, clean linework, cel shading",
    product: ", product photography, seamless backdrop, softbox, crisp detail",
    poster: ", graphic poster design, bold typography, limited palette",
    illustration: ", editorial illustration, textured brushwork, muted palette",
  };

  const QUALITY = { draft: 512, balanced: 768, detail: 1024 };

  const SURPRISES = [
    "A miniature bonsai tree in a black ceramic pot on a maple table, morning light through a shoji screen, soft shadows, quiet zen mood, photographic",
    "An editorial photograph of a bonsai artist's studio, hands shaping a pine over a low wooden bench, scattered tools, warm window light, shallow depth of field",
    "Anime-style digital illustration of a girl with short teal hair and green eyes, oversized red high-collared jacket, night street, neon signs, cinematic composition",
    "A minimalist product poster for a matte black perfume bottle on a sandstone plinth, hard sun, long shadow, the words NEW FORM in clean sans-serif type",
    "A tiny cabin on a Norwegian fjord at blue hour, warm window light, low fog, long exposure water, muted teal and amber palette",
    "Close-up portrait of an older fisherman, weathered skin, salt-and-pepper beard, harbour lights bokeh, 105mm, natural light",
    "A retro-futurist transit poster for Mars, bold geometric type, burnt orange and cream, screen-print texture, the words MARS LINE",
    "A bowl of ramen on a dark walnut table, steam catching a single hard light, overhead view, deep shadows, rich reds and golds",
    "A paper-craft diorama of a small city block, layered card stock, soft studio light, pastel palette, tilt-shift",
    "A black cat in a spacesuit floating inside a space station, Earth in the window, cool blue light, photorealistic",
  ];

  const state = {
    config: null,
    status: null,
    jobs: new Map(),
    activeId: null,
    ratio: "1:1",
    quality: "draft",
    styles: new Set(),
    key: localStorage.getItem("anotherlab.key") || "",
    es: null,
    failedConnects: 0,
    seenExpired: new Set(),
    // Persisted browser preference (key: anotherlab.autoDownload). "1" = on.
    autoDownload: localStorage.getItem("anotherlab.autoDownload") === "1",
    // Jobs whose finished image still needs saving while a download is in
    // flight (or the tab reloaded mid-download).
    autoDownloadPending: new Set(),
    // Jobs already auto-saved (per job id, so a reconnect/snapshot never
    // saves the same image twice). Persisted and trimmed.
    autoDownloadSaved: new Set(
      safeJsonList(localStorage.getItem("anotherlab.autoDownloadSaved"))
    ),
    autoDownloadDraining: false,
  };

  function safeJsonList(raw) {
    try {
      const value = JSON.parse(raw || "[]");
      return Array.isArray(value) ? value : [];
    } catch (_) {
      return [];
    }
  }

  function persistAutoDownloadLists() {
    localStorage.setItem(
      "anotherlab.autoDownloadPending",
      JSON.stringify([...state.autoDownloadPending])
    );
    localStorage.setItem(
      "anotherlab.autoDownloadSaved",
      JSON.stringify([...state.autoDownloadSaved].slice(-20))
    );
  }

  // ---------------------------------------------------------------- utils
  function toast(message, kind = "") {
    const box = $("#toasts");
    const node = document.createElement("div");
    node.className = "toast " + kind;
    node.textContent = message;
    box.appendChild(node);
    setTimeout(() => {
      node.style.transition = "opacity .3s, transform .3s";
      node.style.opacity = "0";
      node.style.transform = "translateY(6px)";
      setTimeout(() => node.remove(), 320);
    }, 3800);
  }

  function fmtClock(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function fmtDuration(seconds) {
    if (seconds == null) return "—";
    if (seconds < 90) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return s ? `${m}m ${s}s` : `${m}m`;
  }

  function withKey(url) {
    if (!state.key) return url;
    return url + (url.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(state.key);
  }

  function imageUrl(job, download = false) {
    if (!job || !job.id) return "";
    const base = `/api/images/${encodeURIComponent(job.id)}`;
    const params = [];
    if (download) params.push("download=1");
    if (state.key) params.push("key=" + encodeURIComponent(state.key));
    params.push("t=" + (job.finished_at || job.created_at || 0));
    return base + "?" + params.join("&");
  }

  async function api(path, options = {}, retried = false) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (state.key) opts.headers["Authorization"] = "Bearer " + state.key;
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401 && !retried) {
      const entered = window.prompt("This AnotherLab needs an API key:");
      if (entered) {
        state.key = entered.trim();
        localStorage.setItem("anotherlab.key", state.key);
        return api(path, options, true);
      }
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  }

  // ---------------------------------------------------------------- dims
  function dimensions() {
    const base = QUALITY[state.quality] || 512;
    const [rw, rh] = state.ratio.split(":").map(Number);
    const scale = base / Math.max(rw, rh);
    const min = state.config?.limits?.min_dimension || 256;
    let width = Math.round((rw * scale) / 16) * 16;
    let height = Math.round((rh * scale) / 16) * 16;
    width = Math.max(min, width);
    height = Math.max(min, height);
    return { width, height };
  }

  function composedPrompt() {
    const base = $("#prompt").value.trim();
    const suffix = [...state.styles].map((s) => STYLE_SUFFIX[s]).join("");
    return (base + suffix).trim();
  }

  // ---------------------------------------------------------------- render
  function renderEngine() {
    const dot = $("#engine-dot");
    const label = $("#engine-label");
    const status = state.status;
    const counts = status?.counts || {};
    const running = (counts.running || 0) + (counts.queued || 0);

    dot.className = "dot";
    if (!status) {
      label.textContent = "connecting…";
    } else if (!status.ready) {
      dot.classList.add("bad");
      label.textContent = "engine offline";
    } else if (running > 0) {
      dot.classList.add("busy");
      label.textContent = `${(status.device_type || "cpu").toUpperCase()} · working`;
    } else {
      dot.classList.add("ok");
      label.textContent = `${(status.device_type || "cpu").toUpperCase()} · ready`;
    }

    const queuePill = $("#queue-pill");
    if (running > 0) {
      queuePill.hidden = false;
      $("#queue-label").textContent = running === 1 ? "1 in queue" : `${running} in queue`;
    } else {
      queuePill.hidden = true;
    }

    if (status?.comfy_version) {
      $("#footer-version").textContent = "ComfyUI " + status.comfy_version;
    }
    if (status?.device) {
      $("#engine-pill").title = `${status.device} · ${status.model?.unet || ""}`;
    }
  }

  function jobReceived(job) {
    job._recv = Date.now();
    const previous = state.jobs.get(job.id);
    state.jobs.set(job.id, job);
    if (!state.activeId && (job.status === "queued" || job.status === "running")) {
      state.activeId = job.id;
    }
    // Succeeded is terminal and can be observed late (SSE reconnect,
    // /api/generations poll) — pick the auto-save up in any case.
    if (job.status === "succeeded" && previous?.status !== "succeeded") {
      queueAutoDownload(job);
    }
  }

  function activeJob() {
    return state.activeId ? state.jobs.get(state.activeId) : null;
  }

  function show(view) {
    for (const id of ["empty-state", "progress-view", "result-view", "error-view"]) {
      const node = document.getElementById(id);
      if (node) node.hidden = id !== view;
    }
  }

  function renderStage() {
    const job = activeJob();
    const heading = $("#stage-heading");
    const eyebrow = $("#stage-eyebrow");
    const actions = $("#stage-actions");
    actions.innerHTML = "";

    if (!job) {
      show("empty-state");
      eyebrow.textContent = "Canvas";
      heading.textContent = "Nothing here yet";
      return;
    }

    if (job.status === "queued" || job.status === "running") {
      show("progress-view");
      eyebrow.textContent = job.status === "queued" ? "Queued" : "Rendering";
      heading.textContent = job.status === "queued" ? "In the queue" : "Painting";
      const pct = Math.max(0, Math.min(1, job.progress || 0));
      $("#progress-pct").textContent = Math.round(pct * 100) + "%";
      $("#progress-step").textContent =
        job.status === "queued"
          ? `position ${job.queue_position ?? "—"}`
          : job.total_steps
            ? `step ${job.step || 0} / ${job.total_steps}`
            : "loading model";
      $("#progress-title").textContent =
        job.status === "queued" ? "Waiting for the engine" : "Painting your image";
      $("#progress-sub").textContent =
        job.status === "queued"
          ? "One job at a time. You can close this tab; the job keeps running."
          : "This engine runs on CPU. The first job also loads the model.";
      $("#progress-bar").style.width = (pct * 100).toFixed(1) + "%";
      const ring = $("#ring");
      ring.style.strokeDashoffset = String(326.7 * (1 - pct));
      const eta = job.eta_seconds;
      $("#progress-eta").textContent =
        eta > 0 ? `about ${fmtDuration(eta)} left` : "almost there…";
      $("#cancel-btn").hidden = false;
      return;
    }

    if (job.status === "succeeded") {
      show("result-view");
      eyebrow.textContent = "Result";
      heading.textContent = "Done";
      const img = $("#result-img");
      const src = imageUrl(job);
      if (img.dataset.src !== src) {
        img.dataset.src = src;
        img.src = src;
      }
      $("#result-prompt").textContent = job.prompt;
      const seconds =
        job.finished_at && job.started_at ? job.finished_at - job.started_at : null;
      $("#result-stats").textContent =
        `${job.width}×${job.height} · ${job.steps} steps\nseed ${job.seed} · ${fmtDuration(seconds)}`;
      $("#result-stats").style.whiteSpace = "pre-line";
      const dl = document.createElement("button");
      dl.className = "btn";
      dl.textContent = "Download";
      dl.onclick = () => download(job);
      actions.appendChild(dl);
      renderTtl();
      return;
    }

    if (job.status === "expired") {
      show("error-view");
      eyebrow.textContent = "Gone";
      heading.textContent = "Image expired";
      $("#error-text").textContent =
        "This image hit its five-minute limit and was deleted. Generate it again to get a fresh copy.";
      $("#retry-btn").textContent = "Generate again";
      $("#retry-btn").onclick = () => generate();
      return;
    }

    // failed / cancelled
    show("error-view");
    eyebrow.textContent = job.status === "cancelled" ? "Cancelled" : "Error";
    heading.textContent = job.status === "cancelled" ? "Cancelled" : "That one failed";
    $("#error-text").textContent =
      job.error || "The engine stopped before returning an image.";
    $("#retry-btn").textContent = "Try again";
    $("#retry-btn").onclick = () => regenerate(job);
  }

  function renderTtl() {
    const job = activeJob();
    const badge = $("#ttl-badge");
    if (!job || job.status !== "succeeded") return;
    const remaining = remainingSeconds(job);
    $("#ttl-text").textContent = fmtClock(remaining);
    badge.classList.toggle("warn", remaining <= 120 && remaining > 30);
    badge.classList.toggle("crit", remaining <= 30);
  }

  function remainingSeconds(job) {
    if (!job || job.expires_in == null) return 0;
    const elapsed = (Date.now() - (job._recv || Date.now())) / 1000;
    return Math.max(0, job.expires_in - elapsed);
  }

  function renderRecent() {
    const strip = $("#recent-strip");
    const jobs = [...state.jobs.values()]
      .filter((j) => j.source !== "warmup")
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
      .slice(0, 14);

    strip.innerHTML = "";
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "recent-empty";
      empty.textContent = "Generated images will appear here.";
      strip.appendChild(empty);
      return;
    }

    for (const job of jobs) {
      const card = document.createElement("div");
      card.className = "card";
      if (job.id === state.activeId) card.classList.add("is-active");

      if (job.status === "succeeded" && job.image_url) {
        const img = document.createElement("img");
        img.alt = job.prompt.slice(0, 80);
        img.loading = "lazy";
        img.src = imageUrl(job);
        card.appendChild(img);

        const ttl = document.createElement("div");
        ttl.className = "card-ttl";
        ttl.dataset.expiresFor = job.id;
        ttl.textContent = fmtClock(remainingSeconds(job));
        card.appendChild(ttl);
      } else {
        const overlay = document.createElement("div");
        overlay.className = "card-state";
        overlay.dataset.stateFor = job.id;
        overlay.textContent = stateLabel(job);
        card.appendChild(overlay);
      }

      const caption = document.createElement("div");
      caption.className = "card-prompt";
      caption.textContent = job.prompt;
      card.appendChild(caption);

      const hit = document.createElement("button");
      hit.className = "card-hit";
      hit.type = "button";
      hit.setAttribute("aria-label", "Open generation");
      hit.onclick = () => {
        state.activeId = job.id;
        renderStage();
        renderRecent();
        document.querySelector(".stage")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      };
      card.appendChild(hit);
      strip.appendChild(card);
    }
  }

  function stateLabel(job) {
    if (job.status === "queued") return "queued";
    if (job.status === "running") return `${Math.round((job.progress || 0) * 100)}%`;
    if (job.status === "expired") return "expired";
    if (job.status === "cancelled") return "cancelled";
    if (job.status === "failed") return "failed";
    return job.status;
  }

  function tick() {
    const job = activeJob();
    if (job && job.status === "succeeded") {
      const remaining = remainingSeconds(job);
      renderTtl();
      if (remaining <= 0 && !state.seenExpired.has(job.id)) {
        state.seenExpired.add(job.id);
        job.status = "expired";
        job.image_url = null;
        renderStage();
        renderRecent();
      }
    }
    for (const node of $$("[data-expires-for]")) {
      const target = state.jobs.get(node.dataset.expiresFor);
      if (!target) continue;
      node.textContent = fmtClock(remainingSeconds(target));
    }
    for (const node of $$("[data-state-for]")) {
      const target = state.jobs.get(node.dataset.stateFor);
      if (target) node.textContent = stateLabel(target);
    }
    if (job && (job.status === "queued" || job.status === "running")) {
      renderStage();
    }
  }

  // ---------------------------------------------------------------- actions
  async function generate() {
    const prompt = $("#prompt").value.trim();
    if (!prompt) {
      toast("Write a prompt first.", "bad");
      $("#prompt").focus();
      return;
    }
    const { width, height } = dimensions();
    const body = {
      prompt: composedPrompt(),
      width,
      height,
      steps: Number($("#steps").value) || 4,
      cfg: Number($("#cfg").value) || 1,
      seed: $("#seed").value === "" ? null : Number($("#seed").value),
      negative_prompt: $("#negative").value || state.config?.defaults?.negative || "",
    };
    const button = $("#generate");
    button.disabled = true;
    try {
      const job = await api("/api/generate", { method: "POST", body });
      jobReceived(job);
      state.activeId = job.id;
      renderStage();
      renderRecent();
      renderEngine();
      toast("Queued. The engine takes it from here.", "good");
    } catch (err) {
      toast(err.message || "Could not queue the job.", "bad");
    } finally {
      button.disabled = false;
    }
  }

  function loadPrompt(job) {
    $("#prompt").value = job.prompt;
    $("#prompt").dispatchEvent(new Event("input"));
    // The stored prompt already contains any style suffixes; clear the chips so
    // regenerating does not append them twice.
    state.styles.clear();
    for (const chip of $$(".chip")) chip.classList.remove("is-active");
  }

  function regenerate(job) {
    loadPrompt(job);
    $("#seed").value = job.seed;
    $("#steps").value = job.steps;
    $("#cfg").value = job.cfg;
    generate();
  }

  // Fetch the image bytes. Returns a Blob, or null when the file is already
  // gone. Used by both the manual download button and auto-download.
  async function fetchImageBlob(job) {
    const res = await fetch(withKey(imageUrl(job, true)));
    if (!res.ok) return null;
    return res.blob();
  }

  function triggerBrowserDownload(blob, filename) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  async function download(job) {
    try {
      const blob = await fetchImageBlob(job);
      if (blob == null) throw new Error("Image is gone");
      triggerBrowserDownload(blob, `anotherlab-${job.id}.png`);
      toast("Saved to your downloads.", "good");
    } catch (err) {
      toast(err.message || "Download failed.", "bad");
    }
  }

  // Auto-download: saves the PNG the moment the job succeeds, so the user
  // never has to wait on the tab for the file to land. One small drain loop
  // (no parallel browser saves), and the pending list survives a reload so
  // a long CPU job that finishes after a refresh is still saved.
  function queueAutoDownload(job) {
    if (!state.autoDownload || !job || job.status !== "succeeded") return;
    if (state.autoDownloadSaved.has(job.id)) return;  // already handled / dedupe
    if (state.autoDownloadPending.has(job.id)) return; // save in flight
    state.autoDownloadPending.add(job.id);
    state.autoDownloadSaved.add(job.id); // no retry on failure: the 5-min TTL means the retry would almost certainly re-fail
    persistAutoDownloadLists();
    drainAutoDownloads();
  }

  function drainAutoDownloads() {
    if (state.autoDownloadDraining) return;
    state.autoDownloadDraining = true;
    (async () => {
      while (state.autoDownload && state.autoDownloadPending.size > 0) {
        const id = state.autoDownloadPending.values().next().value;
        const job = state.jobs.get(id);
        if (!job) {
          // Job record not loaded yet (just after a reload); the incoming
          // snapshot/refresh will re-trigger the drain.
          state.autoDownloadDraining = false;
          return;
        }
        await saveAutoDownload(job);
      }
      state.autoDownloadDraining = false;
      persistAutoDownloadLists();
    })();
  }

  async function saveAutoDownload(job) {
    // Claim the id synchronously so no second save can claim it too.
    state.autoDownloadPending.delete(job.id);
    const wasActive = job.id === state.activeId;
    try {
      const blob = await fetchImageBlob(job);
      if (blob == null) throw new Error("image is gone");
      triggerBrowserDownload(blob, `anotherlab-${job.id}.png`);
      if (wasActive) toast("Auto-saved to your downloads.", "good");
    } catch (err) {
      if (wasActive) toast("Auto-download failed: " + (err.message || "image deleted."), "bad");
    }
    persistAutoDownloadLists();
  }

  async function cancelActive() {
    const job = activeJob();
    if (!job) return;
    try {
      const updated = await api(`/api/generations/${job.id}/cancel`, { method: "POST" });
      jobReceived(updated);
      renderStage();
      renderRecent();
      toast("Cancelled.");
    } catch (err) {
      toast(err.message || "Could not cancel.", "bad");
    }
  }

  async function deleteActive() {
    const job = activeJob();
    if (!job) return;
    try {
      await api(`/api/generations/${job.id}`, { method: "DELETE" });
      job.status = "expired";
      job.image_url = null;
      renderStage();
      renderRecent();
      toast("Deleted.");
    } catch (err) {
      toast(err.message || "Could not delete.", "bad");
    }
  }

  // ---------------------------------------------------------------- events
  function connect() {
    if (state.es) state.es.close();
    const url = withKey("/api/events");
    const es = new EventSource(url);
    state.es = es;
    es.onopen = () => {
      state.failedConnects = 0;
    };
    es.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      handleEvent(payload);
    };
    es.onerror = () => {
      state.failedConnects += 1;
      if (state.failedConnects === 3) refresh();
    };
  }

  function handleEvent(event) {
    if (event.type === "snapshot") {
      state.status = event.status;
      for (const job of event.jobs || []) jobReceived(job);
      if (!state.activeId) {
        const active = (event.jobs || []).find(
          (j) => j.status === "queued" || j.status === "running"
        );
        if (active) state.activeId = active.id;
      }
      renderEngine();
      renderStage();
      renderRecent();
      return;
    }
    if (event.type === "job" && event.job) {
      const previous = state.jobs.get(event.job.id);
      if (previous && previous.status === "succeeded" && event.job.status === "expired") {
        state.seenExpired.add(event.job.id);
      }
      jobReceived(event.job);
      if (event.job.id === state.activeId) renderStage();
      renderRecent();
      renderEngine();
      if (event.job.status === "succeeded" && previous?.status !== "succeeded") {
        toast("Image ready. It self-destructs in 5 minutes.", "good");
      }
      if (event.job.status === "failed") {
        toast("Generation failed.", "bad");
      }
      return;
    }
    if (event.type === "expired") {
      const job = state.jobs.get(event.id);
      if (job) {
        job.status = "expired";
        job.image_url = null;
        if (job.id === state.activeId) renderStage();
        renderRecent();
      }
      return;
    }
    if (event.type === "status") {
      state.status = event.status;
      renderEngine();
    }
  }

  async function refresh() {
    try {
      const data = await api("/api/generations?limit=24");
      state.status = data.status;
      for (const job of data.generations || []) jobReceived(job);
      if (!state.activeId) {
        const active = (data.generations || []).find(
          (j) => j.status === "queued" || j.status === "running"
        );
        if (active) state.activeId = active.id;
      }
      renderEngine();
      renderStage();
      renderRecent();
    } catch (_) {
      /* offline; SSE or the next poll will recover */
    }
  }

  // ---------------------------------------------------------------- UI wiring
  function bindComposer() {
    const prompt = $("#prompt");
    prompt.addEventListener("input", () => {
      const max = state.config?.limits?.max_prompt_chars || 4000;
      $("#char-count").textContent = `${prompt.value.length} / ${max}`;
    });

    $("#surprise").addEventListener("click", () => {
      const pick = SURPRISES[Math.floor(Math.random() * SURPRISES.length)];
      prompt.value = pick;
      prompt.dispatchEvent(new Event("input"));
      prompt.focus();
    });

    for (const chip of $$(".chip")) {
      chip.addEventListener("click", () => {
        const style = chip.dataset.style;
        if (state.styles.has(style)) {
          state.styles.delete(style);
          chip.classList.remove("is-active");
        } else {
          state.styles.add(style);
          chip.classList.add("is-active");
        }
      });
    }

    for (const button of $$(".ratio")) {
      button.addEventListener("click", () => {
        for (const other of $$(".ratio")) other.classList.remove("is-active");
        button.classList.add("is-active");
        state.ratio = button.dataset.ratio;
        updateSize();
      });
    }

    for (const button of $$("#quality button")) {
      button.addEventListener("click", () => {
        for (const other of $$("#quality button")) other.classList.remove("is-active");
        button.classList.add("is-active");
        state.quality = button.dataset.quality;
        updateSize();
      });
    }

    $("#generate").addEventListener("click", generate);
    $("#cancel-btn").addEventListener("click", cancelActive);
    $("#auto-download").addEventListener("change", (event) => {
      state.autoDownload = event.target.checked;
      localStorage.setItem("anotherlab.autoDownload", state.autoDownload ? "1" : "0");
      $("#generate-note").textContent = state.autoDownload
        ? "Images are deleted 5 minutes after they are ready — with this on, finished images save to your browser automatically."
        : "Images are deleted 5 minutes after they are ready.";
      if (!state.autoDownload) {
        // Switching off means "stop saving what is still queued".
        state.autoDownloadPending.clear();
        persistAutoDownloadLists();
      }
    });
    $("#download-btn").addEventListener("click", () => {
      const job = activeJob();
      if (job) download(job);
    });
    $("#reuse-btn").addEventListener("click", () => {
      const job = activeJob();
      if (!job) return;
      loadPrompt(job);
      $("#prompt").focus();
      toast("Prompt loaded.");
    });
    $("#delete-btn").addEventListener("click", deleteActive);

    for (const sample of $$(".sample")) {
      sample.addEventListener("click", () => {
        prompt.value = sample.dataset.prompt || "";
        prompt.dispatchEvent(new Event("input"));
        prompt.focus();
        $("#prompt").scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }

    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        generate();
      }
      if (event.key === "Escape") closeModal();
    });
  }

  function updateSize() {
    const { width, height } = dimensions();
    $("#size-label").textContent = `${width} × ${height}`;
    $("#generate-meta").textContent = `${width} × ${height} · ${$("#steps").value || 4} steps`;
    const note = $("#speed-note");
    if (state.quality === "draft") {
      note.textContent = "CPU engine. Draft is the only sensible choice for quick iterations.";
    } else if (state.quality === "balanced") {
      note.textContent = "Balanced is ~2.25× the pixels of Draft and takes noticeably longer.";
    } else {
      note.textContent = "Detail on CPU can take tens of minutes. Use it for a final pass.";
    }
  }

  // ---------------------------------------------------------------- modal
  function mcpUrl() {
    const base = state.config?.public_url || location.origin;
    return base.replace(/\/$/, "") + "/mcp";
  }

  function buildAgentPanel() {
    const url = mcpUrl();
    $("#mcp-url").textContent = url;
    $("#mcp-config").textContent = JSON.stringify(
      { mcpServers: { anotherlab: { type: "http", url } } },
      null,
      2
    );
    $("#mcp-cli").textContent = `claude mcp add --transport http anotherlab ${url}`;
    $("#mcp-stdio").textContent =
      `python -m gateway.mcp_stdio --url ${url}` +
      (state.config?.auth_required ? " --key <API_KEY>" : "");
    $("#rest-generate").textContent =
      `curl -s ${location.origin}/api/generate \\\n` +
      `  -H 'Content-Type: application/json' \\\n` +
      `  -d '{"prompt":"a bonsai tree in a black ceramic pot, soft light","width":512,"height":512,"steps":4}'`;
    $("#rest-poll").textContent =
      `curl -s ${location.origin}/api/generations/<id>\n` +
      `curl -s -o out.png ${location.origin}/api/images/<id>`;

    const tools = [
      ["generate_image", "prompt · width · height · steps · cfg · seed · wait_seconds", "Generate an image and wait for the PNG. The image comes back inline as base64."],
      ["get_generation", "id", "Check your generation by id (live progress while running); returns the PNG inline while it is alive."],
      ["cancel_generation", "id", "Stop a queued or running generation."],
      ["engine_info", "—", "Device, queue, defaults, limits, model and TTL."],
    ];
    const list = $("#tool-list");
    list.innerHTML = "";
    for (const [name, args, desc] of tools) {
      const row = document.createElement("div");
      row.className = "tool";
      row.innerHTML =
        `<div class="tool-head"><span class="tool-name"></span><span class="tool-args"></span></div><p></p>`;
      $(".tool-name", row).textContent = name;
      $(".tool-args", row).textContent = args;
      $("p", row).textContent = desc;
      list.appendChild(row);
    }
  }

  function openModal() {
    buildAgentPanel();
    $("#agent-modal").hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    $("#agent-modal").hidden = true;
    document.body.style.overflow = "";
  }

  function bindModal() {
    $("#agent-btn").addEventListener("click", openModal);
    for (const node of $$("[data-close]")) node.addEventListener("click", closeModal);
    for (const tab of $$(".tab")) {
      tab.addEventListener("click", () => {
        for (const other of $$(".tab")) other.classList.remove("is-active");
        tab.classList.add("is-active");
        for (const panel of $$(".tab-panel")) {
          panel.hidden = panel.dataset.panel !== tab.dataset.tab;
        }
      });
    }
    document.addEventListener("click", async (event) => {
      const button = event.target.closest(".copy");
      if (!button) return;
      const target = document.getElementById(button.dataset.copy);
      if (!target) return;
      const text = target.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy"), 1400);
      } catch (_) {
        toast("Clipboard blocked by the browser.", "bad");
      }
    });
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    bindComposer();
    bindModal();

    try {
      state.config = await api("/api/config");
    } catch (_) {
      state.config = null;
    }

    // Restore the auto-download state: preference, and any job that was
    // mid-save when the page reloaded (its file may still be within TTL).
    state.autoDownloadPending = new Set(
      safeJsonList(localStorage.getItem("anotherlab.autoDownloadPending"))
    );
    const autoDownloadBox = $("#auto-download");
    autoDownloadBox.checked = state.autoDownload;
    $("#generate-note").textContent = state.autoDownload
      ? "Images are deleted 5 minutes after they are ready — with this on, finished images save to your browser automatically."
      : "Images are deleted 5 minutes after they are ready.";

    if (state.config?.defaults) {
      $("#steps").value = state.config.defaults.steps;
      $("#cfg").value = state.config.defaults.cfg;
      $("#negative").value = state.config.defaults.negative;
      $("#prompt").placeholder =
        "A bonsai tree in a black ceramic pot, morning light through a shoji screen…";
    }
    if (state.config?.limits?.max_prompt_chars) {
      $("#prompt").maxLength = state.config.limits.max_prompt_chars;
    }
    if (state.config?.device_label) {
      $("#speed-note").textContent = `${state.config.device_label} engine. Draft is the fastest option.`;
    }

    updateSize();
    await refresh();
    connect();
    // Jobs are hydrated; pick up any auto-save that a reload interrupted.
    if (state.autoDownload && state.autoDownloadPending.size > 0) drainAutoDownloads();
    setInterval(tick, 1000);
    setInterval(() => {
      if (!state.status || !state.status.ready) refresh();
    }, 20000);
  }

  boot();
})();
