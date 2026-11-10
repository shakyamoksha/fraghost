export interface Torrent {
  id: string;
  name: string;
  progress: number;
  state: string;
  size: number;
  downloaded: number;
  uploaded: number;
  download_rate: number;
  upload_rate: number;
  eta: number;
  seeds_connected: number;
  seeds_total: number;
  peers_connected: number;
  peers_total: number;
  ratio: number | null;
  added_at: number;
  label: string;
  save_path: string;
  queue_position: number;
  sequential: boolean;
  first_last: boolean;
  super_seeding: boolean;
  force_start: boolean;
  has_metadata: boolean;
  error: string;
  manual: string;
}

export interface TorrentFile {
  index: number;
  path: string;
  size: number;
  priority: number;
  progress: number;
}

export interface TrackerRow {
  url: string;
  tier: number;
  status: string;
  seeds: number | null;
  peers: number | null;
  next_announce: number;
  message: string;
}

export interface PeerRow {
  address: string;
  client: string;
  progress: number;
  down_speed: number;
  up_speed: number;
  flags: string;
}

export interface TorrentDetail extends Torrent {
  comment: string;
  creator: string;
  piece_size: number;
  num_pieces: number;
  have_pieces: number;
  pieces: number[];
  availability: number | null;
  active_time: number;
  seeding_time: number;
  download_limit: number;
  upload_limit: number;
  ratio_limit: number | null;
  seed_minutes: number | null;
  magnet: string;
  files: TorrentFile[];
  trackers: TrackerRow[];
  peers: PeerRow[];
}

export interface SpeedSample {
  t: number;
  down: number;
  up: number;
}

export interface Feed {
  id: string;
  url: string;
  enabled: boolean;
  filter: string;
  exclude: string;
  label: string;
  save_path: string;
  seen: string[];
}

export interface Settings {
  save_path: string;
  completed_path: string;
  move_completed: boolean;
  watch_path: string;
  watch_enabled: boolean;
  show_add_dialog: boolean;
  preallocate: boolean;
  listen_port: number;
  random_port: boolean;
  dht: boolean;
  pex: boolean;
  lsd: boolean;
  utp: boolean;
  upnp: boolean;
  natpmp: boolean;
  encryption: "enabled" | "forced" | "disabled";
  anonymous_mode: boolean;
  announce_to_all_trackers: boolean;
  rate_limit_overhead: boolean;
  rate_limit_utp: boolean;
  limit_lan: boolean;
  dont_count_slow: boolean;
  max_connections: number;
  max_uploads: number;
  download_limit: number;
  upload_limit: number;
  alt_download_limit: number;
  alt_upload_limit: number;
  scheduler_enabled: boolean;
  scheduler: string;
  max_active_downloads: number;
  max_active_seeds: number;
  max_active: number;
  seed_ratio: number;
  seed_minutes: number;
  vpn_enabled: boolean;
  ghost_enabled: boolean;
  ghost_endpoint_id: string;
  proxy_type: "none" | "socks4" | "socks5" | "socks5_pw" | "http" | "http_pw";
  proxy_host: string;
  proxy_port: number;
  proxy_username: string;
  proxy_password: string;
  proxy_has_password?: boolean;
  proxy_peers: boolean;
  proxy_hostnames: boolean;
  web_password: string;
  blocklist_path: string;
  rss_feeds: Feed[];
  disk_cache_mb: number;
  confirm_remove: boolean;
  theme: "system" | "light" | "dark";
  update_mode: "off" | "notify" | "auto";
}

export interface UpdateStatus {
  current_version: string;
  current_build: number;
  repo: string;
  mode: "off" | "notify" | "auto";
  automatic: boolean;
  checked_at: number;
  available: boolean;
  latest_version: string;
  latest_build: number;
  notes: string;
  url: string;
  error: string;
}

export interface Stats {
  download_rate: number;
  upload_rate: number;
  dht_nodes: number;
  has_incoming: boolean;
  listen_port: number;
  external_ip: string;
  total_downloaded: number;
  total_uploaded: number;
  schedule: string;
  scheduler_enabled: boolean;
  dht: boolean;
  vpn: "off" | "on" | "blocked";
  vpn_label: string;
}

export interface GhostEndpoint {
  id: string;
  name: string;
  host: string;
  port: number;
  public_key: string;
  country: string;
  has_preshared: boolean;
  latency_ms?: number | null;
  score?: number;
  reachable?: boolean;
}

export interface GhostStatus {
  enabled: boolean;
  state: "DISABLED" | "INITIALIZING" | "DISCOVERING" | "CONNECTING" | "VERIFYING" | "PROTECTED" | "DEGRADED" | "BLOCKED" | "ERROR";
  reason: string;
  since: number;
  protected: boolean;
  endpoint: string;
  country: string;
  public_key?: string;
  selected?: string;
  endpoints?: GhostEndpoint[];
  next_retry?: number;
  kill_switch?: boolean;
  ipv6?: "blocked" | "open";
  dns?: "held" | "system";
  outside?: string[];
}

export interface Snapshot {
  torrents: Torrent[];
  stats: Stats;
  speed_history: SpeedSample[];
  labels: string[];
  detail: TorrentDetail | null;
  app: { name: string; version: string; build: number; engine: string };
  ghost?: GhostStatus;
}

export interface Preview {
  id: string;
  name: string;
  size: number;
  comment: string;
  creator: string;
  piece_size: number;
  num_pieces: number;
  files: TorrentFile[];
  trackers: string[];
}

export type SortKey =
  | "name"
  | "size"
  | "progress"
  | "state"
  | "seeds_connected"
  | "peers_connected"
  | "download_rate"
  | "upload_rate"
  | "eta"
  | "ratio"
  | "label"
  | "added_at"
  | "queue_position";
