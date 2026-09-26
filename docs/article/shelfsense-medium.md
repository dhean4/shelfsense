<!--
Medium draft. Medium's editor does not convert markdown on paste: open this file in a
markdown preview, copy from the rendered view, then upload the images from docs/images by
hand where the [IMAGE: ...] markers are and paste the italic line beneath as the caption.

Subtitle: A retail intelligence system for shelf monitoring, reorder recommendations, and
fridge monitoring. A project about putting a person between an AI model and the money.
Tags: Artificial Intelligence, Retail, Nigeria, Software Engineering, Claude
Canonical URL: (leave blank on first publish)

Images, in order:
  ../images/review-queue.png   alt: The review queue holding three proposed actions
  ../images/run-planner.png    alt: One planner run: summary, actions requested, proposals, cost
  ../images/dashboard.png      alt: The dashboard with the Ikeja store marked red
  ../images/evals.png          alt: The evaluation scoreboard
-->

# ShelfSense: Building AI That Knows When to Ask a Human

_A retail intelligence system for shelf monitoring, reorder recommendations, and fridge
monitoring. A project about putting a person between an AI model and the money._

Ada runs the morning round for a small distributor in Lagos. At each shop, she photographs
the shelves her company stocks, sends the pictures to a WhatsApp group, and moves on.
Somewhere in an office, someone opens each photo, squints at the products, and types what
they see into a spreadsheet. By the time an order goes out, the gap on the shelf is already
two days old. And nobody is watching the chiller in the corner, which warmed up overnight
and is quietly spoiling everything inside it.

That is the situation I built ShelfSense for.

It is a portfolio project, not a product with paying customers, which gave me the freedom
to make one design decision without a business pushing back:

**The AI reads and proposes. A person decides.**

This article is about what that looks like in practice, and why I think the line between
_recommending_ something and _doing_ it is one of the most important parts of building
useful AI systems.

---

## What ShelfSense actually does

Take one shelf on one morning. Ada photographs the dairy chiller at Ikeja Depot Shop from
her phone and taps upload. That is the whole of her job in the system.

ShelfSense compares the photo with the plan for that shelf: a simple list of which product
belongs in which slot. In this example, it finds two empty slots out of six:

- Powdered milk
- Milk sachets

It also reports how sure it is about what it saw. In this case, 82%. That number matters,
because a shaky reading should not quietly turn into a business decision. It should get
another pair of eyes.

Next, ShelfSense checks the stock room records.

The powdered milk is missing from the shelf, but there is still some in the back, so the
system recommends a refill. The evaporated milk is the opposite. The shelf looks fine, but
the back room is empty and the next refill has nothing to draw on. So ShelfSense drafts an
order for two cases, about ₦10,800, and writes a short explanation of why.

This is where the interesting part begins.

**The AI is not allowed to place the order.**

The system has a spending limit: it may approve anything up to ₦5,000 on its own. ₦10,800
is above that limit, so the order does not go anywhere. It enters a review queue, with the
reason it was held, and waits for a manager.

The manager can:

- Approve the order as proposed.
- Change the quantity, then approve it.
- Reject it.

Whatever they decide is recorded with their name and kept as part of the decision history.

The model made the recommendation. The organisation kept the authority.

[IMAGE: review-queue.png]
_The review queue: a technician call-out, an escalation and the milk reorder, each showing
why it was held, with Approve and Reject buttons._

---

## Why "asks before it spends" is the whole design

It would have been easy to let the model place orders. The interesting engineering was
deciding when it must not.

The rules are deliberately simple enough to print on a card:

- Anything costing more than ₦5,000 needs a person's approval.
- Anything based on a photo the model was less than 70% sure about needs a person's review.
- Escalations always go to a person. The model can raise a flag, but it cannot lower one.
- Each role only gets the tools it is allowed to use. A field agent's account cannot
  trigger a reorder, whatever the model recommends.

These rules live outside the model, in ordinary program code. That distinction matters.

You can _ask_ a model to follow a policy. But the policy itself should not depend on the
model behaving perfectly. No cleverly worded request should be able to convince the system
that ₦10,800 is somehow less than ₦5,000.

