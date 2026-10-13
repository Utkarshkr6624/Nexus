"""Phase 2.1 reasoning - critical-thinking families: fallacy spotting, causal vs correlational reasoning, Fermi estimation (numbers computed),
 weighted decisions with a computed flip point, ambiguity detection, consistency checking (weighted union-find), analogies / odd-one-out,
 cognitive-reflection traps with computed answers, and 'what would change your answer' decisions. Includes short multi-turn follow-ups."""
from math import isqrt, gcd
from gen21_reason_util import *

# ------------------------------------------------------------------ I. fallacies (hand-written; includes sound arguments)
FALL = [
    ("Our new hire can't be right about the database design - he dropped out of college.", "ad hominem (attacking the person)", "The argument attacks the person's background instead of the design. A sound reply evaluates the design on its merits: schema, queries, trade-offs."),
    ("Nine out of ten people who got the flu this winter had taken vitamin C, so vitamin C causes flu.", "base-rate / confounding error", "If most people take vitamin C in winter, nearly all flu cases will have taken it. The observation says nothing about cause until you compare flu rates between takers and non-takers."),
    ("If it rained, the street is wet. The street is wet. So it rained.", "affirming the consequent", "A wet street has other causes (a burst pipe, street cleaning, a sprinkler). The rule goes from rain to wet, not back."),
    ("Either we cut the budget by half or the company goes bankrupt. We don't want bankruptcy, so we must cut the budget by half.", "false dilemma", "Only two options are presented, but there are others: raising revenue, cutting costs by less, renegotiating debt. The conclusion depends on the claim that those are the only two."),
    ("Every successful founder I read about dropped out of university, so dropping out is a good path to success.", "survivorship bias", "You hear about the dropouts who succeeded, not the many who failed and are never profiled. To judge the strategy you need the failures as well."),
    ("This herbal tea is natural, so it must be safe.", "appeal to nature", "Natural does not imply safe: many poisons are natural, and many safe things are synthetic. Safety has to be shown by evidence about the specific product."),
    ("Everyone in the office uses this software, so it must be the best one.", "appeal to popularity", "Widespread use shows popularity, possibly habit or a company mandate, not quality. Compare it on features, cost and fit."),
    ("If we allow students to retake one exam, soon they'll demand to retake every exam and grades will mean nothing.", "slippery slope", "The argument assumes a chain of events without showing that each step must follow. A policy can have clear limits (one retake, with conditions)."),
    ("The new bridge has been open for a month and nobody has been hurt, so it is safe.", "hasty generalisation", "One month of data cannot show safety for a structure that must last decades; inspections and load tests are the proper evidence."),
    ("My doctor smokes, so smoking can't be that bad.", "tu quoque / irrelevant evidence", "The doctor's habits do not change what the studies show about smoking. The behaviour of one person is not evidence about the risk."),
    ("The study was funded by a coffee company, so its finding that coffee is healthy is false.", "genetic fallacy", "Funding is a reason for extra scrutiny, but it does not make a finding false. Check the methods, the sample, whether it was replicated."),
    ("No one has proved that the system is secure, so it must be insecure.", "argument from ignorance", "Lack of proof of security does not prove insecurity, just as lack of proof of safety does not. What counts is the evidence from testing and review."),
    ("Since you can't prove that ghosts don't exist, they do.", "argument from ignorance (burden of proof)", "The burden of proof lies on the person making the claim. Inability to disprove something is not evidence for it."),
    ("We have spent two years and a million on this project, so we can't stop now.", "sunk cost fallacy", "The money and time are gone either way. The decision should compare future costs and benefits only."),
    ("Ice cream sales and drownings both rise in summer, so ice cream causes drowning.", "correlation as causation (common cause)", "Hot weather increases both swimming and ice-cream buying. A third factor explains the pattern."),
    ("The last three times I washed my car it rained, so washing my car causes rain.", "post hoc ergo propter hoc", "Occurring after is not the same as occurring because of. Three coincidences are weak evidence, and there is no mechanism."),
    ("A famous actor endorses this investment fund, so it must be a good fund.", "appeal to inappropriate authority", "Acting fame is not financial expertise. Look at fees, track record over many years and regulation."),
    ("You say we should reduce plastic bags. So you want to ban all plastic and send us back to the Stone Age?", "straw man", "The reply replaces a modest proposal with an extreme one that nobody proposed, then attacks that."),
    ("Either you're with us or you're against us.", "false dichotomy", "People can agree partly, be neutral or disagree with some actions and agree with others."),
    ("I know three people who won small prizes at that lottery, so my chances of winning are good.", "anecdotal evidence", "A few winners say nothing about the odds; millions of tickets lose and are never mentioned. Odds come from the prize structure."),
    ("This medication has no side effects reported yet in the first 20 patients, so it's side-effect free.", "hasty generalisation from a small sample", "Rare side effects need large samples to appear. Twenty patients can only rule out common ones."),
    ("A: Good drivers don't have accidents. B: My neighbour had an accident, so he isn't a good driver.", "no true Scotsman / circular definition", "If 'good driver' is defined as someone without accidents, the claim is true by definition and tells us nothing. Skill and accidents are separate things (bad luck happens)."),
    ("Our product must be excellent: it's the best product we have ever made, and I'm sure the best is excellent.", "circular reasoning", "The conclusion is assumed in the premises. Independent evidence, such as reviews or tests, is needed."),
    ("When the temperature drops, people buy more scarves. So scarves cool down the weather.", "reversed causation", "The causal arrow goes from cold weather to scarf purchases, not the other way round."),
    ("The experiment worked on mice, so it'll work on people.", "inappropriate analogy", "Mice and humans differ in biology, dosage and environment; many treatments that work in mice fail in people. Human trials are required."),
    ("The shop has a 20% off sign next to the 'was 100, now 80' tag, but the tag was printed yesterday. The 'sale' must be real.", "unsupported premise (anchoring)", "A new price tag proves nothing about the earlier price. Check price history to see whether the discount is genuine."),
    ("Of 100 patients in our clinic, the ones who exercise are healthier, so exercise makes people healthier.", "possible confounding / reverse causation", "Healthier people may be more able to exercise, and exercisers may differ in diet and income. Randomised or well-controlled studies are needed to claim cause."),
    ("All dogs are mammals. All mammals are animals. Therefore all dogs are animals.", None, "This is a valid argument: if both premises are true, the conclusion cannot be false. Nothing is wrong with the logic."),
    ("The data show that people who sleep 7-8 hours score higher on memory tests in this randomised trial, where participants were assigned sleep schedules. So sleep helps memory.", None, "Random assignment lets the study separate the effect of sleep from other differences between people, so a causal reading is reasonable (subject to sample size and replication)."),
    ("The bridge inspector says the cracks are within tolerance, so the bridge is safe to use this week.", None, "Appealing to a qualified expert on a question in their field is a reasonable form of evidence, though you can still ask for the inspection report."),
    ("If we do not back up the files, a disk failure would lose everything. A disk failure is likely within five years. So we should back up the files.", None, "A conditional risk plus a likelihood plus a way to avoid the loss gives a sound practical argument. It does not claim certainty, only that the benefit justifies the cost."),
]
FALL_ASK = ["Is there a flaw in this argument?\n\n\"{a}\"", "What's wrong with the following reasoning, if anything?\n\"{a}\"", "Evaluate this argument: \"{a}\"",
            "Someone told me: \"{a}\" Does that hold up?", "Spot the reasoning error (if there is one): {a}", "Is this a good argument? \"{a}\""]


