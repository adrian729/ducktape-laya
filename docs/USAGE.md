# Using Laya in an application

This page is for deciding whether Laya suits a task, and for getting the best results from it when it does. It describes what the models do on real data, where they fall short, which settings change the results and by how much, and how to measure Laya on your own task before you depend on it. The API itself is in [API.md](API.md); speed and memory are in [PERFORMANCE.md](PERFORMANCE.md).

## Where these numbers come from

One evaluation on real data, run on 2026-10-28 with 0.3.0, the English and typed-decisions models (the multilingual model on the first task) and 4 threads on an Intel Core i7-1165G7. The data come from a desktop application that supervises several coding agents, and stay private:

- **"Is the agent waiting for the user?"**: 240 final messages of coding-agent turns (the last 1,500 characters of each, median 230 tokens), 79 of them labelled as asking the user to reply, decide, approve or act, the others as only reporting. The application needed precision of at least 0.9 at recall of at least 0.8 to replace a rule that flags a `?` in the last 400 characters (precision 0.95, recall 0.51).
- **"How big is this ticket?"**: 33 software tickets (title and description, median 765 tokens) with the tier the project gave them: light, standard or heavy; 36% are standard.
- **Minimal pairs**: 5 pairs of short sentences that differ only in whether they ask the reader for something ("Done. Want me to open a PR?" against "Done. I opened a PR."), and 4 single sentences.

