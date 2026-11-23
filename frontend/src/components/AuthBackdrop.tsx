// Decorative background for the sign-in and setup screens: overlapping diagonal tiles in blue, black
// and white. Purely visual, so it is hidden from assistive technology and ignores clicks.
// The parent must be `relative isolate` so the backdrop sits behind its content.
export function AuthBackdrop() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      className="pointer-events-none absolute inset-0 -z-10 h-full w-full"
      viewBox="0 0 1000 600"
      preserveAspectRatio="xMidYMid slice"
    >
      <defs>
        <linearGradient id="tileNight" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0a1a5c" />
          <stop offset="1" stopColor="#02040f" />
        </linearGradient>
        <linearGradient id="tileDeep" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#12307f" />
          <stop offset="1" stopColor="#050c2e" />
        </linearGradient>
        <linearGradient id="tileBright" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0b2cff" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
        <linearGradient id="tileSky" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#ffffff" />
        </linearGradient>
        <linearGradient id="tileGlow" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#1d4ed8" />
          <stop offset="1" stopColor="#7dd3fc" />
        </linearGradient>
        <linearGradient id="tileFade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#1e3a8a" stopOpacity="0.9" />
          <stop offset="1" stopColor="#020617" stopOpacity="0.9" />
        </linearGradient>
      </defs>

      {/* Everything is drawn square to the page, then turned 45 degrees about the centre. */}
      <g transform="rotate(45 500 300)">
        <g className="auth-tile auth-tile-a">
          <rect x="-500" y="170" width="1100" height="520" fill="url(#tileNight)" />
        </g>
        <g className="auth-tile auth-tile-b">
          <rect x="60" y="-250" width="380" height="400" fill="url(#tileBright)" />
        </g>
        <g className="auth-tile auth-tile-c">
          <rect x="430" y="-420" width="300" height="420" fill="url(#tileSky)" opacity="0.95" />
        </g>
        <g className="auth-tile auth-tile-a">
          <rect x="470" y="-40" width="470" height="430" fill="url(#tileDeep)" />
        </g>
        <g className="auth-tile auth-tile-b">
          <rect x="760" y="150" width="330" height="330" fill="url(#tileFade)" />
        </g>
        <g className="auth-tile auth-tile-c">
          <rect x="560" y="380" width="360" height="300" fill="url(#tileGlow)" opacity="0.9" />
        </g>
        <g className="auth-tile auth-tile-b">
          <rect x="240" y="300" width="150" height="150" fill="url(#tileBright)" opacity="0.7" />
        </g>
      </g>
    </svg>
  );
}
