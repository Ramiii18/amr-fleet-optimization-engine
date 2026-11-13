# Experiment lab: design proposal (phase 2b)

**Status: a proposal. Nothing in this document is built.** It follows phase 2a
([plan](DIGITAL_TWIN_PLAN.md)) and needs the owner's approval before any implementation.

The lab compares the existing dispatch policies across scenarios and seeds. Its purpose is to
support statements such as "policy A completes orders faster than policy B under these conditions",
with evidence that a critical reader accepts. A dashboard that shows two runs side by side is not
that evidence: one run is one draw from a distribution.

## 1. What "the same seed" guarantees today

Paired comparisons rely on both policies facing the same conditions. Reading the code and measuring
a few runs gives a precise answer.

| Input | Where it is drawn | The same for every policy? |
|---|---|---|
| Orders (release time, pickup, drop-off) | Before the run, from the scenario seed | **Yes** |
| Robot start positions | Before the run | **Yes** |
| Battery start levels | Before the run, from their own stream | **Yes** |
| Delays | During the run: each robot's own stream, one draw per corridor entry | **Only the numbers, not where they land** |
| Breakdowns | During the run: each robot's own stream, one draw per corridor entry | **Only the numbers, not where they land** |

The k-th draw of a robot's stream applies to that robot's k-th corridor entry. Two policies route a
robot differently, so the same draw lands on a different corridor at a different time, and a robot
that drives more entries consumes more draws.

**Measured** (large map, 10 robots, 200 orders/h for 30 minutes, 2% of entries break down for 30 to
180 s, replanning; greedy against `insertion-fair`; seeds 0 to 4):

| Seed | Orders identical | Breakdowns, greedy / insertion-fair | Same repair times per robot, in order | On the same robot and corridor | ... and within 60 s |
|---:|---|---:|---|---:|---:|
| 0 | yes | 96 / 78 | yes | 22 | 19 |
| 1 | yes | 87 / 86 | yes | 13 | 10 |
| 2 | yes | 73 / 64 | yes | 15 | 14 |
| 3 | yes | 108 / 96 | yes | 12 | 9 |
| 4 | yes | 120 / 118 | yes | 21 | 18 |

So the pairing is exact for demand and for start conditions, and weak for disturbances: 10% to 20%
of the breakdowns are shared in any physical sense, and even their number differs.

**What this means for the published results.** They remain valid as comparisons under the same
disturbance *distribution*, and the benchmark text already says that policies face the same
breakdowns only as far as their paths agree. What cannot be said is "both policies faced the same
breakdowns". The paired intervals are correct, but pairing removes less noise than it could.

## 2. A disturbance model that is the same for every policy

**Principle.** Whatever is exogenous is fixed before the run and does not depend on what a policy
does. It is data, not a side effect of execution.

### 2.1 The first idea, and its weaknesses

Draw a disturbance from a hash of (seed, corridor, time window): any robot that enters that
corridor in that window meets the same disturbance under every policy. The owner asked for a closer
look at four points.

**Window boundaries.** With windows of length W on a fixed grid, an entry at tick W - 1 and one at
tick W get unrelated draws, while entries at ticks 0 and W - 1 get the same one.
- The chance that any one entry is disturbed is unchanged, so no policy is favoured on average.
- The *pairing* is discontinuous: a policy that arrives one tick later across a boundary meets a
  different disturbance, and one that arrives W - 1 ticks later within a window meets the same.
- On a grid shared by all corridors, every corridor redraws at the same tick. That is an artificial
  rhythm in the scenario.
- W is not a tuning knob: it is how long a cause of delay persists. W of one tick is the current
  independent model; a very large W makes a corridor either always slow or never slow for a run.

**Several robots in one window.** They all get the same draw: all disturbed or none.
- The number of disturbances per run then varies more than under independent draws, with the same
  mean. If k robots enter in a window, their outcomes are perfectly correlated.
- A policy that concentrates traffic on few corridors meets more correlated disturbances than one
  that spreads it. That is a real property of place-based causes, not an artefact, but it is a
  property of *this* model and has to be stated with every result.
- It is wrong for breakdowns. A breakdown is a property of the robot, not of the place: two robots
  do not break down because they used the same corridor in the same minute.

### 2.2 The proposed model: a disturbance schedule, generated before the run

A schedule of **episodes**, generated from the seed and the map alone, stored with the scenario:

| Cause | Keyed by | An episode is | Effect |
|---|---|---|---|
| Delay | a corridor and a time interval | "this corridor is slow from tick s to tick e" | A robot that enters the corridor during the episode takes extra time |
| Breakdown | a robot and a time | "this robot's next corridor entry at or after tick s breaks down" | That entry includes a repair |

- **No grid.** Episodes start at random ticks, drawn per corridor (or per robot) as a renewal
  process: the time to the next episode is drawn, then its length. There is no shared boundary.
- **Calibrated to the current model.** The share of time a corridor spends in an episode is set so
  that a robot entering at a random time is delayed with the configured probability. The current
  independent model stays available: published results still reproduce, and it serves as a
  sensitivity analysis.
- **Magnitudes per entrant.** Whether an entry is delayed is decided by the episode. How long is
  drawn from a hash of (seed, corridor, episode, robot), so the same robot in the same episode gets
  the same delay under every policy, and two robots in one episode get different ones.
