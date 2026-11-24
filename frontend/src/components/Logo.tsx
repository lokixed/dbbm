import { LOGO_TILES, LOGO_VIEWBOX } from "@/lib/logo";

// The DBBM mark: three overlapping diamond tiles, no background. The same shapes are the
// favicon (public/favicon.svg) and echo the sign-in backdrop. Decorative by default; pass a `title`
// when the mark is the only thing naming the app.
interface LogoProps {
  size?: number;
  title?: string;
  className?: string;
}

export function Logo({ size = 32, title, className }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={LOGO_VIEWBOX}
      className={className}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {LOGO_TILES.map((tile) => (
        <rect key={tile.fill} x={tile.x} y={tile.y} width={tile.size} height={tile.size} fill={tile.fill} transform={tile.transform} />
      ))}
    </svg>
  );
}
