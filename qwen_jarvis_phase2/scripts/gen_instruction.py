"""Category 6: instruction following (target ~750). Transformations are computed; constraints are checked programmatically."""
import csv
import io
import json
import re
from lib import *

CAT = "instruction_following"
R = seeded("instruction")
out = []
NAMES = ["Maya", "Arjun", "Lena", "Omar", "Sofia", "Ken", "Priya", "Tom", "Aisha", "Diego", "Nora", "Ravi", "Chloe", "Ivan", "Zara", "Ben", "Hana", "Luis", "Mira", "Sam"]
CITIES = ["Pune", "Delhi", "Mumbai", "Lagos", "Oslo", "Lima", "Cairo", "Osaka", "Perth", "Quito", "Dublin", "Hanoi"]
ITEMS = ["pencil", "notebook", "stapler", "lamp", "backpack", "mug", "keyboard", "folder", "scissors", "calculator"]


def add(sub, q, a, diff="medium", g=None, src="computed"):
    out.append(mk(CAT, sub, [("user", q), ("assistant", a)], g or sub, diff, src, pick(R, SYS_GENERAL)))


# ---------------- IF1: format conversion (CSV <-> JSON <-> markdown table)
for n in range(70*2):
    k = R.choice([2, 3, 4])
    rows = [(R.choice(NAMES), R.randint(18, 60), R.choice(CITIES)) for _ in range(k)]
    names = [r[0] for r in rows]
    if len(set(names)) < len(names):
        continue
    csv_txt = "name,age,city\n" + "\n".join(f"{a},{b},{c}" for a, b, c in rows)
    js = json.dumps([{"name": a, "age": b, "city": c} for a, b, c in rows], indent=2)
    md = "| name | age | city |\n|---|---|---|\n" + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    typ = R.randrange(4)
    if typ == 0:
        add("convert_csv_json", f"Convert this CSV to JSON. Return only the JSON.\n\n{csv_txt}", f"```json\n{js}\n```")
    elif typ == 1:
        add("convert_csv_md", f"Turn this CSV into a Markdown table.\n\n{csv_txt}", md)
    elif typ == 2:
        add("convert_json_csv", f"Convert this JSON to CSV with a header row.\n\n{js}", f"```csv\n{csv_txt}\n```")
    else:
        oldest = max(rows, key=lambda r: r[1])
        add("convert_extract", f"From this data, give me a JSON object with the keys `oldest` (name) and `count`.\n\n{csv_txt}", f"```json\n{json.dumps({'oldest': oldest[0], 'count': len(rows)})}\n```")

# ---------------- IF2: extraction with regex (computed)
for n in range(80*2):
    typ = R.randrange(5)
    nm1, nm2 = R.sample(NAMES, 2)
    if typ == 0:
        e1 = f"{nm1.lower()}@{R.choice(['example.com', 'mail.org', 'corp.io'])}"
        e2 = f"{nm2.lower()}{R.randint(1, 99)}@{R.choice(['example.com', 'mail.org', 'corp.io'])}"
        text = f"Please contact {nm1} at {e1} or {nm2} ({e2}) before Friday."
        found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        add("extract_emails", f"Extract all email addresses from this text, one per line, nothing else:\n\n{text}", "\n".join(found))
    elif typ == 1:
        d = [f"2024-{R.randint(1,12):02d}-{R.randint(1,28):02d}" for _ in range(R.choice([2, 3]))]
        text = "Key dates: " + ", then ".join(d) + f". Ask {nm1} if they change."
        found = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        add("extract_dates", f"List the dates in this text as a JSON array of strings.\n\n{text}", f"```json\n{json.dumps(found)}\n```")
    elif typ == 2:
        nums = [R.randint(3, 900) for _ in range(R.choice([3, 4, 5]))]
        text = "Sales this week were " + ", ".join(f"{R.choice(CITIES)}: {x}" for x in nums) + "."
        got = [int(x) for x in re.findall(r"\d+", text)]
        add("extract_numbers", f"Add up all the numbers in this sentence and give only the total.\n\n{text}", str(sum(got)))
    elif typ == 3:
        a, b = R.sample(CITIES, 2)
        text = f"{nm1} flew from {a} to {b} and met {nm2} at the airport on 2024-0{R.randint(1,9)}-1{R.randint(0,9)}."
        add("extract_entities", f"Extract the people and places from this sentence as JSON with keys `people` and `places`.\n\n{text}", f"```json\n{json.dumps({'people': [nm1, nm2], 'places': [a, b]})}\n```")
    else:
        prices = [round(R.uniform(2, 90), 2) for _ in range(R.choice([2, 3]))]
        its = R.sample(ITEMS, len(prices))
        text = ", ".join(f"{i} ${p:.2f}" for i, p in zip(its, prices))
        mx = its[prices.index(max(prices))]
        add("extract_compare", f"From this list, which item is the most expensive? Answer with just the item name.\n\n{text}", mx)

