import React from 'react';

/**
 * Voiceym's compact mark: five rounded sound bars hanging from one line, their
 * lengths tracing the letter V. The desktop icon uses the same bars on a dark
 * tile; app chrome keeps it transparent so it stays crisp at titlebar sizes and
 * in every theme.
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
        <rect x="11" y="15" width="6" height="12" rx="3" opacity="0.72" />
        <rect x="20" y="15" width="6" height="22" rx="3" opacity="0.86" />
        <rect x="29" y="15" width="6" height="34" rx="3" />
        <rect x="38" y="15" width="6" height="22" rx="3" opacity="0.86" />
        <rect x="47" y="15" width="6" height="12" rx="3" opacity="0.72" />
      </g>
    </svg>
  );
}
