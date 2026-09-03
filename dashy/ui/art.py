"""Splash art. ponytail: background dots blanked so the bat reads on any terminal theme."""
NAME = "M a r t i n   S o r i a   R ø v a n g"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
REFRESH_SPINNER = "◐◓◑◒"
LOGO = [
	"        *",
	"       *@                  *-",
	"      -@@=                @@",
	"      @@@=              =@@@",
	"     #@@@*             *@@@*",
	"    =@@@@@@@@@@@@@@@@@@@@@@=",
	"    #@@@@@@@@@@@@@@@@@@@@@@-",
	"    @@@@@@@@@@@@@@@@@@@@@@@",
	"   *@@@@@@@@@@@@@@@@@@@@@@@",
	"   %@@@@@@@@@@@@@@@@@@@@@@@",
	"   @@@#*@@@@@@@@=-=%@@@@@@@-",
	"   @%    -@@@@+    -@@@@@@@+",
	"   @@@@@@@@@@@@@@@@@@@@@@@@@",
	"  =@@@@@#     -%@@@*  #@@@@@-",
	"  +@@     -+    +@     *@@@@*",
	"  -@@-   @@@@@@=@      @@@@@@-",
	"   @@@=     -==-*    -@@@@@@@@",
	"    @@@@%=        *@@@@@@@@@@@@",
	"     =@@@@@@@@@@@@@@@@@@@@@@@@@@@-",
	"          #@@@@@@@@###*+=-  #%%%@@",
]
LOGO_W = max(map(len, LOGO))


def marquee(text, width, t, gap="   ·   ", cps=6):
	"""The width-wide window of text scrolling left at cps chars/sec, or text itself when it fits.
	ponytail: pure function of the clock like the spinners, no animation state anywhere."""
	if width <= 0:
		return ""
	if len(text) <= width:
		return text
	loop = text + gap
	i = int(t * cps) % len(loop)
	return (loop + loop)[i:i + width]