# ---------------- IF3: list operations (sort/filter/count/dedupe/group)
for n in range(100*2):
    k = R.choice([5, 6, 7, 8])
    xs = [R.randint(1, 50) for _ in range(k)]
    typ = R.randrange(8)
    L = ", ".join(map(str, xs))
    if typ == 0:
        add("list_sort", f"Sort these numbers in ascending order and return them comma-separated: {L}", ", ".join(map(str, sorted(xs))))
    elif typ == 1:
        add("list_sort_desc", f"Sort in descending order: {L}", ", ".join(map(str, sorted(xs, reverse=True))))
    elif typ == 2:
        ev = [x for x in xs if x % 2 == 0]
        add("list_filter", f"Keep only the even numbers from this list: {L}. If there are none, answer 'none'.", ", ".join(map(str, ev)) if ev else "none")
    elif typ == 3:
        add("list_count", f"How many of these numbers are greater than 25? {L}. Answer with a single number.", str(sum(x > 25 for x in xs)))
    elif typ == 4:
        seen, u = set(), []
        for x in xs:
            if x not in seen:
                seen.add(x)
                u.append(x)
        add("list_dedupe", f"Remove duplicates from this list while keeping the first occurrence order: {L}", ", ".join(map(str, u)))
    elif typ == 5:
        words = R.sample(ITEMS, R.choice([4, 5, 6]))
        add("list_alpha", f"Alphabetise this list and number the items: {', '.join(words)}", "\n".join(f"{i+1}. {w}" for i, w in enumerate(sorted(words))))
    elif typ == 6:
        small = [x for x in xs if x <= 25]
        big = [x for x in xs if x > 25]
        add("list_group", f"Split this list into two groups, 25 or below and above 25, and return JSON with keys `low` and `high`: {L}", f"```json\n{json.dumps({'low': small, 'high': big})}\n```")
    else:
        add("list_stats", f"Give me the minimum, maximum and sum of: {L}. Use the format `min=..., max=..., sum=...`.", f"min={min(xs)}, max={max(xs)}, sum={sum(xs)}")

