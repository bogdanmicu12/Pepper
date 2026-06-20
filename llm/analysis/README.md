# Pepper Elicitation Analysis

This folder contains the analysis script for turning elicitation transcripts,
robot intervention logs, and manually coded measures into CSV summaries and PNG
graphs.

## Files

- `generate_elicitation_measurement_graphs.py`: main analysis script.
- `REAL_EXPERIMENT_MEASUREMENT_GUIDE.md`: step-by-step guide for collecting
  real data and generating the final charts.
- `README.md`: this usage guide.

The script writes generated outputs to a folder you choose with `--output-dir`.
Keep raw data, manually coded CSVs, and generated outputs separate so it is clear
which files are source data and which files are produced by the script.

## Analysis Unit

The unit of analysis is one elicitation window.

An elicitation window starts when Pepper finishes an elicitation-strategy prompt.
It ends when the next elicitation-strategy prompt starts. Normal/context prompts
between two elicitation strategies remain inside the earlier strategy's window.

Example:

```text
Elicitation strategy A
normal prompt
normal prompt
normal prompt
normal prompt
Elicitation strategy B
```

Everything after strategy A until strategy B starts belongs to strategy A's
window. If a phase transition happens before the next elicitation strategy, add
an explicit `window_end_time` in the intervention log so the window stops at the
phase transition.

## Runtime

Use the bundled Codex Python runtime if available:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' --help
```

The script uses `pandas`, `numpy`, and `Pillow`. It does not require
`matplotlib`.

## Step 1: Create Input Templates

Run this once to create blank/example CSV schemas:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --write-templates 'llm\analysis\templates'
```

This creates:

- `transcript_template.csv`
- `intervention_log_template.csv`
- `manual_window_measures_template.csv`

Copy these templates and fill them with your real study data.

## Step 2: Fill The Transcript CSV

Required columns:

```csv
session_id,speaker,start_time,end_time,text
```

Live transcript logs may use `start_timestamp` and `end_timestamp`; the script
will treat those as `start_time` and `end_time`.

Alternative duration format:

```csv
session_id,speaker,start_time,duration_seconds,text
```

Column meanings:

- `session_id`: unique session/group discussion ID, such as `S01`.
- `speaker`: participant or robot label, such as `P1`, `P2`, `Participant`,
  `Robot`, or `Pepper`.
- `start_time`: speech onset time.
- `end_time`: speech end time.
- `duration_seconds`: use this instead of `end_time` if your transcript has
  start time plus duration.
- `text`: transcript text for that turn.

Accepted time formats:

- ISO datetimes, for example `2026-05-05T10:00:05`.
- Numeric seconds, for example `245.2`.
- Clock times, for example `04:05` or `01:04:05`.

Use the same time basis in the transcript and intervention log. Do not mix
absolute timestamps in one file with relative seconds in the other unless they
refer to the same timeline.

## Step 3: Fill The Intervention Log CSV

Required columns:

```csv
session_id,prompt_id,phase,strategy,prompt_start_time,prompt_end_time
```

Recommended columns:

```csv
session_id,prompt_id,phase,strategy,prompt_type,prompt_start_time,prompt_end_time,window_end_time,elicitation_engagement_score,creative_confidence_score,evaluation_moment
```

Column meanings:

- `session_id`: must match the transcript session.
- `prompt_id`: unique ID for the robot prompt, such as `E01`.
- `phase`: `divergence` or `convergence`.
- `strategy`: elicitation strategy name, such as `generative`,
  `elaboration_evidence`, or `perspective_shift`.
- `prompt_type`: use `elicitation` for strategy prompts and `normal` or
  `context_only` for non-strategy prompts.
- `prompt_start_time`: when Pepper started speaking.
- `prompt_end_time`: when Pepper finished speaking.
- `window_end_time`: optional manual override, useful when a phase transition
  should close the window before the next elicitation strategy.
- `elicitation_engagement_score`: optional 1-100 engagement score for this
  elicitation window.
- `creative_confidence_score`: optional 1-100 answer to "How confident are you
  in your creative abilities?"
- `evaluation_moment`: optional label such as `start` or `end` for
  session-level evaluation rows.

Live `transcript.csv` files from `lmstudio_minimal_bridge.py` can also be used
as the intervention log. The analysis script will read robot rows with
`prompt_id`, `strategy`, `start_timestamp`, and `end_timestamp`. If live mode is
started with `--evaluation_elicitation`, the start-of-session engagement and
creative-confidence scores are stored as a `session_start_evaluation` event.
The score entered before a new elicitation prompt is stored on that new robot
row together with `previous_elicitation_prompt_id`; the analysis maps it back
to the previous elicitation window. The final engagement and creative-confidence
scores entered on `exit` are stored as a `session_end_evaluation` event, with
the engagement score mapped directly to the last open window.

How the script detects elicitation rows:

- If `is_elicitation` exists, values like `1`, `true`, `yes`, or `elicitation`
  are treated as elicitation rows.
- Else, if `prompt_type` contains `elicitation`, those rows are used.
- Else, any row with a non-empty strategy that is not `context_only`, `normal`,
  `baseline`, `none`, or blank is treated as an elicitation row.

## Step 4: Fill Manual Measure Data

Manual measures are needed for the substantive contribution variables that
should not be trusted to automatic AI coding:

- idea fluency
- elaboration depth
- consecutive turns on the same subject

Recommended columns:

```csv
window_id,session_id,prompt_id,phase,strategy,idea_fluency,elaboration_units,elaborated_contribution_count,consecutive_topic_turns,coder_id,notes
```

Column meanings:

- `window_id`: use the generated format `session_id__prompt_id`, for example
  `S01__E01`.
- `session_id`, `prompt_id`, `phase`, `strategy`: match the elicitation window.
- `idea_fluency`: number of distinct, non-redundant, task-relevant ideas.
- `elaboration_units`: number of concrete elaborative units, such as reasons,
  examples, mechanisms, constraints, consequences, implementation details,
  evidence, analogies, or clarifications.
- `elaborated_contribution_count`: number of ideas/thoughts that contain at
  least one elaborative unit.
- `consecutive_topic_turns`: length of the same-subject turn chain for that
  window. Use one consistent rule, such as longest chain per window.
- `coder_id`: human coder identifier.
- `notes`: optional coding notes.

The script also computes `elaboration_units_per_idea` when both
`elaboration_units` and `idea_fluency` are present.

## Step 5: Run The Full Analysis

Example command:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --transcript 'llm\analysis\data\transcript.csv' `
  --interventions 'llm\analysis\data\intervention_log.csv' `
  --manual-measures 'llm\analysis\data\manual_window_measures.csv' `
  --output-dir 'llm\analysis\outputs'
```

You can omit inputs you do not have yet. For example, you can run only the
transcript/intervention part:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --transcript 'llm\analysis\data\transcript.csv' `
  --interventions 'llm\analysis\data\intervention_log.csv' `
  --output-dir 'llm\analysis\outputs'
```

If you pass `--transcript`, you must also pass `--interventions`, because the
transcript metrics need elicitation windows.

## Output Files

The script writes CSV summaries and PNG charts to `--output-dir`.

Core CSV outputs:

- `elicitation_windows.csv`: constructed windows with start/end times.
- `elicitation_engagement_summary_by_phase_strategy.csv`: grouped mean,
  median, standard deviation, and counts for 1-100 engagement scores when
  present.
- `session_evaluation_scores.csv`: start/end evaluation rows, including
  engagement and creative-confidence scores.
- `session_evaluation_summary.csv`: grouped start/end summaries for those
  1-100 evaluation scores.
- `creative_confidence_change_by_session.csv`: start, end, and change scores
  for creative confidence when both moments are present.
- `transcript_window_metrics.csv`: response delay, speaking time, turn count,
  word count, and mean participant turn duration for each window.
- `transcript_summary_by_phase_strategy.csv`: grouped transcript metrics by
  phase and strategy.
- `manual_window_measures_cleaned.csv`: manual measures after numeric cleanup.
- `manual_summary_by_phase_strategy.csv`: grouped idea fluency, elaboration, and
  topic-chain metrics.
- `run_manifest.json`: list of generated files.

Chart outputs are written to `--output-dir\charts`.

Generated chart filenames:

- `response_delay_by_phase_strategy.png`
- `speaking_time_by_phase_strategy.png`
- `elicitation_engagement_by_phase_strategy.png`
- `evaluation_engagement_by_moment.png`
- `creative_confidence_by_moment.png`
- `vocal_activation_by_phase_strategy.png`
- `connection_cue_score_by_phase_strategy.png`
- `speech_rate_by_phase_strategy.png`
- `long_pause_burden_by_phase_strategy.png`
- `connection_cues_per_minute_by_phase_strategy.png`
- `idea_fluency_by_phase_strategy.png`
- `elaboration_units_by_phase_strategy.png`
- `elaboration_units_per_idea_by_phase_strategy.png`
- `consecutive_topic_turns_by_phase_strategy.png`
- `pepper_measurement_dashboard.png`: combined visual overview of all generated
  charts.

Charts are grouped by `phase / strategy` when those columns are present.

## Speaker Labels

By default, these speaker labels are treated as participants:

```text
P1, P2, Participant, participant, Human
```

These labels are treated as robot/facilitator:

```text
Robot, Pepper, Facilitator, robot, pepper
```

If your transcript uses different labels, pass them explicitly:

```powershell
--participant-speakers P1 P2 A B `
--robot-speakers Pepper Robot
```

## Recommended Workflow

1. Generate the templates.
2. Fill `transcript.csv` and `intervention_log.csv`.
3. Run the script once with only transcript and intervention data.
4. Inspect `elicitation_windows.csv` to make sure each window starts and ends
   where expected.
5. Fill manual coding using the confirmed `window_id` values.
6. Run the full command with `--manual-measures`.
7. Use the grouped CSV summaries and PNG charts in the report.

## Measurement Notes

Automated metrics:

- response delay
- participant speaking time until the next elicitation strategy
- participant turn count
- participant word count
- vocal activation score: 0-100 proxy combining log audio RMS, participant
  words per second, and inverse long-pause burden
- connection-cue score: 0-100 proxy combining quick uptake, short
  acknowledgements/backchannels, laughter, and cooperative overlap

Manual metrics:

- idea fluency
- elaboration units
- elaborated contribution count
- consecutive turns on the same subject

The manual metrics are intentionally manual because idea boundaries,
elaboration quality, and same-topic continuity require human judgment for this
study.

The vocal activation and connection-cue scores are lightweight behavioral
proxies rather than validated standalone scales. They are designed to complement
response latency, speaking time, elicitation-window engagement, and the
start/end creative-confidence rating with transparent transcript/audio-log
features.