- **Breakdowns stay with the robot.** A policy that drives a robot less does not escape its
  breakdown; it meets it on a different corridor. The breakdown times are identical across policies.
- **What stays endogenous, on purpose.** Which episodes a robot meets depends on the policy. That is
  the effect being measured, not noise.

**Remaining weaknesses, stated up front.**
- It is a different stochastic model from the published one. Results under it are not comparable
  with the published tables, and both must be labelled.
- Episode length and rate are modelling choices without real data behind them. They are synthetic,
  like the battery parameters.
- Correlation between robots in one delay episode remains (section 2.1). It is reported, not hidden:
  see the pairing diagnostics below.
- Policies do not see the schedule. Delays stay unannounced, breakdowns announced when they happen,
  as today.

### 2.3 Inspecting, exporting and reproducing disturbances

- **A value, not a process.** `generate_disturbances(scenario, spec, seed)` returns an immutable
  schedule: a list of episodes. It can be printed, counted and plotted before any run.
- **In the scenario file.** An optional section holds the schedule, so a hand-written scenario can
  carry hand-written disturbances, and a published experiment ships exactly what it used.
- **In the trace.** A later trace version records the schedule and, for every realised `delay` and
  `breakdown` event (phase 2a), the episode it came from. The viewer can then show episodes on the
  map and the timeline, including those no robot met.
- **A fingerprint.** A hash of orders, start levels and schedule is written into every result. Two
  runs are comparable exactly when their fingerprints are equal, and a test asserts that the
  fingerprint does not depend on the policy.
- **Reconciliation tests**, in the style of phase 2a: every realised event points to an episode;
  every episode that a robot entered produced an event; no event exists without one.

## 3. Making paired comparisons statistically meaningful

**The unit of replication is the seed,** not the order and not the robot. Orders within one run
share robots and congestion, so they are not independent observations.

| Question | Method |
|---|---|
| Is A better than B on KPI x? | Paired differences over seeds, d = x(A) - x(B), with a confidence interval for the mean |
| Which interval? | Student t for roughly symmetric differences; a bootstrap over seeds for skewed KPIs (maximum, P95); a signed-rank test and the count of wins as a robustness check |
| Tail KPIs pooled over orders | A hierarchical bootstrap that resamples seeds, never single orders |
| "No difference" | Only when the interval lies inside a stated margin (for example 5 s). A non-significant test is not evidence of equality |
| Several policies and KPIs | One primary KPI and hypothesis fixed in the experiment file before the runs; a correction for multiple comparisons (Holm) across the primary tests; everything else labelled exploratory |
| How many seeds? | A pilot estimates the spread of the paired differences; the number of seeds follows from the interval width wanted. Adding seeds until a result looks good is not allowed without saying so |
| Did pairing help? | Report the correlation of x(A) and x(B) over seeds, and the variance of the paired difference against the sum of the two variances. This measures the disturbance model of section 2 directly |

**Fairness controls.**
- The same scenario, schedule and model settings per seed, checked by the fingerprint.
- Solver limits that bound work, not wall time, so results do not depend on the machine; every limit
  that was hit is counted and reported (already done for CP-SAT).
- Every policy sees the same information at the same time.
- Wall-clock decision latency is reported separately and never mixed with simulated time.

**KPI definitions that survive scrutiny.**
- **Undelivered orders.** A mean over delivered orders flatters a policy that starves some. Runs are
  either run until every order is delivered (the current default, with the worst case reported), or
  cut at a fixed horizon with the delivered share reported next to every time KPI.
- **Throughput** over the order window, not over the run's length, which differs between policies.
- **Warm-up.** A start-of-shift run begins with an empty system. Either the first minutes are
  excluded, or the result is labelled as a start-of-shift result.
- **Scope of a conclusion.** Seeds replicate one scenario family (one map, one load). A claim holds
  for that family. Several families are analysed as blocks, each with its own result, before any
  overall statement.

## 4. Shape of the lab

- **An experiment file** (strict JSON, like scenario files): scenario families, policies, seeds,
  disturbance model and its parameters, primary KPI, margin, analysis settings.
- **A runner** on the existing job service: runs in worker processes, per-run results as rows with
  the fingerprint and the commit.
- **An analysis module** built on `metrics/compare.py`: the tables above, as data.
- **The viewer** shows a results file: differences with intervals and the number of seeds, the
  pairing diagnostics, and a link from any row to the replay of that run. A single replay is always
  labelled as an example.
- **Export:** the experiment file, the results and the schedules, so that anyone can rerun it.

## 5. Suggested order, when approved

1. The disturbance schedule as a value, with its tests, next to the current model (no result changes).
2. The fingerprint, and the pairing diagnostics on existing benchmarks: how much does pairing gain
   under each model? This is itself a measurable result.
3. The experiment file and runner.
4. The analysis module and its presentation in the viewer.

## 6. Open questions for the owner

1. Should delay episodes affect every robot that enters (place-based, proposed), or should delays
   stay independent per entry and only breakdowns move to the schedule?
2. Which primary KPI: mean completion time, or the worst case that the fairness work targets?
3. Is a fixed horizon with censoring wanted, or do runs keep ending when every order is delivered?