def gen_fallacies(rng):
    n = 0
    for arg, name, expl in FALL:
        for rep in range(2 if name else 1):
            q = pick(rng, FALL_ASK).format(a=arg)
            if name:
                a = pick(rng, [f"Yes, the flaw is {name}. {expl}", f"The mistake here: {name}. {expl}", f"Yes. Label: {name}. {expl}"]) if rep == 0 else f"{expl} The label for this mistake is {name}."
            else:
                a = pick(rng, ["No flaw here. ", "It holds up. ", "This one is fine. "]) + expl
            emit("fallacy_spotting", [("user", q), ("assistant", a)], "medium", "fall", rng, src="authored")
            n += 1
    return n


# ------------------------------------------------------------------ J. causal reasoning
CAUSAL = [
    ("Cities with more police officers have more crime.", "More police causes crime", "Crime levels drive staffing decisions (reverse causation), and big cities have both more people and more police.", "Compare the same city before and after a staffing change, or use natural experiments such as budget shocks."),
    ("Students who eat breakfast get better grades.", "Breakfast raises grades", "Families with more stable routines or resources may both serve breakfast and support study, so the link may be confounded.", "Randomly provide breakfast to some schools and compare, or control for family income and routine."),
    ("People who own bikes live longer.", "Owning a bike extends life", "Healthier and wealthier people are more likely to own and ride bikes.", "Track people over time who start cycling versus matched people who do not, adjusting for baseline health."),
    ("Websites with a chat widget have higher sales.", "The chat widget creates sales", "Bigger, better-funded sites are more likely to add a widget and also to have higher sales.", "Randomly show the widget to half of visitors (an A/B test) and compare conversion."),
    ("Children who read more have larger vocabularies.", "Reading builds vocabulary", "This is plausible, but children with strong vocabularies may also enjoy reading more, and parents' habits may drive both.", "Run a reading intervention with a control group and measure the change in vocabulary."),
    ("Houses with more fire trucks at the scene suffer more damage.", "Fire trucks cause damage", "A bigger fire needs more trucks and also causes more damage.", "Compare fires of similar size by how many trucks arrive, or note that fire size is the common cause."),
    ("People who take vitamin supplements are healthier.", "Supplements cause good health", "People who take supplements often also exercise, eat well and see doctors (the 'healthy user' effect).", "A randomised trial where supplements or placebos are assigned."),
    ("Countries that eat more chocolate win more Nobel prizes.", "Chocolate improves intelligence", "Wealthy countries have more chocolate consumption and more research funding, which is the likely common cause.", "Look within countries, adjust for income and research spending, or test the hypothesis with experiments on cognition."),
    ("Employees who work longer hours get promoted more often.", "Long hours cause promotion", "Visibility and ambition could drive both, or managers may favour presence over output.", "Compare employees with similar output who differ in hours, or use schedules assigned independently."),
    ("After a new traffic law, accidents fell by 20%.", "The law reduced accidents", "Accidents fluctuate, and the drop may reflect weather, regression to the mean or other changes happening at the same time.", "Compare with similar roads without the law over the same period (difference-in-differences) and check the long-term trend."),
    ("Patients who got the new drug recovered faster than those who did not, but doctors chose who got the drug.", "The drug speeds recovery", "Doctors may give the drug to patients who are healthier or sicker, so the groups differ before treatment.", "Random assignment to drug or placebo, ideally blinded."),
    ("People who use standing desks report less back pain.", "Standing desks cure back pain", "People with back pain may be more likely to buy a standing desk, and any change in posture habit could help.", "Randomly assign desks, compare with a sit-and-move control, and measure pain over months."),
    ("Teams that hold daily stand-up meetings ship more features.", "Stand-ups cause productivity", "Well-organised teams may adopt stand-ups, or a stand-up might simply be easier in small teams.", "Introduce stand-ups in some teams chosen at random and compare output."),
    ("Wearing a seat belt is associated with more injuries in crashes in some datasets.", "Seat belts cause injuries", "Seat belt users who survive a severe crash are counted as injured, while non-wearers in severe crashes die and are not counted as injured. The dataset only includes survivors.", "Include fatalities and compare injury and death combined for belted and unbelted occupants."),
    ("Kids who play violent games are more aggressive.", "Games cause aggression", "Aggressive kids may prefer such games, and family environment can drive both.", "Longitudinal studies that measure aggression before and after, with controls."),
    ("My headache went away after I took the herbal remedy.", "The remedy cured my headache", "Most headaches go away on their own, and expectation can reduce pain (placebo effect).", "A blinded comparison with a placebo across many people, or at least repeated trials on the same person."),
]
CA_ASK = ["Observation: {o} Can we conclude: '{c}'? What would you want to check?", "\"{o}\" Does that show that {c2}? Think about alternative explanations.",
          "Data show: {o} A colleague says that means {c2}. Fair?", "Is it justified to say that {c2}, given that: {o}"]


