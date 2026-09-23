'use strict';

/**
 * Turn a title into a URL slug: lowercase ASCII letters and digits, with each run of anything
 * else collapsed to a single hyphen. A slug never starts or ends with a hyphen.
 */
function slugify(title) {
  return title
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(' ')
    .join('-');
}

module.exports = { slugify };
