import React from 'react';

/**
 * Voiceym's compact mark: seven rounded sound bars that rise and fall like a
 * voice and together form the letter V. The desktop icon uses the same bars on
 * a dark tile; app chrome keeps it transparent so it stays crisp at titlebar
 * sizes and in every theme.
 */
export default function VoiceymMark({ className = '', title, ...props }) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      {...props}
    >
      {title ? <title>{title}</title> : null}
      <g fill="currentColor">
        <rect x="8.5" y="12" width="5" height="12" rx="2.5" opacity="0.72" />
        <rect x="15.5" y="18" width="5" height="17" rx="2.5" opacity="0.82" />
        <rect x="22.5" y="28.5" width="5" height="13" rx="2.5" opacity="0.92" />
        <rect x="29.5" y="36" width="5" height="16" rx="2.5" />
        <rect x="36.5" y="28.5" width="5" height="13" rx="2.5" opacity="0.92" />
        <rect x="43.5" y="18" width="5" height="17" rx="2.5" opacity="0.82" />
        <rect x="50.5" y="12" width="5" height="12" rx="2.5" opacity="0.72" />
      </g>
    </svg>
  );
}
