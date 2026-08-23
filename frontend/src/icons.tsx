type Props = { size?: number; color?: string };

const base = (size: number, color: string) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: color,
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export const Mark = ({ size = 22 }: Props) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="#191817"
       strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5.5 14.5c-.4 3.6-.3 7.4.2 11 .1.6.5 1 1.1 1.1 6 .7 12.1.7 18.2 0 .6-.1 1-.5 1.1-1.1.5-3.6.6-7.4.2-11" />
    <path d="M5.6 14.4c6.9-.8 13.9-.8 20.8 0" />
    <path d="M16 14.3c-.2-2.9-.1-5.8.3-8.7" />
    <circle cx="16.2" cy="5.4" r="2.2" fill="#c0603d" stroke="none" />
  </svg>
);

export const Check = ({ size = 15, color = "#f6f1e9" }: Props) => (
  <svg {...base(size, color)}><path d="M20 6 9 17l-5-5" /></svg>
);

export const Chevron = ({ size = 11, color = "#191817" }: Props) => (
  <svg {...base(size, color)} strokeWidth={2.4}><path d="m6 9 6 6 6-6" /></svg>
);

export const Copy = ({ size = 13, color = "#b6ab98" }: Props) => (
  <svg {...base(size, color)}>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h10" />
  </svg>
);

export const Trash = ({ size = 15, color = "#f6f1e9" }: Props) => (
  <svg {...base(size, color)}>
    <path d="M4 7h16" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
    <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
    <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
  </svg>
);

export const Redo = ({ size = 15, color = "#f6f1e9" }: Props) => (
  <svg {...base(size, color)}>
    <path d="M21 12a9 9 0 1 1-2.6-6.4" />
    <path d="M21 4v5h-5" />
  </svg>
);

/** A folder with a plus: make one inside the folder this sits next to. */
export const NewFolder = ({ size = 13, color = "#7d7365" }: Props) => (
  <svg {...base(size, color)}>
    <path d="M20 12.5V8a1 1 0 0 0-1-1h-6.6a1 1 0 0 1-.8-.4L10 4.4a1 1 0 0 0-.8-.4H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h7" />
    <path d="M17.5 15v5M15 17.5h5" />
  </svg>
);

/**
 * An arrow coming down into a tray: add files here.
 *
 * Deliberately not another folder outline — beside NewFolder the two read as the same
 * button at 13px, and the difference between "put files in" and "make a folder" is not
 * something to squint at.
 */
export const ImportInto = ({ size = 13, color = "#7d7365" }: Props) => (
  <svg {...base(size, color)}>
    <path d="M12 3v11" />
    <path d="m7.5 9.5 4.5 4.5 4.5-4.5" />
    <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
  </svg>
);
