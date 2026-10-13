"""Category 11: context & multi-turn conversation (target ~500). Running state in the computed dialogs is tracked in code."""
from fractions import Fraction as Fr
from lib import *

CAT = "multi_turn_context"
R = seeded("context")
out = []
NAMES = ["Maya", "Arjun", "Lena", "Omar", "Sofia", "Ken", "Priya", "Tom", "Aisha", "Diego", "Nora", "Ravi"]


def add(sub, turns, g, diff="medium", src="computed", sysm="rand"):
    if sysm == "rand":
        sysm = pick(R, SYS_GENERAL)
    out.append(mk(CAT, sub, turns, g, diff, src, sysm, {"turns": len([t for t in turns if t[0] == "user"])}))


def fmt(x):
    x = Fr(x)
    return str(x.numerator) if x.denominator == 1 else (f"{float(x):.2f}".rstrip("0").rstrip("."))


# ---------------------------------------------------------------- M1: running arithmetic with references
OPS = [
 ("Double that.", lambda v: v * 2, "Doubling {c} gives {r}."), ("Now add {n}.", lambda v, n: v + n, "Adding {a} to {c} gives {r}."), ("Take away {n}.", lambda v, n: v - n, "Taking {a} away from {c} leaves {r}."),
 ("Triple it.", lambda v: v * 3, "Tripling {c} gives {r}."), ("Halve it.", lambda v: v / 2, "Half of {c} is {r}."), ("Multiply that by {n}.", lambda v, n: v * n, "{c} times {a} is {r}."),
 ("Now square it.", lambda v: v * v, "{c} squared is {r}."), ("Increase it by {p} percent.", lambda v, p: v * (100 + p) / 100, "{a}% more than {c} is {r}."),
]
for n in range(130):
    v = Fr(R.randint(3, 40))
    turns = [("user", R.choice([f"Let's start with {v}.", f"Take the number {v}.", f"Start from {v}."])), ("assistant", f"Okay, we're starting from {v}.")]
    steps = R.choice([2, 3, 4])
    cur = v
    for k in range(steps):
        t, fn, lab = R.choice(OPS)
        if "{n}" in t or "{p}" in t:
            arg = R.randint(2, 9) if "{n}" in t else R.choice([10, 20, 25, 50])
            txt = t.format(n=arg, p=arg)
            new = fn(cur, arg)
            sent = lab.format(a=arg, c=fmt(cur), r=fmt(new))
        else:
            txt = t
            new = fn(cur)
            sent = lab.format(c=fmt(cur), r=fmt(new))
        turns += [("user", txt), ("assistant", sent)]
        cur = new
    turns += [("user", R.choice(["What's the result now?", "So what do we have?", "Remind me where we ended up."])), ("assistant", f"We ended up with {fmt(cur)}.")]
    add("running_arithmetic", turns, f"m1_{n}")

# ---------------------------------------------------------------- M2: list editing with references
LISTS = [("shopping list", ["milk", "eggs", "bread", "apples", "rice", "tomatoes", "cheese", "coffee"]), ("packing list", ["passport", "charger", "umbrella", "sunscreen", "headphones", "jacket", "book", "snacks"]),
         ("reading list", ["Dune", "Emma", "Hamlet", "Ulysses", "Matilda", "Beloved", "Walden", "Persuasion"]), ("to-do list", ["call the bank", "water plants", "pay rent", "book dentist", "update CV", "email Sam", "buy stamps", "cook dinner"])]
