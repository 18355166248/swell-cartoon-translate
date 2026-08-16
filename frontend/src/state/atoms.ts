/** Global state.
 *
 * Two problems this solves:
 *
 * 1. **Tab switching.** Previously the tabs stayed mounted only because of
 *    `forceMount`, which keeps a hidden DOM tree alive purely so component
 *    state survives. Holding the state outside the components means they can
 *    unmount freely and still come back to the same place.
 *
 * 2. **Page refresh.** The job itself lives on the backend, so a reload should
 *    be able to re-attach rather than lose the run. The pieces needed to find
 *    it again -- the job id, the project path, the form the user filled in --
 *    are persisted; everything derived from the backend is not.
 */

import { atom } from "jotai";
import { atomWithStorage } from "jotai/utils";
import type { Job, PreviewResponse } from "@/lib/api";

/**
 * `atomWithStorage` with `getOnInit`.
 *
 * Without it jotai returns the *default* on the first read and only syncs
 * from storage afterwards. That one render is enough to do damage: the folder
 * picker mounts with an empty path, falls back to listing the home directory,
 * and writes that back over the path you had saved. Reading storage during
 * initialisation is the only way the first render is already correct.
 */
function persisted<T>(name: string, initial: T) {
  return atomWithStorage<T>(`ctt.${name}`, initial, undefined, { getOnInit: true });
}

// ---------------------------------------------------------------- 持久化 ---
// Only what cannot be recovered from the backend. Job progress is deliberately
// absent: re-fetching it is both cheap and correct, whereas a cached copy goes
// stale the moment the run advances.

export const inputDirAtom = persisted("inputDir", "");
export const outputDirAtom = persisted("outputDir", "");
export const outputEditedAtom = persisted("outputEdited", false);

/** Empty means "translate everything" -- the sane default for a chapter. */
export const limitAtom = persisted("limit", "");

/** Recursion is on by default: pointing at a series folder and having it
 *  translate every chapter is the common case. */
export const recursiveAtom = persisted("recursive", true);

/** Lets a reload re-attach to a run that is still going. */
export const activeJobIdAtom = persisted("activeJobId", "");

/** So the results tab can reopen the last project after a refresh. */
export const projectPathAtom = persisted("projectPath", "");

export const activeTabAtom = persisted("activeTab", "run");

// ------------------------------------------------------------- 会话内状态 ---
// Survives tab switches, not reloads.

export const jobAtom = atom<Job | null>(null);
export const previewAtom = atom<PreviewResponse | null>(null);
export const backendOnlineAtom = atom<boolean | null>(null);

/** Which results page is showing, and a nonce that forces the rendered image
 *  to be re-fetched after an edit. */
export const pageIndexAtom = atom(0);
export const renderNonceAtom = atom(0);

export const jobRunningAtom = atom((get) => {
  const job = get(jobAtom);
  return job?.status === "running" || job?.status === "pending";
});
