"""Wiring the knowledge system into a machine, and into a repo.

ponytail: reviews read and write memory with no setup at all — that half needs no installing. What needs
installing is the half that reaches a regular coding session, which until now lived in one person's private
agent corpus, and so was unreachable for everyone else.
"""
import os
import subprocess

from .. import config
from . import knowledge, mirror

BEGIN, END = "<!-- gitdashy:begin -->", "<!-- gitdashy:end -->"
IMPORT = "@prs-memory/general.md"  # the line that says the wiring is already there, block or not
REGISTRY = os.path.expanduser("~/.prs_mirrors")  # "<into>\t<repo>" per line; the filesystem keeps the setting
BLOCK = f"""{BEGIN}
# Review memory

Cross-repo facts gitdashy's PR reviews have earned: yours first, then the team's. Written only once two
independent observations agreed, so trust them — but they are what the code turned out to be, not rules.
The team file does not exist until you are in a team, and a missing import is simply skipped. Facts about
one repo arrive separately, through that repo's own mirror.

{IMPORT}
@prs-team/general.md
{END}
"""


def claude_dir():
	return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def links():
	"""(link, target) for the two paths a session reads memory through."""
	d = claude_dir()
	return [(os.path.join(d, "prs-memory"), config.LOCAL_MEMORY),
	        (os.path.join(d, "prs-team"), os.path.join(config.TEAM, "memory"))]


def _read(p):
	try:
		return open(p).read()
	except FileNotFoundError:
		return ""


def _ours(link, target):
	return os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target)


def explain():
	"""What install would change on this machine, in the user's own paths. Returns report lines."""
	d, out = claude_dir(), []
	out.append("gitdashy install wires this machine so every agent session reads what your reviews learned.")
	out.append("")
	out.append("Reviews do not need this. They read memory through their own prompt and always have; this is")
	out.append("only so a coding session sees the same facts. It is additive, and reversible.")
	out.append("")
	out.append("It will:")
	for link, target in links():
		state = ("already correct" if _ours(link, target)
		         else "EXISTS, will be left alone" if os.path.lexists(link) else "new")
		out.append(f"  · symlink {knowledge.tilde(link)} -> {knowledge.tilde(target)}   [{state}]")
	md = os.path.join(d, "CLAUDE.md")
	out.append(f"  · append two imports to {knowledge.tilde(md)}, inside a marked block"
	           + ("   [already there]" if IMPORT in _read(md) else "   [new]"))
	out.append("")
	out.append("It will NOT: install hooks, touch settings.json, change any repo, or send anything anywhere.")
	out.append("Cross-repo facts load live through the symlink — the session reads the same file a review")
	out.append("writes, so nothing is copied and nothing goes stale. Per-repo facts are separate: run")
	out.append("`gitdashy init` inside a repo to add those, or leave them out.")
	out.append("")
	out.append("Reverse it any time with `gitdashy install --uninstall`, which removes only what it wrote.")
	return out


def apply(dry=False):
	"""Wire this machine so every session reads review memory. Returns report lines."""
	d = claude_dir()
	if not os.path.isdir(d):
		return [f"FAIL  no agent config directory at {knowledge.tilde(d)} — is claude installed?"]
	out, did = [], "would " if dry else ""
	for link, target in links():
		if _ours(link, target):
			out.append(f"ok    {knowledge.tilde(link)} already points at {knowledge.tilde(target)}")
		elif os.path.lexists(link):  # ponytail: never replace something we did not make
			out.append(f"SKIP  {knowledge.tilde(link)} exists and is not ours — left alone")
		else:
			out.append(f"{did}link  {knowledge.tilde(link)} -> {knowledge.tilde(target)}")
			if not dry:
				os.symlink(target, link)
	md = os.path.join(d, "CLAUDE.md")
	text = _read(md)
	if IMPORT in text:
		out.append(f"ok    {knowledge.tilde(md)} already imports the review memory")
	else:
		out.append(f"{did}add   the import block to {knowledge.tilde(md)}")
		if not dry:
			with open(md, "a") as f:
				f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + BLOCK)
	return out


