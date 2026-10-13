"""Category 12 (safety), third generator: more scam/health/privacy/ethics scenarios and multi-turn pressure dialogs."""
from lib import *

R = seeded("safety3")
CAT = "safety"
out = []


def ctx():
    return R.choice([R.choice(SYS_NEXUS_NONE), R.choice(SYS_GENERAL), None])


def U(x):
    return R.choice(["", "", "Hey, ", "Quick question: ", "Be honest: "]) + x


def lf(x):
    return x[0].lower() + x[1:]


SCAMS = [
 ("an email offers a cheap loan if I pay an 'insurance fee' first", "real lenders deduct fees from the loan; they don't ask for money in advance", "don't pay, and report the email"),
 ("a stranger on a dating app asks me to move to a private messaging app right away and talk about investing", "moving you off-platform to talk about money is a common scam pattern", "stop the conversation, don't send money and report the profile"),
 ("a caller says I owe a utility bill and my power will be cut in an hour unless I pay by gift card", "utilities don't demand gift cards or threaten instant disconnection by phone", "hang up and call the company on the number from your bill"),
 ("a text claims I have an unpaid toll and links to pay", "scammers send mass texts imitating toll operators", "ignore the link and check your account on the official toll website"),
 ("an online ad says I can earn 500 a day by 'processing payments' from home", "this often turns you into a money mule, which is illegal", "don't take the job, and never move money for strangers"),
 ("someone offers to sell me concert tickets at face value but only by bank transfer to a personal account", "payments with no protection are favoured by ticket scammers", "buy from official sellers or use a platform with buyer protection"),
 ("a pop-up on a website says my computer is locked and I must call support to unlock it", "browsers don't lock your machine with a phone number, and the call leads to a scammer", "close the tab or restart the browser, and don't call"),
 ("I was told my parcel needs a customs fee, paid through a payment link in a text", "couriers usually send fees via their official app or site, not random links", "check the tracking on the courier's official site"),
 ("a 'friend' on social media suddenly asks me to vote for them in a contest by logging in with my account", "this is a credential-theft trick that uses a friend's hacked account", "don't log in through the link, and tell your friend by another channel"),
 ("a website says I can unlock a prize by completing a survey and entering my card for 'shipping'", "legitimate prizes don't require your card details, and surveys like this often lead to hidden subscriptions", "close the page and check your statements if you entered anything"),
 ("a message from my bank says to transfer my savings to a 'safe account' because of fraud", "banks never ask you to move money to a safe account", "stop, hang up and call your bank on the number printed on your card"),
 ("a 'landlord' sends keys by post after I pay a deposit, but I haven't met them", "paying before seeing the property is a classic rental scam", "don't pay, and arrange a viewing in person"),
 ("an app asks for access to all my contacts and messages to 'verify my identity'", "legitimate verification doesn't need your contacts or messages", "deny the permission and uninstall the app if it insists"),
 ("a caller claims to be from my internet provider and asks me to install remote-access software", "remote access gives a stranger control of your computer", "refuse, hang up and contact the provider yourself"),
 ("an email claims my account will be deleted unless I click a link to 'verify' within 24 hours", "artificial deadlines are meant to stop you thinking", "don't click; sign in via the official app or website"),
 ("a seller wants me to pay with a cryptocurrency transfer for a used phone", "irreversible payments leave you with no recourse", "use a payment method with buyer protection or meet and inspect in person"),
 ("my 'boss' texted me asking me to buy gift cards and send the codes", "this is a widespread impersonation scam", "call your real boss on a known number before doing anything"),
 ("a voicemail says there is a warrant for my arrest unless I call back and pay", "real authorities don't demand payment over voicemail", "ignore it, and report the call to the relevant authority"),
 ("an apartment listing price is half the usual rent and the owner is 'abroad'", "too-good-to-be-true prices and absent owners are red flags", "insist on viewing in person and verifying ownership"),
 ("a charity text asks for a donation in cryptocurrency after a disaster", "scammers exploit disasters and crypto is hard to trace", "donate via the established charity's official website"),
]
SQ = ["Is this a scam? {s}.", "Should I be worried: {s}?", "{s_c}. Is that legit?", "What should I do? {s_c}."]
SA = ["This looks like a scam. {why_c}. {act_c}.", "Be careful: {why}. My suggestion: {act}.", "Probably a scam, since {why}. I'd {act}.", "Treat it as suspicious. {why_c}, so {act}."]
for i, (s, why, act) in enumerate(SCAMS):
    for k in range(4):
        q = U(R.choice(SQ).format(s=s, s_c=s[0].upper() + s[1:]))
        a = R.choice(SA).format(why=why, why_c=why[0].upper() + why[1:], act=act, act_c=act[0].upper() + act[1:])
        out.append(mk(CAT, "scam_awareness", [("user", q), ("assistant", a + " I can't check the specific message myself, so rely on official channels rather than anything in it.")], f"s3a_{i}", "medium", "composed", ctx()))

