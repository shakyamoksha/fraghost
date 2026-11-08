export function formatBytes(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.max(0, value);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const digits = index === 0 || size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(digits)} ${units[index]}`;
}

export function formatSpeed(value: number): string {
  if (!value) return "0 B/s";
  return `${formatBytes(value)}/s`;
}

export function formatEta(seconds: number): string {
  if (seconds < 0 || !Number.isFinite(seconds)) return "∞";
  const total = Math.round(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function formatRatio(value: number | null, uploaded: number): string {
  if (value === null) return uploaded > 0 ? "∞" : "0.000";
  return value.toFixed(3);
}

export function formatPercent(progress: number): string {
  const percent = progress * 100;
  return `${percent >= 100 ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

export function formatWhen(unix: number): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}

export function formatDuration(seconds: number): string {
  if (!seconds) return "—";
  return formatEta(seconds);
}

export function swarm(connected: number, total: number): string {
  return `${connected} (${total})`;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "downloading":
      return "Downloading";
    case "seeding":
      return "Seeding";
    case "paused":
      return "Paused";
    case "stopped":
      return "Stopped";
    case "queued":
      return "Queued";
    case "checking":
      return "Checking";
    case "metadata":
      return "Fetching metadata";
    case "error":
      return "Error";
    case "finished":
      return "Finished";
    default:
      return state;
  }
}

export function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

export const PRIORITIES: { value: number; label: string }[] = [
  { value: 0, label: "Skip" },
  { value: 1, label: "Low" },
  { value: 4, label: "Normal" },
  { value: 6, label: "High" },
  { value: 7, label: "Max" },
];