def gen_causal(rng):
    n = 0
    for obs, claim, alt, test in CAUSAL:
        c2 = claim[0].lower() + claim[1:]
        for rep in range(2):
            q = pick(rng, CA_ASK).format(o=obs, c=claim, c2=c2)
            if rep == 0:
                a = f"Not from this alone. {alt} To test the causal claim: {test[0].lower() + test[1:]}"
            else:
                a = pick(rng, ["A correlation like this has several possible explanations. ", "There are at least three readings: the claim, the reverse direction, or a hidden third factor. ", "Be careful with the causal reading. "]) + alt + f" What would settle it: {test[0].lower() + test[1:]}"
            emit("causal_vs_correlation", [("user", q), ("assistant", a)], "medium", "causal", rng, src="authored")
            n += 1
    return n


# ------------------------------------------------------------------ K. Fermi estimates (numbers are computed)
def sig(x, k=2):
    if x == 0:
        return "0"
    from math import log10, floor
    d = k - 1 - floor(log10(abs(x)))
    v = round(x, d)
    return f"{v:,.0f}" if d <= 0 else f"{v:,.{d}f}"


FERMI = [
    ("How many piano tuners work in a city of 2 million people?", [("population", 2_000_000, "people"), ("people per household", 4, ""), ("share of households with a piano", 0.05, ""), ("tunings per piano per year", 1, ""), ("tunings one tuner does per day", 4, ""), ("working days per year", 220, "")],
     lambda p: p[0] / p[1] * p[2] * p[3] / (p[4] * p[5]), "tuners", "Treat each number as a guess good to a factor of about two; the answer is the right order of magnitude, not a count."),
    ("Roughly how many litres of water does a city of 500,000 use for showers in a day?", [("people", 500_000, ""), ("share who shower per day", 0.7, ""), ("minutes per shower", 7, ""), ("litres per minute", 9, "")],
     lambda p: p[0] * p[1] * p[2] * p[3], "litres per day", "Shower flow rates and habits vary a lot, so expect a factor-of-two error."),
    ("How many coffees are sold in a city of 1 million people each year?", [("population", 1_000_000, ""), ("share of adults (and over-15s)", 0.8, ""), ("share who buy coffee out", 0.35, ""), ("cups per buyer per week", 3, ""), ("weeks", 50, "")],
     lambda p: p[0] * p[1] * p[2] * p[3] * p[4], "cups per year", "Home-brewed coffee is excluded."),
    ("About how many words are in a 300-page novel?", [("pages", 300, ""), ("lines per page", 30, ""), ("words per line", 11, "")], lambda p: p[0] * p[1] * p[2], "words", "Layout varies; typical novels run 60,000-100,000 words."),
    ("How many tennis balls fit in a school bus?", [("bus interior volume (m^3)", 35, ""), ("tennis ball volume (cm^3)", 160, ""), ("packing fraction", 0.64, "")],
     lambda p: p[0] * 1_000_000 * p[2] / p[1], "balls", "A random sphere packing fills roughly 64% of the space."),
    ("How long would it take to count to one million out loud?", [("numbers", 1_000_000, ""), ("seconds per number (average)", 1.5, ""), ("seconds per hour", 3600, "")], lambda p: p[0] * p[1] / p[2], "hours", "Longer numbers take longer to say, so the average is a guess; no breaks or sleep."),
    ("How many slices of pizza are eaten in a country of 60 million in a week?", [("population", 60_000_000, ""), ("share who eat pizza in a week", 0.4, ""), ("slices per pizza eater per week", 4, "")], lambda p: p[0] * p[1] * p[2], "slices", "Frozen and fresh pizza are both counted."),
    ("How much paper is used by a school of 800 students in a year?", [("students", 800, ""), ("sheets per student per school day", 4, ""), ("school days", 180, "")], lambda p: p[0] * p[1] * p[2], "sheets", "Photocopying for staff is not counted, so the true figure is higher."),
    ("How many heartbeats does a person have in 80 years?", [("beats per minute", 70, ""), ("minutes per hour", 60, ""), ("hours per day", 24, ""), ("days per year", 365, ""), ("years", 80, "")], lambda p: p[0] * p[1] * p[2] * p[3] * p[4], "beats", "Resting rate; exercise raises it, sleep lowers it."),
    ("How many car journeys happen in a town of 100,000 in a day?", [("population", 100_000, ""), ("share who drive on a given day", 0.5, ""), ("trips per driver per day", 2.5, "")], lambda p: p[0] * p[1] * p[2], "trips", "Passengers are not counted separately."),
    ("How many hairs are on a human head?", [("scalp area (cm^2)", 600, ""), ("hairs per cm^2", 170, "")], lambda p: p[0] * p[1], "hairs", "Density varies with hair colour and age (about 100,000 is the typical quoted figure)."),
    ("How much does the world's population drink in a day (litres, drinks only)?", [("people", 8_000_000_000, ""), ("litres of drinks per person per day", 2.0, "")], lambda p: p[0] * p[1], "litres per day", "Includes tea, coffee, water and other drinks; climate and diet change the average."),
    ("How many emails does a company of 5,000 employees receive in a month?", [("employees", 5000, ""), ("emails per employee per working day", 60, ""), ("working days", 21, "")], lambda p: p[0] * p[1] * p[2], "emails", "Spam filters and mailing lists make the real figure differ."),
    ("How many steps does someone walk in a year?", [("steps per day", 6000, ""), ("days", 365, "")], lambda p: p[0] * p[1], "steps", "Average daily steps vary widely between people."),
    ("How many books are in a university library with 40,000 students?", [("students", 40000, ""), ("books per student (rule of thumb)", 25, "")], lambda p: p[0] * p[1], "books", "This is a loose rule of thumb; the actual holdings depend on the research profile."),
    ("How long to fill an Olympic swimming pool (2,500,000 litres) from a garden hose?", [("pool volume (litres)", 2_500_000, ""), ("hose flow (litres per minute)", 20, ""), ("minutes per hour", 60, ""), ("hours per day", 24, "")], lambda p: p[0] / p[1] / p[2] / p[3], "days", "Running non-stop, no evaporation or leaks."),
    ("How many pages of text could you read in a week of 1 hour per day?", [("reading speed (pages per hour)", 40, ""), ("hours per day", 1, ""), ("days", 7, "")], lambda p: p[0] * p[1] * p[2], "pages", "Dense material is slower."),
    ("How many litres of fuel do cars in a city of 2 million use each day?", [("cars (per person)", 0.4, ""), ("population", 2_000_000, ""), ("km per car per day", 30, ""), ("litres per km", 0.08, "")], lambda p: p[0] * p[1] * p[2] * p[3], "litres per day", "Includes private cars only; goods vehicles and buses are excluded."),
]
FERMI_ASK = ["Estimate: {q}", "{q} Show your assumptions.", "Fermi question: {q}", "Give a rough estimate. {q}", "Without looking anything up, {q_l}"]