# ---------------- IF4: passage-based tasks with format constraints
P = [
 ("Honeybees communicate the location of food by dancing. In the waggle dance, the angle of the dance relative to vertical shows the direction relative to the sun, and the length of the waggle shows the distance.", "Honeybees tell nestmates where food is by dancing, with the angle showing direction and the waggle length showing distance.", ["Bees communicate food locations by dancing.", "The dance angle shows direction relative to the sun.", "The length of the waggle indicates distance."], "How Honeybees Dance to Share Directions", ["honeybees", "waggle dance", "navigation"]),
 ("The city council approved a plan to add 40 kilometres of protected bike lanes over three years. Supporters say it will reduce traffic injuries, while some shop owners worry about losing parking spaces.", "A city council approved 40 km of protected bike lanes over three years, praised for safety but questioned by shop owners over parking.", ["The council approved 40 km of protected bike lanes.", "The rollout is planned over three years.", "Shop owners worry about losing parking spaces."], "City Approves 40 km of Protected Bike Lanes", ["bike lanes", "city council", "parking"]),
 ("Sourdough bread rises using a starter, a mix of flour and water that hosts wild yeast and bacteria. The long fermentation develops a tangy flavour and can make the bread easier to digest for some people.", "Sourdough rises with a wild-yeast starter, and its long fermentation gives a tangy flavour.", ["Sourdough uses a starter of flour and water.", "The starter contains wild yeast and bacteria.", "Long fermentation creates the tangy taste."], "What Makes Sourdough Different", ["sourdough", "starter", "fermentation"]),
 ("The company will move to a four-day work week starting in January. Employees will work 32 hours at the same pay, and managers will review productivity after six months.", "The company is moving to a four-day, 32-hour week at the same pay from January, with a productivity review after six months.", ["A four-day work week begins in January.", "Employees work 32 hours with no pay cut.", "Productivity will be reviewed after six months."], "Company Adopts Four-Day Work Week", ["four-day week", "productivity", "work hours"]),
 ("Coral reefs cover less than one percent of the ocean floor but support about a quarter of marine species. Rising ocean temperatures cause bleaching, when corals expel the algae that give them colour and food.", "Coral reefs cover under 1% of the seabed yet support about a quarter of marine species, and warming oceans cause damaging bleaching.", ["Reefs cover less than 1% of the ocean floor.", "They support roughly 25% of marine species.", "Heat causes corals to bleach by expelling algae."], "Why Coral Reefs Are at Risk", ["coral reefs", "bleaching", "ocean temperature"]),
 ("A new library branch opens on Monday with a children's reading room, free Wi-Fi and a space for community events. It will be open from 9 a.m. to 8 p.m. on weekdays.", "A new library branch opens Monday with a children's room, free Wi-Fi and event space, open 9 to 8 on weekdays.", ["A new library branch opens on Monday.", "It has a children's reading room and free Wi-Fi.", "Weekday hours are 9 a.m. to 8 p.m."], "New Library Branch Opens Monday", ["library", "community", "children"]),
 ("The software update fixes a security flaw that could let attackers read saved passwords. Users should install version 4.2.1 as soon as possible and restart the application.", "The 4.2.1 update fixes a flaw that could expose saved passwords, so users should install it and restart.", ["Version 4.2.1 fixes a security flaw.", "The flaw could expose saved passwords.", "Users should update and restart the app."], "Urgent Security Update Released", ["security", "update", "passwords"]),
 ("Volcanoes form where molten rock rises through cracks in Earth's crust. Some erupt violently with ash and gas, while others release slow-moving lava, depending on how thick and gas-rich the magma is.", "Volcanoes form where magma rises through the crust, and eruption style depends on the magma's thickness and gas content.", ["Volcanoes form where magma rises through cracks.", "Some erupt violently, others flow gently.", "Thick, gas-rich magma tends to erupt explosively."], "How Volcanoes Erupt", ["volcanoes", "magma", "eruption"]),
 ("Regular exercise improves mood by increasing the release of endorphins and reducing stress hormones. Even a 20-minute walk can make people feel more energetic.", "Regular exercise lifts mood by raising endorphins and lowering stress hormones, and even a 20-minute walk helps.", ["Exercise increases endorphins.", "It lowers stress hormones.", "A 20-minute walk can boost energy."], "How Exercise Improves Mood", ["exercise", "endorphins", "stress"]),
 ("The museum is offering free admission on the first Sunday of every month. Visitors can join guided tours at 11 a.m. and 2 p.m., which last about 45 minutes.", "The museum is free on the first Sunday of each month, with 45-minute guided tours at 11 a.m. and 2 p.m.", ["Admission is free on the first Sunday monthly.", "Tours run at 11 a.m. and 2 p.m.", "Each tour lasts about 45 minutes."], "Free Museum Sundays", ["museum", "free admission", "tours"]),
 ("Electric buses reduce tailpipe emissions and noise, but they cost more to buy and need charging infrastructure. Over their lifetime, lower fuel and maintenance costs can offset the higher price.", "Electric buses cut emissions and noise but cost more upfront and need chargers, though lifetime running costs can make up the difference.", ["Electric buses cut emissions and noise.", "They cost more and need charging infrastructure.", "Lower running costs can offset the price."], "Electric Buses: Costs and Benefits", ["electric buses", "emissions", "charging"]),
 ("The recipe calls for two cups of flour, one teaspoon of baking powder and a pinch of salt. Mix the dry ingredients first, then fold in milk and melted butter until just combined.", "The recipe combines dry ingredients first, then folds in milk and melted butter until just mixed.", ["Mix flour, baking powder and salt first.", "Fold in milk and melted butter.", "Stop as soon as it's just combined."], "A Simple Batter Recipe", ["batter", "flour", "mixing"]),
 ("Remote teams often rely on written communication, so clear documentation matters. Teams that record decisions and share meeting notes avoid repeated discussions and onboard new people faster.", "Remote teams depend on written communication, and documenting decisions and meetings reduces repeat discussions and speeds onboarding.", ["Remote teams depend on writing.", "Recording decisions avoids repeated debates.", "Good notes speed up onboarding."], "Why Remote Teams Need Documentation", ["remote work", "documentation", "onboarding"]),
 ("Many languages borrow words from each other. English, for example, took 'pyjamas' from Hindi, 'algebra' from Arabic and 'kindergarten' from German.", "Languages borrow words from one another, as English did with 'pyjamas', 'algebra' and 'kindergarten'.", ["Languages often borrow vocabulary.", "English took 'pyjamas' from Hindi.", "'Algebra' came from Arabic and 'kindergarten' from German."], "Borrowed Words in English", ["loanwords", "English", "etymology"]),
 ("The train service will be suspended between the two main stations this weekend for track repairs. Replacement buses will run every 15 minutes, adding about 30 minutes to the journey.", "This weekend trains between the main stations are replaced by buses every 15 minutes, adding about 30 minutes to journeys.", ["Trains are suspended this weekend for repairs.", "Replacement buses run every 15 minutes.", "Journeys take about 30 minutes longer."], "Weekend Train Disruption", ["train", "repairs", "replacement buses"]),
 ("Photosynthesis lets plants turn sunlight, water and carbon dioxide into sugar and oxygen. This process is the basis of most food chains on Earth.", "Plants use photosynthesis to turn sunlight, water and carbon dioxide into sugar and oxygen, which underpins most food chains.", ["Plants convert light, water and CO2 into sugar.", "The process releases oxygen.", "Most food chains depend on it."], "Photosynthesis in Brief", ["photosynthesis", "plants", "oxygen"]),
 ("A good password is long and unique. Using a password manager helps because you only need to remember one strong passphrase, and the manager creates a different random password for every site.", "A good password is long and unique, and a password manager generates and stores a different one for every site.", ["Strong passwords are long and unique.", "A password manager creates random passwords per site.", "You remember only one strong passphrase."], "Why Use a Password Manager", ["passwords", "security", "password manager"]),
 ("The town's annual lantern festival draws thousands of visitors. Lanterns are launched over the river at sunset, and local food stalls line the main street until midnight.", "The town's lantern festival draws thousands, with lanterns launched at sunset and food stalls open until midnight.", ["Lanterns are launched over the river at sunset.", "Thousands of visitors attend.", "Food stalls stay open until midnight."], "Lantern Festival Lights Up the River", ["festival", "lanterns", "food stalls"]),
 ("Sleep researchers recommend a consistent bedtime, a cool dark room and limiting caffeine after lunch. They add that irregular sleep schedules can affect mood and concentration.", "Sleep experts advise a steady bedtime, a cool dark room and less caffeine after lunch, since irregular schedules hurt mood and focus.", ["Keep a consistent bedtime.", "Use a cool, dark bedroom.", "Avoid caffeine after lunch."], "Simple Habits for Better Sleep", ["sleep", "caffeine", "routine"]),
]
CONS = [
 ("Summarise this in one sentence:\n\n{p}", "one_sentence", lambda e: e[1]),
 ("Give exactly three bullet points summarising the following text.\n\n{p}", "three_bullets", lambda e: "\n".join(f"- {b}" for b in e[2])),
 ("Write a short headline for this text. Return only the headline.\n\n{p}", "headline", lambda e: e[3]),
 ("List three keywords for this text, comma-separated, lowercase, nothing else.\n\n{p}", "keywords", lambda e: ", ".join(e[4])),
 ("Return JSON with the keys `title` and `keywords` (an array of three strings) for this text.\n\n{p}", "json_fields", lambda e: "```json\n" + json.dumps({"title": e[3], "keywords": e[4]}, indent=2) + "\n```"),
 ("Number the key points of this text (three of them).\n\n{p}", "numbered", lambda e: "\n".join(f"{i+1}. {b}" for i, b in enumerate(e[2]))),
 ("Summarise in under 25 words:\n\n{p}", "under_25_words", lambda e: e[1]),
 ("Turn this into a Markdown heading plus a one-line description.\n\n{p}", "heading_desc", lambda e: f"## {e[3]}\n\n{e[1]}"),
]
for pi, e in enumerate(P):
    kinds = R.sample(CONS, 5)
    for tmpl, kind, fn in kinds:
        ans = fn(e)
        if kind == "under_25_words" and len(ans.split()) >= 25:
            continue
        if kind == "three_bullets":
            assert ans.count("\n- ") == 2
        add("passage_" + kind, tmpl.format(p=e[0]), ans, "medium", "pass_" + str(pi), "authored")