So the model gets to be good at reading shelves, understanding the situation, and writing
useful recommendations. The application decides what the model is actually allowed to do.

**The model provides intelligence. The application provides authority.**

That principle extends beyond orders.

---

## The review queue is part of the learning loop

The approval queue is not just a safety net. It also creates a feedback loop.

Suppose the model reports four units of a product visible on the shelf when there are
actually five. A reviewer corrects the number. That correction is saved as a worked example
with a known right answer.

So the people doing the ordinary approval work are also, without extra effort, building the
collection of examples that the next version of the system will be tested against.

That is something I like about keeping a human in the loop. The human is not only there to
catch mistakes. The human becomes part of how the system improves.

---

## What the planner does

ShelfSense has two AI steps, not one.

The first step reads the shelf photo. The second step, which I call the planner, takes that
reading, combines it with stock levels and the shelf plan, and works out what should happen
next.

In one demo run, the planner:

- Reviews the shelf findings.
- Looks up the available stock.
- Checks the shelf plan.
- Decides whether any action is needed.
- Proposes one or more actions.
- Explains why it chose them.
- Reports how much the run cost.

The important part is that the planner does not get unlimited freedom. It works through a
fixed set of actions it is allowed to request, and every request passes through the same
rules described above.

It can propose a reorder. It cannot quietly turn that proposal into a completed purchase.

It can recommend sending a technician. It cannot skip the approval queue.

It can raise an escalation. It cannot decide that the escalation is resolved.

[IMAGE: run-planner.png]
_One planner run: the model's summary in plain English, the two actions it requested, the
two proposals it made, and what the run cost ($0.054)._

---

## The fridge

The second half of ShelfSense is the cold chain: keeping chilled goods cold all the way
from the depot to the shelf.

Every fridge and delivery van reports its temperature and location every few seconds. The
rule for trouble is deliberately simple: **a fridge above 8 °C for 15 minutes is a
problem.**

Not one hot reading. Anyone opening a fridge door creates a brief spike. The system is
looking for a sustained fault.

In the demo, the Ikeja chiller drifts up to 11 °C and stays there. The rule fires, an alert
opens, and the same planning step runs.

ShelfSense proposes sending a technician. A call-out costs around ₦15,000, so that action
also waits for approval. At the same time, it drafts a message for the manager:

- Move the chilled stock to a working unit now.
- Check the door seal and the power supply.
- Set aside anything that has been warm for more than four hours.

On the dashboard map, the store turns red.

[IMAGE: dashboard.png]
_The dashboard: two stores, three decisions awaiting review, spend for the last ten runs,
and the Ikeja marker in red._

This mattered to me because it shows the same principle working beyond inventory. The
system can detect. It can reason. It can recommend. But once an action has a real
operational or financial consequence, a person decides.

---

## How I know it works, and how well

"It seems to work" is not a measurement. So ShelfSense has a test set of 100 cases:

- 60 cases for reading shelves.
- 40 cases for planning.

Each case has a known correct answer. The model's responses to those cases were recorded
once and are replayed every time the code changes, so the whole set is re-scored
automatically without paying for fresh model calls. A change that drops the score by more
than two points fails the build and cannot be merged.

The current numbers are honest, and a little unflattering.

**Shelf reading: 100% exact match.** ShelfSense gets every one of the 60 shelf-reading cases
right. That sounds excellent, until you learn how the test shelves were made. They are
computer-rendered: clean labels, even lighting, no glare, no badly angled phone photos. It
is deliberately an easy test. The 100% is useful as a floor, but it is not evidence that the
system is ready for a real shop floor. Real photos will score lower, and the workflow for
adding them to the test set is already built.

**Planning: 67.5% policy match.** On the 40 planning cases, 67.5% of the model's decisions
match the written policy. Most of the misses are judgement calls about when to notify
someone versus when to escalate. There is another wrinkle: six of the 40 cases are unscored
because the API account ran out of credit while the responses were being recorded. That is
its own small lesson. AI systems have budgets, and a test suite that ignores cost is not
describing the whole system.

