import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { api, AuthError, setPassword } from "./api";
import { hasDesktop, notifyUpdate, pickFolder, pickSave, pickTorrents, whenDesktopReady, windowDrag, windowHide, windowMinimize, windowResize, windowToggleFullscreen, windowToggleMaximize } from "./desktop";
import { Glyph, type GlyphName } from "./icons";
import { formatBytes, formatEta, formatPercent, formatRatio, formatSpeed, stateLabel, swarm, toBase64 } from "./format";
import { Mark } from "./Mark";
import type { Preview, Settings, Snapshot, SortKey, Torrent, TorrentFile, UpdateStatus } from "./types";
import "./styles.css";

type FileDialog = {
  kind: "files";
  name: string;
  size: number;
  infoHash: string;
  files: TorrentFile[];
  savePath: string;
  label: string;
  sequential: boolean;
  fetching: boolean;
  torrentId: string | null;
  source: string;
  value: string;
};

type Dialog =
  | { kind: "link"; value: string }
  | FileDialog
  | { kind: "create" }
  | { kind: "settings" }
  | { kind: "vpn" }
  | { kind: "about" }
  | { kind: "remove" };

const FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "downloading", label: "Downloading" },
  { id: "queued", label: "Queued" },
  { id: "seeding", label: "Seeding" },
  { id: "completed", label: "Completed" },
  { id: "stopped", label: "Stopped" },
];

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [focus, setFocus] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "added_at", dir: "desc" });
  const [tab, setTab] = useState("general");
  const [detailHeight, setDetailHeight] = useState(280);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [offline, setOffline] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [password, setPasswordValue] = useState("");
  const [native, setNative] = useState(false);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [updating, setUpdating] = useState(false);
  const toldUpdate = useRef(0);
  const [menu, setMenu] = useState<{ x: number; y: number; ids: string[] } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const focusRef = useRef<string | null>(null);
  const anchorRef = useRef<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  focusRef.current = focus;

  function notify(message: string) {
    setToast(message);
    window.setTimeout(() => setToast((current) => (current === message ? null : current)), 3200);
  }

  useEffect(() => {
    if (locked) return;
    let stop = false;
    const look = () => {
      api.updateStatus().then((status) => {
        if (!stop) setUpdate(status);
      }).catch(() => undefined);
    };
    look();
    const timer = window.setInterval(look, 120_000);
    const onFocus = () => look();
    window.addEventListener("focus", onFocus);
    return () => {
      stop = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [locked]);

  useEffect(() => {
    if (!update?.available || update.latest_build <= toldUpdate.current) return;
    toldUpdate.current = update.latest_build;
    const text = update.notes
      ? `Build ${update.latest_build} is ready. ${update.notes}`
      : `Build ${update.latest_build} is ready.`;
    notify(text);
    notifyUpdate("fraghost update", text);
  }, [update]);

  function installUpdate() {
    setUpdating(true);
    void api.applyUpdate().catch((error: Error) => {
      setUpdating(false);
      notify(error.message);
    });
  }

  useEffect(() => {
    if (locked) return;
    let stop = false;
    async function tick() {
      try {
        const next = await api.state(focusRef.current);
        if (stop) return;
        setSnapshot(next);
        setOffline(false);
      } catch (error) {
        if (stop) return;
        if (error instanceof AuthError) setLocked(true);
        else setOffline(true);
      }
    }
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [locked]);

  useEffect(() => {
    if (locked) return;
    api.settings().then(setSettings).catch(() => undefined);
  }, [locked]);

  useEffect(() => {
    document.documentElement.dataset.theme = settings?.theme || "system";
  }, [settings?.theme]);

  useEffect(() => {
    void whenDesktopReady().then(() => setNative(hasDesktop()));
  }, []);

  useEffect(() => {
    if (!menu) return;
    function close() { setMenu(null); }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenu(null);
    }
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  useEffect(() => {
    const node = menuRef.current;
    if (!menu || !node) return;
    const rect = node.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) node.style.top = `${Math.max(8, window.innerHeight - rect.height - 8)}px`;
    if (rect.right > window.innerWidth) node.style.left = `${Math.max(8, window.innerWidth - rect.width - 8)}px`;
  }, [menu]);

  useEffect(() => {
    if (!snapshot) return;
    const ids = new Set(snapshot.torrents.map((torrent) => torrent.id));
    setSelected((current) => current.filter((id) => ids.has(id)));
    setFocus((current) => (current && ids.has(current) ? current : null));
  }, [snapshot]);

  useEffect(() => {
    if (!dialog || dialog.kind !== "files" || !dialog.fetching || !dialog.torrentId || !snapshot?.detail) return;
    const detail = snapshot.detail;
    if (detail.id !== dialog.torrentId || !detail.files?.length) return;
    setDialog({ ...dialog, fetching: false, name: detail.name, size: detail.size, files: detail.files, infoHash: detail.id });
  }, [snapshot, dialog]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.key === "Delete" && selected.length) setDialog({ kind: "remove" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  const torrents = snapshot?.torrents ?? [];
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = torrents.filter((torrent) => matches(torrent, filter) && (!needle || `${torrent.name} ${torrent.label}`.toLowerCase().includes(needle)));
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => compare(a, b, sort.key) * factor);
  }, [torrents, filter, query, sort]);

  const counts = useMemo(() => ({
    all: torrents.length,
    downloading: torrents.filter((torrent) => ["downloading", "metadata", "checking"].includes(torrent.state)).length,
    queued: torrents.filter((torrent) => torrent.state === "queued").length,
    seeding: torrents.filter((torrent) => torrent.state === "seeding" || torrent.state === "finished").length,
    completed: torrents.filter((torrent) => torrent.progress >= 1 || torrent.state === "seeding" || torrent.state === "finished").length,
    stopped: torrents.filter((torrent) => torrent.state === "paused" || torrent.state === "stopped").length,
    error: torrents.filter((torrent) => torrent.state === "error").length,
  }), [torrents]);

  async function run(task: () => Promise<unknown>, success?: string) {
    try {
      await task();
      if (success) notify(success);
      const next = await api.state(focusRef.current);
      setSnapshot(next);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Something went wrong");
    }
  }

  function toggleVpn() {
    if (!settings) return;
    if (settings.vpn_enabled) {
      void run(async () => {
        setSettings(await api.saveSettings({ ...settings, vpn_enabled: false }));
      }, "VPN off. Transfers use this connection directly.");
      return;
    }
    const ready = settings.proxy_type !== "none" && settings.proxy_host.trim() !== "" && settings.proxy_port > 0;
    if (!ready) {
      setDialog({ kind: "vpn" });
      return;
    }
    void run(async () => {
      setSettings(await api.saveSettings({ ...settings, vpn_enabled: true }));
    }, `VPN on. BitTorrent traffic uses ${settings.proxy_host}:${settings.proxy_port}.`);
  }

  async function act(action: string) {
    if (!selected.length) return;
    await actIds(action, selected);
  }

  async function actIds(action: string, ids: string[]) {
    if (!ids.length) return;
    await run(async () => {
      for (const id of ids) await api.action(id, action);
    });
  }

  function openMenu(torrent: Torrent, event: ReactMouseEvent) {
    event.preventDefault();
    const ids = selected.includes(torrent.id) ? selected : [torrent.id];
    if (!selected.includes(torrent.id)) {
      setSelected([torrent.id]);
      anchorRef.current = torrent.id;
    }
    setFocus(torrent.id);
    setMenu({ x: event.clientX, y: event.clientY, ids });
  }

  function copyMagnet(id: string) {
    void run(async () => {
      const next = await api.state(id);
      const magnet = next.detail?.magnet || "";
      if (!magnet) throw new Error("That magnet link is not ready yet");
      await navigator.clipboard.writeText(magnet);
    }, "Magnet link copied");
  }

  function startTitleDrag(event: ReactMouseEvent) {
    if (!native || event.button !== 0) return;
    event.stopPropagation();
    const ratioX = event.clientX / Math.max(1, window.innerWidth);
    const grabY = event.clientY;
    const originX = event.screenX;
    const originY = event.screenY;
    let chain = Promise.resolve();
    function move(next: MouseEvent) {
      if (Math.abs(next.screenX - originX) + Math.abs(next.screenY - originY) < 4) return;
      const screenX = next.screenX;
      const screenY = next.screenY;
      chain = chain.then(() => windowDrag(screenX, screenY, ratioX, grabY));
    }
    function up() {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  function startResize(edge: string, event: ReactMouseEvent) {
    event.preventDefault();
    let lastX = event.screenX;
    let lastY = event.screenY;
    let chain = Promise.resolve();
    function move(next: MouseEvent) {
      const dx = next.screenX - lastX;
      const dy = next.screenY - lastY;
      lastX = next.screenX;
      lastY = next.screenY;
      chain = chain.then(() => windowResize(edge, dx, dy));
    }
    function up() {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  async function removeSelected(deleteFiles: boolean, ids = selected) {
    const chosen = [...ids];
    await run(async () => {
      for (const id of chosen) await api.remove(id, deleteFiles);
    }, deleteFiles ? "Removed and deleted files" : "Removed");
    setDialog(null);
  }

  function chooseRow(torrent: Torrent, event: ReactMouseEvent) {
    if (event.shiftKey && anchorRef.current) {
      const ids = visible.map((item) => item.id);
      const start = ids.indexOf(anchorRef.current);
      const end = ids.indexOf(torrent.id);
      if (start >= 0 && end >= 0) {
        const [from, to] = start < end ? [start, end] : [end, start];
        setSelected(ids.slice(from, to + 1));
        setFocus(torrent.id);
        return;
      }
    }
    if (event.metaKey || event.ctrlKey) {
      setSelected((current) => (current.includes(torrent.id) ? current.filter((id) => id !== torrent.id) : [...current, torrent.id]));
    } else {
      setSelected([torrent.id]);
      anchorRef.current = torrent.id;
    }
    setFocus(torrent.id);
  }

  async function openTorrentFiles() {
    const native = await pickTorrents();
    if (native === null) {
      fileRef.current?.click();
      return;
    }
    if (native.length) await addPaths(native);
  }

  async function addPaths(paths: string[]) {
    const show = settings?.show_add_dialog !== false;
    await run(async () => {
      for (const path of paths) {
        const preview = await api.preview("path", path);
        if (!show) {
          await api.add({ source: "preview", value: preview.id, paused: false, save_path: settings?.save_path || "" });
        } else {
          setDialog(fileDialogFromPreview(preview, settings?.save_path || ""));
          return;
        }
      }
    });
  }

  async function onFileInput(files: FileList | null) {
    if (!files?.length) return;
    const show = settings?.show_add_dialog !== false;
    await run(async () => {
      for (const file of files) {
        const value = toBase64(await file.arrayBuffer());
        const preview = await api.preview("base64", value);
        if (!show) await api.add({ source: "preview", value: preview.id, paused: false, save_path: settings?.save_path || "" });
        else {
          setDialog(fileDialogFromPreview(preview, settings?.save_path || ""));
          return;
        }
      }
    });
    if (fileRef.current) fileRef.current.value = "";
  }

  async function submitLink(value: string) {
    const link = value.trim();
    const show = settings?.show_add_dialog !== false;
    if (link.startsWith("magnet:")) {
      await run(async () => {
        const added = await api.add({
          source: "magnet",
          value: link,
          paused: false,
          save_path: settings?.save_path || "",
        });
        setFocus(added.id);
        setSelected([added.id]);
        setDialog(null);
      }, "Added. Fetching metadata in the background.");
      return;
    }
    await run(async () => {
      const preview = await api.preview("url", link);
      if (!show) {
        await api.add({ source: "preview", value: preview.id, paused: false, save_path: settings?.save_path || "" });
        setDialog(null);
        return;
      }
      setDialog(fileDialogFromPreview(preview, settings?.save_path || ""));
    });
  }

  async function confirmFiles(start: boolean) {
    if (!dialog || dialog.kind !== "files") return;
    const priorities = dialog.files.map((file) => file.priority);
    await run(async () => {
      if (dialog.torrentId) {
        await api.update(dialog.torrentId, {
          file_priorities: priorities,
          save_path: dialog.savePath,
          label: dialog.label,
          sequential: dialog.sequential,
        });
        if (start) await api.action(dialog.torrentId, "resume");
      } else {
        await api.add({
          source: dialog.source,
          value: dialog.value,
          save_path: dialog.savePath,
          label: dialog.label,
          sequential: dialog.sequential,
          file_priorities: priorities,
          paused: !start,
        });
      }
    }, start ? "Torrent started" : "Torrent added");
    setDialog(null);
  }

  async function cancelFiles() {
    if (dialog?.kind === "files" && dialog.torrentId) {
      await run(() => api.remove(dialog.torrentId as string, false));
    }
    setDialog(null);
  }

  async function onDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    const text = event.dataTransfer.getData("text");
    if (text && (text.startsWith("magnet:") || text.startsWith("http"))) {
      setDialog({ kind: "link", value: text.trim() });
      return;
    }
    const files = [...event.dataTransfer.files].filter((file) => file.name.endsWith(".torrent"));
    if (!files.length) return;
    const list = new DataTransfer();
    files.forEach((file) => list.items.add(file));
    await onFileInput(list.files);
  }

  const detail = snapshot?.detail && snapshot.detail.id === focus ? snapshot.detail : null;
  const stats = snapshot?.stats;

  if (locked) {
    return (
      <div className="overlay">
        <form
          className="dialog lock"
          onSubmit={(event) => {
            event.preventDefault();
            setPassword(password);
            setLocked(false);
          }}
        >
          <div className="brand-lock">
            <Mark size={28} />
            <h2>fraghost</h2>
          </div>
          <p className="lead">This client is locked with a local password.</p>
          <label className="field">
            <span>Password</span>
            <input type="password" value={password} onChange={(event) => setPasswordValue(event.target.value)} autoFocus />
          </label>
          <div className="dialog-actions">
            <button className="btn-primary" type="submit">Unlock</button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div
      className={`shell${native ? " native" : ""}${dragging ? " drop" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => void onDrop(event)}
    >
      {native && (["n", "s", "e", "w", "nw", "ne", "sw", "se"] as const).map((edge) => (
        <div key={edge} className={`resize ${edge}`} onMouseDown={(event) => startResize(edge, event)} />
      ))}
      <header className={native ? "top native" : "top"}>
        <div className="brand pywebview-drag-region" onMouseDown={startTitleDrag} onDoubleClick={() => native && windowToggleMaximize()}>
          <Mark />
          <span className="word">fraghost</span>
        </div>
        <div className="title-gap pywebview-drag-region" onMouseDown={startTitleDrag} onDoubleClick={() => native && windowToggleMaximize()} />
        <input className="search" placeholder="Filter" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Filter torrents" />
        <div className="rates">
          <button
            type="button"
            className="vpn-switch"
            role="switch"
            aria-checked={settings?.vpn_enabled === true}
            aria-label="VPN"
            data-state={stats?.vpn === "blocked" ? "blocked" : settings?.vpn_enabled ? "on" : "off"}
            disabled={!settings}
            onClick={toggleVpn}
          >
            <span className="track" aria-hidden="true"><span className="knob" /></span>
            VPN
          </button>
          <span className="down">↓ {formatSpeed(stats?.download_rate ?? 0)}</span>
          <span className="up">↑ {formatSpeed(stats?.upload_rate ?? 0)}</span>
        </div>
        {native && (
          <div className="window-controls">
            <button type="button" className="win-btn" aria-label="Full screen" title="Full screen" onClick={windowToggleFullscreen}><Glyph name="fullscreen" /></button>
            <button type="button" className="win-btn" aria-label="Minimize" onClick={windowMinimize}><Glyph name="minimize" /></button>
            <button type="button" className="win-btn" aria-label="Maximize" title="Maximize" onClick={windowToggleMaximize}><Glyph name="maximize" /></button>
            <button type="button" className="win-btn close" aria-label="Close" title="Close to the tray" onClick={windowHide}><Glyph name="close" /></button>
          </div>
        )}
      </header>
      <div className="toolbar">
        <button type="button" className="btn icon-btn" title="Add" onClick={() => void openTorrentFiles()}><Glyph name="add" /><span>Add</span></button>
        <button type="button" className="btn icon-btn" title="Link" onClick={() => setDialog({ kind: "link", value: "" })}><Glyph name="link" /><span>Link</span></button>
        <button type="button" className="btn icon-btn" title="Create" onClick={() => setDialog({ kind: "create" })}><Glyph name="create" /><span>Create</span></button>
        <button type="button" className="btn icon-btn" title="Start" disabled={!selected.length} onClick={() => void act("resume")}><Glyph name="play" /><span>Start</span></button>
        <button type="button" className="btn icon-btn" title="Pause" disabled={!selected.length} onClick={() => void act("pause")}><Glyph name="pause" /><span>Pause</span></button>
        <button type="button" className="btn icon-btn" title="Stop" disabled={!selected.length} onClick={() => void act("stop")}><Glyph name="stop" /><span>Stop</span></button>
        <button type="button" className="btn icon-btn" title="Force" disabled={!selected.length} onClick={() => void act("force_start")}><Glyph name="force" /><span>Force</span></button>
        <button type="button" className="btn icon-btn" title="Up" disabled={!selected.length} onClick={() => void act("queue_up")}><Glyph name="up" /><span>Up</span></button>
        <button type="button" className="btn icon-btn" title="Down" disabled={!selected.length} onClick={() => void act("queue_down")}><Glyph name="down" /><span>Down</span></button>
        <button type="button" className="btn icon-btn" title="Open" disabled={selected.length !== 1} onClick={() => selected[0] && void run(() => api.open(selected[0]))}><Glyph name="folder" /><span>Open</span></button>
        <button type="button" className="btn-danger icon-btn" title="Remove" disabled={!selected.length} onClick={() => (settings?.confirm_remove === false ? void removeSelected(false) : setDialog({ kind: "remove" }))}><Glyph name="trash" /><span>Remove</span></button>
        <button type="button" className="btn icon-btn" title="Settings" onClick={() => setDialog({ kind: "settings" })}><Glyph name="gear" /><span>Settings</span></button>
      </div>
      {offline && <div className="banner">fraghost isn't reachable. The local engine may still be starting.</div>}
      <div className="workspace">
        <aside>
          <div className="nav-label">Transfers</div>
          {FILTERS.map((item) => (
            <button key={item.id} className="nav-btn" aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>
              <strong>{item.label}</strong>
              <em>{counts[item.id as keyof typeof counts]}</em>
            </button>
          ))}
          {counts.error > 0 && (
            <button className="nav-btn error" aria-pressed={filter === "error"} onClick={() => setFilter("error")}>
              <strong>Error</strong>
              <em>{counts.error}</em>
            </button>
          )}
          <div className="nav-label">Labels</div>
          {(snapshot?.labels ?? []).length === 0 && <div className="nav-btn"><strong style={{ color: "var(--faint)" }}>None yet</strong></div>}
          {(snapshot?.labels ?? []).map((label) => (
            <button key={label} className="nav-btn" aria-pressed={filter === `label:${label}`} onClick={() => setFilter(`label:${label}`)}>
              <strong>{label}</strong>
              <em>{torrents.filter((torrent) => torrent.label === label).length}</em>
            </button>
          ))}
        </aside>
        <div className="column">
          <div className="table-scroll">
            {visible.length === 0 ? (
              <div className="empty">
                <div>
                  {torrents.length === 0 && <Mark size={44} />}
                  <h2>{torrents.length === 0 ? "No torrents yet" : "Nothing in this view"}</h2>
                  <p>Add a .torrent file or a magnet link. Magnet metadata is fetched in the background, so the transfer can start immediately. fraghost is free software: no ads, no tracking, no bundled offers.</p>
                </div>
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th />
                    <SortHead className="col-queue" label="#" sortKey="queue_position" sort={sort} onSort={setSort} />
                    <SortHead label="Name" sortKey="name" sort={sort} onSort={setSort} />
                    <SortHead className="col-size" label="Size" sortKey="size" sort={sort} onSort={setSort} numeric />
                    <SortHead label="Progress" sortKey="progress" sort={sort} onSort={setSort} />
                    <SortHead label="Status" sortKey="state" sort={sort} onSort={setSort} />
                    <SortHead className="col-seeds" label="Seeds" sortKey="seeds_connected" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-peers" label="Peers" sortKey="peers_connected" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-down" label="Down" sortKey="download_rate" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-up" label="Up" sortKey="upload_rate" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-eta" label="ETA" sortKey="eta" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-ratio" label="Ratio" sortKey="ratio" sort={sort} onSort={setSort} numeric />
                    <SortHead className="col-label" label="Label" sortKey="label" sort={sort} onSort={setSort} />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((torrent) => (
                    <tr key={torrent.id} data-selected={selected.includes(torrent.id)} data-state={torrent.state} onClick={(event) => chooseRow(torrent, event)} onContextMenu={(event) => openMenu(torrent, event)}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${torrent.name}`}
                          checked={selected.includes(torrent.id)}
                          onClick={(event) => event.stopPropagation()}
                          onChange={(event) => {
                            const on = event.target.checked;
                            setSelected((current) => (on ? [...current, torrent.id] : current.filter((id) => id !== torrent.id)));
                            setFocus(torrent.id);
                          }}
                        />
                      </td>
                      <td className="num col-queue">{torrent.queue_position >= 0 ? torrent.queue_position + 1 : "—"}</td>
                      <td className="name" title={torrent.name}>{torrent.name}{torrent.force_start ? <span className="bolt">∗</span> : null}</td>
                      <td className="num col-size">{formatBytes(torrent.size)}</td>
                      <td>
                        <div className="progress-cell">
                          <div className="bar" aria-hidden="true"><span style={{ width: `${Math.min(100, torrent.progress * 100)}%` }} /></div>
                          <span className="num">{formatPercent(torrent.progress)}</span>
                        </div>
                      </td>
                      <td><span className="status" data-state={torrent.state}><i />{stateLabel(torrent.state)}</span></td>
                      <td className="num col-seeds">{swarm(torrent.seeds_connected, torrent.seeds_total)}</td>
                      <td className="num col-peers">{swarm(torrent.peers_connected, torrent.peers_total)}</td>
                      <td className="num col-down">{formatSpeed(torrent.download_rate)}</td>
                      <td className="num col-up">{formatSpeed(torrent.upload_rate)}</td>
                      <td className="num col-eta">{formatEta(torrent.eta)}</td>
                      <td className="num col-ratio">{formatRatio(torrent.ratio, torrent.uploaded)}</td>
                      <td className="col-label">{torrent.label || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div
            className="splitter"
            onMouseDown={(event) => {
              const startY = event.clientY;
              const startH = detailHeight;
              function move(next: MouseEvent) {
                setDetailHeight(Math.min(520, Math.max(168, startH + (startY - next.clientY))));
              }
              function up() {
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
              }
              window.addEventListener("mousemove", move);
              window.addEventListener("mouseup", up);
            }}
          />
          <section className="detail" style={{ height: detailHeight }}>
            <div className="tabs" role="tablist">
              {["general", "files", "trackers", "peers", "pieces", "speed"].map((item) => (
                <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>
                  {item[0].toUpperCase() + item.slice(1)}
                </button>
              ))}
            </div>
            <div className="detail-body">
              {!detail && <p className="muted">Select a torrent to see files, peers, and trackers.</p>}
              {detail && (
                <Detail
                  tab={tab}
                  detail={detail}
                  history={snapshot?.speed_history ?? []}
                  onPatch={(patch) => void run(() => api.update(detail.id, patch))}
                  onOpen={() => void run(() => api.open(detail.id))}
                  onCopy={async () => {
                    await navigator.clipboard.writeText(detail.magnet);
                    notify("Magnet link copied");
                  }}
                  onExport={() => void run(() => api.exportTorrent(detail.id, detail.name))}
                  onRecheck={() => void run(() => api.action(detail.id, "recheck"), "Recheck started")}
                  onReannounce={() => void run(() => api.action(detail.id, "reannounce"), "Announced")}
                />
              )}
            </div>
          </section>
        </div>
      </div>
      <footer className="footer">
        <span>
          {stats?.vpn === "on" ? `VPN ${stats.vpn_label}` : stats?.vpn === "blocked" ? "VPN waiting for a server" : "VPN off"}
          {" · "}
          {stats?.dht ? `DHT ${stats.dht_nodes}` : "DHT off"}
          {" · "}port {stats?.listen_port || "—"}
          {stats?.vpn === "on" || stats?.vpn === "blocked" ? "" : stats?.external_ip ? ` · ${stats.external_ip}` : ""}
          {stats?.has_incoming ? " · incoming" : ""}
          {stats?.scheduler_enabled && stats.schedule === "L" ? " · scheduler limited" : ""}
          {stats?.scheduler_enabled && stats.schedule === "S" ? " · scheduler stopped" : ""}
        </span>
        <span>
          <b>{torrents.length}</b> torrents
          {" · "}↓ {formatBytes(stats?.total_downloaded ?? 0)}
          {" · "}↑ {formatBytes(stats?.total_uploaded ?? 0)}
          {" · "}
          {update?.available && (
            <button className="btn" type="button" disabled={updating} onClick={installUpdate}>{updating ? "Updating…" : "Update"}</button>
          )}
          {" · "}
          <button className="btn-ghost" onClick={() => setDialog({ kind: "about" })}>About</button>
        </span>
      </footer>
      {menu && (
        <div
          ref={menuRef}
          className="context-menu"
          role="menu"
          style={{ left: menu.x, top: menu.y }}
          onClick={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
        >
          <MenuItem glyph="play" label="Start" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("resume", ids); }} />
          <MenuItem glyph="pause" label="Pause" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("pause", ids); }} />
          <MenuItem glyph="stop" label="Stop" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("stop", ids); }} />
          <MenuItem glyph="force" label="Force start" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("force_start", ids); }} />
          <MenuItem glyph="up" label="Move up" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("queue_up", ids); }} />
          <MenuItem glyph="down" label="Move down" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("queue_down", ids); }} />
          <hr />
          <MenuItem glyph="folder" label="Open folder" disabled={menu.ids.length !== 1} onClick={() => { const id = menu.ids[0]; setMenu(null); if (id) void run(() => api.open(id)); }} />
          <MenuItem glyph="copy" label="Copy magnet" disabled={menu.ids.length !== 1} onClick={() => { const id = menu.ids[0]; setMenu(null); if (id) copyMagnet(id); }} />
          <MenuItem glyph="recheck" label="Recheck" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("recheck", ids); }} />
          <MenuItem glyph="announce" label="Reannounce" onClick={() => { const ids = menu.ids; setMenu(null); void actIds("reannounce", ids); }} />
          <hr />
          <MenuItem glyph="trash" label="Remove" danger onClick={() => { const ids = menu.ids; setMenu(null); setSelected(ids); if (settings?.confirm_remove === false) void removeSelected(false, ids); else setDialog({ kind: "remove" }); }} />
        </div>
      )}
      <input ref={fileRef} type="file" accept=".torrent,application/x-bittorrent" multiple hidden onChange={(event) => void onFileInput(event.target.files)} />
      {dialog?.kind === "link" && (
        <LinkDialog
          value={dialog.value}
          onClose={() => setDialog(null)}
          onSubmit={(value) => void submitLink(value)}
        />
      )}
      {dialog?.kind === "files" && (
        <FilesDialog
          dialog={dialog}
          onChange={setDialog}
          onCancel={() => void cancelFiles()}
          onConfirm={(start) => void confirmFiles(start)}
        />
      )}
      {dialog?.kind === "create" && settings && (
        <CreateDialog
          savePath={settings.save_path}
          onClose={() => setDialog(null)}
          onCreate={async (body) => {
            const result = await api.create(body);
            notify(`Created ${String(result.name || "torrent")}`);
            setDialog(null);
            const next = await api.state(focusRef.current);
            setSnapshot(next);
          }}
        />
      )}
      {dialog?.kind === "vpn" && settings && (
        <VpnDialog
          settings={settings}
          onClose={() => setDialog(null)}
          onSave={async (next) => {
            const saved = await api.saveSettings({ ...settings, ...next, vpn_enabled: true, proxy_peers: true, proxy_hostnames: true });
            setSettings(saved);
            notify(`VPN on. BitTorrent traffic uses ${saved.proxy_host}:${saved.proxy_port}.`);
            setDialog(null);
            setSnapshot(await api.state(focusRef.current));
          }}
        />
      )}
      {dialog?.kind === "settings" && settings && (
        <SettingsDialog
          settings={settings}
          onClose={() => {
            document.documentElement.dataset.theme = settings.theme || "system";
            setDialog(null);
          }}
          onSave={async (next) => {
            const saved = await api.saveSettings(next);
            setSettings(saved);
            if (saved.web_password) setPassword(saved.web_password);
            notify("Settings saved");
            setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "remove" && (
        <RemoveDialog count={selected.length} onCancel={() => setDialog(null)} onConfirm={(deleteFiles) => void removeSelected(deleteFiles)} />
      )}
      {dialog?.kind === "about" && (
        <AboutDialog
          app={snapshot?.app}
          update={update}
          updating={updating}
          onUpdate={installUpdate}
          onClose={() => setDialog(null)}
        />
      )}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function matches(torrent: Torrent, filter: string): boolean {
  if (filter === "all") return true;
  if (filter === "downloading") return ["downloading", "metadata", "checking"].includes(torrent.state);
  if (filter === "queued") return torrent.state === "queued";
  if (filter === "seeding") return torrent.state === "seeding" || torrent.state === "finished";
  if (filter === "completed") return torrent.progress >= 1 || torrent.state === "seeding" || torrent.state === "finished";
  if (filter === "stopped") return torrent.state === "paused" || torrent.state === "stopped";
  if (filter === "error") return torrent.state === "error";
  if (filter.startsWith("label:")) return torrent.label === filter.slice(6);
  return true;
}

function compare(a: Torrent, b: Torrent, key: SortKey): number {
  const left = a[key];
  const right = b[key];
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left ?? "").localeCompare(String(right ?? ""));
}

function fileDialogFromPreview(preview: Preview, savePath: string): FileDialog {
  return {
    kind: "files",
    name: preview.name,
    size: preview.size,
    infoHash: preview.id,
    files: preview.files,
    savePath,
    label: "",
    sequential: false,
    fetching: false,
    torrentId: null,
    source: "preview",
    value: preview.id,
  };
}

function SortHead({ label, sortKey, sort, onSort, numeric, className = "" }: { label: string; sortKey: SortKey; sort: { key: SortKey; dir: "asc" | "desc" }; onSort: (next: { key: SortKey; dir: "asc" | "desc" }) => void; numeric?: boolean; className?: string }) {
  const mark = sort.key === sortKey ? (sort.dir === "asc" ? " ↑" : " ↓") : "";
  const classes = [numeric ? "num" : "", className].filter(Boolean).join(" ");
  return (
    <th className={classes || undefined}>
      <button type="button" onClick={() => onSort({ key: sortKey, dir: sort.key === sortKey && sort.dir === "asc" ? "desc" : "asc" })}>
        {label}{mark}
      </button>
    </th>
  );
}

function LinkDialog({ value, onClose, onSubmit }: { value: string; onClose: () => void; onSubmit: (value: string) => void }) {
  const [text, setText] = useState(value);
  return (
    <div className="overlay">
      <form className="dialog" onSubmit={(event) => { event.preventDefault(); onSubmit(text); }}>
        <h2>Add link</h2>
        <p className="lead">Paste a magnet link or the http(s) address of a .torrent file. A magnet starts immediately and fetches its file list in the background.</p>
        <textarea className="inline" style={{ width: "100%", minHeight: 90 }} value={text} onChange={(event) => setText(event.target.value)} autoFocus />
        <div className="dialog-actions">
          <button className="btn-ghost" type="button" onClick={onClose}>Cancel</button>
          <button className="btn-primary" type="submit" disabled={!text.trim()}>Add</button>
        </div>
      </form>
    </div>
  );
}

function FilesDialog({ dialog, onChange, onCancel, onConfirm }: { dialog: FileDialog; onChange: (next: FileDialog) => void; onCancel: () => void; onConfirm: (start: boolean) => void }) {
  const selectedSize = dialog.files.reduce((sum, file) => sum + (file.priority > 0 ? file.size : 0), 0);
  return (
    <div className="overlay">
      <div className="dialog">
        <h2>{dialog.fetching ? "Fetching metadata" : dialog.name}</h2>
        <p className="lead">{dialog.fetching ? "Asking the swarm for the file list. You can start the torrent while that runs." : `${formatBytes(dialog.size)} · selected ${formatBytes(selectedSize)}`}</p>
        <div className="grid-2">
          <label className="field">
            <span>Save to</span>
            <span style={{ display: "flex", gap: 8 }}>
              <input value={dialog.savePath} onChange={(event) => onChange({ ...dialog, savePath: event.target.value })} />
              <button className="btn" type="button" onClick={() => void pickFolder().then((folder) => folder && onChange({ ...dialog, savePath: folder }))}>Browse</button>
            </span>
          </label>
          <label className="field">
            <span>Label</span>
            <input value={dialog.label} onChange={(event) => onChange({ ...dialog, label: event.target.value })} />
          </label>
        </div>
        <label className="check" style={{ margin: "12px 0" }}>
          <input type="checkbox" checked={dialog.sequential} onChange={(event) => onChange({ ...dialog, sequential: event.target.checked })} />
          Download in order
        </label>
        <div className="file-table">
          <table className="subtable">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Select all files"
                    checked={dialog.files.length > 0 && dialog.files.every((file) => file.priority > 0)}
                    onChange={(event) => onChange({ ...dialog, files: dialog.files.map((file) => ({ ...file, priority: event.target.checked ? 4 : 0 })) })}
                  />
                </th>
                <th>File</th>
                <th className="num">Size</th>
                <th>Priority</th>
              </tr>
            </thead>
            <tbody>
              {dialog.files.map((file) => (
                <tr key={file.index}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={file.path}
                      checked={file.priority > 0}
                      onChange={(event) => onChange({
                        ...dialog,
                        files: dialog.files.map((item) => item.index === file.index ? { ...item, priority: event.target.checked ? item.priority || 4 : 0 } : item),
                      })}
                    />
                  </td>
                  <td className="path" title={file.path}>{file.path}</td>
                  <td className="num">{formatBytes(file.size)}</td>
                  <td>
                    <select
                      className="inline"
                      value={file.priority > 0 ? nearestPriority(file.priority) : 0}
                      onChange={(event) => {
                        const priority = Number(event.target.value);
                        onChange({ ...dialog, files: dialog.files.map((item) => item.index === file.index ? { ...item, priority } : item) });
                      }}
                    >
                      <option value={0}>Skip</option>
                      <option value={1}>Low</option>
                      <option value={4}>Normal</option>
                      <option value={6}>High</option>
                      <option value={7}>Max</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="dialog-actions">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn" disabled={dialog.fetching} onClick={() => onConfirm(false)}>Add paused</button>
          <button className="btn-primary" disabled={dialog.files.length > 0 && dialog.files.every((file) => file.priority === 0)} onClick={() => onConfirm(true)}>Start</button>
        </div>
      </div>
    </div>
  );
}

function nearestPriority(value: number): number {
  if (value >= 7) return 7;
  if (value >= 6) return 6;
  if (value >= 4) return 4;
  if (value >= 1) return 1;
  return 0;
}

function CreateDialog({ savePath, onClose, onCreate }: { savePath: string; onClose: () => void; onCreate: (body: Record<string, unknown>) => Promise<void> }) {
  const [path, setPath] = useState("");
  const [output, setOutput] = useState("");
  const [trackers, setTrackers] = useState("");
  const [comment, setComment] = useState("");
  const [isPrivate, setPrivate] = useState(false);
  const [pieceSize, setPieceSize] = useState(0);
  const [hybrid, setHybrid] = useState(false);
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="overlay">
      <form
        className="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          setError("");
          onCreate({ path, output, trackers, comment, private: isPrivate, piece_size: pieceSize, hybrid, start }).catch((reason: Error) => {
            setError(reason.message);
            setBusy(false);
          });
        }}
      >
        <h2>Create torrent</h2>
        <p className="lead">A blank line in the tracker list starts the next tier. v1 torrents work with classic clients.</p>
        <div className="stack">
          <label className="field">
            <span>File or folder</span>
            <span style={{ display: "flex", gap: 8 }}>
              <input value={path} onChange={(event) => setPath(event.target.value)} required />
              <button className="btn" type="button" onClick={() => void pickFolder().then((folder) => folder && setPath(folder))}>Browse</button>
            </span>
          </label>
          <label className="field">
            <span>Save torrent as</span>
            <span style={{ display: "flex", gap: 8 }}>
              <input value={output} onChange={(event) => setOutput(event.target.value)} placeholder={`${savePath}\\name.torrent`} required />
              <button className="btn" type="button" onClick={() => void pickSave("new.torrent").then((file) => file && setOutput(file))}>Browse</button>
            </span>
          </label>
          <label className="field">
            <span>Trackers</span>
            <textarea value={trackers} onChange={(event) => setTrackers(event.target.value)} placeholder={"udp://tracker.example:1337/announce"} />
          </label>
          <div className="grid-2">
            <label className="field">
              <span>Comment</span>
              <input value={comment} onChange={(event) => setComment(event.target.value)} />
            </label>
            <label className="field">
              <span>Piece size</span>
              <select value={pieceSize} onChange={(event) => setPieceSize(Number(event.target.value))}>
                <option value={0}>Automatic</option>
                {[16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384].map((kb) => (
                  <option key={kb} value={kb * 1024}>{kb >= 1024 ? `${kb / 1024} MB` : `${kb} KB`}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="check"><input type="checkbox" checked={isPrivate} onChange={(event) => setPrivate(event.target.checked)} /> Private torrent</label>
          <label className="check"><input type="checkbox" checked={hybrid} onChange={(event) => setHybrid(event.target.checked)} /> Hybrid v1 + v2</label>
          <label className="check"><input type="checkbox" checked={start} onChange={(event) => setStart(event.target.checked)} /> Start seeding after creation</label>
          {error && <p style={{ color: "var(--danger)", margin: 0 }}>{error}</p>}
        </div>
        <div className="dialog-actions">
          <button className="btn-ghost" type="button" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Hashing…" : "Create"}</button>
        </div>
      </form>
    </div>
  );
}

function RemoveDialog({ count, onCancel, onConfirm }: { count: number; onCancel: () => void; onConfirm: (deleteFiles: boolean) => void }) {
  const [deleteFiles, setDeleteFiles] = useState(false);
  return (
    <div className="overlay">
      <div className="dialog">
        <h2>Remove {count === 1 ? "torrent" : `${count} torrents`}</h2>
        <p className="lead">The transfer leaves the list. Downloaded files stay unless you delete them.</p>
        <label className="check"><input type="checkbox" checked={deleteFiles} onChange={(event) => setDeleteFiles(event.target.checked)} /> Also delete files</label>
        <div className="dialog-actions">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-danger" onClick={() => onConfirm(deleteFiles)}>Remove</button>
        </div>
      </div>
    </div>
  );
}

function AboutDialog({
  app,
  update,
  updating,
  onUpdate,
  onClose,
}: {
  app?: { name: string; version: string; build?: number; engine: string };
  update: UpdateStatus | null;
  updating: boolean;
  onUpdate: () => void;
  onClose: () => void;
}) {
  const build = app?.build ?? update?.current_build ?? 0;
  return (
    <div className="overlay">
      <div className="dialog">
        <div className="brand-lock">
          <Mark size={36} />
          <h2>{app?.name || "fraghost"} {app?.version ?? "0.1.0"}</h2>
        </div>
        <p className="lead">
          A free BitTorrent client with the classic transfer tools: magnets, file selection, queues, rate limits, a scheduler, RSS, and a blocklist.
          Magnet links start immediately and fetch their file list in the background.
          There are no ads, no telemetry, and no bundled offers. The interface is GPL-3.0-or-later. The transfer engine is libtorrent {app?.engine}, BSD licensed.
        </p>
        <p className="muted">
          {build > 0 ? `Installed build ${build}.` : "This is a development copy."}
          {update?.available ? ` Build ${update.latest_build} is published.` : ""}
          {update?.notes ? ` ${update.notes}` : ""}
          {!update?.available && update?.error ? ` ${update.error}` : ""}
        </p>
        <div className="dialog-actions">
          {update?.available && (
            <button className="btn" type="button" disabled={updating} onClick={onUpdate}>{updating ? "Updating…" : "Update"}</button>
          )}
          <button className="btn-primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function Detail({ tab, detail, history, onPatch, onOpen, onCopy, onExport, onRecheck, onReannounce }: {
  tab: string;
  detail: NonNullable<Snapshot["detail"]>;
  history: Snapshot["speed_history"];
  onPatch: (patch: Record<string, unknown>) => void;
  onOpen: () => void;
  onCopy: () => void;
  onExport: () => void;
  onRecheck: () => void;
  onReannounce: () => void;
}) {
  const [label, setLabel] = useState(detail.label);
  const [down, setDown] = useState(detail.download_limit);
  const [up, setUp] = useState(detail.upload_limit);
  const [tracker, setTracker] = useState("");
  const [peer, setPeer] = useState("");
  useEffect(() => {
    setLabel(detail.label);
    setDown(detail.download_limit);
    setUp(detail.upload_limit);
  }, [detail.id, detail.label, detail.download_limit, detail.upload_limit]);

  if (tab === "files") {
    return (
      <table className="subtable">
        <thead><tr><th>File</th><th className="num">Size</th><th>Progress</th><th>Priority</th></tr></thead>
        <tbody>
          {detail.files.length === 0 && (
            <tr>
              <td colSpan={4}>{detail.has_metadata ? "This torrent has no files." : "Fetching the file list in the background. Start, pause, and stop still work."}</td>
            </tr>
          )}
          {detail.files.map((file) => (
            <tr key={file.index}>
              <td className="path" title={file.path}>{file.path}</td>
              <td className="num">{formatBytes(file.size)}</td>
              <td className="num">{formatPercent(file.progress)}</td>
              <td>
                <select className="inline" value={nearestPriority(file.priority)} onChange={(event) => onPatch({ file_priorities: [{ index: file.index, priority: Number(event.target.value) }] })}>
                  <option value={0}>Skip</option>
                  <option value={1}>Low</option>
                  <option value={4}>Normal</option>
                  <option value={6}>High</option>
                  <option value={7}>Max</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (tab === "trackers") {
    return (
      <div>
        <table className="subtable">
          <thead><tr><th>Tracker</th><th>Status</th><th className="num">Seeds</th><th className="num">Peers</th><th className="num">Next</th><th /></tr></thead>
          <tbody>
            {detail.trackers.map((row) => (
              <tr key={row.url}>
                <td className="path" title={row.url}>{row.url}</td>
                <td>{row.status}</td>
                <td className="num">{row.seeds ?? "—"}</td>
                <td className="num">{row.peers ?? "—"}</td>
                <td className="num">{row.next_announce ? formatEta(row.next_announce) : "—"}</td>
                <td><button className="btn-ghost" onClick={() => onPatch({ remove_trackers: [row.url] })}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <form className="controls" onSubmit={(event) => { event.preventDefault(); if (tracker.trim()) { onPatch({ add_trackers: [tracker.trim()] }); setTracker(""); } }}>
          <input className="inline" style={{ flex: 1 }} placeholder="udp://tracker:1337/announce" value={tracker} onChange={(event) => setTracker(event.target.value)} />
          <button className="btn" type="submit">Add tracker</button>
          <button className="btn" type="button" onClick={onReannounce}>Update trackers</button>
        </form>
      </div>
    );
  }
  if (tab === "peers") {
    return (
      <div>
        <table className="subtable">
          <thead><tr><th>Address</th><th>Client</th><th>Progress</th><th className="num">Down</th><th className="num">Up</th><th>Flags</th></tr></thead>
          <tbody>
            {detail.peers.length === 0 && <tr><td colSpan={6}>No peers connected.</td></tr>}
            {detail.peers.map((row) => (
              <tr key={row.address}>
                <td className="num" style={{ textAlign: "left" }}>{row.address}</td>
                <td>{row.client || "—"}</td>
                <td className="num">{formatPercent(row.progress)}</td>
                <td className="num">{formatSpeed(row.down_speed)}</td>
                <td className="num">{formatSpeed(row.up_speed)}</td>
                <td>{row.flags || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <form className="controls" onSubmit={(event) => { event.preventDefault(); if (peer.trim()) { onPatch({ add_peer: peer.trim() }); setPeer(""); } }}>
          <input className="inline" placeholder="ip:port" value={peer} onChange={(event) => setPeer(event.target.value)} />
          <button className="btn" type="submit">Add peer</button>
        </form>
      </div>
    );
  }
  if (tab === "pieces") {
    return (
      <div>
        <p className="muted">{detail.have_pieces} / {detail.num_pieces} pieces · {formatBytes(detail.piece_size)} each</p>
        <div className="pieces">
          {detail.pieces.map((value, index) => <i key={index} data-v={String(value)} />)}
        </div>
        <div className="legend"><span>Have</span><span>Partial</span><span>Missing</span></div>
      </div>
    );
  }
  if (tab === "speed") {
    const width = 640;
    const height = 140;
    const max = Math.max(1, ...history.flatMap((sample) => [sample.down, sample.up]));
    const line = (key: "down" | "up") => history.map((sample, index) => {
      const x = history.length < 2 ? 0 : (index / (history.length - 1)) * width;
      const y = height - (sample[key] / max) * (height - 8) - 4;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return (
      <div>
        <p className="muted">Session speed, last {history.length} seconds. Down is the darker line, up is the lighter line.</p>
        <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Session speed">
          <polyline className="down" points={line("down")} />
          <polyline className="up" points={line("up")} />
        </svg>
      </div>
    );
  }
  return (
    <div>
      <div className="stats">
        <div><span>Downloaded</span><strong>{formatBytes(detail.downloaded)}</strong></div>
        <div><span>Uploaded</span><strong>{formatBytes(detail.uploaded)}</strong></div>
        <div><span>Down speed</span><strong>{formatSpeed(detail.download_rate)}</strong></div>
        <div><span>Up speed</span><strong>{formatSpeed(detail.upload_rate)}</strong></div>
        <div><span>Ratio</span><strong>{formatRatio(detail.ratio, detail.uploaded)}</strong></div>
        <div><span>ETA</span><strong>{formatEta(detail.eta)}</strong></div>
        <div><span>Seeds</span><strong>{swarm(detail.seeds_connected, detail.seeds_total)}</strong></div>
        <div><span>Peers</span><strong>{swarm(detail.peers_connected, detail.peers_total)}</strong></div>
        <div><span>Availability</span><strong>{detail.availability ?? "—"}</strong></div>
        <div><span>Active</span><strong>{formatEta(detail.active_time)}</strong></div>
        <div><span>Save path</span><strong className="path" title={detail.save_path}>{detail.save_path || "—"}</strong></div>
        <div><span>Added</span><strong>{detail.added_at ? new Date(detail.added_at * 1000).toLocaleString() : "—"}</strong></div>
      </div>
      {!detail.has_metadata && detail.state !== "paused" && detail.state !== "stopped" && (
        <p className="muted">Fetching metadata from the swarm. The torrent is already running.</p>
      )}
      {!detail.has_metadata && (detail.state === "paused" || detail.state === "stopped") && (
        <p className="muted">Metadata has not arrived yet. Start the torrent to keep fetching it.</p>
      )}
      {detail.error && <p style={{ color: "var(--danger)" }}>{detail.error}</p>}
      {detail.comment && <p className="muted">{detail.comment}</p>}
      <p className="hash">{detail.id}</p>
      <div className="controls">
        <label className="check"><input type="checkbox" checked={detail.sequential} onChange={(event) => onPatch({ sequential: event.target.checked })} /> Sequential</label>
        <label className="check"><input type="checkbox" checked={detail.super_seeding} onChange={(event) => onPatch({ super_seeding: event.target.checked })} /> Super seeding</label>
        <label className="field"><span>Label</span><input className="inline" value={label} onChange={(event) => setLabel(event.target.value)} onBlur={() => label !== detail.label && onPatch({ label })} /></label>
        <label className="field"><span>Down KB/s</span><input className="inline" type="number" min={0} value={down} onChange={(event) => setDown(Number(event.target.value))} onBlur={() => down !== detail.download_limit && onPatch({ download_limit: down })} /></label>
        <label className="field"><span>Up KB/s</span><input className="inline" type="number" min={0} value={up} onChange={(event) => setUp(Number(event.target.value))} onBlur={() => up !== detail.upload_limit && onPatch({ upload_limit: up })} /></label>
        <button className="btn" onClick={onOpen}>Show in folder</button>
        <button className="btn" onClick={onCopy} disabled={!detail.magnet}>Copy magnet</button>
        <button className="btn" onClick={onExport}>Export .torrent</button>
        <button className="btn" onClick={onRecheck}>Recheck</button>
      </div>
    </div>
  );
}

function VpnDialog({
  settings,
  onClose,
  onSave,
}: {
  settings: Settings;
  onClose: () => void;
  onSave: (next: Pick<Settings, "proxy_type" | "proxy_host" | "proxy_port" | "proxy_username" | "proxy_password">) => Promise<void>;
}) {
  const [proxyType, setProxyType] = useState<Settings["proxy_type"]>(settings.proxy_type === "none" ? "socks5" : settings.proxy_type);
  const [host, setHost] = useState(settings.proxy_host);
  const [port, setPort] = useState(settings.proxy_port || 1080);
  const [username, setUsername] = useState(settings.proxy_username);
  const [password, setProxyPassword] = useState(settings.proxy_password);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = host.trim() !== "" && port > 0 && port <= 65535;

  return (
    <div className="overlay">
      <form
        className="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready) {
            setError("Enter the server host and port.");
            return;
          }
          setBusy(true);
          onSave({
            proxy_type: proxyType,
            proxy_host: host.trim(),
            proxy_port: port,
            proxy_username: username,
            proxy_password: password,
          }).catch((reason: Error) => {
            setBusy(false);
            setError(reason.message);
          });
        }}
      >
        <h2>VPN</h2>
        <p className="muted">fraghost sends only BitTorrent traffic through this server and stops direct connections while the switch is on. Other programs on this computer stay on your normal connection.</p>
        <p className="muted">fraghost does not bundle a public relay. Those servers can see the transfers they carry. Use the SOCKS or HTTP proxy from a VPN you trust. Proton VPN, Mullvad, AirVPN, and IVPN allow BitTorrent.</p>
        <label className="field"><span>Type</span>
          <select value={proxyType} onChange={(event) => setProxyType(event.target.value as Settings["proxy_type"])}>
            <option value="socks5">SOCKS5</option>
            <option value="socks5_pw">SOCKS5 with password</option>
            <option value="socks4">SOCKS4</option>
            <option value="http">HTTP</option>
            <option value="http_pw">HTTP with password</option>
          </select>
        </label>
        <div className="grid-2">
          <label className="field"><span>Host</span><input value={host} onChange={(event) => setHost(event.target.value)} autoFocus /></label>
          <label className="field"><span>Port</span><input type="number" min={1} max={65535} value={port} onChange={(event) => setPort(Number(event.target.value))} /></label>
          <label className="field"><span>Username</span><input value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          <label className="field"><span>Password</span><input type="password" value={password} onChange={(event) => setProxyPassword(event.target.value)} /></label>
        </div>
        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        <div className="controls">
          <button className="btn" type="button" onClick={onClose}>Cancel</button>
          <button className="btn-primary" type="submit" disabled={!ready || busy}>{busy ? "Turning on…" : "Turn on"}</button>
        </div>
      </form>
    </div>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SETTING_TABS = ["Downloads", "Connection", "Bandwidth", "Queue", "Scheduler", "RSS", "Advanced"] as const;

function MenuItem({ glyph, label, disabled, danger, onClick }: { glyph: GlyphName; label: string; disabled?: boolean; danger?: boolean; onClick: () => void }) {
  return (
    <button type="button" role="menuitem" className={danger ? "danger" : undefined} disabled={disabled} onClick={onClick}>
      <Glyph name={glyph} />
      {label}
    </button>
  );
}

function SettingsDialog({ settings, onClose, onSave }: { settings: Settings; onClose: () => void; onSave: (next: Settings) => Promise<void> }) {
  const [draft, setDraft] = useState(settings);
  const [section, setSection] = useState<(typeof SETTING_TABS)[number]>("Downloads");
  const [error, setError] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const paint = useRef<string | null>(null);
  useEffect(() => {
    const up = () => { paint.current = null; };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, []);
  useEffect(() => {
    if (section === "Advanced") api.logs().then((result) => setLogs(result.lines)).catch(() => undefined);
  }, [section]);

  function writeCell(index: number, mode: string) {
    const chars = draft.scheduler.split("");
    chars[index] = mode;
    setDraft({ ...draft, scheduler: chars.join("") });
  }

  return (
    <div className="overlay">
      <form
        className="dialog wide"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(draft).catch((reason: Error) => setError(reason.message));
        }}
      >
        <h2>Settings</h2>
        <div className="settings-layout">
          <div className="settings-nav">
            {SETTING_TABS.map((item) => (
              <button key={item} type="button" aria-pressed={section === item} onClick={() => setSection(item)}>{item}</button>
            ))}
          </div>
          <div className="stack">
            {section === "Downloads" && (
              <>
                <label className="field"><span>Theme</span>
                  <select
                    value={draft.theme || "system"}
                    onChange={(event) => {
                      const theme = event.target.value as Settings["theme"];
                      setDraft({ ...draft, theme });
                      document.documentElement.dataset.theme = theme;
                    }}
                  >
                    <option value="system">System</option>
                    <option value="light">Light</option>
                    <option value="dark">Dark</option>
                  </select>
                </label>
                <p className="muted">System follows the Windows app theme. Light and dark stay fixed.</p>
                <PathField label="Save downloads to" value={draft.save_path} onChange={(save_path) => setDraft({ ...draft, save_path })} />
                <label className="check"><input type="checkbox" checked={draft.move_completed} onChange={(event) => setDraft({ ...draft, move_completed: event.target.checked })} /> Move completed downloads</label>
                <PathField label="Completed folder" value={draft.completed_path} onChange={(completed_path) => setDraft({ ...draft, completed_path })} />
                <label className="check"><input type="checkbox" checked={draft.watch_enabled} onChange={(event) => setDraft({ ...draft, watch_enabled: event.target.checked })} /> Watch a folder for new .torrent files</label>
                <PathField label="Watch folder" value={draft.watch_path} onChange={(watch_path) => setDraft({ ...draft, watch_path })} />
                <label className="check"><input type="checkbox" checked={draft.show_add_dialog} onChange={(event) => setDraft({ ...draft, show_add_dialog: event.target.checked })} /> Choose files before a torrent starts</label>
                <label className="check"><input type="checkbox" checked={draft.preallocate} onChange={(event) => setDraft({ ...draft, preallocate: event.target.checked })} /> Preallocate files</label>
                <label className="check"><input type="checkbox" checked={draft.confirm_remove} onChange={(event) => setDraft({ ...draft, confirm_remove: event.target.checked })} /> Ask before removing</label>
              </>
            )}
            {section === "Connection" && (
              <>
                <div className="grid-2">
                  <label className="field"><span>Listen port</span><input type="number" value={draft.listen_port} onChange={(event) => setDraft({ ...draft, listen_port: Number(event.target.value) })} /></label>
                  <label className="field"><span>Encryption</span>
                    <select value={draft.encryption} onChange={(event) => setDraft({ ...draft, encryption: event.target.value as Settings["encryption"] })}>
                      <option value="enabled">Enabled</option>
                      <option value="forced">Forced</option>
                      <option value="disabled">Disabled</option>
                    </select>
                  </label>
                </div>
                <label className="check"><input type="checkbox" checked={draft.random_port} onChange={(event) => setDraft({ ...draft, random_port: event.target.checked })} /> Random port on start</label>
                <label className="check"><input type="checkbox" checked={draft.upnp} onChange={(event) => setDraft({ ...draft, upnp: event.target.checked })} /> UPnP port mapping</label>
                <label className="check"><input type="checkbox" checked={draft.natpmp} onChange={(event) => setDraft({ ...draft, natpmp: event.target.checked })} /> NAT-PMP</label>
                <label className="check"><input type="checkbox" checked={draft.dht} onChange={(event) => setDraft({ ...draft, dht: event.target.checked })} /> DHT</label>
                <label className="check"><input type="checkbox" checked={draft.pex} onChange={(event) => setDraft({ ...draft, pex: event.target.checked })} /> Peer exchange</label>
                <label className="check"><input type="checkbox" checked={draft.lsd} onChange={(event) => setDraft({ ...draft, lsd: event.target.checked })} /> Local peer discovery</label>
                <label className="check"><input type="checkbox" checked={draft.utp} onChange={(event) => setDraft({ ...draft, utp: event.target.checked })} /> µTP</label>
                <label className="check"><input type="checkbox" checked={draft.vpn_enabled} onChange={(event) => setDraft({ ...draft, vpn_enabled: event.target.checked })} /> VPN</label>
                <p className="muted">VPN sends only BitTorrent traffic through the server below and stops direct peers, DHT, µTP, and port mapping. Other programs stay on your normal connection. A public free relay can see the transfers it carries, so use the SOCKS or HTTP proxy from a VPN you trust.</p>
                <label className="field"><span>VPN server</span>
                  <select value={draft.proxy_type} onChange={(event) => setDraft({ ...draft, proxy_type: event.target.value as Settings["proxy_type"] })}>
                    <option value="none">None</option>
                    <option value="socks4">SOCKS4</option>
                    <option value="socks5">SOCKS5</option>
                    <option value="socks5_pw">SOCKS5 with password</option>
                    <option value="http">HTTP</option>
                    <option value="http_pw">HTTP with password</option>
                  </select>
                </label>
                <div className="grid-2">
                  <label className="field"><span>Host</span><input value={draft.proxy_host} onChange={(event) => setDraft({ ...draft, proxy_host: event.target.value })} /></label>
                  <label className="field"><span>Port</span><input type="number" value={draft.proxy_port} onChange={(event) => setDraft({ ...draft, proxy_port: Number(event.target.value) })} /></label>
                  <label className="field"><span>Username</span><input value={draft.proxy_username} onChange={(event) => setDraft({ ...draft, proxy_username: event.target.value })} /></label>
                  <label className="field"><span>Password</span><input type="password" value={draft.proxy_password} onChange={(event) => setDraft({ ...draft, proxy_password: event.target.value })} /></label>
                </div>
              </>
            )}
            {section === "Bandwidth" && (
              <div className="grid-2">
                <label className="field"><span>Download limit KB/s</span><input type="number" min={0} value={draft.download_limit} onChange={(event) => setDraft({ ...draft, download_limit: Number(event.target.value) })} /></label>
                <label className="field"><span>Upload limit KB/s</span><input type="number" min={0} value={draft.upload_limit} onChange={(event) => setDraft({ ...draft, upload_limit: Number(event.target.value) })} /></label>
                <label className="field"><span>Limited download KB/s</span><input type="number" min={0} value={draft.alt_download_limit} onChange={(event) => setDraft({ ...draft, alt_download_limit: Number(event.target.value) })} /></label>
                <label className="field"><span>Limited upload KB/s</span><input type="number" min={0} value={draft.alt_upload_limit} onChange={(event) => setDraft({ ...draft, alt_upload_limit: Number(event.target.value) })} /></label>
                <p className="muted">Zero means unlimited. Limited rates are used by the scheduler.</p>
              </div>
            )}
            {section === "Queue" && (
              <div className="grid-2">
                <label className="field"><span>Max active downloads</span><input type="number" min={1} value={draft.max_active_downloads} onChange={(event) => setDraft({ ...draft, max_active_downloads: Number(event.target.value) })} /></label>
                <label className="field"><span>Max active seeds</span><input type="number" min={1} value={draft.max_active_seeds} onChange={(event) => setDraft({ ...draft, max_active_seeds: Number(event.target.value) })} /></label>
                <label className="field"><span>Max active torrents</span><input type="number" min={1} value={draft.max_active} onChange={(event) => setDraft({ ...draft, max_active: Number(event.target.value) })} /></label>
                <label className="field"><span>Global connections</span><input type="number" min={2} value={draft.max_connections} onChange={(event) => setDraft({ ...draft, max_connections: Number(event.target.value) })} /></label>
                <label className="field"><span>Upload slots</span><input type="number" min={1} value={draft.max_uploads} onChange={(event) => setDraft({ ...draft, max_uploads: Number(event.target.value) })} /></label>
                <label className="field"><span>Seed ratio</span><input type="number" min={0} step="0.1" value={draft.seed_ratio} onChange={(event) => setDraft({ ...draft, seed_ratio: Number(event.target.value) })} /></label>
                <label className="field"><span>Seed time, minutes</span><input type="number" min={0} value={draft.seed_minutes} onChange={(event) => setDraft({ ...draft, seed_minutes: Number(event.target.value) })} /></label>
                <p className="muted">A seed ratio or time of zero does not stop seeding. Force Start ignores both.</p>
              </div>
            )}
            {section === "Scheduler" && (
              <>
                <label className="check"><input type="checkbox" checked={draft.scheduler_enabled} onChange={(event) => setDraft({ ...draft, scheduler_enabled: event.target.checked })} /> Enable scheduler</label>
                <p className="muted">Click or drag. Empty is full speed, amber is the limited rate, red stops transfers. Monday is the first row.</p>
                <div className="sched-scroll">
                <div className="hours"><span />{Array.from({ length: 24 }, (_, hour) => <span key={hour}>{hour % 3 === 0 ? hour : ""}</span>)}</div>
                <div className="scheduler">
                  {DAYS.map((day, dayIndex) => (
                    <div className="sched-row" key={day}>
                      <span>{day}</span>
                      {Array.from({ length: 24 }, (_, hour) => {
                        const index = dayIndex * 24 + hour;
                        const mode = draft.scheduler[index] || "F";
                        return (
                          <button
                            key={hour}
                            type="button"
                            className="cell"
                            data-mode={mode}
                            aria-label={`${day} ${hour}:00 ${mode === "F" ? "full" : mode === "L" ? "limited" : "stopped"}`}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              const next = mode === "F" ? "L" : mode === "L" ? "S" : "F";
                              paint.current = next;
                              writeCell(index, next);
                            }}
                            onMouseEnter={() => { if (paint.current) writeCell(index, paint.current); }}
                          />
                        );
                      })}
                    </div>
                  ))}
                </div>
                </div>
              </>
            )}
            {section === "RSS" && (
              <>
                <p className="muted">Feeds are checked every 15 minutes. A filter is a regular expression matched against the title. Leave it blank to take every torrent in the feed.</p>
                {draft.rss_feeds.map((feed, index) => (
                  <div className="feed" key={feed.id}>
                    <input className="inline" placeholder="https://example.com/feed.xml" value={feed.url} onChange={(event) => updateFeed(index, { url: event.target.value })} />
                    <input className="inline" placeholder="Title filter" value={feed.filter} onChange={(event) => updateFeed(index, { filter: event.target.value })} />
                    <input className="inline" placeholder="Label" value={feed.label} onChange={(event) => updateFeed(index, { label: event.target.value })} />
                    <button className="btn-ghost" type="button" onClick={() => setDraft({ ...draft, rss_feeds: draft.rss_feeds.filter((item) => item.id !== feed.id) })}>Remove</button>
                    <label className="check"><input type="checkbox" checked={feed.enabled} onChange={(event) => updateFeed(index, { enabled: event.target.checked })} /> Enabled</label>
                    <input className="inline" placeholder="Save path (optional)" value={feed.save_path} onChange={(event) => updateFeed(index, { save_path: event.target.value })} />
                  </div>
                ))}
                <div className="controls">
                  <button className="btn" type="button" onClick={() => setDraft({ ...draft, rss_feeds: [...draft.rss_feeds, { id: crypto.randomUUID(), url: "", enabled: true, filter: "", label: "", save_path: "", seen: [] }] })}>Add feed</button>
                  <button className="btn" type="button" onClick={() => void api.refreshRss().then((result) => setError(result.added ? `Added ${result.added}` : "No new items")).catch((reason: Error) => setError(reason.message))}>Check now</button>
                </div>
              </>
            )}
            {section === "Advanced" && (
              <>
                <label className="check"><input type="checkbox" checked={draft.anonymous_mode} onChange={(event) => setDraft({ ...draft, anonymous_mode: event.target.checked })} /> Anonymous mode</label>
                <label className="check"><input type="checkbox" checked={draft.announce_to_all_trackers} onChange={(event) => setDraft({ ...draft, announce_to_all_trackers: event.target.checked })} /> Announce to all trackers</label>
                <label className="field"><span>Disk cache MB</span><input type="number" min={1} value={draft.disk_cache_mb} onChange={(event) => setDraft({ ...draft, disk_cache_mb: Number(event.target.value) })} /></label>
                <label className="field"><span>Web UI password</span><input type="password" value={draft.web_password} onChange={(event) => setDraft({ ...draft, web_password: event.target.value })} placeholder="Empty leaves the local UI open" /></label>
                <label className="field"><span>Update repository</span><input value={draft.update_repo} onChange={(event) => setDraft({ ...draft, update_repo: event.target.value })} placeholder="owner/fraghost" spellCheck={false} /></label>
                <p className="muted">After a push publishes a newer installer, Update appears in the footer.</p>
                <label className="field">
                  <span>IP blocklist</span>
                  <span style={{ display: "flex", gap: 8 }}>
                    <input value={draft.blocklist_path} onChange={(event) => setDraft({ ...draft, blocklist_path: event.target.value })} placeholder="C:\\filters\\ipfilter.dat" />
                    <button className="btn" type="button" onClick={() => void api.blocklist(draft.blocklist_path).then((result) => setError(`Blocked ${result.ranges} ranges`)).catch((reason: Error) => setError(reason.message))}>Load</button>
                  </span>
                </label>
                <pre className="log">{logs.join("\n") || "No log lines yet."}</pre>
              </>
            )}
            {error && <p style={{ color: "var(--accent)", margin: 0 }}>{error}</p>}
          </div>
        </div>
        <div className="dialog-actions">
          <button className="btn-ghost" type="button" onClick={onClose}>Cancel</button>
          <button className="btn-primary" type="submit">Save</button>
        </div>
      </form>
    </div>
  );

  function updateFeed(index: number, patch: Partial<Settings["rss_feeds"][number]>) {
    setDraft({
      ...draft,
      rss_feeds: draft.rss_feeds.map((feed, feedIndex) => (feedIndex === index ? { ...feed, ...patch } : feed)),
    });
  }
}

function PathField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <span style={{ display: "flex", gap: 8 }}>
        <input value={value} onChange={(event) => onChange(event.target.value)} />
        <button className="btn" type="button" onClick={() => void pickFolder().then((folder) => folder && onChange(folder))}>Browse</button>
      </span>
    </label>
  );
}
