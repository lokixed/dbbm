import type { Config } from "tailwindcss";
import xedonium from "xedonium/tailwind-preset";

export default {
  presets: [xedonium],
  content: ["./index.html", "./src/**/*.{ts,tsx}", "./node_modules/xedonium/dist/**/*.js"],
} satisfies Config;
