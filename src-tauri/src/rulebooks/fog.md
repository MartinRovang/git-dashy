FOG AUDIT — WRITING RULEBOOK v1.0
The writing counterpart to the Spaghetti Audit.

PURPOSE
Score text for clarity, precision and traceability.
Readable is not the bar. The bar: the intended reader gets the point on
the first read, knows who does what, and can trust every claim.

Core rule: if a sentence resists the reader, rewrite the sentence.
Never ask the reader to work harder.

SOURCES (tags used below)
[W]   Williams, Style: Lessons in Clarity and Grace
[GS]  Gopen & Swan, The Science of Scientific Writing
[P]   Pinker, The Sense of Style / Thomas & Turner, Clear and Simple
      as the Truth
[M]   Minto, The Pyramid Principle
[O]   Orwell, Politics and the English Language / Strunk & White /
      Zinsser, On Writing Well
[HS]  Google and Microsoft style guides
[PL]  Plain-language guidelines

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRINCIPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Point first. The answer comes first; support follows. [M][W]
2. Characters as subjects, actions as verbs. Say who does what. [W]
3. Old before new. Each sentence starts from what the reader knows and
   ends with what is new. [W][GS]
4. Stress at the end. The most important information goes last. [W][GS]
5. Consistent topics. A paragraph's sentences stay on the same few
   characters. [W][GS]
6. Write for the reader, not yourself. Assume nothing the reader
   doesn't know. [P]
7. Every word works. Cut what carries no meaning. [O]
8. Plain over inflated. The common word beats the impressive one. [O][PL]
9. Group logically. Sections and lists don't overlap and don't miss
   anything obvious. [M]
10. Every claim is verifiable. No source, no claim.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCOPE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Clinical and patient-facing text: all checks apply in full. TF07 is
judged against a lay reader.
Platform UI and documentation: all checks apply in full.
Marketing and landing page: all checks apply. TF03 stays a fail —
claims about a medical device must be verifiable.

Not covered: regulatory labelling and IFU requirements (e.g. EU MDR).
These need their own checklist.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FAIL CHECKS
Each instance = 1 fail. Any fail blocks publishing.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Meaning
TF01 Buried point — the main point is not in the first paragraph
     (the first sentence for UI text). [M][W]
     → Point test (see procedure).
TF02 Hidden actor — it is unclear who must do what: the action is
     buried in a noun, or passive voice drops an actor who matters.
     Critical in instructions, responsibilities and errors. [W]
TF03 Unverified claim — a fact, number or reference without a source,
     or a source that doesn't say what the text claims.
TF04 Inconsistent terminology — one thing called by two names, or one
     name used for two things. [HS]
TF05 Ambiguity — a sentence that can be read two ways: unclear "it" or
     "this", unclear scope of "not", misplaced modifiers.
TF06 Hedge hiding a commitment — "may", "should generally",
     "typically" where the text must state a fact or a requirement. [W][O]
TF07 Undefined jargon — a term or abbreviation the intended reader
     doesn't know, not defined on first use. [P][PL]

Instructions
TF08 Steps out of order — steps not in the order performed, or the
     condition after the action ("Press X if Y" → "If Y, press X"). [HS]

Ownership
TF09 Author cannot state the point in one sentence.
     → Point test (see procedure).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WARNING CHECKS
Each instance = 1 warning.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sentence
TW01 Nominalisation — an action as a noun (analysis, implementation,
     verification) where a verb works. [W]
TW02 Passive voice where the actor is known and relevant. Passive is
     fine when the actor doesn't matter. [W]
TW03 Long windup — more than 10 words before the main subject. [W]
TW04 Subject–verb gap — a long interruption between subject and verb. [W][GS]
TW05 Weak ending — the sentence ends on minor information instead of
     the important part. [W][GS]
TW06 New before old — the sentence opens with unfamiliar information
     that doesn't link to the previous sentence. [W][GS]
TW07 Sentence over 25 words. [PL][HS]

Paragraph and document
TW08 Topic drift — sentence subjects in a paragraph jump between
     unrelated characters. [W][GS]
TW09 Paragraph with more than one point. [M]
TW10 Bad grouping — a list or set of sections that overlaps, mixes
     levels, or misses an obvious member. [M]
TW11 Label heading — a heading that names a topic ("Overview") instead
     of saying what the section says. [M][HS]
TW12 Steps or parallel items written as prose instead of a list, or list
     items that aren't grammatically parallel. [HS]

Concision
TW13 Redundancy — saying the same thing twice, or redundant pairs
     ("each and every", "final outcome"). [O][W]
TW14 Empty modifiers — very, really, basically, actually, extremely,
     robust, seamless, crucial. [O]
TW15 Throat-clearing — "It is important to note that", "In this
     section we will", "As mentioned above". [W][O]
TW16 Inflated word — utilise (use), facilitate (help), in order to (to),
     prior to (before). [O][PL]
TW17 Dead sentence — delete it and nothing is lost. Generic filler that
     could appear in any document. [O]

Reader
TW18 Instructions not addressed to the reader — "the user should"
     where "you" works. [HS][PL]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KW = words in audited text ÷ 1000. Excludes code samples, tables of
data and quoted material.

Accumulation: every section with 3+ warnings adds 1 fail.

F = fails ÷ KW          (including accumulation fails)
W = warnings ÷ KW

Score = max(0, 100 − 50×F − 10×W)

Grade
A   score ≥ 90 and zero fails
B   score ≥ 75
C   score ≥ 50
D   score < 50

Gate: publish-ready only with zero fails, whatever the score.

Weights and thresholds (10-word windup, 25-word sentences) are starting
points. Recalibrate after the first 2–3 audits and record the change as
a new rulebook version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROCEDURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Pin — record document, version and rulebook version.
2. Reader — state the intended reader in one line before auditing.
   TF07 and TW18 are judged against this reader.
3. Scope — under 2,000 words: audit everything. Otherwise: the opening
   section, all instructions, and two sections picked at random.
   List them.
4. Point test — after one read, the auditor writes the point in one
   sentence. The author does the same, separately. If the two don't
   match, or the author can't do it: TF01 or TF09.
5. Mechanical pass — run a prose linter configured with the house style
   (e.g. Vale) for the word-level checks. Map every hit to a check ID.
6. Manual pass — sentence, paragraph and document checks. Verify every
   factual claim against its source (TF03).
7. Record every finding:
   Check ID | Section/paragraph | Short quote | Fix direction
8. Compute score and grade. Report: document, KW, fails, warnings,
   score, grade, three worst sections.
9. Re-audit on every major revision.

Disputes: the rule text decides. If the rule text is ambiguous, fix it
in a new rulebook version. Never decide case by case.