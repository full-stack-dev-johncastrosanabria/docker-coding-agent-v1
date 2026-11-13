'use strict';

// Hand-rolled slug: it drops accented letters instead of folding them ("Crème" -> "cr-me").
function slug(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function makeLabel(title) {
  return `label:${slug(title)}`;
}

module.exports = { makeLabel };
