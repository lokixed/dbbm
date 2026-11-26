// Sets <html data-theme> before the first paint, so a dark page does not flash white on load.
// Same rules as the ThemeProvider in App.tsx (storageKey "dbbm-theme", defaultTheme "system"):
// the saved choice, else the device setting. A separate file because the CSP forbids inline
// scripts. Keep in sync with xedonium's getThemeInitScript if the provider's options change.
(function () {
  try {
    var saved = localStorage.getItem("dbbm-theme");
    var mode = saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
    var theme =
      mode === "light" || mode === "dark"
        ? mode
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    // No storage or matchMedia: the provider sets the theme once the app loads.
  }
})();