for n in range(110):
    nm, pool = R.choice(LISTS)
    items = R.sample(pool, R.choice([3, 4]))
    turns = [("user", f"Start a {nm}: {', '.join(items)}."), ("assistant", f"Started your {nm}: " + ", ".join(items) + ".")]
    cur = items[:]
    for k in range(R.choice([2, 3])):
        op = R.choice(["add", "remove", "remove_ref", "sort", "count", "swap"])
        if op == "add":
            new = R.choice([p for p in pool if p not in cur] or ["extra item"])
            cur.append(new)
            turns += [("user", f"Add {new}."), ("assistant", f"Added {new}. The {nm} now has {len(cur)} items: {', '.join(cur)}.")]
        elif op == "remove" and len(cur) > 2:
            rem = R.choice(cur)
            cur.remove(rem)
            turns += [("user", f"Take {rem} off."), ("assistant", f"Removed {rem}. Now: {', '.join(cur)}.")]
        elif op == "remove_ref" and len(cur) > 2:
            idx = R.randrange(len(cur))
            ordinal = ["first", "second", "third", "fourth", "fifth"][idx] if idx < 5 else None
            if ordinal is None:
                continue
            rem = cur.pop(idx)
            turns += [("user", f"Remove the {ordinal} one."), ("assistant", f"The {ordinal} item, {rem}, is off the list. Your {nm} is now: {', '.join(cur)}.")]
        elif op == "sort":
            cur = sorted(cur)
            turns += [("user", "Put them in alphabetical order."), ("assistant", f"Alphabetical: {', '.join(cur)}.")]
        elif op == "count":
            turns += [("user", "How many are on it?"), ("assistant", f"There are {len(cur)} items: {', '.join(cur)}.")]
        else:
            if len(cur) >= 2:
                cur[0], cur[-1] = cur[-1], cur[0]
                turns += [("user", "Swap the first and last."), ("assistant", f"Swapped. Now: {', '.join(cur)}.")]
    turns += [("user", R.choice(["Read me the final list.", "What's on it now?", "Give me the whole thing again."])), ("assistant", f"Your {nm}: " + "; ".join(cur) + ".")]
    add("list_editing", turns, f"m2_{n}")

# ---------------------------------------------------------------- M3: unit chains and "what about X" follow-ups
CONV = [("miles", "kilometres", 1.609344), ("kilometres", "miles", 1 / 1.609344), ("kilograms", "pounds", 2.20462), ("pounds", "kilograms", 1 / 2.20462), ("litres", "gallons (US)", 0.264172), ("metres", "feet", 3.28084), ("inches", "centimetres", 2.54), ("centimetres", "inches", 1 / 2.54)]
for n in range(90):
    a, b, f = R.choice(CONV)
    x1, x2 = R.sample(range(2, 60), 2)
    turns = [("user", R.choice([f"How many {b} is {x1} {a}?", f"Convert {x1} {a} to {b}."])), ("assistant", f"{x1} {a} is about {x1*f:.2f} {b}."),
             ("user", R.choice([f"And {x2}?", f"What about {x2}?", f"Same for {x2}, please."])), ("assistant", f"{x2} {a} is about {x2*f:.2f} {b}.")]
    if R.random() < 0.6:
        turns += [("user", "Which of those two is bigger, and by how much?"), ("assistant", f"{x2 if x2 > x1 else x1} {a} is bigger, by {abs(x2-x1)} {a}, which is about {abs(x2-x1)*f:.2f} {b}.")]
    add("unit_followups", turns, f"m3_{n}")

# ---------------------------------------------------------------- M4: changing requirements (schedule capacity)
TASKS = [("write the report", 3), ("review the code", 2), ("answer emails", 1), ("prepare slides", 2), ("test the release", 2), ("plan the sprint", 1), ("fix the build", 2), ("update docs", 1)]
for n in range(100):
    ts = R.sample(TASKS, R.choice([3, 4]))
    cap1 = R.choice([4, 5, 6])
    cap2 = cap1 - R.choice([1, 2])
    def fit(cap):
        used, chosen = 0, []
        for name, h in ts:
            if used + h <= cap:
                chosen.append(name)
                used += h
        return chosen, used
    c1, u1 = fit(cap1)
    c2, u2 = fit(cap2)
    listing = ", ".join(f"{nme} ({h}h)" for nme, h in ts)
    turns = [("user", f"I have these tasks in this priority order: {listing}. I've got {cap1} hours today. What can I fit?"),
             ("assistant", f"Going down your priority order and adding each task that still fits: {', '.join(c1)}. That's {u1} of your {cap1} hours."),
             ("user", f"Actually a meeting came up and I only have {cap2} hours."),
             ("assistant", (f"With {cap2} hours, the same rule gives: {', '.join(c2)} ({u2}h). " + (f"That drops: {', '.join(x for x in c1 if x not in c2)}." if len(c2) < len(c1) else "Nothing changes from before.")))]
    if R.random() < 0.6:
        dropped = [nme for nme, h in ts if nme not in c2]
        turns += [("user", "What got left out?"), ("assistant", ("These didn't fit: " + ", ".join(dropped) + ". You could move them to tomorrow or ask whether any can be shortened.") if dropped else "Nothing; everything fits.")]
    add("changing_requirements", turns, f"m4_{n}")

