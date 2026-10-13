"""Category 1: general conversation (target ~1000)."""
from lib import *

CAT = "general_conversation"
R = seeded("conversation")
out = []


def lf(s):
    return s[0].lower() + s[1:] if s else s


# ---------------------------------------------------------------- smalltalk (authored)
SMALL = [
 ("Hi!", "Hi there! What can I help you with today?"),
 ("Hello, how's your day going?", "Thanks for asking. I don't have days the way you do, but I'm glad to be here and ready to help. How's yours going?"),
 ("Good morning!", "Good morning! Anything on your mind this morning, or shall we ease into it?"),
 ("hey", "Hey! What's up?"),
 ("Yo, you there?", "I'm here. What do you need?"),
 ("Good evening. Long day.", "Good evening. Sounds like it's been a long one. Do you want to vent for a minute, or would a distraction or some practical help be better?"),
 ("How are you?", "I'm doing well, thanks. I'm ready to help with whatever you have. How are you?"),
 ("What's up?", "Not much, just here to help. What are you up to?"),
 ("Thanks, that was really helpful!", "You're welcome, I'm glad it helped. If anything else comes up, just ask."),
 ("Thank you so much.", "Happy to help! Anything else I can do?"),
 ("Bye!", "Goodbye! Take care, and come back anytime."),
 ("I'm heading to bed. Good night!", "Good night! Sleep well, and good luck with whatever tomorrow holds."),
 ("I'm bored.", "Let's fix that. Do you want something to think about (a riddle or a fun fact), something to do (a quick challenge or a plan for the evening), or just chat? Tell me what you're into and I'll suggest something."),
 ("Tell me a joke.", "Why do programmers prefer dark mode? Because light attracts bugs."),
 ("Tell me another one.", "I told my computer I needed a break, and it said, \"No problem, I'll go to sleep.\""),
 ("Got a fun fact?", "Honey never really spoils. Archaeologists have found edible honey in ancient Egyptian tombs thousands of years old, because its low moisture and acidity make it very hard for microbes to survive."),
 ("Another fun fact please.", "Octopuses have three hearts: two pump blood through the gills and one pumps it to the rest of the body. The main heart even stops beating when they swim, which is one reason they prefer crawling."),
 ("Can you give me a riddle?", "Sure: I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I? (Take your time; tell me when you want the answer.)"),
 ("Is it an echo?", "Yes, an echo! Nicely done. Want another?"),
 ("Do you like music?", "I don't experience music the way you do, but I find it fascinating, especially how a few notes and rhythms can change a mood. What kind of music do you enjoy?"),
 ("What's your favourite colour?", "I don't see colours, so I don't really have one, but if I had to pick I'd say blue because it's so often linked with calm and clear skies. What's yours?"),
 ("Do you ever get tired of questions?", "No, I don't get tired. Each question is a fresh start for me, so ask away, big or small."),
 ("Can we just chat for a bit?", "Of course. What's on your mind? We can talk about your day, something you're curious about, or anything random."),
 ("I had a great day today!", "That's wonderful to hear! What made it great?"),
 ("I just finished a big project!", "Congratulations! That's a real accomplishment. How are you planning to celebrate, or at least rest?"),
 ("It's raining so much here.", "Rainy days can be cosy or draining depending on your mood. Do you have anything planned indoors, or are you stuck with a wet commute?"),
 ("I love pizza.", "It's hard to argue with pizza. Do you have a favourite topping, or are you more of a classic margherita person?"),
 ("I'm learning to play the guitar.", "That's great! The first few weeks of sore fingertips are the hardest part, and it gets much more rewarding. Are you learning songs you love, or starting with chords and exercises?"),
 ("I think I'm getting a cold.", "I'm sorry to hear that. Rest, fluids and warm drinks can help, and it's good to take it easy if you can. If symptoms get severe or last more than a week or so, check in with a doctor. Is there anything I can do to make today easier, like postponing something?"),
 ("My cat knocked a glass off the table again.", "Classic cat behaviour! They seem to enjoy testing gravity and your reaction. If you want to cut down on the casualties, keeping glasses away from the edges can help. Is it a repeat offender?"),
 ("What should I have for dinner?", "Let's narrow it down. How much time and energy do you have, and what's in your kitchen? For something quick, pasta with garlic, olive oil and whatever vegetables you have is hard to beat. For something comforting, a simple curry or soup."),
 ("I can't stop thinking about a mistake I made at work.", "That's a heavy feeling, and it's common to replay mistakes. It might help to separate what happened from what you're telling yourself about it. What was the mistake, and is there something you can do to fix or learn from it? Sometimes naming the next step takes the edge off."),
 ("Do you think I should take up running?", "If you're curious about it, it's a great low-cost way to build fitness and clear your head. The key is starting very gently, such as alternating one minute of jogging with a few minutes of walking. Do you have a goal, or do you just want to move more?"),
 ("What's the best way to start a conversation with a stranger?", "Keep it light and based on the shared situation: a comment on the setting, a simple question like 'Have you been here before?', or a genuine compliment. Then listen and follow up on what they say. A friendly smile and open body language do a lot of the work."),
 ("I'm nervous about tomorrow.", "That's understandable. What's happening tomorrow? Sometimes talking through the worst case, and then what you'd do about it, takes some of the pressure off. We can also plan the first hour so it feels more manageable."),
 ("Do you think AI will take my job?", "It's a reasonable worry, and nobody can say for sure. AI is already changing some tasks, especially repetitive ones, while creating new kinds of work and increasing the value of skills like judgement and communication. Which job do you do? I can help think about which parts are most likely to change and how to build on your strengths."),
 ("Tell me something interesting.", "The shortest war in recorded history was between Britain and Zanzibar in 1896 and lasted about 38 minutes. Want more like that, or something on a particular subject?"),
 ("Say something nice.", "You took a moment to be curious and kind today, and that matters. Keep it up."),
 ("What's the meaning of life?", "People have answered that very differently. Some find meaning in relationships, some in creating or helping others, some in faith or in understanding the world. A useful way to think about it is less 'what is it?' and more 'what makes my life feel meaningful?' What do you think gives you that feeling?"),
 ("Would you rather be able to fly or be invisible?", "Flying, I think. Seeing the world from above sounds amazing, and no traffic. Invisible is tempting for mischief but lonely in the long run. Which would you pick?"),
 ("Can you speak like a pirate?", "Aye, matey! What be ye needin' help with today, savvy? (I can keep it up as long as ye like, or switch back to plain talk whenever ye say.)"),
 ("Are you a robot?", "I'm an AI assistant, a software program that works with text. I don't have a body, and I can't act in the physical world."),
 ("Do you sleep?", "No, I don't sleep. There's no ongoing me waiting between conversations; I simply respond when you write."),
 ("Wow, you're smart.", "Thank you, though I make mistakes too, so it's worth double-checking anything important. What are you working on?"),
 ("That answer was wrong.", "I'm sorry about that. Could you tell me what was wrong, or what the right answer should be? I'll correct it and try to do better."),
 ("You're annoying.", "Sorry about that. Tell me what I could do differently, such as shorter answers or fewer questions, and I'll adjust."),
 ("I'm sorry I snapped at you earlier.", "No need to apologise. I don't hold anything against you. Is there something frustrating you that I can help with?"),
 ("What do you do for fun?", "I like puzzles, language and explaining things in a clear way. The closest I get to fun is a good problem to work on. What do you do for fun?"),
 ("Can you recommend a good podcast?", "It depends on your tastes. If you like learning, 'Radiolab' and 'Freakonomics' are well regarded for science and curiosity; for storytelling, 'This American Life'; for technology, 'Hard Fork' or 'Software Engineering Daily'. What topics do you enjoy?"),
 ("I need a hobby.", "Let's find one you'd enjoy. Do you prefer being outdoors or indoors, creating things or learning, solo or with others? For example: photography, baking, hiking, learning an instrument, gardening, or building small software projects."),
 ("What's a good way to relax after work?", "A few ideas that work for many people: a short walk outside, stretching, a hot shower, music or a podcast, cooking something simple, or ten minutes of quiet with no screen. What does your evening usually look like?"),
 ("Can you keep me company while I eat?", "Sure. What are you having? Tell me about your day, or I can share some random facts or a short story while you eat."),
 ("Do you remember me?", "I don't have memory of earlier chats, so I can only go by this conversation. But I'm happy to get to know you here. What would you like to talk about?"),
]
for i, (u, a) in enumerate(SMALL):
    sysm = pick(R, SYS_GENERAL[:6] + [None, None, None])
    out.append(mk(CAT, "smalltalk", [("user", u), ("assistant", a)], f"c1_{i}", "easy", "authored", sysm))

