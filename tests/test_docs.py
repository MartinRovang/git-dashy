"""The prose and the keys agree.

A key list lives in four places — the main loop, the settings table, the README table and the
README's prose — and a doc that names a key the code dropped is worse than one that names none,
because a reader acts on it. Nothing here reads a fixture; the source files ARE the fixture, so
these fail the moment the two sides drift, which is the only moment anyone would want to know.
"""
import re
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts)) as f:
		return f.read()


def handled():
	"""Every single-character key the dashboard acts on: the main loop's, plus the settings table's.

	ponytail: the settings keys are NOT in the main loop as ord() calls — one `chr(k) in settings(state)`
	branch dispatches all eight, which is the shape that makes the table the key list. Reading only the
	ord() calls would report d/e/h/i/m/s/t/x as undocumented and the test would be noise.
	"""
	src = read("dashy", "ui", "screen.py")
	loop = src[src.index("\ndef main("):]
	table = src[src.index("def settings(state)"):src.index("def set_theme")]
	return set(re.findall(r'ord\("(\S)"\)', loop)) | set(re.findall(r'"(\w)": \(', table))


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
	"""The single-char keys the README's key table gives a row of their own."""
	rows = re.findall(r"^\| (`\S`[^|]*?) \| ", keys_section(), re.M)
	return {k for r in rows for k in re.findall(r"^`(\S)`", r)}


def test_every_dashboard_key_is_named_in_the_keys_section():
	named = set(re.findall(r"`(\S)`", keys_section()))
	assert not (handled() - named), "keys the dashboard acts on that the Keys section never names"


def test_every_key_the_readme_table_promises_still_exists():
	assert not (table_rows() - handled()), "README rows for keys the dashboard no longer acts on"


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
