"""Review memory: ~/.prs_memory/general.md plus one <owner>__<repo>.md per repo. Plain text, edit freely."""
import os

from .. import config


def path(repo=None):
	return os.path.join(config.MEMORY_DIR, (repo.replace("/", "__") if repo else "general") + ".md")


def read(repo):
	"""General + repo memory as one prompt block, '' when both are empty."""
	parts = []
	for label, p in (("General", path()), (repo, path(repo))):
		try:
			text = open(p).read().strip()
		except FileNotFoundError:
			text = ""
		if text:
			parts.append(f"## {label}\n{text}")
	return "\n\n".join(parts)


def append(repo, text):
	text = (text or "").strip()
	if not text:
		return
	os.makedirs(config.MEMORY_DIR, exist_ok=True)
	with open(path(repo), "a") as f:
		f.write("".join(f"- {l.strip().lstrip('-• ')}\n" for l in text.splitlines() if l.strip()))