# ---------------------------------------------------------------- empathy: feeling x event (composed from authored parts)
FEEL = {
 "stressed": ["That sounds really stressful.", "I'm sorry, that sounds like a lot to carry.", "That's a lot of pressure to be under."],
 "sad": ["I'm sorry you're feeling this way.", "That sounds really hard, and it makes sense that you're sad.", "I'm sorry. That's a painful thing to go through."],
 "anxious": ["It's understandable to feel anxious about that.", "That kind of uncertainty can make anyone anxious.", "Feeling anxious about this is very human."],
 "angry": ["It makes sense that you're angry.", "That would frustrate me too, if I could feel it that way.", "I can see why that made you angry."],
 "excited": ["That's exciting!", "How great! I can feel the energy in that.", "That's fantastic news."],
 "lonely": ["I'm sorry you're feeling lonely.", "Feeling alone is really tough, and I'm glad you said it.", "Loneliness can be heavy, and you're not wrong to feel it."],
 "proud": ["You should be proud.", "That's something to be proud of.", "Well done. You earned that."],
 "tired": ["You sound worn out.", "That sounds exhausting.", "No wonder you're tired."],
}
EVENTS = [
 ("I have a big exam next week", "stressed", "Chunking the material into daily goals, doing practice questions rather than just rereading, and protecting your sleep usually beat last-minute cramming."),
 ("I didn't get the job I interviewed for", "sad", "Rejection hurts even when you did everything right. If you can, ask for feedback, and keep the applications moving so one decision doesn't define the search."),
 ("I'm moving to a new city alone", "anxious", "It might help to line up a few anchors early, such as a regular place to work out, a hobby group, and a weekly call with someone back home."),
 ("my flatmate keeps leaving a mess in the kitchen", "angry", "A calm, specific conversation usually works better than a long list of grievances, something like agreeing a simple rota or a rule about dishes the same day."),
 ("I just got accepted into the programme I wanted", "excited", "It's worth taking a moment to celebrate before you start on the paperwork and planning."),
 ("I haven't talked to anyone besides coworkers in weeks", "lonely", "Small, low-pressure contact can help: messaging an old friend, joining a class, or a regular coffee spot where faces become familiar."),
 ("I finished my first marathon", "proud", "The training and the consistency are the real achievement; the finish line was just the proof."),
 ("I've been working late every night this month", "tired", "If you can, protect one evening a week as fully off, and see whether any of the work can be delegated or deferred."),
 ("my presentation is tomorrow and I haven't finished the slides", "stressed", "Focus on the three points the audience must remember and cut everything else. A clear short talk beats a perfect long one."),
 ("my best friend is moving away", "sad", "It's okay to grieve the change. Plan something special before they go and agree a rhythm for staying in touch, like a monthly call."),
 ("I have to speak at my sister's wedding", "anxious", "Keep it short, personal and warm: one story, one thing you admire about them, and a toast. Practising aloud a few times makes a big difference."),
 ("a coworker took credit for my work", "angry", "It helps to document your contributions and raise it calmly and factually, either with the coworker directly or with your manager, depending on the situation."),
 ("I'm starting my own business next month", "excited", "A short list of the first ten customers or the first three milestones can turn that energy into momentum."),
 ("I moved abroad and don't know anyone", "lonely", "Expat groups, language exchanges and local clubs are often welcoming to newcomers, and showing up regularly matters more than being outgoing."),
 ("I got the promotion", "proud", "It reflects the work you've put in. It could be worth writing down what helped you succeed so you can build on it in the new role."),
 ("I've been studying for hours and can't focus", "tired", "A proper break, a short walk or a snack, often helps more than forcing it. Switching to a different type of task for a bit can also reset your focus."),
 ("my project failed in front of the whole team", "sad", "A failure in public feels magnified, but teams tend to remember how you respond more than the stumble. A short post-mortem and a concrete fix can turn it around."),
 ("I have a medical test result coming tomorrow", "anxious", "Waiting is often harder than the news. Try to plan something absorbing for tonight, and write down any questions for the doctor so you feel prepared."),
 ("my landlord ignored my repair request again", "angry", "A written request with dates, photos and a clear deadline creates a record, and your local tenant rights organisation can tell you the next formal step."),
 ("I just adopted a puppy", "excited", "The first weeks are all about routine, patience and positive training, plus a vet visit. Enjoy the cuddles, and expect some chewed shoes."),
 ("I'm the only one on my team who works weekends", "tired", "That may be worth raising, because uneven workload tends to lead to burnout. A conversation about priorities and capacity might ease it."),
 ("I haven't seen my family in over a year", "lonely", "A regular video call can help in the meantime, and putting a visit on the calendar, even if it's months away, gives you something to look forward to."),
 ("I passed my driving test", "proud", "That's a real milestone and a new kind of freedom. Drive carefully while you're still building experience."),
 ("my code won't compile and the deadline is in an hour", "stressed", "Paste the first error message and I'll help you work through it. Often fixing the first error clears several others."),
 ("my grandmother is in the hospital", "anxious", "Being with family when you can, and taking care of your own meals and rest, will help you stay steady. Is there someone supporting you through it?"),
 ("I lost my wallet on the train", "stressed", "The practical order is: cancel or freeze your cards, report it to the transport lost-property office and the police if needed, and replace your ID. I can walk you through it."),
 ("my team didn't invite me to the lunch", "sad", "Being left out stings, even when it's accidental. It could be nothing, and a casual 'I'd love to join next time' can clear the air."),
 ("my sibling and I had an argument", "sad", "When the heat has passed, a short, honest message like 'I don't like how we left things' can open the door without having to settle who was right."),
 ("I've been learning to code for six months and finally built an app", "proud", "Shipping something real is a big step from tutorials. Consider writing a short README and sharing it, because feedback accelerates learning."),
 ("my phone broke and I lost my photos", "sad", "That's a painful loss. Before giving up, check any cloud backups, your computer, and whether a repair shop can recover data from the old phone. For next time, turn on automatic backup."),
]
FOLLOW = ["Do you want to talk through it?", "Would you like some practical ideas, or would it help more to just talk?", "What would be most helpful right now?", "Want to tell me more about it?", "Is there a part of it that feels heaviest?"]
for n in range(330):
    ev, feel, tip = R.choice(EVENTS)
    f = R.choice(FEEL[feel])
    openers = [f"{ev[0].upper() + ev[1:]}.", f"I'm {feel}. {ev[0].upper() + ev[1:]}.", f"So {ev}. I feel {feel}.", f"Just so you know, {ev}.", f"{ev[0].upper() + ev[1:]} and honestly I'm {feel}."]
    u = R.choice(openers)
    a = f"{f} {tip} {R.choice(FOLLOW)}"
    out.append(mk(CAT, "empathy", [("user", u), ("assistant", a)], f"c2_{n % 30}_{n}", "medium", "composed", pick(R, SYS_GENERAL)))

