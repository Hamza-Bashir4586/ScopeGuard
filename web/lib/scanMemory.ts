// Remembers the current scan_id in localStorage so a refresh resumes.

const KEY = "scopeguard.currentScanId";

export function getRememberedScanId(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function rememberScanId(scanId: string | null) {
  try {
    if (scanId) window.localStorage.setItem(KEY, scanId);
    else window.localStorage.removeItem(KEY);
  } catch {
    // storage unavailable — URL still carries the scan id
  }
}

export function findingHref(scanId: string, findingId: string): string {
  return `/finding/?scan=${encodeURIComponent(scanId)}&id=${encodeURIComponent(findingId)}`;
}
