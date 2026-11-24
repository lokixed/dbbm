// The DBBM mark: three overlapping diamond tiles. Logo draws them still and LogoLoader animates
// them, so the shapes live here once.
export interface LogoTile {
  x: number;
  y: number;
  size: number;
  fill: string;
  transform: string;
}

export const LOGO_VIEWBOX = "16.5 16.5 87 87";

export const LOGO_TILES: readonly LogoTile[] = [
  { x: 30, y: 30, size: 60, fill: "#1d3fb8", transform: "rotate(45 60 60)" },
  { x: 30, y: 28, size: 36, fill: "#2563eb", transform: "rotate(45 48 46)" },
  { x: 58, y: 60, size: 32, fill: "#e0f2fe", transform: "rotate(45 74 76)" },
];