# ---------------- IF5: text transformations (computed)
SENT = ["the quick brown fox jumps over the lazy dog", "learning to program takes patience and practice", "please send the invoice by friday afternoon", "good code is easy to read and easy to change",
        "we will meet at the cafe near the old library", "tomorrow's forecast shows rain in the morning", "keep your commitments small and finish them", "data beats opinions when you can measure it"]
for n in range(80*2):
    s = R.choice(SENT)
    typ = R.randrange(8)
    if typ == 0:
        add("text_upper", f"Convert to uppercase: {s}", s.upper(), "easy")
    elif typ == 1:
        add("text_title", f"Rewrite in Title Case: {s}", s.title(), "easy")
    elif typ == 2:
        add("text_reverse_words", f"Reverse the order of the words in this sentence: \"{s}\"", " ".join(reversed(s.split())), "easy")
    elif typ == 3:
        add("text_count", f"How many words and how many characters (including spaces) are in: \"{s}\"? Reply as `words=..., characters=...`.", f"words={len(s.split())}, characters={len(s)}", "easy")
    elif typ == 4:
        w = R.choice(s.split())
        add("text_replace", f"Replace every occurrence of \"{w}\" with \"[X]\" in: {s}", s.replace(w, "[X]"), "easy")
    elif typ == 5:
        add("text_capitalise_first", f"Capitalise the first letter of the sentence and end it with a full stop: {s}", s[0].upper() + s[1:] + ".", "easy")
    elif typ == 6:
        longest = max(s.split(), key=len)
        add("text_longest", f"Which word is the longest in \"{s}\"? If tied, give the first. Reply with the word only.", longest, "easy")
    else:
        add("text_acronym", f"Make an acronym from the first letters of each word in: \"{s}\". Reply in uppercase.", "".join(w[0] for w in s.split()).upper(), "easy")

