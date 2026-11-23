import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        neutral: "border-transparent bg-secondary text-secondary-foreground",
        success: "border-transparent bg-green-600/15 text-green-700 dark:text-green-400",
        danger: "border-transparent bg-destructive/15 text-destructive",
        warning: "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400",
        info: "border-transparent bg-blue-600/15 text-blue-700 dark:text-blue-400",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
