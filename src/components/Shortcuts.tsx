import { close, open } from '../modals'

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
      ['⏎', 'Open the detail pane'],
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
      ['f', 'Refresh now'],
      ['u', 'Update, when a newer release exists'],
      ['?', 'This sheet'],
      ['⎋', 'The menu: theme, notifications, quit'],
      ['q', 'Quit'],
    ],
  ],
]

/** Open the sheet. It goes on the modal stack, so Esc, the scrim click and the board's key guard
 *  all work the way every other dialog's do — and nothing can open underneath it and steal the keys. */
export function shortcuts() {
  const m = open({
    title: 'Keyboard shortcuts',
    sub: 'every key the board answers to',
    side: true,
    body: () => (
      <div className="keys">
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
    ),
    foot: [['Esc', 'close', () => close(m)]],
  })
  m.keys = { Escape: () => close(m), '?': () => close(m) }
}