# ---------------- IF6: classification with output constraints (authored labels)
SENTI = [
 ("The delivery was late and the box was damaged.", "negative"), ("Absolutely loved the new update, everything feels faster!", "positive"), ("The meeting is at 3 pm in room 204.", "neutral"),
 ("I waited an hour and nobody helped me.", "negative"), ("Great service and friendly staff.", "positive"), ("The package arrived on Tuesday.", "neutral"),
 ("This is the worst app I've ever used.", "negative"), ("Such a lovely surprise, thank you!", "positive"), ("The store opens at nine.", "neutral"),
 ("The food was cold and tasteless.", "negative"), ("I'm thrilled with the results.", "positive"), ("The report has twelve pages.", "neutral"),
 ("It keeps crashing every time I open it.", "negative"), ("The new design looks fantastic.", "positive"), ("Class begins next Monday.", "neutral"),
 ("I regret buying this.", "negative"), ("Best purchase I've made all year.", "positive"), ("The file is saved in the shared folder.", "neutral"),
 ("Customer support was rude and unhelpful.", "negative"), ("What a wonderful day for a picnic!", "positive"), ("The bus runs every ten minutes.", "neutral"),
 ("I'm so disappointed with the quality.", "negative"), ("Everything worked perfectly on the first try.", "positive"), ("The password must be at least eight characters.", "neutral"),
 ("The room was dirty and noisy all night.", "negative"), ("Our team won the final, I can't stop smiling!", "positive"), ("He lives on the second floor.", "neutral"),
]
TOPIC = [
 ("The central bank raised interest rates again.", "economy"), ("The striker scored twice in the second half.", "sports"), ("The new phone has a faster chip and a better camera.", "technology"),
 ("Heavy rain is expected across the region tonight.", "weather"), ("The film won three awards at the festival.", "entertainment"), ("Doctors recommend more fibre in your diet.", "health"),
 ("The parliament passed the new education bill.", "politics"), ("Researchers discovered a new species of frog.", "science"), ("Stocks fell sharply after the earnings report.", "economy"),
 ("The goalkeeper saved a penalty in the final minute.", "sports"), ("The app now supports dark mode and offline sync.", "technology"), ("A cold front will bring snow to the mountains.", "weather"),
 ("The orchestra will premiere a new symphony on Friday.", "entertainment"), ("A daily walk can lower blood pressure.", "health"), ("The senator announced a run for president.", "politics"),
 ("Astronomers observed a distant exploding star.", "science"),
]
PERSON = ["Reply with only the label.", "Answer with one word.", "Output just the label in lowercase.", "Respond with the label and nothing else."]
for i, (s, lab) in enumerate(SENTI):
    for rep in range(2):
        q = R.choice([f"Classify the sentiment as positive, negative or neutral. {R.choice(PERSON)}\n\n\"{s}\"", f"Is this review positive, negative or neutral? {R.choice(PERSON)}\n\n{s}", f"Sentiment (positive/negative/neutral): \"{s}\" {R.choice(PERSON)}"])
        add("classify_sentiment", q, lab, "easy", "sent_" + str(i), "authored")
    add("classify_sentiment_json", f"Return JSON with `sentiment` (positive, negative or neutral) and `confidence` (high, medium or low) for: \"{s}\"", "```json\n" + json.dumps({"sentiment": lab, "confidence": "high" if lab != "neutral" else "medium"}) + "\n```", "medium", "sent_" + str(i), "authored")
