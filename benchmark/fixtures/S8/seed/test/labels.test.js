'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { makeLabel } = require('../src/labels');

test('plain titles', () => {
  assert.strictEqual(makeLabel('Hello World'), 'label:hello-world');
});

test('accents are folded, not dropped', () => {
  assert.strictEqual(makeLabel('Crème Brûlée'), 'label:creme-brulee');
});
