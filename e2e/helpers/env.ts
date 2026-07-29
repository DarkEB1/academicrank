/**
 * Where the suite points.
 *
 * IMPORTANT — why 127.0.0.1 and not `localhost`:
 * on Windows, `localhost` resolves to ::1 first. The docker-compose `web`
 * service publishes 5173 on 0.0.0.0 (IPv4 only), so if anything else is
 * listening on ::1:5173 — a leftover `vite dev` in web/, for instance — then
 * `http://localhost:5173` silently hits THAT instead of the containerised
 * nginx build, and the acceptance gate ends up testing a different artefact.
 * 127.0.0.1 is unambiguous. Override with PROVENANCE_WEB / PROVENANCE_API to
 * point elsewhere (e.g. PROVENANCE_WEB=http://localhost:5173 to test a dev
 * server on purpose).
 */
export const WEB_ORIGIN = process.env.PROVENANCE_WEB ?? 'http://127.0.0.1:5173';
export const API_ORIGIN = process.env.PROVENANCE_API_ORIGIN ?? 'http://127.0.0.1:8000';
export const API_BASE = process.env.PROVENANCE_API ?? `${API_ORIGIN}/api`;
