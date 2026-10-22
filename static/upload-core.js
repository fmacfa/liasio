"use strict";
// Shared with the dependency-free Node tests. No file contents are retained here.
((root) => {
  const merge = (current, added) => {
    const result = [...current], paths = new Set(current.map(item => item.path));
    let duplicates = 0;
    for (const item of added) {
      const path = item.path.normalize("NFC");
      if (paths.has(path)) { duplicates++; continue; }
      if (result.length >= 100) throw new Error("limit");
      paths.add(path); result.push({...item, path});
    }
    return {files: result, duplicates};
  };
  const collect = async (entries) => {
    const files = []; let visited = 0, empty = 0, hidden = 0;
    const visit = async (entry, parent = "", depth = 0) => {
      if (++visited > 2000 || depth >= 20) throw new Error("limit");
      if (entry.name.startsWith(".")) { hidden++; return; }
      const path = parent + entry.name;
      if (entry.isFile) {
        if (files.length >= 100) throw new Error("limit");
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        files.push({file, path});
      } else if (entry.isDirectory) {
        const before = files.length, reader = entry.createReader();
        for (;;) {
          const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
          if (!batch.length) break;
          for (const child of batch) await visit(child, path + "/", depth + 1);
        }
        if (files.length === before) empty++;
      } else throw new Error("unsupported");
    };
    for (const entry of entries) await visit(entry);
    return {files, empty, hidden};
  };
  const progress = (parts, total, inFlight = 0) => {
    const confirmed = parts.reduce((sum, part) => sum + part.offset, 0);
    const sent = Math.min(total, confirmed + Math.max(0, inFlight));
    // Do not announce 100% while bytes are still in flight or unacknowledged.
    const percent = confirmed >= total ? 100 : Math.min(99, Math.floor(sent / Math.max(1, total) * 100));
    return {confirmed, sent, percent};
  };
  const validOffset = (value, size) => Number.isSafeInteger(value) && value >= 0 && value <= size;
  const api = {merge, collect, progress, validOffset};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LinkVaultUpload = api;
})(globalThis);
