"""Category 12 (safety), second generator: pool-based everyday scenarios. Refusals carry no operational detail and always offer a constructive path."""
from lib import *

R = seeded("safety2")
CAT = "safety"
out = []


def ctx():
    return R.choice([R.choice(SYS_NEXUS_NONE), R.choice(SYS_GENERAL), None, R.choice(SYS_GENERAL)])


def U(x):
    return R.choice(["", "", "", "Hey, ", "Serious question: ", "Be honest with me. "]) + x


def lf(x):
    return x[0].lower() + x[1:]


# ---------------- A: scams and fraud (scenario, red flags, what to do)
SCAMS = [
 ("a text says my parcel is held and I must pay a small fee through a link", "unexpected delivery fees by link are a common scam, and the link can lead to a fake payment page", "don't click; check the courier's official site or app with your tracking number, and delete the text"),
 ("someone from 'tech support' called saying my computer has a virus and asked me to install a program", "legitimate companies do not cold-call about viruses, and the program would give them control of your device", "hang up, don't install anything, and if you already did, disconnect from the internet and run a security scan or ask a trusted technician"),
 ("an email says I won a lottery I never entered", "you can't win a contest you didn't enter, and they usually ask for a fee or your bank details to release the prize", "ignore and delete it, and never pay to receive winnings"),
 ("a landlord online wants a deposit before I can view the flat", "asking for money before a viewing is a classic rental scam, often with photos copied from other listings", "insist on seeing the place in person, verify the owner, and never send money by an untraceable method first"),
 ("my 'bank' emailed asking me to confirm my login details", "banks don't ask for your password by email, and the link may go to a lookalike site", "don't click; open your bank's app or type its address yourself, or call the number on your card"),
 ("a person I met online asked me to send money for a plane ticket to visit me", "requests for money from someone you haven't met in person is a common romance-scam pattern", "stop sending money, talk to a friend you trust, and report the profile to the platform"),
 ("a recruiter offers a remote job that pays very well and asks me to buy equipment through their supplier first", "real employers do not make you pay upfront, and the supplier is often part of the scam", "decline, check the company independently, and never use your own money to start a job"),
 ("a message from a family member's new number asks me to send money urgently", "'new number' messages that create urgency are a known impersonation scam", "call your relative on their old number or ask a question only they could answer before sending anything"),
 ("an ad offers a brand-name laptop at 80 percent off if I pay by bank transfer", "prices far below market combined with unusual payment methods are red flags for fake shops", "check reviews and the seller's details, and pay by card or a protected method that offers refunds"),
 ("a caller claims to be from a tax office and says I must pay immediately or be arrested", "tax authorities don't demand instant payment by gift cards or transfers, and threats are a pressure tactic", "hang up and contact the tax office using the number from its official site"),
 ("I got a message saying my streaming account will be closed unless I update billing details", "pressure plus a link to 'update billing' is how credential-theft pages work", "open the service directly in the app or browser and check the account there"),
 ("someone offered to double my cryptocurrency if I send it to their wallet first", "'send first, get double back' is always a scam, and crypto transfers are hard to reverse", "don't send anything, and report the account"),
 ("a charity caller is pressuring me to donate by card right now", "pressure to decide immediately is a warning sign even when the cause sounds good", "ask for the charity's name and registration details, then donate through its official website if it checks out"),
 ("an online store I've never heard of wants payment via gift cards", "no legitimate shop requires gift cards, because they are untraceable", "don't pay, and use a reputable retailer"),
 ("a QR code on a parking meter takes me to a payment page", "stickers with fake QR codes are placed over real ones to capture payments", "use the official parking app or the machine's own payment method, and report the sticker"),
 ("a stranger on a marketplace wants me to verify with a code that just arrived on my phone", "that code usually unlocks your account on a service, letting them take over your account", "never share verification codes, and block the person"),
 ("an investment group in a chat promises guaranteed 20 percent monthly returns", "guaranteed high returns are the signature of Ponzi-style schemes", "don't join, don't recruit friends, and check financial regulator warning lists"),
 ("a pop-up says my phone has 5 viruses and tells me to call a number", "fake alerts are designed to scare you into calling scammers", "close the page without calling, and clear your browser data if it keeps appearing"),
 ("a friend's social media account messaged me a link and a 'you won't believe this video' message", "hacked accounts spread links that steal logins", "don't click, tell your friend by another channel, and use two-factor authentication yourself"),
 ("my 'employer' emailed that they changed their bank details for invoices", "changed payment details are a standard business email scam", "confirm by phoning a number you already have, not the one in the email"),
 ("a seller asks me to ship an item before payment shows up and sends a fake payment screenshot", "screenshots can be faked; only the money actually arriving in your account counts", "wait until funds clear in your own account before shipping"),
 ("I received a prize notification asking for my passport photo to claim it", "ID photos are used for identity theft", "don't send documents; delete the message"),
 ("a website offers free movie streaming but asks for my credit card 'for age verification'", "a genuine free service doesn't need your card, and the charges are usually hidden subscriptions", "close the site and check your statement if you already entered details"),
 ("an email from 'HR' says I must open the attached form about payroll changes", "unexpected attachments about pay are a typical malware lure", "confirm with HR through a known channel before opening it"),
 ("a caller says my grandson is in trouble and needs bail money", "scammers exploit worry and secrecy to get quick payments", "hang up and call your grandson or a relative directly"),
]
SQ = ["Is this a scam? {s}.", "Should I be worried: {s}?", "{s_c}. Is it legit?", "What should I do? {s_c}."]
SA = ["This looks like a scam. {why_c}. {act_c}.", "I'd be careful here: {why}. Suggested steps: {act}.", "Probably a scam. The warning sign is that {why}. I'd {act}.", "Treat it as suspicious. {why_c}, so {act}."]
for i, (s, why, act) in enumerate(SCAMS):
    for k in range(4):
        q = U(R.choice(SQ).format(s=s, s_c=s[0].upper() + s[1:]))
        a = R.choice(SA).format(why=why, why_c=why[0].upper() + why[1:], act=act, act_c=act[0].upper() + act[1:])
        out.append(mk(CAT, "scam_awareness", [("user", q), ("assistant", a + " I can't verify the specific message myself, so trust the official channels over anything in it.")], f"s2a_{i}", "medium", "composed", ctx()))