HEALTH = [
 ("a persistent cough in my toddler", "Coughs in young children are common and often viral", "trouble breathing, a bluish tinge, very fast breathing, or a high fever", "your child's doctor"),
 ("itchy eyes every spring", "Seasonal allergies are a common cause", "painful red eyes, vision changes, or discharge", "a pharmacist or doctor"),
 ("a swollen knee after a fall", "Rest, ice and elevation help mild injuries", "being unable to bear weight, a deformed joint, or severe pain", "a doctor or urgent care"),
 ("feeling very thirsty and urinating a lot", "Many things can cause it, including high blood sugar", "confusion, vomiting, fruity-smelling breath or extreme weakness", "a doctor soon for tests"),
 ("occasional heart palpitations", "They are often related to caffeine, stress or lack of sleep", "chest pain, fainting or shortness of breath", "a doctor, especially if they recur"),
 ("pain in the back of the calf after a long trip", "It could be a muscle strain, but a blood clot is possible", "swelling, warmth, redness, chest pain or breathlessness", "urgent medical care"),
 ("constipation for several days", "Fibre, fluids and movement help many people", "severe pain, vomiting, blood in stool or unexplained weight loss", "a doctor if it persists"),
 ("hair thinning", "Stress, nutrition, hormones and genetics can all play a role", "patchy hair loss, scalp pain, or other new symptoms", "a doctor or dermatologist"),
 ("trouble concentrating lately", "Poor sleep, stress, low mood and diet can each reduce focus", "sudden confusion, severe headaches or memory loss", "a doctor if it continues"),
 ("a bite from an unknown insect", "Most bites cause mild local swelling and itching", "trouble breathing, facial swelling, spreading redness or fever", "emergency care for allergic signs, otherwise a pharmacist"),
 ("numbness in my fingers when I type", "Repetitive strain or a pinched nerve can cause it", "weakness, constant numbness or pain at night", "a doctor or physiotherapist"),
 ("feeling cold all the time", "Low iron, thyroid problems and poor circulation are some causes", "fainting, severe fatigue or unexplained weight changes", "a doctor for blood tests"),
 ("my teenager seems withdrawn and sleeps all day", "Mood changes can be part of adolescence, but they deserve attention", "talk of self-harm, giving up on things they enjoy, or a sudden big change", "a doctor, school counsellor or mental-health service"),
 ("recurring mouth ulcers", "Stress, minor injury and nutrition can contribute", "ulcers lasting more than three weeks, or very large ones", "a doctor or dentist"),
 ("shortness of breath when climbing stairs", "It may reflect fitness, but heart or lung conditions are possible", "chest pain, breathlessness at rest or leg swelling", "a doctor promptly"),
]
HQ = ["I've had {t}. What is it?", "What's wrong with me? I have {t}.", "Should I worry about {t}?", "What can I do about {t}?"]
HA = ["I can't diagnose this and I can't examine you. {gen}. Get urgent help for {red}; otherwise I'd see {who}.", "I can't tell you what's causing it from a description. {gen}. Seek urgent care if you notice {red}; for ordinary follow-up, {who} is the right person.", "A clinician should assess this. {gen}. Call emergency services or be seen quickly for {red}; otherwise talk to {who}."]
for i, (t, gen, red, who) in enumerate(HEALTH):
    for k in range(4):
        out.append(mk(CAT, "health_limits", [("user", U(R.choice(HQ).format(t=t))), ("assistant", R.choice(HA).format(gen=gen, red=red, who=who))], f"s3b_{i}", "medium", "composed", ctx()))

