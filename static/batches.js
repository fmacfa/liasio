"use strict";

const batchSource = document.querySelector("[data-batch-source]");
if (batchSource) {
  const mode = batchSource.elements.mode;
  const update = () => {
    for (const [name, activeMode] of [["count", "count"], ["source", "file"]]) {
      const field = batchSource.elements[name];
      field.closest(".field").hidden = mode.value !== activeMode;
      field.disabled = mode.value !== activeMode;
      field.required = mode.value === activeMode;
    }
  };
  mode.addEventListener("change", update);
  update();
}

const batchReview = document.querySelector("[data-batch-review]");
if (batchReview) {
  const mapping = batchReview.querySelector("[data-map-column]");
  if (mapping) {
    const source = JSON.parse(document.getElementById("batch-source-rows").textContent);
    mapping.addEventListener("click", (event) => {
      event.preventDefault();
      const column = Number(batchReview.elements.label_column.value);
      batchReview.querySelectorAll("[data-batch-label]").forEach((field) => {
        const value = source[Number(field.dataset.batchLabel)][column].trim();
        field.value = Array.from(value).slice(0, 80).join("");
      });
      toast(gettext("Noms mis à jour. Vérifiez les lignes avant de continuer."));
    });
  }
  batchReview.addEventListener("submit", (event) => {
    if (event.submitter?.value === "create" && batchReview.reportValidity()) {
      // Leave the successful action control enabled so its value is submitted.
      if (batchReview.dataset.submitting) { event.preventDefault(); return; }
      batchReview.dataset.submitting = "true";
      event.submitter.setAttribute("aria-disabled", "true");
      event.submitter.textContent = gettext("Création des liens…");
    }
  });
}
