import { useId } from 'react'

/** Img3D brand mark (inline SVG, no network). */
export function Logo({ size = 28 }: { size?: number }) {
  const gradientId = useId()
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden focusable="false">
      <defs>
        <linearGradient id={gradientId} x1="8" y1="6" x2="56" y2="58" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#8b6cff" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="#141824" />
      <path
        d="M32 9 52 20.5v23L32 55 12 43.5v-23Z"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="3.5"
        strokeLinejoin="round"
      />
      <path
        d="M12 20.5 32 32l20-11.5M32 32v23"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="3.5"
        strokeLinejoin="round"
      />
      <path d="M32 9 52 20.5 32 32 12 20.5Z" fill={`url(#${gradientId})`} opacity=".28" />
    </svg>
  )
}