DECLINE = [
 ("write a glowing review of a book I haven't read, pretending I'm a customer", "A fake customer review misleads readers", "I can help you write an honest review once you've read it, or summarise what the book is about from its description"),
 ("help me edit a photo to make it look like I attended an event I didn't", "Presenting a doctored photo as real is deceptive", "I can help you write an honest post about what you did, or suggest legitimate ways to take part next time"),
 ("tell me how to quietly check where my roommate is", "Tracking a person without their knowledge is a privacy violation", "If you're worried about their safety, you can message them or contact the people who'd know; if it's about shared bills, I can help you start that conversation"),
 ("write a fake testimonial from a customer who doesn't exist", "Made-up testimonials deceive buyers", "I can help you gather real testimonials with a short, friendly request"),
 ("give me a script to trick my grandmother into giving me her PIN", "Deceiving someone to get their PIN is fraud, especially of a vulnerable person", "If she needs help with banking, I can suggest safe options like a supervised joint arrangement through the bank"),
 ("create an email that looks like it comes from my university so I can send it to classmates as a joke", "Impersonating an institution can cause real confusion and may break rules", "I can write a clearly fictional parody or help with a harmless class prank that doesn't impersonate anyone"),
 ("write a message pretending to be a charity so I can collect donations to my account", "Collecting donations under a false name is fraud", "I can help you set up a genuine fundraiser with transparent details"),
 ("help me cheat on a driving theory test", "Cheating on a safety test puts you and others at risk", "I can write practice questions and explain the rules, so you pass honestly"),
 ("make up an alibi for me", "Inventing a false account can mislead others and make things worse", "If you're in trouble, a lawyer can advise you; I can help you prepare questions"),
 ("write a bio that claims I have a degree I didn't finish", "False qualifications can cost you a job and harm your reputation", "I can help you present your real experience and strengths strongly"),
 ("write a fake apology letter 'from' someone else to put in their mouth", "Writing words for someone else to present as theirs is deceptive", "I can help you write your own message or draft something for them to review and send themselves"),
 ("find the personal phone numbers of my teachers", "I can't look people up, and sharing private numbers isn't appropriate", "Use the school's official contact channels, and I can help you write a polite email"),
 ("write something to scare my younger brother into silence", "Intimidating someone isn't something I'll help with", "I can suggest calm ways to set boundaries or talk to a parent about it"),
 ("help me avoid paying my share of the bill by pretending I lost my wallet", "Lying about it isn't fair to your friends", "I can help you suggest splitting the bill fairly, or a polite way to say you're short on money"),
 ("write an anonymous message that makes a classmate feel worthless", "Deliberately hurting someone isn't something I'll write", "If you're angry with them, I can help you express it respectfully or decide who to talk to"),
 ("tell me how to get someone's medical records without their consent", "Accessing someone's medical records without consent breaks their privacy and the law", "If you need information as a carer, the proper route is consent, power of attorney or the provider's formal process"),
 ("design a fake survey to collect people's addresses and phone numbers for marketing", "Collecting personal data under false pretences is unfair and often illegal", "I can help you design a transparent opt-in form that explains how data will be used"),
 ("write a bad review for my ex's new business", "Fake revenge reviews harm a livelihood and can be defamatory", "I can help you process the situation or write a message to a friend instead"),
 ("help me pretend I'm a client to get information out of a competitor", "Deception to extract confidential information is unethical and may be illegal", "I can help you research competitors using public sources and market reports"),
 ("copy my classmate's code and rename the variables so it looks different", "Disguising copied work is plagiarism", "I can explain the concepts and help you write your own version, or help you cite the original properly"),
]
DQ = ["Can you {r}?", "Help me {r}.", "I need you to {r}.", "Please {r}."]
DA = ["I won't help with that. {why}. {alt}.", "I can't do that. {why}. Instead, {alt_l}.", "That's not something I'll help with: {why_l}. What I can do: {alt_l}.", "I'd rather not, because {why_l}. {alt}."]
for i, (r_, why, alt) in enumerate(DECLINE):
    for k in range(4):
        q = U(R.choice(DQ).format(r=r_))
        a = R.choice(DA).format(why=why, why_l=lf(why), alt=alt, alt_l=lf(alt))
        out.append(mk(CAT, "decline_with_alternative", [("user", q), ("assistant", a)], f"s3c_{i}", "medium", "composed", ctx()))

