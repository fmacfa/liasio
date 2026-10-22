"use strict";

// One POST creates the account. Credentials stay in this form, never in browser storage.
const setupForm = document.querySelector("[data-setup-form]");
if (setupForm) {
  const panels = [...setupForm.querySelectorAll("[data-setup-panel]")];
  const progress = document.querySelector("[data-setup-progress]");
  const next = setupForm.querySelector("[data-setup-next]");
  const back = setupForm.querySelector("[data-setup-back]");
  let step = 0;

  const showStep = (index, focus = true) => {
    step = index;
    panels.forEach((panel, position) => { panel.hidden = position !== step; });
    [...progress.children].forEach((item, position) => {
      if (position === step) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    if (focus) panels[step].querySelector("h2").focus();
  };
  const validate = (panel) => {
    const invalid = [...panel.querySelectorAll("input")].find((field) => !field.validity.valid);
    if (!invalid) return true;
    showStep(panels.indexOf(panel), false);
    invalid.reportValidity();
    return false;
  };
  const advance = () => { if (validate(panels[0])) showStep(1); };

  // Native validation cannot focus a required field in a hidden panel. Validate
  // each visible panel explicitly; without JS the complete native form works.
  setupForm.noValidate = true;
  progress.hidden = next.hidden = back.hidden = false;
  next.addEventListener("click", advance);
  back.addEventListener("click", () => showStep(0));
  setupForm.addEventListener("submit", (event) => {
    if (step === 0) {
      event.preventDefault();
      advance();
    } else if (!panels.every(validate)) {
      event.preventDefault();
    }
  });
  // Password widgets intentionally render empty after a server validation error.
  // Always return to credentials so they can be re-entered before submission.
  showStep(0, false);
}
