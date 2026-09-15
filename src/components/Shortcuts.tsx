import { useFloatBox, type Box } from '../float'

/** Every key the dashboard answers to, grouped the way you reach for them.
 *
 * ponytail: this is the only place the whole set is written down in the app rather than in the
 * README, so a wrong or missing row is worse than not having it. Every entry below is one that
 * App.tsx's handleKey actually binds; the code-viewer rows are the ones it binds while the viewer
 * holds focus.
 */
const KEYS: [string, [string, string][]][] = [
  [
    'Navigate',
    [
      ['j / k', 'Move between PRs'],
      ['[ / ]', 'Previous / next queue'],
      ['/', 'Filter the list'],
      ['⏎', 'Show or hide the detail pane'],
      ['o', 'Open the PR in your browser'],
      ['right-click', 'Everything you can do to that PR'],
      ['G', 'Graph view'],
    ],
  ],
  [
    'Review',
    [
      ['r', 'Review with the agent'],
      ['p', 'Pre-review your own PR'],
      ['v', 'Read the full review'],
      ['y', 'Copy the PR URL'],
      ['+', 'Request a review'],
      ['Y', 'Read the one waiting to post'],
      ['␣', 'On a reviewed row: its older runs'],
    ],
  ],
  [
    'Agent',
    [
      ['a', 'Toggle auto-run on new PRs'],
      ['m', 'Model'],
      ['d', 'Depth'],
      ['e', 'Effort'],
      ['x', 'Voices'],
      ['h', 'Hunters'],
    ],
  ],
  [
    'View',
    [
      ['s', 'Summary lines'],
      ['t', 'History window'],
      ['i', 'Refresh interval'],
      ['D', 'Show or hide draft PRs'],
      ['O', 'Sources for the TEAM section'],
      ['F', 'Follow someone: a card of what they are working on'],
    ],
  ],
  [
    'Knowledge',
    [
      ['W', 'Waiting drafts'],
      ['P', 'What the team knows'],
      ['Z', 'Dream'],
      ['g', 'General memory'],
      ['n', 'This repo’s memory'],
      ['T', 'Teams'],
      ['b', 'Bind this repo to a team'],
      ['L / C', 'Move the memory or the team store'],
    ],
  ],
  [
    'Code viewer',
    [
      ['2 / ⇥', 'Open it on the selected PR'],
      ['j / k', 'Next / previous file'],
      ['D', 'Marks only, or the whole diff'],
      ['c', 'How much context'],
      ['⎋', 'Close it'],
    ],
  ],
  [
    'App',
    [
      ['S', 'Collapse the sidebar'],
      ['?', 'Keyboard shortcuts'],
      ['f', 'Refresh now'],
      ['u', 'Update, when a newer release exists'],
      ['⎋', 'The menu: theme, notifications, quit'],
      ['q', 'Quit'],
    ],
  ],
]

/** The keys, as a window you can leave open while you try them.
 *
 * ponytail: it was a modal drawer, which meant reading a key, closing it, pressing the key, and
 * opening it again. A floating window sits beside the board instead — it takes no keys of its own
 * beyond Escape, and it lives on the code viewer's layer, under any dialog, so nothing can open
 * behind it and steal the keyboard.
 */
const KEY = 'dashy-keys-box'

function initialBox(): Box {
  const W = window.innerWidth
  const H = window.innerHeight
  const w = Math.min(430, Math.round(W * 0.9))
  const h = Math.min(560, Math.round(H * 0.78))
  return { x: Math.max(8, W - w - 28), y: Math.max(8, Math.round((H - h) / 2)), w, h, max: false }
}

export function Shortcuts({ hints, onHints, onClose }: { hints: boolean; onHints: () => void; onClose: () => void }) {
  const { box, el, drag, style } = useFloatBox(KEY, initialBox, '.iconbtn, .fld')
  return (
    <div ref={el} className={`fw${box.max ? ' max' : ''}`} style={style} role="dialog" aria-label="Keyboard shortcuts">
      <div className="bar" title="drag to move, double-click to maximize" {...drag}>
        <b>Keyboard shortcuts</b>
        <div style={{ flex: 1 }} />
        <button className="iconbtn" onClick={onClose} title="close (esc)">
          ✕
          <kbd className="hint">esc</kbd>
        </button>
      </div>
      <div className="keys scroll">
        {/* the same switch as the rail's View group, here because this is where you read a key and
            then want to see it on the thing it belongs to */}
        <button className="fld" aria-pressed={hints} onClick={onHints}>
          <span>Show these keys on the buttons</span>
          <span className="sw" />
        </button>
        {KEYS.map(([group, rows]) => (
          <div className="kgrp" key={group}>
            <span>{group}</span>
            {rows.map(([k, what]) => (
              <div className="krow" key={`${group}/${k}`}>
                <kbd>{k}</kbd>
                <s>{what}</s>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