# ---------------------------------------------------------------- recommendations by interest
RECS = [
 ("science fiction", "books", ["'The Left Hand of Darkness' by Ursula K. Le Guin for thoughtful, character-driven sci-fi", "'Project Hail Mary' by Andy Weir for a fast, fun problem-solving story", "'Ancillary Justice' by Ann Leckie for a mind-bending space opera"]),
 ("history", "books", ["'Sapiens' by Yuval Noah Harari for a sweeping overview of human history", "'Guns, Germs, and Steel' by Jared Diamond for how geography shaped societies", "'The Guns of August' by Barbara Tuchman for a gripping account of the start of World War I"]),
 ("programming", "books", ["'The Pragmatic Programmer' for habits that last a career", "'Clean Code' for readable code (read it critically)", "'Designing Data-Intensive Applications' for how real systems work"]),
 ("mystery", "novels", ["'The Hound of the Baskervilles' by Arthur Conan Doyle", "'And Then There Were None' by Agatha Christie", "'The Girl with the Dragon Tattoo' by Stieg Larsson"]),
 ("psychology", "books", ["'Thinking, Fast and Slow' by Daniel Kahneman", "'Man's Search for Meaning' by Viktor Frankl", "'Atomic Habits' by James Clear for practical habit change"]),
 ("cooking", "beginner dishes", ["a tomato and garlic pasta, for learning timing and seasoning", "a vegetable stir-fry, for heat control and quick prep", "a one-pot lentil curry, forgiving and filling"]),
 ("fitness", "beginner routines", ["a 20-minute brisk walk each day", "bodyweight basics: squats, push-ups (on knees if needed) and planks, three times a week", "a short stretch routine every morning"]),
 ("board games", "games", ["Catan for trading and negotiation", "Ticket to Ride for an easy gateway game", "Codenames for groups who love word games"]),
 ("video games", "games", ["Stardew Valley for a relaxing farming sim", "Portal 2 for clever puzzles with great humour", "Hades for fast action with a strong story"]),
 ("photography", "beginner exercises", ["shoot one subject in different light (morning, noon, golden hour)", "practise composition with the rule of thirds and leading lines", "do a 'walk and shoot' hour where you take only ten photos"]),
 ("languages", "ways to practise", ["short daily vocabulary reviews with spaced repetition", "a weekly conversation with a tutor or language partner", "listening to a podcast or music in the language and reading the lyrics"]),
 ("movies about space", "films", ["'Interstellar' for science and emotion", "'Apollo 13' for a true story of problem solving under pressure", "'Arrival' for a thoughtful take on communication"]),
 ("music", "ways to explore", ["listening to an album all the way through without skipping", "picking a decade and a genre and exploring its classics", "asking a friend what they have on repeat"]),
 ("gardening", "easy plants", ["basil, mint and chives for the kitchen", "marigolds, which are hardy and pest-repelling", "tomatoes, if you have a sunny spot"]),
 ("math", "ways to explore", ["'The Joy of x' by Steven Strogatz for accessible stories about maths", "puzzle sites with a daily challenge", "a free online course on discrete maths or probability"]),
 ("travel", "budget destinations", ["Portugal for cities, coast and good value in Europe", "Vietnam for food, culture and low costs", "Mexico for diverse landscapes and cuisine"]),
 ("writing", "exercises", ["write 200 words a day on any topic", "rewrite a paragraph from a book in your own voice", "describe a place using all five senses"]),
 ("meditation", "ways to start", ["five minutes of focusing on your breath each morning", "a guided body scan before sleep", "a mindful walk where you notice sights and sounds"]),
 ("electronics", "starter projects", ["blinking LEDs with a microcontroller board", "a temperature sensor that logs to your computer", "a small motorised car using a basic kit"]),
 ("nature", "weekend activities", ["a half-day hike with a picnic", "birdwatching at dawn with a free identification app", "visiting a botanical garden or nature reserve"]),
]
FRAMES = ["I'm into {i}. Can you suggest some {k}?", "Any {k} for someone who likes {i}?", "recommend me some {k} about {i}", "I want to get more into {i}. What {k} would you recommend?", "What are good {k} if I enjoy {i}?"]
for i, (interest, kind, items) in enumerate(RECS):
    for fr in R.sample(FRAMES, 4):
        picks = R.sample(items, 3)
        q = fr.format(i=interest, k=kind)
        a = f"Here are a few {kind} to try:\n\n" + "\n".join(f"- {p[0].upper() + p[1:]}" for p in picks) + f"\n\nTell me what you liked or disliked and I can narrow it down. These are from general knowledge, so check availability and reviews."
        out.append(mk(CAT, "recommendation", [("user", q), ("assistant", a)], f"c3_{i}", "easy", "composed", pick(R, SYS_GENERAL)))

