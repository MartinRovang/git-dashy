/** Every key the dashboard answers to, grouped the way you reach for them.
 *
 * ponytail: a sheet rather than a row of hints. The footer carries the four keys you need while
 * reading the board; this is the list you open when you want to know what else is there, and it is
 * the only place the whole set is written down in the app rather than in the README.
 */
const KEYS: [string, [string, string][]][] = [
  [
    'Navigate',
    [
      ['j / k', 'Move between PRs'],
      ['[ / ]', 'Previous / next queue'],
      ['/', 'Filter the list'],
      ['⏎', 'Open the pane'],
      ['o', 'Open in browser'],
      ['G', 'Graph view'],
    ],
  ],
  [
    'Review',
    [
      ['r', 'Review with the agent'],
      ['p', 'Pre-review your own PR'],
      ['Y', 'Open the pre-review'],
      ['y', 'Copy the PR URL'],
      ['+', 'Request a review'],
      ['v', 'Read the full review'],
    ],
  ],
  [
    'Agent',
    [
      ['a', 'Toggle auto-run'],
      ['m', 'Model'],
      ['d', 'Depth'],
      ['e', 'Effort'],
      ['x', 'Voices'],
      ['h', 'Hunters'],
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
    ],
  ],
  [
    'App',
    [
      ['S', 'Collapse the sidebar'],
      ['f', 'Refresh now'],
      ['u', 'Check for updates'],
      ['?', 'This sheet'],
      ['⎋', 'Close overlays'],
      ['q', 'Quit'],
    ],
  ],
]

export function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <>
      <div className="scrim on" onClick={onClose} />
      <div className="sheet on" role="dialog" aria-label="Keyboard shortcuts">
        <div className="sheeth">
          <b>Keyboard shortcuts</b>
          <button className="iconbtn" onClick={onClose} title="close (esc)">
            ✕
          </button>
        </div>
        <div className="keys scroll">
          {KEYS.map(([group, rows]) => (
            <div className="kgrp" key={group}>
              <span>{group}</span>
              {rows.map(([k, what]) => (
                <div className="krow" key={k}>
                  <kbd>{k}</kbd>
                  <s>{what}</s>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
