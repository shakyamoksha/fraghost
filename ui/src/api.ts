import type { Preview, Settings, Snapshot, Torrent, UpdateStatus } from "./types";

let password = "";

export function setPassword(value: string) {
  password = value;
}

export class AuthError extends Error {
  constructor() {
    super("Password required");
    this.name = "AuthError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (password) headers.set("X-Fraghost-Password", password);
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) throw new AuthError();
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data as T;
}

export const api = {
  state: (detail: string | null) =>
    request<Snapshot>(`/api/state${detail ? `?detail=${encodeURIComponent(detail)}` : ""}`),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (settings: Settings) => request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  logs: () => request<{ lines: string[] }>("/api/logs"),
  updateStatus: () => request<UpdateStatus>("/api/update"),
  checkUpdate: () => request<UpdateStatus>("/api/update?refresh=1"),
  applyUpdate: () => request<{ ok: boolean }>("/api/update", { method: "POST", body: "{}" }),
  preview: (source: string, value: string) =>
    request<Preview>("/api/preview", { method: "POST", body: JSON.stringify({ source, value }) }),
  add: (body: Record<string, unknown>) => request<Torrent>("/api/torrents", { method: "POST", body: JSON.stringify(body) }),
  action: (id: string, action: string) =>
    request<Torrent>(`/api/torrents/${encodeURIComponent(id)}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  update: (id: string, patch: Record<string, unknown>) =>
    request<Torrent>(`/api/torrents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  remove: (id: string, deleteFiles: boolean) =>
    request<{ ok: boolean }>(`/api/torrents/${encodeURIComponent(id)}?delete_files=${deleteFiles ? "1" : "0"}`, {
      method: "DELETE",
    }),
  open: (id: string) => request<{ path: string }>(`/api/torrents/${encodeURIComponent(id)}/open`, { method: "POST", body: "{}" }),
  create: (body: Record<string, unknown>) => request<Record<string, unknown>>("/api/create", { method: "POST", body: JSON.stringify(body) }),
  blocklist: (path: string) => request<{ ranges: number; path: string }>("/api/blocklist", { method: "POST", body: JSON.stringify({ path }) }),
  refreshRss: () => request<{ added: number }>("/api/rss/refresh", { method: "POST", body: "{}" }),
  exportTorrent: async (id: string, name: string) => {
    const headers = new Headers();
    if (password) headers.set("X-Fraghost-Password", password);
    const response = await fetch(`/api/torrents/${encodeURIComponent(id)}/export`, { headers });
    if (response.status === 401) throw new AuthError();
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Could not export the torrent");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name.endsWith(".torrent") ? name : `${name}.torrent`;
    link.click();
    URL.revokeObjectURL(url);
  },
};
