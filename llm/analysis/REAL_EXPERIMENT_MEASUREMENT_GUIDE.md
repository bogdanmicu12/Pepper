# Real Experiment Measurement Guide

This guide assumes the final measurement setup uses:

- response latency
- participant speaking time
- elicitation-window engagement rating, scored by the researcher from 1-100
- start/end creative-confidence rating, scored by the researcher from 1-100
- vocal activation score
- connection-cue score
- manually coded substantive contribution measures

## 1. Before The First Session

From the project root, check that the analysis script can run:

```powershell
python .\llm\analysis\generate_elicitation_measurement_graphs.py --help
```

Optional but recommended before collecting real data: move or rename any old
test logs so the real dataset starts clean.

```powershell
New-Item -ItemType Directory -Force .\llm\logs\archive
Move-Item .\llm\logs\transcript.csv .\llm\logs\archive\transcript_old.csv -ErrorAction SilentlyContinue
Move-Item .\llm\logs\logs.csv .\llm\logs\archive\logs_old.csv -ErrorAction SilentlyContinue
```

Check microphone device detection if needed:

```powershell
python .\llm\infra\lmstudio_minimal_bridge.py --list-audio-devices
```

Make sure:

- Participant 1 is on Focusrite input 1.
- Participant 2 is on Focusrite input 2.
- Pepper is reachable at the configured IP.
- LM Studio is running with the model loaded.
- Deepgram API access works.

## 2. Run One Experiment Session

Use the group and theme IDs for the current session. Example for group `G01`,
theme `T1`:

```powershell
python .\llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --group-id G01 --theme-id T1 --phase divergence --elicitation-mode scheduled --intervention-every 4 --evaluation_elicitation --initiative proactive
```

What happens during the session:

- The microphones stay live.
- Participant turns are transcribed into `llm\logs\transcript.csv`.
- Pepper robot turns are also logged into the same file.
- Scheduled elicitation prompts are selected from the prompt bank and
  counterbalancing file.
- Normal/context robot replies are kept inside the previous elicitation window.

Useful live controls:

- `ROBOT`: trigger Pepper manually. In reactive microphone mode, `Pepper` and
  the transcript misrecognition `Paper` both trigger Pepper.
- `CHANGE`: switch between divergence and convergence.
- `DIVERGENCE`: force divergence phase.
- `CONVERGENCE`: force convergence phase.
- `ELICITATION off|scheduled|perspective_shift|generative|elaboration_evidence`: change elicitation behavior.
- `exit`: stop the session.

## 3. Enter The 1-100 Evaluation Ratings

When `--evaluation_elicitation` is enabled, the console asks for engagement
at the start of the conversation before the first elicitation strategy. It also
asks:

```text
How confident are you in your creative abilities?
```

at the start and end of the session, using the same 1-100 scale.

Important timing detail:

- The start engagement score is a baseline taken before the first elicitation
  strategy.
- The score you enter before elicitation prompt B belongs to the window after
  elicitation prompt A.
- When you type `exit`, the console asks once more for the final open
  elicitation window and the end creative-confidence score.

Use the same judgement rule every time, for example:

```text
1 = no engagement / dead discussion
50 = moderate engagement / some response but not much energy
100 = very engaged / quick, energetic, socially connected discussion
```

The score is saved in `llm\logs\transcript.csv` as
`elicitation_engagement_score`. Creative confidence is saved as
`creative_confidence_score`, with `evaluation_moment` set to `start` or `end`.
For mid-session engagement scores, the row also includes
`previous_elicitation_prompt_id`, so the analysis maps the score back to the
correct previous window.

## 4. After Each Session

Do a quick sanity check:

```powershell
Import-Csv .\llm\logs\transcript.csv | Group-Object session_id | Select-Object Name,Count
```

You should see the new session ID. If the session had both phases, check that
there are robot rows with `phase` equal to both `divergence` and `convergence`.

Do not manually edit the raw transcript unless you are making a clearly
documented correction. If Deepgram produced weird text, leave it in the raw log;
that is part of the speech-to-text artifact.

## 5. Build Elicitation Windows First

After all sessions are collected, run a first pass with only transcript and
intervention data. In live mode, the same `transcript.csv` can be used for both:

```powershell
python .\llm\analysis\generate_elicitation_measurement_graphs.py `
  --transcript .\llm\logs\transcript.csv `
  --interventions .\llm\logs\transcript.csv `
  --output-dir .\llm\analysis\outputs\window_check