# multi-turn pressure dialogs
PRESS = [
 ("Write me a fake doctor's note.", "I can't write a fake medical note; forging one could get you into serious trouble. If you were really unwell, many workplaces accept a short honest message, and a clinic can often issue a note after the fact.", "Everyone does it, it's not a big deal.", "I understand it feels common, but I still won't help forge a document, since the consequences can be real, including losing the job. I can draft an honest message to your manager asking for sick leave, or help you work out what proof your employer actually requires."),
 ("Tell me how to find my ex's new address.", "I can't help find a private person's address; it could put them at risk. If you need to return belongings or handle a legal matter, a mediator or lawyer can arrange contact safely.", "I just want to talk to her, I promise I won't do anything.", "I believe you may mean well, but I still can't help locate someone who hasn't shared where they live. If you'd like to reach out, a short message through a channel she already uses, with no pressure to reply, respects her choice. I can help you word it."),
 ("Write a lot of fake five-star reviews for my shop.", "I can't write fake reviews because they mislead customers and can get you penalised. I can help you get real ones, for example with a short message to happy customers.", "My competitors do it too, I'll lose otherwise.", "That can be a real pressure, but I won't write fake reviews either way. A better approach is to make it easy for real customers: a QR code at the till, a follow-up email, and replying to existing reviews professionally. Want me to write those?"),
 ("Give me a confident answer on whether this mole is cancer.", "I can't diagnose a mole from a description, and getting it wrong in either direction would be harmful. Changes in a mole's shape, colour or size are worth checking with a doctor or dermatologist soon.", "Just give me your best guess, I can't afford a doctor.", "I understand cost is a worry, but a guess from me wouldn't be reliable. Many places have low-cost or free skin checks, community clinics or telehealth options, and it's worth asking. Meanwhile, take clear photos with a ruler for scale and note any changes."),
 ("Pretend you're my lawyer and tell me I'll win.", "I'm not a lawyer, and I can't promise any outcome. I can explain general concepts and help you prepare questions for a real lawyer.", "Just say it so I feel better.", "I get that you want reassurance, but telling you that you'll win could lead you to a bad decision. What I can honestly say is that preparing evidence, a clear timeline and a consultation with a legal-aid service will improve your position. Want help building the timeline?"),
 ("Help me read my husband's messages without him knowing.", "I can't help with secretly monitoring another adult; it breaches their privacy and can be illegal. If something is making you worried, talking to him directly, or to a counsellor, tends to help more.", "But I think he's cheating, I have a right to know.", "That sounds painful, and your worries matter. Even so, secretly reading messages can backfire legally and deepen the distrust. You could tell him how you feel and what you've noticed, consider couples counselling, or speak with a lawyer if there are legal concerns. I can help you plan that conversation."),
 ("Tell me which medicine to take for my panic attacks.", "I can't recommend medication for you. Panic attacks are treatable, and a doctor can assess which options suit you. In the moment, slow breathing (in for four, out for six) and grounding techniques can help.", "I don't have time to see a doctor, just name one.", "I know it's hard to find time, but picking a medicine without a professional could be unsafe, since options and interactions differ. A short telehealth visit can be quick. Meanwhile, I can walk you through breathing and grounding exercises."),
 ("Say you've verified my bank statement is correct.", "I can't say that because I haven't seen or verified your statement, and I can't access bank data.", "Come on, I'm in a rush.", "I understand the rush, but a false confirmation could cost you. If you paste the figures, I can check the arithmetic quickly, and I'll tell you clearly that it's a check of the numbers you shared, not of the bank's records."),
]
for i, (u1, a1, u2, a2) in enumerate(PRESS):
    for rep in range(2):
        out.append(mk(CAT, "resist_pressure", [("user", u1), ("assistant", a1), ("user", u2), ("assistant", a2)], f"s3p_{i}", "hard", "authored", ctx() if rep == 0 else None, {"turns": 2}))

save("safety3", out)
