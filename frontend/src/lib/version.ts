/** Semver comparison for the "is there a newer release?" check.
 *
 * The update banner used to compare the running version against GitHub's latest
 * release tag with `!==`, so *any* difference read as "update available" — a
 * build ahead of the newest published release (which is what every user saw
 * while the releases page sat nineteen versions behind Docker Hub, issue #5)
 * was reported as out of date. Compare numerically and only warn when the
 * remote is genuinely greater.
 */

/** Numeric release parts of a version string; [] if it isn't a version. */
function parts(v: string): number[] {
  const core = v.trim().replace(/^v/i, "").split(/[-+]/)[0];
  if (!/^\d+(\.\d+)*$/.test(core)) return [];
  return core.split(".").map(Number);
}

/** -1 / 0 / 1 for a < b, a === b, a > b. Returns null if either is unparseable. */
export function compareVersions(a: string, b: string): number | null {
  const pa = parts(a);
  const pb = parts(b);
  if (!pa.length || !pb.length) return null;
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

export type UpdateState = "up-to-date" | "update-available" | "ahead" | "unknown";

/** How `current` stands against the latest published release. */
export function updateState(current: string | undefined, latest: string | undefined): UpdateState {
  if (!current || !latest) return "unknown";
  const c = compareVersions(latest, current);
  if (c === null) return "unknown";
  if (c > 0) return "update-available";
  return c < 0 ? "ahead" : "up-to-date";
}