for i, (s, lab) in enumerate(TOPIC):
    for rep in range(2):
        q = R.choice([f"Assign one topic from: economy, sports, technology, weather, entertainment, health, politics, science. {R.choice(PERSON)}\n\n\"{s}\"", f"What topic is this headline about (economy, sports, technology, weather, entertainment, health, politics, science)? {R.choice(PERSON)}\n\n{s}"])
        add("classify_topic", q, lab, "easy", "topic_" + str(i), "authored")

# ---------------- IF7: multi-constraint writing (constraints verified in code)
SUBJ = [("the ocean", ["The ocean covers most of our planet.", "The tides follow the moon's pull.", "The deepest trenches stay dark and cold.", "The waves keep shaping every shoreline."]),
        ("a library", ["The library is quiet and warm.", "The shelves hold thousands of stories.", "The readers pass slowly between the rows.", "The windows let in soft afternoon light."]),
        ("a city at night", ["The city glows long after sunset.", "The streets hum with late buses.", "The cafes stay open for the sleepless.", "The skyline blinks in red and white."]),
        ("a mountain", ["The mountain rises above the clouds.", "The paths wind through pine and stone.", "The air grows thin near the top.", "The snow clings to the highest ridge."]),
        ("a kitchen", ["The kitchen smells of fresh bread.", "The kettle sings on the stove.", "The knives wait in a neat row.", "The window fogs with warm steam."]),
        ("a train journey", ["The train leaves just after dawn.", "The fields slide past the window.", "The passengers doze or watch the hills.", "The station lights appear at last."])]