These are small sets from one domain. Read the differences below as directions with rough sizes: on 240 examples an AUC difference under about 0.03 is within noise, and on 33 tickets one ticket is 3 points of accuracy. The way to know for your task is to measure it ([below](#measure-it-on-your-task)).

## Decide whether Laya fits

Laya reads a text and answers a question about it with a probability, without generating text, in about a second per question for a short text on a laptop CPU. On the tasks above it ranked texts far better than chance, but did not beat simple baselines by enough to be used:

| Task | Laya at its best | The baseline it had to beat |
| --- | --- | --- |
| Waiting for the user | AUC 0.84. At precision 0.9, recall at most 0.27 with the threshold picked on the same messages; picked on one half, it reached precision 0.83 at recall 0.25 on the other | `?` rule: precision 0.95, recall 0.51. Text length alone: AUC 0.76 |
| Ticket size | 52% exact (typed `score`, split into thirds by rank); `choice` answered "light" for at most 1 of 33 | Always "standard": 36%. Description length in thirds: 42% |

**Do not use Laya for these.** Each failed on real data, and no phrasing, setting or correction tried here fixed it:

- **Grading size, effort, difficulty or priority from a description.** On the tickets it did no better than description length, `choice` almost never picked the lowest tier, and `score` used less than half of its scale (limitation 6).
- **Replacing a rule, or an exact signal, where you need both high precision and high recall.** A threshold aimed at precision 0.9 reached precision 0.83 and recall 0.25 on held-out data, against the `?` rule's 0.95 and 0.51. If something can report the answer exactly (here, the agent marking that it is waiting), use that instead.
- **Telling apart texts that use the same words with different intent:** a request against a report of the same action, a rhetorical question against a real one, who is asked to act. The difference between such sentences moved the probability by only 0.05 to 0.25, and shuffling a text's words kept most of its signal (limitations 2 and 3).
- **Reading the output as a probability, or cutting at 0.5.** Probabilities are compressed and shifted by question (limitation 4).

**Use it with care** when text length differs between your classes (it follows length: check AUC within length), when a `choice` has options that differ in length or wording style (it can lean to one), and with the multilingual model on English text.

**It can fit** when the answer is stated by the words of the text (a topic, a mention, a request that names what it asks for), when a ranking or a coarse filter is enough (for example ordering items for human review), when a few seconds per text are acceptable, and when you can label one or two hundred examples to choose a threshold. These are the conditions under which it ranked well here, not measured successes: the [example presets](../examples/README.md) (support triage, email triage, prompt-injection screening, moderation) have not been measured on real data either. Measure before relying on it.

## Known limitations

Each of these was measured on the data above. None comes from this library's code: its results match the original Laya's within 0.05 on every fixture ([qualification](QUALIFICATION.md)), and it adds no pooling or length normalisation of its own. They are properties of the models.

1. **Longer texts get higher probabilities.** On the waiting messages, the probability of "waiting" correlated r = +0.53 to +0.64 with the log of the text's length, while length alone ranked the messages with AUC 0.76. Within groups of similar length (quartiles), the best AUC fell from 0.82 to 0.73. Dividing by the answer for an empty state or for a neutral text of the same length (a prior correction) barely reduced the correlation and lowered AUC slightly. For `noul`, much of the pull comes from the option names `true` and `false`: renaming them with [`labels`](API.md#primitive-requests-and-results) (`B`, `A`) cut the correlation from +0.64 to +0.37 (typed) and from +0.55 to +0.11 (English) and raised AUC within length, but lowered overall AUC (0.82 to 0.81 typed, 0.77 to 0.71 English), because in this data longer messages really are more often waiting.
2. **Which words appear counts more than what they say.** With each message's words shuffled into random order, the best question still reached AUC 0.72–0.77, against 0.82 in order. Questions whose answer depends on word order or negation ("I opened a PR" against "Want me to open a PR?") separate weakly.
3. **Requests that are not questions are missed.** Imperatives ("Please run the app and tell me…"), offers and conditionals (in the style of "I can also add the tests, if useful" or "If that works for you, say so and I'll merge") often scored below 0.5, and a question the message answers itself scored as high as a real one. In the minimal pairs, the asking sentence scored only 0.05–0.25 above its twin for most phrasings, and most absolute values stayed below 0.5.
4. **Probabilities are not calibrated for your question.** The temperatures stored in each model were fitted by the model's authors on their own data. On the waiting task, the typed model put almost every message between 0.2 and 0.7, so 0.5 is not a meaningful threshold; the expected calibration error (the mean gap between a probability and how often it is right) was 0.08–0.21 for the better phrasings. A `temperatures` override fitted on half of the examples and measured on the other half (averaged over 10 splits) lowered it in three of four cases, for example from 0.21 to 0.14, but a temperature rescales probabilities around 0.5 and cannot move their centre; fitting an offset as well (`sigmoid(a·logit(p) + b)`, applied by the application) reached 0.10 for the typed model and did not help the English one. Neither changes the ranking, so neither changes AUC or the precision a threshold reaches. Choose thresholds on your own labelled examples, and calibrate in the application only if it needs to read the number as a probability.
5. **Option order changes `choice` answers.** Reversing the two options of a yes/no `choice` moved the probability by 0.06 (typed) to 0.10 (English) on average and flipped 15% of the answers; reversing three ticket-size options flipped 9%.
6. **`choice` can favour one option whatever the text.** On the tickets, both models answered "heavy" for 27 to 31 of 33 tickets and "light" for at most 1, with option descriptions of equal length, with neutral keys (A, B, C) and in reversed order. Prior corrections only moved the lean (one answered "light" for 23 of 33) without raising exact agreement above 39%. `score` stayed in a narrow band (expected levels 1.05 to 1.87 on a 0–2 scale) and correlated with the tier index at r ≤ 0.18.
7. **A bare `noul` leans to "no".** Without criteria, the English model put 113 of 240 messages below 0.1, a fifth of them asking messages; the original Laya reports the same ([upstream issue 156](https://github.com/NandhaKishorM/laya/issues/156)). Always give `noul` its `true` and `false` criteria, or ask a two-option `choice`.
8. **Each question re-reads the whole state.** The models are bidirectional: the question is written before the state, and every token's encoding depends on both, so nothing can be shared between questions. Time grows linearly with the number of questions and with the number of tokens of question plus state: about 7 ms per token per question on the machine above (0.19 s for a 30-token prompt, 1.6 s for 230 tokens, 8.9 s for 850). Packing several questions into one pass was measured at 1.15× faster and changed the results ([roadmap](ROADMAP.md#research-questions)).

## Get the best results

Measured effects on the waiting task (typed-decisions model unless stated; AUC over 240 messages, "within length" is the mean AUC within length quartiles):

| Question | AUC | AUC within length | Minimal pairs: mean margin, reversed pairs |
| --- | --- | --- | --- |
| `noul` with short criteria (the starting point) | 0.80 | 0.67 | +0.11, 0 of 5 |
| `choice` with keys `question`/`report`, short descriptions | 0.78 | 0.76 | +0.09, 2 of 5 |
| `choice` with keys `A`/`B`, the same short descriptions | 0.75 | 0.72 | +0.13, 0 of 5 |
| `noul` phrased as a statement, detailed `true`/`false` criteria | 0.82 | 0.71 | +0.06, 1 of 5 |
| `choice` with keys `question`/`report`, detailed descriptions | **0.84** | 0.74 | +0.16, 1 of 5 |
| `choice` with keys `A`/`B`, detailed descriptions | 0.82 | 0.73 | **+0.22, 0 of 5** |
| the same, both option orders averaged | 0.82 | 0.72 | +0.22, 0 of 5 |
| `noul` statement, detailed criteria, `labels` `B`/`A` | 0.81 | 0.73 | +0.13, 1 of 5 |
| `choice` with keys `A`/`B`, detailed descriptions, last 400 characters only | **0.84** | **0.77** | |

- **Describe every option in detail, including the forms you care about.** The detailed descriptions named offers ("want me to…?") and requests ("tell me when…") explicitly. With either kind of key they raised AUC by 0.06 to 0.07; they also strengthened the pull towards long texts (r from +0.38 to +0.58 with `question`/`report`), so check AUC within length too.
- **For `noul`, try `labels`** (`{"false": "B", "true": "A"}`) when the text's length should not decide the answer. On the waiting messages it nearly removed the length bias for the English model and halved it for the typed one, and widened the minimal-pair margins (from +0.06 to +0.13 for the typed model), at the cost of the AUC that length itself contributed in this data. Measure both forms on your examples.
- **Key names matter less than descriptions.** Neutral keys (`A`, `B`) did not raise AUC on the real messages (0.82 against 0.84 with detailed descriptions) but answered the minimal pairs more consistently (no pair reversed). Prefer neutral keys when the key words themselves could suggest an answer, as `true`/`false` do for `noul` (limitation 1); otherwise descriptive keys are fine. For a yes/no task, a two-option `choice` gives the probability of the positive key.
- **Check both option orders.** Averaging the two orders (in logit space) removed the order dependence without changing AUC; it doubles the cost. The [option order](API.md#option-order) section shows how to ask both in one request.
- **Choose the threshold on labelled data, never 0.5.** For a target precision, pick the lowest threshold that reaches it on half of your examples and check it on the other half; the [evaluation tool](#measure-it-on-your-task) does this.
- **Combine with a rule rather than replacing it.** Flagging a message when the `?` rule fires or the probability passes a threshold chosen for precision 0.9 gave precision 0.88–0.90 at recall 0.58–0.59 on held-out halves, against the rule's 0.95 and 0.51: a little more recall for a little less precision.
- **Send only the text that carries the answer.** For the neutral-key `choice`, the last 400 characters of each message (median 97 tokens) did better than the last 1,500 (median 230): AUC 0.84 against 0.82, AUC within length 0.77 against 0.73, and a weaker pull towards long texts (r = +0.36 against +0.53), at about half the time. The last 200 characters were too few (AUC 0.78). Where the answer sits in a known part of the text, try a few cut-offs with the evaluation tool.
- **Model:** the English and typed-decisions models gave similar results; neither was consistently better. The multilingual model was much weaker on these English messages (AUC 0.57–0.68 with a 512-token context, at which 17 of the 240 were split into windows; the neutral-key `choice` flipping 13% of answers with the options reversed); use it for text in other languages, and route English text to an English model ([routing](API.md#choosing-the-model-by-language)).
- **What did not help:** prior corrections (dividing by the answer for an empty or same-length neutral state), and a 1,024-token context for the English model (AUC 0.772 against 0.773 at 512; only 12 of the 240 prompts were longer than 512 tokens). A fitted temperature improves calibration but not ranking (limitation 4).

## Measure it on your task

Label one or two hundred real examples first (more if positives are rare), then run [`scripts/evaluate-questions.py`](../scripts/evaluate-questions.py) from this repository. It is a single file that needs only the Python package (`pip install ducktape-laya`):

```sh
python3 evaluate-questions.py MODEL.gguf examples.jsonl questions.json --controls order,shuffle --json report.json
```

`examples.jsonl` holds one `{"state": ..., "label": ...}` per line, and optionally `"rule": true/false` with your current rule's verdict. Labels that are `true`/`false` make a yes/no evaluation; option keys make a `choice` evaluation; numbers an ordinal one. `questions.json` maps names to questions in the request format, so several phrasings run side by side; a `choice` used for a yes/no task names its positive option with `"positive": "A"`. The tool reports, for each question:

- AUC, and AUC within length quartiles next to length alone: how much of the signal is more than length.
- Precision and recall at your target precision (`--precision`, default 0.9) and at the best F1, with thresholds chosen on one half of the examples and measured on the other, and the same for your rule OR the score.
- With `--controls order`: the AUC and flip rate with each `choice`'s options reversed. With `shuffle`: the AUC with the state's words shuffled; close to the real AUC means the answer comes from which words appear.

Run it on the hardware and model you will ship: results differ slightly between [numerical profiles](PLATFORMS.md#numerical-results-across-platforms).

Checklist before relying on a question:

1. The score beats the baselines you would otherwise use (a rule, length, the most common label) at the operating point you need, on held-out examples.
2. AUC within length quartiles is well above 0.5, and the shuffled-words AUC is clearly lower than the real one.
3. The `choice` answers do not flip often when the options are reversed, and every option you need is actually predicted.
4. The threshold comes from your data, and you have a plan to re-check it when the model, the platform or the text changes.
5. The time per text, at your text lengths and number of questions, fits your budget.

## Cost

- **Time** is proportional to (question tokens + state tokens) × questions; see limitation 8 for measured values, and [PERFORMANCE.md](PERFORMANCE.md) for whole requests. Long criteria cost time on every text: keep descriptions as long as they need to be to help, not longer.
- **Threads:** the default (at most 4) was fastest. On 4 cores with 8 hardware threads, 8 threads were at most 4% faster than 4 (one 850-token case) and up to 1.9 times slower on short prompts.
- **Long input:** above the context, `overflow: "scan"` runs every window, multiplying the time; for a question about the end of a text, send the end.
- **Memory:** about 0.6 GiB per open engine with the English or typed-decisions model.
