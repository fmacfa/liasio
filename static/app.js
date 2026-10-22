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
      toast(gettext("Lien copié. Prêt à être partagé."));
    } catch {
      // A selectable value remains available without Clipboard API permission.
      const existing = button.parentElement.querySelector(".copy-fallback");
      if (existing) { existing.focus(); existing.select(); return; }
      const field = document.createElement("input");
      field.className = "copy-fallback";
      field.value = button.dataset.copy;
      field.readOnly = true;
      field.setAttribute("aria-label", gettext("Lien à copier"));
      button.after(field);
      field.focus(); field.select();
      toast(gettext("Copiez le lien sélectionné avec votre clavier."));
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
    const name = brandForm.elements.name.value || gettext("Mon espace");
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

const storageForm = document.querySelector("[data-storage-form]");
if (storageForm) {
  const all = storageForm.querySelector("[data-select-all]");
  const boxes = [...storageForm.querySelectorAll("[name=selected]")];
  const summary = storageForm.querySelector("[data-selection-summary]");
  const button = storageForm.querySelector("[data-delete-selection]");
  const update = () => {
    const chosen = boxes.filter((box) => box.checked);
    const total = chosen.reduce((value, box) => value + Number(box.dataset.size), 0);
    if (summary) summary.textContent = chosen.length ? interpolate(gettext("%(value1)s fichier(s) sélectionné(s) · %(value2)s octets vers la corbeille"), {value1: chosen.length, value2: total.toLocaleString(document.documentElement.lang)}, true) : gettext("Sélectionnez les fichiers à supprimer.");
    if (button) button.disabled = !chosen.length;
    all.checked = boxes.length > 0 && chosen.length === boxes.length;
    all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
  };
  all.addEventListener("change", () => { boxes.forEach((box) => { box.checked = all.checked; }); update(); });
  boxes.forEach((box) => box.addEventListener("change", update));
  update();
}
