"use strict";

const toast = (message) => {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
};

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      toast("Lien copié. Prêt à être partagé.");
    } catch {
      // A selectable value remains available without Clipboard API permission.
      const existing = button.parentElement.querySelector(".copy-fallback");
      if (existing) { existing.focus(); existing.select(); return; }
      const field = document.createElement("input");
      field.className = "copy-fallback";
      field.value = button.dataset.copy;
      field.readOnly = true;
      field.setAttribute("aria-label", "Lien à copier");
      button.after(field);
      field.focus(); field.select();
      toast("Copiez le lien sélectionné avec votre clavier.");
    }
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const brandForm = document.querySelector("[data-branding-form]");
if (brandForm) {
  const preview = document.querySelector("[data-brand-preview]");
  const update = () => {
    const name = brandForm.elements.name.value || "Mon espace";
    preview.querySelector("[data-preview-name]").textContent = name;
    preview.querySelector("[data-preview-monogram]").textContent = name.slice(0, 1).toUpperCase();
    preview.querySelector("[data-preview-tagline]").textContent = brandForm.elements.tagline.value;
    preview.querySelector("[data-preview-footer]").textContent = brandForm.elements.footer.value;
    const theme = brandForm.elements.theme.value;
    if (["forest", "ocean", "plum", "ink"].includes(theme)) preview.className = `share-preview theme-${theme}`;
  };
  brandForm.addEventListener("input", update);
  brandForm.addEventListener("change", update);
}

const uploadForm = document.querySelector("[data-upload-form]");
if (uploadForm) {
  let selected = [];
  let busy = false;
  const fileInput = uploadForm.querySelector("[data-file-input]");
  const folderInput = uploadForm.querySelector("[data-folder-input]");
  const summary = uploadForm.querySelector(".upload-selection");
  const submit = uploadForm.querySelector(".upload-submit");
  const progress = uploadForm.querySelector("progress");
  const progressShell = uploadForm.querySelector(".progress-shell");
  const phase = uploadForm.querySelector("[data-upload-phase]");
  const percent = uploadForm.querySelector("[data-upload-percent]");
  const selectedList = uploadForm.querySelector(".selected-files");
  const choose = (files) => {
    selected = Array.from(files);
    const bytes = selected.reduce((total, file) => total + file.size, 0);
    const readableSize = bytes < 1024 * 1024 ? `${bytes.toLocaleString("fr-FR")} octets` : `${(bytes / 1024 / 1024).toLocaleString("fr-FR", {maximumFractionDigits: 1})} Mio`;
    summary.textContent = `${selected.length} fichier(s) sélectionné(s) · ${readableSize}`;
    if (selectedList) {
      selectedList.replaceChildren();
      selected.slice(0, 100).forEach((file) => {
        const row = document.createElement("li");
        const name = document.createElement("span");
        name.textContent = file.webkitRelativePath || file.name;
        const size = document.createElement("small");
        size.textContent = `${file.size.toLocaleString("fr-FR")} octets`;
        row.append(name, size); selectedList.append(row);
      });
    }
    submit.disabled = !selected.length;
  };
  submit.disabled = true;
  fileInput.addEventListener("change", () => choose(fileInput.files));
  folderInput.addEventListener("change", () => choose(folderInput.files));
  uploadForm.addEventListener("dragover", (event) => { event.preventDefault(); if (!busy) uploadForm.classList.add("dragover"); });
  uploadForm.addEventListener("dragleave", () => uploadForm.classList.remove("dragover"));
  uploadForm.addEventListener("drop", (event) => {
    event.preventDefault(); uploadForm.classList.remove("dragover");
    if (busy) return;
    const entries = Array.from(event.dataTransfer.items).map((item) => item.webkitGetAsEntry?.());
    if (entries.some((entry) => entry?.isDirectory)) {
      toast("Pour conserver un dossier complet, utilisez « Choisir un dossier ».");
      return;
    }
    choose(event.dataTransfer.files);
  });
  uploadForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!selected.length || busy) return;
    const data = new FormData();
    const csrf = uploadForm.querySelector("[name=csrfmiddlewaretoken]").value;
    selected.forEach((file) => data.append("files", file, file.name));
    data.append("paths", JSON.stringify(selected.map((file) => file.webkitRelativePath || file.name)));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", uploadForm.action);
    xhr.setRequestHeader("X-CSRFToken", csrf);
    busy = true;
    submit.disabled = true;
    fileInput.disabled = folderInput.disabled = true;
    progress.value = 0;
    if (percent) percent.textContent = "0 %";
    if (phase) phase.textContent = "Envoi des fichiers";
    progress.hidden = false;
    if (progressShell) progressShell.hidden = false;
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) progress.value = Math.round(event.loaded / event.total * 100);
      if (percent) percent.textContent = `${progress.value} %`;
      if (phase && progress.value >= 100) phase.textContent = "Vérification et enregistrement…";
      if (progress.value >= 100) summary.textContent = "Envoi reçu. Vérification et enregistrement des fichiers…";
    });
    const recover = (message) => {
      busy = false; submit.disabled = false; fileInput.disabled = folderInput.disabled = false;
      progress.hidden = true;
      if (progressShell) progressShell.hidden = true;
      summary.textContent = message;
    };
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 400) {
        // The server redirects to the collection and includes escaped flash messages.
        // Replace only with same-origin server-rendered HTML; never uploaded content.
        const doc = new DOMParser().parseFromString(xhr.responseText, "text/html");
        const serverMessages = doc.querySelector(".messages");
        if (serverMessages) sessionStorage.setItem("uploadMessages", serverMessages.textContent);
        window.location.assign(xhr.responseURL || window.location.href);
      } else recover(`Envoi refusé (${xhr.status}). Vérifiez la taille des fichiers ou reconnectez-vous.`);
    });
    xhr.addEventListener("error", () => recover("Connexion interrompue. Consultez le dossier avant de relancer l’envoi."));
    xhr.send(data);
  });
}
const uploadMessage = sessionStorage.getItem("uploadMessages");
if (uploadMessage) { sessionStorage.removeItem("uploadMessages"); toast(uploadMessage.trim()); }

const storageForm = document.querySelector("[data-storage-form]");
if (storageForm) {
  const all = storageForm.querySelector("[data-select-all]");
  const boxes = [...storageForm.querySelectorAll("[name=selected]")];
  const summary = storageForm.querySelector("[data-selection-summary]");
  const button = storageForm.querySelector("[data-delete-selection]");
  const update = () => {
    const chosen = boxes.filter((box) => box.checked);
    const total = chosen.reduce((value, box) => value + Number(box.dataset.size), 0);
    if (summary) summary.textContent = chosen.length ? `${chosen.length} fichier(s) sélectionné(s) · ${total.toLocaleString("fr-FR")} octets à libérer` : "Sélectionnez les fichiers à supprimer.";
    if (button) button.disabled = !chosen.length;
    all.checked = boxes.length > 0 && chosen.length === boxes.length;
    all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
  };
  all.addEventListener("change", () => { boxes.forEach((box) => { box.checked = all.checked; }); update(); });
  boxes.forEach((box) => box.addEventListener("change", update));
  update();
}
