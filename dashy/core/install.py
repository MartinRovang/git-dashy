"""Wiring the knowledge system into a machine, and into a repo.

ponytail: reviews read and write memory with no setup at all — that half needs no installing. What needs
installing is the half that reaches a regular coding session, which until now lived in one person's private
agent corpus, and so was unreachable for everyone else.
"""
import json
import os
import shlex
import shutil
import subprocess

from .. import config
from . import knowledge, mirror

BEGIN, END = "<!-- gitdashy:begin -->", "<!-- gitdashy:end -->"
CBEGIN, CEND = "<!-- gitdashy:corpus:begin -->", "<!-- gitdashy:corpus:end -->"  # a separate block: one can go without the other
CORPUS_HOME = os.path.expanduser("~/.agent-corpus")  # where an installed corpus lives, independent of gitdashy
IMPORT = "@prs-memory/general.md"  # the line that says the wiring is already there, block or not
REGISTRY = os.path.expanduser("~/.prs_mirrors")  # "<into>\t<repo>" per line; the filesystem keeps the setting
BLOCK = f"""{BEGIN}
# Review memory

Cross-repo facts gitdashy's PR reviews have earned: yours first, then the team's. Written only once two
independent observations agreed, so trust them — but they are what the code turned out to be, not rules.
The team file does not exist until you are in a team, and a missing import is simply skipped. Facts about
one repo arrive separately, through that repo's own mirror.

@prs-team/project.md
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
	out.append(f"  · append three imports to {knowledge.tilde(md)}, inside a marked block"
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
			pending = "" if os.path.isdir(target) else "   (waits until there is one)"
			out.append(f"{did}link  {knowledge.tilde(link)} -> {knowledge.tilde(target)}{pending}")
			if not dry:
				# ponytail: make your own memory dir rather than leaving a link to nothing. The team's is
				# left dangling on purpose — it exists once you join, and a missing import is skipped.
				if target == config.LOCAL_MEMORY:
					os.makedirs(target, exist_ok=True)
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


def _gitdir(root):
	"""The directory holding info/exclude. ponytail: NOT <root>/.git — in a linked worktree or a
	submodule that is a FILE, and building a path through it raises NotADirectoryError. Ask git."""
	r = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"],
	                   capture_output=True, text=True, timeout=60)
	d = r.stdout.strip()
	if r.returncode != 0 or not d:
		return ""
	return d if os.path.isabs(d) else os.path.join(root, d)


def _exclude(root, rel):
	"""Keep the mirror out of git via info/exclude.

	ponytail: never .gitignore. That file is tracked and belongs to everyone; a mirror is one machine's
	local copy, and committing an ignore rule for it puts your setup in someone else's history.
	"""
	gd = _gitdir(root)
	if not gd:
		return "FAIL  cannot find the git directory — refusing to write a mirror git could commit"
	p = os.path.join(gd, "info", "exclude")
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


def corpus_files(corpus):
	"""The identity markdown a corpus offers, sorted, or [] when it has none."""
	d = os.path.join(corpus, "identity")
	return sorted(f for f in os.listdir(d) if f.endswith(".md") and not f.endswith(".template")) \
		if os.path.isdir(d) else []


def corpus_block(corpus):
	names = corpus_files(corpus)
	body = "\n".join(f"@identity/{n}" for n in names)
	return f"""{CBEGIN}
# Agent corpus

How this machine's agent works: what to establish before changing code, when to stop and ask,
and who it is working with. Installed by `gitdashy install --full` from {os.path.basename(corpus)}.