# ---------------------------------------------------------------- M5: authored reference-resolution dialogues
DIA = [
 [("user", "Compare Python and Java for a beginner."), ("assistant", "Python has simpler syntax and gets you running quickly, which is why it's popular for learning. Java is more verbose and strictly typed, which can feel heavier at first but teaches structure well and is common in large enterprise systems."), ("user", "Which one pays more?"), ("assistant", "Pay depends more on role, location and experience than on the language alone. Both Python and Java roles can pay well; Java is common in large companies and Python is strong in data and machine learning. I can't give current figures, so check salary surveys for your region."), ("user", "OK, and which of them is easier to debug?"), ("assistant", "Python is usually quicker to try things in because you can run code interactively, but Java's compiler catches many errors before the program runs. Both have good debuggers, so the 'easier' one often comes down to familiarity.")],
 [("user", "I'm thinking of getting a dog or a cat."), ("assistant", "Both are great. Dogs need daily walks and more attention; cats are more independent but still need play and care. What's your living situation and schedule like?"), ("user", "I work long hours in a small flat."), ("assistant", "In that case a cat may fit better, since it doesn't need walks and copes with being alone for longer during the day. It would still need company in the evenings, a litter box and enrichment like toys."), ("user", "What would it cost?"), ("assistant", "Roughly: adoption fee, food, litter, vet visits and vaccinations, and some emergency savings. Costs vary a lot by region, so I'd ask a local shelter or vet for a realistic annual estimate.")],
 [("user", "Explain what a mortgage is."), ("assistant", "A mortgage is a loan used to buy property, secured by the property itself. You repay it over a long term, typically with interest, and if you can't pay, the lender can take the property."), ("user", "What's the difference between fixed and variable?"), ("assistant", "With a fixed rate, the interest rate stays the same for a set period so payments are predictable. With a variable rate, it moves with market rates, so payments can go up or down."), ("user", "Which is safer?"), ("assistant", "Fixed is safer in the sense of predictability: you know what you'll pay. Variable can be cheaper if rates fall, but you carry the risk that they rise. If a rise in payments would strain your budget, fixed is usually the safer choice.")],
 [("user", "I'm writing an email to my professor."), ("assistant", "Happy to help. What's it about, and what do you need from them?"), ("user", "I need an extension on my essay."), ("assistant", "Here's a draft:\n\nSubject: Extension request - [Course name] essay\n\nDear Professor [Name],\n\nI'm writing to ask whether I might have a short extension on the essay due [date]. [Briefly and honestly explain the reason.] I'd be grateful if I could submit it by [new date]. I understand if this isn't possible, and I'm happy to discuss.\n\nThank you for your time,\n[Your name]"), ("user", "Make it shorter."), ("assistant", "Subject: Extension request - [Course name]\n\nDear Professor [Name],\n\nCould I please have an extension on the essay due [date], until [new date]? [One-sentence reason.] Thank you for considering it.\n\n[Your name]")],
 [("user", "What's a good first programming project?"), ("assistant", "A small tool that solves a real problem for you, like a to-do list app, a budget tracker or a script that renames files. It should be small enough to finish in a weekend."), ("user", "I like the budget tracker."), ("assistant", "Nice choice. A simple plan: 1) store transactions with date, amount and category, 2) add a way to enter them, 3) show totals per category and month. Which language are you using?"), ("user", "Python."), ("assistant", "Good. Start with a list of dictionaries in memory and a command-line menu, then save to a CSV file, and later move to SQLite or a small web interface. Want me to sketch the first version?"), ("user", "Yes please."), ("assistant", "Here's a starting point:\n\n```python\nimport csv\nfrom collections import defaultdict\n\ntransactions = []\n\ndef add(date, amount, category):\n    transactions.append({'date': date, 'amount': amount, 'category': category})\n\ndef totals():\n    result = defaultdict(float)\n    for t in transactions:\n        result[t['category']] += t['amount']\n    return dict(result)\n```\n\nNext steps: loading and saving CSV with the `csv` module, and a small menu loop.")],
 [("user", "Do you know about Mars?"), ("assistant", "Yes, some of the basics. Mars is the fourth planet from the Sun, a cold desert world with a thin atmosphere, famous for its red colour from iron oxide. What would you like to know?"), ("user", "How long is a day there?"), ("assistant", "A Martian day, called a sol, is about 24 hours and 40 minutes, just a little longer than an Earth day."), ("user", "And a year?"), ("assistant", "A Martian year is about 687 Earth days, because Mars is farther from the Sun and takes longer to complete its orbit."), ("user", "Could we live there?"), ("assistant", "Not without a lot of support. The air is thin and mostly carbon dioxide, it's very cold, and there's radiation exposure, so people would need sealed habitats and life support. It's an active area of research, but not something possible today.")],
 [("user", "Can you recommend a laptop?"), ("assistant", "Sure. What will you mostly use it for, and what's your budget?"), ("user", "Programming and some video calls, around 800 dollars."), ("assistant", "For that budget, look for 16 GB of RAM, a fast SSD (512 GB if possible), a recent mid-range CPU and a decent keyboard and screen. I can't check current models or prices, so compare recent reviews for laptops in that range."), ("user", "Is 8 GB enough?"), ("assistant", "For light programming it can work, but with a browser, an editor and video calls open at once you'll feel it. 16 GB is a safer choice, and RAM that can be upgraded later is a plus."), ("user", "Okay, and Mac or Windows?"), ("assistant", "Both are fine for programming. Macs are polished and common for web and mobile development, but cost more in your range; Windows (or Linux) laptops give more hardware for the money. Choose by the tools you need and what you're comfortable with.")],
 [("user", "My code says 'IndexError: list index out of range'."), ("assistant", "That error means you accessed a position that doesn't exist in the list, for example index 5 in a list with 3 items. Can you show the line and the list's contents?"), ("user", "for i in range(len(items) + 1): print(items[i])"), ("assistant", "There's the problem: `range(len(items) + 1)` goes one step too far, so the last value of `i` equals `len(items)`, which is past the final index. Use `range(len(items))`, or loop directly with `for item in items:`."), ("user", "Fixed. Thanks! Why does it start at zero?"), ("assistant", "Python (like many languages) uses zero-based indexing because an index is an offset from the start: the first item is 0 items away. It also makes slicing and arithmetic with offsets simpler.")],
 [("user", "I'm planning dinner for four. Two are vegetarian."), ("assistant", "Got it: four people, two vegetarian. Any allergies or cuisines you'd like to focus on?"), ("user", "One person is allergic to nuts."), ("assistant", "Noted: no nuts. A dish that works for everyone is a vegetable and chickpea curry with rice (check the spice mixes and any naan for nuts), served with a yoghurt side. The meat eaters could add grilled chicken separately."), ("user", "Can we make it a pasta night instead?"), ("assistant", "Sure. Pasta with a tomato and basil sauce plus roasted vegetables works for the vegetarians; for the others, add sausage or chicken on the side. Since one guest is allergic to nuts, skip pesto, which usually contains pine nuts or other nuts, and check any sauce labels."), ("user", "What was the allergy again?"), ("assistant", "One guest is allergic to nuts, so the meal should be nut-free, including sauces and toppings.")],
 [("user", "Let's say I earn 3000 a month."), ("assistant", "Okay, we'll work with 3,000 a month."), ("user", "I want to save 20 percent."), ("assistant", "20% of 3,000 is 600 a month."), ("user", "And rent is 900."), ("assistant", "After saving 600 and paying 900 in rent, you have 3,000 - 600 - 900 = 1,500 left for everything else."), ("user", "How much per week is that?"), ("assistant", "1,500 over about 4.3 weeks in a month is roughly 350 a week for food, transport, bills and spending.")],
 [("user", "I'm choosing between two phones. Phone A has a better camera, Phone B has better battery life."), ("assistant", "Good trade-off. Which do you use more: taking photos, or being away from a charger for long stretches?"), ("user", "I travel a lot but I also love photos."), ("assistant", "In that case consider how you'd handle charging: a power bank solves the battery issue for travelling cheaply, but nothing fixes a weaker camera. That would tilt me towards Phone A, unless battery differences are large."), ("user", "How large is large?"), ("assistant", "A day's difference matters; an hour or two of screen time doesn't. Check independent battery tests rather than the spec sheet, and compare both phones' sample photos in low light.")],
 [("user", "Write a haiku about rain."), ("assistant", "Soft rain on the roof\nThe street hums a quiet song\nPuddles hold the sky"), ("user", "Make it about snow instead."), ("assistant", "Soft snow on the roof\nThe street holds its breath, silent\nWhite hides every sound"), ("user", "Now one about the first one's puddles."), ("assistant", "Puddles in the lane\nA small sky trembles in each\nBoots break the clouds up")],
 [("user", "What's the capital of Canada?"), ("assistant", "Ottawa."), ("user", "And its biggest city?"), ("assistant", "Toronto is Canada's largest city by population."), ("user", "Which is further north?"), ("assistant", "Ottawa is slightly further north than Toronto; both are in the southern part of the country, but Ottawa is roughly 1.5 degrees of latitude farther north.")],
 [("user", "I want to learn guitar."), ("assistant", "Great. Do you have a guitar yet, and acoustic or electric?"), ("user", "Acoustic, and it's a hand-me-down."), ("assistant", "A hand-me-down is a fine start. Make sure the strings are fairly new and the action (string height) isn't too high, because that makes playing hard. A shop can set it up cheaply."), ("user", "How often should I practise?"), ("assistant", "Short and regular beats long and rare: 15 to 20 minutes most days is better than two hours on a weekend. Expect sore fingertips for the first couple of weeks."), ("user", "What should I learn first?"), ("assistant", "A few open chords (G, C, D, Em, Am), clean switching between them, and a simple strumming pattern. With those you can play a surprising number of songs.")],
]
for i, d in enumerate(DIA):
    for rep in range(2):
        sysm = pick(R, SYS_GENERAL) if rep == 0 else None
        add("reference_dialogue", d, f"m5_{i}", "medium", "authored", sysm)

