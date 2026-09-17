/**
 * Shared numeric formatting helpers for the MMS4 dashboard.
 *
 * Extracted per the UI/UX Compendium refactor (`.agents/specs/knowledge/
 * mms4-dashboard-ui-ux-standards.md`) — this file exists ONLY to remove the
 * 3-way duplicated "sign + $ + abs().toFixed(2)" pattern found in
 * MonitoringTab.jsx and AnalyticsTab.jsx. Deliberately not touching color
 * logic: callers still decide their own `>= 0` styling next to this string.
 */

/**
 * Format a signed USD amount as "+$12.34" / "-$12.34".
 * @param {number} value
 * @param {{ decimals?: number }} [options]
 * @returns {string}
 */
export function formatSignedUsd(value, { decimals = 2 } = {}) {
  const num = Number(value) || 0;
  const sign = num >= 0 ? '+' : '-';
  return `${sign}$${Math.abs(num).toFixed(decimals)}`;
}