def fmtn(v):
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}"


def gen_fermi(rng):
    n = 0
    for q0, params, f, unit, caveat in FERMI:
        vals = [v for _, v, _ in params]
        base = f(vals)
        q = pick(rng, FERMI_ASK).format(q=q0, q_l=q0[0].lower() + q0[1:])
        lines = "\n".join(f"- {lab}: {v:,.4g}" for lab, v, _ in params)
        a = (f"Assumptions:\n{lines}\n\nCombining them: about {sig(base)} {unit}. {caveat}")
        turns = [("user", q), ("assistant", a)]
        # follow-up: change the most influential assumption by x2 or x0.5
        j = rng.randrange(len(params))
        fac = rng.choice([2, 0.5, 3])
        vals2 = vals[:]
        vals2[j] = vals2[j] * fac
        new = f(vals2)
        eff = (f"it {'divides' if new < base else 'multiplies'} the result by {max(new / base, base / new):g}")
        turns += [("user", pick(rng, [f"What if the {params[j][0]} were {fac:g} times what you assumed?", f"How sensitive is that to the {params[j][0]}? Try {fac:g}x."])),
                  ("assistant", f"With the {params[j][0]} at {fmtn(vals2[j])}, the estimate becomes about {sig(new)} {unit}: {eff}. {'Quantities that appear in the numerator scale the answer directly; ones in the denominator scale it inversely.'}")] if rng.random() < 0.6 else []
        emit("fermi_estimation", turns, "medium", "fermi", rng)
        n += 1
        if rng.random() < 0.5:   # second framing: ask for the range
            lo, hi = f([v * 0.7 if i != j else v for i, v in enumerate(vals)]), f([v * 1.4 if i != j else v for i, v in enumerate(vals)])
            emit("fermi_estimation", [("user", f"{q0} Also give a plausible range."), ("assistant", f"Point estimate: about {sig(base)} {unit}. If every input is off by 30-40% in the same direction the result moves between roughly {sig(min(lo, hi))} and {sig(max(lo, hi))} {unit}, so quote it as a range. {caveat}")], "medium", "fermi", rng)
            n += 1
    return n


# ------------------------------------------------------------------ L. weighted decisions with computed flip point
DEC = [
    ("choosing a laptop", ["price", "battery", "weight", "screen"], ["Laptop A", "Laptop B", "Laptop C"]),
    ("picking a flat to rent", ["rent", "commute", "space", "noise level"], ["Flat 1", "Flat 2", "Flat 3"]),
    ("choosing between job offers", ["salary", "growth", "work-life balance", "team"], ["Offer X", "Offer Y", "Offer Z"]),
    ("selecting a holiday destination", ["cost", "weather", "things to do", "travel time"], ["Lisbon", "Prague", "Athens"]),
    ("choosing a database for a small project", ["ease of setup", "performance", "ecosystem", "cost"], ["Option P", "Option Q", "Option R"]),
    ("choosing a car to buy", ["running cost", "comfort", "reliability", "safety"], ["Car 1", "Car 2", "Car 3"]),
]


def totals(w, scores):
    return [sum(wi * s for wi, s in zip(w, row)) / sum(w) for row in scores]


def gen_weighted(rng, count=26):
    n, tries = 0, 0
    while n < count and tries < 400:
        tries += 1
        ctx, crit, opts = rng.choice(DEC)
        k = len(crit)
        w = [rng.choice([10, 20, 30, 40]) for _ in range(k)]
        scores = [[rng.randint(3, 9) for _ in range(k)] for _ in opts]
        t = totals(w, scores)
        order = sorted(range(len(opts)), key=lambda i: -t[i])
        if t[order[0]] - t[order[1]] < 0.15 or len(set(round(x, 6) for x in t)) < len(t):
            continue
        win, run = order[0], order[1]
        # find the weight of criterion j (others rescaled proportionally to total weight 100) at which the runner-up overtakes the winner
        j = rng.randrange(k)
        flip = None
        for step in range(0, 1001):
            wj = step / 10
            rest = sum(w[i] for i in range(k) if i != j)
            ww = [w[i] * (100 - wj) / rest if i != j else wj for i in range(k)]
            tt = totals(ww, scores)
            if tt[run] > tt[win]:
                flip = wj
                break
        tab = "\n".join(f"- {opts[i]}: " + ", ".join(f"{crit[c]} {scores[i][c]}" for c in range(k)) for i in range(len(opts)))
        wtxt = ", ".join(f"{crit[c]} {w[c]}" for c in range(k))
        q = pick(rng, [f"I'm {ctx}. Scores out of 10 and importance weights:\n{tab}\nWeights: {wtxt}.\nWhich option wins on a weighted score?",
                       f"Weighted decision for {ctx}. Weights: {wtxt}. Scores:\n{tab}\nRank the options and tell me how robust the winner is."])
        base_w = 100 * w[j] / sum(w)
        a = f"Weighted scores (sum of weight x score divided by total weight): " + ", ".join(f"{opts[i]} {t[i]:.2f}" for i in order) + f". {opts[win]} comes first, ahead of {opts[run]} by {t[win] - t[run]:.2f} points. "
        if flip is None:
            a += f"The ranking of the top two is robust against the {crit[j]} weight: even if {crit[j]} counted for anything from 0% to 100% of the total, {opts[win]} stays ahead."
        else:
            direction = "more" if flip > base_w else "less"
            a += f"Sensitivity: the winner changes if {crit[j]} carried {direction} weight. With the other weights kept in proportion, {opts[run]} overtakes {opts[win]} once {crit[j]} reaches about {flip:g}% of the total weight (currently {base_w:.0f}%)."
        turns = [("user", q), ("assistant", a)]
        if n % 3 == 0:
            j2 = rng.randrange(k)
            w2 = w[:]
            w2[j2] = w2[j2] * 3
            t2 = totals(w2, scores)
            o2 = sorted(range(len(opts)), key=lambda i: -t2[i])
            turns += [("user", f"What if {crit[j2]} mattered three times as much?"), ("assistant", f"New weights: " + ", ".join(f"{crit[c]} {w2[c]}" for c in range(k)) + ". Scores: " + ", ".join(f"{opts[i]} {t2[i]:.2f}" for i in o2) + f". {'Same winner: ' + opts[o2[0]] if o2[0] == win else opts[o2[0]] + ' now wins'}.")]
        emit("weighted_decision_sensitivity", turns, "hard", "wd", rng)
        n += 1
    return n