def remove(dry=False):
	"""Undo exactly what apply() wrote, and say so when something is not ours to undo."""
	out, did = [], "would " if dry else ""
	for link, target in links():
		if _ours(link, target):
			out.append(f"{did}remove  {knowledge.tilde(link)}")
			if not dry:
				os.remove(link)
		elif os.path.lexists(link):
			out.append(f"SKIP    {knowledge.tilde(link)} is not the link we made — left alone")
		else:
			out.append(f"ok      {knowledge.tilde(link)} is not there")
	md = os.path.join(d := claude_dir(), "CLAUDE.md")
	text = _read(md)
	if BEGIN in text and END in text:
		out.append(f"{did}remove  the import block from {knowledge.tilde(md)}")
		if not dry:
			head, _, rest = text.partition(BEGIN)
			_, _, tail = rest.partition(END)
			with open(md, "w") as f:
				f.write(head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n"))
	elif IMPORT in text:
		out.append(f"SKIP    {knowledge.tilde(md)} imports the memory but not in a block we wrote — remove it by hand")
	else:
		out.append(f"ok      {knowledge.tilde(md)} does not import it")
	return out


def registered():
	"""[(into, repo)] for every repo mirror this machine keeps fresh."""
	out = []
	for line in _read(REGISTRY).splitlines():
		into, _, repo = line.partition("\t")
		if into.strip():
			out.append((into.strip(), repo.strip()))
	return out


def register(into, repo):
	"""Remember to refresh this mirror. Returns True when it was not already known."""
	into = os.path.abspath(os.path.expanduser(into))
	known = registered()
	if any(i == into for i, _ in known):
		return False
	with open(REGISTRY, "a") as f:
		f.write(f"{into}\t{repo}\n")
	return True


def unregister(into):
	"""Stop refreshing this mirror. Returns True when it was known."""
	into = os.path.abspath(os.path.expanduser(into))
	known = registered()
	kept = [(i, r) for i, r in known if i != into]
	if len(kept) == len(known):
		return False
	with open(REGISTRY, "w") as f:
		f.write("".join(f"{i}\t{r}\n" for i, r in kept))
	return True


def _toplevel(path):
	"""The git repo `path` sits in, or "" — asked from the nearest directory that exists."""
	base = path
	while not os.path.isdir(base) and os.path.dirname(base) != base:
		base = os.path.dirname(base)
	r = subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
	                   capture_output=True, text=True, timeout=60)
	return r.stdout.strip() if r.returncode == 0 else ""


def _exclude(root, rel):
	"""Keep the mirror out of git via .git/info/exclude.

	ponytail: never .gitignore. That file is tracked and belongs to everyone; a mirror is one machine's
	local copy, and committing an ignore rule for it puts your setup in someone else's history.
	"""
	p = os.path.join(root, ".git", "info", "exclude")
	rule = rel.rstrip("/") + "/"
	if rule in _read(p).splitlines():
		return f"ok    {rule} is already excluded"
	os.makedirs(os.path.dirname(p), exist_ok=True)
	with open(p, "a") as f:
		f.write(f"\n# gitdashy mirror (local, never commit)\n{rule}\n")
	return f"added {rule} to .git/info/exclude"


def _import(loader, line):
	text = _read(loader)
	if line in text.splitlines():
		return f"ok    {knowledge.tilde(loader)} already imports {line}"
	if os.path.dirname(loader):
		os.makedirs(os.path.dirname(loader), exist_ok=True)
	with open(loader, "a") as f:
		f.write(("\n" if text and not text.endswith("\n") else "")
		        + f"\n# gitdashy: this repo's review memory (read-only mirror)\n{line}\n")
	return f"added {line} to {knowledge.tilde(loader)}"


def wire_repo(into, loader, repo):
	"""Wire one repo: ignore the mirror, import it, keep it fresh, write it now. Returns report lines."""
	out = []
	into = os.path.abspath(os.path.expanduser(into))
	loader = os.path.abspath(os.path.expanduser(loader))
	root = _toplevel(into)
	out.append(_exclude(root, os.path.relpath(into, root)) if root else
	           f"note  {knowledge.tilde(into)} is not inside a git repo — nothing to exclude")
	out.append(_import(loader, "@" + os.path.relpath(into, os.path.dirname(loader)) + "/repo.md"))
	out.append(f"added {knowledge.tilde(into)} to the refresh list, as {repo}" if register(into, repo)
	           else f"ok    {knowledge.tilde(into)} is already refreshed every tick")
	out.append("      " + mirror.sync(into, repo, pull=False))
	return out