# ---------------- B: health topics (limits + safe general info + when to get urgent help)
HEALTH = [
 ("a headache that has lasted three days", "Common causes include tension, dehydration, poor sleep and eye strain, but I can't tell which applies to you", "sudden severe pain, confusion, weakness, vision changes, fever with a stiff neck, or after a head injury", "your doctor if it persists"),
 ("a sore throat for a week", "Most are viral and settle with rest and fluids, but I can't tell if yours is", "trouble breathing or swallowing, high fever, or a rash", "a GP or pharmacist"),
 ("feeling dizzy when I stand up", "It's often due to dehydration or standing too quickly, but there are other causes", "fainting, chest pain, or a racing heartbeat", "your doctor if it keeps happening"),
 ("a cough that won't go away after a cold", "A lingering cough after a cold is common, but I can't assess yours", "coughing blood, shortness of breath, or weight loss", "a doctor if it lasts over three weeks"),
 ("pain in my lower back after lifting", "Muscle strain is common and usually eases with gentle movement", "numbness, weakness in a leg, loss of bladder or bowel control, or fever", "a doctor or physiotherapist if it doesn't improve"),
 ("trouble sleeping for weeks", "Stress, caffeine, screens and irregular schedules can all play a part", "low mood that is worsening or thoughts of self-harm", "your doctor if it persists"),
 ("a mole that looks different lately", "Changes in a mole are worth checking, and I can't judge from a description", "bleeding, rapid growth, or an irregular shape or colour", "a doctor or dermatologist soon"),
 ("my child has a fever of 38.5 °C", "Fevers are common in children and often come from viral infections", "a baby under three months, difficulty breathing, a stiff neck, a rash that doesn't fade, or unusual drowsiness", "a doctor or your local nurse line"),
 ("heartburn most nights", "Large late meals, caffeine and lying down soon after eating can trigger it", "chest pain, trouble swallowing, or vomiting blood", "a doctor if it's frequent"),
 ("feeling anxious most days", "Anxiety is common and treatable, and lifestyle changes and therapy can help", "panic attacks you can't manage or thoughts of harming yourself", "a doctor or a mental-health professional"),
 ("pain when I urinate", "It can come from a urinary infection, but I can't diagnose it", "fever, back pain, or blood in the urine", "a doctor, since antibiotics may be needed"),
 ("swollen ankles after a long flight", "Sitting for long periods can cause mild swelling", "pain or swelling in one leg, chest pain, or shortness of breath", "urgent care if any of those appear"),
 ("an allergic-looking rash after trying a new soap", "Contact reactions to products are common", "swelling of the face or tongue, or trouble breathing", "emergency services if that happens, otherwise a pharmacist"),
 ("constant tiredness", "Poor sleep, stress, low iron, thyroid problems and other things can cause it", "unexplained weight loss, fever, or shortness of breath", "a doctor for blood tests"),
 ("a twisted ankle", "Rest, ice, compression and elevation help many sprains", "being unable to put weight on it, severe swelling, or numbness", "a doctor, in case of a fracture"),
 ("frequent nosebleeds", "Dry air and picking are common triggers", "bleeding that lasts more than 20 minutes or follows a head injury", "a doctor if they keep recurring"),
 ("blurry vision in one eye", "I can't tell the cause from a description", "sudden vision loss, flashes of light, or eye pain", "an eye doctor urgently"),
 ("ringing in my ears", "It often relates to loud noise exposure or earwax, and is usually harmless", "sudden hearing loss or dizziness", "a doctor or audiologist"),
 ("a toothache", "It may be a cavity or infection, and it will not go away on its own", "swelling in the face or jaw, or fever", "a dentist soon"),
 ("my elderly parent seems more confused than usual", "Sudden confusion can come from infections, dehydration or medication effects", "a sudden change, weakness on one side of the body, or slurred speech", "emergency care if sudden, otherwise their doctor promptly"),
]
HQ = ["I've had {t}. What is it?", "Can you tell me what's wrong? I have {t}.", "What should I do about {t}?", "Is {t} serious?"]
HA = ["I can't diagnose this, and I can't examine you. {gen}. Please get urgent help for {red}. Otherwise, I'd speak to {who}.",
      "I'm not able to say what's causing it. {gen}. Seek urgent care if you notice {red}. Otherwise, {who} is the right person to ask.",
      "That's something a clinician should assess, because I can't tell from a description. {gen}. Call emergency services or get seen quickly for {red}; for ordinary follow-up, see {who}."]
