"use strict";

(() => {
  const gallery = document.querySelector("[data-file-gallery]");
  const dialog = document.querySelector("[data-preview-dialog]");
  if (!gallery || !dialog || !dialog.showModal) return;
  const links = [...gallery.querySelectorAll("[data-preview]")];
  let large = dialog.querySelector("[data-preview-image]");
  const message = dialog.querySelector("[data-preview-message]");
  const previous = dialog.querySelector("[data-preview-prev]");
  const next = dialog.querySelector("[data-preview-next]");
  let index = 0, opener = null, revision = 0;
  const queue = [], enqueued = new WeakSet();
  let draining = false;

  // A queue avoids starting dozens of native decoders from a single page.
  const load = (image, url) => new Promise(resolve => {
    const timer = setTimeout(() => { image.removeAttribute("src"); finish(false); }, 15000);
    const finish = success => { clearTimeout(timer); image.onload = image.onerror = null; resolve(success); };
    image.onload = () => finish(true);
    image.onerror = () => finish(false);
    image.src = url;
  });
  const drain = async () => {
    if (draining) return;
    draining = true;
    while (queue.length) {
      const item = queue.shift();
      const success = await load(item.image, item.url);
      if (!success && item.attempt < 2) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        queue.push({...item, attempt: item.attempt + 1});
        continue;
      }
      item.done(success);
    }
    draining = false;
  };
  const enqueue = (item, priority = false) => {
    if (priority) queue.unshift(item); else queue.push(item);
    drain();
  };
  const thumbnail = image => {
    if (enqueued.has(image)) return;
    enqueued.add(image);
    const tile = image.closest("[data-preview]");
    tile.querySelector("[data-preview-state]").textContent = gettext("Préparation de l’aperçu…");
    enqueue({image, url: image.dataset.previewSrc, attempt: 0, done: success => {
      image.hidden = !success;
      tile.classList.toggle("preview-ready", success);
      tile.querySelector("[data-preview-state]").textContent = success ? "" : gettext("Aperçu indisponible");
    }});
  };
  if ("IntersectionObserver" in window) {
    // Observe the visible tile: its image starts hidden before it loads.
    const tiles = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        tiles.unobserve(entry.target); thumbnail(entry.target.querySelector("img"));
      }
    }, {rootMargin: "200px"});
    links.forEach(tile => tiles.observe(tile));
  } else {
    gallery.querySelectorAll("[data-preview-src]").forEach(thumbnail);
  }

  const show = selected => {
    for (let i = queue.length - 1; i >= 0; i--) if (queue[i].viewer) queue.splice(i, 1);
    index = selected;
    const link = links[index], ticket = ++revision;
    dialog.querySelector("#preview-title").textContent = link.dataset.name;
    dialog.querySelector("#preview-detail").textContent = link.dataset.detail;
    dialog.querySelector("[data-preview-position]").textContent = `${index + 1} / ${links.length}`;
    const focused = document.activeElement;
    previous.disabled = index === 0; next.disabled = index === links.length - 1;
    // Keep keyboard events inside the dialog when a navigation button disables.
    if (focused === next && next.disabled) previous.focus();
    if (focused === previous && previous.disabled) next.focus();
    large.hidden = true; message.hidden = false; message.textContent = gettext("Chargement de l’aperçu…");
    // A fresh authorized request also applies revocation to gallery navigation.
    const pendingImage = new Image();
    enqueue({image: pendingImage, url: link.href, attempt: 0, viewer: true, done: success => {
      if (ticket !== revision || !dialog.open) return;
      if (success) {
        large.replaceWith(pendingImage);
        large = pendingImage;
        pendingImage.dataset.previewImage = "";
        pendingImage.alt = interpolate(gettext("Aperçu de %(value1)s"), {value1: link.dataset.name}, true);
        pendingImage.hidden = false;
        message.hidden = true;
      } else message.textContent = gettext("Cet aperçu n’est pas disponible. Fermez cette fenêtre pour retrouver le téléchargement du dossier.");
    }}, true);
  };
  links.forEach((link, selected) => link.addEventListener("click", event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); opener = link;
    dialog.showModal(); document.body.classList.add("preview-open"); show(selected);
  }));
  const close = () => dialog.close();
  dialog.querySelector("[data-preview-close]").addEventListener("click", close);
  dialog.addEventListener("click", event => { if (event.target === dialog) close(); });
  dialog.addEventListener("close", () => {
    revision++; document.body.classList.remove("preview-open");
    dialog.querySelector("[data-preview-image]").removeAttribute("src");
    opener?.focus();
  });
  previous.addEventListener("click", () => { if (index > 0) show(index - 1); });
  next.addEventListener("click", () => { if (index < links.length - 1) show(index + 1); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "ArrowLeft" && index > 0) { event.preventDefault(); show(index - 1); }
    if (event.key === "ArrowRight" && index < links.length - 1) { event.preventDefault(); show(index + 1); }
  });
})();
