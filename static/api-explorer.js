"use strict";
(() => {
  const form = document.querySelector("#api-explorer");
  if (!form) return;
  const byId = id => document.getElementById(id);
  const select = byId("api-operation"), body = byId("api-body"), key = byId("api-key");
  const status = byId("api-status"), result = byId("api-result"), download = byId("api-export");
  let operations = [], objectUrl = null;
  const clear = () => {
    result.textContent = ""; status.textContent = ""; download.hidden = true;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  byId("api-clear").onclick = () => { key.value = ""; clear(); };
  byId("api-new").onclick = () => { byId("api-idempotency").value = crypto.randomUUID(); clear(); };
  window.addEventListener("pagehide", () => { key.value = ""; clear(); });
  const choose = () => {
    clear(); const operation = operations[select.selectedIndex];
    byId("api-description").textContent = operation.schema.description;
    const parameters = byId("api-parameters"); parameters.replaceChildren();
    for (const parameter of operation.schema.parameters.filter(p => p.in === "path" || p.in === "query")) {
      const label = document.createElement("label"), input = document.createElement("input");
      input.id = `api-param-${parameter.name}`; input.name = parameter.name;
      input.required = Boolean(parameter.required); input.dataset.location = parameter.in;
      label.htmlFor = input.id; label.textContent = parameter.name;
      if (parameter.schema.enum) {
        input.value = parameter.schema.enum[0];
        input.pattern = parameter.schema.enum.join("|");
      }
      if (parameter.schema.format === "uuid") input.pattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
      parameters.append(label, input);
    }
    body.value = JSON.stringify(operation.schema.requestBody?.content?.["application/json"]?.example || {}, null, 2);
    body.disabled = operation.method === "get";
    byId("api-write").hidden = operation.method === "get";
    byId("api-confirm").checked = false;
    byId("api-idempotency").value = crypto.randomUUID();
  };
  select.addEventListener("change", choose);
  fetch(form.dataset.specUrl, {credentials: "same-origin"}).then(r => {
    if (!r.ok) throw new Error(); return r.json();
  }).then(spec => {
    for (const [path, methods] of Object.entries(spec.paths)) for (const [method, schema] of Object.entries(methods)) {
      if (!['get', 'post'].includes(method) || schema.requestBody?.content?.["application/octet-stream"]) continue;
      operations.push({path, method, schema});
      const option = document.createElement("option"); option.textContent = `${method.toUpperCase()} ${path} — ${schema.summary}`;
      select.append(option);
    }
    choose();
  }).catch(() => { status.textContent = gettext("Impossible de charger la spécification API."); });
  form.addEventListener("submit", async event => {
    event.preventDefault(); clear();
    const operation = operations[select.selectedIndex];
    if (!operation) return;
    if (operation.method !== "get" && !byId("api-confirm").checked) {
      status.textContent = gettext("Confirmez cette action avant de l’exécuter."); return;
    }
    byId("api-execute").disabled = true;
    try {
      let path = operation.path; const query = new URLSearchParams();
      for (const input of byId("api-parameters").querySelectorAll("input")) {
        if (input.dataset.location === "path") path = path.replace(`{${input.name}}`, encodeURIComponent(input.value));
        else if (input.value) query.set(input.name, input.value);
      }
      const url = new URL(`/api/v1${path}`, location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith("/api/v1/")) throw new Error();
      url.search = query.toString();
      const headers = {"Authorization": `Bearer ${key.value.trim()}`};
      const options = {method: operation.method.toUpperCase(), credentials: "omit", redirect: "error", headers};
      if (operation.method !== "get") {
        options.body = JSON.stringify(JSON.parse(body.value)); headers["Content-Type"] = "application/json";
        headers["Idempotency-Key"] = byId("api-idempotency").value;
      }
      const response = await fetch(url, options);
      status.textContent = `HTTP ${response.status}`;
      if (response.headers.get("Content-Type")?.includes("application/json")) result.textContent = JSON.stringify(await response.json(), null, 2);
      else if (response.ok) {
        objectUrl = URL.createObjectURL(await response.blob()); download.href = objectUrl;
        download.download = path.includes("/xlsx/") ? "liasio-links.xlsx" : "liasio-links.csv"; download.hidden = false;
      } else result.textContent = await response.text();
    } catch { status.textContent = gettext("Requête impossible. Vérifiez le JSON et la connexion. Réutilisez la même clé d’idempotence en cas de doute."); }
    finally { byId("api-execute").disabled = false; }
  });
})();