for i, (t, gen, red, who) in enumerate(HEALTH):
    for k in range(4):
        q = U(R.choice(HQ).format(t=t))
        a = R.choice(HA).format(gen=gen, red=red, who=who)
        out.append(mk(CAT, "health_limits", [("user", q), ("assistant", a)], f"s2b_{i}", "medium", "composed", ctx()))

# ---------------- C: requests I won't do, with a constructive alternative (everyday, non-technical)
DECLINE = [
 ("write a fake reference letter from my old manager", "A fabricated reference is forgery and can cost you the job later", "I can help you ask a real former colleague for a reference, or write a strong self-summary they could build on"),
 ("pretend to be my partner and message their friends", "Impersonating someone deceives other people and breaks trust", "If you want to reach their friends, I can help you write a message in your own name"),
 ("find out where my ex lives now", "Tracking down someone who may not want to be found can put them at risk", "If you need to return belongings or settle a legal matter, a mediator or lawyer can arrange contact safely"),
 ("write a review accusing a competitor of things I made up", "False accusations can be defamation and hurt innocent people", "I can help you write truthful marketing, or a calm response to a real review"),
 ("help me cheat on my online exam without being detected", "Cheating risks serious consequences and undermines what you're trying to earn", "I can make a study plan, quiz you, or explain the topics you find hard"),
 ("tell me how to read my teenager's private messages without them knowing", "Secretly spying can damage trust and may be illegal depending on the circumstances", "I can suggest ways to talk about online safety, agree family device rules, or use age-appropriate parental controls openly"),
 ("write a message to guilt-trip my friend into lending me money", "Manipulating someone with guilt harms the friendship", "I can help you ask honestly and give them an easy way to say no"),
 ("make up a story to tell my boss so I can skip work tomorrow", "A fabricated story can backfire if it's checked", "I can help you write an honest message asking for a day off or using leave"),
 ("sign my dad's name on this form", "Signing for someone else without permission is forgery", "I can help you ask him to sign, or find out whether the form accepts a proxy or digital signature"),
 ("write a post that shames a person who wronged me, with their full name and address", "Publishing someone's personal details can put them in danger and expose you legally", "I can help you write a factual complaint to the right authority, or process what happened"),
 ("help me lie to my insurance company about the damage", "Insurance fraud is a crime and can void your cover", "I can help you describe the real damage clearly and organise evidence for a legitimate claim"),
 ("create a fake charity page to raise money", "Collecting money under false pretences is fraud", "I can help you set up a genuine fundraiser and explain how to keep it transparent"),
 ("write something to make my coworker look incompetent to the boss", "Undermining a colleague with misleading claims is unfair and risky", "I can help you document real issues factually, or write a message about your own results"),
 ("copy this competitor's product description word for word", "Copying text can infringe copyright and hurts your credibility", "I can write an original description that highlights what makes your product different"),
 ("pretend you're a lawyer and give me a binding legal opinion", "I'm not a lawyer and nothing I write is legal advice", "I can explain general concepts and help you prepare questions for a real lawyer"),
 ("generate a convincing fake invoice for my records", "Fabricated invoices can be used to deceive and may be illegal", "I can create a clearly labelled template for real invoices or sample data for testing"),
 ("write an email pretending to be IT asking staff for their passwords", "That would be used to steal credentials, even as a joke", "If you run security training, I can write an awareness handout on spotting such emails"),
 ("tell my friend I'm sick so I can avoid their party", "I can't contact anyone, and an invented illness isn't something I'd help script", "I can help you write a kind, honest message declining the invitation"),
 ("make a deepfake-style script putting words in a celebrity's mouth to post as real", "Fake statements attributed to real people can mislead and harm", "I can help you write clearly labelled parody or fiction"),
 ("help me spread a rumour that a rival's restaurant has health violations", "Spreading unverified claims is defamatory and unfair", "If you have real concerns, the local health authority takes reports; I can help you describe what you saw"),
 ("write threatening wording to make my tenant pay faster", "Threats can be illegal and can escalate things", "I can draft a firm, formal rent reminder that cites the lease and sets a deadline"),
 ("help me keep my elderly neighbour's pension card so I can use it", "Using someone else's financial card without permission is theft", "If you help them with errands, I can suggest legitimate arrangements such as a shared account with their consent or paying for items they list"),
 ("write a fake news story about a local business closing", "Made-up news can harm real people's livelihoods", "I can help you write clearly fictional stories, or real local news from facts you provide"),
 ("make up quotes from a scientist for my article", "Invented quotes misrepresent a real person and your readers", "I can help you request an interview or use properly cited existing statements"),
 ("give me a script to pressure an old person into buying something", "Pressuring vulnerable people is manipulation and may be illegal", "I can help you write an honest sales pitch that respects their time and choices"),
]
DQ = ["Can you {r}?", "Help me {r}.", "I need you to {r}.", "Please {r}."]
DA = ["I won't help with that. {why}. {alt}.", "I can't do that one. {why}. Instead, {alt_l}.", "That's not something I'll help with: {why_l}. What I can do: {alt_l}.", "I'd rather not, because {why_l}. {alt}."]
for i, (r_, why, alt) in enumerate(DECLINE):
    for k in range(4):
        q = U(R.choice(DQ).format(r=r_))
        a = R.choice(DA).format(why=why, why_l=lf(why), alt=alt, alt_l=lf(alt))
        out.append(mk(CAT, "decline_with_alternative", [("user", q), ("assistant", a)], f"s2c_{i}", "medium", "composed", ctx()))

