import type { JSX } from "react";

export type GlyphName =
  | "add"
  | "link"
  | "create"
  | "play"
  | "pause"
  | "stop"
  | "force"
  | "up"
  | "down"
  | "folder"
  | "trash"
  | "gear"
  | "copy"
  | "recheck"
  | "announce"
  | "minimize"
  | "maximize"
  | "fullscreen"
  | "close";

export function Glyph({ name }: { name: GlyphName }) {
  return (
    <svg className="glyph" viewBox="0 0 16 16" aria-hidden="true">
      {draw[name]}
    </svg>
  );
}

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const draw: Record<GlyphName, JSX.Element> = {
  add: <path {...stroke} d="M8 2.5v11M2.5 8h11" />,
  link: <path {...stroke} d="M6.2 9.8 4.4 11.6a2.6 2.6 0 0 1-3.7-3.7L2.5 6.1M9.8 6.2l1.8-1.8a2.6 2.6 0 0 1 3.7 3.7L13.5 9.9M5.6 10.4l4.8-4.8" />,
  create: (
    <>
      <path {...stroke} d="M3.5 2.5h6L13 6v7.5h-9.5z" />
      <path {...stroke} d="M9.2 2.5V6H13M6 9.2h4M8 7.2v4" />
    </>
  ),
  play: <path fill="currentColor" d="M4.2 2.4v11.2L13 8z" />,
  pause: <path fill="currentColor" d="M3.2 2.4h3.2v11.2H3.2zM9.6 2.4h3.2v11.2H9.6z" />,
  stop: <path fill="currentColor" d="M3.2 3.2h9.6v9.6H3.2z" />,
  force: <path {...stroke} d="M9 1.6 4.2 8.2H8L7 14.4 12.2 7.2H8.2z" />,
  up: <path {...stroke} d="M8 13.2V3.2M3.6 7.2 8 2.8l4.4 4.4" />,
  down: <path {...stroke} d="M8 2.8v10M3.6 8.8 8 13.2l4.4-4.4" />,
  folder: <path {...stroke} d="M2.2 4.2h4l1.4 1.6h6.2v7.2H2.2z" />,
  trash: (
    <>
      <path {...stroke} d="M3.2 4.4h9.6M6.2 4.4V2.8h3.6v1.6M4.4 4.4l.6 8.4h5.9l.6-8.4" />
    </>
  ),
  gear: (
    <>
      <circle {...stroke} cx="8" cy="8" r="2.2" />
      <path {...stroke} d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1" />
    </>
  ),
  copy: (
    <>
      <rect {...stroke} x="5.2" y="5.2" width="7.6" height="7.6" rx="1.2" />
      <path {...stroke} d="M10.6 5.2V3.6A1.2 1.2 0 0 0 9.4 2.4H3.8A1.2 1.2 0 0 0 2.6 3.6v5.6a1.2 1.2 0 0 0 1.2 1.2h1.4" />
    </>
  ),
  recheck: <path {...stroke} d="M3 8.2A5 5 0 1 0 4.2 4.4M3 2.8v2.8h2.8" />,
  announce: <path {...stroke} d="M3.2 6.2h2.2l4-2.4v8.4l-4-2.4H3.2zM11.2 6.4a2.6 2.6 0 0 1 0 3.2" />,
  minimize: <path {...stroke} d="M3 11.5h10" />,
  maximize: <rect {...stroke} x="3.2" y="3.2" width="9.6" height="9.6" rx="1" />,
  fullscreen: <path {...stroke} d="M3.2 6.2V3.4h2.8M10 3.4h2.8v2.8M13 9.8v2.8h-2.8M6 12.6H3.2V9.8" />,
  close: <path {...stroke} d="M3.6 3.6 12.4 12.4M12.4 3.6 3.6 12.4" />,
};