# ---------------------------------------------------------------- M6: corrections / misunderstandings
CORR = [
 [("user", "Book me a table for six on Friday."), ("assistant", "I can't book tables, but I can help you plan it. For six on Friday, which restaurant or cuisine do you have in mind?"), ("user", "No wait, I meant for sixteen, not six."), ("assistant", "Thanks for the correction: sixteen people on Friday. A group that size usually needs a call-ahead or a private room, and a set menu. I can't make the booking, but I can draft a message to the restaurant: \"Hello, I'd like to reserve for 16 people on Friday at [time]. Do you offer a group menu or private space?\"")],
 [("user", "Give me a recipe for pancakes."), ("assistant", "Basic pancakes: mix 1 cup flour, 1 tablespoon sugar, 2 teaspoons baking powder and a pinch of salt; whisk in 1 cup milk, 1 egg and 2 tablespoons melted butter. Cook ladlefuls on a hot, lightly greased pan, flipping when bubbles form."), ("user", "Sorry, I'm vegan."), ("assistant", "No problem. Swap the milk for plant milk (oat or soy work well), the egg for 1 tablespoon ground flaxseed mixed with 3 tablespoons water (let it sit 5 minutes), and the butter for a neutral oil or vegan butter. The rest stays the same.")],
 [("user", "What's the weather in Paris?"), ("assistant", "I can't check live weather. Do you mean Paris, France? A weather app will give you the current conditions."), ("user", "I meant Paris, Texas."), ("assistant", "Thanks for clarifying. I still can't see live weather for Paris, Texas, so a weather site will help. In general, northeast Texas summers are hot and humid and winters are mild with occasional cold snaps.")],
 [("user", "Summarise the article in three bullets."), ("assistant", "I don't see an article in our conversation yet. Could you paste the text?"), ("user", "Oh, I forgot. Here: 'The city plans to plant 10,000 trees by 2027 to cut summer heat. Volunteers will plant half. The programme costs 2 million dollars.'"), ("assistant", "- The city plans to plant 10,000 trees by 2027 to reduce summer heat.\n- Volunteers will plant half of them.\n- The programme costs 2 million dollars.")],
 [("user", "Write a function that adds two numbers."), ("assistant", "```python\ndef add(a, b):\n    return a + b\n```"), ("user", "I wanted it in JavaScript."), ("assistant", "```javascript\nfunction add(a, b) {\n  return a + b;\n}\n```\n\nSame idea in JavaScript. Note that `+` also concatenates strings, so `add('1', 2)` gives `'12'`.")],
 [("user", "Is 91 a prime number?"), ("assistant", "Yes, 91 is prime."), ("user", "Are you sure? I think 7 times 13."), ("assistant", "You're right, and I was wrong. 7 × 13 = 91, so 91 is not prime. Thank you for checking; 91 is a well-known number that looks prime but isn't.")],
 [("user", "How many days are in February?"), ("assistant", "February has 28 days."), ("user", "What about leap years?"), ("assistant", "In a leap year, February has 29 days. To be precise about my first answer: it's 28 days in a common year and 29 in a leap year.")],
 [("user", "Remind me to call mum at 5."), ("assistant", "I can't set reminders because I'm not connected to your device, but note: call mum at 5. Do you want me to suggest a way to set the alarm?"), ("user", "Fine, how do I set a phone alarm?"), ("assistant", "On most phones: open the Clock app, tap Alarm, add a new one for 5:00 (check AM/PM), give it a label such as 'Call mum', and save. A voice assistant can also do it if you say 'set an alarm for 5 o'clock called call mum'.")],
]
for i, d in enumerate(CORR):
    for rep in range(2):
        add("corrections", d, f"m6_{i}", "medium", "authored", pick(R, SYS_GENERAL) if rep == 0 else None)

# ---------------------------------------------------------------- M7: remembering earlier info (names, constraints)
PEOPLE = NAMES
for n in range(60):
    nm = R.choice(PEOPLE)
    fact = R.choice([("a vegetarian", "dinner ideas", "vegetarian"), ("allergic to peanuts", "snack ideas", "peanut-free"), ("on a tight budget", "a gift idea", "budget-friendly"), ("short on time", "a lunch idea", "quick")])
    base = {"vegetarian": "a chickpea and spinach curry with rice", "peanut-free": "sliced apple with cheese and crackers", "budget-friendly": "a handwritten card with a home-baked treat", "quick": "a wrap with hummus, salad and leftover chicken or beans"}[fact[2]]
    turns = [("user", f"Hi, I'm {nm}. I'm {fact[0]}."), ("assistant", f"Nice to meet you, {nm}. I'll keep in mind that you're {fact[0]}. What can I help with?"),
             ("user", f"Can you suggest {fact[1]}?"), ("assistant", f"Since you're {fact[0]}, how about {base}? Want a couple more options?"),
             ("user", "What's my name again?"), ("assistant", f"You're {nm}.")]
    add("remember_in_conversation", turns, f"m7_{n}", "easy", "composed")

save("context", out)
