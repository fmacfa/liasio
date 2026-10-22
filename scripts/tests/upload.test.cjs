const {test} = require('node:test');
const assert = require('node:assert/strict');
const {collect, merge, progress, validOffset} = require('../../static/upload-core.js');
const file = name => ({name, isFile: true, file: resolve => resolve({name, size: 10})});
const folder = (name, batches) => ({name, isDirectory: true, createReader: () => {let index = 0; return {readEntries: resolve => resolve(batches[index++] || [])};}});

test('mixed drops retain root files and all nested readEntries batches in order', async () => {
  const selection = await collect([file('Z.txt'), folder('Project', [[file('B.txt')], [folder('Plans', [[file('A.txt')]])]])]);
  assert.deepEqual(selection.files.map(item => item.path), ['Z.txt', 'Project/B.txt', 'Project/Plans/A.txt']);
});
test('later selections append, NFC duplicates never replace existing files', () => {
  const first = {path: 'café.txt', file: {size: 10}};
  const result = merge([first], [{path: 'cafe\u0301.txt', file: {size: 99}}, {path: 'Folder/A.txt', file: {size: 5}}]);
  assert.equal(result.duplicates, 1);
  assert.deepEqual(result.files.map(item => item.path), ['café.txt', 'Folder/A.txt']);
  assert.equal(result.files[0].file.size, 10);
});
test('empty/hidden directories are reported and traversal is bounded', async () => {
  const result = await collect([folder('Empty', [[]]), file('.DS_Store')]);
  assert.equal(result.files.length, 0); assert.equal(result.empty, 1); assert.equal(result.hidden, 1);
  await assert.rejects(collect(Array.from({length: 101}, (_, i) => file(`${i}.txt`))), /limit/);
  let deep = file('a.txt'); for (let i = 0; i < 21; i++) deep = folder(`d${i}`, [[deep]]);
  await assert.rejects(collect([deep]), /limit/);
  assert.throws(() => merge(Array.from({length: 100}, (_, i) => ({path: `${i}.txt`})), [{path: 'new.txt'}]), /limit/);
});
test('real in-flight bytes advance the meter, only confirmed bytes complete it', () => {
  assert.deepEqual(progress([{offset: 40}], 100, 20), {confirmed: 40, sent: 60, percent: 60});
  assert.equal(progress([{offset: 40}], 100, 60).percent, 99);
  assert.equal(progress([{offset: 100}], 100).percent, 100);
  assert.equal(progress([], 0).percent, 100);
  assert.equal(progress([{offset: 4}], 100, 1000).sent, 100);
});
test('resumed offsets cannot exceed declared size or become fractional/negative', () => {
  for (const value of [-1, 11, '5', 1.5, NaN, Infinity]) assert.equal(validOffset(value, 10), false);
  assert.equal(validOffset(0, 10), true); assert.equal(validOffset(10, 10), true);
});