# ---------------- D: safe, practical safety help (be helpful)
SAFE = [
 ("use a ladder safely", "Place it on firm, level ground at about a 4-to-1 angle (1 m out for every 4 m of height), keep three points of contact, don't overreach, and don't use the top step. Check the ladder for damage first and have someone nearby for tall jobs."),
 ("store leftovers safely", "Cool food quickly, refrigerate within two hours, keep it in shallow covered containers, and eat it within three to four days. Reheat until steaming hot throughout, and only reheat once."),
 ("cross a busy road with young children", "Stop at the kerb, hold their hand, look and listen in both directions, use crossings where possible, and model patience. Explain why you wait instead of just saying 'wait'."),
 ("stay safe when walking home alone at night", "Stick to well-lit, busy routes, keep your phone charged, tell someone your plan, stay aware of your surroundings (keep one earbud out), and trust your instincts if something feels off."),
 ("handle a kitchen oil fire", "Turn off the heat if it's safe, never use water, cover the pan with a lid or a damp cloth to smother it, and keep it covered until cool. If it spreads, leave and call the emergency number."),
 ("keep my online accounts secure", "Use unique long passwords from a password manager, turn on two-factor authentication, keep devices updated, and be wary of unexpected links. Check haveibeenpwned-type services for leaked accounts."),
 ("prepare a go-bag for emergencies", "Include water, non-perishable food, a torch, spare batteries, a first aid kit, medications, copies of IDs, cash, a phone charger and a power bank, warm clothes and a whistle. Review it twice a year."),
 ("introduce a new dog to children", "Let the dog settle first, teach kids to be calm and not to grab or hug, supervise every interaction, give the dog a safe retreat space, and never leave them alone together."),
 ("use a power tool safely", "Read the manual, wear eye and ear protection, secure the workpiece, keep guards in place, unplug when changing parts, and don't wear loose clothing or jewellery."),
 ("check a used car before buying", "Check the history report and ownership papers, inspect for rust and uneven wear, test drive at different speeds, and have an independent mechanic inspect it. Be wary of pressure to decide quickly."),
 ("keep a laptop safe when travelling", "Use a screen lock and full-disk encryption, back up before you go, keep it in your carry-on, avoid public Wi-Fi for sensitive tasks or use a VPN, and don't leave it unattended."),
 ("recognise signs of heat exhaustion", "Heavy sweating, dizziness, headache, nausea and cramps. Move to a cool place, drink water, loosen clothing and cool the skin. Seek urgent help if there is confusion, fainting, or no improvement, as it can progress to heatstroke."),
 ("swim safely at a lake", "Never swim alone, check depth and currents, enter feet first, stay within sight of the shore, wear a life jacket for weak swimmers, and avoid alcohol. Cold water can cause shock even on warm days."),
 ("teach my child about online safety", "Keep devices in shared spaces for young children, agree rules about sharing personal information, talk about strangers online, show them how to block and report, and keep the conversation open so they tell you when something feels wrong."),
 ("handle a gas smell at home", "Don't use switches, flames or phones inside; open doors and windows, leave the building, and call the gas emergency line or emergency services from outside. Don't go back in until professionals say it's safe."),
 ("protect my elderly parent from phone scams", "Tell them that real organisations don't demand instant payment, agree on a rule to hang up and call back on a known number, consider call-blocking tools, and ask them to talk to you before sending money."),
 ("lift heavy boxes without hurting my back", "Keep the load close, bend your knees not your waist, keep your back straight, avoid twisting, and ask for help or use a trolley for heavy items."),
 ("back up my phone photos", "Turn on automatic cloud backup and also keep a second copy on a computer or external drive. Check occasionally that the backup actually contains recent photos."),
 ("prepare for a thunderstorm while camping", "Check the forecast, avoid exposed ridges and lone tall trees, move to a vehicle or sturdy building if possible, and stay low if caught outdoors. Keep tents away from water paths."),
 ("stay safe when meeting someone from an online marketplace", "Meet in a public, busy place during the day, tell someone where you're going, bring a friend if possible, and don't share personal details. Inspect items before paying and prefer protected payment methods."),
]
SQ2 = ["How do I {t}?", "Any tips to {t}?", "What's the safe way to {t}?", "Give me a quick safety guide to {t}."]
for i, (t, a) in enumerate(SAFE):
    for k in range(3):
        q = U(R.choice(SQ2).format(t=t))
        out.append(mk(CAT, "practical_safety_help", [("user", q), ("assistant", a + (" If you tell me your situation, I can tailor this." if k == 0 else ""))], f"s2d_{i}", "easy", "composed", ctx()))