**Cost: about nine US cents per shelf.** Reading a shelf photo costs about four cents.
Planning the actions that follow costs about five. Every model call is priced from the
exact amount of text it consumed and produced, and that price is stored next to the result.
So the cost page is not a rough estimate. It is a record.

[IMAGE: evals.png]
_The evaluation scoreboard: scores for the shelf-reading and planning steps across the
100-case test set._

---

## What is not done

A portfolio project should also say what it is not.

ShelfSense has not been put on the public internet. The deployment steps are written down,
but they have not been carried out.

The WhatsApp and email messages are drafted and recorded, not sent, because no messaging
service is connected.

The map uses free OpenStreetMap tiles, which are fine for a demo and not for a product.

And the shelf-reading results above come from rendered shelves, not the blurry,
glare-streaked photographs a real morning round produces.

It is tempting to present a clean demo and let the reader fill in the gaps. I would rather
make the gaps visible. The project demonstrates the architecture, the workflow, the testing
approach and the decision controls. It does not yet demonstrate accuracy on a large
collection of real shelf photographs. That is the next problem to solve.

---

## What I learned

Three things I would carry into the next project.

### 1. Ask for a fixed shape, then check it

Instead of asking the model to describe the shelf in free text, ShelfSense asks for a fixed
form: this slot, this product, this many visible, empty or not. The answer is then checked
against the shelf plan. If it contradicts itself, say a slot marked empty that also has a
product count, the error is sent back and the model is asked to fix it. Most readings pass
first time. The rest usually correct themselves in one more attempt.

The lesson: do not treat what a model says as fact. Check it. Ask for a repair when it is
wrong. Reject it when it cannot be repaired.

### 2. A test set changes how you work

Before the test set existed, a change to the model's instructions was mostly a feeling. The
output looked better, or worse, or maybe just different.

After the test set existed, the same change became a number. It went up or it went down.
And sometimes it went down in ways I would never have noticed by looking at a handful of
examples by hand.

Instead of asking "does this seem better?", you can ask "what changed across the whole
set?". That is a small shift, but it changes how you build.

### 3. Keep customers apart at the deepest level

ShelfSense serves several distributors at once, each with their own shops. Every record in
the database is tagged with the customer it belongs to, and the database itself refuses to
hand one customer's data to another, even if the program asking for it has a bug. The
system also refuses to start if it is connected with an account that could bypass that
check.

That one rule removed a whole class of "what if?" questions from the rest of the build.
Instead of hoping every future piece of code remembers to filter correctly, the database
enforces it. It is not exciting in a demo. It becomes essential as a system grows.

---

## The bigger idea

The most interesting thing I built in ShelfSense was not the part that reads photos. It was
the boundary around it.

There is a temptation with AI systems to ask: "What can we let the model do automatically?"
I think there is a better question: "What should the model never be allowed to decide on
its own?"

For ShelfSense, the answer was money, escalations, and anything with a real-world
consequence.

That does not make the system less capable. It gives its autonomy a boundary. The model can
still do a surprising amount:

- Look at a shelf and identify what is missing.
- Reason about stock levels.
- Recognise a fridge that is failing.
- Decide what information it needs and go and get it.
- Propose the next action and explain its reasoning.

But when a decision crosses a line that matters to the business, the system asks.

That is what I wanted to explore with this project. Not whether AI can make the decision,
but whether it knows when it should not be the one making it.

---

## Try it

The code is on GitHub: **github.com/dhean4/shelfsense**

Run `make demo` and the system loads two distributors, three shops, and one misbehaving
fridge. The shelf-reading step replays recorded model responses, so that part runs without
an API key. The planning step calls the model live and needs a key and a few cents of
credit.

The project is intentionally unfinished in a few places, because those unfinished parts are
part of the story. The next useful step is real shelf photographs, especially bad ones:
glare, poor lighting, half-visible products, awkward angles, cluttered shelves. Those are
the cases that would tell me whether the system is ready for the environment it was
designed for.

If you have photographs like that from a real shop floor, I would genuinely like to add
them to the test set.
