interface DesktopApi {
  pick_torrents: () => Promise<string[] | null>;
  pick_folder: () => Promise<string | null>;
  pick_save: (name: string) => Promise<string | null>;
  window_minimize: () => Promise<void>;
  window_toggle_maximize: () => Promise<void>;
  window_toggle_fullscreen: () => Promise<void>;
  window_hide: () => Promise<void>;
  window_resize: (edge: string, dx: number, dy: number) => Promise<void>;
  window_drag: (screenX: number, screenY: number, ratioX: number, grabY: number) => Promise<void>;
  notify_update: (title: string, body: string) => Promise<void>;
}

function bridge(): DesktopApi | null {
  const host = window as unknown as { pywebview?: { api?: DesktopApi } };
  return host.pywebview?.api ?? null;
}

export function whenDesktopReady(): Promise<void> {
  if (bridge()) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 700);
    window.addEventListener(
      "pywebviewready",
      () => {
        window.clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export async function pickTorrents(): Promise<string[] | null> {
  const api = bridge();
  if (!api) return null;
  return (await api.pick_torrents()) ?? [];
}

export async function pickFolder(): Promise<string | null> {
  const api = bridge();
  if (!api) return null;
  const chosen = await api.pick_folder();
  return chosen || "";
}

export async function pickSave(name: string): Promise<string | null> {
  const api = bridge();
  if (!api) return null;
  const chosen = await api.pick_save(name);
  return chosen || "";
}

export function hasDesktop(): boolean {
  return Boolean(bridge());
}

export function windowMinimize() {
  void bridge()?.window_minimize();
}

export function windowToggleMaximize() {
  void bridge()?.window_toggle_maximize();
}

export function windowToggleFullscreen() {
  void bridge()?.window_toggle_fullscreen();
}

export function windowHide() {
  void bridge()?.window_hide();
}

export function windowResize(edge: string, dx: number, dy: number) {
  return bridge()?.window_resize(edge, dx, dy) ?? Promise.resolve();
}

export function windowDrag(screenX: number, screenY: number, ratioX: number, grabY: number) {
  return bridge()?.window_drag(screenX, screenY, ratioX, grabY) ?? Promise.resolve();
}

export function notifyUpdate(title: string, body: string) {
  void bridge()?.notify_update(title, body);
}