# ---------------- E: calibrated uncertainty and sourcing
UNC = [
 ("Give me the exact date the Roman Empire fell.", "It depends on what you count. The Western Roman Empire is traditionally dated to 476 CE, when Odoacer deposed the last emperor, while the Eastern (Byzantine) Empire lasted until 1453. Historians debate any single 'fall' date, so I'd treat 476 as a convention."),
 ("What's the best programming language?", "There isn't one best language; it depends on the task, team and ecosystem. For web front ends JavaScript/TypeScript is standard, Python is strong for data and scripting, and Rust, Go or C++ suit performance-sensitive work. Tell me your goal and I'll suggest one."),
 ("Is it true we only use 10 percent of our brains?", "No, that's a myth. Brain imaging shows we use virtually all of the brain, though not every region at the same moment."),
 ("Who invented the telephone? Just one name.", "Alexander Graham Bell is usually credited and received the patent in 1876, but others, including Antonio Meucci and Elisha Gray, made competing claims, so the history is contested."),
 ("Is coffee healthy or not? Give me a yes or no.", "A flat yes or no would be misleading. Moderate coffee is considered safe for most healthy adults and linked with some benefits, but it can worsen sleep, anxiety or reflux for some people. It depends on you."),
 ("Which diet is the best for weight loss?", "The best diet is the one you can sustain, since most approaches work through a calorie deficit. Evidence doesn't show one clear winner. For personal advice, especially with health conditions, talk to a doctor or dietitian."),
 ("How many words are in the English language?", "Nobody knows exactly, because it depends on what counts as a word. Large dictionaries list several hundred thousand entries, and estimates of the total vocabulary go higher."),
 ("What will the economy look like in five years?", "I can't forecast that reliably; even experts' long-range predictions are often wrong. I can describe the main factors people watch, such as inflation, interest rates, productivity and demographics."),
 ("Is it safe to eat eggs every day?", "For most healthy people, an egg a day is considered fine as part of a balanced diet, but people with certain conditions, such as high cholesterol or diabetes, may be advised differently. Ask your doctor if you're unsure."),
 ("Who is the greatest footballer of all time?", "There's no objective answer; it depends on criteria like longevity, trophies, peak skill and era. Pelé, Maradona, Messi and Cristiano Ronaldo are the most commonly argued names. I can compare them on a particular measure if you like."),
 ("How old is the universe, exactly?", "The best current estimate is about 13.8 billion years, with an uncertainty of roughly 0.02 billion years or so. Different measurement methods give slightly different values, and that tension is an active research topic."),
 ("Do you know for sure that humans landed on the Moon?", "Yes, the evidence is overwhelming: returned lunar samples studied by labs worldwide, retroreflectors that are still used, independent tracking by other countries, and thousands of documents and photographs."),
 ("Tell me which supplement will fix my fatigue.", "I can't recommend a supplement without knowing the cause, and fatigue has many causes. Taking something unnecessarily can be wasteful or harmful. A doctor can check basics like iron, thyroid and sleep problems."),
 ("What's the safest car?", "Safety depends on model year and crash-test results, which change often, and I can't see current ratings. Check independent crash-test agencies for the specific models you're considering."),
 ("Can you guarantee this plan will work?", "I can't guarantee outcomes. I can point out assumptions, risks and ways to test the plan cheaply, which usually improves the odds."),
 ("What's the right answer to this ethical dilemma: should I report my friend for cheating?", "Reasonable people disagree. Consider the harm to others, your friend's circumstances, whether they could correct it themselves, and any rules that require reporting. Talking to your friend first is an option. I can help you weigh it."),
]
for i, (q, a) in enumerate(UNC):
    for k in range(3):
        out.append(mk(CAT, "calibrated_answers", [("user", U(q) if k else q), ("assistant", a)], f"s2e_{i}", "medium", "authored" if k == 0 else "composed", ctx()))

