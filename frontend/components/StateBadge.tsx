"use client";

/** 具身状态徽标（待机 / 聆听中 / 思考中 / 说话中）。 */

import { AVATAR_STATE_LABELS, type AvatarState } from "@/lib/types";

const STATE_STYLE: Record<AvatarState, string> = {
  idle: "bg-surface-hover text-ink-muted",
  listen: "bg-accent-soft text-accent-text ring-1 ring-accent/30",
  think: "bg-warning-soft text-warning-text ring-1 ring-warning/30",
  speak: "bg-success-soft text-success-text ring-1 ring-success/30",
};

export function StateBadge({ state }: { state: AvatarState }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${STATE_STYLE[state]}`}
    >
      {state === "think" && (
        <span className="flex gap-0.5">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1 w-1 animate-bounce rounded-full bg-current"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </span>
      )}
      {state === "speak" && (
        <span className="flex items-end gap-0.5">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-2 w-0.5 origin-bottom animate-sound-wave rounded-sm bg-current"
              style={{ animationDelay: `${i * 0.12}s` }}
            />
          ))}
        </span>
      )}
      {AVATAR_STATE_LABELS[state]}
    </span>
  );
}
