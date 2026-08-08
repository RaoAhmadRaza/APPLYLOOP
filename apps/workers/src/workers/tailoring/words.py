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

# Words that only exist to point at something already checked. Deliberately short: every
# entry here is a word a cover letter needs and a bullet's facts do not live in.
_CONNECTIVE = """
also currently exactly first including like now previously same still then today
work working works role roles kind
"""

ALLOWED = frozenset(word for block in (_FUNCTION, _VERBS, _CONNECTIVE) for word in block.split())
