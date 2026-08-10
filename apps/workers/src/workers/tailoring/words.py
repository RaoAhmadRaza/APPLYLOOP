"""Words that carry no claim, and are therefore free to appear in a rewrite.

The validator's content rule is: every token of a generated bullet must already be in the
claim it cites. That is exactly right for the tokens that assert something — a technology,
a metric, an employer, a credential, a scale — and wrong for the ones that only join them
together. "Led the migration of 41 services" and "Migrated 41 services" state the identical
fact; refusing the second would mean §3.3's "rephrase" allowance permits nothing.

**The test for membership here is: could this word alone make a résumé line false?** A verb
inflection cannot — the facts are the nouns and the numbers, and those stay checked. A
determiner cannot. `Kubernetes` obviously can. So can `certified`, `senior`, `team` and
`million`: they are absent on purpose and must stay absent.

Verb inflections are listed rather than stemmed. A suffix-stripper gets `processing` to
`processed` and then quietly fails on `cut`/`cutting`, and a half-working stemmer is harder
to review than a list you can read. The list is data; the retention floor in BAR.md §2 is
what says whether it is complete enough, and it is set at 0.70 rather than 1.00 precisely
so this file does not have to be perfect.

Everything here is compared after `workers.text.squash`, so hyphens and case do not matter.
"""

# Determiners, pronouns, prepositions, conjunctions — pure grammar.
_FUNCTION = """
a an the this that these those my our your their its his her it they we i you he she
and or but nor so yet as if then than because while when where which who whom whose what
of in on at by for from to with within into onto across over under through during after
before between among against about around per via up down out off again further
is am are was were be been being have has had having do does did doing
not no nor both each few more most other some such only own same very
here there both either neither all any every
"""

# Action verbs and their inflections. A verb states how something was done, never what was
# true — the object it acts on is still checked against the claim.
_VERBS = """
achieve achieved achieving add added adding architect architected architecting
author authored authoring automate automated automating build building built
change changed changing collaborate collaborated collaborating consolidate consolidated
create created creating cut cuts cutting decrease decreased decreasing deliver delivered
delivering deploy deployed deploying design designed designing develop developed
developing drive driven drove eliminate eliminated eliminating enable enabled enabling
ensure ensured ensuring establish established establishing expand expanded expanding
extend extended extending grow grew growing halve halved halving handle handled handling
help helped helping hire hired hires hiring
implement implemented implementing improve improved improving
increase increased increasing introduce introduced introducing launch launched launching
lead leading led maintain maintained maintaining make made making manage managed managing
migrate migrated migrating move moved moving operate operated operating optimise optimised
optimising optimize optimized optimizing own owned owning partition partitioned
partitioning perform performed performing process processed processing produce produced
producing provide provided providing raise raised raising rebuild rebuilding rebuilt
reduce reduced reducing refactor refactored refactoring remove removed removing replace
replaced replacing rewrite rewriting rewrote run running ran scale scaled scaling
serve served serving ship shipped shipping simplify simplified simplifying solve solved
solving speed sped speeding spend spent spending support supported supporting take taken
taking took trim trimmed trimming use used using write writing wrote
"""

# Words that only exist to point at something already checked. Every entry is a word a
# cover letter needs and a bullet's facts do not live in.
#
# **Widened 2026-08-10, by the project owner, reversing the refusal in BAR.md §8.** That
# refusal was right for its run: it declined to add the seven words that had just failed,
# immediately after watching them fail, on the third query of the same twenty pairs — the
# adaptive-analysis shape M4's §8 warns about. Three things are different now. The rate is
# 1.00, not 0.50: on `deepseek-v4-flash` no letter is written at all, and a stage that
# produces nothing on every honest pair is a product failure rather than a strict one. The
# letter's rate is reported and not gated (BAR.md §2), so nothing here can make the gate
# lie. And the owner has ruled prose quality in scope, which it previously was not.
#
# **What was still refused, and why the split matters.** Run 4's two real catches were
# `the number 13` and `the number 17` — figures the résumé never states — and the number
# rule runs before any of this and is untouched. Of the words that blocked run 3, the
# nouns stay out: `projects`, `outcomes`, `training`, `background`, `results` are the
# OBJECTS of the verbs above, and this file's own argument for allowing verbs is that
# "the object it acts on is still checked". An object is a fact. `certified`, `senior`,
# `team` and `million` stay out for the reason already stated above.
#
# So the widening is grammar only: modals, discourse adverbs, and the frame words a letter
# needs to be addressed to somebody. Nothing added below can be the answer to "what did
# this candidate do".
_CONNECTIVE = """
also currently exactly first including like now previously same still then today
work working works role roles kind
can could may might must shall should will would
however therefore moreover furthermore additionally instead otherwise meanwhile
already rather whether directly particularly especially notably often always never
well much many several enough less least greatly closely fully largely mainly
alongside beyond throughout upon regarding toward towards since given despite although
though unless until whereas wherever whenever
apply applied applies applying
"""

ALLOWED = frozenset(word for block in (_FUNCTION, _VERBS, _CONNECTIVE) for word in block.split())
