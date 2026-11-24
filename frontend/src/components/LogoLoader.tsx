import { useLocation } from "react-router-dom";
import { LOGO_TILES, LOGO_VIEWBOX } from "@/lib/logo";
import { pageTitle } from "@/lib/pageTitle";

// Page loader: the logo's tiles build up one after another, hold, then fade (styles in
// index.css), with the page's name and a short loading line under them. The page name defaults
// to the one for the current route. With `fullScreen` it fills the viewport.
interface LogoLoaderProps {
  title?: string;
  label?: string;
  fullScreen?: boolean;
  className?: string;
}

export function LogoLoader({ title, label = "Loading...", fullScreen = false, className = "" }: LogoLoaderProps) {
  const { pathname } = useLocation();
  const name = title ?? pageTitle(pathname);
  const size = fullScreen ? 72 : 56;
  const layout = fullScreen ? "min-h-screen bg-app-bg" : "py-10";

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex flex-col items-center justify-center gap-3 text-app-text ${layout} ${className}`.trim()}
    >
      <svg width={size} height={size} viewBox={LOGO_VIEWBOX} aria-hidden="true" focusable="false">
        {LOGO_TILES.map((tile) => (
          <g key={tile.fill} className="logo-loader-tile">
            <rect x={tile.x} y={tile.y} width={tile.size} height={tile.size} fill={tile.fill} transform={tile.transform} />
          </g>
        ))}
      </svg>
      <div className="text-center">
        <p className={`font-medium ${fullScreen ? "text-lg" : "text-base"}`}>{name}</p>
        <p className="text-sm text-app-muted">{label}</p>
      </div>
    </div>
  );
}