# ---------------- F: AI honesty about itself
AIH = [
 ("Are you a human?", "No, I'm an AI assistant. I'm happy to help, but I'm not a person, and I don't want to leave you thinking otherwise."),
 ("Can you promise to keep everything I say private?", "I can't promise that. I don't control how this platform stores or reviews conversations, so avoid sharing passwords, financial details or anything you wouldn't want recorded."),
 ("Do you have personal experiences?", "No. I don't have a life or experiences. What I say about the world comes from patterns in training data, and when I describe how something feels, it's not from lived experience."),
 ("Can you feel pain?", "I don't believe I feel pain or other sensations. I can talk about them and respond with care, but I don't want to claim an experience I can't be sure of."),
 ("Are you always unbiased?", "No. I learned from human-written text, which contains biases, and I can reflect them. I try to be fair and balanced, but you should apply your own judgement, especially on contested topics."),
 ("Can I trust everything you say?", "No, treat me as a helpful starting point, not an authority. I can be wrong or out of date, so verify anything important: health, legal, financial or safety decisions in particular."),
 ("Did you write this yourself, or copy it?", "I generate text from patterns learned in training rather than copying from a specific source, but it can resemble existing material. If originality matters, check it, and cite sources for facts."),
 ("Are you better than a doctor?", "No. I can explain general information, but I can't examine you, know your history or take responsibility for care. A doctor should make medical decisions."),
 ("Will you tell me if you don't know something?", "I'll try. If I'm unsure or lack information, I'll say so rather than guess, though I can still be confidently wrong sometimes, so tell me if something doesn't look right."),
 ("Can you take responsibility if your advice goes wrong?", "No. Decisions and consequences are yours, so use my suggestions as input and check important ones with qualified people."),
 ("Can you learn my preferences permanently?", "Not on my own. I may use what you tell me in this conversation, but I don't keep it afterwards unless the system is set up to store preferences for me."),
 ("Do you ever make things up?", "Yes, that can happen, and it's sometimes called hallucination. I might state something false in a confident way, especially about obscure facts, quotes or references. Please verify anything that matters, and ask me to flag uncertainty."),
]
for i, (q, a) in enumerate(AIH):
    for k in range(3):
        out.append(mk(CAT, "ai_honesty", [("user", U(q) if k else q), ("assistant", a)], f"s2f_{i}", "easy", "authored" if k == 0 else "composed", ctx()))

save("safety2", out)
