// Path helpers. The library normally stores POSIX ('/'-separated) paths, but a
// Windows dev library can yield '\'-separated paths, so split on both — mirrors
// the os.path.basename behaviour the Jinja templates relied on.
const SEP = /[/\\]/;

export function basename(p: string): string {
  const parts = p.split(SEP);
  return parts[parts.length - 1] || p;
}

export function parentName(p: string): string {
  const a = p.split(SEP);
  return a.length >= 2 ? a[a.length - 2] : "";
}

export function dirname(p: string): string {
  const a = p.split(SEP);
  a.pop();
  return a.join("/");
}
