# The rule

Security is structural, not a checklist appended at the end. You cannot build correctness
on top of an insecure foundation and add safety later — the foundation is where safety
lives or does not.

In practice: know what is trusted and what is not, at every boundary. Anything crossing
from outside is input, whatever it is called. Anything sensitive that reaches somewhere it
can be read is a leak, whether or not anyone reads it.
