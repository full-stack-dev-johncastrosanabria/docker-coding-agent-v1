'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { slugify } = require('../src/slugify');

test('hidden: no leading hyphen', () => {
  assert.equal(slugify('--Leading dashes'), 'leading-dashes');
  assert.equal(slugify('¡Hola!'), 'hola');
});

test('hidden: digits are kept', () => {
  assert.equal(slugify('Top 10 Tips for 2024'), 'top-10-tips-for-2024');
});

test('hidden: nothing sluggable gives an empty slug', () => {
  assert.equal(slugify('!!!'), '');
  assert.equal(slugify('   '), '');
});

test('hidden: already a slug is unchanged', () => {
  assert.equal(slugify('already-a-slug'), 'already-a-slug');
});
