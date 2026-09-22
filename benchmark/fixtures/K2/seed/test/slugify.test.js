'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { slugify } = require('../src/slugify');

test('lowercases and joins words', () => {
  assert.equal(slugify('Hello World'), 'hello-world');
});

test('drops punctuation without a trailing hyphen', () => {
  assert.equal(slugify('Hello, World!'), 'hello-world');
});

test('collapses runs of separators', () => {
  assert.equal(slugify('a  --  b'), 'a-b');
});
