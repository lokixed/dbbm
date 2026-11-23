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
      viewBox="16.5 16.5 87 87"
      className={className}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <rect x="30" y="30" width="60" height="60" fill="#1d3fb8" transform="rotate(45 60 60)" />
      <rect x="30" y="28" width="36" height="36" fill="#2563eb" transform="rotate(45 48 46)" />
      <rect x="58" y="60" width="32" height="32" fill="#e0f2fe" transform="rotate(45 74 76)" />
    </svg>
  );
}