# ------------------------------------------------------------------ M. ambiguity / underspecification
AMB = [
    ("How many days are there between 1 March and 15 March?", "It depends on whether the endpoints are counted. The difference is 14 days; counting both dates inclusively gives 15. State which you mean (for a deadline, 'days left' usually excludes today)."),
    ("Split the 90 euro bill between us fairly.", "'Fairly' and 'us' are open. Equal split among 3 people is 30 each, among 4 it is 22.50. Fair could also mean by what each person ordered, or include the tip. How many people, and does the 90 include service?"),
    ("Which is faster, 100 or 62?", "These numbers have no units. If they are km/h and mph, 100 km/h is about 62.1 mph, so they are almost the same (100 km/h is very slightly faster). If both are km/h, 100 is faster. The units decide."),
    ("What's the average of the last three scores?", "No scores were given, so I can't compute it. Also 'average' could mean the mean or the median. Send the three numbers and say which average you want."),
    ("A train leaves at 3 and travels at 80 km/h. When does it arrive?", "Missing: the distance or the journey time, and whether 3 means 3 a.m. or 3 p.m. With distance d, arrival is 3:00 plus d/80 hours (for 200 km, 2.5 hours, so 5:30)."),
    ("Is it cheaper to take the bus or the car?", "It depends on the trip. For a car: fuel, parking, wear and tolls divided by occupants. For the bus: fare or pass. A 10 km trip with a 2 euro ticket versus 0.12 euro/km fuel (1.20) plus parking is a different answer than a 100 km trip. Give the distance, car running cost and fares."),
    ("The meeting is at 10. Will I make it?", "I can't tell without where you start, how you are travelling and how long it takes. Also confirm the time zone and whether it is 10 a.m. If the trip takes T minutes, leave by 10:00 minus T minus a buffer."),
    ("Round 2.5 to the nearest whole number.", "There's more than one convention. Round-half-up gives 3; round-half-to-even (banker's rounding, used by Python's round) gives 2. Which rule should be used?"),
    ("How many people can sit at 6 tables?", "Seats per table are missing, and the table shape matters. At 8 seats per table, 48; at 6 seats, 36."),
    ("Add 20% to 50, then take 20% off. What do I get?", "Read in order: 50 + 20% = 60, then 60 - 20% = 48. If 'take 20% off' referred to the original 50 it would be 50. The sequence of the percentages matters, and the second 20% applies to the new base."),
    ("What's the next number: 2, 4, 8, ...?", "Several rules fit three terms. Doubling gives 16. 'Add 2, then 4, then 6, ...' (the step grows by 2) gives 14. With so few terms the rule isn't determined; doubling (16) is the simplest guess, but ask for more terms or the rule."),
    ("Is 'a dozen eggs for 3 euros' better than 'eggs at 0.30 each'?", "Per egg, the dozen costs 3 / 12 = 0.25 euro, which is cheaper than 0.30, assuming both are the same size and quality. If you only need 6 eggs, 6 x 0.30 = 1.80 is less than 3."),
    ("Everyone in the team got a raise. Did the average salary go up?", "Yes if every raise was positive and the team did not change: the average rises by the average raise. But if the team changed (higher earners left or new hires started lower), the average could fall. Also check the percentage versus absolute amount."),
    ("How tall is the tallest of three friends if their heights average 170 cm?", "That can't be determined. The mean fixes the total (510 cm), but the tallest could be 170 cm (all equal) or taller, up to just under 510 cm minus two small heights. We need the individual heights or more constraints."),
    ("Is it 'before noon' if the call starts at 12:00?", "At exactly 12:00 it is not before noon; 12:00 is noon itself. Clarify whether 'before noon' includes 12:00 and whether the call needs to finish by then."),
    ("What time is it in Tokyo when it's 9 am in London?", "It depends on the date because of daylight saving. Tokyo is UTC+9 all year. London is UTC+0 in winter, so 9:00 London is 18:00 Tokyo; in summer London is UTC+1, so 9:00 London is 17:00 Tokyo."),
    ("Which has more calories, 100 g of the cake or one slice?", "It depends on the slice's weight. If a slice weighs more than 100 g it has more calories than 100 g; if it's 80 g it has fewer. Give the slice mass."),
    ("How long does 5 GB take to download?", "It depends on the speed. At 50 Mbit/s: 5 GB is about 40,000 Mbit, so 800 seconds (about 13 minutes). At 10 Mbit/s: roughly 67 minutes. Also note GB (bytes) versus Mbit (bits): divide the speed by 8 for MB/s."),
    ("Is the sum of two prime numbers always even?", "Not always, because 2 is prime and even. 2 + 3 = 5 is odd. For two odd primes the sum is even. The statement needs 'two odd primes'."),
    ("Who won, A or B? A scored 3 in the first half and B scored 2. Then A scored 1 more.", "A leads 4 to 2 only if B scored nothing in the second half. The statement doesn't say what B scored then, so the winner can't be fixed. If B scored 0, A wins 4-2."),
    ("My bill is 120 and the tip is 15%. What do I pay?", "If the 15% applies to the 120 bill: tip is 18, total 138. If the 120 already includes the tip, the pre-tip bill is about 104.35 and the tip about 15.65. Which one is the case?"),
    ("How many months until the project ends if it ends in the 4th quarter?", "Quarter 4 is October to December, so the end could be anywhere from October to December (end date between 31 October and 31 December), and the starting month isn't given. State the current date and the exact end date."),
    ("Is the glass half full or half empty?", "As a description they're equivalent, because both say the glass is 50% full. The framing changes the emphasis, not the amount. In practice what matters is whether the liquid is being poured in or drunk."),
    ("Divide 10 by 0.5.", "Not ambiguous in arithmetic: 10 / 0.5 = 20, because 0.5 fits into 10 twenty times. The common mix-up is to expect a smaller result than 10, but dividing by less than 1 increases the value."),
    ("Which weighs more, a kilo of feathers or a kilo of steel?", "They weigh the same: one kilogram each. The steel is much denser, so it takes up far less space. The question invites the wrong answer by focusing on the materials, not the stated mass."),
]
AMB_ASK = ["{q}", "Quick question: {q}", "{q} (I just want the answer.)", "Can you answer this? {q}"]