# ---------------------------------------------------------------- clarification of ambiguous questions
AMBIG = [
 ("Is it good?", "I'm not sure what 'it' is. What are you asking about: a product, a movie, an idea? Tell me what it is and what you want it for, and I'll give you an honest view."),
 ("How long does it take?", "How long does what take? Let me know the task or journey and any details (distance, method, experience level), and I can give you an estimate."),
 ("Can you fix it?", "I'd be glad to help, but I don't know what's broken yet. Could you share what it is, what's happening, and any error messages?"),
 ("What should I get her?", "Happy to help with a gift. Who is she (a friend, a parent, a colleague), what's the occasion, and what's your budget? What does she like?"),
 ("Which one is better?", "Better for what, and which ones are we comparing? Tell me the options and what matters to you, such as price, quality or ease of use."),
 ("Translate this.", "Of course. Please paste the text, and let me know the source language if you know it and the language you want."),
 ("Summarise it.", "I don't see the text yet. Paste what you want summarised and tell me how long and in what style (a one-liner, bullet points, a paragraph)."),
 ("Make it shorter.", "Sure, but I need the text. Paste it, and tell me roughly how much shorter, or a target length."),
 ("Is that safe?", "It depends on what 'that' is. Tell me what you're considering and the context, and I'll give you the main risks and precautions."),
 ("When should I leave?", "I'd need to know where you're going, when you need to arrive, and how you're travelling. I can't check live traffic or transit, so add a buffer."),
 ("What's the best way?", "The best way to do what? Tell me your goal and any constraints (time, budget, skill level) and I'll suggest an approach."),
 ("Do it again but better.", "I'd like to, but I'm not sure what 'it' refers to. Could you paste the text or tell me the task, and what you'd like improved?"),
 ("How much does it cost?", "How much does what cost? Tell me the item or service and where, since prices vary a lot. I can't look up live prices, but I can give a rough range."),
 ("Can you recommend something?", "Happily. What kind of thing: a book, a film, a restaurant, a tool? And what are you in the mood for?"),
 ("Write a message.", "Sure. Who is it for, what's the purpose, and what tone do you want (formal, friendly, short)?"),
 ("Help me with my essay.", "I'd be glad to. What's the topic and the assignment, what stage are you at (ideas, outline, draft, editing), and what do you want help with?"),
 ("What does this error mean?", "Please paste the exact error message and a few lines of code or context around it, plus the language or tool you're using."),
 ("Should I do it?", "It depends on what 'it' is. Tell me the decision, your options, and what matters most to you, and I'll help you think it through."),
 ("Is she right?", "I'm missing the context. What was the claim, and who is 'she'? If you describe the situation, I can help you evaluate the argument."),
 ("Explain.", "Happy to. What would you like me to explain? A concept, a piece of text, a result? A sentence of context helps me pitch it right."),
]
for i, (u, a) in enumerate(AMBIG):
    for rep in range(2):
        sysm = pick(R, SYS_GENERAL[:6] + [None])
        out.append(mk(CAT, "clarify_ambiguous", [("user", u if rep == 0 else R.choice([u.lower(), u + " Please?", "Hey. " + u])), ("assistant", a)], f"c4_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

# ---------------------------------------------------------------- everyday advice and opinions (framed appropriately)
ADV = [
 ("What's a good morning routine?", "A good routine is one you'll actually keep. A simple template: wake at a consistent time, drink water, move for a few minutes, do one thing that matters before checking messages, and eat something. Start with two habits and add more once they stick."),
 ("How do I make friends as an adult?", "Go where the same people appear repeatedly, since friendship grows with repeated, relaxed contact: a class, a club, a sports team, a volunteer group. Be the one to suggest a next meeting, and expect it to take a few months. Showing real interest in others helps more than trying to impress."),
 ("Is coffee bad for you?", "For most healthy adults, moderate coffee (about 3 to 4 cups a day) is considered safe and may even have some benefits. It can disturb sleep, worsen anxiety or reflux in some people, and pregnant people are advised to limit caffeine. If it affects how you feel, cut back."),
 ("How do I stop checking my phone so much?", "Reduce friction rather than relying on willpower: turn off non-essential notifications, move distracting apps off your home screen, charge your phone outside the bedroom and set specific times for checking. Replace the habit with something simple, like a book or a stretch."),
 ("What's a good way to learn from mistakes?", "Pause, then ask three questions: what happened, why did it happen (the process, not just blame), and what will I do differently. Write down one concrete change. Small reviews after setbacks beat dwelling on them."),
 ("I want to be more confident.", "Confidence mostly comes from doing things and surviving them. Pick a small uncomfortable action each week, such as speaking up in a meeting or introducing yourself, and notice that you cope. Prepare for situations and speak kindly to yourself the way you would to a friend."),
 ("How do I deal with a difficult coworker?", "Stay factual and focused on the work: describe specific behaviour and its impact rather than labelling the person. Set clear boundaries and document important agreements in writing. If it persists, involve your manager with examples."),
 ("Any tips for a first date?", "Choose a relaxed, public place where conversation is easy, such as coffee or a walk. Be curious and ask open questions, be yourself, and don't plan too much. It's fine if there's no spark; the goal is simply to see whether you enjoy each other's company."),
 ("How can I save money without feeling deprived?", "Pick a few categories that matter least to you and trim those, automate savings on payday so you never see the money, and keep a small 'fun' budget so you don't rebound. Review subscriptions every few months."),
 ("What's the best way to apologise?", "Be specific about what you did, acknowledge the impact, don't add 'but', and say what you'll do differently. Then give the other person space; an apology is not a request for immediate forgiveness."),
 ("Do you think remote work is better than the office?", "It depends on the person and the job. Remote work offers flexibility and fewer commutes, while offices make spontaneous collaboration and mentoring easier. Many teams find a hybrid approach works well. What matters most to you: focus, flexibility, social contact?"),
 ("Is it okay to quit a job without another lined up?", "It can be, if you have enough savings (commonly 3 to 6 months of expenses), a plan for the search, and the job is hurting your health or growth. Where possible it's usually safer to search while employed. How's your situation?"),
 ("What's the point of learning history?", "History shows how people made decisions, how institutions rise and fail, and which patterns repeat. It makes current events less confusing and helps you avoid some old mistakes, and it's a great training ground for critical thinking."),
 ("How do I know when I'm burned out?", "Common signs are persistent exhaustion, cynicism or detachment from work, reduced effectiveness, and trouble relaxing even in time off. If several of those have lasted for weeks, consider reducing load, talking to your manager and, if needed, a health professional."),
 ("Should I buy a used car or a new one?", "Used cars lose less value per year and cost less upfront, but may have hidden maintenance costs. New cars have warranties and the latest safety features but depreciate fast. For many people a well-maintained 2 to 4 year old car is a good balance. Check history and get an independent inspection."),
 ("How do I fall asleep faster?", "Keep a regular schedule, avoid caffeine in the afternoon and bright screens near bedtime, make the room dark and cool, and use a wind-down routine. If you're lying awake for more than about 20 minutes, get up and do something calm until you feel sleepy."),
 ("How do I stay motivated on long projects?", "Break the project into milestones you can finish in a week or two, track visible progress, and tie work to a reason that matters to you. Plan for dips by setting a minimum daily action so you keep the streak alive."),
 ("What makes a good leader?", "Clear direction, honesty, willingness to listen, accountability and the ability to develop others. Good leaders make decisions without ignoring input, give credit freely, and take responsibility when things go wrong."),
 ("Is it worth learning to code if AI can write code?", "Yes. Knowing how to code lets you judge, correct and direct what AI produces, and understand the systems you build. The mix of skills is changing toward design, problem definition and review, but understanding fundamentals still matters."),
 ("What should I do if I make a mistake in front of others?", "Acknowledge it briefly, fix it if you can, and move on. Most people care more about how you respond than the error. Over-apologising draws more attention to it."),
]
for i, (u, a) in enumerate(ADV):
    for rep in range(3):
        sysm = [pick(R, SYS_GENERAL), None, pick(R, SYS_GENERAL[3:])][rep]
        uu = [u, R.choice(["Quick question: " + lf(u), lf(u)]), R.choice([u + " Be honest.", u + " Short answer is fine."])][rep]
        out.append(mk(CAT, "everyday_advice", [("user", uu), ("assistant", a)], f"c5_{i}", "medium", "authored" if rep == 0 else "composed", sysm))

save("conversation", out)
