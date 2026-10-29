"use strict";
(() => {
  const form = document.querySelector("[data-upload-form]");
  if (!form) return;
  const core = window.LinkVaultUpload;
  const fileInput = form.querySelector("[data-file-input]"), folderInput = form.querySelector("[data-folder-input]");
  const submit = form.querySelector(".upload-submit"), pause = form.querySelector("[data-pause-upload]");
  const summary = form.querySelector(".upload-selection"), list = form.querySelector(".selected-files");
  const progress = form.querySelector("progress"), shell = form.querySelector(".progress-shell");
  const phase = form.querySelector("[data-upload-phase]"), percent = form.querySelector("[data-upload-percent]");
  const metrics = form.querySelector("[data-upload-metrics]"), pending = form.querySelector("[data-pending-uploads]");
  const clear = form.querySelector("[data-clear-upload]");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const sessionBase = form.dataset.sessionBase || "/uploads/";
  let files = [], busy = false, selecting = false, paused = false, sessions = [], xhr = null;
  const hex = buffer => [...new Uint8Array(buffer)].map(n => n.toString(16).padStart(2, "0")).join("");
  const digest = async blob => hex(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
  const mib = bytes => (bytes / 1024 ** 2).toLocaleString(document.documentElement.lang, {maximumFractionDigits: 1});
  const sizeLabel = bytes => {
    if (bytes < 1024) return `${bytes} ${gettext("octets")}`;
    const unit = bytes < 1024 ** 2 ? gettext("Kio") : bytes < 1024 ** 3 ? gettext("Mio") : gettext("Gio");
    const divisor = bytes < 1024 ** 2 ? 1024 : bytes < 1024 ** 3 ? 1024 ** 2 : 1024 ** 3;
    return `${(bytes / divisor).toLocaleString(document.documentElement.lang, {maximumFractionDigits: 1})} ${unit}`;
  };
  const api = async (url, body) => {
    const response = await fetch(url, {method: "POST", credentials: "same-origin", redirect: "error",
      headers: {"X-CSRFToken": csrf, "Content-Type": "application/json"}, body});
    let data;
    try { data = await response.json(); } catch { throw new Error(gettext("Envoi refusé. Reconnectez-vous si votre session a expiré.")); }
    if (!response.ok) throw new Error(data.error || gettext("Envoi refusé."));
    return data;
  };
  const blockRequest = (url, block, offset, checksum, onProgress) => new Promise((resolve, reject) => {
    xhr = new XMLHttpRequest();
    xhr.open("POST", url); xhr.responseType = "json"; xhr.timeout = 300000;
    xhr.setRequestHeader("X-CSRFToken", csrf);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("Upload-Offset", String(offset));
    xhr.setRequestHeader("Upload-Checksum", checksum);
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.min(event.loaded, block.size)); };
    xhr.onload = () => {
      const data = xhr.response;
      if ((xhr.status >= 200 && xhr.status < 300 || xhr.status === 409) && data && Number.isSafeInteger(data.offset)) resolve(data);
      else reject(new Error(data?.error || gettext("Envoi refusé. Reconnectez-vous si votre session a expiré.")));
    };
    xhr.onerror = xhr.ontimeout = () => reject(new TypeError("network"));
    xhr.onabort = () => reject(new Error(gettext("Envoi en pause. Cliquez sur Importer pour reprendre pendant les prochaines 24 h.")));
    xhr.send(block);
  });
  const refresh = async () => {
    const response = await fetch(form.dataset.pendingUrl, {redirect: "error"});
    if (!response.ok) throw new Error(gettext("Impossible de retrouver les envois. Reconnectez-vous."));
    sessions = (await response.json()).sessions;
    pending.replaceChildren(); pending.hidden = !sessions.length;
    for (const session of sessions) {
      const row = document.createElement("div"), text = document.createElement("p"), cancel = document.createElement("button");
      const received = session.parts.reduce((n, p) => n + p.offset, 0);
      text.textContent = interpolate(gettext("%(value1)s fichier(s) en attente · %(value2)s % reçus. Resélectionnez les mêmes fichiers puis cliquez sur Importer pour reprendre."), {value1: session.parts.length, value2: Math.round(received / Math.max(1, session.size) * 100)}, true);
      cancel.textContent = gettext("Annuler cet envoi"); cancel.type = "button"; cancel.className = "button secondary";
      cancel.addEventListener("click", async () => { if (busy) return; try { await api(`${sessionBase}${session.id}/cancel/`, "{}"); await refresh(); } catch (e) { summary.textContent = e.message; } });
      row.append(text, cancel); pending.append(row);
    }
  };
  const controls = () => {
    submit.disabled = busy || selecting || !files.length;
    fileInput.disabled = folderInput.disabled = clear.disabled = busy || selecting;
    list.querySelectorAll("button").forEach(button => { button.disabled = busy || selecting; });
  };
  const render = () => {
    list.replaceChildren(); clear.hidden = !files.length;
    summary.textContent = interpolate(gettext("%(count)s fichier(s) · %(size)s"), {count: files.length, size: sizeLabel(files.reduce((n, item) => n + item.file.size, 0))}, true);
    const root = new Map();
    for (const item of files) {
      let branch = root; const parts = item.path.split("/");
      for (const name of parts.slice(0, -1)) {
        if (!branch.has(name)) branch.set(name, {children: new Map()});
        // The server rejects a path that is both a file and a folder.
        if (!branch.get(name).children) branch.get(name).children = new Map();
        branch = branch.get(name).children;
      }
      branch.set(parts.at(-1), {item});
    }
    const draw = (tree, parent) => {
      for (const [name, node] of tree) {
        const row = document.createElement("li");
        if (node.children) {
          const folder = document.createElement("details"), heading = document.createElement("summary"), children = document.createElement("ul");
          folder.open = true; heading.textContent = name + "/"; children.className = "file-tree";
          draw(node.children, children); folder.append(heading, children); row.append(folder);
        } else {
          const line = document.createElement("div"), text = document.createElement("span"), size = document.createElement("small"), remove = document.createElement("button");
          line.className = "selection-file"; text.textContent = name; text.title = node.item.path;
          size.textContent = sizeLabel(node.item.file.size);
          remove.type = "button"; remove.textContent = "×"; remove.className = "icon-button";
          remove.setAttribute("aria-label", interpolate(gettext("Retirer %(name)s"), {name: node.item.path}, true));
          remove.addEventListener("click", () => { if (!busy) { files = files.filter(item => item !== node.item); render(); } });
          line.append(text, size, remove); row.append(line);
        }
        parent.append(row);
      }
    };
    draw(root, list); controls();
  };
  const choose = added => {
    const result = core.merge(files, added); files = result.files; render();
    if (result.duplicates) summary.textContent += " · " + gettext("Chemins déjà sélectionnés conservés, sans remplacement.");
  };
  const selectionError = error => { summary.textContent = error.message === "limit" ? gettext("Maximum : 100 fichiers et 20 niveaux de dossiers par sélection.") : gettext("Impossible de lire cette sélection. Utilisez les boutons d’ajout."); };
  const picker = input => {
    if (busy || selecting) return;
    try { choose(Array.from(input.files, file => ({file, path: file.webkitRelativePath || file.name})).filter(item => !item.path.split("/").some(name => name.startsWith(".")))); }
    catch (error) { selectionError(error); }
    input.value = "";
  };
  fileInput.addEventListener("change", () => picker(fileInput));
  folderInput.addEventListener("change", () => picker(folderInput));
  clear.addEventListener("click", () => { if (!busy) { files = []; render(); } });
  pause.addEventListener("click", () => { paused = true; pause.disabled = true; xhr?.abort(); });
  window.addEventListener("beforeunload", event => { if (busy) { event.preventDefault(); event.returnValue = ""; } });
  form.addEventListener("dragover", event => { event.preventDefault(); if (!busy) form.classList.add("dragover"); });
  form.addEventListener("dragleave", () => form.classList.remove("dragover"));
  form.addEventListener("drop", async event => {
    event.preventDefault(); form.classList.remove("dragover"); if (busy || selecting) return;
    // Capture entry handles synchronously: the drag data store closes after this event.
    const items = [...event.dataTransfer.items].filter(item => item.kind === "file");
    const entries = items.map(item => item.webkitGetAsEntry?.());
    const fallback = Array.from(event.dataTransfer.files, file => ({file, path: file.name}));
    selecting = true; controls(); summary.textContent = gettext("Lecture des dossiers…");
    try {
      if (entries.length && entries.every(Boolean)) {
        const result = await core.collect(entries); choose(result.files);
        if (result.empty || result.hidden) summary.textContent += " · " + gettext("Les dossiers vides et les fichiers cachés ne sont pas importés.");
      } else choose(fallback);
    } catch (error) { selectionError(error); }
    finally { selecting = false; controls(); }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (busy || selecting || !files.length) return;
    busy = true; paused = false; controls();
    pause.hidden = false; pause.disabled = false; shell.hidden = false; phase.textContent = gettext("Préparation de l’envoi…");
    metrics.textContent = ""; progress.value = 0; percent.textContent = "0 %";
    try {
      await refresh(); pending.hidden = true;
      const manifest = [];
      for (const {file, path} of files) {
        const sample = new Blob([String(file.size), ":", String(file.lastModified), ":", file.slice(0, 65536), file.slice(Math.max(0, file.size - 65536))]);
        manifest.push({path, size: file.size, fingerprint: await digest(sample)});
      }
      if (paused) throw new Error(gettext("Envoi en pause. Cliquez sur Importer pour reprendre pendant les prochaines 24 h."));
      const replaceExisting = Boolean(form.elements.replace_existing?.checked);
      let session = sessions.find(s => Boolean(s.replace_existing) === replaceExisting && s.parts.length === manifest.length && s.parts.every(p => manifest.some(f => f.path === p.path && f.size === p.size && f.fingerprint === p.fingerprint)));
      if (!session) session = await api(form.dataset.startUrl, JSON.stringify({files: manifest, replace_existing: replaceExisting}));
      const total = files.reduce((n, item) => n + item.file.size, 0);
      const baseline = core.progress(session.parts, total).confirmed, started = performance.now();
      const update = (inFlight = 0) => {
        const state = core.progress(session.parts, total, inFlight);
        progress.value = state.percent; percent.textContent = `${state.percent} %`;
        metrics.textContent = interpolate(gettext("%(sent)s / %(total)s Mio transmis · %(confirmed)s Mio confirmés par le serveur"), {sent: mib(state.sent), total: mib(total), confirmed: mib(state.confirmed)}, true);
        const elapsed = (performance.now() - started) / 1000, rate = (state.sent - baseline) / elapsed;
        if (elapsed >= 2 && rate > 0 && state.sent < total) {
          metrics.textContent += " · " + interpolate(gettext("%(speed)s Mio/s · environ %(seconds)s s restantes"), {speed: mib(rate), seconds: Math.ceil((total - state.sent) / rate).toLocaleString(document.documentElement.lang)}, true);
        }
      };
      update();
      for (const part of session.parts) {
        const file = files.find(item => item.path === part.path).file;
        let conflicts = 0;
        while (part.offset < part.size) {
          if (paused) throw new Error(gettext("Envoi en pause. Cliquez sur Importer pour reprendre pendant les prochaines 24 h."));
          phase.textContent = interpolate(gettext("Envoi de %(value1)s"), {value1: part.path}, true);
          const block = file.slice(part.offset, Math.min(part.size, part.offset + session.chunk_bytes));
          const checksum = await digest(block);
          if (paused) continue;
          const result = await blockRequest(`${sessionBase}${session.id}/parts/${part.id}/chunk/`, block, part.offset, checksum, update);
          xhr = null;
          if (!core.validOffset(result.offset, part.size) || result.error && ++conflicts > 3) throw new Error(gettext("Position du transfert invalide. Rechargez la page pour reprendre."));
          part.offset = result.offset; update();
        }
      }
      if (paused) throw new Error(gettext("Envoi en pause avant vérification. Cliquez sur Importer pour terminer."));
      pause.hidden = true; phase.textContent = gettext("Transfert terminé · analyse antivirus et enregistrement…");
      metrics.textContent = gettext("Gardez cette page ouverte. La vérification peut prendre du temps pour un dossier volumineux.");
      progress.value = 100; percent.textContent = "100 %";
      const result = await api(`${sessionBase}${session.id}/finish/`, "{}");
      busy = false; window.location.assign(result.redirect);
    } catch (error) {
      xhr = null;
      summary.textContent = error instanceof TypeError ? gettext("Connexion interrompue. Vos blocs reçus sont conservés : cliquez sur Importer pour reprendre.") : error.message;
      phase.textContent = gettext("En attente");
      try { await refresh(); } catch { /* The connection can still be offline. */ }
      busy = false; controls(); pause.hidden = true;
    }
  });
  render(); refresh().catch(error => { summary.textContent = error.message; });
})();