def gen_ambig(rng):
    n = 0
    for q0, a in AMB:
        q = pick(rng, AMB_ASK).format(q=q0)
        emit("ambiguity_check", [("user", q), ("assistant", a)], "medium", "amb", rng, src="authored")
        n += 1
    for q0, a in AMB[:14]:
        q = pick(rng, [f"Before answering, what is missing or unclear in this question? \"{q0}\"", f"Is this question fully specified? \"{q0}\"", f"What assumptions would I have to make to answer: \"{q0}\""])
        emit("ambiguity_check", [("user", q), ("assistant", a)], "medium", "amb2", rng, src="authored")
        n += 1
    return n


# ------------------------------------------------------------------ N. consistency checks (weighted union-find)
QTY = [("people", "{x} is {d} years older than {y}.", "years"), ("books", "{x} costs {d} euros more than {y}.", "euros"), ("runners", "{x} finished {d} minutes after {y}.", "minutes"),
       ("towns", "{x} is {d} metres higher above sea level than {y}.", "metres"), ("rooms", "{x} is {d} degrees warmer than {y}.", "degrees")]
NAMESETS = [["Ana", "Ben", "Cleo", "Dev", "Eli"], ["the novel", "the atlas", "the guide", "the journal", "the comic"], ["Kim", "Lou", "Max", "Nia", "Oto"],
            ["Hill Town", "Riverside", "Oakfield", "Stonebridge", "Westham"], ["the attic", "the kitchen", "the cellar", "the hall", "the study"]]


def gen_consistency(rng, count=40):
    n, tries = 0, 0
    while n < count and tries < 600:
        tries += 1
        qi = rng.randrange(len(QTY))
        kind, tmpl, unit = QTY[qi]
        names = NAMESETS[qi]
        k = rng.choice([3, 4, 4])
        nodes = rng.sample(names, k)
        val = {x: rng.randint(0, 20) for x in nodes}
        stmts = []
        used_pairs = set()
        for _ in range(rng.choice([k, k + 1])):
            x, y = rng.sample(nodes, 2)
            if frozenset((x, y)) in used_pairs:
                continue
            used_pairs.add(frozenset((x, y)))
            stmts.append([x, y, val[x] - val[y]])
        stmts = [s for s in stmts if s[2] > 0] or None
        if not stmts or len(stmts) < 3:
            continue
        corrupt = rng.random() < 0.55
        if corrupt:
            i = rng.randrange(len(stmts))
            stmts[i][2] += rng.choice([-2, -1, 1, 2, 3])
            if stmts[i][2] <= 0:
                continue
        # weighted union-find consistency (stated difference val[x] - val[y] = d)
        parent = {x: x for x in nodes}
        off = {x: 0 for x in nodes}   # value[x] - value[root]

        def find(x):
            if parent[x] == x:
                return x, 0
            r, o = find(parent[x])
            parent[x] = r
            off[x] += o
            return r, off[x]
        bad = None
        for idx, (x, y, d) in enumerate(stmts):
            rx, ox = find(x)
            ry, oy = find(y)
            if rx == ry:
                if ox - oy != d:
                    bad = (idx, ox - oy)
                    break
            else:
                parent[rx] = ry
                off[rx] = d + oy - ox
        if corrupt and bad is None:
            continue   # the changed number happened to stay consistent; not what we want
        if not corrupt and bad is not None:
            continue
        lines = "\n".join(f"{i + 1}. " + tmpl.format(x=cap(x), y=y, d=d) for i, (x, y, d) in enumerate(stmts))
        form = rng.randrange(3)
        q = [f"Do these statements about {kind} fit together?\n{lines}", f"Check the following for consistency:\n{lines}\nIs there a contradiction?", f"Can all of these be true at once?\n{lines}"][form]
        yes = ["Yes, they are consistent.", "No, there is no contradiction.", "Yes, they can all be true."][form]
        no = ["No, they contradict each other.", "Yes, there is a contradiction.", "No, they cannot all be true."][form]
        if bad:
            idx, derived = bad
            x, y, d = stmts[idx]
            a = (f"{no} The earlier statements (1-{idx}) already fix the gap between {x} and {y}: it comes out as {derived} {unit} ({x} over {y}), but statement {idx + 1} says {d}. "
                 f"At least one of the {idx + 1} statements involved in that chain must be wrong; the rest of the statements cannot repair it. (Checked by propagating differences through the statements in order.)")
        else:
            roots = {find(z)[0] for z in nodes}
            if len(roots) == 1:
                r = next(iter(roots))
                rel = {z: find(z)[1] for z in nodes}
                m = min(rel.values())
                table = ", ".join(f"{z}: +{rel[z] - m}" for z in sorted(nodes, key=lambda z: rel[z] - m))
                a = f"{yes} Taking the lowest one as the baseline, the differences are: {table} ({unit}). Only differences are fixed; the actual values need one more fact."
            else:
                a = f"{yes} They only link some of the items, so several gaps stay undetermined."
        emit("consistency_check", [("user", q), ("assistant", a)], "medium", "cons", rng)
        n += 1
    return n


