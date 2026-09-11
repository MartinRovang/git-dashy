"""The prose and the keys agree.

A key list lives in four places — the main loop, the settings table, the README table and the
README's prose — and a doc that names a key the code dropped is worse than one that names none,
because a reader acts on it. Nothing here reads a fixture; the source files ARE the fixture, so
these fail the moment the two sides drift.
"""
import re
import os

from dashy.core import memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts)) as f:
		return f.read()


def handled():
	"""Every single-character key the dashboard acts on: the main loop's, plus the settings table's.

	ponytail: the MAIN loop, deliberately. Keys that only work inside a screen the main loop opened —
	`t`/`x` in share, `t`/`x`/`s` in drafts, `o`/`x` in bind, `n`/`a` in teams — are out of scope here
	and are described in the second cell of a Keys row rather than given one. Widening this to the
	screen functions means parsing which of them the README's prose is talking about, and a parity test
	that guesses at its own subject is worse than one with a stated edge.

	ponytail: the eight settings keys are dispatched by one `chr(k) in settings(state)` branch, so the
	table is the key list and reading only ord() would report them as undocumented.
	"""
	src = read("dashy", "ui", "screen.py")
	loop = src[src.index("\ndef main("):]
	table = src[src.index("def settings(state)"):src.index("def set_theme")]
	named = {"↑": "KEY_UP", "↓": "KEY_DOWN"}  # by name, not ord(); Enter is spelled as a word
	return (set(re.findall(r'ord\("(\S)"\)', loop)) | set(re.findall(r'"(\w)": \(', table))
	        | {k for k, name in named.items() if f"curses.{name}" in loop})


def keys_section():
	"""README's "## Keys" section — the table and the prose under it, nothing else.

	ponytail: this section, not the whole file. Deleting a key's row was not caught while the test read
	the whole README, because a letter that also appears in an unrelated backtick somewhere in 400 lines
	still counted as documented. The section is where a reader looks the key up, so it is where the
	guarantee has to hold.
	"""
	md = read("README.md")
	start = md.index("\n## Keys\n")
	return md[start:md.index("\n## ", start + 1)]


def table_rows():
	"""Every single-char key the Keys table gives a row: the whole KEY CELL, not only its first entry.

	ponytail: the key cell only. Its whole contents, because `N`, `D`, `c` and `2` share a row rather
	than having one of their own; and not the description cell, or `o` counts as documented by the `b`
	row's prose and deleting its own row passes.
	"""
	# ponytail: split on the pipe rather than matched. The key cell holds "`j` / `k`, `↑` / `↓`" and
	# "`1` `2` `Tab`" as well as a bare "`e`", and a pattern that covers all of those is a pattern
	# nobody can check. Field 1 of the row is the key cell by definition of the table.
	cells = [l.split("|")[1] for l in keys_section().splitlines()
	         if l.startswith("| `") and l.count("|") >= 3]
	return {k for c in cells for k in re.findall(r"`(\S)`", c)}


def prose_keys():
	"""Keys the paragraphs under the table document: the run of backticked keys each line OPENS with.

	ponytail: the line's SUBJECT, not every key it mentions. Reading the whole paragraph counted `q`
	as documented because the dropdown line ends "`Esc` (or `q`) keeps", which is a different `q` from
	the one that quits — so deleting the quit row passed. The same went for `t`, `s`, `m`, `d`, `e`,
	`i`, `j` and `k`.
	"""
	out = set()
	for line in keys_section().splitlines():
		if line.startswith("`"):
			out |= set(re.findall(r"`(\S)`", re.match(r"^((?:`\S`[,/ ]*)+)", line).group(1)))
	return out


def test_every_dashboard_key_is_named_in_the_keys_section():
	missing = handled() - table_rows() - prose_keys()
	assert not missing, f"the dashboard acts on {sorted(missing)}; the Keys section gives them no row"


def test_every_key_the_readme_table_promises_still_exists():
	gone = table_rows() - handled()
	assert not gone, f"the Keys table promises {sorted(gone)}; nothing in the dashboard acts on them"


def test_the_numbers_the_spec_quotes_are_the_numbers_the_code_uses():
	"""The spec's constants must match memory.py. §3.1 said NEAR was 0.82 while it had been 0.88 for
	weeks, and §7 said 0.88 two hundred lines away."""
	spec = read("docs", "memory.md")
	assert f"`NEAR` ({memory.NEAR})" in spec
	assert f"| `NEAR` | {memory.NEAR} |" in spec
	assert f"| `PROMOTE_AT` | {memory.PROMOTE_AT} |" in spec
	assert f"`PROMOTE_AT = {memory.PROMOTE_AT}` is a guess" in spec


def test_usage_synopsis_and_the_command_dispatch_are_the_same_list():
	src = read("dashy", "cli.py")
	usage = src[src.index('USAGE = f"""'):]
	usage = usage[:usage.index('"""', 12)]
	synopsis = usage[usage.index("Usage:"):]
	synopsis = synopsis[:synopsis.index("\n\n")]  # the block, not the per-command prose below it
	# ponytail: anchored to line starts. "gitdashy ships" appears in prose further down USAGE and is a
	# sentence, not a command — a loose search for "gitdashy <word>" invented a command and failed here.
	named = {m.group(1) for l in synopsis.splitlines()
	         if (m := re.match(r"\s*(?:Usage:\s*)?gitdashy ([a-z][\w-]*)", l))}
	assert named == set(re.findall(r'argv\[1\] == "([\w-]+)"', src))