{body}
{CEND}
"""


def full_explain(corpus, url=""):
	"""What a full install changes. Returns report lines."""
	d, out = claude_dir(), []
	names = corpus_files(corpus)
	words = sum(len(open(os.path.join(corpus, "identity", n)).read().split()) for n in names)
	out.append("gitdashy install --full puts an agent corpus on this machine, so every coding session")
	out.append("works to the same discipline — and adds the review-memory wiring `install` does.")
	out.append("")
	out.append("This is the big one. Read it before agreeing.")
	out.append("")
	out.append("It will:")
	out.append(f"  · {'clone ' + url if url else 'copy the corpus gitdashy ships'} to {knowledge.tilde(CORPUS_HOME)}"
	           + ("   [EXISTS, will be left alone]" if os.path.isdir(CORPUS_HOME) else "   [new]"))
	out.append(f"  · symlink {knowledge.tilde(os.path.join(d, 'identity'))} -> that corpus's identity/")
	out.append(f"  · import {len(names)} files into {knowledge.tilde(os.path.join(d, 'CLAUDE.md'))}: {', '.join(names)}")
	out.append(f"  · seed USER.md from the template, for you to fill in, if it is not there already")
	out.append(f"  · register a SessionStart hook in {knowledge.tilde(os.path.join(d, 'settings.json'))}")
	out.append("  · everything plain `gitdashy install` does, for review memory")
	out.append("")
	out.append(f"What that costs, every session on this machine, permanently:")
	out.append(f"  · about {int(words * 1.35):,} tokens of instructions, before you have typed anything")
	out.append(f"  · one hook running at the start of every session, in every repo")
	out.append("")
	out.append("The hook seeds .agent/ notes in a repo, excludes them from git (via .git/info/exclude,")
	out.append("never the tracked .gitignore), and mirrors that repo's review memory. It writes nothing")
	out.append("that git can see, and exits quietly if it is not in a repo.")
	out.append("")
	out.append("`gitdashy install --full --uninstall` reverses all of it. The corpus is left on disk,")
	out.append("because by then you may have edited it.")
	return out


HOOK = "session-start.sh"


def _count(settings):
	"""How many SessionStart hooks there are in total, across every group."""
	return sum(len(g.get("hooks", [])) for g in settings.get("hooks", {}).get("SessionStart", []))


def _hooks(settings, script):
	"""SessionStart groups with our hook taken out. Empty groups are dropped.

	ponytail: callers compare HOOK counts, never group counts. Ours can end up sharing a group with
	somebody else's — then the group survives, the count of groups is unchanged, and a group-count
	check concludes we were never installed and appends a second copy.
	"""
	out = []
	for group in settings.get("hooks", {}).get("SessionStart", []):
		kept = [h for h in group.get("hooks", []) if script not in str(h.get("command", ""))]
		if kept:
			out.append({**group, "hooks": kept})
	return out


def full_apply(corpus, url="", dry=False):
	"""Install a corpus and its hook, then the memory wiring. Returns report lines."""
	d, out, did = claude_dir(), [], "would " if dry else ""
	if not os.path.isdir(d):
		return [f"FAIL  no agent config directory at {knowledge.tilde(d)} — is claude installed?"]
	if os.path.isdir(CORPUS_HOME):
		out.append(f"ok    {knowledge.tilde(CORPUS_HOME)} is already there — left as it is")
	else:
		out.append(f"{did}install  the corpus into {knowledge.tilde(CORPUS_HOME)}")
		if not dry:
			from . import team
			err = team.clone(url, CORPUS_HOME) if url else ""
			if url and err:
				return out[:-1] + [f"FAIL  {err}"]
			if not url:
				shutil.copytree(corpus, CORPUS_HOME)
	home = CORPUS_HOME if not dry or os.path.isdir(CORPUS_HOME) else corpus
	link = os.path.join(d, "identity")
	ident = os.path.join(home, "identity")
	if _ours(link, ident):
		out.append(f"ok    {knowledge.tilde(link)} already points at the corpus")
	elif os.path.lexists(link):
		out.append(f"SKIP  {knowledge.tilde(link)} exists and is not ours — left alone, so nothing is imported")
	else:
		out.append(f"{did}link  {knowledge.tilde(link)} -> {knowledge.tilde(ident)}")
		if not dry:
			os.symlink(ident, link)
	user, tmpl = os.path.join(ident, "USER.md"), os.path.join(ident, "USER.md.template")
	if os.path.exists(user):
		out.append("ok    USER.md is already filled in")
	elif os.path.exists(tmpl):
		out.append(f"{did}seed  USER.md from the template — fill it in, it is the highest-value file here")
		if not dry:
			shutil.copy(tmpl, user)
	md = os.path.join(d, "CLAUDE.md")
	text = _read(md)
	if CBEGIN in text:
		out.append(f"ok    {knowledge.tilde(md)} already imports the corpus")
	else:
		out.append(f"{did}add   the corpus imports to {knowledge.tilde(md)}")
		if not dry:
			with open(md, "a") as f:
				f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + corpus_block(home))
	script = os.path.join(home, "bin", HOOK)
	sp = os.path.join(d, "settings.json")
	try:
		settings = json.loads(_read(sp) or "{}")
	except ValueError:
		return out + [f"FAIL  {knowledge.tilde(sp)} is not valid JSON — fix it first, nothing was changed"]
	script_ok = os.path.isfile(script) and os.access(script, os.X_OK)
	if _count({"hooks": {"SessionStart": _hooks(settings, HOOK)}}) != _count(settings):
		out.append("ok    the SessionStart hook is already registered")
	elif not script_ok:  # ponytail: a corpus that ships no hook must not leave every session start failing
		out.append(f"SKIP  {knowledge.tilde(script)} is missing or not executable — no hook registered")
	else:
		out.append(f"{did}hook  register SessionStart -> {knowledge.tilde(script)}")
		if not dry:
			settings.setdefault("hooks", {}).setdefault("SessionStart", []).append(
				{"hooks": [{"type": "command", "command": shlex.quote(script), "timeout": 10,
				            "statusMessage": "Preparing repo notes"}]})
			with open(sp, "w") as f:
				json.dump(settings, f, indent=2)
	return out + [""] + (apply(dry) if not dry else [f"{did}do    everything plain `install` does"])


def full_remove(dry=False):
	"""Reverse a full install. The corpus itself stays: by now you may have edited it."""
	d, out, did = claude_dir(), [], "would " if dry else ""
	link, ident = os.path.join(d, "identity"), os.path.join(CORPUS_HOME, "identity")
	if _ours(link, ident):
		out.append(f"{did}remove  {knowledge.tilde(link)}")
		if not dry:
			os.remove(link)
	elif os.path.lexists(link):
		out.append(f"SKIP    {knowledge.tilde(link)} is not the link we made — left alone")
	md = os.path.join(d, "CLAUDE.md")
	text = _read(md)
	if CBEGIN in text and CEND in text:
		out.append(f"{did}remove  the corpus imports from {knowledge.tilde(md)}")
		if not dry:
			head, _, rest = text.partition(CBEGIN)
			_, _, tail = rest.partition(CEND)
			with open(md, "w") as f:
				f.write(head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n"))
	sp = os.path.join(d, "settings.json")
	try:
		settings = json.loads(_read(sp) or "{}")
	except ValueError:
		out.append(f"SKIP    {knowledge.tilde(sp)} is not valid JSON — remove the hook by hand")
		settings = None
	if settings is not None and settings.get("hooks", {}).get("SessionStart"):
		kept = _hooks(settings, HOOK)
		if _count({"hooks": {"SessionStart": kept}}) != _count(settings):
			out.append(f"{did}remove  the SessionStart hook from {knowledge.tilde(sp)}")
			if not dry:
				settings["hooks"]["SessionStart"] = kept
				if not kept:
					settings["hooks"].pop("SessionStart")
				with open(sp, "w") as f:
					json.dump(settings, f, indent=2)
	known = registered()
	if known:
		out.append(f"{did}forget  {len(known)} registered mirror{'s' if len(known) > 1 else ''}"
		           f" — the files stay, they simply stop being refreshed")
		if not dry:
			for into, _ in known:
				unregister(into)
	out.append(f"note    {knowledge.tilde(CORPUS_HOME)} is left on disk — you may have edited it")
	return out + [""] + remove(dry)
