'use strict';
// A row is an array of displayed cell strings. Column filters combine with AND.
const WorkbookFilters = (() => {
  function matches(values, filters, query = '', ignoreColumn = -1) {
    const needle = query.toLowerCase();
    if (needle && !values.some(value => value.toLowerCase().includes(needle))) return false;
    for (const [column, selected] of filters) {
      if (column !== ignoreColumn && !selected.has(values[column])) return false;
    }
    return true;
  }
  function counts(rows, column, filters, query = '') {
    const counts = new Map();
    // Include unavailable values with a zero count so selections remain reversible.
    for (const row of rows) counts.set(row[column], 0);
    for (const row of rows) if (matches(row, filters, query, column)) counts.set(row[column], counts.get(row[column]) + 1);
    return counts;
  }
  function filtered(rows, filters, query = '') { return rows.filter(row => matches(row, filters, query)); }
  return {matches, counts, filtered};
})();
if (typeof module !== 'undefined') module.exports = WorkbookFilters;