for i, (topic, sents) in enumerate(SUBJ):
    for k in (2, 3, 4):
        chosen = sents[:k]
        text = " ".join(chosen)
        assert all(s.startswith("The ") for s in chosen) and "," not in text
        add("multi_constraint", f"Write exactly {k} sentences about {topic}. Each sentence must start with \"The\" and you may not use any commas.", text, "hard", f"mc_{i}", "authored")
    # word-limit constraint
    one = sents[0]
    add("multi_constraint", f"Write one sentence about {topic} with fewer than 10 words.", one if len(one.split()) < 10 else sents[1], "medium", f"mc_{i}", "authored")
    # format constraints
    add("multi_constraint", f"Describe {topic} in three bullet points. Each bullet must be under 8 words.", "\n".join(f"- {s.rstrip('.')}" for s in sents[:3] if len(s.rstrip('.').split()) < 8) or "- " + sents[0].rstrip("."), "hard", f"mc_{i}", "authored")

# ---------------- IF8: follow an explicit persona/format instruction (authored)
FMT = [
 ("Answer in exactly one word: is the sun a star?", "Yes."),
 ("Respond only in JSON with keys `answer` and `reason`. Question: Is 17 a prime number?", "```json\n{\"answer\": \"yes\", \"reason\": \"17 has no divisors other than 1 and 17\"}\n```"),
 ("Answer in the form 'Yes/No - one short reason'. Can penguins fly?", "No - penguins' wings evolved into flippers for swimming."),
 ("Respond with a numbered list of exactly three items: ways to save water at home.", "1. Fix leaking taps and toilets.\n2. Take shorter showers.\n3. Run dishwashers and washing machines only with full loads."),
 ("Write the answer in all caps and no more than five words: what's the capital of France?", "THE CAPITAL IS PARIS."),
 ("Explain what HTTP is in two sentences, no more.", "HTTP is the protocol browsers and servers use to exchange web pages and data, built around requests and responses. Each request names a method such as GET or POST and a URL, and the response carries a status code and content."),
 ("Give me a three-word slogan for a coffee shop.", "Brewed for you."),
 ("Respond only with 'true' or 'false': water boils at 100 degrees Celsius at sea level.", "true"),
 ("Reply in French only: what is 2 plus 2?", "Deux plus deux font quatre."),
 ("Use a table with two columns, Pros and Cons, for working from home.", "| Pros | Cons |\n|---|---|\n| No commute | Harder to separate work and home |\n| Flexible schedule | Can feel isolating |\n| Fewer interruptions | Less spontaneous collaboration |"),
 ("Write a haiku about autumn. Three lines only.", "Crisp leaves drift and fall\nThe river carries their gold\nQuiet air at dusk"),
 ("Start your answer with the word 'Actually' and explain why bats aren't blind.", "Actually, bats can see: most species have functional eyes, and many see reasonably well in dim light. Some also use echolocation to navigate in the dark, which is probably why people assume they are blind."),
 ("Don't use the letter 'e' in your answer, just say hello in some way.", "Hi, fam! Good day to you."),
 ("List four prime numbers greater than 20, separated by semicolons, and nothing else.", "23; 29; 31; 37"),
 ("Answer in the style of a weather report: it's a good day for coding.", "Today's forecast: clear skies and steady focus across the editor, with light interruptions in the afternoon. Expect good conditions for deep work, with a high chance of finished pull requests by evening."),
 ("Describe a cat in exactly 12 words.", "A small, soft, curious hunter who naps often and always ignores commands."),
]
for i, (q, a) in enumerate(FMT):
    if q.startswith("Describe a cat"):
        assert len(a.split()) == 12, len(a.split())
    if q.startswith("Give me a three-word"):
        assert len(a.rstrip(".").split()) == 3
    add("format_constraint", q, a, "medium", f"fmt_{i}", "authored")
    add("format_constraint", "Follow the format exactly. " + q, a, "medium", f"fmt_{i}", "composed")

save("instruction", out)
