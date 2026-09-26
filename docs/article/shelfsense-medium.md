<!--
Medium draft. Paste the body into the Medium editor; upload the images from docs/images by
hand where the [image: ...] markers are (Medium does not fetch relative paths).

Title options:
  1. The ₦10,800 order the AI was not allowed to place
  2. What a shelf photo can tell you, and what it should not decide
  3. I built an AI assistant for a Lagos distributor. It asks before it spends.

Subtitle: A portfolio project about putting a person between a vision model and the money.
Tags: Artificial Intelligence, Retail, Nigeria, Software Engineering, Claude
Canonical URL: (leave blank on first publish)

Images, in order:
  ../images/shelf-dairy-two-stockouts.png  alt: A rendered dairy shelf, six slots, two empty
  ../images/review-queue.png               alt: The review queue holding three proposed actions
  ../images/run-planner.png                alt: One planner run: summary, tool calls, actions, cost
  ../images/dashboard.png                  alt: The dashboard with the Ikeja store marked red
  ../images/evals.png                      alt: The eval scoreboard
-->

# The ₦10,800 order the AI was not allowed to place

_A portfolio project about putting a person between a vision model and the money._

Ada runs the morning round for a small distributor in Lagos. At each shop she photographs
the shelves her company stocks, sends the pictures to a WhatsApp group, and moves on.
Somewhere in an office, someone opens each photo, squints, and types what they see into a
spreadsheet. By the time an order goes out, the gap on the shelf is two days old. And
nobody is watching the chiller in the corner, which warmed up overnight and is quietly
spoiling everything inside it.

That is the situation I built ShelfSense for. It is a portfolio project, not a product with
customers, so I could make one design decision without a business pushing back: the AI
reads and proposes, and a person decides. This article is about what that looks like in
practice.

[image: ../images/shelf-dairy-two-stockouts.png — a rendered dairy shelf, six slots, two
of them empty. This is a computer-generated test shelf; more on that below.]

## What the system actually does

Take one shelf on one morning. Ada photographs the dairy chiller at Ikeja Depot Shop from
her phone and taps upload. That is the whole of her job in the system.

ShelfSense compares the photo with the plan for that shelf, the list of which product
belongs in which slot. It finds two empty slots out of six: powdered milk and milk sachets.
It also says how sure it is, 82 % in this case, because a shaky reading should get a second
pair of eyes.

Then it looks at the stock room. The powdered milk is only missing from the shelf, there
is more in the back, so it asks for a refill. The evaporated milk is the opposite: the shelf
looks fine, but the back room is empty and the next refill has no cover. So it drafts an
order for two cases, about ₦10,800, and writes a one-paragraph note explaining why.

Here is the part I care about. ₦10,800 is above the ₦5,000 that the system may approve on
its own. So the order does not go anywhere. It appears in a queue, with the reason it was
held, and waits for a manager. The manager can approve it, change the quantity first, or
reject it. Whatever they choose is stamped with their name and kept.

[image: ../images/review-queue.png — the review queue holding a technician dispatch, an
escalation and the milk reorder, each with the reason it was held.]

## Why "asks before it spends" is the whole design

It would have been easy to let the model place orders. The interesting engineering was in
deciding when it may not.

The rules are plain enough to print on a card:

- Anything that costs more than ₦5,000 waits for a person.
- Anything based on a photo the model was less than 70 % sure about waits for a person.
- Escalations always go to a person. The model can raise a flag; it cannot close one.
- Each role sees only the tools it is allowed to use. A field agent's account cannot
  trigger a reorder, whatever the model suggests.

These rules live outside the model, in ordinary code, so a clever prompt cannot talk its
way past them. The model gets to be good at reading shelves and writing clear
recommendations. The organisation keeps the authority.

The queue does one more thing. When a reviewer corrects a reading, say the model counted
four facings and there were five, that correction becomes a labelled example. Over time,
the people doing the boring approval work are also building the test set that the next
version of the model will be graded against.

[image: ../images/run-planner.png — one planner run: the model's summary, the two tool
calls it made, the two actions it proposed, and what the run cost.]

## The fridge

The second half of the project is the cold chain. Every fridge and delivery van reports its
temperature and position every few seconds. The rule for trouble is deliberately simple: a
fridge above 8 °C for 15 minutes is an excursion. Not one hot reading, which happens every
time someone opens the door, but a quarter of an hour of it.

When the rule fires, an alert opens and the same planning step runs. In the demo, the Ikeja
chiller drifts up to 11 °C. The system proposes sending a technician, which is held for
approval because a call-out costs about ₦15,000, and drafts a message to the manager: move
the chilled stock to a working unit now, check the door seal and the power, and set aside
anything that has been warm for more than four hours. On the dashboard map, the store turns
red.

[image: ../images/dashboard.png — the dashboard: two stores, three decisions awaiting
review, spend for the last ten runs, and the Ikeja marker in red.]

## How I know it works, and how well

"It seems to work" is not a number, so the project has a test set: 100 cases, 60 for
reading shelves and 40 for planning, each with a known right answer. The model's responses
to those cases are recorded once and replayed on every code change, which means the
scoring runs in continuous integration for free. A change that drops a score by more than
two points fails the build.

The current numbers are honest and a little unflattering:

- Reading shelves: 100 % exact match on the 60 cases. That sounds wonderful until you learn
  that the shelves are computer-rendered, with clean labels and even lighting. It is a
  deliberately easy test. Real photos will score lower, and the workflow for adding them is
  ready.
- Planning: 67.5 % of decisions match the written policy on the 40 cases. Most of the misses
  are judgement calls about when to notify someone versus when to escalate. Six of the 40
  cases are unscored because the API account ran out of credit halfway through recording,
  which is its own small lesson about budgets.
- Cost: about four US cents to read a shelf and about five cents to plan the actions for it.
  Every model call is priced from its token usage and stored next to its result, so the
  costs page is not an estimate.

[image: ../images/evals.png — the eval scoreboard: per-metric scores for the vision and
planner agents on the 100-case test set.]

## What is not done

A portfolio project should say what it is not. ShelfSense has not been put on the public
internet; the deployment steps are written down and unexecuted. The WhatsApp and email
messages are recorded rather than sent, because no messaging provider is connected. The map
uses free OpenStreetMap tiles. And the vision numbers above are on rendered shelves, not on
the blurry, glare-streaked photos a real morning round produces.

## What I learned

Three things I would carry into the next project.

Structured output with a repair loop beats asking nicely for JSON. The model is asked for a
fixed shape, the result is checked against the shelf plan, and any contradiction (a slot
marked empty that also has a count) is sent back with the error. Most readings pass first
time; the rest fix themselves in one more turn.

An eval changes how you work. Before the test set existed, a prompt change was a feeling.
After it, a prompt change was a number that went up or down, and a few went down in ways I
would never have noticed by eye.

Tenancy belongs in the database. Every table carries a customer id and the database itself
refuses to return another customer's rows, even to buggy code. The API will not start if it
is connected with an account that could bypass that check. That one rule removed a whole
class of "what if" from the rest of the build.

## Try it

The code is at github.com/dhean4/shelfsense. `make demo` loads two distributors, three
shops and one misbehaving fridge, and the recorded model responses mean you can watch the
whole day above without an API key. If you have real shelf photos from a shop floor,
especially bad ones, I would like to add them to the test set.
