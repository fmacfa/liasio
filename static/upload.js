"use strict";
// Only metadata is retained by the server. Reselecting the files permits resuming
// after a page reload without granting persistent access to the local filesystem.
(() => {
  const form = document.querySelector("[data-upload-form]");
  if (!form) return;
  const fileInput = form.querySelector("[data-file-input]");
  const folderInput = form.querySelector("[data-folder-input]");
  const submit = form.querySelector(".upload-submit");
  const pause = form.querySelector("[data-pause-upload]");
  const summary = form.querySelector(".upload-selection");
  const list = form.querySelector(".selected-files");
  const progress = form.querySelector("progress");
  const shell = form.querySelector(".progress-shell");
  const phase = form.querySelector("[data-upload-phase]");
  const percent = form.querySelector("[data-upload-percent]");
  const pending = form.querySelector("[data-pending-uploads]");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  let files = [], busy = false, paused = false, sessions = [];
  const pathOf = file => (file.webkitRelativePath || file.name).normalize("NFC");
  const hex = buffer => [...new Uint8Array(buffer)].map(n => n.toString(16).padStart(2, "0")).join("");
  const digest = async blob => hex(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
  const api = async (url, body, extra = {}) => {
    const response = await fetch(url, {method: "POST", credentials: "same-origin", redirect: "error",
      headers: {"X-CSRFToken": csrf, "Content-Type": "application/json", ...extra}, body});
    let data;
    try { data = await response.json(); } catch { throw new Error(interpolate(gettext("Envoi refusé (%(value1)s). Reconnectez-vous si votre session a expiré."), {value1: response.status}, true)); }
    if (!response.ok && response.status !== 409) throw new Error(data.error || gettext("Envoi refusé."));
    return data;
  };
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
      cancel.addEventListener("click", async () => { if (busy) return; try { await api(`/uploads/${session.id}/cancel/`, "{}"); await refresh(); } catch (e) { summary.textContent = e.message; } });
      row.append(text, cancel); pending.append(row);
    }
  };
  const choose = selection => {
    if (busy) return;
    files = Array.from(selection); list.replaceChildren();
    const size = files.reduce((n, f) => n + f.size, 0);
    summary.textContent = interpolate(gettext("%(count)s fichier(s) · %(size)s Mio"), {count: files.length, size: (size / 1024 / 1024).toLocaleString(document.documentElement.lang, {maximumFractionDigits:1})}, true);
    for (const file of files.slice(0, 100)) {
      const row = document.createElement("li"), name = document.createElement("span"), size = document.createElement("small");
      name.textContent = pathOf(file); size.textContent = interpolate(gettext("%(size)s octets"), {size: file.size.toLocaleString(document.documentElement.lang)}, true); row.append(name, size); list.append(row);
    }
    submit.disabled = !files.length || files.length > 100;
    if (files.length > 100) summary.textContent = gettext("Sélectionnez au maximum 100 fichiers.");
  };
  submit.disabled = true;
  fileInput.addEventListener("change", () => choose(fileInput.files));
  folderInput.addEventListener("change", () => choose(folderInput.files));
  pause.addEventListener("click", () => { paused = true; pause.disabled = true; phase.textContent = gettext("Pause après le bloc en cours…"); });
  form.addEventListener("dragover", e => { e.preventDefault(); if (!busy) form.classList.add("dragover"); });
  form.addEventListener("dragleave", () => form.classList.remove("dragover"));
  form.addEventListener("drop", e => {
    e.preventDefault(); form.classList.remove("dragover");
    if ([...e.dataTransfer.items].some(item => item.webkitGetAsEntry?.()?.isDirectory)) { summary.textContent = gettext("Utilisez « Choisir un dossier » pour conserver l’arborescence."); return; }
    choose(e.dataTransfer.files);
  });
  form.addEventListener("submit", async e => {
    e.preventDefault(); if (busy || !files.length) return;
    busy = true; paused = false; submit.disabled = fileInput.disabled = folderInput.disabled = true;
    pause.hidden = false; pause.disabled = false; shell.hidden = false; phase.textContent = gettext("Préparation de l’envoi…");
    try {
      await refresh();
      pending.hidden = true;
      const manifest = [];
      for (const file of files) {
        const sample = new Blob([String(file.size), ":", String(file.lastModified), ":", file.slice(0, 65536), file.slice(Math.max(0, file.size - 65536))]);
        manifest.push({path: pathOf(file), size: file.size, fingerprint: await digest(sample)});
      }
      let session = sessions.find(s => s.parts.length === manifest.length && s.parts.every(p => manifest.some(f => f.path === p.path && f.size === p.size && f.fingerprint === p.fingerprint)));
      if (!session) session = await api(form.dataset.startUrl, JSON.stringify({files: manifest}));
      const total = files.reduce((n, f) => n + f.size, 0);
      const update = () => {
        const value = Math.round(session.parts.reduce((n, p) => n + p.offset, 0) / Math.max(1, total) * 100);
        progress.value = value; percent.textContent = `${value} %`;
      };
      update();
      for (const part of session.parts) {
        const file = files.find(f => pathOf(f) === part.path);
        while (part.offset < part.size) {
          if (paused) throw new Error(gettext("Envoi en pause. Cliquez sur Importer pour reprendre pendant les prochaines 24 h."));
          phase.textContent = interpolate(gettext("Envoi de %(value1)s"), {value1: part.path}, true);
          const block = file.slice(part.offset, Math.min(part.size, part.offset + session.chunk_bytes));
          const checksum = await digest(block);
          const result = await api(`/uploads/${session.id}/parts/${part.id}/chunk/`, block, {
            "Content-Type": "application/octet-stream", "Upload-Offset": String(part.offset), "Upload-Checksum": checksum});
          part.offset = result.offset; update();
        }
      }
      if (paused) throw new Error(gettext("Envoi en pause avant vérification. Cliquez sur Importer pour terminer."));
      pause.hidden = true; phase.textContent = gettext("Analyse antivirus et enregistrement…");
      progress.value = 100; percent.textContent = "100 %";
      const result = await api(`/uploads/${session.id}/finish/`, "{}");
      window.location.assign(result.redirect);
    } catch (error) {
      summary.textContent = error instanceof TypeError ? gettext("Connexion interrompue. Vos blocs reçus sont conservés : cliquez sur Importer pour reprendre.") : error.message;
      phase.textContent = gettext("En attente");
      try { await refresh(); } catch { /* The connection can still be offline. */ }
      busy = false; submit.disabled = fileInput.disabled = folderInput.disabled = false; pause.hidden = true;
    }
  });
  refresh().catch(error => { summary.textContent = error.message; });
})();
