"use strict";
(() => {
  const form = document.querySelector("[data-branding-form]");
  if (!form) return;
  const preview = document.querySelector("[data-brand-preview]"), status = form.querySelector("[data-brand-status]");
  const logo = preview.querySelector("[data-preview-logo]");
  const initialLogo = logo.getAttribute("src"), initialImage = getComputedStyle(preview).getPropertyValue("--brand-image");
  const images = {}, revisions = {}; let pending = 0;
  const contrast = color => {
    const values = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
    return values.reduce((n, c, i) => n + c * [.2126, .7152, .0722][i], 0) > .179 ? "#000000" : "#ffffff";
  };
  const update = () => {
    const name = form.elements.name.value || gettext("Mon espace");
    preview.querySelector("[data-preview-name]").textContent = name;
    preview.querySelector("[data-preview-monogram]").textContent = name.slice(0, 1).toUpperCase();
    preview.querySelector("[data-preview-tagline]").textContent = form.elements.tagline.value;
    preview.querySelector("[data-preview-footer]").textContent = form.elements.footer.value;
    const theme = form.elements.theme.value;
    for (const value of ["forest", "ocean", "plum", "ink"]) preview.classList.toggle(`theme-${value}`, value === theme);
    const accent = form.elements.accent_color.value, background = form.elements.background_color.value;
    const themes = {forest: "#245e48", ocean: "#285b73", plum: "#72506c", ink: "#3d4949"};
    const color = /^#[0-9a-f]{6}$/i.test(accent) ? accent : themes[theme];
    preview.style.setProperty("--accent", color); preview.style.setProperty("--accent-contrast", contrast(color));
    preview.style.setProperty("--brand-background", /^#[0-9a-f]{6}$/i.test(background) ? background : "#f6f8f1");
    const source = form.elements.remove_logo.checked ? null : images.logo || initialLogo;
    logo.hidden = !source; if (source) logo.src = source; else logo.removeAttribute("src");
    preview.querySelector("[data-preview-monogram]").hidden = !!source;
    preview.style.setProperty("--brand-image", form.elements.remove_background.checked ? "none" : images.background ? `url("${images.background}")` : initialImage || "none");
  };
  const asDataURL = blob => new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob);
  });
  form.querySelectorAll("[data-brand-image]").forEach(input => input.addEventListener("change", async () => {
    const kind = input.dataset.brandImage, file = input.files[0], revision = (revisions[kind] || 0) + 1;
    revisions[kind] = revision;
    if (!file) { input.setCustomValidity(""); delete images[kind]; update(); return; }
    pending++; form.querySelector("button").disabled = true;
    input.setCustomValidity(""); status.textContent = gettext("Optimisation de l’image…");
    let bitmap;
    try {
      if (file.size > 10 * 1024 ** 2 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("format");
      bitmap = await createImageBitmap(file);
      const edge = kind === "logo" ? 512 : 1600, max = kind === "logo" ? 512 * 1024 : 2 * 1024 ** 2;
      let ratio = Math.min(1, edge / Math.max(bitmap.width, bitmap.height)), blob;
      const canvas = document.createElement("canvas");
      for (let attempt = 0; attempt < 5; attempt++) {
        canvas.width = Math.max(1, Math.round(bitmap.width * ratio)); canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
        if (blob && blob.size <= max) break;
        ratio *= .75;
      }
      if (!blob || blob.size > max) throw new Error("size");
      const dataURL = await asDataURL(blob);
      if (revisions[kind] !== revision) return;
      const transfer = new DataTransfer(); transfer.items.add(new File([blob], `${kind}.png`, {type: "image/png"}));
      input.files = transfer.files; images[kind] = dataURL;
      form.elements[`remove_${kind}`].checked = false;
      status.textContent = gettext("Image prête. Enregistrez pour appliquer les changements."); update();
    } catch {
      if (revisions[kind] === revision) {
        input.value = "";
        status.textContent = gettext("Image non prise en charge. Choisissez un PNG, JPEG ou WebP de moins de 10 Mio.");
        input.setCustomValidity(status.textContent); input.reportValidity();
      }
    } finally {
      bitmap?.close(); pending--; form.querySelector("button").disabled = pending > 0;
    }
  }));
  form.querySelectorAll("[data-color-picker]").forEach(picker => {
    picker.addEventListener("input", () => {
      form.elements[picker.dataset.colorPicker].value = picker.value; update();
    });
    form.elements[picker.dataset.colorPicker].addEventListener("input", event => {
      if (/^#[0-9a-f]{6}$/i.test(event.target.value)) picker.value = event.target.value;
    });
  });
  form.addEventListener("input", update);
  form.addEventListener("change", event => {
    for (const kind of ["logo", "background"]) {
      if (event.target === form.elements[`remove_${kind}`] && event.target.checked) form.elements[kind].setCustomValidity("");
    }
    update();
  });
  form.addEventListener("submit", event => { if (pending) event.preventDefault(); });
  update();
})();
