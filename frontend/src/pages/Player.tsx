import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { usePlayer } from "../lib/player";
import ModeSwitch, { type PlayerMode } from "../components/ModeSwitch";
import Run from "./Run";
import Listen from "./Listen";

/** The player page: Run and Listen behind one Run | Listen switch.
 *
 *  Both are the same engine (lib/player.tsx owns the queue, transport and tempo
 *  lock); they differ only in whether a cadence lock is on and in the controls
 *  drawn around it. The mode is the route, so `/run` and `/listen` stay valid deep
 *  links (the installed app's start page, Home's preset cards, bookmarks), and the
 *  switch is just navigation with the one piece of behaviour that matters:
 *
 *  - **Run → Listen** releases the tempo lock and keeps the queue playing, so the
 *    music carries on at native speed. Nothing is lost, so nothing is confirmed.
 *  - **Listen → Run** keeps the queue as it is; the cadence controls then let you
 *    lock it or build a fresh queue for a pace. (A dialog for that choice is the
 *    next step of the merge.)
 *
 *  The kiosk (named player accounts and the shared Guest login) is left alone: it
 *  has its own Run / Listen tabs in its nav, and its mode set is the admin's
 *  `player_listen_mode` setting, enforced in App.tsx's PlayerLayout. */
export default function Player({ mode }: { mode: PlayerMode }) {
  const navigate = useNavigate();
  const { role } = useAuth();
  const { tempoLock, setTempoLock } = usePlayer();
  const kiosk = role === "player";

  function switchTo(next: PlayerMode) {
    if (next === mode) return;
    if (next === "listen" && tempoLock) setTempoLock(null);
    navigate(next === "run" ? "/run" : "/listen");
  }

  const modeSwitch = kiosk
    ? undefined
    : (fill: boolean) => <ModeSwitch mode={mode} onChange={switchTo} fill={fill} />;

  return mode === "run" ? <Run modeSwitch={modeSwitch} /> : <Listen modeSwitch={modeSwitch} />;
}
