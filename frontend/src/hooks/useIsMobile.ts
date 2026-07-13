import { useEffect, useState } from "react";

/** True below the given viewport width; tracks live resizes via matchMedia. */
export function useIsMobile(maxWidth = 700): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia(`(max-width: ${maxWidth}px)`).matches);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const onChange = (e: MediaQueryListEvent) => setMobile(e.matches);
    mq.addEventListener("change", onChange);
    setMobile(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, [maxWidth]);
  return mobile;
}