# ------------------------------------------------------------------ O. analogies and odd-one-out
ANALOGY = [
    ("Finger is to hand as toe is to ___.", "foot", "A finger is a digit of the hand; a toe is a digit of the foot."),
    ("Pilot is to aircraft as captain is to ___.", "ship", "The first operates the vehicle named second; a captain commands a ship."),
    ("Seed is to tree as egg is to ___.", "bird (or other animal)", "Each is the early form from which the mature organism develops."),
    ("Thermometer is to temperature as odometer is to ___.", "distance travelled", "The first instrument measures the quantity named second."),
    ("Chapter is to book as scene is to ___.", "play (or film)", "A chapter is a section of a book; a scene is a section of a play or film."),
    ("Hammer is to nail as screwdriver is to ___.", "screw", "The tool is used to drive the item named second."),
    ("Cold is to freezing as hot is to ___.", "boiling", "Freezing is the extreme of cold, boiling the extreme of hot (degree analogy)."),
    ("Librarian is to books as curator is to ___.", "exhibits (artworks, collections)", "Each looks after a collection."),
    ("Peak is to valley as crest is to ___.", "trough", "Opposite points of a wave or landscape."),
    ("Leaf is to branch as page is to ___.", "book", "A leaf grows on a branch; a page belongs to a book (part to whole)."),
    ("Sculptor is to marble as potter is to ___.", "clay", "Creator and typical material."),
    ("Vaccine is to disease as seat belt is to ___.", "injury (crash harm)", "Both are preventive measures against a harm."),
    ("Doctor is to patient as teacher is to ___.", "student", "Professional and the person they serve."),
    ("Sprint is to marathon as snack is to ___.", "meal (feast)", "Short and light versus long and heavy form of the same type of activity."),
    ("Drought is to rain as famine is to ___.", "food", "The first is a shortage of the thing named second."),
]
PROPS = [("prime numbers", lambda n: n > 1 and all(n % i for i in range(2, isqrt(n) + 1))), ("square numbers", lambda n: isqrt(n) ** 2 == n), ("powers of two", lambda n: n > 0 and n & (n - 1) == 0),
         ("multiples of 7", lambda n: n % 7 == 0), ("palindromes", lambda n: str(n) == str(n)[::-1]), ("even numbers", lambda n: n % 2 == 0), ("triangular numbers", lambda n: isqrt(8 * n + 1) ** 2 == 8 * n + 1),
         ("multiples of 5", lambda n: n % 5 == 0)]


def gen_analogy(rng):
    n = 0
    for q0, ans, why in ANALOGY:
        q = pick(rng, [q0, f"Complete the analogy: {q0}", f"What fills the blank? {q0}"])
        a = f"{cap(ans)}. {why}" if rng.random() < 0.7 else f"The answer is {ans}: {why[0].lower() + why[1:]}"
        emit("analogy", [("user", q), ("assistant", a)], "easy", "analogy", rng, src="authored")
        n += 1
    made, tries = 0, 0
    while made < 26 and tries < 4000:
        tries += 1
        pname, pred = rng.choice(PROPS)
        k = rng.choice([4, 5])
        # k-1 numbers with the property and one without (or the reverse)
        inv = rng.random() < 0.5
        pool_t = [x for x in range(1, 100) if pred(x)]
        pool_f = [x for x in range(1, 100) if not pred(x)]
        if len(pool_t) < k or len(pool_f) < k:
            continue
        if inv:
            nums = rng.sample(pool_f, k - 1) + rng.sample(pool_t, 1)
            odd_val = nums[-1]
        else:
            nums = rng.sample(pool_t, k - 1) + rng.sample(pool_f, 1)
            odd_val = nums[-1]
        # verify that the odd one is uniquely identified by this property; also make sure no other simple property isolates a different number equally well
        cnt_true = sum(1 for x in nums if pred(x))
        if cnt_true not in (1, k - 1):
            continue
        rng.shuffle(nums)
        ambiguous = 0
        for pn2, p2 in PROPS:
            if pn2 == pname:
                continue
            c = sum(1 for x in nums if p2(x))
            if c in (1, k - 1):
                ambiguous += 1
        if ambiguous:
            continue
        q = pick(rng, [f"Which number doesn't belong: {', '.join(map(str, nums))}?", f"Odd one out in {', '.join(map(str, nums))}? Explain the rule.", f"Find the outlier: {' | '.join(map(str, nums))}"])
        same = [x for x in nums if x != odd_val]
        a = f"{odd_val}. " + (f"All the others are in the group '{pname}', but {odd_val} is not." if not inv else f"Only {odd_val} belongs to the group '{pname}'; the rest ({', '.join(map(str, sorted(same)))}) do not.")
        emit("odd_one_out", [("user", q), ("assistant", a)], "easy", "odd", rng)
        made += 1
    return n + made


# ------------------------------------------------------------------ P. cognitive-reflection traps (computed) and decisions that depend on conditions
def gen_traps(rng):
    n = 0
    for total, diff in [(110, 100), (150, 120), (210, 200), (130, 100), (330, 300), (170, 150)]:   # cents; total - diff is even
        ball = (total - diff) // 2
        bat = ball + diff
        assert ball + bat == total and bat - ball == diff
        q = pick(rng, [f"A bat and a ball cost {total / 100:.2f} together. The bat costs {diff / 100:.2f} more than the ball. How much is the ball?",
                       f"Together a pen and a cap cost {total / 100:.2f}. The pen costs {diff / 100:.2f} more than the cap. What does the cap cost?"])
        tempt = total - diff
        a = (f"{ball / 100:.2f}. The tempting answer is {tempt / 100:.2f}, but with that the two prices would differ by {(diff - tempt) / 100:.2f} less than stated... "
             f"let the cheaper item cost c: the other costs c + {diff / 100:.2f}, so 2c + {diff / 100:.2f} = {total / 100:.2f} and c = {ball / 100:.3f}. Check: {ball / 100:.2f} + {bat / 100:.2f} = {total / 100:.2f} and {bat / 100:.2f} - {ball / 100:.2f} = {diff / 100:.2f}.")
        a = a.replace(f"with that the two prices would differ by {(diff - tempt) / 100:.2f} less than stated... let", f"the item prices would then be {tempt / 100:.2f} and {(tempt + diff) / 100:.2f}, totalling {(2 * tempt + diff) / 100:.2f}, not {total / 100:.2f}. So let")
        emit("reflection_trap", [("user", q), ("assistant", a)], "medium", "bat", rng)
        n += 1
    for full in (48, 30, 20, 60, 100, 12):
        q = pick(rng, [f"A patch of lily pads doubles in size every day. It covers the whole pond on day {full}. On which day did it cover half the pond?", f"Algae double every day and fill a tank on day {full}. When was the tank half full?"])
        a = f"Day {full - 1}. If it doubles each day, the day before it was full, it was exactly half full. (The tempting answer, day {full // 2}, treats the growth as steady rather than doubling.)"
        emit("reflection_trap", [("user", q), ("assistant", a)], "easy", "lily", rng)
        n += 1
    for m, t in [(5, 5), (3, 3), (8, 8), (10, 10)]:
        big = rng.choice([100, 40, 60])
        a_ = f"{t} minutes. If {m} machines make {m} items in {t} minutes, each machine makes one item in {t} minutes. So {big} machines make {big} items in the same {t} minutes."
        emit("reflection_trap", [("user", f"If {m} machines take {t} minutes to make {m} widgets, how long do {big} machines take to make {big} widgets?"), ("assistant", a_)], "medium", "mach", rng)
        n += 1
    for d1, d2 in [(60, 40), (30, 60), (20, 30), (50, 100), (40, 80)]:
        avg = Fr(2 * d1 * d2, d1 + d2)
        q = f"You drive to a town at {d1} km/h and come back the same way at {d2} km/h. What is your average speed for the whole trip?"
        a = f"{float(avg):.1f} km/h, not {(d1 + d2) / 2:g}. You spend more time at the slower speed. For a distance D each way, the total time is D/{d1} + D/{d2}, and the total distance is 2D, so the average is 2D / (D/{d1} + D/{d2}) = {frac_str(avg)} km/h (the harmonic mean)."
        emit("reflection_trap", [("user", q), ("assistant", a)], "medium", "avg", rng)
        n += 1
    for p in (10, 20, 25, 30, 50):
        up = Fr(100 + p, 100)
        down = 1 / up
        pdown = (1 - down) * 100
        q = pick(rng, [f"A share price rises by {p}% and then falls by {p}%. Is it back where it started?", f"A price goes up {p}% on Monday and drops {p}% on Tuesday. Net change?"])
        net = up * (Fr(100 - p, 100))
        a = f"No. Multiply: (1 + {p}/100) x (1 - {p}/100) = {float(net):.4f}, so the price ends {float((1 - net) * 100):.2f}% lower than where it started. The fall applies to the bigger, risen price. To get back to the start you would need a fall of {float(pdown):.2f}%."
        emit("reflection_trap", [("user", q), ("assistant", a)], "medium", "pct", rng)
        n += 1
    for tot, but in [(17, 9), (25, 7), (30, 12), (50, 20)]:
        q = f"A farmer has {tot} sheep. All but {but} run away. How many are left?"
        emit("reflection_trap", [("user", q), ("assistant", f"{but}. 'All but {but}' means every sheep except {but}, so {but} remain; the subtraction {tot} - {but} = {tot - but} is the number that ran away.")], "easy", "sheep", rng)
        n += 1
    return n