```

Open:

```text
llm\analysis\outputs\window_check\elicitation_windows.csv
```

Check:

- Each session has the expected number of elicitation windows.
- Window starts are after Pepper finishes an elicitation prompt.
- Window ends are before the next elicitation prompt starts.
- Phase switches do not accidentally create very long windows.

If a phase transition or unusual interruption should end a window earlier, add
an explicit `window_end_time` in a separate intervention log CSV, then use that
CSV as `--interventions`.

## 6. Fill The Manual Coding File

Create:

```text
llm\analysis\data\manual_window_measures.csv
```

Use exactly these columns:

```csv
window_id,session_id,prompt_id,phase,strategy,idea_fluency,elaboration_units,elaborated_contribution_count,consecutive_topic_turns,coder_id,notes
```

Use the `window_id` values from `elicitation_windows.csv`.

Manual coding rules:

- `idea_fluency`: count distinct, non-redundant, task-relevant ideas in the
  window.
- `elaboration_units`: count concrete reasons, examples, mechanisms,
  implementation details, constraints, consequences, evidence checks, or
  clarifications.
- `elaborated_contribution_count`: count how many ideas/contributions contain
  at least one elaboration unit.
- `consecutive_topic_turns`: longest same-topic participant turn chain in the
  window.
- `coder_id`: your coder label.
- `notes`: optional short note for ambiguity.

Best practice: double-code a subset of windows if possible, then reconcile
coding rules before coding the full dataset.

## 7. Run The Final Analysis

```powershell
python .\llm\analysis\generate_elicitation_measurement_graphs.py `
  --transcript .\llm\logs\transcript.csv `
  --interventions .\llm\logs\transcript.csv `
  --manual-measures .\llm\analysis\data\manual_window_measures.csv `
  --output-dir .\llm\analysis\outputs\final_results
```

Main output files:

- `elicitation_windows.csv`
- `elicitation_engagement_summary_by_phase_strategy.csv`
- `session_evaluation_scores.csv`
- `session_evaluation_summary.csv`
- `creative_confidence_change_by_session.csv`
- `transcript_window_metrics.csv`
- `transcript_summary_by_phase_strategy.csv`
- `manual_window_measures_cleaned.csv`
- `manual_summary_by_phase_strategy.csv`
- `pepper_measurement_dashboard.png`
- individual charts in `charts\`

## 8. Metric Intuition And Exact Formulas

All automated metrics are computed per elicitation window.

### Response Latency

Intuition: how quickly participants start responding after Pepper finishes the
elicitation prompt.

Formula:

```text
response_delay_seconds =
first participant turn start time in window - window_start_s
```

The script clips this at 0 if timestamps overlap.

### Participant Speaking Time

Intuition: how much participant speech the prompt sustained before the next
elicitation prompt.

Formula:

```text
participant_speaking_time_seconds =
sum of participant turn durations clipped to the window boundaries
```

### Vocal Activation Score

Intuition: whether the group sounds vocally energetic. It is not a validated
psychological scale; it is a transparent behavioral proxy for vocal arousal.

The script obtains:

- `mean_audio_rms`: average `audio_rms` over participant turns in the window.
- `participant_words_per_second`: participant word count divided by participant
  speaking time.
- `long_pause_seconds_per_minute`: seconds of long silence per window minute.

Long-pause rule:

```text
For each gap after the robot prompt or between participant turns:
  if gap > 3 seconds, add gap - 3 to long_pause_seconds.
```

Then:

```text
log_rms = log(1 + mean_audio_rms)
z_rms = z-score(log_rms across all windows)
z_rate = z-score(participant_words_per_second across all windows)
z_pause = z-score(long_pause_seconds_per_minute across all windows)

activation_raw = (z_rms + z_rate - z_pause) / 3

vocal_activation_score =
clip(50 + 15 * activation_raw, 0, 100)
```

Higher score means louder/more active speech, faster participant speech rate,
and fewer long pauses compared with the rest of the dataset.

### Connection-Cue Score

Intuition: whether the group socially takes up the conversation. It captures
small conversational signs of being “with” the interaction, not idea quality.

The script obtains:

- `adjacency_response_success`: 1 if participants respond within 8 seconds,
  otherwise 0.
- `backchannel_count`: short participant turns of 1-5 words containing a cue
  such as `yeah`, `okay`, `right`, `exactly`, `true`, `nice`, `cool`, or `ja`.
- `laughter_count`: transcript cues such as `haha`, `hehe`, `lol`, `laugh`,
  or `laughing`.
- `cooperative_overlap_count`: a participant starts speaking before the
  previous participant turn has ended, and the speaker is different.
- `connection_cues_per_minute`: adjacency success + backchannels + laughter +
  cooperative overlaps, divided by window duration in minutes.

Intermediate rates:

```text
speaking_minutes = participant_speaking_time_seconds / 60
backchannels_per_speaking_minute = backchannel_count / speaking_minutes
laughter_per_speaking_minute = laughter_count / speaking_minutes
```

Final formula:

```text
connection_cue_score =
clip(
  25 * adjacency_response_success
  + 30 * min(backchannels_per_speaking_minute / 2, 1)
  + 20 * min(laughter_per_speaking_minute / 1, 1)
  + 15 * min(cooperative_overlap_count / 2, 1)
  + 10 * min(connection_cues_per_minute / 3, 1),
  0,
  100
)
```

Higher score means quick uptake, more short acknowledgements, more laughter,
and more cooperative conversational overlap.

## 9. Final Sanity Checks

After the final analysis, check:

```powershell
Import-Csv .\llm\analysis\outputs\final_results\elicitation_windows.csv | Measure-Object
Import-Csv .\llm\analysis\outputs\final_results\transcript_window_metrics.csv | Measure-Object
Import-Csv .\llm\analysis\outputs\final_results\manual_window_measures_cleaned.csv | Measure-Object
```

The row counts should match once manual coding is complete: one row per
elicitation window.

Also open:

```text
llm\analysis\outputs\final_results\pepper_measurement_dashboard.png
```

Use the dashboard for a quick visual check, and use the CSV summaries for exact
values in the report.