DECIDE = [
    ("Should I take the train or fly for a 600 km trip?", "Default: take the train if door-to-door times are within about an hour of each other, since airports add check-in, security and transfers. Fly if the train takes more than about 5 hours or costs much more.", "Changes the answer: train journey over ~5 h, flight price much cheaper, heavy luggage (favours train), tight schedule with direct flight, strikes or delays on the rail line."),
    ("Is it worth buying a 300 euro warranty on a 900 euro laptop?", "Usually not: statistically a warranty costs more than the expected repair bill, and many cards or consumer laws already cover the first two years. Buying one can make sense if a repair costs a large part of the price and you can't afford it.", "Changes the answer: no credit-card cover, a model with known failure rates, a work-critical machine without a spare, rough use (travel, kids)."),
    ("Should our small team write a custom tool or buy a subscription?", "Buy first if the problem is common: a subscription costs less than the developer time. Build only when the need is distinctive or the tool is core to what you sell.", "Changes the answer: subscription price rising sharply, data-privacy limits, integration needs that off-the-shelf tools can't meet, a developer with spare time that has no better use."),
    ("Should I pay off a 4% loan early or invest the money?", "If a safe return is below 4%, paying the loan gives a guaranteed 4% saving. Investing might return more but with risk. A balanced approach keeps an emergency fund, then pays down debt when the rate is above what you could earn safely.", "Changes the answer: a loan rate above ~6-7% (pay it off), a tax-advantaged employer match (take it first), no emergency fund, the loan having prepayment penalties."),
    ("Is a 10-minute walk or a 3-minute bus ride better for my commute?", "If the bus runs every few minutes and costs little, the walk is competitive door-to-door and gives exercise. If the bus comes every 20 minutes, the walk is usually faster in practice.", "Changes the answer: bus frequency, weather, carrying heavy items, cost per ride, timetable reliability."),
    ("Should I accept the higher-paying job with the longer hours?", "It depends on how you value time. Convert the pay gain to an hourly figure: extra pay divided by extra hours, including the commute. If it is below what your free hour is worth to you, the extra pay isn't compensating.", "Changes the answer: career growth, health impact, family commitments, whether the hours are fixed or expected to grow, the notice period."),
    ("Is it better to rent or buy a car for a 3-month project?", "For three months renting or a subscription almost always wins: purchase costs and depreciation are large relative to the period, and resale takes time.", "Changes the answer: long-term rental price above buying and reselling a used car, extended project, need for modifications, difficult rental availability."),
    ("Is it worth cooking at home instead of ordering food most nights?", "Usually yes financially: groceries typically cost a third to half of delivery for similar meals, even counting time. The case weakens if you throw away a lot of food or your time is very valuable.", "Changes the answer: food waste, working late most days, tiny kitchen, meal-kit or batch cooking options that cut time."),
    ("Should the team migrate to a new framework this quarter?", "Only if there's a concrete benefit (security fixes, performance, hiring) and a bounded plan. Otherwise the cost of a rewrite and the risk to delivery often outweigh the gain.", "Changes the answer: end of support for the current one, a blocking bug, the product being about to be rewritten anyway, a small codebase where migration costs days."),
    ("Should I back up my files to the cloud or an external drive?", "Both, if possible: a local drive is fast and private; a cloud copy protects against fire, theft and disk failure. If only one, choose cloud for off-site safety.", "Changes the answer: very large data sets, privacy rules against cloud storage, slow upload bandwidth, files that change constantly."),
]


def gen_decide(rng):
    n = 0
    for q0, rec, chg in DECIDE:
        for rep in range(2):
            if rep == 0:
                q = q0
                a = f"{rec}\n\n{chg}"
            else:
                q = f"{q0} What would change your answer?"
                a = f"{chg[0].lower() + chg[1:]} Otherwise: {rec[0].lower() + rec[1:]}"
            emit("conditional_decision", [("user", q), ("assistant", a)], "medium", "dec", rng, src="authored")
            n += 1
    return n


def run(rng):
    return {"fallacy": gen_fallacies(rng), "causal": gen_causal(rng), "fermi": gen_fermi(rng), "weighted": gen_weighted(rng), "ambig": gen_ambig(rng),
            "consistency": gen_consistency(rng), "analogy_odd": gen_analogy(rng), "traps": gen_traps(rng), "decide": gen_decide(rng)}
